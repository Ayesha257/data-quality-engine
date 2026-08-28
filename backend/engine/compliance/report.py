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

import hashlib
import html as html_lib
import json
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
    modules: dict[str, list[CheckResult]] | None = None,
    gemini_api_key: str | None = None,
    regulation: str = "HIPAA",
    df: Any | None = None,
    resolved_findings: list[dict[str, Any]] | None = None,
    confidence_tiers: dict[str, list[dict[str, Any]]] | None = None,
    prompt: Any | None = None,
    resolved_decisions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    reg_upper = (regulation or "HIPAA").upper().replace("-", "_")

    meta = {
        "filename": Path(filepath).name if filepath else "",
        "sheet_name": sheet_name or "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    overview = {
        "rows": row_count,
        "columns": column_count,
    }

    # -----------------------------------------------------------------------
    # HIPAA branch: completely unchanged behavior
    # -----------------------------------------------------------------------
    if reg_upper == "HIPAA":
        from backend.engine.reports.report_generator import _summarize_check
        from backend.engine.ai_explanation.ai_explainer import explain_check
        from backend.engine.pii.detect_pii import detect_pii_in_series
        from backend.engine.compliance.scanner import assess_hipaa_compliance_as_check_results

        mod_dict = dict(modules or {})
        if "hipaa_phi" not in mod_dict and df is not None:
            pii_summary = {str(c): detect_pii_in_series(df[c]) for c in df.columns}
            hipaa_results = assess_hipaa_compliance_as_check_results(pii_summary, len(df))
            mod_dict["hipaa_phi"] = hipaa_results

        sections = {}
        ai_explanations = {}
        for name, results in mod_dict.items():
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
            "regulation": "HIPAA",
        }

    # -----------------------------------------------------------------------
    # Multi-regulation branch: PCI_DSS / GLBA / SOX
    # -----------------------------------------------------------------------
    from backend.compliance.financial_compliance import run_compliance_scan

    disclaimer = (
        f"This report flags compliance-relevant data patterns. "
        f"It does not certify legal compliance with {regulation}."
    )

    high_findings: list[dict[str, Any]] = []
    medium_findings: list[dict[str, Any]] = []
    confirmed_findings: list[dict[str, Any]] = []

    if confidence_tiers is not None:
        high_findings = confidence_tiers.get("High Confidence", [])
        medium_findings = confidence_tiers.get("Medium Confidence", [])
        confirmed_findings = confidence_tiers.get("Confirmed (User-Verified)", [])
    elif resolved_findings is not None:
        for f in resolved_findings:
            conf = str(f.get("confidence", "")).lower()
            status = str(f.get("status", "")).lower()
            if conf == "high":
                high_findings.append(f)
            elif conf == "medium":
                medium_findings.append(f)
            elif conf in ("confirmed", "low") and (status == "confirmed" or f.get("confirmed") is True):
                confirmed_findings.append(f)
    elif df is not None:
        scan_res = run_compliance_scan(
            df,
            regulation=regulation,
            prompt=prompt,
            resolved_decisions=resolved_decisions,
        )
        tiers = scan_res.get("confidence_tiers", {})
        high_findings = tiers.get("High Confidence", [])
        medium_findings = tiers.get("Medium Confidence", [])
        confirmed_findings = tiers.get("Confirmed (User-Verified)", [])

    all_resolved = [*high_findings, *medium_findings, *confirmed_findings]

    return {
        "meta": meta,
        "overview": overview,
        "regulation": regulation,
        "disclaimer": disclaimer,
        "confidence_tiers": {
            "High Confidence": high_findings,
            "Medium Confidence": medium_findings,
            "Confirmed (User-Verified)": confirmed_findings,
        },
        "findings": all_resolved,
        "sections": {
            "high_confidence": high_findings,
            "medium_confidence": medium_findings,
            "confirmed_findings": confirmed_findings,
        },
        "gemini_api_key": gemini_api_key,
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


def _finding_inspect_id(f: dict[str, Any], tier_name: str) -> str:
    raw = f"{f.get('rule')}|{f.get('column_name')}|{tier_name}|{f.get('field_name')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _financial_finding_to_check_summary(
    f: dict[str, Any], regulation: str, tier_name: str
) -> dict[str, Any]:
    """Shape a financial finding for the shared explain_check() path."""
    col = str(f.get("column_name") or "")
    rule = str(f.get("rule") or f.get("field_name") or "compliance_finding")
    samples = f.get("masked_samples") or f.get("samples") or []
    return {
        "check_name": rule,
        "display_name": f.get("display_name") or rule,
        "severity": "High" if "High" in tier_name else "Medium" if "Medium" in tier_name else "Low",
        "columns_checked": 1,
        "columns_with_issues": 1,
        "total_issues_found": f.get("issues_found") or 1,
        "affected_columns": [col] if col else [],
        "sample_findings": [{"column": col, "issues_found": f.get("issues_found") or 1}],
        "business_impact": (
            f.get("description")
            or f"{regulation} flagged column '{col}' at {tier_name} confidence."
        ),
        "recommendation": (
            "Review this column against your handling policy for this regulation "
            "and restrict, mask, or remove it if it should not be in this file."
        ),
        "regulation": regulation,
        "confidence_tier": tier_name,
        "masked_samples": samples,
    }


