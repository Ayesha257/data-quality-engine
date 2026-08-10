"""HTML data-quality report generator.

Root-cause fix for a report/console divergence bug: an earlier report
generator (not part of this codebase -- only its output was seen) showed
"Columns with fuzzy remaps: 0" and Consistency=85 for a file where the
console output of the very same engine run found 4 fuzzy-remap columns
(19 rows) and Consistency=76. The two numbers can only disagree if the
report is computed by a second, separate code path from the console.

This module is built so that can't happen again: ``generate_html_report``
takes the *exact* summary dicts / CheckResult lists that main.py's console
printers (``_print_top_results``, ``_print_task3_results``, etc.) already
built for that run, and renders them -- it never re-runs a check or
recomputes a number. Report and console are guaranteed to agree because
they are two views of the same objects, not two calculations.
"""

from __future__ import annotations

import html
from collections import defaultdict
from pathlib import Path
from typing import Any

from data_quality_engine.engine.models import CheckResult

# ---------------------------------------------------------------------------
# Severity scale -- one mapping, reused everywhere (KPI bars, badges, matrix
# chips, business-impact cards) so the same score always reads as the same
# color across the whole report. The old report used near-identical amber
# tones for "Fair" / "Medium" / several different severities at once, which
# is part of why it read as visually flat.
# ---------------------------------------------------------------------------
_SEVERITY_COLORS = {
    "critical": "#DC2626",
    "high": "#EA580C",
    "medium": "#D97706",
    "low": "#2563EB",
    "none": "#16A34A",
}

_DIMENSION_COPY = {
    "completeness": (
        "Missing Values",
        "May produce inaccurate analytics and unreliable reporting.",
        "Improve data collection at source; consider a required-field rule for critical columns.",
    ),
    "uniqueness": (
        "Duplicates",
        "Can create duplicate customers/orders and inflate revenue or count metrics.",
        "Add a uniqueness constraint on the business key at the source system.",
    ),
    "type_reliability": (
        "Type Mismatch",
        "Charting, grouping, and trend analysis may silently break or mislead.",
        "Standardize the column's expected type at data entry.",
    ),
    "outlier_risk": (
        "Outliers",
        "May distort averages, totals, and any ML model trained on this data.",
        "Manually review flagged values before using this column in analysis or ML.",
    ),
    "schema_quality": (
        "Schema Quality",
        "Downstream tools and analysts may misread or skip unclear columns.",
        "Rename unclear/duplicate/blank columns before this file is reused.",
    ),
    "consistency": (
        "Consistency",
        "Grouping and aggregation become unreliable (e.g. 'Paid' vs 'paid' split apart).",
        "Standardize values at entry (dropdowns/lookups instead of free text).",
    ),
    "validity": (
        "Validity",
        "Values that look present may still be unusable or logically wrong.",
        "Fix values that fail format/logic rules at the source before further use.",
    ),
    "freshness": (
        "Freshness",
        "Decisions may be based on stale data without anyone noticing.",
        "Confirm this data is refreshed on the expected schedule.",
    ),
}

_DIMENSION_ORDER = (
    "completeness",
    "uniqueness",
    "type_reliability",
    "outlier_risk",
    "schema_quality",
    "consistency",
    "validity",
    "freshness",
)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _is_role_skip(result: CheckResult) -> bool:
    reason = result.details.get("reason")
    return isinstance(reason, str) and reason.startswith("skipped_")


def _severity_for_score(score: float | None) -> str:
    if score is None:
        return "low"
    if score >= 95:
        return "none"
    if score >= 80:
        return "low"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "high"
    return "critical"


def _severity_badge(label: str, key: str) -> str:
    color = _SEVERITY_COLORS.get(key, "#64748B")
    return f'<span class="badge" style="background:{color}">{_esc(label)}</span>'


