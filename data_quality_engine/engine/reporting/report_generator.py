"""Builds structured report-data dicts from pipeline outputs.

Enhanced with:
- Business Rule Validation summaries (Rule, Status, Failed Records, Severity, Business Impact, Recommendation).
- Dynamic, business-specific actionable recommendations.
- Issue Severity Distribution (CRITICAL, HIGH, MEDIUM, LOW).
- Priority Fix Roadmap.
- Executive Risk Summary & Score Breakdown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from data_quality_engine.engine.models import CheckResult

BUSINESS_IMPACT = {
    "missing_values": "May produce inaccurate analytics and unreliable reporting.",
    "duplicates": "Can create duplicate customers/orders and inflate revenue or count metrics.",
    "type_mismatch": "Charting, grouping, and trend analysis may silently break or mislead.",
    "outliers": "May distort averages, totals, and financial reporting accuracy.",
    "pii": "Privacy and regulatory compliance risk if shared or stored without masking.",
    "consistency": "Grouping and aggregation become unreliable (e.g. 'Paid' vs 'paid' split apart).",
    "schema_quality": "Downstream tools and analysts may misread or skip unclear columns.",
    "validity": "Values that look present may still be unusable or logically wrong.",
    "freshness": "Decisions may be based on stale data without anyone noticing.",
    "referential_integrity": "Records may reference customers/suppliers/products that don't exist.",
    "business_rule_validation": "Direct violation of defined enterprise business and operational constraints.",
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
    "business_rule_validation": "Business Rule Validation",
}


def generate_business_recommendation(
    check_name: str, column: str | None, severity: str, details: dict[str, Any] | None = None
) -> str:
    """Generate dynamic, business-specific actionable recommendation."""
    dt = details or {}
    col_str = str(column or "").lower()

    if dt.get("recommendation"):
        return str(dt["recommendation"])

    if check_name == "duplicates":
        if "customer" in col_str or "id" in col_str or "key" in col_str:
            return "Duplicate Customer ID -> Add UNIQUE constraint on customer key."
        return f"Duplicate Records in {column or 'Key'} -> Add UNIQUE constraint at source."

    if check_name == "missing_values":
        if "email" in col_str:
            return "Missing Email -> Make Email mandatory and validate format."
        return f"Missing Values in {column or 'Field'} -> Make field required in ERP ingestion."

    if check_name == "validity":
        if "country" in col_str or "city" in col_str:
            return "Invalid Country/City -> Use standard ISO lookup list."
        if "date" in col_str:
            return "Invalid Date Order -> Validate timeline constraints before save."
        return f"Invalid Values in {column or 'Field'} -> Fix values failing business logic rules."

    if check_name == "outliers":
        if "amount" in col_str or "price" in col_str or "qty" in col_str or "total" in col_str:
            return f"Outlier Amount in {column or 'Field'} -> Review transactions before reporting."
        return f"Outliers Detected in {column or 'Field'} -> Validate numeric bounds."

    if check_name == "pii":
        return f"PII Detected in {column or 'Field'} → Mask/encrypt sensitive data."

    return "Review and remediate flagged records at source system."


def _severity_from_ratio(ratio: float) -> str:
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
    sev = str(severity).upper()
    return {
        "CRITICAL": "#B91C1C",
        "HIGH": "#D97706",
        "MEDIUM": "#CA8A04",
        "LOW": "#65A30D",
        "NONE": "#16A34A",
    }.get(sev, "#6B7280")


def _summarize_check(check_name: str, results: list[CheckResult]) -> dict[str, Any]:
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
            if k in ("reason", "rule", "note", "sample_outlier_values", "examples", "rule_name")
        }
        samples.append({"column": r.column, "issues_found": r.issues_found, **detail_bits})

    rec = generate_business_recommendation(
        check_name, affected_columns[0] if affected_columns else None, severity
    )

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
        "business_impact": BUSINESS_IMPACT.get(check_name, "May affect downstream analysis."),
        "recommendation": rec,
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
    score: dict[str, Any],
    engine_version: str = "Phase 1 Enterprise",
    sheet_disclosure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now()
    rows, cols = df_shape

    role_counts: dict[str, int] = {}
    for role in classification.values():
        role_counts[role] = role_counts.get(role, 0) + 1

    check_summaries = {
        name: _summarize_check(name, results)
        for name, results in check_results_by_name.items()
    }

    # Extract Business Rule Engine Validation results specifically
    business_rule_results: list[dict[str, Any]] = []
    validity_list = check_results_by_name.get("validity", [])
    for r in validity_list:
        if r.details and r.details.get("rule_name"):
            dt = r.details
            status = r.status.upper()
            sev = dt.get("severity", "MEDIUM").upper()
            rec = generate_business_recommendation("validity", r.column, sev, dt)
            business_rule_results.append(
                {
                    "rule_id": dt.get("rule_id", "rule"),
                    "rule_name": dt.get("rule_name", "Business Rule"),
                    "column": r.column or "Dataset",
                    "status": status,
                    "failed_records": r.issues_found,
                    "failed_pct": dt.get("failed_pct", 0.0),
                    "severity": sev,
                    "severity_color": _severity_color(sev),
                    "business_impact": dt.get(
                        "business_impact", "Affects operational data integrity."
                    ),
                    "recommendation": rec,
                }
            )

    # Issue Severity Distribution
    severity_distribution = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for s in check_summaries.values():
        sev = str(s["severity"]).upper()
        if sev in severity_distribution:
            severity_distribution[sev] += s["columns_with_issues"]

    for br in business_rule_results:
        if br["status"] == "FAILED":
            sev = br["severity"]
            if sev in severity_distribution:
                severity_distribution[sev] += 1

    # PII summary block
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

    fuzzy_flagged = {c: v for c, v in (fuzzy_results or {}).items() if v.get("remap_rows", 0) > 0}
    fuzzy_block = {
        "columns_with_remaps": len(fuzzy_flagged),
        "total_remap_rows": sum(v.get("remap_rows", 0) for v in fuzzy_flagged.values()),
        "flagged_columns": sorted(fuzzy_flagged.keys()),
    }

    # Column Quality Matrix
    all_results_by_column: dict[str, list[CheckResult]] = {}
    for results in check_results_by_name.values():
        for r in results:
            if r.column:
                all_results_by_column.setdefault(r.column, []).append(r)

    matrix = []
    for col, role in classification.items():
        col_results = [r for r in all_results_by_column.get(col, []) if r.status != "error"]
        if not col_results:
            continue
        failed = [r for r in col_results if r.status == "failed"]
        total_issues = sum(r.issues_found for r in col_results)
        ratio = len(failed) / len(col_results) if col_results else 0
        severity = _severity_from_ratio(ratio)
        score_val = round(100.0 * (1 - ratio), 1)
        failing_checks = sorted({r.check_name for r in failed})
        rec = generate_business_recommendation(
            failing_checks[0] if failing_checks else "general", col, severity
        )
        matrix.append(
            {
                "column": col,
                "role": role,
                "quality_score": score_val,
                "issues_found": total_issues,
                "severity": severity,
                "severity_color": _severity_color(severity),
                "failing_checks": failing_checks,
                "recommendation": rec if failing_checks else "No action needed.",
            }
        )
    matrix.sort(key=lambda r: r["quality_score"])

    # Priority Fix Roadmap
    roadmap = []
    for m in matrix:
        if m["issues_found"] > 0:
            roadmap.append(
                {
                    "priority": len(roadmap) + 1,
                    "column": m["column"],
                    "issues": ", ".join(m["failing_checks"]),
                    "severity": m["severity"],
                    "severity_color": m["severity_color"],
                    "action": m["recommendation"],
                }
            )

    # Top critical issues
    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "None": 4}
    top_issues = []
    for summary in check_summaries.values():
        for col in summary["affected_columns"]:
            rec = generate_business_recommendation(summary["check_name"], col, summary["severity"])
            top_issues.append(
                {
                    "issue": summary["display_name"],
                    "column": col,
                    "severity": summary["severity"],
                    "severity_color": summary["severity_color"],
                    "impact": summary["business_impact"],
                    "action": rec,
                }
            )
    top_issues.sort(key=lambda x: severity_rank.get(x["severity"], 9))
    top_issues = top_issues[:10]

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
            msg += f" Separate reports generated for: {', '.join(other_reported)}."
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
            "rating": _rating_from_score(dqs),
            "readiness": _readiness_from_score(dqs),
            "scorable_weight_fraction": score.get("scorable_weight_fraction"),
            "dimension_scores": score.get("dimension_scores", {}),
            "dimensions_excluded": score.get("dimensions_excluded", []),
        },
        "severity_distribution": severity_distribution,
        "business_rules": business_rule_results,
        "priority_roadmap": roadmap[:8],
        "sheet_disclosure": sheet_disclosure or {},
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
