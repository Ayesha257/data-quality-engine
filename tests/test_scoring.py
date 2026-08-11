"""Tests for scoring.py (composite Data Quality Score, teacher rubric)."""

from __future__ import annotations

from data_quality_engine.engine.models import CheckResult
from data_quality_engine.engine.scoring import (
    CRITICAL_SEVERITY_COMPOSITE_CAP,
    RUBRIC_DIMENSIONS,
    compute_data_quality_score,
)
from data_quality_engine.phase2.compliance.scoring import HipaaComplianceScore


def _result(status: str, dimension: str = "completeness") -> CheckResult:
    return CheckResult(
        check_name="x", status=status, column="c", issues_found=0, dimension=dimension
    )


def test_all_nine_dimensions_present_gives_full_composite():
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
    # weight scorable = completeness(0.18) + uniqueness(0.09) = 0.27 of 1.0
    assert abs(out["scorable_weight_fraction"] - 0.27) < 1e-6
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
    dimension_results = {
        "completeness": [_result("passed"), _result("passed")],
        # 1/4 failed = 25% -> High severity, not Critical (no cap)
        "uniqueness": [_result("passed"), _result("passed"), _result("passed"), _result("failed")],
    }
    out = compute_data_quality_score(dimension_results)
    # weighted: (100*0.18 + 75*0.09) / (0.18+0.09) = 24.75/0.27 = 91.67
    assert abs(out["data_quality_score"] - 91.67) < 0.01
    assert out["data_quality_score_raw"] == out["data_quality_score"]


def test_custom_weights_override_settings_default():
    dimension_results = {"completeness": [_result("passed")], "uniqueness": [_result("failed")]}
    out = compute_data_quality_score(
        dimension_results, weights={"completeness": 1.0, "uniqueness": 1.0}
    )
    assert out["data_quality_score"] == 50.0


def test_pii_affects_composite_via_privacy_sensitivity():
    dimension_results = {"completeness": [_result("passed")]}
    pii_summary = {
        "Email": {"rows_with_pii": 5, "type_counts": {"EMAIL": 5}},
        "Notes": {"rows_with_pii": 0, "type_counts": {}},
    }
    out = compute_data_quality_score(dimension_results, pii_summary_by_column=pii_summary)
    # completeness 100 (w=0.18) + privacy 50 (1/2 cols clean, w=0.10) -> raw ~96.43
    assert out["data_quality_score_raw"] is not None
    assert out["data_quality_score_raw"] < 100.0
    assert out["dimension_scores"]["privacy_sensitivity"]["score"] == 50.0
    assert out["privacy_risk"]["columns_with_pii"] == 1


def test_proportional_pii_prevalence_one_vs_eight_columns():
    """1/10 PII columns must score higher than 8/10 — no flat Critical cap."""
    base_dims = {dim: [_result("passed")] for dim in RUBRIC_DIMENSIONS if dim != "privacy_sensitivity"}

    def pii_summary(flagged_cols: int, total: int = 10) -> dict:
        summary = {}
        for i in range(total):
            col = f"col_{i}"
            if i < flagged_cols:
                summary[col] = {"rows_with_pii": 1, "type_counts": {"EMAIL": 1}}
            else:
                summary[col] = {"rows_with_pii": 0, "type_counts": {}}
        return summary

    out_low = compute_data_quality_score(base_dims, pii_summary_by_column=pii_summary(1))
    out_high = compute_data_quality_score(base_dims, pii_summary_by_column=pii_summary(8))

    assert out_low["data_quality_score"] > out_high["data_quality_score"]
    assert out_low["dimension_scores"]["privacy_sensitivity"]["score"] == 90.0
    assert out_high["dimension_scores"]["privacy_sensitivity"]["score"] == 20.0
    assert out_low["data_quality_score"] == out_low["data_quality_score_raw"]
    assert out_high["data_quality_score"] == out_high["data_quality_score_raw"]
    # 1/10: privacy 90% -> composite ~99; 8/10: privacy 20% -> composite ~92
    assert out_low["data_quality_score"] >= 98.0
    assert out_high["data_quality_score"] <= 93.0


