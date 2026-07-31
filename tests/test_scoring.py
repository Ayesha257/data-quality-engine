"""Tests for scoring.py (composite Data Quality Score, teacher rubric)."""

from __future__ import annotations

from data_quality_engine.engine.models import CheckResult
from data_quality_engine.engine.scoring import (
    RUBRIC_DIMENSIONS,
    compute_data_quality_score,
)


def _result(status: str, dimension: str = "completeness") -> CheckResult:
    return CheckResult(
        check_name="x", status=status, column="c", issues_found=0, dimension=dimension
    )


def test_all_eight_dimensions_present_gives_full_composite():
    dimension_results = {dim: [_result("passed"), _result("failed")] for dim in RUBRIC_DIMENSIONS}
    out = compute_data_quality_score(dimension_results)
    assert out["data_quality_score"] == 50.0  # every dim is 50% passed
    assert out["scorable_weight_fraction"] == 1.0
    assert out["dimensions_excluded"] == []


def test_partial_dimensions_excludes_missing_ones_transparently():
    dimension_results = {
        "completeness": [_result("passed"), _result("passed")],
        "uniqueness": [_result("passed")],
    }
    out = compute_data_quality_score(dimension_results)
    assert out["data_quality_score"] == 100.0
    # weight scorable = completeness(0.20) + uniqueness(0.10) = 0.30 of 1.0
    assert abs(out["scorable_weight_fraction"] - 0.30) < 1e-6
    assert set(out["dimensions_excluded"]) == set(RUBRIC_DIMENSIONS) - {
        "completeness",
        "uniqueness",
    }


def test_no_dimensions_supplied_returns_none_score():
    out = compute_data_quality_score({})
    assert out["data_quality_score"] is None
    assert out["scorable_weight_fraction"] == 0.0
    assert set(out["dimensions_excluded"]) == set(RUBRIC_DIMENSIONS)


def test_all_error_status_in_a_dimension_excludes_it():
    dimension_results = {"completeness": [_result("error"), _result("error")]}
    out = compute_data_quality_score(dimension_results)
    assert out["dimension_scores"]["completeness"]["available"] is False
    assert out["data_quality_score"] is None


def test_unknown_dimension_key_returns_error_dict_not_crash():
    out = compute_data_quality_score({"not_a_real_dimension": [_result("passed")]})
    assert out["data_quality_score"] is None
    assert "error" in out


def test_weighted_dimensions_combine_correctly():
    # completeness weight 0.20, all passed (100); uniqueness weight 0.10, all failed (0)
    dimension_results = {
        "completeness": [_result("passed"), _result("passed")],
        "uniqueness": [_result("failed"), _result("failed")],
    }
    out = compute_data_quality_score(dimension_results)
    # weighted: (100*0.20 + 0*0.10) / (0.20+0.10) = 20/0.30 = 66.67
    assert abs(out["data_quality_score"] - 66.67) < 0.01


def test_custom_weights_override_settings_default():
    dimension_results = {"completeness": [_result("passed")], "uniqueness": [_result("failed")]}
    out = compute_data_quality_score(
        dimension_results, weights={"completeness": 1.0, "uniqueness": 1.0}
    )
    # equal weights now: (100*1.0 + 0*1.0) / 2.0 = 50.0
    assert out["data_quality_score"] == 50.0


def test_privacy_risk_is_separate_and_never_affects_composite():
    dimension_results = {"completeness": [_result("passed")]}
    pii_summary = {
        "Email": {"rows_with_pii": 5, "type_counts": {"EMAIL": 5}},
        "Notes": {"rows_with_pii": 0, "type_counts": {}},
    }
    out = compute_data_quality_score(dimension_results, pii_summary_by_column=pii_summary)
    assert out["data_quality_score"] == 100.0  # unaffected by PII presence
    assert out["privacy_risk"]["columns_with_pii"] == 1
    assert out["privacy_risk"]["total_columns"] == 2
    assert "EMAIL" in out["privacy_risk"]["pii_types_found"]
    assert out["privacy_risk"]["risk_level"] == "high"  # 1/2 = 0.5 ratio (>= 0.30 threshold)


def test_privacy_risk_none_when_no_pii_summary_given():
    out = compute_data_quality_score({"completeness": [_result("passed")]})
    assert out["privacy_risk"] is None


def test_privacy_risk_level_none_when_no_pii_found():
    pii_summary = {"Notes": {"rows_with_pii": 0, "type_counts": {}}}
    out = compute_data_quality_score(
        {"completeness": [_result("passed")]}, pii_summary_by_column=pii_summary
    )
    assert out["privacy_risk"]["risk_level"] == "none"
