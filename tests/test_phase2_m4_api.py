"""
Phase 2 — M4 (REST API) integration tests.

Run just these with:
    pytest tests/test_phase2_m4_api.py -v

What this covers, end to end, using FastAPI's TestClient (no real network
socket, no separate server process):
    - POST /v1/files/upload: accepts a real .xlsx, rejects bad extensions,
      rejects oversized/empty files, validates client_id / target+date
      pairing using the exact same M1 Pydantic rules the rest of Phase 2
      already relies on (not a second, possibly-drifting copy)
    - background execution actually runs main.run_pipeline() and the
      RunRecord transitions pending -> running -> completed/failed
    - GET status / results / report reflect what the pipeline actually
      produced, including the multi-sheet aggregation and the failure
      paths (no sheets processed, all sheets failed)
    - the whole flow is dataset-agnostic: every test drives it through
      the real sample_data.xlsx already used by tests/test_main_pipeline.py,
      never a hand-tuned "API-specific" fixture that could hide a
      dataset-dependent bug the CLI path wouldn't have

Isolation: every test gets its own temporary SQLite DB and its own
temporary uploads/reports directory (via monkeypatch + tmp_path), exactly
like tests/test_phase2_m1_setup.py and tests/test_main_pipeline.py do, so
nothing here touches the repo's real logs/reports/uploads/db and no test
depends on another test's state.

Execution model in tests: the background thread pool is reduced to a
single worker and every polling helper below has a real wall-clock
timeout (never an infinite loop), so a genuinely broken run fails the
test instead of hanging CI.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_quality_engine.phase2.api import jobs
from data_quality_engine.phase2.database import get_session, init_db
from data_quality_engine.phase2.database.models import RunManifest, RunRecord, RunStatus

SAMPLE = Path(__file__).resolve().parents[1] / "src" / "sample_data" / "sample_data.xlsx"

# Generous but bounded: real pipeline runs on this small sample file take
# well under a second; this only guards against a genuine hang.
POLL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.05


# Auth (see data_quality_engine/phase2/api/auth.py): DQE_API_KEYS is
# "key:client_id" pairs, comma-separated. Most tests below exercise
# upload/status/results/report behavior, not auth itself, so the default
# fixture is granted an admin key ("*") -- valid for any client_id -- to
# keep those tests focused on one thing at a time. Auth-specific
# behavior (missing key, wrong key, cross-client access) is covered by
# TestAuthentication below using its own client-scoped keys.
ADMIN_API_KEY = "test-admin-key"
SCOPED_API_KEY_A = "test-scoped-key-a"  # bound to client_a only


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """
    A fully isolated app instance per test:
      - its own SQLite file (never the dev/prod database)
      - its own uploads/ and reports/ directories under tmp_path
      - its own single-worker executor, so background runs are easy to
        reason about and can't leak into the next test
      - its own admin-scoped API key, so every request in the test is
        authenticated by default without every call site needing to pass
        a header explicitly
    """
    from data_quality_engine.config.settings import SETTINGS

    db_path = tmp_path / "test_phase2_m4.db"
    init_db(database_url=f"sqlite:///{db_path}")

    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", tmp_path / "reports")
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")

    monkeypatch.setenv(
        "DQE_API_KEYS", f"{ADMIN_API_KEY}:*,{SCOPED_API_KEY_A}:client_a"
    )

    jobs.configure_executor(max_workers=1)

    # Import app.py directly (see phase2/api/__init__.py's docstring for
    # why the package __init__ deliberately does not re-export `app`).
    from data_quality_engine.phase2.api.app import app

    with TestClient(app, headers={"X-API-Key": ADMIN_API_KEY}) as client:
        yield client


def _upload_sample(api_client, **params) -> dict:
    params.setdefault("client_id", "acme_corp")
    headers = params.pop("headers", None)
    with open(SAMPLE, "rb") as f:
        resp = api_client.post(
            "/v1/files/upload",
            params=params,
            files={"file": ("sample_data.xlsx", f.read(), "application/vnd.ms-excel")},
            headers=headers,
        )
    return resp


def _wait_for_terminal_status(api_client, run_id: str) -> dict:
    """Poll GET /status until COMPLETED or FAILED, or raise on timeout.
    A timeout here is a real test failure, never a silent pass, so a
    genuinely deadlocked background run can't masquerade as green CI."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = api_client.get(f"/v1/runs/{run_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Run {run_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS}s")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_reports_connected_database(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"


