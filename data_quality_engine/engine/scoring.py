"""Composite Data Quality Score -> teacher's 8-dimension rubric.

Scores all 8 rubric dimensions when results are supplied:
completeness, validity, type_reliability, consistency, uniqueness,
schema_quality, outlier_risk, freshness. Privacy risk (Task 4 PII) is
reported separately and never folded into the composite.

Design choices (match the teacher's brief):

1. Weights come from SETTINGS["rubric_dimension_weights"] (8 dimensions,
   sum to 1.0), not from CheckResult.dimension on individual checks.
   Those two sets do not line up 1:1 -- e.g. type_mismatch and outliers
   both tag dimension="validity" internally, but the rubric splits them
   into type_reliability and outlier_risk, and reserves "validity" for
   value-rule checks (negatives, date order, suspicious zeros). Callers
   pass results under the rubric key they belong to.

2. Missing dimensions are excluded transparently (listed in
   dimensions_excluded); their weight is not silently redistributed
   without reporting scorable_weight_fraction.

3. Privacy Risk is a separate field -- a dataset can score high on
   analytical quality and still be unsafe to share.

Per-dimension formula: score = 100 * (passed / assessed), where
"assessed" excludes status="error" and role-skip results whose
details["reason"] starts with "skipped_" (e.g. outlier checks on
identifier columns). Skips must not inflate the score.
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
)

_DEFAULT_WEIGHTS = {
    "completeness": 0.20,
    "validity": 0.20,
    "type_reliability": 0.15,
    "consistency": 0.15,
    "uniqueness": 0.10,
    "schema_quality": 0.10,
    "outlier_risk": 0.05,
    "freshness": 0.05,
}


def _is_role_skip(result: CheckResult) -> bool:
    """True when a check was intentionally not applicable (wrong column role)."""
    reason = result.details.get("reason")
    return isinstance(reason, str) and reason.startswith("skipped_")


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
        }
    passed = sum(1 for r in assessed if r.status == "passed")
    total = len(assessed)
    return {
        "score": round(100.0 * passed / total, 2),
        "passed": passed,
        "total": total,
        "skipped": skipped,
        "errored": errored,
    }


def _privacy_risk(
    pii_summary_by_column: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """
    Summarize Task 4 PII results as a standalone risk report -- never part
    of the composite score. pii_summary_by_column is column_name ->
    detect_pii_in_series() output (the same dict shape main.py already
    loops over in _print_task4_results), NOT a list of CheckResult, since
    PII detection doesn't produce CheckResults today.
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
        "note": "Reported separately -- never subtracted from data_quality_score.",
    }


def compute_data_quality_score(
    dimension_results: dict[str, list[CheckResult]],
    weights: dict[str, float] | None = None,
    pii_summary_by_column: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    dimension_results: rubric dimension name -> list of CheckResult already
        run for that dimension. Keys must be a subset of RUBRIC_DIMENSIONS.
        Dimensions not present are treated as "not yet available", not as
        a failing score of 0.
    weights: overrides SETTINGS["rubric_dimension_weights"] if given.
    pii_summary_by_column: optional Task 4 output for the separate privacy
        risk report (see _privacy_risk docstring for the expected shape).

    Returns a dict (not a CheckResult -- this is a composite report over
    many checks, not a single pass/fail result):
        {
            "data_quality_score": float | None,
            "scorable_weight_fraction": float,   # e.g. 0.85 if dims worth
                                                  # 85% of total weight were
                                                  # actually available
            "dimension_scores": {dim: {score, passed, total, errored,
                                        weight, available}},
            "dimensions_excluded": [dim, ...],
            "privacy_risk": {...} | None,
        }
    """
    try:
        weights = weights or SETTINGS.get("rubric_dimension_weights", _DEFAULT_WEIGHTS)

        unknown = set(dimension_results) - set(RUBRIC_DIMENSIONS)
        if unknown:
            raise ValueError(
                f"Unknown rubric dimension(s) in dimension_results: {sorted(unknown)}. "
                f"Expected a subset of {RUBRIC_DIMENSIONS}."
            )

        dimension_scores: dict[str, Any] = {}
        available: dict[str, float] = {}

        for dim in RUBRIC_DIMENSIONS:
            weight = float(weights.get(dim, 0.0))
            results = dimension_results.get(dim)
            if not results:
                dimension_scores[dim] = {
                    "score": None,
                    "passed": 0,
                    "total": 0,
                    "skipped": 0,
                    "errored": 0,
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
            composite = sum(
                available[dim] * float(weights.get(dim, 0.0)) for dim in available
            ) / scorable_weight
            composite = round(composite, 2)
        else:
            composite = None

        return {
            "data_quality_score": composite,
            "scorable_weight_fraction": round(scorable_fraction, 4),
            "dimension_scores": dimension_scores,
            "dimensions_excluded": [d for d in RUBRIC_DIMENSIONS if d not in available],
            "privacy_risk": _privacy_risk(pii_summary_by_column),
        }
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return {
            "data_quality_score": None,
            "error": str(exc),
            "dimension_scores": {},
            "dimensions_excluded": list(RUBRIC_DIMENSIONS),
            "privacy_risk": None,
        }