def _sample_detail(dim: str, r: CheckResult) -> str:
    """Extra one-line detail per result, matching what the console prints
    for that check type -- same underlying details dict, just rendered."""
    d = r.details
    if dim == "outlier_risk" and "note" in d and "dominant_value" in d:
        return (
            f"note: {d['note']} (dominant_value={d['dominant_value']}, "
            f"dominant_value_ratio={d.get('dominant_value_ratio')})"
        )
    if dim == "uniqueness":
        note = d.get("note")
        return f"note={note}" if note else ""
    if dim == "consistency" and d.get("examples"):
        ex = d["examples"][:2]
        return f"examples={ex}"
    if dim == "validity" and (d.get("rule") or d.get("reason")):
        rule = d.get("rule") or d.get("reason")
        return f"rule={rule}"
    if dim == "freshness" and d.get("max_date"):
        return f"max_date={d['max_date']}  days_since_max={d.get('days_since_max')}"
    return ""


def _check_card(dim: str, results: list[CheckResult]) -> dict[str, Any]:
    label, impact, recommendation = _DIMENSION_COPY[dim]
    errored = [r for r in results if r.status == "error"]
    skipped = [r for r in results if r.status != "error" and _is_role_skip(r)]
    assessed = [r for r in results if r.status != "error" and not _is_role_skip(r)]
    with_issues = [r for r in assessed if r.issues_found > 0]
    total_issues = sum(r.issues_found for r in assessed)

    affected_columns = [str(r.column) for r in with_issues if r.column is not None]
    samples = sorted(with_issues, key=lambda r: r.issues_found, reverse=True)[:5]
    sample_findings = [
        {
            "column": str(r.column) if r.column is not None else "(dataset-level)",
            "issues": r.issues_found,
            "detail": _sample_detail(dim, r),
        }
        for r in samples
    ]

    pass_ratio = (
        100.0 * (len(assessed) - len(with_issues)) / len(assessed) if assessed else None
    )
    severity_key = "none" if not with_issues else _severity_for_score(pass_ratio)

    return {
        "dim": dim,
        "label": label,
        "impact": impact,
        "recommendation": recommendation,
        "checked": len(results),
        "assessed": len(assessed),
        "skipped": len(skipped),
        "errored": len(errored),
        "with_issues": len(with_issues),
        "total_issues": total_issues,
        "affected_columns": affected_columns,
        "sample_findings": sample_findings,
        "severity_key": severity_key,
    }


def _column_quality_matrix(
    dimension_results: dict[str, list[CheckResult]],
) -> list[dict[str, Any]]:
    """
    One row per column, aggregated equally across every dimension check
    that actually applies to that column (role-skips and errors excluded,
    same rule scoring.py uses). Score = 100 * checks_passed / checks_assessed
    for that column -- an unweighted per-column pass rate, documented here
    rather than an undocumented formula: this keeps the matrix a direct,
    checkable function of the same CheckResult objects everything else in
    the report uses, with no separate/opaque scoring logic of its own.
    Duplicates are dataset-level (column=None for the full-row check), not
    attributable to one column, so they're excluded from this matrix --
    same as the check-card table above.
    """
    col_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"assessed": 0, "passed": 0, "issues": 0, "role": None, "dims": set()}
    )
    for dim, results in dimension_results.items():
        for r in results:
            if r.column is None or r.status == "error" or _is_role_skip(r):
                continue
            stats = col_stats[str(r.column)]
            stats["assessed"] += 1
            if r.status == "passed":
                stats["passed"] += 1
            stats["issues"] += r.issues_found
            if r.issues_found > 0:
                stats["dims"].add(dim)

    rows = []
    for col, stats in col_stats.items():
        assessed = stats["assessed"]
        score = round(100.0 * stats["passed"] / assessed, 0) if assessed else 100.0
        severity_key = "none" if stats["issues"] == 0 else _severity_for_score(score)
        recommendation = "No action needed."
        if stats["dims"]:
            first_dim = sorted(stats["dims"])[0]
            recommendation = _DIMENSION_COPY.get(
                first_dim, (None, None, "Review this column.")
            )[2]
        rows.append(
            {
                "column": col,
                "score": score,
                "issues": stats["issues"],
                "severity_key": severity_key,
                "recommendation": recommendation,
            }
        )
    rows.sort(key=lambda r: (r["score"], -r["issues"]))
    return rows


