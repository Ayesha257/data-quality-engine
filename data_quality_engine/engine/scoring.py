"""Composite Data Quality Score -> teacher rubric + privacy sensitivity.

Scores all rubric dimensions when results are supplied:
completeness, validity, type_reliability, consistency, uniqueness,
schema_quality, outlier_risk, freshness, privacy_sensitivity (PII).

HIPAA (M9) exposure is reported separately (not a rubric dimension) but
applies a composite ceiling when PHI exposure severity is elevated so
Critical HIPAA findings cannot coexist with a headline score of 100.

Design choices:

1. Weights come from SETTINGS["rubric_dimension_weights"] (9 dimensions,
   sum to 1.0). Callers pass check results under the rubric key they
   belong to; PII is auto-materialized as privacy_sensitivity from
   detect_pii_in_series summaries when supplied.

2. Missing dimensions are excluded transparently (listed in
   dimensions_excluded); their weight is not silently redistributed
   without reporting scorable_weight_fraction.

3. Per-dimension formula: score = 100 * average(quality_ratio) across
   assessed results (see _result_quality). Binary checks without
   quality_ratio use passed=1.0 / failed=0.0.

4. Composite adjustments (applied after the weighted average):
   - Dimension scores already scale with affected volume via quality_ratio
     (duplicates, missing) or column pass/fail rates (PII). No flat
     "Critical = 59" ceiling — that collapsed 1/10 and 8/10 PII columns
     to the same headline score.
   - HIPAA exposure (separate M9 score) applies a proportional ceiling:
     cap = 100 - (exposure_score/100) * (100 - 59). exposure 0 -> no cap;
     exposure 100 -> cap 59; values in between scale linearly.
   - Final score = min(raw_composite, applicable HIPAA cap).

5. privacy_risk is retained for backward-compatible reporting but PII
   now also flows through privacy_sensitivity in the composite.
"""

from __future__ import annotations

from typing import Any

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.models import CheckResult

RUBRIC_DIMENSIONS = (
    "completeness",
    "validity",
    "type_reliability",
    "consistency",
    "uniqueness",
    "schema_quality",
    "outlier_risk",
    "freshness",
    "privacy_sensitivity",
)

_DEFAULT_WEIGHTS = {
    "completeness": 0.18,
    "validity": 0.18,
    "type_reliability": 0.14,
    "consistency": 0.14,
    "uniqueness": 0.09,
    "schema_quality": 0.09,
    "outlier_risk": 0.04,
    "freshness": 0.04,
    "privacy_sensitivity": 0.10,
}

# Severity labels for reporting (report_generator._severity_from_ratio).
# >=50% failed checks in a dimension -> "Critical" label only — does NOT
# trigger a flat composite cap (see _apply_composite_adjustments).
COMPOSITE_FLOOR = 59.0

# Legacy alias kept for tests/docs referencing the old flat cap value.
CRITICAL_SEVERITY_COMPOSITE_CAP = COMPOSITE_FLOOR


def _severity_from_ratio(ratio: float) -> str:
    """Align with engine/reporting/report_generator._severity_from_ratio."""
    if ratio >= 0.50:
        return "Critical"
    if ratio >= 0.20:
        return "High"
    if ratio >= 0.05:
        return "Medium"
    if ratio > 0:
        return "Low"
    return "None"


def _is_role_skip(result: CheckResult) -> bool:
    """True when a check was intentionally not applicable (wrong column role)."""
    reason = result.details.get("reason")
    return isinstance(reason, str) and reason.startswith("skipped_")


def _result_quality(result: CheckResult) -> float:
    """
    Per-result quality in [0.0, 1.0] used to build a dimension's score.
    """
    if result.quality_ratio is not None:
        return max(0.0, min(1.0, float(result.quality_ratio)))
    return 1.0 if result.status == "passed" else 0.0


def _dimension_sub_score(results: list[CheckResult]) -> dict[str, Any]:
    errored = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status != "error" and _is_role_skip(r))
    assessed = [r for r in results if r.status != "error" and not _is_role_skip(r)]
    if not assessed:
        return {
            "score": None,
            "passed": 0,
            "total": 0,
            "skipped": skipped,
            "errored": errored,
            "failed": 0,
            "severity": "None",
        }
    passed = sum(1 for r in assessed if r.status == "passed")
    failed = len(assessed) - passed
    total = len(assessed)
    avg_quality = sum(_result_quality(r) for r in assessed) / total
    fail_ratio = failed / total
    return {
        "score": round(100.0 * avg_quality, 2),
        "passed": passed,
        "total": total,
        "failed": failed,
        "skipped": skipped,
        "errored": errored,
        "severity": _severity_from_ratio(fail_ratio),
    }


