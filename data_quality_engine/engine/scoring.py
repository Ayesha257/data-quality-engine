"""Composite Data Quality Score -> teacher's 8-dimension rubric.

This is a *skeleton*: it can score any subset of the 8 rubric dimensions
today (the 4 that are fully reusable from existing checks -- completeness,
type_reliability, uniqueness, outlier_risk -- plus the 2 built in this
pass -- schema_quality, consistency) and will pick up "validity" and
"freshness" automatically once those check modules exist, with zero
changes needed here.

Design choices, both deliberate and matching the teacher's brief exactly:

1. Weights come from SETTINGS["rubric_dimension_weights"] (8 dimensions,
   sums to 1.0), not from the older CheckResult.dimension values that
   existing checks already use internally. Those two dimension sets don't
   line up 1:1 -- e.g. type_mismatch.py and outliers.py both set
   CheckResult.dimension = "validity" for historical/internal reasons, but
   the rubric treats "type reliability" (dominant-type mismatches) and
   "outlier risk" (IQR/KNN flags) as two separate weighted dimensions, and
   reserves "validity" for a not-yet-built check (invalid numeric/date
   *values*, not type mismatches). So callers pass results in explicitly
   under the rubric dimension name they belong to -- this module does not
   try to infer rubric dimension from CheckResult.dimension.

2. If a rubric dimension has no results supplied (module not built yet, or
   simply not run for a given sheet), it is excluded from the composite
   score and its weight is *not* silently redistributed without saying so
   -- the returned dict always lists which dimensions were excluded and
   what fraction of total rubric weight was actually scorable, so a
   partial score is never presented as if it were a full one.

3. Privacy Risk (from PII detection, Task 4) is reported as a fully
   separate field and is never folded into the composite score -- this is
   the explicit "Important design choice" from the rubric: a dataset can
   be analytically high quality but still unsafe to share.

Per-dimension sub-score formula (Phase 1, matches the simplicity of every
other "Phase 1 Method" in the rubric table): for a dimension's list of
CheckResults, score = 100 * (passed_count / total_non_error_count). This
mirrors how the CLI report already summarizes every check today (e.g.
"Columns with type issues: n / total") -- it's just that ratio turned into
a 0-100 number and weighted.
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


def _dimension_sub_score(results: list[CheckResult]) -> dict[str, Any]:
    non_error = [r for r in results if r.status != "error"]
    if not non_error:
        return {"score": None, "passed": 0, "total": 0, "errored": len(results)}
    passed = sum(1 for r in non_error if r.status == "passed")
    total = len(non_error)
    return {
        "score": round(100.0 * passed / total, 2),
        "passed": passed,
        "total": total,
        "errored": len(results) - len(non_error),
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