def _non_hipaa_finding_card(f: dict[str, Any], tier_name: str, inspect_id: str) -> str:
    title = html_lib.escape(str(f.get("display_name") or f.get("field_name") or f.get("rule", "Compliance Finding")))
    col = html_lib.escape(str(f.get("column_name", "-")))
    issues = f.get("issues_found", 0)
    desc = f.get("description", "")
    badge_label = tier_name
    badge_color = "#EF4444" if "High" in tier_name else "#F59E0B" if "Medium" in tier_name else "#3FD1C6"

    samples_html = ""
    if f.get("masked_samples"):
        items = "".join(f"<li><code>{s}</code></li>" for s in f["masked_samples"])
        samples_html = f"<p><b>Masked Sample Values:</b></p><ul class='sample-list'>{items}</ul>"
    elif f.get("samples"):
        items = "".join(f"<li><code>{s}</code></li>" for s in f["samples"])
        samples_html = f"<p><b>Sample Values:</b></p><ul class='sample-list'>{items}</ul>"
    elif f.get("matched_categories"):
        cats = ", ".join(f["matched_categories"])
        samples_html = f"<p><b>Audit Categories Matched:</b> {cats}</p>"

    return f"""
    <div class="check-card" style="margin-bottom:14px; background:#131C2E; border:1px solid #1E293B; border-radius:10px; padding:16px;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; gap:12px;">
        <h3 style="margin:0; font-size:1.05rem; color:#F1F5F9;">{title}</h3>
        <div style="display:flex; align-items:center; gap:8px;">
          {_badge(badge_label, badge_color)}
          <button type="button" class="inspect-compliance-btn" onclick="openComplianceFindingModal('{inspect_id}')">
            Inspect
          </button>
        </div>
      </div>
      <p style="margin:4px 0; color:#CBD5E1; font-size:0.9rem;"><b>Flagged Column:</b> <code>{col}</code></p>
      {f'<p style="margin:4px 0; color:#94A3B8; font-size:0.85rem;">{desc}</p>' if desc else ''}
      {samples_html}
    </div>
    """


def _generate_non_hipaa_html_report(report_data: dict[str, Any], output_path: str) -> str:
    d = report_data
    meta, ov = d["meta"], d["overview"]
    regulation = d.get("regulation", "Compliance")
    disclaimer = d.get(
        "disclaimer",
        f"This report flags compliance-relevant data patterns. It does not certify legal compliance with {regulation}."
    )
    confidence_tiers: dict[str, list[dict[str, Any]]] = d.get("confidence_tiers") or {
        "High Confidence": d.get("sections", {}).get("high_confidence", []),
        "Medium Confidence": d.get("sections", {}).get("medium_confidence", []),
        "Confirmed (User-Verified)": d.get("sections", {}).get("confirmed_findings", []),
    }

    total_findings = sum(len(items) for items in confidence_tiers.values())

    from backend.engine.ai_explanation.ai_explainer import explain_check

    finding_explanations: dict[str, str] = {}
    gemini_key = d.get("gemini_api_key")

    tier_sections_html = ""
    for tier_name, findings in confidence_tiers.items():
        cards_html = ""
        if findings:
            parts = []
            for f in findings:
                fid = _finding_inspect_id(f, tier_name)
                summary = _financial_finding_to_check_summary(f, str(regulation), tier_name)
                try:
                    exp = explain_check(summary["check_name"], summary, api_key=gemini_key)
                    exp_dict = exp.to_dict() if hasattr(exp, "to_dict") else exp
                    _, grid_html, _ = _format_ai_explanation_html(fid, exp_dict)
                    finding_explanations[fid] = grid_html
                except Exception:
                    finding_explanations[fid] = (
                        "<div><span style='color:#CBD5E1;'>Explanation unavailable.</span></div>"
                    )
                parts.append(_non_hipaa_finding_card(f, tier_name, fid))
            cards_html = "".join(parts)
        else:
            cards_html = "<div style='color:#64748B; font-style:italic; padding:8px 0;'>No findings in this category.</div>"

        tier_sections_html += f"""
        <div style="margin-bottom:24px;">
          <h3 style="color:#F1F5F9; border-bottom:1px solid #1E293B; padding-bottom:8px; margin-bottom:12px; font-size:1.1rem; display:flex; align-items:center; gap:8px;">
            <span>{tier_name}</span>
            <span style="font-size:0.8rem; background:#1E293B; color:#94A3B8; padding:2px 8px; border-radius:12px;">{len(findings)}</span>
          </h3>
          {cards_html}
        </div>
        """

    explanations_json = json.dumps(finding_explanations)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{regulation} Compliance Report -- {meta['filename']}</title>