def _pii_check_results(
    pii_summary_by_column: dict[str, dict[str, Any]] | None,
) -> list[CheckResult]:
    """
    Materialize one CheckResult per column for the privacy_sensitivity
    rubric dimension from detect_pii_in_series() summaries.
    """
    if not pii_summary_by_column:
        return []

    results: list[CheckResult] = []
    for col, summary in pii_summary_by_column.items():
        rows_with_pii = int(summary.get("rows_with_pii") or 0)
        status = "passed" if rows_with_pii == 0 else "failed"
        results.append(
            CheckResult(
                check_name="pii",
                status=status,
                column=str(col),
                issues_found=rows_with_pii,
                dimension="privacy_sensitivity",
                quality_ratio=0.0 if rows_with_pii > 0 else 1.0,
            )
        )
    return results


def _privacy_risk(
    pii_summary_by_column: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """
    Standalone PII risk summary for reports. PII also affects the
    composite via privacy_sensitivity when summaries are supplied.
    """
    if not pii_summary_by_column:
        return None

    total_columns = len(pii_summary_by_column)
    flagged = {
        col: summary
        for col, summary in pii_summary_by_column.items()
        if summary.get("rows_with_pii", 0) > 0
    }
    columns_with_pii = len(flagged)
    ratio = (columns_with_pii / total_columns) if total_columns else 0.0

    if columns_with_pii == 0:
        level = "none"
    elif ratio < 0.10:
        level = "low"
    elif ratio < 0.30:
        level = "medium"
    else:
        level = "high"

    types_found: set[str] = set()
    for summary in flagged.values():
        types_found.update(summary.get("type_counts", {}).keys())

    return {
        "risk_level": level,
        "columns_with_pii": columns_with_pii,
        "total_columns": total_columns,
        "columns_flagged": sorted(flagged.keys()),
        "pii_types_found": sorted(types_found),
        "note": "Also scored in composite via privacy_sensitivity dimension.",
    }


def _hipaa_exposure_block(hipaa_exposure: Any | None) -> dict[str, Any] | None:
    if hipaa_exposure is None:
        return None
    if hasattr(hipaa_exposure, "exposure_score"):
        return {
            "exposure_score": float(hipaa_exposure.exposure_score),
            "severity": str(hipaa_exposure.severity),
            "identifiers_detected": int(hipaa_exposure.identifiers_detected),
            "columns_affected": int(hipaa_exposure.columns_affected),
            "note": "Separate M9 exposure score; applies composite ceiling when severity > none.",
        }
    if isinstance(hipaa_exposure, dict):
        return {
            "exposure_score": float(hipaa_exposure.get("exposure_score", 0)),
            "severity": str(hipaa_exposure.get("severity", "none")),
            "identifiers_detected": int(hipaa_exposure.get("identifiers_detected", 0)),
            "columns_affected": int(hipaa_exposure.get("columns_affected", 0)),
            "note": "Separate M9 exposure score; applies composite ceiling when severity > none.",
        }
    return None


def _hipaa_proportional_cap(exposure_score: float) -> float | None:
    """
    Scale HIPAA ceiling with exposure_score (0-100).

    exposure 0   -> None (no cap)
    exposure 100 -> 59
    exposure 50  -> 79.5
    """
    if exposure_score <= 0:
        return None
    return round(100.0 - (exposure_score / 100.0) * (100.0 - COMPOSITE_FLOOR), 2)


def _apply_composite_adjustments(
    raw_composite: float,
    dimension_scores: dict[str, Any],
    hipaa_exposure: Any | None,
) -> tuple[float, dict[str, Any]]:
    """
    Apply HIPAA exposure proportional cap to the weighted average.
    Critical-dimension labels do NOT apply a flat ceiling — dimension
    scores already reflect the fraction of checks/columns affected.
    """
    caps_applied: list[dict[str, Any]] = []
    adjusted = raw_composite

    critical_dims = [
        dim
        for dim, info in dimension_scores.items()
        if info.get("available") and info.get("severity") == "Critical"
    ]

    exposure = _hipaa_exposure_block(hipaa_exposure)
    if exposure and exposure["exposure_score"] > 0:
        hipaa_cap = _hipaa_proportional_cap(exposure["exposure_score"])
        if hipaa_cap is not None and adjusted > hipaa_cap:
            caps_applied.append(
                {
                    "reason": "hipaa_exposure",
                    "severity": exposure["severity"],
                    "cap": hipaa_cap,
                    "exposure_score": exposure["exposure_score"],
                    "detail": (
                        f"proportional ceiling: 100 - (exposure/100)*({100 - COMPOSITE_FLOOR:.0f})"
                    ),
                }
            )
            adjusted = hipaa_cap

    return round(adjusted, 2), {
        "raw_composite": round(raw_composite, 2),
        "caps_applied": caps_applied,
        "critical_dimensions": critical_dims,
    }


def compute_data_quality_score(
    dimension_results: dict[str, list[CheckResult]],
    weights: dict[str, float] | None = None,
    pii_summary_by_column: dict[str, dict[str, Any]] | None = None,
    hipaa_exposure: Any | None = None,
) -> dict[str, Any]:
    """
    dimension_results: rubric dimension name -> list of CheckResult.
    pii_summary_by_column: Task 4 output; auto-populates privacy_sensitivity
        unless that key is already present in dimension_results.
    hipaa_exposure: HipaaComplianceScore (or dict) from M9; applies a
        composite ceiling by exposure severity without becoming a rubric dim.

    Returns:
        {
            "data_quality_score": float | None,      # after caps
            "data_quality_score_raw": float | None,  # weighted avg before caps
            "composite_adjustments": {...},
            "scorable_weight_fraction": float,
            "dimension_scores": {dim: {score, passed, total, severity, ...}},
            "dimensions_excluded": [dim, ...],
            "privacy_risk": {...} | None,
            "hipaa_exposure": {...} | None,
        }
    """
    try:
        weights = weights or SETTINGS.get("rubric_dimension_weights", _DEFAULT_WEIGHTS)

        merged_results = dict(dimension_results)
        if pii_summary_by_column and "privacy_sensitivity" not in merged_results:
            merged_results["privacy_sensitivity"] = _pii_check_results(pii_summary_by_column)

        unknown = set(merged_results) - set(RUBRIC_DIMENSIONS)
        if unknown:
            raise ValueError(
                f"Unknown rubric dimension(s) in dimension_results: {sorted(unknown)}. "
                f"Expected a subset of {RUBRIC_DIMENSIONS}."
            )

        dimension_scores: dict[str, Any] = {}
        available: dict[str, float] = {}

        for dim in RUBRIC_DIMENSIONS:
            weight = float(weights.get(dim, 0.0))
            results = merged_results.get(dim)
            if not results:
                dimension_scores[dim] = {
                    "score": None,
                    "passed": 0,
                    "total": 0,
                    "failed": 0,
                    "skipped": 0,
                    "errored": 0,
                    "severity": "None",
                    "weight": weight,
                    "available": False,
                }
                continue

            sub = _dimension_sub_score(results)
            dimension_scores[dim] = {**sub, "weight": weight, "available": sub["score"] is not None}
            if sub["score"] is not None:
                available[dim] = sub["score"]

        weight_total = sum(float(weights.get(d, 0.0)) for d in RUBRIC_DIMENSIONS)
        scorable_weight = sum(float(weights.get(d, 0.0)) for d in available)
        scorable_fraction = (scorable_weight / weight_total) if weight_total else 0.0

        if available and scorable_weight > 0:
            raw_composite = sum(
                available[dim] * float(weights.get(dim, 0.0)) for dim in available
            ) / scorable_weight
            raw_composite = round(raw_composite, 2)
            composite, adjustments = _apply_composite_adjustments(
                raw_composite, dimension_scores, hipaa_exposure
            )
        else:
            raw_composite = None
            composite = None
            adjustments = {"raw_composite": None, "caps_applied": [], "critical_dimensions": []}

        return {
            "data_quality_score": composite,
            "data_quality_score_raw": raw_composite,
            "composite_adjustments": adjustments,
            "scorable_weight_fraction": round(scorable_fraction, 4),
            "dimension_scores": dimension_scores,
            "dimensions_excluded": [d for d in RUBRIC_DIMENSIONS if d not in available],
            "privacy_risk": _privacy_risk(pii_summary_by_column),
            "hipaa_exposure": _hipaa_exposure_block(hipaa_exposure),
        }
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return {
            "data_quality_score": None,
            "data_quality_score_raw": None,
            "composite_adjustments": {"raw_composite": None, "caps_applied": [], "critical_dimensions": []},
            "error": str(exc),
            "dimension_scores": {},
            "dimensions_excluded": list(RUBRIC_DIMENSIONS),
            "privacy_risk": None,
            "hipaa_exposure": None,
        }
