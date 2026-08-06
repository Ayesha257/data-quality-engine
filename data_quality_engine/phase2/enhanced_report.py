"""
Phase 2 — "Inspect" button (M2 AI Explanation Layer, UI half).

This module NEVER modifies `engine/reporting/html_report.py`. Instead it:

  1. Calls Phase 1's own `generate_html_report()` unchanged, to produce the
     exact same report Phase 1 has always produced.
  2. Reads that HTML back in.
  3. Generates AI (or, on any failure, rule-based fallback) explanations
     for every check via `ai_explainer.generate_explanations()`.
  4. Surgically injects an "Inspect" button into each check card and a
     small modal + JS at the end of the page. The injected JS reads the
     explanations from a small embedded JSON blob -- no server, no live
     API calls from the browser, and therefore no API key is ever exposed
     in the HTML file.

Because step 1 always runs and always succeeds independently of the AI
layer, the worst case if Gemini is completely unreachable is: every
Inspect button shows a clearly-labeled rule-based explanation instead of
an AI one. The report itself -- scores, tables, charts, everything Phase 1
computed -- is always present and unaffected.

Works on any dataset: everything here operates on the generic
`report_data` dict produced by `report_generator.build_report_data()`,
never on dataset-specific values.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from data_quality_engine.engine.reporting.html_report import generate_html_report
from data_quality_engine.phase2 import ai_explainer

_INSPECT_CSS = """
.inspect-btn{
  margin-left:auto;background:#EFF6FF;color:#1D4ED8;border:1px solid #BFDBFE;
  border-radius:6px;padding:5px 12px;font-size:12.5px;font-weight:600;
  cursor:pointer;white-space:nowrap;
}
.inspect-btn:hover{background:#DBEAFE}
.inspect-btn[data-source="fallback"]{background:#F8FAFC;color:#475569;border-color:#E2E8F0}
.ai-modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);
  z-index:9999;align-items:center;justify-content:center;padding:24px;
}
.ai-modal-overlay.open{display:flex}
.ai-modal{
  background:#fff;border-radius:12px;max-width:640px;width:100%;
  max-height:80vh;overflow-y:auto;padding:24px 28px;box-shadow:0 20px 60px rgba(0,0,0,.3);
}
.ai-modal h3{margin:0 0 4px 0;font-size:18px}
.ai-modal .ai-badge{
  display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;
  padding:3px 8px;border-radius:999px;margin-bottom:18px;text-transform:uppercase;
}
.ai-modal .ai-badge.ai{background:#ECFDF5;color:#047857}
.ai-modal .ai-badge.fallback{background:#F1F5F9;color:#475569}
.ai-modal .ai-close{
  float:right;border:none;background:none;font-size:20px;cursor:pointer;color:#64748B;
}
.ai-modal .ai-note{margin-top:16px;font-size:12px;color:#94A3B8}
.ai-point{
  display:flex;gap:12px;margin-bottom:16px;align-items:flex-start;
}
.ai-point:last-of-type{margin-bottom:0}
.ai-point-icon{
  flex:0 0 auto;width:26px;height:26px;border-radius:8px;
  display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:700;margin-top:1px;
}
.ai-point-icon.whats-wrong{background:#FEF2F2;color:#DC2626}
.ai-point-icon.why-it-matters{background:#FFFBEB;color:#D97706}
.ai-point-icon.what-to-do{background:#ECFDF5;color:#059669}
.ai-point-body strong{
  display:block;font-size:12px;font-weight:700;letter-spacing:.02em;
  text-transform:uppercase;color:#334155;margin-bottom:3px;
}
.ai-point-body span{line-height:1.55;color:#1E293B;font-size:14.5px}
.ai-modal .ai-fallback-text{line-height:1.6;color:#1E293B;white-space:pre-wrap}
"""

_INSPECT_JS_TEMPLATE = """
<div class="ai-modal-overlay" id="aiModalOverlay" onclick="if(event.target===this) closeAiModal()">
  <div class="ai-modal">
    <button class="ai-close" onclick="closeAiModal()">&times;</button>
    <h3 id="aiModalTitle">Inspect</h3>
    <span class="ai-badge" id="aiModalBadge">AI</span>
    <div id="aiModalBody"></div>
    <div class="ai-note" id="aiModalNote"></div>
  </div>
</div>
<script>
window.__AI_EXPLANATIONS__ = __EXPLANATIONS_JSON__;

function escapeHtml(str){
  var d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

var AI_POINT_LABELS = [
  {key: "WHAT'S WRONG", icon: "!", cls: "whats-wrong"},
  {key: "WHY IT MATTERS", icon: "?", cls: "why-it-matters"},
  {key: "WHAT TO DO", icon: "\\u2713", cls: "what-to-do"}
];

function renderExplanationBody(rawText){
  var lines = rawText.split(/\\n+/).map(function(l){ return l.trim(); }).filter(Boolean);
  var points = [];
  for (var i = 0; i < lines.length; i++) {
    var matched = null;
    for (var j = 0; j < AI_POINT_LABELS.length; j++) {
      var label = AI_POINT_LABELS[j];
      if (lines[i].toUpperCase().indexOf(label.key) === 0) {
        var content = lines[i].slice(lines[i].indexOf(':') + 1).trim();
        matched = {label: label, content: content};
        break;
      }
    }
    if (matched) points.push(matched);
  }

  if (points.length === 0) {
    // Text didn't follow the expected structure (e.g. an unusual AI
    // response) -- fall back to showing it as plain text so nothing
    // is ever hidden from the user.
    return '<p class="ai-fallback-text">' + escapeHtml(rawText) + '</p>';
  }

  var html = '';
  points.forEach(function(p){
    html += '<div class="ai-point">'
      + '<div class="ai-point-icon ' + p.label.cls + '">' + p.label.icon + '</div>'
      + '<div class="ai-point-body"><strong>' + escapeHtml(p.label.key) + '</strong>'
      + '<span>' + escapeHtml(p.content) + '</span></div>'
      + '</div>';
  });
  return html;
}

function openAiModal(checkName, label){
  var data = window.__AI_EXPLANATIONS__[checkName];
  var overlay = document.getElementById('aiModalOverlay');
  var title = document.getElementById('aiModalTitle');
  var badge = document.getElementById('aiModalBadge');
  var body = document.getElementById('aiModalBody');
  var note = document.getElementById('aiModalNote');
  title.textContent = 'Inspect: ' + label;
  if (data) {
    body.innerHTML = renderExplanationBody(data.text);
    if (data.ai_available) {
      badge.textContent = 'AI Explanation';
      badge.className = 'ai-badge ai';
      note.textContent = '';
    } else {
      badge.textContent = 'Rule-based explanation';
      badge.className = 'ai-badge fallback';
      note.textContent = 'AI explanation is temporarily unavailable, so this is the deterministic Phase 1 explanation instead.';
    }
  } else {
    badge.textContent = 'Unavailable';
    badge.className = 'ai-badge fallback';
    body.innerHTML = '<p class="ai-fallback-text">No explanation is available for this item.</p>';
    note.textContent = '';
  }
  overlay.classList.add('open');
}
function closeAiModal(){
  document.getElementById('aiModalOverlay').classList.remove('open');
}
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape') closeAiModal();
});
</script>
"""


def _inspect_button_html(check_name: str, label: str, source: str) -> str:
    safe_label = label.replace("'", "\\'")
    return (
        f'<button type="button" class="inspect-btn" data-check="{check_name}" '
        f'data-source="{source}" onclick="event.preventDefault(); event.stopPropagation(); '
        f"openAiModal('{check_name}', '{safe_label}')\">&#128269; Inspect</button>"
    )


def _inject_inspect_buttons(html: str, report_data: dict[str, Any], explanations: dict[str, dict]) -> str:
    """Insert one Inspect button into each `<details class="check-card"
    id="check-{name}">` block's summary line, using the exact structure
    `html_report._check_card()` always produces. If a check name isn't
    found in the HTML (e.g. a future Phase 1 change renames the id), that
    single button is simply skipped -- the rest of the report, and every
    other Inspect button, is unaffected.
    """
    checks = report_data.get("checks", {}) or {}
    for name, summary in checks.items():
        label = summary.get("display_name", name)
        exp = explanations.get(name, {})
        source = "ai" if exp.get("ai_available") else "fallback"
        button = _inspect_button_html(name, label, source)
        pattern = re.compile(
            r'(id="check-' + re.escape(name) + r'">\s*<summary class="check-header">.*?)(</summary>)',
            re.DOTALL,
        )
        html, n = pattern.subn(lambda m: m.group(1) + button + m.group(2), html, count=1)
        # If the id/summary shape isn't found, we silently skip this one
        # button rather than raising -- the report must still render.
    return html


def _inject_overall_inspect(html: str, explanations: dict[str, dict]) -> str:
    """Add one Inspect button next to the executive summary heading, if
    that section exists in the rendered HTML."""
    overall = explanations.get("__overall__")
    if not overall:
        return html
    source = "ai" if overall.get("ai_available") else "fallback"
    button = _inspect_button_html("__overall__", "Executive Summary", source)
    pattern = re.compile(r"(<h2[^>]*>\s*Executive Summary\s*</h2>)", re.IGNORECASE)
    html, _ = pattern.subn(lambda m: m.group(1) + button, html, count=1)
    return html


def _inject_modal_and_css(html: str, explanations: dict[str, dict]) -> str:
    # Escape "</" so an AI explanation that happens to contain the literal
    # text "</script>" (e.g. quoting something odd from the source data)
    # can never break out of the embedded <script> block.
    exp_json = json.dumps(explanations).replace("</", "<\\/")
    modal_block = _INSPECT_JS_TEMPLATE.replace("__EXPLANATIONS_JSON__", exp_json)
    html = html.replace("</style>", _INSPECT_CSS + "\n</style>", 1)
    html = html.replace("</body>", modal_block + "\n</body>", 1)
    return html


def generate_ai_enhanced_html_report(
    report_data: dict[str, Any],
    output_path: str,
    *,
    api_key: str | None = None,
    model: str = ai_explainer.DEFAULT_MODEL,
    timeout: float = ai_explainer.DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Produce an AI-enhanced copy of the standard Phase 1 HTML report.

    Guarantees, in order of priority:
      1. This always produces a complete, valid Data Quality Report --
         identical in content to Phase 1's own report -- even if every
         single AI call fails.
      2. If the AI layer is fully or partially available, each check gets
         an "Inspect" button that opens a plain-language AI explanation.
      3. Any explanation Gemini couldn't produce falls back to a clearly
         labeled rule-based explanation instead of an error or blank panel.
    """
    # Step 1: render the untouched Phase 1 report to a scratch file, then
    # read it back in. Phase 1's generator itself is never modified or
    # monkey-patched.
    with tempfile.TemporaryDirectory() as tmp:
        base_path = str(Path(tmp) / "base_report.html")
        generate_html_report(report_data, base_path)
        html = Path(base_path).read_text(encoding="utf-8")

    # Step 2: AI explanations. This call itself never raises (see
    # ai_explainer.generate_explanations docstring), but we wrap it again
    # here as a last line of defense so a bug in the AI layer can never
    # prevent the (already-rendered) Phase 1 report from being saved.
    try:
        explanations = ai_explainer.generate_explanations(
            report_data, api_key=api_key, model=model, timeout=timeout
        )
    except Exception:  # noqa: BLE001
        explanations = {}

    # Step 3: inject Inspect buttons + modal. Each injection step is
    # independently best-effort; if a particular button can't be placed,
    # the report is still written with everything else intact.
    try:
        html = _inject_inspect_buttons(html, report_data, explanations)
        html = _inject_overall_inspect(html, explanations)
        html = _inject_modal_and_css(html, explanations)
    except Exception:  # noqa: BLE001
        # Worst case: fall back to the plain, unmodified Phase 1 HTML
        # rather than fail the whole report.
        with tempfile.TemporaryDirectory() as tmp:
            base_path = str(Path(tmp) / "base_report.html")
            generate_html_report(report_data, base_path)
            html = Path(base_path).read_text(encoding="utf-8")

    output_path = str(output_path)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path