"""Report display correctness: N/A dimensions, readiness, labels, PII counts."""

from __future__ import annotations

from backend.engine.models import CheckResult
from backend.engine.reports.html_report import _kpi_card
from backend.engine.reports.report_generator import (
    CHECK_DISPLAY_NAME,
    _effective_readiness_score,
    _readiness_from_score,
    _summarize_check,
    build_report_data,
)


def test_unavailable_dimension_kpi_shows_na_not_zero():
    html = _kpi_card(
        "freshness",
        {"score": None, "available": False, "weight": 0.04},
    )
    assert "N/A" in html
    assert ">0<" not in html
    assert "#DC2626" not in html  # must not use failing-score red for N/A


def test_readiness_uses_limiting_dimension_not_headline_only():
    score = {
        "data_quality_score": 78.38,
        "compliance_adjusted_score": 70.0,
        "dimension_scores": {
            "validity": {"available": True, "score": 60.0},
            "completeness": {"available": True, "score": 90.0},
            "freshness": {"available": False, "score": None},
        },
    }
    assert _effective_readiness_score(score) == 60.0
    assert _readiness_from_score(_effective_readiness_score(score)) == "Ready with Moderate Cleaning"


def test_readiness_not_minor_cleaning_when_compliance_caps_below_75():
    score = {
        "data_quality_score": 78.0,
        "compliance_adjusted_score": 70.0,
        "dimension_scores": {
            "completeness": {"available": True, "score": 85.0},
        },
    }
    assert _readiness_from_score(_effective_readiness_score(score)) == "Ready with Moderate Cleaning"


def test_hipaa_phi_display_name():
    assert CHECK_DISPLAY_NAME["hipaa_phi"] == "HIPAA PHI Compliance Scan"
    summary = _summarize_check(
        "hipaa_phi",
        [
            CheckResult(
                check_name="hipaa_phi",
                status="failed",
                column=None,
                issues_found=5,
                dimension="",
                details={"columns_with_phi": ["email_col", "phone_col"]},
            ),
            CheckResult(
                check_name="hipaa_phi",
                status="failed",
                column="email_col",
                issues_found=3,
                dimension="",
            ),
            CheckResult(
                check_name="hipaa_phi",
                status="failed",
                column="phone_col",
                issues_found=2,
                dimension="",
            ),
        ],
    )
    assert summary["display_name"] == "HIPAA PHI Compliance Scan"
    assert summary["total_issues_found"] == 5
    assert summary["columns_with_issues"] == 2
    assert summary["columns_checked"] == 2
    assert summary["affected_columns"] == ["email_col", "phone_col"]
    assert len(summary["column_breakdown"]) == 2
    assert summary["column_breakdown"][0]["column"] == "email_col"
    assert summary["column_breakdown"][0]["issues_found"] == 3


def test_pii_row_union_deduplicates_across_columns():
    pii_summary = {
        "email": {
            "rows_with_pii": 100,
            "type_counts": {"EMAIL": 100},
            "masked_rows": {0: "x", 1: "y", 2: "z"},
        },
        "phone": {
            "rows_with_pii": 100,
            "type_counts": {"PHONE": 100},
            "masked_rows": {1: "x", 2: "y", 3: "z"},
        },
        "clean": {"rows_with_pii": 0, "type_counts": {}, "masked_rows": {}},
    }
    report = build_report_data(
        filepath="fake.xlsx",
        sheet_name="Sheet1",
        df_shape=(10, 3),
        header_row=0,
        processing_time_seconds=1.0,
        classification={"email": "pii", "phone": "pii", "clean": "categorical"},
        check_results_by_name={},
        pii_summary_by_column=pii_summary,
        fuzzy_results=None,
        score={
            "data_quality_score": 80.0,
            "dimension_scores": {},
            "dimensions_excluded": [],
        },
    )
    assert report["pii"]["columns_with_pii"] == 2
    assert report["pii"]["total_rows_with_pii"] == 4
    assert set(report["pii"]["types_found"]) == {"EMAIL", "PHONE"}
    assert set(report["pii"]["flagged_columns"]) == {"email", "phone"}


def test_build_report_data_freshness_unavailable_in_dimension_scores():
    report = build_report_data(
        filepath="fake.xlsx",
        sheet_name="Sheet1",
        df_shape=(100, 5),
        header_row=0,
        processing_time_seconds=1.0,
        classification={},
        check_results_by_name={},
        pii_summary_by_column={},
        fuzzy_results=None,
        score={
            "data_quality_score": 85.0,
            "dimension_scores": {
                "freshness": {
                    "score": None,
                    "available": False,
                    "weight": 0.04,
                    "severity": "None",
                },
            },
            "dimensions_excluded": ["freshness"],
        },
    )
    fresh = report["score"]["dimension_scores"]["freshness"]
    assert fresh["available"] is False
    assert fresh["score"] is None