<style>{_CSS}{_COMPLIANCE_CSS_EXTRA}</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">Download PDF</button>

<div class="hero">
  <div class="hero-brand">Data Quality Platform</div>
  <div class="hero-icon">{_icon("shield-check")}</div>
  <h1>{regulation} Compliance Report</h1>
  <div class="subtitle">{meta['filename']} &middot; Sheet: {meta['sheet_name']}</div>
  <div class="hero-meta">
    <div>Generated: {meta['generated_at']}</div>
    <div>Rows Analyzed: {ov['rows']:,}</div>
    <div>Columns Analyzed: {ov['columns']}</div>
    <div>Resolved Findings: {total_findings}</div>
  </div>
</div>

<div class="container">
  <!-- Mandatory Non-HIPAA Legal Disclaimer -->
  <div style="background:#1E293B; border-left:4px solid #F59E0B; padding:14px 18px; border-radius:8px; margin-bottom:20px; color:#F1F5F9; font-size:0.92rem; line-height:1.5;">
    <strong style="color:#F59E0B; margin-right:6px;">DISCLAIMER:</strong>
    {disclaimer}
  </div>

  <div class="card">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
      <h2 style="margin:0;">Resolved Compliance Findings</h2>
      <span style="font-size:0.85rem; color:#94A3B8;">Grouped by Confidence Tier</span>
    </div>
    <p class="section-intro" style="margin-bottom:20px;">
      This report contains all resolved compliance findings for <b>{regulation}</b>, including high/medium confidence detections and user-confirmed review items. Rejected and pending unverified items are omitted. Click <b>Inspect</b> on a finding for a plain-language explanation.
    </p>
    {tier_sections_html}
  </div>
</div>

<div id="complianceAiModalOverlay" class="ai-modal-overlay">
  <div class="ai-modal-container">
    <div class="ai-modal-header">
      <div class="ai-title-wrap">
        <svg class="ai-sparkle-icon" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#3FD1C6" stroke-width="2">
          <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z"/>
        </svg>
        <div>
          <div class="ai-card-title" style="font-size:1.3rem;">AI Executive Compliance Insights</div>
          <div style="font-size:0.85rem; color:#94A3B8; margin-top:2px;">What is wrong, why it matters, and what to do — {html_lib.escape(str(regulation))}</div>
        </div>
      </div>
      <button class="ai-modal-close" onclick="closeComplianceFindingModal()" title="Close">&times;</button>
    </div>
    <div id="complianceAiModalBody">
      <div class="ai-grid" id="complianceFindingModalGrid"></div>
    </div>
    <div class="ai-modal-footer">
      <button class="ai-btn-secondary" onclick="window.print()">Print Insights</button>
      <button class="ai-btn-secondary" onclick="closeComplianceFindingModal()">Close</button>
    </div>
  </div>
</div>

<script>
var FINDING_EXPLANATIONS = {explanations_json};
function openComplianceFindingModal(id) {{
  var grid = document.getElementById('complianceFindingModalGrid');
  if (grid) grid.innerHTML = FINDING_EXPLANATIONS[id] || '<div><span style="color:#CBD5E1;">No explanation for this finding.</span></div>';
  var overlay = document.getElementById('complianceAiModalOverlay');
  if (overlay) overlay.classList.add('open');
}}
function closeComplianceFindingModal() {{
  var overlay = document.getElementById('complianceAiModalOverlay');
  if (overlay) overlay.classList.remove('open');
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeComplianceFindingModal();
}});
</script>

<div class="footer-note">{regulation} Compliance Report &middot; Data Quality Platform</div>
</body>
</html>
"""
    output_path = str(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def generate_compliance_html_report(report_data: dict[str, Any], output_path: str) -> str:
    """
    Renders the standalone Compliance Report to a single self-contained
    HTML file. Supports HIPAA, PCI_DSS, GLBA, and SOX frameworks.
    """
    reg = str(report_data.get("regulation", "HIPAA")).upper().replace("-", "_")
    if reg != "HIPAA":
        return _generate_non_hipaa_html_report(report_data, output_path)

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
