"""
Tests for the interactive (human-in-the-loop) header-row confirmation
checkpoint, surfaced over the REST API by engine/api_prompt.py.

Mirrors the fixture/isolation pattern from test_phase2_m4_api.py: own
temp SQLite DB, own uploads/reports dirs, single-worker executor, bounded
polling with a real wall-clock timeout so a genuine deadlock fails the
test instead of hanging CI.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.services import jobs
from backend.database import init_db

from conftest import SAMPLE_XLSX, bearer_headers

SAMPLE = SAMPLE_XLSX
POLL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.05


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    from backend.config.settings import SETTINGS
    from backend.engine import api_prompt

    db_path = tmp_path / "test_header_confirm.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DQE_DATABASE_URL", db_url)
    init_db(database_url=db_url)

    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", tmp_path / "reports")
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")

    # Tests that deliberately never answer a checkpoint (422/409 cases)
    # would otherwise leave a worker thread blocked in event.wait() for
    # the real 30-minute production timeout -- and since ThreadPoolExecutor
    # threads aren't daemons, that hangs the whole test process at exit.
    # Shrink it to something a test run can actually wait out.
    monkeypatch.setattr(api_prompt, "CONFIRMATION_TIMEOUT_SECONDS", 2)

    jobs.configure_executor(max_workers=1)

    from backend.app import app

    with TestClient(app, headers=bearer_headers("*")) as client:
        yield client


def _upload_sample(api_client, **params) -> dict:
    params.setdefault("client_id", "acme_corp")
    params.setdefault("interactive", True)
    with open(SAMPLE, "rb") as f:
        resp = api_client.post(
            "/v1/files/upload",
            params=params,
            files={"file": ("sample_data.xlsx", f.read(), "application/vnd.ms-excel")},
        )
    return resp


def _wait_for_status(api_client, run_id: str, targets: set[str]) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = api_client.get(f"/v1/runs/{run_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in targets:
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Run {run_id} did not reach {targets} within {POLL_TIMEOUT_SECONDS}s")


class TestInteractiveHeaderConfirmation:
    def test_pauses_at_awaiting_confirmation_with_preview(self, api_client):
        resp = _upload_sample(api_client)
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        body = _wait_for_status(api_client, run_id, {"awaiting_confirmation", "failed"})
        assert body["status"] == "awaiting_confirmation"
        pc = body["pending_confirmation"]
        assert pc["type"] == "header_row"
        assert pc["sheet_name"]
        assert "detected_header_row" in pc
        assert "header_values" in pc

    def test_accepting_detected_header_resumes_and_completes(self, api_client):
        resp = _upload_sample(api_client)
        run_id = resp.json()["run_id"]
        body = _wait_for_status(api_client, run_id, {"awaiting_confirmation", "failed"})
        assert body["status"] == "awaiting_confirmation"

        confirm_resp = api_client.post(
            f"/v1/runs/{run_id}/confirm", json={"accept": True}
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "running"

        final = _wait_for_status(api_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"

    def test_overriding_header_row_is_applied(self, api_client):
        resp = _upload_sample(api_client)
        run_id = resp.json()["run_id"]
        body = _wait_for_status(api_client, run_id, {"awaiting_confirmation", "failed"})
        detected = body["pending_confirmation"]["detected_header_row"]

        confirm_resp = api_client.post(
            f"/v1/runs/{run_id}/confirm",
            json={"accept": False, "override_header_row": detected},
        )
        assert confirm_resp.status_code == 200

        final = _wait_for_status(api_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"

    def test_confirm_requires_override_value_when_rejecting(self, api_client):
        resp = _upload_sample(api_client)
        run_id = resp.json()["run_id"]
        _wait_for_status(api_client, run_id, {"awaiting_confirmation", "failed"})

        confirm_resp = api_client.post(f"/v1/runs/{run_id}/confirm", json={"accept": False})
        assert confirm_resp.status_code == 422

    def test_confirm_rejected_when_run_not_awaiting(self, api_client):
        resp = _upload_sample(api_client, interactive=False)
        run_id = resp.json()["run_id"]
        _wait_for_status(api_client, run_id, {"completed", "failed"})

        confirm_resp = api_client.post(f"/v1/runs/{run_id}/confirm", json={"accept": True})
        assert confirm_resp.status_code == 409

    def test_headless_default_unchanged(self, api_client):
        """interactive is opt-in: omitting it entirely must behave exactly
        like before -- straight through to completed/failed, no pause."""
        with open(SAMPLE, "rb") as f:
            resp = api_client.post(
                "/v1/files/upload",
                params={"client_id": "acme_corp"},
                files={"file": ("sample_data.xlsx", f.read(), "application/vnd.ms-excel")},
            )
        run_id = resp.json()["run_id"]
        final = _wait_for_status(api_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
