"""Renders build_report_data()'s output into an Enterprise Data Quality HTML Report.

Includes:
- Executive Risk Summary & Score Breakdown
- Business Rule Validation Summary Table (Rule, Status, Failed Records, Severity, Business Impact, Recommendation)
- Issue Severity Distribution (CRITICAL, HIGH, MEDIUM, LOW)
- Priority Fix Roadmap
- Dynamic Business Recommendations across all checks
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SEVERITY_COLORS = {
    "CRITICAL": "#B91C1C",
    "HIGH": "#D97706",
    "MEDIUM": "#CA8A04",
    "LOW": "#65A30D",
    "NONE": "#16A34A",
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
            f'<span style="color:#B91C1C"><b>Not analyzed in any report:</b> {", ".join(not_covered)}.</span>'
        )
    if hidden:
        parts.append(f"Hidden sheet(s) skipped: {', '.join(hidden)}.")

    return (
        '<div style="max-width:700px;margin:12px auto 0;padding:10px 16px;'
        'background:#FEF3C7;border:1px solid #F59E0B;border-radius:8px;'
        'font-size:13px;color:#92400E;text-align:left">'
        + " ".join(parts)
        + "</div>"
    )


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
      <td><b>{row['column']}</b></td>
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
      <td><b>{issue['issue']}</b></td>
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
.hero { background: linear-gradient(135deg, #0F172A, #1E293B); color: white; padding: 48px 24px; text-align: center; }
.hero-icon { width: 56px; height: 56px; line-height: 56px; border-radius: 50%; background: rgba(255,255,255,0.12);
  border: 2px solid rgba(255,255,255,0.35); font-size: 26px; color: #4ADE80; margin: 0 auto 16px; }
.hero h1 { margin: 0 0 8px; font-size: 30px; font-weight: 800; letter-spacing: -0.01em; }
.hero .subtitle { color: #CBD5E1; font-size: 14px; margin-bottom: 24px; }
.hero-meta { display: flex; justify-content: center; gap: 32px; margin-top: 24px; font-size: 12px; color: #94A3B8; flex-wrap: wrap; text-transform: uppercase; letter-spacing: 0.04em; }
.card { background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border: 1px solid #E2E8F0; }
h2 { font-size: 20px; font-weight: 700; border-left: 4px solid #2563EB; padding-left: 12px; margin-top: 0; margin-bottom: 18px; letter-spacing: -0.01em; }
h3 { font-size: 16px; font-weight: 700; }
p { font-size: 13.5px; line-height: 1.6; }
.badge { color: white; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; display: inline-block; }
.readiness-badge { color: white; font-size: 15px; font-weight: 700; padding: 8px 18px; border-radius: 8px; display: inline-block; }
.sev-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
.sev-card { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px; text-align: center; }
.sev-val { font-size: 24px; font-weight: 800; margin-top: 4px; }
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
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 700px) { .two-col, .sev-grid { grid-template-columns: 1fr; } }
.score-hero { display: flex; align-items: center; gap: 32px; flex-wrap: wrap; }
.nav { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.nav a { font-size: 12px; color: #2563EB; text-decoration: none; background: #EFF6FF; padding: 6px 12px; border-radius: 20px; }
.print-btn { position: fixed; top: 16px; right: 16px; background: #2563EB; color: white; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; }
@media print { .print-btn, .nav { display: none; } }
"""


