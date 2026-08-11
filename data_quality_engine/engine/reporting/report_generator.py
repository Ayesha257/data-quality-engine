"""Builds one structured report-data dict from existing pipeline outputs.

This module does NOT run any checks and does NOT recalculate anything --
it only reshapes CheckResult lists (already produced by engine/checks/*.py)
and scoring.py's output into the shape the PDF/HTML/Excel renderers need.
All text is deterministic template strings, never AI-generated, per the
report requirements (business-impact mapping, severity, recommendations
are all rule-based lookups keyed off check_name/dimension).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from data_quality_engine.engine.models import CheckResult

# ---------------------------------------------------------------------------
# Deterministic lookup tables (rule-based, not AI-generated)
# ---------------------------------------------------------------------------

BUSINESS_IMPACT = {
    "missing_values": "May produce inaccurate analytics and unreliable reporting.",
    "duplicates": "Can create duplicate customers/orders and inflate revenue or count metrics.",
    "type_mismatch": "Charting, grouping, and trend analysis may silently break or mislead.",
    "outliers": "May distort averages, totals, and any ML model trained on this data.",
    "pii": "Privacy and regulatory compliance risk if shared or stored without masking.",
    "consistency": "Grouping and aggregation become unreliable (e.g. 'Paid' vs 'paid' split apart).",
    "schema_quality": "Downstream tools and analysts may misread or skip unclear columns.",
    "validity": "Values that look present may still be unusable or logically wrong.",
    "freshness": "Decisions may be based on stale data without anyone noticing.",
    "referential_integrity": "Records may reference customers/suppliers/products that don't exist.",
    "hipaa_phi": "Protected health information may require HIPAA safeguards before use or sharing.",
}

RECOMMENDATION = {
    "missing_values": "Improve data collection at source; consider a required-field rule for critical columns.",
    "duplicates": "Add a uniqueness constraint on the business key at the source system.",
    "type_mismatch": "Standardize the column's expected type at data entry.",
    "outliers": "Manually review flagged values before using this column in analysis or ML.",
    "pii": "Apply masking/encryption before sharing; restrict access to raw values.",
    "consistency": "Standardize values at entry (dropdowns/lookups instead of free text).",
    "schema_quality": "Rename unclear/duplicate/blank columns before this file is reused.",
    "validity": "Fix values that fail format/logic rules at the source before further use.",
    "freshness": "Confirm this data is refreshed on the expected schedule.",
    "referential_integrity": "Investigate and correct orphaned references before reporting on this data.",
    "hipaa_phi": "Review PHI exposure, apply masking, and restrict access per your compliance policy.",
}

CHECK_DISPLAY_NAME = {
    "missing_values": "Missing Values",
    "duplicates": "Duplicates",
    "type_mismatch": "Type Mismatch",
    "outliers": "Outliers",
    "pii": "PII Detection",
    "fuzzy_match": "Fuzzy Standardization",
    "consistency": "Consistency",
    "schema_quality": "Schema Quality",
    "validity": "Validity",
    "freshness": "Freshness",
    "referential_integrity": "Referential Integrity",
    "hipaa_phi": "HIPAA PHI Compliance Scan",
}

# Maps report check blocks to rubric dimensions shown in Quality Score Breakdown.
CHECK_TO_DIMENSION = {
    "missing_values": "completeness",
    "duplicates": "uniqueness",
    "type_mismatch": "type_reliability",
    "outliers": "outlier_risk",
    "consistency": "consistency",
    "fuzzy_match": "consistency",
    "schema_quality": "schema_quality",
    "validity": "validity",
    "freshness": "freshness",
    "pii": "privacy_sensitivity",
}


def _result_quality(result: CheckResult) -> float:
    """Mirror engine/scoring.py::_result_quality for report severity alignment."""
    if result.quality_ratio is not None:
        return max(0.0, min(1.0, float(result.quality_ratio)))
    return 1.0 if result.status == "passed" else 0.0


def _impact_ratio_from_results(results: list[CheckResult]) -> float:
    """
    Fraction of quality lost across assessed results — same basis as dimension
    scoring (average quality_ratio, or binary pass/fail when ratio absent).
    """
    assessed = [r for r in results if r.status != "error"]
    if not assessed:
        return 0.0
    avg_quality = sum(_result_quality(r) for r in assessed) / len(assessed)
    return 1.0 - avg_quality


def _severity_from_score(score: float | None) -> str:
    """Convert a 0-100 dimension/check score to a severity label."""
    if score is None:
        return "None"
    return _severity_from_ratio(1.0 - score / 100.0)


def _severity_from_ratio(ratio: float) -> str:
    """issues / total -> a severity label. Simple, explainable thresholds."""
    if ratio >= 0.50:
        return "Critical"
    if ratio >= 0.20:
        return "High"
    if ratio >= 0.05:
        return "Medium"
    if ratio > 0:
        return "Low"
    return "None"


def _readiness_from_score(score: float | None) -> str:
    """Deterministic threshold mapping -- no AI judgement involved."""
    if score is None:
        return "Not Recommended"
    if score >= 90:
        return "Ready"
    if score >= 75:
        return "Ready with Minor Cleaning"
    if score >= 55:
        return "Ready with Moderate Cleaning"
    return "Not Recommended"


def _effective_readiness_score(score: dict[str, Any]) -> float | None:
    """
    Limiting score for the Data Readiness band — same thresholds as
    ``_readiness_from_score``, applied to the minimum of headline data
    quality, compliance-adjusted score (when present), and each assessed
    dimension score.
    """
    dqs = score.get("data_quality_score")
    if dqs is None:
        return None
    candidates = [float(dqs)]
    comp = score.get("compliance_adjusted_score")
    if comp is not None:
        candidates.append(float(comp))
    for info in (score.get("dimension_scores") or {}).values():
        if info.get("available") and info.get("score") is not None:
            candidates.append(float(info["score"]))
    return min(candidates)


def _rating_from_score(score: float | None) -> str:
    if score is None:
        return "Unrated"
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 55:
        return "Fair"
    return "Poor"


def _severity_color(severity: str) -> str:
    return {
        "Critical": "#B91C1C",
        "High": "#D97706",
        "Medium": "#CA8A04",
        "Low": "#65A30D",
        "None": "#16A34A",
    }.get(severity, "#6B7280")


def _summarize_check(check_name: str, results: list[CheckResult]) -> dict[str, Any]:
    """One deterministic summary block per implemented check."""
    non_error = [r for r in results if r.status != "error"]
    column_breakdown: list[dict[str, Any]] = []

    if check_name == "hipaa_phi":
        file_level = next((r for r in non_error if r.column is None), None)
        column_level = [r for r in non_error if r.column is not None]
        failed_columns = [r for r in column_level if r.status == "failed"]

        affected_columns = sorted({str(r.column) for r in failed_columns if r.column})
        if not affected_columns and file_level:
            phi_cols = (file_level.details or {}).get("columns_with_phi") or []
            affected_columns = sorted(str(c) for c in phi_cols)

        for r in sorted(
            failed_columns,
            key=lambda x: (-x.issues_found, str(x.column or "")),
        ):
            column_breakdown.append(
                {
                    "column": r.column,
                    "issues_found": r.issues_found,
                    "identifiers": dict((r.details or {}).get("identifiers") or {}),
                }
            )

        total_issues = (
            file_level.issues_found if file_level else sum(r.issues_found for r in non_error)
        )
        impact_results = [file_level] if file_level else non_error
        columns_checked = len(affected_columns)
        columns_with_issues = len(affected_columns)
        samples = list(column_breakdown[:10])
    else:
        failed = [r for r in non_error if r.status == "failed"]
        total_issues = sum(r.issues_found for r in non_error)
        impact_results = non_error
        affected_columns = sorted({r.column for r in failed if r.column})
        columns_checked = len(non_error)
        columns_with_issues = len(failed)
        samples = []
        for r in failed[:5]:
            detail_bits = {
                k: v
                for k, v in (r.details or {}).items()
                if k in ("reason", "rule", "note", "sample_outlier_values", "examples")
            }
            samples.append({"column": r.column, "issues_found": r.issues_found, **detail_bits})

    impact_ratio = _impact_ratio_from_results(impact_results)
    severity = _severity_from_ratio(impact_ratio)

    return {
        "check_name": check_name,
        "display_name": CHECK_DISPLAY_NAME.get(check_name, check_name.replace("_", " ").title()),
        "columns_checked": columns_checked,
        "columns_with_issues": columns_with_issues,
        "total_issues_found": total_issues,
        "impact_ratio": round(impact_ratio, 4),
        "severity": severity,
        "severity_color": _severity_color(severity),
        "severity_basis": "proportional",
        "affected_columns": affected_columns,
        "column_breakdown": column_breakdown,
        "sample_findings": samples,
        "business_impact": BUSINESS_IMPACT.get(check_name, "May affect downstream analysis reliability."),
        "recommendation": RECOMMENDATION.get(check_name, "Review and remediate flagged rows/columns."),
    }


def _align_check_severities_with_dimensions(
    check_summaries: dict[str, dict[str, Any]],
    dimension_scores: dict[str, Any],
) -> None:
    """
    Ensure check-level severity badges match the Quality Score Breakdown
    dimension they belong to (same score -> same severity band).
    """
    for check_name, dim in CHECK_TO_DIMENSION.items():
        summary = check_summaries.get(check_name)
        info = dimension_scores.get(dim) or {}
        if not summary or not info.get("available") or info.get("score") is None:
            continue
        score = float(info["score"])
        summary["dimension_score"] = score
        summary["severity"] = _severity_from_score(score)
        summary["severity_color"] = _severity_color(summary["severity"])
        summary["impact_ratio"] = round(1.0 - score / 100.0, 4)
        summary["severity_basis"] = "dimension_score"


def _column_quality_matrix(
    classification: dict[str, str],
    all_results_by_column: dict[str, list[CheckResult]],
) -> list[dict[str, Any]]:
    rows = []
    for col, role in classification.items():
        col_results = [r for r in all_results_by_column.get(col, []) if r.status != "error"]
        if not col_results:
            continue
        failed = [r for r in col_results if r.status == "failed"]
        total_issues = sum(r.issues_found for r in col_results)
        avg_quality = sum(_result_quality(r) for r in col_results) / len(col_results)
        impact_ratio = 1.0 - avg_quality
        severity = _severity_from_ratio(impact_ratio)
        score = round(100.0 * avg_quality, 1)
        failing_checks = sorted({r.check_name for r in failed})
        rows.append(
            {
                "column": col,
                "role": role,
                "quality_score": score,
                "issues_found": total_issues,
                "severity": severity,
                "severity_color": _severity_color(severity),
                "failing_checks": failing_checks,
                "recommendation": RECOMMENDATION.get(failing_checks[0], "Review flagged values.")
                if failing_checks
                else "No action needed.",
            }
        )
    rows.sort(key=lambda r: r["quality_score"])
    return rows


def _duplicate_analysis_block(dup_results: list[CheckResult]) -> dict[str, Any]:
    """
    Reshape ``check_duplicates_frame()`` output for the report.

    Purpose
        Pure presentation reshaping -- no new detection or scoring. Splits
        the flat result list into "full-row" vs "business-key" duplicates,
        and surfaces the ``uniqueness_evidence`` that ``checks/duplicates.py``
        already attaches to every inferred key (see ``uniqueness_evidence``/
        ``infer_uniqueness_keys`` there) so the report can explain *why* a
        column was treated as a business key instead of just asserting it.
    """
    full_row = next((r for r in dup_results if r.check_name == "duplicates"), None)
    key_results = [r for r in dup_results if r.check_name == "duplicate_keys"]

    business_keys = []
    for r in key_results:
        ev = (r.details or {}).get("uniqueness_evidence") or {}
        confidence_pct = round(ev["score"] * 100) if "score" in ev else None
        evidence_notes = []
        if ev.get("source") == "configured_override":
            evidence_notes.append("Manually configured as a business key")
        else:
            if ev.get("name_match"):
                evidence_notes.append("Identifier-style column name")
            if (ev.get("uniqueness_ratio") or 0) >= 0.9:
                evidence_notes.append("Very high uniqueness ratio")
            if (ev.get("top_value_ratio") or 0) and ev["top_value_ratio"] < 0.05:
                evidence_notes.append("Very low value repetition")
            if ev.get("role") == "identifier":
                evidence_notes.append("Classified as an identifier column")
        business_keys.append(
            {
                "column": r.column,
                "status": r.status,
                "issues_found": r.issues_found,
                "rows_sharing_key": (r.details or {}).get("duplicate_set_rows", r.issues_found),
                "distinct_keys_reused": (r.details or {}).get("unique_keys_repeated", 0),
                "confidence_pct": confidence_pct,
                "evidence_notes": evidence_notes,
            }
        )

    fr_details = (full_row.details or {}) if full_row else {}
    return {
        "full_row": {
            "status": full_row.status if full_row else "passed",
            "issues_found": full_row.issues_found if full_row else 0,
            "rows_in_duplicate_sets": fr_details.get("duplicate_set_rows", 0),
            "total_rows": fr_details.get("total_rows"),
        },
        "business_keys": business_keys,
        "business_keys_evaluated": len(business_keys),
    }


def _column_intelligence_block(
    classification: dict[str, str],
    business_keys: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Per-column semantic summary for the report.

    Purpose
        Combine the column role already assigned by ``column_classifier``
        with the business-key evidence already computed for duplicate
        detection into one row per column -- again, pure reshaping of data
        that was computed elsewhere, not a new classification pass.
    """
    by_col = {bk["column"]: bk for bk in business_keys}
    rows = []
    for col, role in classification.items():
        bk = by_col.get(col)
        rows.append(
            {
                "column": col,
                "role": role,
                "is_business_key": bk is not None,
                "confidence_pct": bk["confidence_pct"] if bk else None,
                "evidence_notes": bk["evidence_notes"] if bk else [],
            }
        )
    return rows


