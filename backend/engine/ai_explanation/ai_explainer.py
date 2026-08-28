"""
Phase 2 — AI Explanation Layer (M2).

Design principle (see PHASE2_PLAN.md): "AI explains findings; Phase 1
makes decisions." This module never decides whether something is a data
quality issue -- Phase 1's checks already did that. All this module does
is take the deterministic, already-computed summary for one check (or for
the whole report) and ask an LLM to restate it in plain language for a
non-technical business user.

Provider: Google Gemini, called directly over the public REST endpoint
(no google-generativeai SDK dependency required -- keeps this generic and
easy to drop into any environment that already has `requests`).

Nothing here is dataset-specific. It only ever reads the generic
dictionaries produced by
`backend.engine.reports.report_generator.build_report_data`,
so it works unmodified on any file/dataset Phase 1 can process.

Failure handling (important):
    Every public function in this module is designed to NEVER raise and
    NEVER block report generation. If the API key is missing, the network
    is unavailable, the request times out, or Gemini returns something
    unexpected, the function falls back to a clearly-labeled rule-based
    explanation built from data Phase 1 already computed (business_impact
    + recommendation). The caller (enhanced_report.py) always gets a
    complete, non-empty result for every check.
"""
from __future__ import annotations
from dotenv import load_dotenv
from pathlib import Path

import concurrent.futures
import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
from backend.engine.ai_explanation.explanation_guides import (
    EXPLANATION_SECTIONS,
    _format_findings,
    format_section_lines,
    guide_for,
)

try:
    import requests
except ImportError:  # pragma: no cover - requests ships with Phase 1's deps
    requests = None  # type: ignore[assignment]


# NOTE (2026-08): "gemini-2.0-flash" was deprecated by Google on
# 2026-03-03. gemini-2.5-flash is the current free-tier default as of
# this writing -- if Google renames/retires it again, set GEMINI_MODEL
# in your environment/.env rather than editing this file.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "20"))
_API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Free-tier Gemini Flash models allow roughly 10-15 requests/minute
# (Google changes this without much notice -- see GEMINI_RPM_LIMIT below
# to adjust without touching code). Defaulting to 8 leaves headroom for
# whatever the current real limit is.
DEFAULT_RPM_LIMIT = int(os.environ.get("GEMINI_RPM_LIMIT", "8"))
DEFAULT_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
# Ceiling on any single retry's backoff sleep. This function runs
# synchronously inside report generation, which itself runs synchronously
# inside the REST API's background job (see phase2/api/jobs.py) -- a
# caller polling for run status has a real, finite patience. A 429/5xx
# response's Retry-After header can legitimately say "60" or more; honor
# it as a signal but never let it (or the exponential-backoff fallback)
# turn one flaky check into a multi-minute stall. 8s x up to
# DEFAULT_MAX_RETRIES retries keeps the worst realistic case in the tens
# of seconds, not minutes -- see generate_explanations()'s batch_deadline,
# which is sized against this constant.
DEFAULT_MAX_RETRY_DELAY_SECONDS = float(os.environ.get("GEMINI_MAX_RETRY_DELAY_SECONDS", "8"))


class _RateLimiter:
    """
    Thread-safe sliding-window rate limiter shared by every Gemini call in
    this process. `generate_explanations()` fires several checks in
    parallel via a thread pool -- without this, that burst alone can
    exceed the free tier's per-minute quota on anything but a tiny file.

    acquire() blocks the calling thread until a slot is free (or until
    `max_wait` elapses), rather than raising, so callers never need extra
    error handling for "too many requests, too fast" -- they just get
    correctly throttled. `max_wait` exists so a caller with its own
    deadline (see generate_explanations' per_future_timeout) can't be
    blocked here for longer than it's willing to wait; returns False
    (never raises) if time ran out without a slot opening up, and the
    caller decides what "no slot became available" means for it.

    Root-cause note (Phase 2 M4): before max_wait existed, a run whose
    Gemini key was invalid/rate-limited/blocked by network egress would
    still queue every check behind this limiter's up-to-60-second window
    with no ceiling, and generate_explanations()'s as_completed() loop had
    no timeout of its own either -- so a single bad key could stall an
    entire pipeline run (and, transitively, every REST API run that
    requests write_report=True) for minutes. Bounding both this and the
    as_completed loop below is what actually fixes that.
    """

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max(1, max_per_minute)
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float | None = None) -> bool:
        deadline = time.monotonic() + max_wait if max_wait is not None else None
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] > 60:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_per_minute:
                    self._timestamps.append(now)
                    return True
                wait_for = 60 - (now - self._timestamps[0])
            if deadline is not None and time.monotonic() + max(0.05, wait_for) > deadline:
                return False
            time.sleep(max(0.05, wait_for))


