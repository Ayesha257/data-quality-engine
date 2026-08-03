"""Renders build_report_data()'s output to a single self-contained HTML page.

This is the "frontend page" -- no server, no framework, no build step.
Open the generated .html file directly in any browser. Inline CSS only,
so it also works as a standalone attachment/email preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SEVERITY_COLORS = {
    "Critical": "#B91C1C",
    "High": "#D97706",
    "Medium": "#CA8A04",
    "Low": "#65A30D",
    "None": "#16A34A",
}
_RATING_COLORS = {
    "Excellent": "#16A34A",
    "Good": "#65A30D",
    "Fair": "#CA8A04",
    "Poor": "#B91C1C",
    "Unrated": "#6B7280",
}
_READINESS_COLORS = {
    "Ready": "#16A34A",
    "Ready with Minor Cleaning": "#65A30D",
    "Ready with Moderate Cleaning": "#CA8A04",
    "Not Recommended": "#B91C1C",
}
_RISK_COLORS = {"high": "#B91C1C", "medium": "#CA8A04", "low": "#65A30D", "none": "#16A34A"}


def _badge(label: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{label}</span>'


def _score_ring(score: float | None, size: int = 120) -> str:
    if score is None:
        pct = 0
        display = "N/A"
    else:
        pct = max(0, min(100, score))
        display = f"{score:.1f}"
    circumference = 2 * 3.14159 * 52
    offset = circumference * (1 - pct / 100)
    color = "#16A34A" if pct >= 90 else "#65A30D" if pct >= 75 else "#CA8A04" if pct >= 55 else "#B91C1C"
    return f"""
    <svg width="{size}" height="{size}" viewBox="0 0 120 120">
      <circle cx="60" cy="60" r="52" fill="none" stroke="#E2E8F0" stroke-width="12"/>
      <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="12"
        stroke-dasharray="{circumference:.1f}" stroke-dashoffset="{offset:.1f}"
        stroke-linecap="round" transform="rotate(-90 60 60)"/>
      <text x="60" y="66" text-anchor="middle" font-size="26" font-weight="700" fill="#1E293B">{display}</text>
    </svg>
    """


def _kpi_card(label: str, score_info: dict[str, Any]) -> str:
    score = score_info.get("score")
    available = score_info.get("available", False)
    weight = score_info.get("weight", 0)
    bar_pct = score if (score is not None and available) else 0
    color = "#16A34A" if bar_pct >= 90 else "#65A30D" if bar_pct >= 75 else "#CA8A04" if bar_pct >= 55 else "#B91C1C"
    score_text = f"{score:.0f}" if (score is not None and available) else "N/A"
    status = "Assessed" if available else "Skipped"
    return f"""
    <div class="kpi-card">
      <div class="kpi-label">{label.replace('_', ' ').title()}</div>
      <div class="kpi-score">{score_text}</div>
      <div class="kpi-bar-track"><div class="kpi-bar-fill" style="width:{bar_pct}%;background:{color}"></div></div>
      <div class="kpi-meta">weight {weight:.0%} &middot; {status}</div>
    </div>
    """


def _check_card(summary: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(summary["severity"], "#6B7280")
    cols = ", ".join(summary["affected_columns"][:10]) or "None"
    samples_html = ""
    if summary["sample_findings"]:
        rows = ""
        for f in summary["sample_findings"]:
            col = f.get("column", "-")
            issues = f.get("issues_found", "-")
            extra = {k: v for k, v in f.items() if k not in ("column", "issues_found")}
            extra_str = "; ".join(f"{k}={v}" for k, v in extra.items())
            rows += f"<li><b>{col}</b>: {issues} issue(s) {extra_str}</li>"
        samples_html = f"<ul class='sample-list'>{rows}</ul>"
    return f"""
    <div class="check-card" id="check-{summary['check_name']}">
      <div class="check-header">
        <h3>{summary['display_name']}</h3>
        {_badge(summary['severity'], color)}
      </div>
      <div class="check-stats">
        <div><span class="stat-num">{summary['columns_checked']}</span><span class="stat-label">Checked</span></div>
        <div><span class="stat-num">{summary['columns_with_issues']}</span><span class="stat-label">With Issues</span></div>
        <div><span class="stat-num">{summary['total_issues_found']}</span><span class="stat-label">Total Issues</span></div>
      </div>
      <p><b>Affected Columns:</b> {cols}</p>
      <p><b>Business Impact:</b> {summary['business_impact']}</p>
      <p><b>Recommendation:</b> {summary['recommendation']}</p>
      {samples_html}
    </div>
    """


def _matrix_row(row: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(row["severity"], "#6B7280")
    return f"""
    <tr>
      <td>{row['column']}</td>
      <td><span class="role-pill">{row['role']}</span></td>
      <td>{row['quality_score']:.0f}</td>
      <td>{row['issues_found']}</td>
      <td>{_badge(row['severity'], color)}</td>
      <td>{row['recommendation']}</td>
    </tr>
    """


def _issue_row(i: int, issue: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(issue["severity"], "#6B7280")
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


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; background: #F8FAFC; color: #1E293B; }
.container { max-width: 1100px; margin: 0 auto; padding: 24px; }
.hero { background: linear-gradient(135deg, #1E293B, #334155); color: white; padding: 48px 24px; text-align: center; }
.hero-icon { width: 56px; height: 56px; line-height: 56px; border-radius: 50%; background: rgba(255,255,255,0.12);
  border: 2px solid rgba(255,255,255,0.35); font-size: 26px; color: #4ADE80; margin: 0 auto 16px; }
.hero h1 { margin: 0 0 8px; font-size: 30px; font-weight: 800; letter-spacing: -0.01em; }
.hero .subtitle { color: #CBD5E1; font-size: 14px; margin-bottom: 24px; }
.hero-meta { display: flex; justify-content: center; gap: 32px; margin-top: 24px; font-size: 12px; color: #94A3B8; flex-wrap: wrap; text-transform: uppercase; letter-spacing: 0.04em; }
.card { background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border: 1px solid #E2E8F0; }
h2 { font-size: 20px; font-weight: 700; border-left: 4px solid #2563EB; padding-left: 12px; margin-top: 0; margin-bottom: 18px; letter-spacing: -0.01em; }
h3 { font-size: 16px; font-weight: 700; }
h4 { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #64748B; margin-bottom: 10px; }
p { font-size: 13.5px; line-height: 1.6; }
.chart-box { position: relative; margin: 0 auto; }
.badge { color: white; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; display: inline-block; }
.readiness-badge { color: white; font-size: 15px; font-weight: 700; padding: 8px 18px; border-radius: 8px; display: inline-block; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; }
.kpi-card { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px; }
.kpi-label { font-size: 11px; color: #64748B; text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600; }
.kpi-score { font-size: 28px; font-weight: 800; margin: 4px 0; letter-spacing: -0.02em; }
.kpi-bar-track { background: #E2E8F0; border-radius: 6px; height: 6px; overflow: hidden; }
.kpi-bar-fill { height: 100%; border-radius: 6px; }
.kpi-meta { font-size: 10px; color: #94A3B8; margin-top: 6px; }
.check-card { border: 1px solid #E2E8F0; border-radius: 10px; padding: 18px; margin-bottom: 14px; }
.check-header { display: flex; justify-content: space-between; align-items: center; }
.check-header h3 { margin: 0; font-size: 16px; }
.check-stats { display: flex; gap: 28px; margin: 12px 0; }
.stat-num { display: block; font-size: 22px; font-weight: 700; }
.stat-label { font-size: 11px; color: #64748B; }
.sample-list { font-size: 12.5px; color: #475569; padding-left: 18px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #1E293B; color: white; text-align: left; padding: 8px 10px; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.03em; }
td { padding: 8px 10px; border-bottom: 1px solid #E2E8F0; }
table.striped tr:nth-child(even) td { background: #F8FAFC; }
table.striped tr:hover td { background: #EFF6FF; }
.role-pill { background: #E2E8F0; color: #334155; padding: 2px 8px; border-radius: 12px; font-size: 11px; }
.findings-critical { color: #B91C1C; }
.findings-positive { color: #16A34A; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }
.score-hero { display: flex; align-items: center; gap: 32px; flex-wrap: wrap; }
.nav { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.nav a { font-size: 12px; color: #2563EB; text-decoration: none; background: #EFF6FF; padding: 6px 12px; border-radius: 20px; }
.print-btn { position: fixed; top: 16px; right: 16px; background: #2563EB; color: white; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; }
@media print { .print-btn, .nav { display: none; } }
"""