def test_no_flat_critical_cap_when_most_dimensions_perfect():
    """3/4 PII columns: Critical label but composite stays near weighted average."""
    base_dims = {dim: [_result("passed")] for dim in RUBRIC_DIMENSIONS if dim != "privacy_sensitivity"}
    pii_summary = {
        "MRN": {"rows_with_pii": 1, "type_counts": {"MRN": 1}},
        "ssn": {"rows_with_pii": 1, "type_counts": {"SSN": 1}},
        "email": {"rows_with_pii": 1, "type_counts": {"EMAIL": 1}},
        "order_id": {"rows_with_pii": 0, "type_counts": {}},
    }
    out = compute_data_quality_score(base_dims, pii_summary_by_column=pii_summary)
    assert out["dimension_scores"]["privacy_sensitivity"]["severity"] == "Critical"
    assert out["data_quality_score"] == out["data_quality_score_raw"]
    assert out["data_quality_score"] >= 90.0
    assert out["data_quality_score"] > CRITICAL_SEVERITY_COMPOSITE_CAP
    assert not any(
        c.get("reason") == "critical_dimension" for c in out["composite_adjustments"]["caps_applied"]
    )


def test_hipaa_proportional_cap_scales_with_exposure():
    dimension_results = {"completeness": [_result("passed")]}
    low = HipaaComplianceScore(
        exposure_score=30.0, identifiers_detected=1, columns_affected=1, severity="low"
    )
    high = HipaaComplianceScore(
        exposure_score=90.0, identifiers_detected=4, columns_affected=3, severity="high"
    )
    out_low = compute_data_quality_score(dimension_results, hipaa_exposure=low)
    out_high = compute_data_quality_score(dimension_results, hipaa_exposure=high)
    assert out_low["data_quality_score"] > out_high["data_quality_score"]
    assert out_high["data_quality_score"] >= 59.0


def test_clean_data_with_no_pii_scores_100():
    dimension_results = {dim: [_result("passed")] for dim in RUBRIC_DIMENSIONS}
    pii_summary = {
        "order_id": {"rows_with_pii": 0, "type_counts": {}},
        "quantity": {"rows_with_pii": 0, "type_counts": {}},
    }
    out = compute_data_quality_score(dimension_results, pii_summary_by_column=pii_summary)
    assert out["data_quality_score"] == 100.0
    assert out["data_quality_score_raw"] == 100.0
    assert not out["composite_adjustments"]["caps_applied"]


def test_privacy_risk_none_when_no_pii_summary_given():
    out = compute_data_quality_score({"completeness": [_result("passed")]})
    assert out["privacy_risk"] is None


def test_privacy_risk_level_none_when_no_pii_found():
    pii_summary = {"Notes": {"rows_with_pii": 0, "type_counts": {}}}
    out = compute_data_quality_score(
        {"completeness": [_result("passed")]}, pii_summary_by_column=pii_summary
    )
    assert out["privacy_risk"]["risk_level"] == "none"


def test_role_skips_do_not_inflate_dimension_score():
    """Skipped non-applicable columns must not count as passed."""
    skipped = CheckResult(
        check_name="outliers",
        status="passed",
        column="InvoiceNo",
        issues_found=0,
        details={"reason": "skipped_non_measurement_column", "classified_role": "identifier"},
        dimension="validity",
    )
    failed = CheckResult(
        check_name="outliers",
        status="failed",
        column="Amount",
        issues_found=3,
        details={"method": "iqr"},
        dimension="validity",
    )
    out = compute_data_quality_score({"outlier_risk": [skipped, skipped, failed]})
    assert out["dimension_scores"]["outlier_risk"]["score"] == 0.0
    assert out["dimension_scores"]["outlier_risk"]["total"] == 1
    assert out["dimension_scores"]["outlier_risk"]["skipped"] == 2