_rate_limiter = _RateLimiter(DEFAULT_RPM_LIMIT)


@dataclass
class Explanation:
    """One check's (or the report's) AI-generated explanation, plus enough
    metadata for the UI to be honest about where the text came from."""

    check_name: str
    text: str
    source: str  # "ai" | "fallback"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "text": self.text,
            "source": self.source,
            "ai_available": self.source == "ai",
            "error": self.error,
        }


def _pii_summary_to_check_shape(pii_block: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize report_data['pii'] (a differently-shaped block than the
    per-check summaries) into the same shape _summarize_check() produces,
    so explain_check()/_build_check_prompt()/_fallback_text() all work on
    it completely unmodified. This is the pattern to follow for adding
    Inspect coverage to any other non-'checks' report section later
    (e.g. 'fuzzy', 'privacy_risk') -- write one small adapter like this
    one, nothing else needs to change.

    Only ever reads generic counts/column-name lists from pii_block, the
    same as every other summary shape here -- never a raw value.
    """
    from backend.engine.reports.report_generator import (
        BUSINESS_IMPACT,
        RECOMMENDATION,
    )

    columns_with_pii = pii_block.get("columns_with_pii", 0)
    return {
        "check_name": "pii",
        "display_name": "Sensitive Data (PII) Assessment",
        "columns_checked": pii_block.get("total_columns", 0),
        "columns_with_issues": columns_with_pii,
        "total_issues_found": pii_block.get("total_rows_with_pii", 0),
        "severity": "Critical" if columns_with_pii else "None",
        "affected_columns": pii_block.get("flagged_columns", []),
        "sample_findings": [
            {"column": col, "issues_found": None} for col in pii_block.get("flagged_columns", [])[:5]
        ],
        "business_impact": BUSINESS_IMPACT.get(
            "pii", "Privacy and regulatory compliance risk if shared or stored without masking."
        ),
        "recommendation": RECOMMENDATION.get(
            "pii", "Apply masking/encryption before sharing; restrict access to raw values."
        ),
    }


def _entity_resolution_summary_to_check_shape(er_block: dict[str, Any]) -> dict[str, Any]:
    """Normalize report_data['entity_resolution'] for the shared explain_check path."""
    summary = er_block.get("summary") or {}
    review_queue = er_block.get("review_queue") or []
    columns = er_block.get("columns") or {}
    affected = list(columns.keys())
    auto = int(summary.get("auto_match", 0))
    review = int(summary.get("review", 0))
    no_match = int(summary.get("no_match", 0))
    total = int(summary.get("total_values", 0))
    severity = "High" if review else ("Medium" if no_match else "Low")
    return {
        "check_name": "entity_resolution",
        "display_name": "Standardized Values",
        "columns_checked": len(affected),
        "columns_with_issues": sum(1 for c in columns.values() if c.get("resolutions")),
        "total_issues_found": review + no_match,
        "severity": severity,
        "affected_columns": affected,
        "sample_findings": [
            {
                "column": item.get("column"),
                "issues_found": item.get("confidence"),
                "decision": item.get("decision"),
                "candidate": item.get("candidate"),
            }
            for item in review_queue[:5]
        ],
        "business_impact": (
            "Inconsistent entity labels (cities, countries, codes) break joins, "
            "reporting roll-ups, and downstream analytics when the same real-world "
            "entity appears under multiple spellings."
        ),
        "recommendation": (
            f"Review {review} suggested mapping(s) and {no_match} unresolved value(s). "
            f"{auto} value(s) auto-matched at high confidence — verify a sample before bulk apply. "
            "Original column values are never overwritten automatically."
        ),
        "entity_resolution_summary": {
            "auto_match": auto,
            "review": review,
            "no_match": no_match,
            "total_values": total,
        },
    }


def get_api_key(api_key: str | None = None) -> str | None:
    """Resolve the Gemini API key generically: explicit arg > env var.

    Never raises. Returns None if no key is configured anywhere, which
    callers treat as "AI layer unavailable" rather than an error.
    """
    return api_key or os.environ.get("GEMINI_API_KEY") or None


def _ml_readiness_summary_to_check_shape(ml_block: dict[str, Any]) -> dict[str, Any]:
    """Normalize report_data['ml_readiness'] for the shared explain_check path."""
    blockers = ml_block.get("blockers") or []
    warnings = ml_block.get("warnings") or []
    verdict = str(ml_block.get("verdict", "NOT_READY"))
    overall = ml_block.get("overall_score")
    severity = "Critical" if blockers else ("Medium" if warnings else "Low")
    issue_count = len(blockers) + len(warnings)
    return {
        "check_name": "ml_readiness",
        "display_name": "Forecast Readiness",
        "columns_checked": 4,
        "columns_with_issues": sum(
            1
            for key in ("temporal", "interval", "target", "leakage")
            if (ml_block.get(key) or {}).get("blockers")
        ),
        "total_issues_found": issue_count,
        "severity": severity,
        "affected_columns": [],
        "sample_findings": [{"column": b, "issues_found": 1} for b in blockers[:5]],
        "business_impact": (
            "Forecasting and ML models trained on unprepared time-series data produce "
            "unreliable predictions, wasted effort, and false confidence in business decisions."
        ),
        "recommendation": (
            " ".join(ml_block.get("recommendations") or [])
            or "Address blockers listed in the readiness section before building a forecast model."
        ),
        "ml_readiness_context": {
            "verdict": verdict,
            "overall_score": overall,
            "blockers": blockers,
            "warnings": warnings,
            "temporal_score": (ml_block.get("temporal") or {}).get("score"),
            "interval_score": (ml_block.get("interval") or {}).get("score"),
            "target_score": (ml_block.get("target") or {}).get("score"),
            "leakage_score": (ml_block.get("leakage") or {}).get("score"),
        },
    }


def _fallback_text(summary: dict[str, Any]) -> str:
    """Rich rule-based explanation for novices — always available when AI is off."""
    check_name = summary.get("check_name", "")
    guide = guide_for(check_name)
    findings = _format_findings(summary)
    fix_text = summary.get("recommendation", guide["fix_hints"])
    if guide["fix_hints"] and guide["fix_hints"] not in fix_text:
        fix_text = f"{fix_text} {guide['fix_hints']}"

    ml_ctx = summary.get("ml_readiness_context")
    if ml_ctx:
        findings = (
            f"Verdict: {ml_ctx.get('verdict')}. Overall readiness score: "
            f"{ml_ctx.get('overall_score', 'N/A')}/100. "
            f"Sub-scores — temporal: {ml_ctx.get('temporal_score', '—')}, "
            f"interval: {ml_ctx.get('interval_score', '—')}, "
            f"target: {ml_ctx.get('target_score', '—')}, "
            f"leakage: {ml_ctx.get('leakage_score', '—')}. "
        )
        if ml_ctx.get("blockers"):
            findings += f"Blockers: {'; '.join(ml_ctx['blockers'][:5])}."
        elif ml_ctx.get("warnings"):
            findings += f"Warnings: {'; '.join(ml_ctx['warnings'][:5])}."
        else:
            findings += "No blockers detected — data looks ready for forecasting."

    if summary.get("regulation"):
        extra = (
            f"Regulation: {summary.get('regulation')}. "
            f"Confidence: {summary.get('confidence_tier') or summary.get('severity')}. "
            f"Column: {', '.join(summary.get('affected_columns') or []) or 'n/a'}."
        )
        samples = summary.get("masked_samples") or []
        if samples:
            extra += f" Masked samples: {', '.join(str(s) for s in samples[:5])}."
        findings = f"{extra} {findings}".strip()

    return format_section_lines(
        {
            "WHAT THIS MEANS": guide["what_it_means"],
            "HOW WE CHECKED IT": guide["how_we_check"],
            "WHAT WE FOUND": findings,
            "WHY IT MATTERS": summary.get(
                "business_impact",
                "Poor data quality in this area reduces trust in reports and downstream decisions.",
            ),
            "HOW TO FIX": fix_text.strip(),
        }
    )


def _build_check_prompt(summary: dict[str, Any]) -> str:
    """Turn one check's summary into a Gemini prompt for novice-friendly explanations."""
    check_name = summary.get("check_name", summary.get("display_name", "check"))
    guide = guide_for(str(check_name))
    display_name = summary.get("display_name", check_name)
    affected = ", ".join(summary.get("affected_columns", [])[:10]) or "none"
    samples = summary.get("sample_findings", [])[:5]
    sample_lines = "\n".join(
        f"- column '{s.get('column')}': {s.get('issues_found', 'see report')} issue(s)"
        + (f" ({s.get('decision', '')})" if s.get("decision") else "")
        for s in samples
    ) or "(no row-level samples — use the counts below)"

    section_format = "\n".join(f"{label}: ..." for label in EXPLANATION_SECTIONS)

    return (
        "You are a patient data-quality coach explaining a report to someone with NO technical "
        "background. Use warm, clear language. Define any term you use. Never invent numbers — "
        "only use facts from the data block below.\n\n"
        f"Check name: {display_name}\n"
        f"Severity: {summary.get('severity')}\n"
        f"Columns checked: {summary.get('columns_checked')}\n"
        f"Columns with issues: {summary.get('columns_with_issues')}\n"
        f"Total issues found: {summary.get('total_issues_found')}\n"
        f"Affected columns: {affected}\n"
        f"Sample findings:\n{sample_lines}\n\n"
        + (
            f"Regulation: {summary.get('regulation')}\n"
            f"Confidence tier: {summary.get('confidence_tier')}\n"
            f"Masked/sample values: {', '.join(str(s) for s in (summary.get('masked_samples') or [])[:5]) or 'none'}\n\n"
            if summary.get("regulation")
            else ""
        )
        + "Educational context for this check in THIS product (use to teach the reader):\n"
        f"- Concept: {guide['what_it_means']}\n"
        f"- Methodology: {guide['how_we_check']}\n\n"
        f"Pre-computed business impact (do not contradict): {summary.get('business_impact')}\n"
        f"Pre-computed recommendation (extend, do not contradict): {summary.get('recommendation')}\n\n"
        "Write exactly five labeled sections, 2-4 sentences each, plain English, no markdown, "
        "no bullet symbols. Use the exact labels below, one per line, in this order:\n\n"
        f"{section_format}\n\n"
        "In WHAT THIS MEANS, explain the concept as if the reader has never heard of this check. "
        "In HOW WE CHECKED IT, describe what the engine did in simple steps. "
        "In WHAT WE FOUND, state the exact severity, counts, and affected columns from the data above. "
        "If there are zero issues, say so positively. "
        "In WHY IT MATTERS, connect to real business impact (reports, decisions, compliance). "
        "In HOW TO FIX, give concrete, ordered steps they can take this week."
    )


def _build_overall_prompt(report_data: dict[str, Any], trend_text: str | None = None) -> str:
    guide = guide_for("__overall__")
    sc = report_data.get("score", {})
    ov = report_data.get("overview", {})
    crit = report_data.get("executive_summary", {}).get("critical_findings", [])
    crit_lines = "\n".join(f"- {c}" for c in crit[:8]) or "(none flagged as critical)"
    trend_line = f"Trend vs last run on this file: {trend_text}\n" if trend_text else ""
    section_format = "\n".join(f"{label}: ..." for label in EXPLANATION_SECTIONS)

    findings = (
        f"Overall Data Quality Score: {sc.get('overall')} / 100 ({sc.get('rating')}). "
        f"Readiness band: {sc.get('readiness')}. "
        f"Dataset: {ov.get('rows')} rows × {ov.get('columns')} columns. "
        f"{trend_line}"
        f"Top critical findings:\n{crit_lines}"
    )

    return (
        "You are summarizing a data quality report for a non-technical business stakeholder. "
        "Teach them what the score means and what to do next. Do not invent facts.\n\n"
        f"{findings}\n\n"
        "Context:\n"
        f"- What this report is: {guide['what_it_means']}\n"
        f"- How scoring works: {guide['how_we_check']}\n\n"
        "Write exactly five labeled sections, 2-4 sentences each, plain English, no markdown:\n\n"
        f"{section_format}\n\n"
        "In WHAT WE FOUND, use the exact score and findings given. "
        "In HOW TO FIX, prioritize the highest-impact actions first."
    )


def _call_gemini(prompt: str, api_key: str, model: str, timeout: float) -> str:
    """Single Gemini REST call, with rate limiting and retry-with-backoff.

    Kept as a thin, swappable function so a different provider or the
    official SDK can be dropped in later without touching callers.

    Resilience added here (on top of the original single-shot call):
      - Waits for a free slot in the shared rate limiter BEFORE sending,
        so concurrent explain_check() calls from the thread pool don't
        all land in the same second and blow through the free-tier quota.
      - On HTTP 429 (rate limited) or 5xx (transient server error),
        retries with exponential backoff + jitter, honoring the server's
        Retry-After header when it sends one, instead of failing straight
        to the fallback explanation on the first hiccup.
      - Any other error (4xx auth/bad-request, timeout, malformed
        response) is NOT retried -- retrying those would just waste the
        remaining quota on a call that can't succeed.
    """
    if requests is None:
        raise RuntimeError("the 'requests' package is not installed")

    url = _API_URL_TEMPLATE.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.35},
    }

    last_exc: Exception | None = None
    for attempt in range(DEFAULT_MAX_RETRIES + 1):
        # Bounded, not unbounded: a caller waiting `timeout` seconds for
        # an HTTP response shouldn't also be able to wait up to a full
        # 60-second rate-limiter window on top of that with no ceiling.
        # If the limiter can't free a slot in time, treat that exactly
        # like any other failure to get a response -- an exception the
        # retry loop / caller's fallback path already knows how to
        # handle -- rather than a silent indefinite stall.
        if not _rate_limiter.acquire(max_wait=timeout):
            last_exc = TimeoutError(
                f"Gemini rate limiter did not free a slot within {timeout}s"
            )
            if attempt < DEFAULT_MAX_RETRIES:
                continue
            raise last_exc
        try:
            resp = requests.post(url, params={"key": api_key}, json=payload, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else (2**attempt) + random.uniform(0, 1)
                last_exc = RuntimeError(f"Gemini returned HTTP {resp.status_code}")
                if attempt < DEFAULT_MAX_RETRIES:
                    time.sleep(min(delay, DEFAULT_MAX_RETRY_DELAY_SECONDS))
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise ValueError("Gemini returned no candidates")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise ValueError("Gemini returned an empty response")
            return text
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            # Transient network issue: worth one retry, not worth exhausting all of them.
            last_exc = exc
            if attempt < min(1, DEFAULT_MAX_RETRIES):
                time.sleep((2**attempt) + random.uniform(0, 1))
                continue
            raise

    raise last_exc or RuntimeError("Gemini call failed for an unknown reason")


def explain_check(
    check_name: str,
    summary: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Explanation:
    """Explain one check's findings. Never raises."""
    summary = {**summary, "check_name": summary.get("check_name", check_name)}
    key = get_api_key(api_key)
    if not key:
        return Explanation(check_name, _fallback_text(summary), source="fallback", error="no_api_key")
    try:
        prompt = _build_check_prompt(summary)
        text = _call_gemini(prompt, key, model, timeout)
        return Explanation(check_name, text, source="ai")
    except Exception as exc:  # noqa: BLE001 - deliberately broad: AI must never break the report
        return Explanation(check_name, _fallback_text(summary), source="fallback", error=str(exc)[:300])


def explain_overall(
    report_data: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    trend_text: str | None = None,
) -> Explanation:
    """Explain the report as a whole (executive summary). Never raises.

    trend_text: optional plain-language score trend (e.g. "improved +5.2
    points since 2026-08-01") from phase2/history.py. When provided, both
    the AI prompt and the fallback text reference it, so the executive
    summary is historically aware, not just a snapshot.
    """
    key = get_api_key(api_key)
    sc = report_data.get("score", {})
    fallback = format_section_lines(
        {
            "WHAT THIS MEANS": guide_for("__overall__")["what_it_means"],
            "HOW WE CHECKED IT": guide_for("__overall__")["how_we_check"],
            "WHAT WE FOUND": (
                f"Overall Data Quality Score is {sc.get('overall')} / 100 ({sc.get('rating')}). "
                f"Readiness: {sc.get('readiness')}."
                + (f" {trend_text}" if trend_text else "")
            ),
            "WHY IT MATTERS": (
                "Scores below your organization's threshold mean reports, dashboards, and shared "
                "exports may mislead stakeholders until the highest-severity checks are addressed."
            ),
            "HOW TO FIX": guide_for("__overall__")["fix_hints"],
        }
    )
    if not key:
        return Explanation("__overall__", fallback, source="fallback", error="no_api_key")
    try:
        prompt = _build_overall_prompt(report_data, trend_text=trend_text)
        text = _call_gemini(prompt, key, model, timeout)
        return Explanation("__overall__", text, source="ai")
    except Exception as exc:  # noqa: BLE001
        return Explanation("__overall__", fallback, source="fallback", error=str(exc)[:300])


def generate_explanations(
    report_data: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_workers: int = 4,
    include_overall: bool = True,
    include_pii: bool = True,
    include_entity_resolution: bool = True,
    include_ml_readiness: bool = True,
    trend_text: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Generate an Inspect-button explanation for every check in
    report_data['checks'], plus (optionally) one for the report overall
    and one for the PII/sensitive-data block.

    Generic by construction: iterates whatever check names Phase 1
    produced for this particular dataset -- no hardcoded check list.
    Non-'checks' sections (currently just PII) are added via a small
    adapter (see _pii_summary_to_check_shape) that normalizes them into
    the same shape, so they flow through the exact same explain_check()
    path -- no separate AI call logic needed per section type.

    Never raises. Worst case (no key / Gemini down / package missing),
    every entry in the returned dict is a "fallback" explanation built
    from Phase 1's own numbers, and the caller can render the report
    exactly as if this function had succeeded.
    """
    checks: dict[str, dict[str, Any]] = dict(report_data.get("checks", {}) or {})

    pii_block = report_data.get("pii") or {}
    if include_pii and pii_block:
        checks["pii"] = _pii_summary_to_check_shape(pii_block)

    er_block = report_data.get("entity_resolution") or {}
    if include_entity_resolution and er_block.get("enabled"):
        checks["entity_resolution"] = _entity_resolution_summary_to_check_shape(er_block)

    ml_block = report_data.get("ml_readiness") or {}
    if include_ml_readiness and ml_block:
        checks["ml_readiness"] = _ml_readiness_summary_to_check_shape(ml_block)

    results: dict[str, dict[str, Any]] = {}

    # Hard wall-clock ceiling for the WHOLE batch, independent of any
    # single call's `timeout`. Sized against the same constants
    # _call_gemini uses for its own worst case (rate-limiter wait capped
    # to `timeout`, plus up to DEFAULT_MAX_RETRIES retries each capped to
    # DEFAULT_MAX_RETRY_DELAY_SECONDS of backoff) so this number is a
    # real, provable ceiling rather than a guess -- and additionally
    # hard-capped at MAX_BATCH_DEADLINE_SECONDS so a misconfigured
    # GEMINI_TIMEOUT_SECONDS/GEMINI_MAX_RETRIES env var can never make
    # this synchronous, best-effort step dominate the request it's part
    # of (this call sits inside main.run_pipeline(), which the REST API's
    # background job runner -- phase2/api/jobs.py -- and its callers wait
    # on to reach a terminal status).
    MAX_BATCH_DEADLINE_SECONDS = 25.0
    batch_deadline = min(
        timeout + DEFAULT_MAX_RETRIES * DEFAULT_MAX_RETRY_DELAY_SECONDS + 5,
        MAX_BATCH_DEADLINE_SECONDS,
    )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(
                    explain_check, name, summary, api_key=api_key, model=model, timeout=timeout
                ): name
                for name, summary in checks.items()
            }
            if include_overall:
                futures[
                    pool.submit(
                        explain_overall,
                        report_data,
                        api_key=api_key,
                        model=model,
                        timeout=timeout,
                        trend_text=trend_text,
                    )
                ] = "__overall__"

            done, not_done = concurrent.futures.wait(
                futures, timeout=batch_deadline, return_when=concurrent.futures.ALL_COMPLETED
            )
            for future in done:
                name = futures[future]
                try:
                    explanation = future.result()
                except Exception as exc:  # noqa: BLE001 - absolute last-resort safety net
                    summary = checks.get(name, {})
                    explanation = Explanation(
                        name, _fallback_text(summary), source="fallback", error=str(exc)[:300]
                    )
                results[name] = explanation.to_dict()
            for future in not_done:
                # Still running past the batch deadline (e.g. Gemini is
                # degraded and every retry+backoff is stacking up). Don't
                # wait on it -- it's a daemon-owned pool thread and will be
                # abandoned when the `with` block exits; give the caller
                # the fallback text now instead of blocking on it.
                name = futures[future]
                summary = checks.get(name, {})
                results[name] = Explanation(
                    name, _fallback_text(summary), source="fallback", error="batch_deadline_exceeded"
                ).to_dict()
    except Exception:  # noqa: BLE001 - even the thread pool must never take the report down
        # Total AI-layer outage (e.g. threading unavailable in a restricted
        # sandbox): fall back to synchronous rule-based text for everything.
        for name, summary in checks.items():
            results[name] = Explanation(
                name, _fallback_text(summary), source="fallback", error="ai_layer_unavailable"
            ).to_dict()
        if include_overall:
            sc = report_data.get("score", {})
            trend_line = f" Trend: {trend_text}" if trend_text else ""
            results["__overall__"] = Explanation(
                "__overall__",
                format_section_lines(
                    {
                        "WHAT THIS MEANS": guide_for("__overall__")["what_it_means"],
                        "HOW WE CHECKED IT": guide_for("__overall__")["how_we_check"],
                        "WHAT WE FOUND": (
                            f"Overall score {sc.get('overall')} ({sc.get('rating')}). "
                            f"Readiness: {sc.get('readiness')}.{trend_line}"
                        ),
                        "WHY IT MATTERS": (
                            "Low scores mean decisions based on this file may be unreliable until fixes are applied."
                        ),
                        "HOW TO FIX": guide_for("__overall__")["fix_hints"],
                    }
                ),
                source="fallback",
                error="ai_layer_unavailable",
            ).to_dict()

    return results