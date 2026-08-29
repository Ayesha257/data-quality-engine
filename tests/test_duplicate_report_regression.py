"""
Regression tests for duplicate compliance-report generation.

Root cause this locks in: run_pipeline used to call _write_compliance_report()
and emit "{stem}_{sheet}_compliance_report.html" (HIPAA-only, no regulation
in the filename) at scan time. GET /v1/runs/{id}/compliance-report then
wrote a second, regulation-parameterized file. Callers saw two reports;
the scan-time one was the wrong regulation (always HIPAA) or empty.

Exactly one HTML file per (scan, sheet, regulation) request is allowed.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.services import jobs
from backend.database import init_db
from conftest import SAMPLE_XLSX, bearer_headers

POLL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.05
REGULATIONS = ("HIPAA", "PCI_DSS", "GLBA", "SOX")


def _legacy_unparameterized_reports(reports_dir: Path) -> list[Path]:
    """Files matching the old scan-time name with no regulation token."""
    known = ("hipaa", "pci_dss", "glba", "sox", "gdpr", "ccpa")
    found = []
    for path in reports_dir.glob("*_compliance_report.html"):
        name = path.name.lower()
        if not any(f"_{token}_compliance_report.html" in name for token in known):
            found.append(path)
    return found


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    from backend.config.settings import SETTINGS

    db_path = tmp_path / "test_dup_report.db"
    monkeypatch.setenv("DQE_DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    init_db(database_url=f"sqlite:///{db_path.as_posix()}")

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", reports_dir)
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")

    jobs.configure_executor(max_workers=1)

    from backend.app import app

    with TestClient(app, headers=bearer_headers("*")) as client:
        client.reports_dir = reports_dir  # type: ignore[attr-defined]
        yield client


def _wait_for_run(api_client, run_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = api_client.get(f"/v1/runs/{run_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Run {run_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS}s")


def _upload_bytes(api_client, filename: str, data: bytes, **params) -> str:
    params.setdefault("client_id", "dup_report_client")
    params.setdefault("write_report", True)
    if params.get("write_report") is not False:
        params.setdefault("compliance_modules", ["HIPAA", "PCI_DSS", "GLBA", "SOX"])
    resp = api_client.post(
        "/v1/files/upload",
        files={"file": (filename, io.BytesIO(data), "text/csv")},
        params=params,
    )
    assert resp.status_code in (200, 202), resp.text
    run_id = resp.json()["run_id"]
    status = _wait_for_run(api_client, run_id)
    assert status["status"] == "completed", status
    return run_id


def _upload_xlsx(api_client, path: Path, **params) -> str:
    params.setdefault("client_id", "dup_report_client")
    params.setdefault("write_report", True)
    params.setdefault("compliance_modules", ["HIPAA", "PCI_DSS", "GLBA", "SOX"])
    resp = api_client.post(
        "/v1/files/upload",
        files={"file": (path.name, path.read_bytes(), "application/vnd.ms-excel")},
        params=params,
    )
    assert resp.status_code in (200, 202), resp.text
    run_id = resp.json()["run_id"]
    status = _wait_for_run(api_client, run_id)
    assert status["status"] == "completed", status
    return run_id


PCI_CSV = b"card_number,exp_date,cvv,amount\n4111111111111111,05/27,123,150.00\n"
MULTI_CSV = (
    b"account_number,routing_number,patient_name,ssn,card_number,created_at\n"
    b"123456789,021000021,John Doe,123-45-6789,4111111111111111,2026-01-01 10:00:00\n"
)


class TestDuplicateReportRegression:
    def test_scan_time_legacy_writer_is_gone(self):
        import backend.main as main

        assert not hasattr(main, "_write_compliance_report")

    def test_scan_does_not_create_compliance_html(self, api_client):
        reports_dir = api_client.reports_dir
        _upload_bytes(api_client, "payments.csv", PCI_CSV)

        html_files = list(reports_dir.glob("*.html"))
        assert html_files, "data quality report should still be written"
        assert not any("compliance_report" in f.name for f in html_files)
        assert _legacy_unparameterized_reports(reports_dir) == []

    def test_pci_dss_get_writes_exactly_one_file_even_if_called_twice(self, api_client):
        reports_dir = api_client.reports_dir
        run_id = _upload_bytes(
            api_client, "pci_sample.csv", PCI_CSV, compliance_modules=["PCI_DSS"]
        )

        for _ in range(2):
            resp = api_client.get(
                f"/v1/runs/{run_id}/compliance-report",
                params={"regulation": "PCI_DSS"},
            )
            assert resp.status_code == 200
            content = resp.text
            assert "PCI-DSS" in content or "PCI_DSS" in content
            assert (
                "This report flags compliance-relevant data patterns. "
                "It does not certify legal compliance with PCI_DSS."
            ) in content

        compliance_reports = list(reports_dir.glob("*_compliance_report.html"))
        assert len(compliance_reports) == 1, compliance_reports
        assert "pci_dss_compliance_report.html" in compliance_reports[0].name
        assert _legacy_unparameterized_reports(reports_dir) == []

    def test_each_regulation_is_one_file_on_csv_and_xlsx(self, api_client):
        reports_dir = api_client.reports_dir

        csv_run = _upload_bytes(api_client, "multi_test.csv", MULTI_CSV)
        xlsx_run = _upload_xlsx(api_client, SAMPLE_XLSX)

        for run_id in (csv_run, xlsx_run):
            before = {
                p.resolve() for p in reports_dir.glob("*_compliance_report.html")
            }
            for reg in REGULATIONS:
                resp = api_client.get(
                    f"/v1/runs/{run_id}/compliance-report",
                    params={"regulation": reg},
                )
                assert resp.status_code == 200, (reg, resp.text[:300])
                assert "<html" in resp.text.lower()
                if reg != "HIPAA":
                    assert f"legal compliance with {reg}" in resp.text

            after = list(reports_dir.glob("*_compliance_report.html"))
            new = [p for p in after if p.resolve() not in before]
            names = [p.name.lower() for p in new]
            assert len(new) == 4, names
            for token in ("hipaa", "pci_dss", "glba", "sox"):
                assert any(f"_{token}_compliance_report.html" in n for n in names)

        assert _legacy_unparameterized_reports(reports_dir) == []

    def test_write_report_false_returns_404(self, api_client):
        run_id = _upload_bytes(
            api_client, "no_rep.csv", b"col1,col2\nval1,val2\n", write_report=False
        )
        resp = api_client.get(
            f"/v1/runs/{run_id}/compliance-report",
            params={"regulation": "HIPAA"},
        )
        assert resp.status_code == 404
        assert list(api_client.reports_dir.glob("*compliance_report*")) == []
