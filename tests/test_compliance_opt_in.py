"""Scan-time compliance opt-in: only selected modules run and can be reported."""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient

from backend.services import jobs
from backend.database import init_db
from conftest import SAMPLE_XLSX, bearer_headers

POLL_TIMEOUT_SECONDS = 40


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    from backend.config.settings import SETTINGS

    db_path = tmp_path / "test_opt_in.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DQE_DATABASE_URL", url)
    init_db(database_url=url)
    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", tmp_path / "reports")
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")
    jobs.configure_executor(max_workers=1)
    from backend.app import app

    with TestClient(app, headers=bearer_headers("*")) as client:
        yield client


def _wait(api_client, run_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = api_client.get(f"/v1/runs/{run_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    pytest.fail("run did not finish")


def _upload(api_client, data: bytes, name: str, **params):
    params.setdefault("client_id", "opt_in_client")
    params.setdefault("write_report", True)
    resp = api_client.post(
        "/v1/files/upload",
        files={"file": (name, io.BytesIO(data), "text/csv")},
        params=params,
    )
    assert resp.status_code in (200, 202), resp.text
    run_id = resp.json()["run_id"]
    status = _wait(api_client, run_id)
    assert status["status"] == "completed", status
    return run_id


PCI_CSV = b"card_number,exp_date,cvv,amount\n4111111111111111,05/27,123,150.00\n"


class TestComplianceOptIn:
    def test_no_modules_means_compliance_report_404(self, api_client):
        run_id = _upload(api_client, PCI_CSV, "none.csv")
        resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "HIPAA"}
        )
        assert resp.status_code == 404
        resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "PCI_DSS"}
        )
        assert resp.status_code == 404

    def test_pci_only_does_not_serve_hipaa(self, api_client):
        run_id = _upload(
            api_client, PCI_CSV, "pci.csv", compliance_modules=["PCI_DSS"]
        )
        pci = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "PCI_DSS"}
        )
        assert pci.status_code == 200
        assert "inspect-compliance-btn" in pci.text
        assert "openComplianceFindingModal" in pci.text
        hipaa = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "HIPAA"}
        )
        assert hipaa.status_code == 404
        glba = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "GLBA"}
        )
        assert glba.status_code == 404

    def test_sox_opt_in_visible_in_list_runs_and_serves_report(self, api_client):
        # Test bracketed query param serialization as sent by web clients
        resp = api_client.post(
            "/v1/files/upload?client_id=opt_in_client&write_report=true&compliance_modules[]=SOX&compliance_modules[]=GLBA",
            files={"file": ("sox_test.csv", io.BytesIO(PCI_CSV), "text/csv")},
        )
        assert resp.status_code in (200, 202), resp.text
        run_id = resp.json()["run_id"]
        status = _wait(api_client, run_id)
        assert status["status"] == "completed"

        # Verify list_runs returns the compliance_modules
        runs_resp = api_client.get("/v1/clients/opt_in_client/runs")
        assert runs_resp.status_code == 200
        runs_data = runs_resp.json()["runs"]
        matched_run = next((r for r in runs_data if r["run_id"] == run_id), None)
        assert matched_run is not None
        assert "SOX" in matched_run["compliance_modules"]
        assert "GLBA" in matched_run["compliance_modules"]
        assert matched_run["has_compliance_report"] is True

        # Verify SOX report is served
        sox_resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "SOX"}
        )
        assert sox_resp.status_code == 200
        assert "SOX" in sox_resp.text

    def test_gdpr_and_ccpa_opt_in_and_serving(self, api_client):
        privacy_csv = b"full_name,ssn,email,ip_address,city\nAlice,123-45-6789,alice@test.com,192.168.1.1,Seattle\n"
        resp = api_client.post(
            "/v1/files/upload?client_id=opt_in_client&write_report=true&compliance_modules=GDPR&compliance_modules=CCPA",
            files={"file": ("privacy_test.csv", io.BytesIO(privacy_csv), "text/csv")},
        )
        assert resp.status_code in (200, 202), resp.text
        run_id = resp.json()["run_id"]
        status = _wait(api_client, run_id)
        assert status["status"] == "completed"

        # Verify list_runs includes GDPR and CCPA
        runs_resp = api_client.get("/v1/clients/opt_in_client/runs")
        assert runs_resp.status_code == 200
        matched_run = next((r for r in runs_resp.json()["runs"] if r["run_id"] == run_id), None)
        assert matched_run is not None
        assert "GDPR" in matched_run["compliance_modules"]
        assert "CCPA" in matched_run["compliance_modules"]

        # Verify GDPR report served
        gdpr_resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "GDPR"}
        )
        assert gdpr_resp.status_code == 200
        assert "GDPR" in gdpr_resp.text
        assert "inspect-compliance-btn" in gdpr_resp.text

        # Verify CCPA report served
        ccpa_resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "CCPA"}
        )
        assert ccpa_resp.status_code == 200
        assert "CCPA" in ccpa_resp.text

        # Verify unselected modules return 404
        hipaa_resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report", params={"regulation": "HIPAA"}
        )
        assert hipaa_resp.status_code == 404