def generate_html_report(report_data: dict[str, Any], output_path: str) -> str:
    d = report_data
    meta, ov, sc = d["meta"], d["overview"], d["score"]

    kpi_cards = "".join(_kpi_card(dim, info) for dim, info in sc["dimension_scores"].items())

    dim_labels = [k.replace("_", " ").title() for k in sc["dimension_scores"].keys()]
    dim_scores = [
        (v.get("score") if v.get("available") else 0) for v in sc["dimension_scores"].values()
    ]
    dim_colors = [
        "#16A34A" if (s or 0) >= 90 else "#65A30D" if (s or 0) >= 75 else "#CA8A04" if (s or 0) >= 55 else "#B91C1C"
        for s in dim_scores
    ]

    pr = d.get("privacy_risk")
    pr_html = ""
    pr_chart_js = ""
    if pr:
        color = _RISK_COLORS.get(pr.get("risk_level", "none"), "#6B7280")
        pii_cols = pr.get("columns_with_pii", 0)
        total_cols = pr.get("total_columns", 0) or 1
        clean_cols = max(total_cols - pii_cols, 0)
        pr_html = f"""
        <div class="card">
          <h2>Privacy Risk Assessment</h2>
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

    # Business Rule Validation Section Table
    br_rows = ""
    for br in d.get("business_rules", []):
        br_status_color = "#16A34A" if br["status"] == "PASSED" else "#B91C1C"
        br_rows += f"""
        <tr>
          <td><b>{br['rule_name']}</b></td>
          <td>{br['column']}</td>
          <td>{_badge(br['status'], br_status_color)}</td>
          <td>{br['failed_records']} ({br['failed_pct']:.1f}%)</td>
          <td>{_badge(br['severity'], br['severity_color'])}</td>
          <td>{br['business_impact']}</td>
          <td><b>{br['recommendation']}</b></td>
        </tr>
        """

    br_section = f"""
    <div class="card" id="business-rules">
      <h2>Business Rule Validation</h2>
      <p>Configurable domain rules evaluated from <code>business_rules.json</code>:</p>
      <table class="striped">
        <thead>
          <tr>
            <th>Rule</th>
            <th>Target Column</th>
            <th>Status</th>
            <th>Failed Records</th>
            <th>Severity</th>
            <th>Business Impact</th>
            <th>Actionable Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {br_rows if br_rows else "<tr><td colspan='7'>No active business rule violations found.</td></tr>"}
        </tbody>
      </table>
    </div>
    """

    # Issue Severity Distribution
    sev_dist = d.get("severity_distribution", {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
    sev_dist_html = f"""
    <div class="sev-grid">
      <div class="sev-card" style="border-top: 4px solid #B91C1C;">
        <div class="kpi-label">Critical Issues</div>
        <div class="sev-val" style="color: #B91C1C;">{sev_dist.get('CRITICAL', 0)}</div>
      </div>
      <div class="sev-card" style="border-top: 4px solid #D97706;">
        <div class="kpi-label">High Severity</div>
        <div class="sev-val" style="color: #D97706;">{sev_dist.get('HIGH', 0)}</div>
      </div>
      <div class="sev-card" style="border-top: 4px solid #CA8A04;">
        <div class="kpi-label">Medium Severity</div>
        <div class="sev-val" style="color: #CA8A04;">{sev_dist.get('MEDIUM', 0)}</div>
      </div>
      <div class="sev-card" style="border-top: 4px solid #65A30D;">
        <div class="kpi-label">Low Severity</div>
        <div class="sev-val" style="color: #65A30D;">{sev_dist.get('LOW', 0)}</div>
      </div>
    </div>
    """

    # Priority Roadmap
    roadmap_rows = ""
    for r in d.get("priority_roadmap", []):
        roadmap_rows += f"""
        <tr>
          <td><b>#{r['priority']}</b></td>
          <td><b>{r['column']}</b></td>
          <td>{r['issues']}</td>
          <td>{_badge(r['severity'], r['severity_color'])}</td>
          <td><b>{r['action']}</b></td>
        </tr>
        """

    roadmap_section = f"""
    <div class="card">
      <h2>Priority Fix Roadmap</h2>
      <p>Prioritized step-by-step remediation guide ranked by business impact:</p>
      <table class="striped">
        <thead>
          <tr>
            <th>Priority</th>
            <th>Column Target</th>
            <th>Detected Issue(s)</th>
            <th>Severity</th>
            <th>Recommended Action</th>
          </tr>
        </thead>
        <tbody>
          {roadmap_rows if roadmap_rows else "<tr><td colspan='5'>All scanned columns meet quality standards.</td></tr>"}
        </tbody>
      </table>
    </div>
    """

    check_cards = "".join(_check_card(s) for s in d["checks"].values())
    nav_links = "".join(
        f'<a href="#check-{name}">{s["display_name"]}</a>' for name, s in d["checks"].items()
    )

    matrix_rows = "".join(_matrix_row(r) for r in d["column_matrix"][:60])
    issue_rows = "".join(_issue_row(i, issue) for i, issue in enumerate(d["top_issues"], 1))

    readiness_color = _READINESS_COLORS.get(sc["readiness"], "#6B7280")
    rating_color = _RATING_COLORS.get(sc["rating"], "#6B7280")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Data Quality Report - {meta['filename']} ({meta['sheet_name']})</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>{_CSS}</style>
</head>
<body>

  <button class="print-btn" onclick="window.print()">Print / Export PDF</button>

  <div class="hero">
    <div class="hero-icon">&#10003;</div>
    <h1>Enterprise Data Quality Report</h1>
    <div class="subtitle">{meta['filename']} &middot; Sheet: <b>{meta['sheet_name']}</b></div>
    {_sheet_disclosure_banner(d.get('sheet_disclosure', {}), meta['sheet_name'])}
    <div class="hero-meta">
      <div>Rows: <b>{ov['rows']:,}</b></div>
      <div>Cols: <b>{ov['columns']}</b></div>
      <div>Header Row: <b>{ov['header_row']}</b></div>
      <div>Time: <b>{meta['processing_time_seconds']}s</b></div>
      <div>Engine: <b>{meta['engine_version']}</b></div>
    </div>
  </div>

  <div class="container">

    <div class="nav">
      <a href="#score">Score Breakdown</a>
      <a href="#business-rules">Business Rules</a>
      <a href="#checks">Quality Checks</a>
      <a href="#matrix">Column Matrix</a>
      <a href="#top-issues">Top Issues</a>
    </div>

    <!-- Executive Risk Summary Card -->
    <div class="card" id="score">
      <h2>Executive Risk Summary</h2>
      <div class="score-hero">
        {_score_ring(sc['overall'])}
        <div style="flex:1">
          <div style="font-size:12px;color:#64748B;text-transform:uppercase;font-weight:600">Composite Score & Rating</div>
          <div style="font-size:24px;font-weight:800;margin:4px 0">
            {sc['overall']:.1f} / 100 &middot; {_badge(sc['rating'], rating_color)}
          </div>
          <div style="margin-top:8px">
            Production Readiness: {_badge(sc['readiness'], readiness_color)}
          </div>
        </div>
      </div>

      <div style="margin-top:24px">
        <h4>Issue Severity Distribution</h4>
        {sev_dist_html}
      </div>

      <div style="margin-top:24px">
        <h4>Data Quality Dimension Score Breakdown</h4>
        <div class="kpi-grid">{kpi_cards}</div>
      </div>
    </div>

    {roadmap_section}

    {br_section}

    <!-- Executive Findings -->
    <div class="card">
      <h2>Executive Findings Overview</h2>
      <div class="two-col">
        <div>
          <h4 class="findings-critical">Critical Findings & Risk Alerts</h4>
          <ul class="sample-list">{critical_html}</ul>
        </div>
        <div>
          <h4 class="findings-positive">Passed Controls</h4>
          <ul class="sample-list">{positive_html}</ul>
        </div>
      </div>
    </div>

    {pr_html}

    <!-- Dimension Score Chart -->
    <div class="card">
      <h2>Dimension Quality Scores</h2>
      <div class="chart-box" style="max-width:750px;height:240px"><canvas id="dimChart"></canvas></div>
    </div>

    <!-- Detailed Check Cards -->
    <div class="card" id="checks">
      <h2>Detailed Quality Check Results ({ov['checks_executed']} Scanned)</h2>
      {check_cards}
    </div>

    <!-- Top Issues -->
    <div class="card" id="top-issues">
      <h2>Top Prioritized Issues</h2>
      <table class="striped">
        <thead>
          <tr>
            <th>#</th>
            <th>Check</th>
            <th>Column</th>
            <th>Severity</th>
            <th>Business Impact</th>
            <th>Dynamic Action Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {issue_rows if issue_rows else "<tr><td colspan='6'>No critical issues found.</td></tr>"}
        </tbody>
      </table>
    </div>

    <!-- Column Quality Matrix -->
    <div class="card" id="matrix">
      <h2>Column Quality & Classification Matrix ({len(d['column_matrix'])} Columns)</h2>
      <table class="striped">
        <thead>
          <tr>
            <th>Column</th>
            <th>Semantic Role</th>
            <th>Quality Score</th>
            <th>Total Issues</th>
            <th>Severity</th>
            <th>Dynamic Business Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {matrix_rows}
        </tbody>
      </table>
    </div>

  </div>

  <script>
    new Chart(document.getElementById('dimChart'), {{
      type: 'bar',
      data: {{
        labels: {dim_labels},
        datasets: [{{
          label: 'Score / 100',
          data: {dim_scores},
          backgroundColor: {dim_colors},
          borderRadius: 6
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{ y: {{ min: 0, max: 100 }} }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
    {pr_chart_js}
  </script>
</body>
</html>
"""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return str(p)