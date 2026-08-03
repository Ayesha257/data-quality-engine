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
}


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
    total = len(non_error)
    failed = [r for r in non_error if r.status == "failed"]
    total_issues = sum(r.issues_found for r in non_error)
    ratio = (len(failed) / total) if total else 0.0
    severity = _severity_from_ratio(ratio)

    affected_columns = sorted({r.column for r in failed if r.column})
    samples = []
    for r in failed[:5]:
        detail_bits = {
            k: v
            for k, v in (r.details or {}).items()
            if k in ("reason", "rule", "note", "sample_outlier_values", "examples")
        }
        samples.append({"column": r.column, "issues_found": r.issues_found, **detail_bits})

    return {
        "check_name": check_name,
        "display_name": CHECK_DISPLAY_NAME.get(check_name, check_name.replace("_", " ").title()),
        "columns_checked": total,
        "columns_with_issues": len(failed),
        "total_issues_found": total_issues,
        "severity": severity,
        "severity_color": _severity_color(severity),
        "affected_columns": affected_columns,
        "sample_findings": samples,
        "business_impact": BUSINESS_IMPACT.get(check_name, "May affect downstream analysis reliability."),
        "recommendation": RECOMMENDATION.get(check_name, "Review and remediate flagged rows/columns."),
    }


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
        ratio = len(failed) / len(col_results) if col_results else 0
        severity = _severity_from_ratio(ratio)
        score = round(100.0 * (1 - ratio), 1)
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
    score: dict[str, Any],
    engine_version: str = "Phase 1",
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

    # ---- PII summary block ----
    pii_flagged = {c: s for c, s in (pii_summary_by_column or {}).items() if s.get("rows_with_pii", 0) > 0}
    pii_block = {
        "columns_with_pii": len(pii_flagged),
        "total_columns": len(pii_summary_by_column or {}),
        "total_rows_with_pii": sum(s.get("rows_with_pii", 0) for s in pii_flagged.values()),
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

    # ---- Top critical issues (ranked) ----
    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "None": 4}
    top_issues = []
    for name, summary in check_summaries.items():
        for col in summary["affected_columns"]:
            top_issues.append(
                {
                    "issue": summary["display_name"],
                    "column": col,
                    "severity": summary["severity"],
                    "severity_color": summary["severity_color"],
                    "impact": summary["business_impact"],
                    "action": summary["recommendation"],
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
            "rating": _rating_from_score(dqs),
            "readiness": _readiness_from_score(dqs),
            "scorable_weight_fraction": score.get("scorable_weight_fraction"),
            "dimension_scores": score.get("dimension_scores", {}),
            "dimensions_excluded": score.get("dimensions_excluded", []),
        },
        "privacy_risk": score.get("privacy_risk"),
        "pii": pii_block,
        "fuzzy": fuzzy_block,
        "checks": check_summaries,
        "column_matrix": matrix,
        "top_issues": top_issues,
        "executive_summary": {
            "critical_findings": critical_findings[:8],
            "positive_findings": positive_findings[:8],
        },
    }