def _kpi_card(dim: str, info: dict[str, Any]) -> str:
    label = dim.replace("_", " ").title()
    score = info.get("score")
    weight = info.get("weight", 0.0)
    available = info.get("available", False)
    if not available or score is None:
        color = "#94A3B8"
        display = "N/A"
        width = "0%"
        meta = f"weight {weight * 100:.0f}% &middot; Skipped"
    else:
        sev = _severity_for_score(score)
        color = _SEVERITY_COLORS[sev]
        display = f"{score:.0f}" if score == round(score) else f"{score:.1f}"
        width = f"{score}%"
        meta = f"weight {weight * 100:.0f}% &middot; Assessed"
    return f"""
    <div class="kpi-card">
      <div class="kpi-label">{_esc(label)}</div>
      <div class="kpi-score" style="color:{color}">{display}</div>
      <div class="kpi-bar-track"><div class="kpi-bar-fill" style="width:{width};background:{color}"></div></div>
      <div class="kpi-meta">{meta}</div>
    </div>"""


def _check_card_html(card: dict[str, Any]) -> str:
    sev = card["severity_key"]
    badge = _severity_badge(sev.capitalize() if sev != "none" else "None", sev)
    affected = ", ".join(card["affected_columns"][:10]) or "None"
    if len(card["affected_columns"]) > 10:
        affected += f" (+{len(card['affected_columns']) - 10} more)"
    samples_html = ""
    if card["sample_findings"]:
        items = "".join(
            f"<li><b>{_esc(s['column'])}</b>: {s['issues']} issue(s) "
            f"{_esc(s['detail'])}</li>"
            for s in card["sample_findings"]
        )
        samples_html = f"<ul class='sample-list'>{items}</ul>"
    skip_note = ""
    if card["skipped"]:
        skip_note = (
            f"<p class='muted-note'>{card['skipped']} column(s) not applicable "
            f"to this check (role-based skip) and excluded from scoring.</p>"
        )
    return f"""
    <div class="check-card" id="check-{_esc(card['dim'])}">
      <div class="check-header">
        <h3>{_esc(card['label'])}</h3>
        {badge}
      </div>
      <div class="check-stats">
        <div><span class="stat-num">{card['checked']}</span><span class="stat-label">Checked</span></div>
        <div><span class="stat-num">{card['with_issues']}</span><span class="stat-label">With Issues</span></div>
        <div><span class="stat-num">{card['total_issues']}</span><span class="stat-label">Total Issues</span></div>
      </div>
      <p><b>Affected Columns:</b> {_esc(affected)}</p>
      <p><b>Business Impact:</b> {_esc(card['impact'])}</p>
      <p><b>Recommendation:</b> {_esc(card['recommendation'])}</p>
      {samples_html}
      {skip_note}
    </div>"""