def generate_html_report(report_data: dict[str, Any], output_path: str) -> str:
    d = report_data
    meta, ov, sc = d["meta"], d["overview"], d["score"]

    role_labels = {
        "measurement": "Measurement",
        "identifier": "Identifier",
        "pii": "PII",
        "free_text": "Free Text",
        "categorical": "Categorical",
        "date": "Date",
    }
    role_cards = "".join(
        f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-score">{ov["role_counts"].get(role, 0)}</div></div>'
        for role, label in role_labels.items()
    )

    kpi_cards = "".join(_kpi_card(dim, info) for dim, info in sc["dimension_scores"].items())

    # ---- Chart.js data for the dimension bar chart ----
    dim_labels = [k.replace("_", " ").title() for k in sc["dimension_scores"].keys()]
    dim_scores = [
        (v.get("score") if v.get("available") else 0) for v in sc["dimension_scores"].values()
    ]
    dim_colors = [
        "#16A34A" if (s or 0) >= 90 else "#65A30D" if (s or 0) >= 75 else "#CA8A04" if (s or 0) >= 55 else "#B91C1C"
        for s in dim_scores
    ]

    pr = d["privacy_risk"]
    pr_html = ""
    pr_chart_js = ""
    if pr:
        color = _RISK_COLORS.get(pr.get("risk_level", "none"), "#6B7280")
        pii_cols = pr.get("columns_with_pii", 0)
        total_cols = pr.get("total_columns", 0) or 1
        clean_cols = max(total_cols - pii_cols, 0)
        pr_html = f"""
        <div class="card">
          <h2>Privacy Risk <span style="font-weight:400;font-size:13px;color:#64748B">(separate -- never part of the score above)</span></h2>
          <div class="two-col">
            <div>
              {_badge(pr.get('risk_level', 'none').upper(), color)}
              <p style="margin-top:12px">Columns with PII: <b>{pii_cols} / {total_cols}</b></p>
              <p>Types found: {', '.join(pr.get('pii_types_found', [])) or 'none'}</p>
            </div>
            <div class="chart-box" style="max-width:220px"><canvas id="prChart"></canvas></div>
          </div>
        </div>
        """
        pr_chart_js = f"""
        new Chart(document.getElementById('prChart'), {{
          type: 'doughnut',
          data: {{
            labels: ['Columns with PII', 'Clean Columns'],
            datasets: [{{ data: [{pii_cols}, {clean_cols}], backgroundColor: ['{color}', '#E2E8F0'], borderWidth: 0 }}]
          }},
          options: {{ plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }}, cutout: '65%' }}
        }});
        """

    critical_html = "".join(f"<li>{f}</li>" for f in d["executive_summary"]["critical_findings"]) or "<li>None identified.</li>"
    positive_html = "".join(f"<li>{f}</li>" for f in d["executive_summary"]["positive_findings"]) or "<li>See check sections below.</li>"

    check_cards = "".join(_check_card(s) for s in d["checks"].values())
    nav_links = "".join(
        f'<a href="#check-{name}">{s["display_name"]}</a>' for name, s in d["checks"].items()
    )

    matrix_rows = "".join(_matrix_row(r) for r in d["column_matrix"][:60])
    issue_rows = "".join(_issue_row(i, issue) for i, issue in enumerate(d["top_issues"], 1))

    fuzzy = d["fuzzy"]
    pii = d["pii"]

    readiness_color = _READINESS_COLORS.get(sc["readiness"], "#6B7280")
    rating_color = _RATING_COLORS.get(sc["rating"], "#6B7280")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Data Quality Report -- {meta['filename']}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">Download / Print PDF</button>

