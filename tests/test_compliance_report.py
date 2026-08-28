"""
Tests for:
  (a) main report generation with include_hipaa=False correctly omitting
      the HIPAA section, without touching any other report content.
  (b) the new standalone Compliance Report (backend/engine/compliance/report.py)
      producing correct HIPAA content, built from the same CheckResult
      objects the main report's HIPAA section would use.
  (c) default behavior (include_hipaa not passed) matching the prior
      always-include-HIPAA behavior exactly.
"""

from __future__ import annotations

from pathlib import Path

from backend.engine.checkpoint import UserPrompt
from backend.engine.models import CheckResult
from backend.engine.compliance.report import (
    build_compliance_report_data,
    generate_compliance_html_report,
)

import backend.main as main

from conftest import SAMPLE_XLSX

SAMPLE = SAMPLE_XLSX

_HIPAA_MARKER = "HIPAA PHI Compliance Scan"


class AutoConfirmPrompt(UserPrompt):
    def confirm(self, message: str, details: dict) -> bool:
        return True

    def ask_int(self, message: str, default: int | None = None) -> int:
        return default if default is not None else 0

    def ask_text(self, message: str, default: str | None = None) -> str:
        return default if default is not None else ""


def _run(tmp_path, monkeypatch, **kwargs):
    from backend.config.settings import SETTINGS

    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")
    report_dir = tmp_path / "reports"
    outcomes = main.run_pipeline(
        str(SAMPLE),
        prompt=AutoConfirmPrompt(),
        write_report=True,
        report_dir=str(report_dir),
        client_id="compliance_test_client",
        **kwargs,
    )
    assert outcomes, "expected at least one processed sheet"
    return outcomes


# ---------------------------------------------------------------------------
# (a) include_hipaa=False omits the HIPAA section from the main report
# ---------------------------------------------------------------------------

def test_main_report_omits_hipaa_section_when_include_hipaa_false(tmp_path, monkeypatch):
    outcomes = _run(tmp_path, monkeypatch, include_hipaa=False)

    sheet = outcomes[0]
    assert sheet["error"] is None
    assert sheet["report_path"], "main report should still be written"

    html = Path(sheet["report_path"]).read_text(encoding="utf-8")
    assert _HIPAA_MARKER not in html
    assert 'id="check-hipaa_phi"' not in html
    assert "Compliance-adjusted score (HIPAA PHI)" not in html
    assert "HIPAA ceiling (hybrid)" not in html
    # The report must still be a complete, valid report -- no broken
    # placeholder left where the section used to be.
    assert "<html" in html.lower()
    assert "Executive Summary" in html


def test_main_report_omits_hipaa_section_by_default(tmp_path, monkeypatch):
    """Compliance is now handled exclusively by the standalone Compliance Report,
    so the main report omits HIPAA by default."""
    outcomes = _run(tmp_path, monkeypatch)

    sheet = outcomes[0]
    assert sheet["report_path"]
    html = Path(sheet["report_path"]).read_text(encoding="utf-8")
    assert _HIPAA_MARKER not in html
    assert 'id="check-hipaa_phi"' not in html


def test_main_report_includes_hipaa_section_when_include_hipaa_true(tmp_path, monkeypatch):
    outcomes = _run(tmp_path, monkeypatch, include_hipaa=True)

    sheet = outcomes[0]
    assert sheet["report_path"]
    html = Path(sheet["report_path"]).read_text(encoding="utf-8")
    assert _HIPAA_MARKER in html


# ---------------------------------------------------------------------------
# (b) standalone Compliance Report generation, independent of include_hipaa
# ---------------------------------------------------------------------------

def test_pipeline_does_not_pregenerate_compliance_report(tmp_path, monkeypatch):
    """run_pipeline produces only the data quality report and does not emit
    a premature duplicate compliance report file at scan time."""
    import backend.main as main_mod

    assert not hasattr(main_mod, "_write_compliance_report"), (
        "legacy scan-time compliance writer must stay removed"
    )

    outcomes = _run(tmp_path, monkeypatch, include_hipaa=False)

    sheet = outcomes[0]
    assert sheet["report_path"], "main report should be written"
    assert sheet.get("compliance_report_path") is None, (
        "compliance report must not be pre-generated at scan time"
    )
    report_dir = tmp_path / "reports"
    on_disk = list(report_dir.glob("*compliance_report*"))
    assert on_disk == [], (
        f"scan must not write compliance HTML; found {on_disk}"
    )


