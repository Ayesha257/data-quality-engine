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
`data_quality_engine.engine.reporting.report_generator.build_report_data`,
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
    from data_quality_engine.engine.reporting.report_generator import (
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


def get_api_key(api_key: str | None = None) -> str | None:
    """Resolve the Gemini API key generically: explicit arg > env var.

    Never raises. Returns None if no key is configured anywhere, which
    callers treat as "AI layer unavailable" rather than an error.
    """
    return api_key or os.environ.get("GEMINI_API_KEY") or None


def _fallback_text(summary: dict[str, Any]) -> str:
    """Rule-based (non-AI) explanation built purely from Phase 1's own
    deterministic output. This is what the Inspect button shows whenever
    the AI layer can't be reached -- so the user always gets *something*
    useful, never a blank panel or an error message. Uses the same
    three-line WHAT'S WRONG / WHY IT MATTERS / WHAT TO DO structure as
    the AI explanations, so both render identically in the UI."""
    impact = summary.get("business_impact", "This may affect downstream analysis reliability.")
    recommendation = summary.get("recommendation", "Review and remediate the flagged rows/columns.")
    severity = summary.get("severity", "Unknown")
    issues = summary.get("total_issues_found", 0)
    cols = summary.get("columns_with_issues", 0)
    return (
        f"WHAT'S WRONG: {severity} severity -- {issues} issue(s) found across {cols} column(s).\n"
        f"WHY IT MATTERS: {impact}\n"
        f"WHAT TO DO: {recommendation}"
    )


def _build_check_prompt(summary: dict[str, Any]) -> str:
    """Turn one check's Phase-1-computed summary into a Gemini prompt.
    Only uses generic keys already present in report_generator's output --
    nothing dataset- or client-specific is hardcoded here."""
    display_name = summary.get("display_name", summary.get("check_name", "Check"))
    affected = ", ".join(summary.get("affected_columns", [])[:10]) or "none"
    samples = summary.get("sample_findings", [])[:5]
    sample_lines = "\n".join(
        f"- column '{s.get('column')}': {s.get('issues_found')} issue(s)" for s in samples
    ) or "(no row-level samples available)"

    return (
        "You are helping a non-technical business user understand an automated "
        "data quality report. Do not invent numbers -- only explain the facts given.\n\n"
        f"Check: {display_name}\n"
        f"Severity: {summary.get('severity')}\n"
        f"Columns checked: {summary.get('columns_checked')}\n"
        f"Columns with issues: {summary.get('columns_with_issues')}\n"
        f"Total issues found: {summary.get('total_issues_found')}\n"
        f"Affected columns: {affected}\n"
        f"Sample findings:\n{sample_lines}\n"
        f"Deterministic business impact (already decided by the rule engine, do not "
        f"contradict it): {summary.get('business_impact')}\n"
        f"Deterministic recommendation (already decided by the rule engine, do not "
        f"contradict it): {summary.get('recommendation')}\n\n"
        "Respond in EXACTLY three lines, using the exact numbers given above "
        "(never round or approximate). Do not add any text before, after, or "
        "between the three lines. Use this exact format:\n\n"
        "WHAT'S WRONG: one plain-English sentence stating the problem.\n"
        "WHY IT MATTERS: one sentence on the business risk, with a concrete "
        "example if useful.\n"
        "WHAT TO DO: one short, specific, actionable step.\n\n"
        "No technical jargon. No markdown symbols. Just the three labelled lines."
    )


def _build_overall_prompt(report_data: dict[str, Any], trend_text: str | None = None) -> str:
    sc = report_data.get("score", {})
    ov = report_data.get("overview", {})
    crit = report_data.get("executive_summary", {}).get("critical_findings", [])
    crit_lines = "\n".join(f"- {c}" for c in crit[:8]) or "(none)"
    trend_line = f"Trend vs. the last run on this file: {trend_text}\n" if trend_text else ""
    return (
        "You are summarizing an automated data quality report for a non-technical "
        "business stakeholder. Do not invent facts -- only use what is given.\n\n"
        f"Dataset size: {ov.get('rows')} rows x {ov.get('columns')} columns\n"
        f"Overall Data Quality Score: {sc.get('overall')} ({sc.get('rating')})\n"
        f"Readiness verdict (already decided by the rule engine): {sc.get('readiness')}\n"
        f"{trend_line}"
        f"Top findings:\n{crit_lines}\n\n"
        "Respond in EXACTLY three lines, using this exact format, no text "
        "before, after, or between them:\n\n"
        "WHAT'S WRONG: one sentence on overall data status, using the exact "
        "score given above" + (" and mentioning the trend if one was given" if trend_text else "") + ".\n"
        "WHY IT MATTERS: one sentence on the single biggest risk.\n"
        "WHAT TO DO: one short, specific, actionable next step.\n\n"
        "No jargon. No markdown symbols."
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
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3},
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
    trend_line = f"\nTREND: {trend_text}" if trend_text else ""
    fallback = (
        f"WHAT'S WRONG: Overall Data Quality Score is {sc.get('overall')} ({sc.get('rating')}).\n"
        f"WHY IT MATTERS: Readiness status is {sc.get('readiness')}.\n"
        f"WHAT TO DO: Review the findings below for details.{trend_line}"
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
            trend_line = f"\nTREND: {trend_text}" if trend_text else ""
            fallback = (
                f"WHAT'S WRONG: Overall Data Quality Score is {sc.get('overall')} ({sc.get('rating')}).\n"
                f"WHY IT MATTERS: Readiness status is {sc.get('readiness')}.\n"
                f"WHAT TO DO: Review the findings below for details.{trend_line}"
            )
            results["__overall__"] = Explanation(
                "__overall__", fallback, source="fallback", error="ai_layer_unavailable"
            ).to_dict()

    return results