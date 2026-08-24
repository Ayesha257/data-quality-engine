"""Regression tests for HIPAA report aggregation and registration-column gating."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.engine.ingestion import load_with_confirmed_header, read_excel_file
from backend.engine.models import CheckResult
from backend.engine.pii.detect_pii import detect_pii_in_series
from backend.engine.reports.report_generator import _summarize_check
from backend.engine.compliance.scanner import assess_hipaa_compliance_as_check_results


def _customer_list_path() -> Path | None:
    candidates = [
        Path("OneDrive_1_26-01-2026 - latest data set") / "Customer List.xls",
        Path("OneDrive_1_26-01-2026 - latest data set") / "Customer List(2).xls",
    ]
    return next((p for p in candidates if p.exists()), None)


def test_hipaa_summary_uses_file_total_and_column_counts():
    checks = assess_hipaa_compliance_as_check_results(
        {
            "Statements": {"type_counts": {"EMAIL": 409}, "rows_with_pii": 409},
            "Company Reg No.": {"type_counts": {}, "rows_with_pii": 0},
            "Telephones Landline": {"type_counts": {"PHONE": 305}, "rows_with_pii": 305},
        },
        row_count=533,
    )
    file_level = next(r for r in checks if r.column is None)
    file_level.issues_found = 714  # simulate summed identifier counts

    summary = _summarize_check("hipaa_phi", checks)
    assert summary["total_issues_found"] == 714
    assert summary["columns_with_issues"] == 2
    assert summary["columns_checked"] == 2
    assert set(summary["affected_columns"]) == {"Statements", "Telephones Landline"}
    assert len(summary["column_breakdown"]) == 2
    assert all(row["identifiers"] for row in summary["column_breakdown"])


@pytest.mark.skipif(_customer_list_path() is None, reason="Customer List fixture not present")
def test_customer_list_company_reg_no_longer_contributes_false_positives():
    path = _customer_list_path()
    raw = read_excel_file(str(path))["Sheet1"]
    df = load_with_confirmed_header(raw, 3)

    reg_summary = detect_pii_in_series(df["Company Reg No."])
    assert reg_summary["rows_with_pii"] == 0

    summaries = {str(c): detect_pii_in_series(df[c]) for c in df.columns}
    checks = assess_hipaa_compliance_as_check_results(summaries, len(df))
    file_level = next(r for r in checks if r.column is None)
    summary = _summarize_check("hipaa_phi", checks)

    assert "Company Reg No." not in summary["affected_columns"]
    assert summary["columns_with_issues"] == 9
    assert summary["total_issues_found"] == file_level.issues_found
    assert summary["total_issues_found"] < 1881
    assert summary["total_issues_found"] >= 1600