def _ml_readiness_block(readiness: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Reshape a Phase 2 M3 readiness result (see
    ``phase2/readiness/scorer.py::score_readiness`` and its four
    sub-analyses) into the "ML Model Readiness Assessment" report section
    (PHASE2_PLAN.md, "3.6 Report Integration").

    Pure presentation reshaping only -- this module does not import from
    phase2 and does not compute anything; the caller (a Phase 2
    orchestrator) already ran ``score_readiness()`` and passes its result
    in as a plain dict (e.g. via ``dataclasses.asdict``), keeping the
    "phase2 calls into engine, never the other way" rule from
    PHASE2_PLAN.md section 2.14 intact.

    Expected shape of ``readiness`` (every key is read defensively --
    missing/malformed input degrades gracefully to "N/A" values rather
    than raising):

        {
          "overall_score": float, "verdict": str,
          "temporal_score": float, "interval_score": float,
          "target_score": float, "leakage_score": float,
          "blockers": [str], "warnings": [str], "recommendations": [str],
          "temporal": {...TemporalAnalysis fields...},
          "interval": {...IntervalAnalysis fields...},
          "target":   {...TargetAnalysis fields...},
          "leakage":  {...LeakageAnalysis fields...},
        }

    Returns None if ``readiness`` is falsy, so callers/renderers that
    never ran M3 (or ran it without a date/target column) simply omit
    the section instead of rendering a block of "N/A"s.
    """
    if not readiness:
        return None

    def _status(blockers: list[Any] | None) -> str:
        return "[BLOCKED]" if blockers else "[OK]"

    temporal = readiness.get("temporal") or {}
    interval = readiness.get("interval") or {}
    target = readiness.get("target") or {}
    leakage = readiness.get("leakage") or {}

    verdict = str(readiness.get("verdict", "not_ready")).upper()
    overall_score = readiness.get("overall_score")

    # ---- Temporal sufficiency ----
    temporal_blockers = temporal.get("blockers") or []
    temporal_conclusion = (
        "; ".join(temporal_blockers)
        if temporal_blockers
        else "Sufficient historical data for forecasting."
    )
    temporal_section = {
        "score": readiness.get("temporal_score"),
        "status": _status(temporal_blockers),
        "observations": temporal.get("total_observations"),
        "minimum_observations": 30,
        "date_range_days": temporal.get("date_range_days"),
        "implied_frequency": temporal.get("implied_frequency"),
        "seasonal_cycles_detected": temporal.get("seasonal_cycles_detected"),
        "minimum_seasonal_cycles": 2,
        "conclusion": temporal_conclusion,
    }

    # ---- Interval regularity ----
    interval_blockers = interval.get("blockers") or []
    interval_conclusion = (
        "; ".join(interval_blockers)
        if interval_blockers
        else (
            "Perfectly regular intervals."
            if interval.get("regularity_score") == 1.0
            else "Mostly regular intervals with some gaps."
        )
    )
    interval_section = {
        "score": readiness.get("interval_score"),
        "status": _status(interval_blockers),
        "frequency": interval.get("inferred_frequency"),
        "missing_intervals": interval.get("missing_intervals"),
        "duplicate_timestamps": interval.get("duplicate_timestamps"),
        "regularity_score": interval.get("regularity_score"),
        "conclusion": interval_conclusion,
    }

    # ---- Target integrity ----
    target_blockers = target.get("blockers") or []
    target_conclusion = (
        "; ".join(target_blockers) if target_blockers else "Target quality acceptable."
    )
    target_section = {
        "score": readiness.get("target_score"),
        "status": _status(target_blockers),
        "column": target.get("column_name"),
        "null_pct": target.get("null_pct"),
        "null_status": "OK" if (target.get("null_pct") or 0) <= 10 else "WARNING",
        "zero_pct": target.get("zero_pct"),
        "zero_status": "OK" if (target.get("zero_pct") or 0) <= 50 else "WARNING",
        "infinite_count": target.get("infinite_count"),
        "infinite_status": "OK" if not (target.get("infinite_count") or 0) else "BLOCKED",
        "variance": target.get("variance"),
        "variance_status": "OK" if (target.get("variance") or 0) > 1e-9 else "WARNING",
        "outlier_pct": target.get("outlier_pct"),
        "outlier_status": "OK" if (target.get("outlier_pct") or 0) <= 20 else "WARNING",
        "conclusion": target_conclusion,
    }

    # ---- Leakage & cardinality ----
    concern_level = leakage.get("concern_level", "none")
    leakage_status = {"none": "[OK]", "warning": "[WARNING]", "blocker": "[BLOCKED]"}.get(
        concern_level, "[OK]"
    )
    perfect_corr = leakage.get("perfect_correlation_features") or []
    high_card = leakage.get("high_cardinality_features") or []
    if perfect_corr:
        leakage_conclusion = "Leakage detected: " + ", ".join(perfect_corr)
    elif high_card or leakage.get("identifier_features"):
        leakage_conclusion = "No leakage detected, but review flagged high-cardinality/identifier columns."
    else:
        leakage_conclusion = "No leakage detected."
    leakage_section = {
        "score": readiness.get("leakage_score"),
        "status": leakage_status,
        "perfect_correlation_features": perfect_corr or "None",
        "high_cardinality_features": high_card or "None",
        "identifier_features": leakage.get("identifier_features") or "None",
        "conclusion": leakage_conclusion,
    }

    recommendations = readiness.get("recommendations") or []

    lines = [
        "ML Model Readiness Assessment",
        "",
        f"Recommendation: {verdict}",
        f"Overall Score: {overall_score}/100",
        "",
        f"Temporal Sufficiency: {temporal_section['score']} {temporal_section['status']}",
        f"  - Observations: {temporal_section['observations']} (minimum: {temporal_section['minimum_observations']})",
        f"  - Date Range: {temporal_section['date_range_days']} days",
        f"  - Seasonal Cycles: {temporal_section['seasonal_cycles_detected']}",
        f"  - Conclusion: {temporal_section['conclusion']}",
        "",
        f"Interval Regularity: {interval_section['score']} {interval_section['status']}",
        f"  - Frequency: {interval_section['frequency']}",
        f"  - Missing Intervals: {interval_section['missing_intervals']}",
        f"  - Duplicate Timestamps: {interval_section['duplicate_timestamps']}",
        f"  - Regularity Score: {interval_section['regularity_score']}",
        f"  - Conclusion: {interval_section['conclusion']}",
        "",
        f"Target Integrity: {target_section['score']} {target_section['status']}",
        f"  - Column: '{target_section['column']}'",
        f"  - Null %: {target_section['null_pct']}% [{target_section['null_status']}]",
        f"  - Zero %: {target_section['zero_pct']}% [{target_section['zero_status']}]",
    ]
    if target_section.get("infinite_count"):
        lines.append(
            f"  - Infinite values: {target_section['infinite_count']} [{target_section['infinite_status']}]"
        )
    lines += [
        f"  - Variance: {target_section['variance']} [{target_section['variance_status']}]",
        f"  - Outliers: {target_section['outlier_pct']}% [{target_section['outlier_status']}]",
        f"  - Conclusion: {target_section['conclusion']}",
        "",
        f"Leakage & Cardinality: {leakage_section['score']} {leakage_section['status']}",
        f"  - Perfect Correlation Features: {leakage_section['perfect_correlation_features']}",
        f"  - High Cardinality Features: {leakage_section['high_cardinality_features']}",
        f"  - Conclusion: {leakage_section['conclusion']}",
        "",
        "Recommendations:",
    ]
    if recommendations:
        lines.extend(f"  {i}. {rec}" for i, rec in enumerate(recommendations, start=1))
    else:
        lines.append("  (none)")

    return {
        "verdict": verdict,
        "overall_score": overall_score,
        "temporal": temporal_section,
        "interval": interval_section,
        "target": target_section,
        "leakage": leakage_section,
        "blockers": readiness.get("blockers") or [],
        "warnings": readiness.get("warnings") or [],
        "recommendations": recommendations,
        "text": "\n".join(lines),
    }


def build_report_data(
    *,
    filepath: str,
    sheet_name: str,
    df_shape: tuple[int, int],
    header_row: int,
    processing_time_seconds: float,
    classification: dict[str, str],
    check_results_by_name: dict[str, list[CheckResult]],
    pii_summary_by_column: dict[str, dict[str, Any]],
    fuzzy_results: dict[str, dict[str, Any]] | None,
    entity_resolution: dict[str, Any] | None = None,
    score: dict[str, Any],
    engine_version: str = "Phase 1",
    sheet_disclosure: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    check_results_by_name: check_name -> list[CheckResult], covering every
    implemented check (missing_values, duplicates, type_mismatch, outliers,
    schema_quality, consistency, validity, freshness, and optionally
    referential_integrity if it was run).
    pii_summary_by_column: same shape main.py already builds
    (column -> detect_pii_in_series() output).
    fuzzy_results: column -> {"remap_rows": int, "clusters": int, "mapping_sample": [...]}
        or None if fuzzy standardization wasn't run.
    score: the exact dict returned by scoring.compute_data_quality_score().
    readiness: optional Phase 2 M3 readiness result (see
        ``_ml_readiness_block`` for the expected shape) produced by
        ``phase2.readiness.scorer.score_readiness()``. None (default) if
        M3 wasn't run for this file (e.g. no target/date column
        configured) -- in that case the "ml_readiness" key is omitted
        from the report rather than being filled with placeholder data.
    """
    now = datetime.now()
    rows, cols = df_shape

    # ---- Column roles summary (Dataset Overview) ----
    role_counts: dict[str, int] = {}
    for role in classification.values():
        role_counts[role] = role_counts.get(role, 0) + 1

    # ---- Per-check summaries ----
    check_summaries = {
        name: _summarize_check(name, results) for name, results in check_results_by_name.items()
    }
    _align_check_severities_with_dimensions(
        check_summaries, score.get("dimension_scores") or {}
    )

    # ---- PII summary block ----
    # FIX (ISS-04, validation audit): total_rows_with_pii used to be
    # sum(rows_with_pii per column), which double/triple counts any row
    # that has PII in more than one column and can exceed the dataset's
    # actual row count (observed: 425,659 "rows with PII" on a 101,352-row
    # file). It is now the number of *distinct* rows flagged by any
    # PII-flagged column -- a union of row indices, never more than
    # len(df).
    pii_flagged = {c: s for c, s in (pii_summary_by_column or {}).items() if s.get("rows_with_pii", 0) > 0}
    _pii_row_union: set[Any] = set()
    for s in pii_flagged.values():
        _pii_row_union.update((s.get("masked_rows") or {}).keys())
    pii_block = {
        "columns_with_pii": len(pii_flagged),
        "total_columns": len(pii_summary_by_column or {}),
        "total_rows_with_pii": len(_pii_row_union),
        "types_found": sorted({t for s in pii_flagged.values() for t in s.get("type_counts", {})}),
        "flagged_columns": sorted(pii_flagged.keys()),
    }

    # ---- Fuzzy standardization block ----
    fuzzy_flagged = {c: v for c, v in (fuzzy_results or {}).items() if v.get("remap_rows", 0) > 0}
    fuzzy_block = {
        "columns_with_remaps": len(fuzzy_flagged),
        "total_remap_rows": sum(v.get("remap_rows", 0) for v in fuzzy_flagged.values()),
        "flagged_columns": sorted(fuzzy_flagged.keys()),
    }

    # ---- Column quality matrix ----
    all_results_by_column: dict[str, list[CheckResult]] = {}
    for results in check_results_by_name.values():
        for r in results:
            if r.column:
                all_results_by_column.setdefault(r.column, []).append(r)
    matrix = _column_quality_matrix(classification, all_results_by_column)

    # ---- Duplicate analysis + column intelligence (presentation reshaping
    # of data already computed by checks/duplicates.py) ----
    duplicate_analysis = _duplicate_analysis_block(check_results_by_name.get("duplicates", []))
    column_intelligence = _column_intelligence_block(
        classification, duplicate_analysis["business_keys"]
    )

    # ---- Top critical issues (ranked) ----
    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "None": 4}
    top_issues = []
    for name, summary in check_summaries.items():
        results = check_results_by_name.get(name, [])
        failed_results = [
            r for r in results if r.status != "error" and r.status == "failed"
        ]
        for r in failed_results:
            col = r.column or "-"
            row_severity = _severity_from_ratio(1.0 - _result_quality(r))
            top_issues.append(
                {
                    "issue": summary["display_name"],
                    "column": col,
                    "severity": row_severity,
                    "severity_color": _severity_color(row_severity),
                    "impact": summary["business_impact"],
                    "action": summary["recommendation"],
                    "issues_found": r.issues_found,
                }
            )
        # Fallback when failed checks have no column name (e.g. full-row duplicates)
        if not failed_results and summary["total_issues_found"] > 0:
            top_issues.append(
                {
                    "issue": summary["display_name"],
                    "column": "-",
                    "severity": summary["severity"],
                    "severity_color": summary["severity_color"],
                    "impact": summary["business_impact"],
                    "action": summary["recommendation"],
                    "issues_found": summary["total_issues_found"],
                }
            )
    top_issues.sort(key=lambda x: severity_rank.get(x["severity"], 9))
    top_issues = top_issues[:10]

    # ---- Executive summary ----
    dqs = score.get("data_quality_score")
    critical_findings = [
        f"{s['display_name']}: {s['columns_with_issues']} column(s) affected, {s['total_issues_found']} issue(s) found"
        for s in check_summaries.values()
        if s["severity"] in ("Critical", "High")
    ]
    positive_findings = [
        f"{s['display_name']}: no issues found"
        for s in check_summaries.values()
        if s["severity"] == "None"
    ]
    if pii_block["columns_with_pii"] > 0:
        critical_findings.append(
            f"PII detected in {pii_block['columns_with_pii']} column(s) -- masking required before sharing"
        )

    # ---- Sheet disclosure (FIX ISS-01/ISS-02/ISS-05, validation audit) ----
    # Whenever the workbook has more than one sheet, or a sheet was skipped
    # because it was hidden/empty, say so up front -- previously a reader
    # had no way to know other sheets existed (e.g. 5 of 6 year-sheets in
    # a multi-year invoice export were silently never analyzed).
    sd = sheet_disclosure or {}
    other_sheets = sd.get("other_sheets_in_workbook") or []
    hidden_sheets = sd.get("hidden_sheet_names") or []
    other_reported = sd.get("other_sheets_also_reported") or []
    if other_sheets:
        not_covered = [s for s in other_sheets if s not in other_reported]
        msg = (
            f"This workbook contains {sd.get('total_sheets_in_workbook', 1)} sheets. "
            f"This report covers sheet '{sheet_name}'."
        )
        if other_reported:
            msg += f" Separate reports were also generated for: {', '.join(other_reported)}."
        if not_covered:
            msg += f" NOT analyzed in any report: {', '.join(not_covered)}."
        if hidden_sheets:
            msg += f" Hidden sheet(s) skipped: {', '.join(hidden_sheets)}."
        critical_findings.insert(0, msg)

    return {
        "meta": {
            "filepath": filepath,
            "filename": filepath.split("\\")[-1].split("/")[-1],
            "sheet_name": sheet_name,
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "engine_version": engine_version,
            "processing_time_seconds": round(processing_time_seconds, 2),
        },
        "overview": {
            "rows": rows,
            "columns": cols,
            "header_row": header_row,
            "role_counts": role_counts,
            "checks_executed": len(check_summaries),
        },
        "score": {
            "overall": dqs,
            "compliance_adjusted": score.get("compliance_adjusted_score"),
            "rating": _rating_from_score(dqs),
            "readiness": _readiness_from_score(_effective_readiness_score(score)),
            "scorable_weight_fraction": score.get("scorable_weight_fraction"),
            "dimension_scores": score.get("dimension_scores", {}),
            "dimensions_excluded": score.get("dimensions_excluded", []),
        },
        "sheet_disclosure": sheet_disclosure or {},
        "ml_readiness": _ml_readiness_block(readiness),
        "privacy_risk": score.get("privacy_risk"),
        "pii": pii_block,
        "fuzzy": fuzzy_block,
        "entity_resolution": entity_resolution or {"enabled": False},
        "checks": check_summaries,
        "column_matrix": matrix,
        "duplicate_analysis": duplicate_analysis,
        "column_intelligence": column_intelligence,
        "top_issues": top_issues,
        "executive_summary": {
            "critical_findings": critical_findings[:8],
            "positive_findings": positive_findings[:8],
        },
    }