def _matrix_row_html(row: dict[str, Any]) -> str:
    sev = row["severity_key"]
    label = sev.capitalize() if sev != "none" else "None"
    return f"""
    <tr>
      <td>{_esc(row['column'])}</td>
      <td>{row['score']:.0f}</td>
      <td>{row['issues']}</td>
      <td>{_severity_badge(label, sev)}</td>
      <td>{_esc(row['recommendation'])}</td>
    </tr>"""


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  margin: 0; background: #F1F5F9; color: #0F172A; line-height: 1.5; }
.container { max-width: 1080px; margin: 0 auto; padding: 0 24px 40px; }
.hero { background: linear-gradient(135deg, #0F172A 0%, #1E3A5F 55%, #1E293B 100%);
  color: white; padding: 56px 24px 40px; text-align: center; }
.hero h1 { margin: 0 0 6px; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; }
.hero .subtitle { color: #94A3B8; font-size: 14px; margin-bottom: 32px; }
.score-hero { display: flex; align-items: center; justify-content: center; gap: 36px; flex-wrap: wrap; }
.score-hero .label-block { text-align: left; }
.score-hero .qualifier { font-size: 22px; font-weight: 700; margin: 0 0 8px; }
.readiness-badge { color: white; font-size: 13px; font-weight: 600; padding: 6px 14px;
  border-radius: 20px; display: inline-block; letter-spacing: 0.01em; }
.hero-meta { display: flex; justify-content: center; gap: 28px; margin-top: 28px;
  font-size: 12.5px; color: #94A3B8; flex-wrap: wrap; }
.card { background: white; border-radius: 14px; padding: 26px; margin-top: 20px;
  box-shadow: 0 1px 2px rgba(15,23,42,0.06); border: 1px solid #E2E8F0; page-break-inside: avoid; }
h2 { font-size: 17px; font-weight: 700; margin: 0 0 18px; padding-bottom: 12px;
  border-bottom: 2px solid #F1F5F9; color: #0F172A; }
h2 .h2-note { font-weight: 400; font-size: 12.5px; color: #64748B; }
.badge { color: white; font-size: 10.5px; font-weight: 700; padding: 3px 10px;
  border-radius: 20px; display: inline-block; text-transform: uppercase; letter-spacing: 0.03em; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }
.kpi-card { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px; }
.kpi-label { font-size: 11px; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.kpi-score { font-size: 30px; font-weight: 800; margin: 6px 0; }
.kpi-bar-track { background: #E2E8F0; border-radius: 6px; height: 6px; overflow: hidden; }
.kpi-bar-fill { height: 100%; border-radius: 6px; }
.kpi-meta { font-size: 10.5px; color: #94A3B8; margin-top: 8px; }
.check-card { border: 1px solid #E2E8F0; border-radius: 12px; padding: 20px; margin-bottom: 16px; page-break-inside: avoid; }
.check-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.check-header h3 { margin: 0; font-size: 15.5px; }
.check-stats { display: flex; gap: 32px; margin: 14px 0; }
.stat-num { display: block; font-size: 22px; font-weight: 800; color: #0F172A; }
.stat-label { font-size: 10.5px; color: #64748B; text-transform: uppercase; letter-spacing: 0.03em; }
.sample-list { font-size: 12.5px; color: #475569; padding-left: 18px; margin: 10px 0 0; }
.muted-note { font-size: 11.5px; color: #94A3B8; font-style: italic; margin: 8px 0 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #0F172A; color: white; text-align: left; padding: 9px 12px; font-size: 11.5px;
  text-transform: uppercase; letter-spacing: 0.03em; }
td { padding: 9px 12px; border-bottom: 1px solid #F1F5F9; }
tr:nth-child(even) td { background: #F8FAFC; }
.findings-critical { color: #DC2626; font-size: 13px; margin: 0 0 8px; }
.findings-positive { color: #16A34A; font-size: 13px; margin: 0 0 8px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }
.nav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.nav a { font-size: 12px; color: #2563EB; text-decoration: none; background: #EFF6FF;
  padding: 5px 10px; border-radius: 6px; }
.provenance { font-size: 11.5px; color: #64748B; background: #F0F9FF; border: 1px solid #BAE6FD;
  border-radius: 8px; padding: 10px 14px; margin-top: 20px; }
footer { text-align: center; font-size: 11px; color: #94A3B8; padding: 24px 0 8px; }
@media print {
  body { background: white; }
  .card { box-shadow: none; break-inside: avoid; }
  .nav { display: none; }
}
"""


def _gauge_svg(score: float | None, color: str) -> str:
    if score is None:
        score = 0.0
    circumference = 2 * 3.14159265 * 52
    offset = circumference * (1 - min(max(score, 0), 100) / 100)
    return f"""
    <svg width="140" height="140" viewBox="0 0 120 120">
      <circle cx="60" cy="60" r="52" fill="none" stroke="#334155" stroke-width="12"/>
      <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="12"
        stroke-dasharray="{circumference:.1f}" stroke-dashoffset="{offset:.1f}"
        stroke-linecap="round" transform="rotate(-90 60 60)"/>
      <text x="60" y="66" text-anchor="middle" font-size="26" font-weight="800" fill="white">{score:.1f}</text>
    </svg>"""


def _quality_label(score: float | None) -> tuple[str, str, str]:
    """Returns (label, readiness_text, severity_key)."""
    if score is None:
        return "Unscored", "Insufficient data to assess readiness.", "low"
    if score >= 90:
        return "Excellent", "Ready to use", "none"
    if score >= 75:
        return "Good", "Ready with Light Cleaning", "low"
    if score >= 60:
        return "Fair", "Ready with Moderate Cleaning", "medium"
    if score >= 40:
        return "Poor", "Significant Cleanup Required", "high"
    return "Critical", "Not Recommended", "critical"


def _ml_readiness_html(readiness: dict[str, Any] | None) -> str:
    """
    Render the Phase 2 M3 "ML Model Readiness Assessment" card from a
    ``ReadinessScore``-shaped dict (see phase2/readiness/scorer.py).
    Returns "" when readiness is None (M3 wasn't run for this file) --
    the card is omitted entirely rather than showing placeholder/N-A data.
    """
    if not readiness:
        return ""

    verdict = str(readiness.get("verdict", "not_ready")).lower()
    verdict_sev = {"ready": "none", "caution": "medium", "not_ready": "critical"}.get(
        verdict, "critical"
    )
    verdict_label = verdict.replace("_", " ").upper()

    blockers = readiness.get("blockers") or []
    warnings = readiness.get("warnings") or []
    recommendations = readiness.get("recommendations") or []

    temporal = readiness.get("temporal") or {}
    interval = readiness.get("interval") or {}
    target = readiness.get("target") or {}
    leakage = readiness.get("leakage") or {}

    def _row(label: str, value: Any) -> str:
        return f"<p><b>{_esc(label)}:</b> {_esc(str(value))}</p>"

    blockers_html = (
        "".join(f"<li>{_esc(b)}</li>" for b in blockers) if blockers else "<li>None</li>"
    )
    warnings_html = (
        "".join(f"<li>{_esc(w)}</li>" for w in warnings) if warnings else "<li>None</li>"
    )
    recs_html = (
        "".join(f"<li>{_esc(r)}</li>" for r in recommendations)
        if recommendations
        else "<li>None</li>"
    )

    return f"""
  <div class="card">
    <h2>ML Model Readiness Assessment <span class="h2-note">(Prophet forecasting preconditions -- separate from the Data Quality Score above)</span></h2>
    {_severity_badge(verdict_label, verdict_sev)}
    <p style="margin-top:12px"><b>Overall Score:</b> {readiness.get('overall_score', 0):.1f} / 100</p>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Temporal</div><div class="kpi-score">{readiness.get('temporal_score', 0):.0f}</div></div>
      <div class="kpi-card"><div class="kpi-label">Interval</div><div class="kpi-score">{readiness.get('interval_score', 0):.0f}</div></div>
      <div class="kpi-card"><div class="kpi-label">Target</div><div class="kpi-score">{readiness.get('target_score', 0):.0f}</div></div>
      <div class="kpi-card"><div class="kpi-label">Leakage</div><div class="kpi-score">{readiness.get('leakage_score', 0):.0f}</div></div>
    </div>

    <div class="two-col" style="margin-top:16px">
      <div>
        <h4>Temporal Sufficiency</h4>
        {_row("Observations", temporal.get("total_observations", "-"))}
        {_row("Date range (days)", temporal.get("date_range_days", "-"))}
        {_row("Implied frequency", temporal.get("implied_frequency", "-"))}
        {_row("Seasonal cycles detected", temporal.get("seasonal_cycles_detected", "-"))}
      </div>
      <div>
        <h4>Interval Regularity</h4>
        {_row("Inferred frequency", interval.get("inferred_frequency", "-"))}
        {_row("Missing intervals", interval.get("missing_intervals", "-"))}
        {_row("Duplicate timestamps", interval.get("duplicate_timestamps", "-"))}
        {_row("Regularity score", interval.get("regularity_score", "-"))}
      </div>
    </div>
    <div class="two-col" style="margin-top:16px">
      <div>
        <h4>Target Integrity ({_esc(str(target.get("column_name", "-")))})</h4>
        {_row("Null %", target.get("null_pct", "-"))}
        {_row("Zero %", target.get("zero_pct", "-"))}
        {_row("Outlier %", target.get("outlier_pct", "-"))}
        {_row("Variance", target.get("variance", "-"))}
      </div>
      <div>
        <h4>Leakage &amp; Cardinality</h4>
        {_row("Perfectly-correlated features", ", ".join(leakage.get("perfect_correlation_features", []) or ["none"]))}
        {_row("High-cardinality features", ", ".join(leakage.get("high_cardinality_features", []) or ["none"]))}
        {_row("Identifier-like features", ", ".join(leakage.get("identifier_features", []) or ["none"]))}
      </div>
    </div>

    <div class="two-col" style="margin-top:16px">
      <div>
        <h4 class="findings-critical">Blockers</h4>
        <ul>{blockers_html}</ul>
        <h4 class="findings-critical" style="margin-top:12px">Warnings</h4>
        <ul>{warnings_html}</ul>
      </div>
      <div>
        <h4 class="findings-positive">Recommendations</h4>
        <ul>{recs_html}</ul>
      </div>
    </div>
  </div>
"""


def generate_html_report(
    *,
    file_label: str,
    sheet_name: str,
    rows: int,
    columns: int,
    header_row: int | None,
    classification: dict[str, str],
    task2_summary: dict[str, Any],
    task3_summary: dict[str, Any],
    task4_summary: dict[str, Any],
    fuzzy_summary: dict[str, Any],
    task5_summary: dict[str, Any],
    score: dict[str, Any],
    processing_date: str,
    execution_time_s: float,
    engine_version: str = "Phase 1",
    readiness: dict[str, Any] | None = None,
) -> str:
    """
    Build the full HTML report as a string.

    Every dimension_results list handed to ``score`` (compute_data_quality_score,
    already called by the caller) is reused here verbatim for the check
    cards and column matrix -- see module docstring. Nothing here re-derives
    a number that the console output didn't already compute.

    readiness: optional Phase 2 M3 result -- an asdict()'d
    ``ReadinessScore`` with ``temporal``/``interval``/``target``/``leakage``
    sub-dicts attached (see main.py's ``_print_ml_readiness_results`` for
    the exact shape). None (default) omits the ML Readiness card entirely
    rather than rendering placeholder data.
    """
    consistency_results = list(task5_summary["consistency_results"])
    if fuzzy_summary and fuzzy_summary.get("fuzzy_results"):
        consistency_results = consistency_results + list(fuzzy_summary["fuzzy_results"])

    dimension_results: dict[str, list[CheckResult]] = {
        "completeness": task2_summary["missing_results"],
        "type_reliability": task2_summary["type_results"],
        "outlier_risk": task3_summary["outlier_results"],
        "schema_quality": task5_summary["schema_results"],
        "consistency": consistency_results,
        "validity": task5_summary["validity_results"],
        "freshness": task5_summary["freshness_results"],
    }

    check_cards = {dim: _check_card(dim, results) for dim, results in dimension_results.items()}

    # Duplicates: dataset-level, column=None for the primary result -- kept
    # as its own card built from the same duplicate_results list scoring.py
    # consumes for the uniqueness dimension, not recomputed.
    dup_results = task2_summary.get("duplicate_results") or [task2_summary["duplicate_result"]]
    dup_card = _check_card("uniqueness", dup_results)
    dup_card["checked"] = 1  # one logical "duplicates" check, matches console framing
    check_cards["uniqueness"] = dup_card

    matrix_rows = _column_quality_matrix(dimension_results)

    dqs = score.get("data_quality_score")
    quality_label, readiness_text, quality_sev = _quality_label(dqs)
    gauge_color = _SEVERITY_COLORS[quality_sev]

    dimension_scores = score.get("dimension_scores", {})
    kpi_html = "".join(_kpi_card(dim, dimension_scores.get(dim, {})) for dim in _DIMENSION_ORDER)

    critical_findings = []
    positive_findings = []
    for dim in _DIMENSION_ORDER:
        card = check_cards.get(dim)
        if not card:
            continue
        if card["with_issues"] > 0:
            critical_findings.append(
                f"{card['label']}: {len(card['affected_columns'])} column(s) affected, "
                f"{card['total_issues']} issue(s) found"
            )
        elif card["assessed"] > 0:
            positive_findings.append(f"{card['label']}: no issues found")
    if dup_card["with_issues"] > 0:
        critical_findings.insert(
            1, f"Duplicates: {dup_card['total_issues']} issue(s) found across the business key"
        )
    pii_flagged = task4_summary["pii_columns_with_hits"]
    if pii_flagged:
        critical_findings.append(
            f"PII detected in {pii_flagged} column(s) -- masking required before sharing"
        )

    risk = score.get("privacy_risk")
    risk_level = (risk or {}).get("risk_level", "none")
    risk_sev = {"none": "none", "low": "low", "medium": "medium", "high": "critical"}.get(
        risk_level, "low"
    )

    role_counts: dict[str, int] = defaultdict(int)
    for role in classification.values():
        role_counts[role] += 1

    fuzzy_flagged = fuzzy_summary.get("fuzzy_columns_with_issues", 0) if fuzzy_summary else 0
    fuzzy_rows_remapped = sum(
        r.issues_found
        for r in (fuzzy_summary.get("fuzzy_results") or [])
        if r.status != "error" and not _is_role_skip(r)
    ) if fuzzy_summary else 0
    fuzzy_flagged_cols = [
        str(r.column)
        for r in (fuzzy_summary.get("fuzzy_results") or [])
        if r.status != "error" and not _is_role_skip(r) and r.issues_found > 0
    ] if fuzzy_summary else []

    excluded = score.get("dimensions_excluded") or []
    excluded_html = (
        f"<p><b>Dimensions Excluded:</b> {_esc(', '.join(excluded))}</p>" if excluded else ""
    )

    check_cards_html = "".join(
        _check_card_html(check_cards[dim]) for dim in _DIMENSION_ORDER if dim != "uniqueness"
    )
    # Insert duplicates card in its natural rubric position (after completeness)
    ordered_dims = ["completeness", "uniqueness"] + [
        d for d in _DIMENSION_ORDER if d not in ("completeness",)
    ]
    check_cards_html = "".join(_check_card_html(check_cards[d]) for d in ordered_dims)
    nav_html = "".join(
        f'<a href="#check-{_esc(d)}">{_esc(check_cards[d]["label"])}</a>' for d in ordered_dims
    )

    matrix_html = "".join(_matrix_row_html(r) for r in matrix_rows)

    top_issues = []
    for dim in _DIMENSION_ORDER:
        card = check_cards.get(dim)
        if not card:
            continue
        for col in card["affected_columns"]:
            top_issues.append((card["label"], col, card["impact"], card["recommendation"]))
    top_issues.sort(key=lambda t: 0)  # stable, keeps dimension-priority order above
    top_issues = top_issues[:10]
    top_issues_html = "".join(
        f"""
        <tr>
          <td>{i + 1}</td>
          <td>{_esc(label)}</td>
          <td>{_esc(col)}</td>
          <td>{_severity_badge("Critical", "critical")}</td>
          <td>{_esc(impact)}</td>
          <td>{_esc(reco)}</td>
        </tr>"""
        for i, (label, col, impact, reco) in enumerate(top_issues)
    )

    weights_str = ", ".join(
        f"{dim.replace('_', ' ').title()} ({dimension_scores.get(dim, {}).get('weight', 0) * 100:.0f}%)"
        for dim in _DIMENSION_ORDER
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Data Quality Report -- {_esc(file_label)}</title>
<style>{_CSS}</style>
</head>
<body>

<div class="hero">
  <h1>Data Quality Assessment Report</h1>
  <div class="subtitle">{_esc(file_label)} &middot; Sheet: {_esc(sheet_name)}</div>
  <div class="score-hero">
    {_gauge_svg(dqs, gauge_color)}
    <div class="label-block">
      <p class="qualifier" style="color:{gauge_color}">{_esc(quality_label)}</p>
      <span class="readiness-badge" style="background:{gauge_color}">{_esc(readiness_text)}</span>
    </div>
  </div>
  <div class="hero-meta">
    <div>Processing Date: {_esc(processing_date)}</div>
    <div>Engine Version: {_esc(engine_version)}</div>
    <div>Execution Time: {execution_time_s:.2f}s</div>
    <div>Rows: {rows:,} &middot; Columns: {columns}</div>
  </div>
</div>

<div class="container">

  <div class="card">
    <h2>Executive Summary</h2>
    <p><b>Rows / Columns:</b> {rows:,} rows &times; {columns} columns
       &middot; <b>Header Row:</b> {header_row if header_row is not None else '-'}
       &middot; <b>Checks Executed:</b> {len(_DIMENSION_ORDER) + 1}</p>
    {excluded_html}
    <div class="two-col">
      <div>
        <h4 class="findings-critical">Critical Findings</h4>
        <ul>{"".join(f"<li>{_esc(f)}</li>" for f in critical_findings) or "<li>None</li>"}</ul>
      </div>
      <div>
        <h4 class="findings-positive">Positive Findings</h4>
        <ul>{"".join(f"<li>{_esc(f)}</li>" for f in positive_findings) or "<li>None</li>"}</ul>
      </div>
    </div>
    <div class="provenance">
      Every figure in this report is read directly from the same check
      results the engine's console output prints during this run -- there
      is no separate report-only calculation, so this document cannot
      disagree with the terminal output for the same run.
    </div>
  </div>

  <div class="card">
    <h2>Data Quality Dashboard</h2>
    <div class="kpi-grid">{kpi_html}</div>
  </div>

  <div class="card">
    <h2>Privacy Risk <span class="h2-note">(separate -- never part of the score above)</span></h2>
    {_severity_badge(risk_level.upper() if risk else "NONE", risk_sev)}
    <p style="margin-top:12px">Columns with PII: <b>{(risk or {}).get('columns_with_pii', 0)} / {(risk or {}).get('total_columns', columns)}</b></p>
    <p>Types found: {_esc(', '.join((risk or {}).get('pii_types_found', [])) or 'none')}</p>
  </div>

  <div class="card">
    <h2>Dataset Overview</h2>
    <h4>Column Classification</h4>
    <div class="kpi-grid">
      {"".join(f'<div class="kpi-card"><div class="kpi-label">{_esc(role.replace("_"," ").title())}</div><div class="kpi-score">{count}</div></div>' for role, count in sorted(role_counts.items()))}
    </div>
  </div>

  <div class="card">
    <h2>PII &amp; Fuzzy Standardization Summary</h2>
    <div class="two-col">
      <div>
        <p><b>PII columns:</b> {task4_summary['pii_columns_with_hits']} / {task4_summary['pii_columns_scanned']}</p>
        <p><b>Rows with PII:</b> {task4_summary['pii_rows_with_hits']:,}</p>
        <p><b>Types found:</b> {_esc(', '.join((risk or {}).get('pii_types_found', [])) or 'none')}</p>
      </div>
      <div>
        <p><b>Columns with fuzzy remaps:</b> {fuzzy_flagged}</p>
        <p><b>Rows remapped:</b> {fuzzy_rows_remapped}</p>
        <p><b>Flagged columns:</b> {_esc(', '.join(fuzzy_flagged_cols) or 'none')}</p>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Quality Checks</h2>
    <div class="nav">{nav_html}</div>
    {check_cards_html}
  </div>

  {_ml_readiness_html(readiness)}
  <div class="card">
    <h2>Column Quality Matrix</h2>
    <table>
      <tr><th>Column</th><th>Score</th><th>Issues</th><th>Severity</th><th>Recommendation</th></tr>
      {matrix_html}
    </table>
  </div>

  <div class="card">
    <h2>Top Critical Issues</h2>
    <table>
      <tr><th>#</th><th>Check</th><th>Column</th><th>Severity</th><th>Impact</th><th>Action</th></tr>
      {top_issues_html or "<tr><td colspan='6'>No critical issues.</td></tr>"}
    </table>
  </div>

  <div class="card">
    <h2>Appendix</h2>
    <p><b>Scoring formula:</b> Each dimension score = 100 &times; (passed checks / assessed checks),
    where role-based skips and errors are excluded from "assessed". Composite = weighted average
    across scorable dimensions only, weights re-normalized when a dimension has no results.
    Privacy Risk is calculated and reported separately -- never subtracted from the composite score.</p>
    <p><b>Column Quality Matrix score:</b> per column, 100 &times; (checks passed / checks assessed for
    that column), unweighted across dimensions -- a simple, directly-checkable function of the same
    check results above, not a separate model.</p>
    <p><b>Quality dimensions:</b> {weights_str}</p>
  </div>

</div>
<footer>Generated by Data Quality Engine &middot; {_esc(engine_version)} (deterministic, rule-based, no AI)</footer>
</body>
</html>
"""


def write_html_report(html_str: str, *, reports_dir: Path, filename: str) -> Path:
    """Write ``html_str`` to ``reports_dir/filename``, creating the
    directory if needed. Returns the written path."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / filename
    out_path.write_text(html_str, encoding="utf-8")
    return out_path