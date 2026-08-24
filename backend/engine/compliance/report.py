"""
Standalone "Compliance Report" -- separate from the main Phase 2 report.

This module produces ONE report dedicated to compliance findings only. It
never re-runs or duplicates any compliance analysis: it consumes the exact
same `CheckResult` lists the main report's HIPAA section already consumes
(see main.py's `_print_hipaa_compliance_results` /
`assess_hipaa_compliance_as_check_results`) and only reshapes/renders them.

Extensibility: `build_compliance_report_data()` takes a `modules` mapping of
check_name -> list[CheckResult]. Today that mapping only ever has one entry
("hipaa_phi"). Adding a second regulation later (e.g. GDPR) means adding a
second entry to that mapping -- the caller still gets back ONE compliance
report with an additional section, never a second report file.

Presentation only, same spirit as engine/reporting/report_generator.py and
engine/reporting/html_report.py: no scoring, no detection logic lives here.
Visual style (colors, fonts, card layout) is intentionally shared with the
main report via html_report.py's `_CSS`, `_icon`, `_badge`, `_check_card` so
this reads as the same product, just a different page.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from backend.engine.models import CheckResult
from backend.engine.reports.html_report import (
    _CSS,
    _badge,
    _icon,
    _SEVERITY_COLORS,
)

COMPLIANCE_MODULE_LABELS: dict[str, str] = {
    "hipaa_phi": "HIPAA PHI Compliance Scan",
    "gdpr_pii": "GDPR PII Compliance Scan",
}


def _format_ai_explanation_html(name: str, exp: dict[str, Any]) -> tuple[str, str, str]:
    text = exp.get("text", "") if isinstance(exp, dict) else getattr(exp, "text", "")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    grid_items = []
    for line in lines:
        if ":" in line:
            heading, _, body = line.partition(":")
            grid_items.append(
                f"<div><strong style='color:#3FD1C6; display:block; margin-bottom:6px;'>{heading.strip()}</strong>"
                f"<span style='color:#CBD5E1; line-height:1.5;'>{body.strip()}</span></div>"
            )
        else:
            grid_items.append(
                f"<div><span style='color:#CBD5E1; line-height:1.5;'>{line}</span></div>"
            )
    if not grid_items:
        grid_items.append(
            "<div><span style='color:#CBD5E1;'>No detailed AI insights available.</span></div>"
        )
    grid_html = "\n".join(grid_items)
    return "", grid_html, ""


def build_compliance_report_data(
    filepath: str,
    sheet_name: str,
    row_count: int,
    column_count: int,
    modules: dict[str, list[CheckResult]],
    gemini_api_key: str | None = None,
) -> dict[str, Any]:
    from backend.engine.reports.report_generator import _summarize_check
    from backend.engine.ai_explanation.ai_explainer import explain_check

    meta = {
        "filename": Path(filepath).name if filepath else "",
        "sheet_name": sheet_name or "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    overview = {
        "rows": row_count,
        "columns": column_count,
    }
    sections = {}
    ai_explanations = {}
    for name, results in (modules or {}).items():
        summary = _summarize_check(name, results)
        sections[name] = summary
        try:
            exp = explain_check(name, summary, api_key=gemini_api_key)
            ai_explanations[name] = exp.to_dict() if hasattr(exp, "to_dict") else exp
        except Exception:
            pass

    return {
        "meta": meta,
        "overview": overview,
        "sections": sections,
        "ai_explanations": ai_explanations,
    }

_COMPLIANCE_CSS_EXTRA = """
.inspect-compliance-btn {
  background: linear-gradient(135deg, #3FD1C6, #2BB8AD);
  color: #0B1120;
  border: none;
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.inspect-compliance-btn:hover { opacity: 0.9; }

/* --- Modal: hidden by default, shown only via .open --- */
.ai-modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.55);
  z-index: 9999;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.ai-modal-overlay.open { display: flex; }

.ai-modal-container {
  background: #0F172A;
  border: 1px solid #1E293B;
  border-radius: 16px;
  max-width: 900px;
  width: 100%;
  max-height: 85vh;
  overflow-y: auto;
  padding: 24px;
}
.ai-modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.ai-title-wrap { display: flex; align-items: flex-start; gap: 12px; }
.ai-card-title { color: #F1F5F9; font-weight: 700; }
.ai-modal-close {
  background: none;
  border: none;
  color: #94A3B8;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  padding: 4px 8px;
}
.ai-modal-close:hover { color: #F1F5F9; }

.ai-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 640px) {
  .ai-grid { grid-template-columns: 1fr; }
}
.ai-grid > div {
  background: #131C2E;
  border: 1px solid #1E293B;
  border-radius: 12px;
  padding: 18px;
}

.ai-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #1E293B;
}
.ai-btn-secondary {
  background: #1E293B;
  color: #E2E8F0;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 8px 16px;
  font-size: 0.85rem;
  cursor: pointer;
}
.ai-btn-secondary:hover { background: #263449; }

.compliance-empty {
  text-align: center;
  color: #94A3B8;
  padding: 32px;
}
"""

def _compliance_check_card(name: str, summary: dict[str, Any]) -> str:
    color = _SEVERITY_COLORS.get(summary["severity"], "#97907F")
    cols = ", ".join(summary["affected_columns"][:10]) or "None"
    label = COMPLIANCE_MODULE_LABELS.get(name, summary["display_name"])
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
    elif summary.get("sample_findings"):
        rows = ""
        for f in summary["sample_findings"]:
            col = f.get("column", "-")
            issues = f.get("issues_found", "-")
            extra = {k: v for k, v in f.items() if k not in ("column", "issues_found")}
            extra_str = "; ".join(f"{k}={v}" for k, v in extra.items())
            rows += f"<li><b>{col}</b>: {issues} issue(s) {extra_str}</li>"
        samples_html = f"<ul class='sample-list'>{rows}</ul>"

    return f"""
    <details class="check-card" id="check-{name}" open>
      <summary class="check-header" style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
        <div style="display:flex; align-items:center; gap:12px;">
          <h3 style="margin:0;">{label}</h3>
          {_badge(summary['severity'], color)}
        </div>
        <button type="button" class="inspect-compliance-btn" onclick="event.stopPropagation(); openComplianceAiModal('{name}')">
          Inspect
        </button>
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


def generate_compliance_html_report(report_data: dict[str, Any], output_path: str) -> str:
    """
    Renders the standalone Compliance Report to a single self-contained
    HTML file with an Inspect button on each compliance finding section.
    """
    d = report_data
    meta, ov = d["meta"], d["overview"]
    sections: dict[str, dict[str, Any]] = d.get("sections") or {}
    ai_explanations: dict[str, dict[str, Any]] = d.get("ai_explanations") or {}

    nav_links = "".join(
        f'<a href="#check-{name}">{COMPLIANCE_MODULE_LABELS.get(name, s["display_name"])}</a>'
        for name, s in sections.items()
    )

    modal_grid_content = ""
    for name in sections:
        if name in ai_explanations:
            _, grid_html, _ = _format_ai_explanation_html(name, ai_explanations[name])
            if not modal_grid_content:
                modal_grid_content = grid_html

    if sections:
        section_cards = "".join(_compliance_check_card(name, s) for name, s in sections.items())
    else:
        section_cards = (
            '<div class="card compliance-empty">'
            "No compliance modules were assessed for this run."
            "</div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compliance Report -- {meta['filename']}</title>
<style>{_CSS}{_COMPLIANCE_CSS_EXTRA}</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">Download PDF</button>

<div class="hero">
  <div class="hero-brand">Data Quality Platform</div>
  <div class="hero-icon">{_icon("shield-check")}</div>
  <h1>Compliance Report</h1>
  <div class="subtitle">{meta['filename']} &middot; Sheet: {meta['sheet_name']}</div>
  <div class="hero-meta">
    <div>Generated: {meta['generated_at']}</div>
    <div>Rows Analyzed: {ov['rows']:,}</div>
    <div>Columns Analyzed: {ov['columns']}</div>
    <div>Modules Assessed: {len(sections) or 0}</div>
  </div>
</div>

<div class="nav-sticky">
  {nav_links}
</div>

<div class="container">
  <div class="card">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
      <h2 style="margin:0;">Compliance Findings</h2>
    </div>
    <p class="section-intro">
      This report is scoped to regulatory compliance findings -- click <b>Inspect</b> on any finding below to view AI explanations, regulatory risks, and remediation steps.
    </p>
    {section_cards}
  </div>
</div>

<!-- Pro Website AI Compliance Modal -->
<div id="complianceAiModalOverlay" class="ai-modal-overlay">
  <div class="ai-modal-container">
    <div class="ai-modal-header">
      <div class="ai-title-wrap">
        <svg class="ai-sparkle-icon" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#3FD1C6" stroke-width="2">
          <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z"/>
        </svg>
        <div>
          <div class="ai-card-title" style="font-size:1.3rem;">AI Executive Compliance Insights</div>
          <div style="font-size:0.85rem; color:#94A3B8; margin-top:2px;">Regulatory risk breakdown, HIPAA Safe Harbor rules & remediation guidance</div>
        </div>
      </div>
      <button class="ai-modal-close" onclick="closeComplianceAiModal()" title="Close">&times;</button>
    </div>
    
    <div id="complianceAiModalBody">
      <div class="ai-grid">
        {modal_grid_content}
      </div>
    </div>

    <div class="ai-modal-footer">
      <button class="ai-btn-secondary" onclick="window.print()">Print Insights</button>
      <button class="ai-btn-secondary" onclick="closeComplianceAiModal()">Close</button>
    </div>
  </div>
</div>

<script>
function openComplianceAiModal() {{
  var overlay = document.getElementById('complianceAiModalOverlay');
  if (overlay) overlay.classList.add('open');
}}
function closeComplianceAiModal() {{
  var overlay = document.getElementById('complianceAiModalOverlay');
  if (overlay) overlay.classList.remove('open');
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeComplianceAiModal();
}});
</script>

<div class="footer-note">Compliance Report &middot; Data Quality Platform</div>
</body>
</html>
"""
    output_path = str(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path