<div class="hero">
  <div class="hero-icon">&#10003;</div>
  <h1>Data Quality Assessment Report</h1>
  <div class="subtitle">{meta['filename']} &middot; Sheet: {meta['sheet_name']}</div>
  <div class="score-hero" style="justify-content:center">
    {_score_ring(sc['overall'])}
    <div style="text-align:left">
      {_badge(sc['rating'], rating_color)}<br><br>
      <span class="readiness-badge" style="background:{readiness_color}">{sc['readiness']}</span>
    </div>
  </div>
  <div class="hero-meta">
    <div>Processing Date: {meta['generated_at']}</div>
    <div>Engine Version: {meta['engine_version']}</div>
    <div>Execution Time: {meta['processing_time_seconds']}s</div>
    <div>Rows: {ov['rows']:,} &middot; Columns: {ov['columns']}</div>
  </div>
</div>

<div class="container">

  <div class="card">
    <h2>Executive Summary</h2>
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

  <div class="card">
    <h2>Data Quality Dashboard</h2>
    <div class="chart-box" style="max-width:100%;height:280px;margin-bottom:20px"><canvas id="dimChart"></canvas></div>
    <div class="kpi-grid">{kpi_cards}</div>
  </div>

  {pr_html}

  <div class="card">
    <h2>Dataset Overview</h2>
    <p><b>Header Row:</b> {ov['header_row']} &middot; <b>Checks Executed:</b> {ov['checks_executed']}</p>
    <h4>Column Classification</h4>
    <div class="kpi-grid">{role_cards}</div>
  </div>

  <div class="card">
    <h2>PII &amp; Fuzzy Standardization Summary</h2>
    <div class="two-col">
      <div>
        <p><b>PII columns:</b> {pii['columns_with_pii']} / {pii['total_columns']}</p>
        <p><b>Rows with PII:</b> {pii['total_rows_with_pii']:,}</p>
        <p><b>Types found:</b> {', '.join(pii['types_found']) or 'none'}</p>
      </div>
      <div>
        <p><b>Columns with fuzzy remaps:</b> {fuzzy['columns_with_remaps']}</p>
        <p><b>Rows remapped:</b> {fuzzy['total_remap_rows']}</p>
        <p><b>Flagged columns:</b> {', '.join(fuzzy['flagged_columns']) or 'none'}</p>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Quality Checks</h2>
    <div class="nav">{nav_links}</div>
    {check_cards}
  </div>

  <div class="card">
    <h2>Column Quality Matrix</h2>
    <table class="striped">
      <tr><th>Column</th><th>Role</th><th>Score</th><th>Issues</th><th>Severity</th><th>Recommendation</th></tr>
      {matrix_rows}
    </table>
  </div>

  <div class="card">
    <h2>Top Critical Issues</h2>
    <table class="striped">
      <tr><th>#</th><th>Issue</th><th>Column</th><th>Severity</th><th>Impact</th><th>Action</th></tr>
      {issue_rows}
    </table>
  </div>

  <div class="card">
    <h2>Appendix</h2>
    <p><b>Scoring formula:</b> Each dimension score = 100 &times; (passed checks / total non-error checks).
    Composite = weighted average across scorable dimensions only, weights re-normalized when a dimension
    has no results. Privacy Risk is calculated and reported separately -- never subtracted from the
    composite score.</p>
    <p><b>Quality dimensions:</b> {', '.join(f"{k.replace('_', ' ').title()} ({v.get('weight', 0):.0%})" for k, v in sc['dimension_scores'].items())}</p>
  </div>

</div>

<script>
new Chart(document.getElementById('dimChart'), {{
  type: 'bar',
  data: {{
    labels: {dim_labels!r},
    datasets: [{{
      label: 'Score',
      data: {dim_scores!r},
      backgroundColor: {dim_colors!r},
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ min: 0, max: 100, grid: {{ color: '#F1F5F9' }} }}, y: {{ grid: {{ display: false }} }} }}
  }}
}});
{pr_chart_js}
</script>
</body>
</html>"""

    output_path = str(output_path)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path