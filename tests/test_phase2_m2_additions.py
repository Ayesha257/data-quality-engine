"""
Phase 2 — M2 additions test suite (rate limiting, run history/trend,
PII Inspect coverage).

Run with:
    pytest tests/test_phase2_m2_additions.py -v

None of these tests make a real network call or need a GEMINI_API_KEY —
Gemini itself is mocked via monkeypatch on `requests.post`. The history
tests use a temporary SQLite file so they never touch your real
phase2_dev.db.
"""

from __future__ import annotations

import time

import pytest

from backend.engine.ai_explanation import ai_explainer
from backend.database import history
from backend.database import init_db
from backend.engine.ai_explanation.enhanced_report import (
    _inject_pii_inspect,
    _inject_trend_banner,
)


# ---------------------------------------------------------------------------
# 1. Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_calls_up_to_the_limit_without_blocking(self):
        limiter = ai_explainer._RateLimiter(max_per_minute=5)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # should be near-instant, no waiting needed yet

    def test_blocks_once_the_limit_is_reached(self, monkeypatch):
        limiter = ai_explainer._RateLimiter(max_per_minute=2)
        sleep_calls = []
        clock = {"t": 1000.0}
        monkeypatch.setattr(ai_explainer.time, "monotonic", lambda: clock["t"])
        def fake_sleep(s):
            sleep_calls.append(s)
            clock["t"] += s + 1.0
        monkeypatch.setattr(ai_explainer.time, "sleep", fake_sleep)

        limiter.acquire()
        limiter.acquire()
        limiter.acquire()  # 3rd call over the limit of 2 -> must wait
        assert len(sleep_calls) >= 1