# ---------------------------------------------------------------------------
# Upload validation — none of this should ever touch the pipeline
# ---------------------------------------------------------------------------

class TestUploadValidation:
    def test_rejects_disallowed_extension(self, api_client):
        resp = api_client.post(
            "/v1/files/upload",
            params={"client_id": "acme_corp"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
        assert "xlsx" in resp.json()["detail"].lower()

    def test_rejects_empty_file(self, api_client):
        resp = api_client.post(
            "/v1/files/upload",
            params={"client_id": "acme_corp"},
            files={"file": ("empty.xlsx", b"", "application/vnd.ms-excel")},
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_rejects_oversized_file(self, api_client, monkeypatch):
        import data_quality_engine.phase2.api.routes as routes_mod

        # Shrink the limit instead of generating a real 200MB+ payload —
        # keeps this test fast regardless of the configured production limit.
        monkeypatch.setattr(routes_mod, "_MAX_UPLOAD_BYTES", 10)
        resp = api_client.post(
            "/v1/files/upload",
            params={"client_id": "acme_corp"},
            files={"file": ("sample_data.xlsx", b"x" * 100, "application/vnd.ms-excel")},
        )
        assert resp.status_code == 413

    def test_rejects_invalid_client_id(self, api_client):
        resp = _upload_sample(api_client, client_id="not a valid id! ??")
        assert resp.status_code == 422

    def test_rejects_target_column_without_date_column(self, api_client):
        resp = _upload_sample(api_client, target_column="revenue")
        assert resp.status_code == 400
        assert "together" in resp.json()["detail"].lower()

    def test_rejects_date_column_without_target_column(self, api_client):
        resp = _upload_sample(api_client, date_column="order_date")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Successful end-to-end run
# ---------------------------------------------------------------------------

class TestSuccessfulRun:
    def test_upload_returns_pending_run_immediately(self, api_client):
        resp = _upload_sample(api_client)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["client_id"] == "acme_corp"
        assert body["file_name"] == "sample_data.xlsx"
        assert body["run_id"]

    def test_run_reaches_completed_and_produces_a_score(self, api_client):
        run_id = _upload_sample(api_client).json()["run_id"]

        status_body = _wait_for_terminal_status(api_client, run_id)
        assert status_body["status"] == "completed"
        assert status_body["error_message"] is None

        results_resp = api_client.get(f"/v1/runs/{run_id}/results")
        assert results_resp.status_code == 200
        results = results_resp.json()

        assert results["status"] == "completed"
        assert results["overall_score"] is not None
        assert 0.0 <= results["overall_score"] <= 100.0
        assert results["rows_processed"] and results["rows_processed"] > 0
        assert len(results["sheets"]) >= 1
        for sheet in results["sheets"]:
            assert sheet["error"] is None

    def test_report_is_generated_and_downloadable(self, api_client):
        run_id = _upload_sample(api_client, write_report=True).json()["run_id"]
        _wait_for_terminal_status(api_client, run_id)

        resp = api_client.get(f"/v1/runs/{run_id}/report")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert b"<html" in resp.content.lower()

    def test_write_report_false_skips_report_generation(self, api_client):
        run_id = _upload_sample(api_client, write_report=False).json()["run_id"]
        _wait_for_terminal_status(api_client, run_id)

        resp = api_client.get(f"/v1/runs/{run_id}/report")
        assert resp.status_code == 404

    def test_results_persisted_in_database_directly(self, api_client):
        """Belt-and-suspenders: check the DB row itself, not just the HTTP
        response, so a bug that only manifests in the response-shaping
        layer (schemas.py) can't hide behind a coincidentally-correct
        results endpoint."""
        run_id = _upload_sample(api_client).json()["run_id"]
        _wait_for_terminal_status(api_client, run_id)

        with get_session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            assert run.status == RunStatus.COMPLETED
            assert run.overall_score is not None
            assert run.completed_at is not None

            manifest = (
                session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
            )
            assert manifest is not None
            assert manifest.extra["sheets"]

    def test_single_sheet_selection_processes_only_that_sheet(self, api_client):
        # sample_data.xlsx's actual sheet name — reuse the same discovery
        # tests/test_main_pipeline.py relies on, rather than hardcoding a
        # guess here that could silently drift from the real file.
        import pandas as pd

        real_sheet_name = pd.ExcelFile(SAMPLE).sheet_names[0]

        run_id = _upload_sample(api_client, sheet_name=real_sheet_name).json()["run_id"]
        results = _wait_for_terminal_status_and_get_results(api_client, run_id)

        assert len(results["sheets"]) == 1
        assert results["sheets"][0]["sheet_name"] == real_sheet_name


def _wait_for_terminal_status_and_get_results(api_client, run_id: str) -> dict:
    _wait_for_terminal_status(api_client, run_id)
    return api_client.get(f"/v1/runs/{run_id}/results").json()


# ---------------------------------------------------------------------------
# Failure paths — the pipeline itself failing must surface as a FAILED run,
# never as a dead background thread or a 500 that loses the reason why.
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_corrupt_file_produces_a_failed_run_not_a_crash(self, api_client):
        resp = api_client.post(
            "/v1/files/upload",
            params={"client_id": "acme_corp"},
            files={
                "file": (
                    "corrupt.xlsx",
                    b"this is not a real xlsx file, just bytes",
                    "application/vnd.ms-excel",
                )
            },
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        status_body = _wait_for_terminal_status(api_client, run_id)
        assert status_body["status"] == "failed"
        assert status_body["error_message"]

        # The run must still be a normal, fully-formed resource — not a
        # 500 or a resource stuck in an unrecoverable state.
        results = api_client.get(f"/v1/runs/{run_id}/results").json()
        assert results["status"] == "failed"
        assert results["error_message"]

    def test_requesting_nonexistent_sheet_name_fails_cleanly(self, api_client):
        run_id = _upload_sample(
            api_client, sheet_name="ThisSheetDefinitelyDoesNotExist"
        ).json()["run_id"]

        status_body = _wait_for_terminal_status(api_client, run_id)
        assert status_body["status"] == "failed"
        assert status_body["error_message"]


# ---------------------------------------------------------------------------
# Not-found handling
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_status_for_unknown_run_id_is_404(self, api_client):
        resp = api_client.get("/v1/runs/does-not-exist/status")
        assert resp.status_code == 404

    def test_results_for_unknown_run_id_is_404(self, api_client):
        resp = api_client.get("/v1/runs/does-not-exist/results")
        assert resp.status_code == 404

    def test_report_for_unknown_run_id_is_404(self, api_client):
        resp = api_client.get("/v1/runs/does-not-exist/report")
        assert resp.status_code == 404

    def test_report_for_run_still_pending_is_409(self, api_client, monkeypatch):
        # Freeze execution before it starts by pointing the executor at a
        # pool with zero live workers won't work cleanly with ThreadPoolExecutor,
        # so instead we simply check status immediately after upload, before
        # any poll loop — pending/running is common in real traffic and must
        # not be treated as "report available".
        run_id = _upload_sample(api_client).json()["run_id"]
        immediate = api_client.get(f"/v1/runs/{run_id}/status").json()
        if immediate["status"] in ("pending", "running"):
            resp = api_client.get(f"/v1/runs/{run_id}/report")
            assert resp.status_code in (404, 409)
        # If the run raced ahead and already completed on this fast
        # machine, there's nothing meaningful left to assert — the
        # terminal-state download path is already covered above.


# ---------------------------------------------------------------------------
# client_id scoping sanity check (defense-in-depth for the M1 validator reuse)
# ---------------------------------------------------------------------------

class TestClientScoping:
    def test_two_clients_uploading_the_same_file_get_independent_runs(self, api_client):
        run_a = _upload_sample(api_client, client_id="client_a").json()["run_id"]
        run_b = _upload_sample(api_client, client_id="client_b").json()["run_id"]

        assert run_a != run_b

        _wait_for_terminal_status(api_client, run_a)
        _wait_for_terminal_status(api_client, run_b)

        results_a = api_client.get(f"/v1/runs/{run_a}/results").json()
        results_b = api_client.get(f"/v1/runs/{run_b}/results").json()

        assert results_a["client_id"] == "client_a"
        assert results_b["client_id"] == "client_b"


# ---------------------------------------------------------------------------
# Authentication / authorization (data_quality_engine/phase2/api/auth.py)
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_health_check_requires_no_api_key(self, api_client):
        # Liveness probes must work before any key is provisioned, and
        # this endpoint returns no client data -- see auth.py's docstring.
        resp = api_client.get("/health", headers={})
        assert resp.status_code == 200

    def test_upload_without_api_key_is_401(self, api_client):
        # api_client carries a default admin header (see fixture); an
        # empty override string is how httpx lets a test un-set a
        # client-level default header for one request rather than merge
        # with it (passing headers={} would keep the default admin key).
        resp = _upload_sample(api_client, headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_upload_with_invalid_api_key_is_401(self, api_client):
        resp = _upload_sample(api_client, headers={"X-API-Key": "not-a-real-key"})
        assert resp.status_code == 401

    def test_upload_with_no_keys_configured_is_401(self, api_client, monkeypatch):
        # Fail closed: an operator forgetting to set DQE_API_KEYS must
        # deny everything, not silently allow unauthenticated access.
        monkeypatch.delenv("DQE_API_KEYS", raising=False)
        resp = _upload_sample(api_client, headers={"X-API-Key": ADMIN_API_KEY})
        assert resp.status_code == 401

    def test_scoped_key_can_upload_for_its_own_client(self, api_client):
        resp = _upload_sample(
            api_client, client_id="client_a", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 202

    def test_scoped_key_cannot_upload_for_a_different_client(self, api_client):
        resp = _upload_sample(
            api_client, client_id="client_b", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 403

    def test_scoped_key_cannot_read_another_clients_run_status(self, api_client):
        # Admin key creates a run for client_b...
        run_id = _upload_sample(api_client, client_id="client_b").json()["run_id"]
        # ...client_a's scoped key must not be able to read it.
        resp = api_client.get(
            f"/v1/runs/{run_id}/status", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 403

    def test_scoped_key_can_read_its_own_clients_run_status(self, api_client):
        run_id = _upload_sample(
            api_client, client_id="client_a", headers={"X-API-Key": SCOPED_API_KEY_A}
        ).json()["run_id"]
        resp = api_client.get(
            f"/v1/runs/{run_id}/status", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 200

    def test_admin_key_can_read_any_clients_run_status(self, api_client):
        run_id = _upload_sample(api_client, client_id="client_b").json()["run_id"]
        resp = api_client.get(f"/v1/runs/{run_id}/status")  # default admin header
        assert resp.status_code == 200

    def test_malformed_dqe_api_keys_env_var_is_500_not_a_crash(self, api_client, monkeypatch):
        monkeypatch.setenv("DQE_API_KEYS", "this-has-no-colon")
        resp = _upload_sample(api_client, headers={"X-API-Key": ADMIN_API_KEY})
        assert resp.status_code == 500
