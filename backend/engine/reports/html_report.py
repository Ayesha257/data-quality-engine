"""Renders build_report_data()'s output to a single self-contained HTML page.

This is the "frontend page" -- no server, no framework, no build step.
Open the generated .html file directly in any browser. Inline CSS only,
so it also works as a standalone attachment/email preview.

Presentation layer only: every number/label rendered here comes from
build_report_data() (report_generator.py), which itself only reshapes
CheckResult output already produced by engine/checks/*.py. Nothing in this
file recomputes a score, a severity, or a duplicate/uniqueness decision --
it only decides how to *display* them (enterprise wording, cards, charts).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Feather-style line icons (24x24, stroke=currentColor) -- replace the old
# emoji HTML entities (&#128202; etc.) so the report renders identically on
# every OS/browser instead of depending on the system's emoji font, and
# reads as a professional document rather than a chat message.
_ICONS = {
    "clipboard": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"/><path d="M9 11h6M9 15h6"/></svg>',
    "shield-check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z"/><path d="m9 12 2 2 4-4"/></svg>',
    "plug": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v4M15 2v4M7 8h10l-1 5a5 5 0 0 1-8 0L7 8Z"/><path d="M12 17v5"/></svg>',
    "scale": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M5 7h14M5 7l-3 7a3.5 3.5 0 0 0 6 0L5 7ZM19 7l-3 7a3.5 3.5 0 0 0 6 0L19 7Z"/></svg>',
    "key": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="m10.6 12.4 8.4-8.4M16 4l3 3M13 7l3 3"/></svg>',
    "grid": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    "trend-up": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 17 6-6 4 4 8-8"/><path d="M15 6h6v6"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>',
    "bar-chart": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>',
    "layers": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/></svg>',
}


def _icon(name: str) -> str:
    return _ICONS.get(name, _ICONS["bar-chart"])

_SEVERITY_COLORS = {
    "Critical": "#DC2626",
    "High": "#EA580C",
    "Medium": "#D97706",
    "Low": "#78716C",
    "None": "#28A89E",
}
_RATING_COLORS = {
    "Excellent": "#28A89E",
    "Good": "#65A30D",
    "Fair": "#D97706",
    "Poor": "#DC2626",
    "Unrated": "#78716C",
}
_READINESS_COLORS = {
    "Ready": "#28A89E",
    "Ready with Minor Cleaning": "#65A30D",
    "Ready with Moderate Cleaning": "#D97706",
    "Not Recommended": "#DC2626",
}
_RISK_COLORS = {"high": "#DC2626", "medium": "#D97706", "low": "#65A30D", "none": "#28A89E"}

# Enterprise wording map -- cosmetic only, does not touch check_name keys
# used anywhere else in the pipeline.
_ENTERPRISE_LABEL = {
    "missing_values": "Completeness Assessment",
    "duplicates": "Duplicate Record Assessment",
    "type_mismatch": "Type Reliability Assessment",
    "outliers": "Outlier Risk Assessment",
    "pii": "Sensitive Data Assessment",
    "consistency": "Value Consistency Assessment",
    "schema_quality": "Schema Quality Assessment",
    "validity": "Validity & Logic Assessment",
    "freshness": "Data Freshness Assessment",
    "referential_integrity": "Referential Integrity Assessment",
    "hipaa_phi": "HIPAA PHI Compliance Scan",
}

_DIM_ICONS = {
    "completeness": _icon("clipboard"),
    "validity": _icon("shield-check"),
    "type_reliability": _icon("plug"),
    "consistency": _icon("scale"),
    "uniqueness": _icon("key"),
    "schema_quality": _icon("grid"),
    "outlier_risk": _icon("trend-up"),
    "freshness": _icon("clock"),
}

_ROLE_LABELS = {
    "measurement": "Measurement",
    "identifier": "Business Key / Identifier",
    "pii": "Sensitive (PII)",
    "free_text": "Free Text",
    "categorical": "Categorical",
    "date": "Date",
}


def _badge(label: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _grade_letter(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= 97:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 55:
        return "D"
    return "F"


def _score_ring(score: float | None, size: int = 160) -> str:
    if score is None:
        pct, display = 0, "N/A"
    else:
        pct = max(0, min(100, score))
        display = f"{score:.1f}"
    circumference = 2 * 3.14159 * 52
    offset = circumference * (1 - pct / 100)
    color = "#28A89E" if pct >= 90 else "#65A30D" if pct >= 75 else "#D97706" if pct >= 55 else "#DC2626"
    grade = _grade_letter(score)
    return f"""
    <div class="gauge-wrap">
      <svg width="{size}" height="{size}" viewBox="0 0 120 120" class="gauge-svg">
        <circle cx="60" cy="60" r="52" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="10"/>
        <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="10"
          stroke-dasharray="{circumference:.1f}" stroke-dashoffset="{offset:.1f}"
          stroke-linecap="round" transform="rotate(-90 60 60)" class="gauge-arc"/>
        <text x="60" y="58" text-anchor="middle" font-size="24" font-weight="800" fill="white">{display}</text>
        <text x="60" y="78" text-anchor="middle" font-size="12" font-weight="700" fill="{color}">{grade}</text>
      </svg>
    </div>
    """


def _sheet_disclosure_banner(sd: dict[str, Any], analyzed_sheet: str) -> str:
    total = sd.get("total_sheets_in_workbook", 1)
    if total <= 1:
        return ""
    other = sd.get("other_sheets_in_workbook") or []
    also_reported = sd.get("other_sheets_also_reported") or []
    not_covered = [s for s in other if s not in also_reported]
    hidden = sd.get("hidden_sheet_names") or []

    parts = [f"This workbook contains <b>{total}</b> sheets. This report covers sheet <b>'{analyzed_sheet}'</b> only."]
    if also_reported:
        parts.append(f"Separate reports were also generated for: {', '.join(also_reported)}.")
    if not_covered:
        parts.append(
            f'<span style="color:#DC2626"><b>Not analyzed in any report:</b> {", ".join(not_covered)}.</span>'
        )
    if hidden:
        parts.append(f"Hidden sheet(s) skipped: {', '.join(hidden)}.")

    return (
        '<div class="alert alert-warning">' + " ".join(parts) + "</div>"
    )


def _kpi_card(label: str, score_info: dict[str, Any]) -> str:
    score = score_info.get("score")
    available = score_info.get("available", False)
    weight = score_info.get("weight", 0)
    if not available or score is None:
        bar_pct = 0
        color = "#A29A8D"
        score_text = "N/A"
    else:
        bar_pct = score
        color = "#28A89E" if bar_pct >= 90 else "#65A30D" if bar_pct >= 75 else "#D97706" if bar_pct >= 55 else "#DC2626"
        score_text = f"{score:.0f}"
    status = "Assessed" if available else "Not Scorable"
    icon = _DIM_ICONS.get(label, _icon("bar-chart"))
    return f"""
    <div class="kpi-card">
      <div class="kpi-icon" style="background:{color}22;color:{color}">{icon}</div>
      <div class="kpi-label">{label.replace('_', ' ').title()}</div>
      <div class="kpi-score" style="color:{color}">{score_text}</div>
      <div class="kpi-bar-track"><div class="kpi-bar-fill" style="width:{bar_pct}%;background:{color}"></div></div>
      <div class="kpi-meta">Weight {weight:.0%} &middot; {status}</div>
    </div>
    """


def _stat_card(icon: str, number: str, label: str, color: str = "#28A89E") -> str:
    return f"""
    <div class="stat-card">
      <div class="stat-card-icon" style="background:{color}1A;color:{color}">{icon}</div>
      <div>
        <div class="stat-card-num">{number}</div>
        <div class="stat-card-label">{label}</div>
      </div>
    </div>
    """


def _rule_row(name: str, summary: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(summary["severity"], "#97907F")
    status = "Passed" if summary["severity"] == "None" else ("Warning" if summary["severity"] in ("Low", "Medium") else "Failed")
    status_color = "#28A89E" if status == "Passed" else ("#D97706" if status == "Warning" else "#DC2626")
    label = _ENTERPRISE_LABEL.get(name, summary["display_name"])
    return f"""
    <tr>
      <td><a href="#check-{name}"><b>{label}</b></a></td>
      <td>{_badge(status, status_color)}</td>
      <td>{_badge(summary['severity'], color)}</td>
      <td>{summary['total_issues_found']}</td>
      <td>{summary['business_impact']}</td>
      <td>{summary['recommendation']}</td>
    </tr>
    """


def _check_card(name: str, summary: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(summary["severity"], "#97907F")
    cols = ", ".join(summary["affected_columns"][:10]) or "None"
    label = _ENTERPRISE_LABEL.get(name, summary["display_name"])
    samples_html = ""
    breakdown = summary.get("column_breakdown") or []
    if breakdown:
        rows = ""
        for row in breakdown:
            col = row.get("column", "-")
            issues = row.get("issues_found", "-")
            identifiers = row.get("identifiers") or {}
            id_str = ", ".join(f"{k}: {v}" for k, v in sorted(identifiers.items()))
            detail = f" ({id_str})" if id_str else ""
            rows += f"<li><b>{col}</b>: {issues} issue(s){detail}</li>"
        samples_html = (
            f"<p><b>Per-Column Breakdown:</b></p><ul class='sample-list'>{rows}</ul>"
        )
    elif summary["sample_findings"]:
        rows = ""
        for f in summary["sample_findings"]:
            col = f.get("column", "-")
            issues = f.get("issues_found", "-")
            extra = {k: v for k, v in f.items() if k not in ("column", "issues_found")}
            extra_str = "; ".join(f"{k}={v}" for k, v in extra.items())
            rows += f"<li><b>{col}</b>: {issues} issue(s) {extra_str}</li>"
        samples_html = f"<ul class='sample-list'>{rows}</ul>"
    return f"""
    <details class="check-card" id="check-{name}">
      <summary class="check-header">
        <h3>{label}</h3>
        {_badge(summary['severity'], color)}
      </summary>
      <div class="check-stats">
        <div><span class="stat-num">{summary['columns_checked']}</span><span class="stat-label">Rules Executed</span></div>
        <div><span class="stat-num">{summary['columns_with_issues']}</span><span class="stat-label">Exceptions Raised</span></div>
        <div><span class="stat-num">{summary['total_issues_found']}</span><span class="stat-label">Affected Records</span></div>
      </div>
      <p><b>Affected Columns:</b> {cols}</p>
      <p><b>Business Impact:</b> {summary['business_impact']}</p>
      <p><b>Recommended Remediation:</b> {summary['recommendation']}</p>
      {samples_html}
    </details>
    """


def _matrix_row(row: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(row["severity"], "#97907F")
    return f"""
    <tr>
      <td>{row['column']}</td>
      <td><span class="role-pill">{_ROLE_LABELS.get(row['role'], row['role'])}</span></td>
      <td>{row['quality_score']:.0f}</td>
      <td>{row['issues_found']}</td>
      <td>{_badge(row['severity'], color)}</td>
      <td>{row['recommendation']}</td>
    </tr>
    """


def _issue_row(i: int, issue: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(issue["severity"], "#97907F")
    return f"""
    <tr>
      <td>{i}</td>
      <td>{issue['issue']}</td>
      <td>{issue['column']}</td>
      <td>{_badge(issue['severity'], color)}</td>
      <td>{issue['impact']}</td>
      <td>{issue['action']}</td>
    </tr>
    """


def _duplicate_section(da: dict[str, Any]) -> str:
    fr = da.get("full_row", {})
    fr_color = "#28A89E" if fr.get("issues_found", 0) == 0 else "#DC2626"
    fr_status = "No exact duplicate records found" if fr.get("issues_found", 0) == 0 else "Exact duplicate records detected"

    bk_rows = ""
    for bk in da.get("business_keys", []):
        color = "#28A89E" if bk["status"] == "passed" else "#DC2626"
        notes = ", ".join(bk["evidence_notes"]) or "Meets configured confidence threshold"
        conf = f"{bk['confidence_pct']}%" if bk["confidence_pct"] is not None else "Manually configured"
        bk_rows += f"""
        <tr>
          <td><b>{bk['column']}</b></td>
          <td>{_badge('Business Key', '#28A89E')}</td>
          <td>{conf}</td>
          <td>{bk['issues_found']}</td>
          <td>{bk['rows_sharing_key']}</td>
          <td>{_badge(bk['status'].title(), color)}</td>
          <td class="evidence-cell">{notes}</td>
        </tr>
        """
    if not bk_rows:
        bk_rows = '<tr><td colspan="7" class="empty-state">No columns met the evidence threshold to be treated as a business key. Full-row detection above still applies.</td></tr>'

    return f"""
    <div class="card" id="duplicate-analysis">
      <h2>Duplicate Record Assessment</h2>
      <p class="section-intro">
        Two independent checks run here: an <b>exact full-row</b> comparison, and a
        <b>business-key</b> comparison limited to columns with real evidence of being
        intended-unique identifiers (name pattern, uniqueness ratio, repeated-value
        frequency, and column role all considered together -- a repeated
        "Product Description" or a categorical "*Code" column is not enough on its
        own to be flagged).
      </p>
      <div class="two-col" style="margin-bottom:18px">
        {_stat_card(_icon("layers"), str(fr.get('issues_found', 0)), fr_status, fr_color)}
        {_stat_card(_icon("key"), str(da.get('business_keys_evaluated', 0)), 'Business key column(s) confirmed by evidence', '#28A89E')}
      </div>
      <h4>Business-Key Duplicate Findings</h4>
      <table class="striped">
        <tr><th>Column</th><th>Detected As</th><th>Confidence</th><th>Extra Duplicates</th><th>Rows Sharing Key</th><th>Status</th><th>Evidence</th></tr>
        {bk_rows}
      </table>
    </div>
    """


def _column_intelligence_section(rows: list[dict[str, Any]]) -> str:
    focus = [r for r in rows if r["role"] in ("identifier", "pii") or r["is_business_key"]]
    focus.sort(key=lambda r: (not r["is_business_key"], r["role"]))
    body = ""
    for r in focus[:40]:
        conf = f"{r['confidence_pct']}%" if r["confidence_pct"] is not None else "&mdash;"
        evidence = ", ".join(r["evidence_notes"]) or "Role inferred from name, dtype and cardinality"
        key_badge = _badge("Business Key", "#28A89E") if r["is_business_key"] else ""
        body += f"""
        <tr>
          <td><b>{r['column']}</b> {key_badge}</td>
          <td><span class="role-pill">{_ROLE_LABELS.get(r['role'], r['role'])}</span></td>
          <td>{conf}</td>
          <td class="evidence-cell">{evidence}</td>
        </tr>
        """
    if not body:
        body = '<tr><td colspan="4" class="empty-state">No identifier / sensitive columns detected.</td></tr>'
    return f"""
    <div class="card" id="column-intelligence">
      <h2>Semantic Column Intelligence</h2>
      <p class="section-intro">Role, confidence, and supporting evidence for every column the engine
      treats as an identifier, business key, or sensitive (PII) field.</p>
      <table class="striped">
        <tr><th>Column</th><th>Detected Role</th><th>Confidence</th><th>Evidence</th></tr>
        {body}
      </table>
    </div>
    """


_CSS = """
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', -apple-system, Roboto, Helvetica, Arial, sans-serif; margin: 0; background: #F5F3EF; color: #3F3A34; }
.container { max-width: 1180px; margin: 0 auto; padding: 24px; }
.hero { background: linear-gradient(135deg, #14181F 0%, #1E2A2E 55%, #1F6D66 100%); color: white; padding: 40px 24px 32px; text-align: center; }
.hero-icon { width: 52px; height: 52px; display: flex; align-items: center; justify-content: center; border-radius: 14px; background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.3); margin: 0 auto 14px; color: #5EDCD2; }
.hero-icon svg { width: 26px; height: 26px; }
.hero-brand { font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: #9DD9D2; font-weight: 700; margin-bottom: 8px; }
.hero h1 { margin: 0 0 6px; font-size: 28px; font-weight: 800; letter-spacing: -0.01em; }
.hero .subtitle { color: #C9E8E4; font-size: 13.5px; margin-bottom: 20px; }
.hero-meta { display: flex; justify-content: center; gap: 28px; margin-top: 22px; font-size: 11.5px; color: #9DD9D2; flex-wrap: wrap; text-transform: uppercase; letter-spacing: 0.04em; }
.score-hero { display: flex; align-items: center; justify-content: center; gap: 28px; flex-wrap: wrap; margin-top: 8px; }
.gauge-wrap { filter: drop-shadow(0 4px 10px rgba(0,0,0,0.25)); }
.readiness-badge { color: white; font-size: 14px; font-weight: 700; padding: 8px 16px; border-radius: 8px; display: inline-block; }
.card { background: white; border-radius: 16px; padding: 26px; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(41,37,31,0.06); border: 1px solid #E7E2DA; }
h2 { font-size: 19px; font-weight: 800; border-left: 4px solid #28A89E; padding-left: 12px; margin-top: 0; margin-bottom: 8px; letter-spacing: -0.01em; color: #29251F; }
h3 { font-size: 15px; font-weight: 700; margin: 0; color: #29251F; }
h4 { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #79726A; margin: 18px 0 10px; }
p { font-size: 13.5px; line-height: 1.65; }
.section-intro { color: #5C554C; margin-bottom: 16px; }
.chart-box { position: relative; margin: 0 auto; }
.badge { color: white; font-size: 10.5px; font-weight: 700; padding: 4px 10px; border-radius: 20px; display: inline-block; white-space: nowrap; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }
.kpi-card { background: #FAF8F5; border: 1px solid #E7E2DA; border-radius: 14px; padding: 16px; transition: transform .15s ease, box-shadow .15s ease; }
.kpi-card:hover { transform: translateY(-3px); box-shadow: 0 10px 24px rgba(41,37,31,0.10); border-color: #D8D1C4; }
.kpi-icon { width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center; margin-bottom: 10px; }
.kpi-icon svg { width: 17px; height: 17px; }
.kpi-label { font-size: 11px; color: #79726A; text-transform: uppercase; letter-spacing: 0.03em; font-weight: 700; }
.kpi-score { font-size: 26px; font-weight: 800; margin: 4px 0; letter-spacing: -0.02em; color: #29251F; }
.kpi-bar-track { background: #E7E2DA; border-radius: 6px; height: 6px; overflow: hidden; }
.kpi-bar-fill { height: 100%; border-radius: 6px; }
.kpi-meta { font-size: 10px; color: #A29A8D; margin-top: 6px; }
.stat-card { display: flex; align-items: center; gap: 14px; background: #FAF8F5; border: 1px solid #E7E2DA; border-radius: 14px; padding: 16px; flex: 1; transition: box-shadow .15s ease; }
.stat-card:hover { box-shadow: 0 10px 24px rgba(41,37,31,0.08); }
.stat-card-icon { width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.stat-card-icon svg { width: 20px; height: 20px; }
.stat-card-num { font-size: 22px; font-weight: 800; color: #29251F; }
.stat-card-label { font-size: 12px; color: #79726A; max-width: 240px; }
.check-card { border: 1px solid #E7E2DA; border-radius: 12px; padding: 4px 18px 18px; margin-bottom: 12px; }
.check-header { display: flex; justify-content: space-between; align-items: center; cursor: pointer; list-style: none; padding: 14px 0; }
.check-header::-webkit-details-marker { display: none; }
.check-stats { display: flex; gap: 28px; margin: 12px 0; }
.stat-num { display: block; font-size: 22px; font-weight: 700; color: #29251F; }
.stat-label { font-size: 11px; color: #79726A; }
.sample-list { font-size: 12.5px; color: #5C554C; padding-left: 18px; }
.evidence-cell { font-size: 12px; color: #5C554C; }
.empty-state { text-align: center; color: #A29A8D; padding: 18px !important; font-style: italic; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { background: #29251F; color: #EDE9E2; text-align: left; padding: 10px 10px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; }
td { padding: 9px 10px; border-bottom: 1px solid #E7E2DA; color: #3F3A34; }
table.striped tr:nth-child(even) td { background: #FAF8F5; }
table.striped tr:hover td { background: #EAF7F5; }
.role-pill { background: #E7E2DA; color: #4A4339; padding: 2px 9px; border-radius: 12px; font-size: 10.5px; font-weight: 600; }
.findings-critical { color: #DC2626; }
.findings-positive { color: #1F8A7F; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
@media (max-width: 800px) { .two-col, .grid-3 { grid-template-columns: 1fr; } }
.nav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.nav-sticky { position: sticky; top: 0; z-index: 20; background: rgba(255,255,255,0.92); backdrop-filter: blur(6px); border-bottom: 1px solid #E7E2DA; padding: 10px 24px; display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.nav a, .nav-sticky a { font-size: 11.5px; color: #1F8A7F; text-decoration: none; background: #EAF7F5; padding: 6px 12px; border-radius: 20px; font-weight: 600; }
.nav a:hover, .nav-sticky a:hover { background: #D5F0EC; }
.alert { border-radius: 10px; padding: 12px 16px; font-size: 12.5px; text-align: left; max-width: 760px; margin: 14px auto 0; }
.alert-warning { background: #FDF1DB; border: 1px solid #E8A33D; color: #8A5A16; }
.print-btn { position: fixed; top: 16px; right: 16px; background: #1F8A7F; color: white; border: none; padding: 10px 18px; border-radius: 10px; font-weight: 700; cursor: pointer; box-shadow: 0 4px 12px rgba(31,138,127,0.35); z-index: 30; }
.footer-note { text-align: center; font-size: 11px; color: #A29A8D; padding: 20px 0 40px; }
@media print { .print-btn, .nav, .nav-sticky { display: none; } }
"""


def _ml_readiness_html(ml: dict[str, Any] | None) -> str:
    """Render Phase 2 M3 ML readiness card from report_data['ml_readiness']."""
    if not ml:
        return ""

    temporal = ml.get("temporal") or {}
    interval = ml.get("interval") or {}
    target = ml.get("target") or {}
    leakage = ml.get("leakage") or {}
    blockers = ml.get("blockers") or []
    warnings = ml.get("warnings") or []
    recommendations = ml.get("recommendations") or []
    verdict = ml.get("verdict", "NOT_READY")
    overall = ml.get("overall_score")

    blockers_html = "".join(f"<li>{b}</li>" for b in blockers) or "<li>None</li>"
    warnings_html = "".join(f"<li>{w}</li>" for w in warnings) or "<li>None</li>"
    recs_html = "".join(f"<li>{r}</li>" for r in recommendations) or "<li>None</li>"

    return f"""
  <div class="card" id="ml-readiness">
    <h2>ML Model Readiness Assessment</h2>
    <p class="section-intro">Prophet/forecasting preconditions — separate from the Data Quality Score above.
    Enabled when both a target column and a date column are supplied at upload time.</p>
    <p style="margin-top:8px"><b>Verdict:</b> {verdict} &middot; <b>Overall score:</b> {overall if overall is not None else 'N/A'} / 100</p>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Temporal</div><div class="kpi-score">{temporal.get('score', '—')}</div></div>
      <div class="kpi-card"><div class="kpi-label">Interval</div><div class="kpi-score">{interval.get('score', '—')}</div></div>
      <div class="kpi-card"><div class="kpi-label">Target</div><div class="kpi-score">{target.get('score', '—')}</div></div>
      <div class="kpi-card"><div class="kpi-label">Leakage</div><div class="kpi-score">{leakage.get('score', '—')}</div></div>
    </div>
    <div class="two-col" style="margin-top:16px">
      <div>
        <h4>Temporal Sufficiency {temporal.get('status', '')}</h4>
        <p>Observations: {temporal.get('observations', '—')} (min {temporal.get('minimum_observations', '—')})</p>
        <p>Date range: {temporal.get('date_range_days', '—')} days</p>
        <p>Seasonal cycles: {temporal.get('seasonal_cycles_detected', '—')}</p>
        <p>{temporal.get('conclusion', '')}</p>
      </div>
      <div>
        <h4>Interval Regularity {interval.get('status', '')}</h4>
        <p>Frequency: {interval.get('frequency', '—')}</p>
        <p>Missing intervals: {interval.get('missing_intervals', '—')}</p>
        <p>Duplicate timestamps: {interval.get('duplicate_timestamps', '—')}</p>
        <p>{interval.get('conclusion', '')}</p>
      </div>
    </div>
    <div class="two-col" style="margin-top:16px">
      <div>
        <h4>Target Integrity ({target.get('column', '—')}) {target.get('status', '')}</h4>
        <p>Null %: {target.get('null_pct', '—')} &middot; Zero %: {target.get('zero_pct', '—')}</p>
        <p>Variance: {target.get('variance', '—')} &middot; Outliers: {target.get('outlier_pct', '—')}%</p>
        <p>{target.get('conclusion', '')}</p>
      </div>
      <div>
        <h4>Leakage &amp; Cardinality {leakage.get('status', '')}</h4>
        <p>Perfect correlation features: {leakage.get('perfect_correlation_features', '—')}</p>
        <p>High cardinality features: {leakage.get('high_cardinality_features', '—')}</p>
        <p>{leakage.get('conclusion', '')}</p>
      </div>
    </div>
    <div class="grid-3" style="margin-top:16px">
      <div><h4>Blockers</h4><ul>{blockers_html}</ul></div>
      <div><h4>Warnings</h4><ul>{warnings_html}</ul></div>
      <div><h4>Recommendations</h4><ul>{recs_html}</ul></div>
    </div>
  </div>
"""


def generate_html_report(report_data: dict[str, Any], output_path: str) -> str:
    d = report_data
    meta, ov, sc = d["meta"], d["overview"], d["score"]

    role_cards = "".join(
        f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-score">{ov["role_counts"].get(role, 0)}</div></div>'
        for role, label in _ROLE_LABELS.items()
    )

    kpi_cards = "".join(_kpi_card(dim, info) for dim, info in sc["dimension_scores"].items())

    # ---- Chart.js data: dimension radar + issue severity doughnut ----
    dim_labels = [k.replace("_", " ").title() for k in sc["dimension_scores"].keys()]
    dim_scores = [
        v.get("score") if v.get("available") else None for v in sc["dimension_scores"].values()
    ]
    dim_scores_json = json.dumps(dim_scores)

    severity_counts: dict[str, int] = {}
    for s in d["checks"].values():
        severity_counts[s["severity"]] = severity_counts.get(s["severity"], 0) + 1
    sev_order = ["Critical", "High", "Medium", "Low", "None"]
    sev_labels = [s for s in sev_order if severity_counts.get(s)]
    sev_data = [severity_counts[s] for s in sev_labels]
    sev_colors = [_SEVERITY_COLORS[s] for s in sev_labels]

    passed_rules = sum(1 for s in d["checks"].values() if s["severity"] == "None")
    total_rules = len(d["checks"])

    pr = d["privacy_risk"]
    pr_html = ""
    pr_chart_js = ""
    privacy_weight = float(
        sc.get("dimension_scores", {}).get("privacy_sensitivity", {}).get("weight", 0.10)
    )
    if pr:
        color = _RISK_COLORS.get(pr.get("risk_level", "none"), "#97907F")
        pii_cols = d["pii"]["columns_with_pii"]
        total_cols = d["pii"]["total_columns"] or 1
        clean_cols = max(total_cols - pii_cols, 0)
        pii_types = d["pii"].get("types_found") or pr.get("pii_types_found") or []
        pr_html = f"""
        <div class="card" id="privacy-risk">
          <h2>Sensitive Data &amp; Privacy Risk <span style="font-weight:400;font-size:12.5px;color:#79726A">(included in composite via privacy_sensitivity, {privacy_weight:.0%} weight)</span></h2>
          <div class="two-col">
            <div>
              {_badge(pr.get('risk_level', 'none').upper(), color)}
              <p style="margin-top:12px">Columns with sensitive data: <b>{pii_cols} / {total_cols}</b></p>
              <p>Distinct rows with sensitive data: <b>{d['pii']['total_rows_with_pii']:,}</b></p>
              <p>Types found: {', '.join(pii_types) or 'none'}</p>
              <p>Flagged columns: {', '.join(d['pii'].get('flagged_columns') or []) or 'none'}</p>
            </div>
            <div class="chart-box" style="max-width:220px"><canvas id="prChart"></canvas></div>
          </div>
        </div>
        """
        pr_chart_js = f"""
        new Chart(document.getElementById('prChart'), {{
          type: 'doughnut',
          data: {{
            labels: ['Sensitive Columns', 'Clean Columns'],
            datasets: [{{ data: [{pii_cols}, {clean_cols}], backgroundColor: ['{color}', '#E7E2DA'], borderWidth: 0 }}]
          }},
          options: {{ plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }}, cutout: '65%' }}
        }});
        """

    critical_html = "".join(f"<li>{f}</li>" for f in d["executive_summary"]["critical_findings"]) or "<li>None identified.</li>"
    positive_html = "".join(f"<li>{f}</li>" for f in d["executive_summary"]["positive_findings"]) or "<li>See the Rules section below.</li>"

    # ---- Consultant-style narrative summary (deterministic, template-based) ----
    n_critical = sum(1 for f in d["executive_summary"]["critical_findings"])
    readiness_word = {
        "Ready": "is production-ready with strong overall quality",
        "Ready with Minor Cleaning": "is close to production-ready, needing only minor remediation",
        "Ready with Moderate Cleaning": "requires moderate remediation before it should be relied on for reporting",
        "Not Recommended": "carries significant quality risk and is not recommended for use until remediated",
    }.get(sc["readiness"], "requires further review")
    narrative = (
        f"This dataset {readiness_word}. Overall Data Quality Score is "
        f"{('%.1f' % sc['overall']) if sc['overall'] is not None else 'not available'} "
        f"({sc['rating']}), based on {total_rules} automated business rule(s), of which {passed_rules} passed "
        f"without exception. {n_critical} finding(s) require attention before this data is used for "
        f"downstream analytics or decision-making; see Priority Remediation Actions below."
    )

    rule_rows = "".join(_rule_row(name, s) for name, s in d["checks"].items())
    check_cards = "".join(_check_card(name, s) for name, s in d["checks"].items())
    nav_links = "".join(
        f'<a href="#check-{name}">{_ENTERPRISE_LABEL.get(name, s["display_name"])}</a>' for name, s in d["checks"].items()
    )

    matrix_rows = "".join(_matrix_row(r) for r in d["column_matrix"][:60])
    issue_rows = "".join(_issue_row(i, issue) for i, issue in enumerate(d["top_issues"], 1))

    duplicate_html = _duplicate_section(d.get("duplicate_analysis", {}))
    column_intel_html = _column_intelligence_section(d.get("column_intelligence", []))

    fuzzy = d["fuzzy"]
    pii = d["pii"]
    er = d.get("entity_resolution") or {}
    er_html = ""
    if er.get("enabled"):
        es = er.get("summary") or {}
        er_html = f"""
  <div class="card" id="entity-resolution">
    <h2>Entity Resolution (M6)</h2>
    <p class="section-intro">Three-tier cascade: canonical lookup &rarr; RapidFuzz &rarr; semantic fallback.
    Original values are never overwritten automatically.</p>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Auto-matched</div><div class="kpi-score">{es.get('auto_match', 0)}</div></div>
      <div class="kpi-card"><div class="kpi-label">Needs review</div><div class="kpi-score">{es.get('review', 0)}</div></div>
      <div class="kpi-card"><div class="kpi-label">No match</div><div class="kpi-score">{es.get('no_match', 0)}</div></div>
    </div>
  </div>
"""

    ml_html = _ml_readiness_html(d.get("ml_readiness"))

    rules_html = f"""
  <div class="card" id="rules">
    <h2>Rules</h2>
    <p class="section-intro">This report works by running a fixed set of automated quality rules against your file.
    Each rule checks one aspect of your data — completeness, duplicates, freshness, sensitive data, and more.
    Start here to see what was checked, which rules passed or failed, and expand any row below for full detail
    (including the Inspect button for a plain-language explanation).</p>
    <table class="striped">
      <tr><th>Rule</th><th>Status</th><th>Severity</th><th>Affected Records</th><th>Business Impact</th><th>Recommendation</th></tr>
      {rule_rows}
    </table>
    <div class="nav" style="margin-top:22px">{nav_links}</div>
    {check_cards}
  </div>
"""

    readiness_color = _READINESS_COLORS.get(sc["readiness"], "#97907F")
    rating_color = _RATING_COLORS.get(sc["rating"], "#97907F")
    hipaa_in_report = "hipaa_phi" in d["checks"]
    compliance = sc.get("compliance_adjusted")
    overall = sc.get("overall")
    compliance_note = ""
    if (
        hipaa_in_report
        and compliance is not None
        and overall is not None
        and float(compliance) < float(overall) - 0.05
    ):
        compliance_note = (
            f'<div style="margin-top:10px;font-size:13px;color:#C9E8E4;line-height:1.5">'
            f"Compliance-adjusted score (HIPAA PHI): "
            f'<b style="color:white">{float(compliance):.1f}</b> / 100</div>'
        )

    scoring_formula_note = (
        "Composite = weighted average across scorable dimensions (including "
        "privacy_sensitivity for PII). HIPAA exposure may apply a proportional "
        "ceiling when PHI is detected. Weights re-normalized when a dimension "
        "has no results."
        if hipaa_in_report
        else "Composite = weighted average across scorable dimensions (including "
        "privacy_sensitivity for PII). Weights re-normalized when a dimension "
        "has no results."
    )
    hipaa_methodology_note = ""
    if hipaa_in_report:
        hipaa_methodology_note = """
    <p><b>HIPAA ceiling (hybrid):</b> When M9 PHI exposure is detected, the headline
    score is capped at the <b>stricter</b> of (a) a proportional ceiling from
    exposure_score, or (b) a severity floor (high&rarr;70, medium&rarr;74,
    low&rarr;89). The weighted <code>privacy_sensitivity</code> dimension (10%)
    still applies in the raw composite first — stewards see column-level PII
    impact in the breakdown; the cap ensures compliance-grade PHI cannot yield
    a near-perfect headline score.</p>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Quality Report -- {meta['filename']}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">Print Report</button>

<div class="hero">
  <div class="hero-brand">Data Quality Platform</div>
  <div class="hero-icon">{_icon("bar-chart")}</div>
  <h1>Enterprise Data Quality Assessment</h1>
  <div class="subtitle">{meta['filename']} &middot; Sheet: {meta['sheet_name']}</div>
  {_sheet_disclosure_banner(d.get('sheet_disclosure') or {}, meta['sheet_name'])}
  <div class="score-hero">
    {_score_ring(sc['overall'])}
    <div style="text-align:left">
      {_badge(sc['rating'], rating_color)}<br><br>
      <span class="readiness-badge" style="background:{readiness_color}">{sc['readiness']}</span>
      {compliance_note}
    </div>
  </div>
  <div class="hero-meta">
    <div>Assessment Date: {meta['generated_at']}</div>
    <div>Engine Version: {meta['engine_version']}</div>
    <div>Processing Time: {meta['processing_time_seconds']}s</div>
    <div>Rows Analyzed: {ov['rows']:,}</div>
    <div>Columns Analyzed: {ov['columns']}</div>
    <div>Business Rules Executed: {total_rules}</div>
  </div>
</div>

<div class="nav-sticky">
  <a href="#exec-summary">Executive Summary</a>
  <a href="#rules">Rules</a>
  <a href="#quality-score">Quality Score</a>
  <a href="#duplicate-analysis">Duplicate Assessment</a>
  <a href="#column-intelligence">Column Intelligence</a>
  <a href="#column-matrix">Column Matrix</a>
  <a href="#priority-actions">Priority Actions</a>
</div>

<div class="container">

  <div class="card" id="exec-summary">
    <h2>Executive Summary</h2>
    <p class="section-intro">{narrative}</p>
    <div class="two-col">
      <div>
        <h4 class="findings-critical">Critical Findings</h4>
        <ul>{critical_html}</ul>
      </div>
      <div>
        <h4 class="findings-positive">Positive Findings</h4>
        <ul>{positive_html}</ul>
      </div>
    </div>
  </div>

  {rules_html}

  <div class="card" id="quality-score">
    <h2>Quality Score Breakdown</h2>
    <div class="two-col" style="align-items:center">
      <div class="chart-box" style="max-width:100%;height:300px"><canvas id="dimChart"></canvas></div>
      <div class="chart-box" style="max-width:100%;height:300px"><canvas id="sevChart"></canvas></div>
    </div>
    <h4>Dimension Detail</h4>
    <div class="kpi-grid">{kpi_cards}</div>
  </div>

  {duplicate_html}

  {column_intel_html}

  {pr_html}

  {er_html}

  {ml_html}

  <div class="card">
    <h2>Dataset Profile</h2>
    <p><b>Header Row:</b> {ov['header_row']} &middot; <b>Business Rules Executed:</b> {ov['checks_executed']}</p>
    <h4>Column Classification</h4>
    <div class="kpi-grid">{role_cards}</div>
  </div>

  <div class="card">
    <h2>Sensitive Data &amp; Standardization Summary</h2>
    <div class="two-col">
      <div>
        <p><b>Sensitive (PII) columns:</b> {pii['columns_with_pii']} / {pii['total_columns']}</p>
        <p><b>Rows with sensitive data:</b> {pii['total_rows_with_pii']:,}</p>
        <p><b>Types found:</b> {', '.join(pii['types_found']) or 'none'}</p>
        <p><b>Flagged columns:</b> {', '.join(pii.get('flagged_columns') or []) or 'none'}</p>
      </div>
      <div>
        <p><b>Columns standardized:</b> {fuzzy['columns_with_remaps']}</p>
        <p><b>Rows remapped:</b> {fuzzy['total_remap_rows']}</p>
        <p><b>Flagged columns:</b> {', '.join(fuzzy['flagged_columns']) or 'none'}</p>
      </div>
    </div>
  </div>

  <div class="card" id="column-matrix">
    <h2>Column Quality Matrix</h2>
    <table class="striped">
      <tr><th>Column</th><th>Role</th><th>Score</th><th>Issues</th><th>Severity</th><th>Recommendation</th></tr>
      {matrix_rows}
    </table>
  </div>

  <div class="card" id="priority-actions">
    <h2>Priority Remediation Actions</h2>
    <table class="striped">
      <tr><th>#</th><th>Quality Exception</th><th>Column</th><th>Severity</th><th>Business Impact</th><th>Recommended Action</th></tr>
      {issue_rows}
    </table>
  </div>

  <div class="card">
    <h2>Appendix &mdash; Methodology</h2>
    <p><b>Scoring formula:</b> Each dimension score = 100 &times; (passed checks / total non-error checks).
    {scoring_formula_note}</p>
    <p><b>Quality dimensions:</b> {', '.join(f"{k.replace('_', ' ').title()} ({v.get('weight', 0):.0%})" for k, v in sc['dimension_scores'].items())}</p>
    {hipaa_methodology_note}
    <p><b>Severity badges:</b> Critical / High / Medium / Low use the same proportional
    thresholds as dimension scoring. &ge;50% impact = Critical, &ge;20% = High,
    &ge;5% = Medium, &gt;0% = Low.</p>
    <p><b>Business-key detection:</b> a column is only treated as an intended-unique identifier when its name
    pattern, uniqueness ratio, repeated-value frequency, and classified role together clear a configurable
    confidence threshold -- never from column name alone.</p>
  </div>

  <div class="footer-note">Generated by the Data Quality Engine &middot; {meta['engine_version']} &middot; {meta['generated_at']}</div>

</div>

<script>
new Chart(document.getElementById('dimChart'), {{
  type: 'radar',
  data: {{
    labels: {dim_labels!r},
    datasets: [{{
      label: 'Score',
      data: {dim_scores_json},
      backgroundColor: 'rgba(37,99,235,0.15)',
      borderColor: '#1F8A7F',
      pointBackgroundColor: '#1F8A7F',
      borderWidth: 2,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'Quality Dimension Radar', font: {{ size: 12 }} }} }},
    scales: {{ r: {{ min: 0, max: 100, ticks: {{ stepSize: 25, backdropColor: 'transparent' }}, grid: {{ color: '#E7E2DA' }} }} }}
  }}
}});
new Chart(document.getElementById('sevChart'), {{
  type: 'doughnut',
  data: {{
    labels: {sev_labels!r},
    datasets: [{{ data: {sev_data!r}, backgroundColor: {sev_colors!r}, borderWidth: 0 }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '62%',
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      title: {{ display: true, text: 'Issue Severity Distribution', font: {{ size: 12 }} }} }}
  }}
}});
{pr_chart_js}
</script>
</body>
</html>"""

    output_path = str(output_path)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