def test_standalone_compliance_report_matches_main_reports_hipaa_findings(tmp_path, monkeypatch):
    """Both reports show identical HIPAA findings for identical
    input -- same underlying CheckResult objects, just two render targets."""
    outcomes = _run(tmp_path, monkeypatch, include_hipaa=True)

    sheet = outcomes[0]
    main_html = Path(sheet["report_path"]).read_text(encoding="utf-8")

    # Generate standalone compliance report via engine
    from backend.engine.pii.detect_pii import detect_pii_in_series
    from backend.engine.compliance.scanner import assess_hipaa_compliance_as_check_results
    from backend.engine.ingestion import read_excel_file, detect_header_row, load_with_confirmed_header

    sheets = read_excel_file(str(SAMPLE))
    raw_df = sheets[sheet["sheet_name"]]
    header_row = detect_header_row(raw_df)
    df = load_with_confirmed_header(raw_df, header_row)
    pii_summary = {str(c): detect_pii_in_series(df[c]) for c in df.columns}
    hipaa_results = assess_hipaa_compliance_as_check_results(pii_summary, len(df))

    report_data = build_compliance_report_data(
        filepath=str(SAMPLE),
        sheet_name=sheet["sheet_name"],
        row_count=len(df),
        column_count=df.shape[1],
        modules={"hipaa_phi": hipaa_results},
        regulation="HIPAA",
    )
    compliance_out = tmp_path / "standalone_compliance.html"
    generate_compliance_html_report(report_data, str(compliance_out))
    compliance_html = compliance_out.read_text(encoding="utf-8")

    def _stats_block(html: str) -> str:
        start = html.index('id="check-hipaa_phi"')
        end = html.index("</details>", start)
        return html[start:end]

    assert _stats_block(main_html).split("check-stats")[1] == _stats_block(compliance_html).split(
        "check-stats"
    )[1]


def test_compliance_report_only_written_when_write_report_true(tmp_path, monkeypatch):
    from backend.config.settings import SETTINGS

    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")
    outcomes = main.run_pipeline(
        str(SAMPLE),
        prompt=AutoConfirmPrompt(),
        write_report=False,
        client_id="compliance_test_client",
    )
    sheet = outcomes[0]
    assert sheet["report_path"] is None


# ---------------------------------------------------------------------------
# Unit-level coverage of engine/compliance/report.py in isolation, without
# running the full pipeline -- fast, deterministic, and covers the
# extensibility contract (multiple modules -> multiple sections in ONE
# report).
# ---------------------------------------------------------------------------

def _hipaa_check_results() -> list[CheckResult]:
    return [
        CheckResult(
            check_name="hipaa_phi",
            status="failed",
            column=None,
            issues_found=7,
            dimension="",
            details={"columns_with_phi": ["Email", "Phone"]},
        ),
        CheckResult(
            check_name="hipaa_phi",
            status="failed",
            column="Email",
            issues_found=4,
            dimension="",
            details={"identifiers": {"EMAIL": 4}},
        ),
        CheckResult(
            check_name="hipaa_phi",
            status="failed",
            column="Phone",
            issues_found=3,
            dimension="",
            details={"identifiers": {"PHONE": 3}},
        ),
    ]


def test_build_compliance_report_data_reuses_summarize_check():
    data = build_compliance_report_data(
        filepath="acme.xlsx",
        sheet_name="Sheet1",
        row_count=500,
        column_count=10,
        modules={"hipaa_phi": _hipaa_check_results()},
    )
    assert data["meta"]["filename"] == "acme.xlsx"
    assert data["meta"]["sheet_name"] == "Sheet1"
    assert data["overview"] == {"rows": 500, "columns": 10}
    section = data["sections"]["hipaa_phi"]
    assert section["total_issues_found"] == 7
    assert set(section["affected_columns"]) == {"Email", "Phone"}


def test_compliance_report_renders_one_section_per_module(tmp_path):
    """Extensibility contract: adding a second module produces a second
    section in the SAME report, not a second report file."""

    # A fake second "regulation" module, reusing hipaa_phi's shape --
    # stands in for a future GDPR module without inventing new detection
    # logic here.
    second_module = [
        CheckResult(
            check_name="gdpr_pii",
            status="passed",
            column=None,
            issues_found=0,
            dimension="",
            details={},
        )
    ]
    data = build_compliance_report_data(
        filepath="acme.xlsx",
        sheet_name="Sheet1",
        row_count=500,
        column_count=10,
        modules={"hipaa_phi": _hipaa_check_results(), "gdpr_pii": second_module},
    )
    out = generate_compliance_html_report(data, str(tmp_path / "compliance.html"))
    html = Path(out).read_text(encoding="utf-8")

    assert html.count("<!DOCTYPE html>") == 1  # one report, not two
    assert 'id="check-hipaa_phi"' in html
    assert 'id="check-gdpr_pii"' in html


def test_compliance_report_empty_state_when_no_modules(tmp_path):
    data = build_compliance_report_data(
        filepath="acme.xlsx",
        sheet_name="Sheet1",
        row_count=10,
        column_count=2,
        modules={},
    )
    out = generate_compliance_html_report(data, str(tmp_path / "empty_compliance.html"))
    html = Path(out).read_text(encoding="utf-8")
    assert "No compliance modules were assessed" in html
    assert "<html" in html.lower()