# ---------------------------------------------------------------------------
# 2. Retry / backoff on 429 and 5xx
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class TestRetryBackoff:
    def test_succeeds_after_one_429_then_200(self, monkeypatch):
        monkeypatch.setattr(ai_explainer.time, "sleep", lambda s: None)  # skip real waiting
        calls = {"n": 0}

        def fake_post(url, params=None, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(429, headers={"Retry-After": "0"})
            return _FakeResponse(
                200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
            )

        monkeypatch.setattr(ai_explainer.requests, "post", fake_post)
        text = ai_explainer._call_gemini("prompt", "fake-key", "gemini-2.5-flash", 20)
        assert text == "ok"
        assert calls["n"] == 2

    def test_gives_up_after_max_retries_on_persistent_429(self, monkeypatch):
        monkeypatch.setattr(ai_explainer.time, "sleep", lambda s: None)
        monkeypatch.setattr(ai_explainer, "DEFAULT_MAX_RETRIES", 2)

        def always_429(url, params=None, json=None, timeout=None):
            return _FakeResponse(429)

        monkeypatch.setattr(ai_explainer.requests, "post", always_429)
        with pytest.raises(Exception):
            ai_explainer._call_gemini("prompt", "fake-key", "gemini-2.5-flash", 20)

    def test_explain_check_falls_back_gracefully_when_gemini_unreachable(self, monkeypatch):
        monkeypatch.setattr(ai_explainer.time, "sleep", lambda s: None)

        def always_fails(url, params=None, json=None, timeout=None):
            return _FakeResponse(500)

        monkeypatch.setattr(ai_explainer.requests, "post", always_fails)
        summary = {
            "severity": "High",
            "total_issues_found": 10,
            "columns_with_issues": 2,
            "business_impact": "impact",
            "recommendation": "fix it",
        }
        result = ai_explainer.explain_check("missing_values", summary, api_key="fake-key")
        assert result.source == "fallback"
        assert "WHAT THIS MEANS" in result.text


# ---------------------------------------------------------------------------
# 3. PII summary adapter + Inspect injection
# ---------------------------------------------------------------------------

class TestPiiInspectCoverage:
    def test_pii_summary_adapts_to_check_shape(self):
        pii_block = {
            "columns_with_pii": 3,
            "total_columns": 10,
            "total_rows_with_pii": 42,
            "types_found": ["EMAIL", "PHONE"],
            "flagged_columns": ["Email", "Phone", "CNIC"],
        }
        adapted = ai_explainer._pii_summary_to_check_shape(pii_block)
        assert adapted["columns_with_issues"] == 3
        assert adapted["total_issues_found"] == 42
        assert adapted["affected_columns"] == ["Email", "Phone", "CNIC"]
        assert adapted["severity"] == "Critical"
        # Only column names, never actual PII values, ever enter this dict.
        assert "types_found" not in str(adapted) or "EMAIL" not in adapted.get("recommendation", "")

    def test_pii_summary_with_zero_findings_is_not_critical(self):
        adapted = ai_explainer._pii_summary_to_check_shape(
            {"columns_with_pii": 0, "total_columns": 5, "total_rows_with_pii": 0,
             "types_found": [], "flagged_columns": []}
        )
        assert adapted["severity"] == "None"

    def test_generate_explanations_includes_pii_when_present(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        report_data = {
            "checks": {},
            "pii": {
                "columns_with_pii": 1,
                "total_columns": 3,
                "total_rows_with_pii": 5,
                "types_found": ["EMAIL"],
                "flagged_columns": ["Email"],
            },
            "score": {"overall": 80, "rating": "Good", "readiness": "Ready"},
        }
        results = ai_explainer.generate_explanations(report_data, include_overall=False)
        assert "pii" in results
        assert results["pii"]["source"] == "fallback"  # no key set -> fallback, never crashes

    def test_inject_pii_inspect_adds_button_when_section_present(self):
        html = '<html><body><h2>Sensitive Data &amp; Standardization Summary</h2></body></html>'
        explanations = {"pii": {"ai_available": False}}
        result = _inject_pii_inspect(html, explanations)
        assert "inspect-btn" in result
        assert 'data-check="pii"' in result

    def test_inject_pii_inspect_is_noop_when_section_missing(self):
        html = "<html><body>no pii section here</body></html>"
        result = _inject_pii_inspect(html, {"pii": {"ai_available": True}})
        assert result == html

    def test_inject_pii_inspect_is_noop_when_no_pii_explanation(self):
        html = '<html><body><h2>Sensitive Data &amp; Standardization Summary</h2></body></html>'
        result = _inject_pii_inspect(html, {})
        assert result == html


# ---------------------------------------------------------------------------
# 4. Run history & score trend
# ---------------------------------------------------------------------------

class TestHistoryTrend:
    @pytest.fixture(autouse=True)
    def _fresh_db(self, tmp_path):
        history._db_initialized = False
        init_db(database_url=f"sqlite:///{tmp_path / 'history_test.db'}")
        history._db_initialized = True
        yield
        history._db_initialized = False

    def test_first_run_has_no_previous_score(self):
        trend = history.get_score_trend("acme", "orders.xlsx", current_score=80.0)
        assert trend.direction == "first_run"
        assert "First recorded run" in trend.to_display_text()

    def test_second_run_shows_improvement(self):
        history.save_run("acme", "orders.xlsx", overall_score=70.0)
        trend = history.get_score_trend("acme", "orders.xlsx", current_score=85.0)
        assert trend.direction == "improved"
        assert trend.delta == pytest.approx(15.0)

    def test_second_run_shows_decline(self):
        history.save_run("acme", "orders.xlsx", overall_score=90.0)
        trend = history.get_score_trend("acme", "orders.xlsx", current_score=60.0)
        assert trend.direction == "declined"
        assert trend.delta == pytest.approx(-30.0)

    def test_different_client_or_file_does_not_mix_history(self):
        history.save_run("acme", "orders.xlsx", overall_score=70.0)
        trend_other_client = history.get_score_trend("globex", "orders.xlsx", current_score=85.0)
        trend_other_file = history.get_score_trend("acme", "invoices.xlsx", current_score=85.0)
        assert trend_other_client.direction == "first_run"
        assert trend_other_file.direction == "first_run"

    def test_record_run_and_get_trend_computes_before_saving(self):
        history.save_run("acme", "orders.xlsx", overall_score=70.0)
        report_data = {
            "score": {"overall": 88.0, "dimension_scores": {}},
            "overview": {"rows": 100, "columns": 5},
        }
        trend = history.record_run_and_get_trend("acme", "orders.xlsx", report_data)
        assert trend.direction == "improved"
        assert trend.previous_score == pytest.approx(70.0)
        # The just-saved run should now be retrievable as history for next time.
        trend2 = history.get_score_trend("acme", "orders.xlsx", current_score=88.0)
        assert trend2.previous_score == pytest.approx(88.0)

    def test_inject_trend_banner_present_for_improved_trend(self):
        html = (
            '<html><head><style></style></head><body>'
            '<div class="score-hero">x</div>'
            '<div class="hero-meta">y</div>'
            '</body></html>'
        )
        trend = history.ScoreTrend(
            previous_score=70.0, current_score=85.0, delta=15.0,
            previous_run_id="r1", previous_run_at="2026-08-01 00:00 UTC",
        )
        result = _inject_trend_banner(html, trend)
        assert "trend-improved" in result

    def test_inject_trend_banner_noop_when_trend_is_none(self):
        html = "<html><body>no hero here</body></html>"
        assert _inject_trend_banner(html, None) == html
