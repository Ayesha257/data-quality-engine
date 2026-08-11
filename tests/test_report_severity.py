"""Severity badge alignment with dimension scores."""

from __future__ import annotations

from data_quality_engine.engine.models import CheckResult
from data_quality_engine.engine.reporting.report_generator import (
    CHECK_TO_DIMENSION,
    _align_check_severities_with_dimensions,
    _impact_ratio_from_results,
    _severity_from_ratio,
    _severity_from_score,
    _summarize_check,
)


def _result(passed: bool, *, quality_ratio: float | None = None, issues: int = 0) -> CheckResult:
    return CheckResult(
        check_name="duplicates",
        status="passed" if passed else "failed",
        column="c",
        issues_found=issues,
        quality_ratio=quality_ratio,
        dimension="uniqueness",
    )


def test_duplicates_few_rows_low_severity_not_critical():
    """4 dup rows in 400 rows -> ~99% quality -> Low, not Critical."""
    results = [
        _result(False, quality_ratio=1.0 - 4 / 400, issues=4),
        _result(True, quality_ratio=1.0),
        _result(True, quality_ratio=1.0),
    ]
    summary = _summarize_check("duplicates", results)
    assert summary["severity"] == "Low"
    assert summary["impact_ratio"] < 0.05


def test_old_binary_fail_ratio_would_have_been_critical():
    """Document the bug: 1 failed check out of 1 assessed -> Critical under old logic."""
    failed_only_ratio = 1 / 1
    assert _severity_from_ratio(failed_only_ratio) == "Critical"


def test_dimension_alignment_overrides_check_summary():
    summaries = {
        "duplicates": _summarize_check(
            "duplicates",
            [_result(False, quality_ratio=0.99, issues=4)],
        )
    }
    _align_check_severities_with_dimensions(
        summaries,
        {"uniqueness": {"available": True, "score": 99.0}},
    )
    assert summaries["duplicates"]["severity"] == "Low"
    assert summaries["duplicates"]["dimension_score"] == 99.0


def test_consistency_score_68_maps_to_high():
    assert _severity_from_score(68.0) == "High"


def test_all_nine_dimension_checks_have_mapping():
    expected = {
        "missing_values",
        "duplicates",
        "type_mismatch",
        "outliers",
        "consistency",
        "schema_quality",
        "validity",
        "freshness",
        "pii",
    }
    assert expected <= set(CHECK_TO_DIMENSION)


def test_binary_checks_use_pass_fail_quality():
    results = [
        CheckResult(check_name="consistency", status="failed", column="a", issues_found=1),
        CheckResult(check_name="consistency", status="passed", column="b", issues_found=0),
        CheckResult(check_name="consistency", status="passed", column="c", issues_found=0),
    ]
    impact = _impact_ratio_from_results(results)
    assert abs(impact - 1 / 3) < 0.01
    assert _severity_from_ratio(impact) == "High"
