"""Integration tests for M6 entity-resolution API endpoints."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.services import jobs
from backend.database import get_session, init_db
from backend.database.models import RunManifest, RunStatus

from conftest import SAMPLE_XLSX

SAMPLE = SAMPLE_XLSX
POLL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.05


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    from backend.config.settings import SETTINGS

    db_path = tmp_path / "test_m6_api.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DQE_DATABASE_URL", db_url)
    init_db(database_url=db_url)

    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", tmp_path / "reports")
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")

    jobs.configure_executor(max_workers=1)

    from backend.app import app

    with TestClient(app) as client:
        reg = client.post(
            "/v1/auth/register",
            json={"email": "m6@test.example", "password": "password123"},
        )
        assert reg.status_code == 201, reg.text
        token = reg.json()["access_token"]
        client_id = reg.json()["client_id"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        client.test_client_id = client_id  # type: ignore[attr-defined]
        yield client


def _wait_for_terminal(api_client, run_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = api_client.get(f"/v1/runs/{run_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Run {run_id} did not finish within {POLL_TIMEOUT_SECONDS}s")


def _upload_er_sample(api_client, **params):
    params.setdefault("client_id", api_client.test_client_id)
    with open(SAMPLE, "rb") as f:
        return api_client.post(
            "/v1/analyze/entity-resolution",
            params=params,
            files={"file": ("sample_data.xlsx", f.read(), "application/vnd.ms-excel")},
        )


class TestEntityResolutionApi:
    def test_analyze_entity_resolution_accepts_upload(self, api_client):
        resp = _upload_er_sample(api_client)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == RunStatus.PENDING.value
        assert body["run_id"]
        assert body["file_name"] == "sample_data.xlsx"

    def test_entity_resolution_results_after_completed_run(self, api_client):
        run_id = _upload_er_sample(api_client, write_report=True).json()["run_id"]
        status = _wait_for_terminal(api_client, run_id)
        assert status["status"] == "completed"

        resp = api_client.get(f"/v1/entity-resolution/results/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["status"] == "completed"
        assert len(body["sheets"]) >= 1
        sheet = body["sheets"][0]
        assert "entity_resolution_auto" in sheet
        assert "entity_resolution_review" in sheet
        assert "entity_resolution_no_match" in sheet
        assert "summary" in sheet
        assert "columns" in sheet

    def test_entity_resolution_results_404_for_unknown_run(self, api_client):
        resp = api_client.get("/v1/entity-resolution/results/does-not-exist")
        assert resp.status_code == 404

    def test_entity_resolution_stored_in_manifest(self, api_client):
        run_id = _upload_er_sample(api_client).json()["run_id"]
        _wait_for_terminal(api_client, run_id)

        with get_session() as session:
            manifest = (
                session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
            )
            assert manifest is not None
            assert "entity_resolution" in (manifest.checks_run or [])
            sheet = manifest.extra["sheets"][0]
            assert "entity_resolution" in sheet
            assert "entity_resolution_auto" in sheet

    def test_city_column_produces_resolutions(self, api_client, tmp_path):
        """Workbook with City column should yield M6 resolution rows."""
        path = tmp_path / "cities.xlsx"
        pd.DataFrame(
            {
                "City": ["LHR", "LHR", "Karachi", "Karachi", "Islamabad"],
                "Country": ["Pakistan", "Pakistan", "Pakistan", "Pakistan", "Pakistan"],
            }
        ).to_excel(path, index=False)

        with open(path, "rb") as f:
            resp = api_client.post(
                "/v1/analyze/entity-resolution",
                params={"client_id": api_client.test_client_id},
                files={"file": ("cities.xlsx", f.read(), "application/vnd.ms-excel")},
            )
        run_id = resp.json()["run_id"]
        _wait_for_terminal(api_client, run_id)

        er = api_client.get(f"/v1/entity-resolution/results/{run_id}").json()
        sheet = er["sheets"][0]
        assert sheet["enabled"] is True
        assert sheet["summary"].get("total_values", 0) >= 1
        city_block = sheet["columns"].get("City") or sheet["columns"].get("city")
        assert city_block is not None
        assert city_block.get("resolutions")
