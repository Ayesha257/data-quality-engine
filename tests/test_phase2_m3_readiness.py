"""
Phase 2 — M3 (ML Readiness Assessment) test suite.

Covers PHASE2_PLAN.md "M3: ML Readiness Assessment" sub-sections 3.1-3.6:
temporal sufficiency, interval regularity, target integrity, leakage &
cardinality, the combined readiness scorer, and the report_generator.py
"ML Model Readiness Assessment" section.

Run with:
    pytest tests/test_phase2_m3_readiness.py -v

All test data is hand-built and deterministic (no randomness, no network,
no real client files), matching the "never raises" / deterministic design
principle used throughout phase2/ai_explainer.py and phase2/rules.py.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from data_quality_engine.phase2.readiness.intervals import analyze_interval_regularity
from data_quality_engine.phase2.readiness.leakage import analyze_leakage_and_cardinality
from data_quality_engine.phase2.readiness.scorer import score_readiness
from data_quality_engine.phase2.readiness.target import analyze_target_integrity
from data_quality_engine.phase2.readiness.temporal import analyze_temporal_sufficiency


def _sine_series(n: int, period: int = 30, amplitude: float = 10.0, base: float = 100.0) -> list[float]:
    """Deterministic, bounded, no-outlier wave -- used as a "clean" target
    series across tests so nothing here depends on a random seed."""
    return [base + amplitude * math.sin(2 * math.pi * i / period) for i in range(n)]


# ---------------------------------------------------------------------------
# 3.1 Temporal Analysis
# ---------------------------------------------------------------------------

class TestTemporalSufficiency:
    def test_temporal_sufficient_with_2_years_daily(self):
        """730+ observations, daily, 2 full years -> sufficient."""
        dates = pd.date_range("2022-01-01", periods=731, freq="D")
        df = pd.DataFrame({"date": dates})

        result = analyze_temporal_sufficiency(df, "date")

        assert result.total_observations == 731
        assert result.implied_frequency == "daily"
        assert result.seasonal_cycles_detected >= 2
        assert result.sufficient is True
        assert result.blockers == []

    def test_temporal_insufficient_with_1_year(self):
        """365 observations, daily, 1 year -> blocker: need 2 cycles."""
        dates = pd.date_range("2022-01-01", periods=365, freq="D")
        df = pd.DataFrame({"date": dates})

        result = analyze_temporal_sufficiency(df, "date")

        assert result.total_observations == 365
        assert result.seasonal_cycles_detected < 2
        assert result.sufficient is False
        assert any("seasonal cycle" in b.lower() for b in result.blockers)

    def test_temporal_detects_frequency(self):
        """Auto-detect daily, weekly, monthly, yearly."""
        cases = [
            ("D", "daily"),
            ("W", "weekly"),
            ("MS", "monthly"),
            ("YS", "yearly"),
        ]
        for alias, expected in cases:
            dates = pd.date_range("2010-01-01", periods=60, freq=alias)
            df = pd.DataFrame({"date": dates})

            result = analyze_temporal_sufficiency(df, "date")

            assert result.implied_frequency == expected, f"freq alias {alias}"

    # -- robustness: never raises --------------------------------------

    def test_temporal_handles_empty_dataframe(self):
        result = analyze_temporal_sufficiency(pd.DataFrame(), "date")
        assert result.sufficient is False
        assert result.blockers

    def test_temporal_handles_missing_column(self):
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = analyze_temporal_sufficiency(df, "date")
        assert result.sufficient is False
        assert "not found" in result.blockers[0]

    def test_temporal_handles_all_invalid_dates(self):
        df = pd.DataFrame({"date": ["not a date", "still not a date", None]})
        result = analyze_temporal_sufficiency(df, "date")
        assert result.sufficient is False
        assert result.total_observations == 0


# ---------------------------------------------------------------------------
# 3.2 Interval Regularity
# ---------------------------------------------------------------------------

class TestIntervalRegularity:
    def test_regularity_perfect_daily(self):
        """Daily data with no gaps -> score 1.0, no blockers."""
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        df = pd.DataFrame({"date": dates})

        result = analyze_interval_regularity(df, "date")

        assert result.inferred_frequency == "daily"
        assert result.missing_intervals == 0
        assert result.duplicate_timestamps == 0
        assert result.regularity_score == 1.0
        assert result.sufficient is True
        assert result.blockers == []

    def test_regularity_with_weekend_gaps(self):
        """Weekday data (weekends missing) -> detected, regularity_score < 1.0."""
        dates = pd.bdate_range("2023-01-02", periods=100)  # business days only
        df = pd.DataFrame({"date": dates})

        result = analyze_interval_regularity(df, "date")

        assert result.missing_intervals > 0
        assert result.regularity_score < 1.0
        assert result.duplicate_timestamps == 0

    def test_regularity_duplicate_timestamps(self):
        """Duplicate timestamps -> blocker, score penalized."""
        dates = list(pd.date_range("2023-01-01", periods=50, freq="D"))
        dates[10] = dates[9]  # inject one duplicate timestamp
        df = pd.DataFrame({"date": dates})

        result = analyze_interval_regularity(df, "date")

        assert result.duplicate_timestamps > 0
        assert result.regularity_score < 1.0
        assert result.sufficient is False
        assert any("duplicate" in b.lower() for b in result.blockers)

    # -- robustness: never raises --------------------------------------

    def test_regularity_handles_empty_dataframe(self):
        result = analyze_interval_regularity(pd.DataFrame(), "date")
        assert result.sufficient is False
        assert result.blockers

    def test_regularity_handles_missing_column(self):
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = analyze_interval_regularity(df, "date")
        assert result.sufficient is False
        assert "not found" in result.blockers[0]


# ---------------------------------------------------------------------------
# 3.3 Target Integrity
# ---------------------------------------------------------------------------

class TestTargetIntegrity:
    def test_target_sufficient_with_low_nulls(self):
        """< 5% nulls, 10% zeros, high variance -> sufficient."""
        n = 200
        values = _sine_series(n, period=25, amplitude=20.0, base=100.0)
        s = pd.Series(values)
        s.iloc[0:20] = 0.0  # 10% zeros
        s.iloc[20:28] = None  # 4% nulls (< 5%)
        df = pd.DataFrame({"amount": s})

        result = analyze_target_integrity(df, "amount")

        assert result.null_pct < 5.0
        assert 5.0 <= result.zero_pct <= 15.0
        assert result.variance > 1.0
        assert result.sufficient is True
        assert result.blockers == []

    def test_target_blocker_near_constant(self):
        """Variance nearly zero -> blocker: unforecastable."""
        s = pd.Series([50.0] * 100)
        df = pd.DataFrame({"amount": s})

        result = analyze_target_integrity(df, "amount")

        assert result.variance == 0.0
        assert result.sufficient is False
        assert any("variance" in b.lower() or "constant" in b.lower() for b in result.blockers)

    def test_target_blocker_too_many_nulls(self):
        """> 30% nulls -> blocker: data too sparse."""
        n = 100
        values = _sine_series(n, period=20, amplitude=5.0, base=50.0)
        s = pd.Series(values)
        s.iloc[0:40] = None  # 40% nulls
        df = pd.DataFrame({"amount": s})

        result = analyze_target_integrity(df, "amount")

        assert result.null_pct > 30.0
        assert result.sufficient is False
        assert any("null" in b.lower() for b in result.blockers)

    # -- robustness: never raises --------------------------------------

    def test_target_handles_empty_dataframe(self):
        result = analyze_target_integrity(pd.DataFrame(), "amount")
        assert result.sufficient is False
        assert result.blockers

    def test_target_handles_missing_column(self):
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = analyze_target_integrity(df, "amount")
        assert result.sufficient is False
        assert "not found" in result.blockers[0]

    def test_target_handles_non_numeric_column(self):
        df = pd.DataFrame({"amount": ["apple", "banana", "cherry", "date"]})
        result = analyze_target_integrity(df, "amount")
        assert result.sufficient is False
        assert any("numeric" in b.lower() for b in result.blockers)


# ---------------------------------------------------------------------------
# 3.4 Leakage & Cardinality
# ---------------------------------------------------------------------------

class TestLeakageAndCardinality:
    def test_leakage_detected(self):
        """Feature = target + constant -> perfect correlation, flagged."""
        target = _sine_series(100, period=17, amplitude=10.0, base=100.0)
        df = pd.DataFrame({"amount": target, "amount_plus_5": [v + 5 for v in target]})

        result = analyze_leakage_and_cardinality(df, "amount")

        assert "amount_plus_5" in result.perfect_correlation_features
        assert result.concern_level == "blocker"

    def test_high_cardinality_flagged(self):
        """100 rows, 99 unique values -> flagged."""
        n = 100
        amount = _sine_series(n, period=13, amplitude=5.0, base=50.0)
        weird_col = list(range(99)) + [0]  # 99 unique values across 100 rows
        df = pd.DataFrame({"amount": amount, "weird_col": weird_col})

        result = analyze_leakage_and_cardinality(df, "amount")

        assert "weird_col" in result.high_cardinality_features

    def test_identifier_column_detected(self):
        """'customer_id' with 100/100 unique -> flagged as identifier."""
        n = 100
        amount = _sine_series(n, period=11, amplitude=5.0, base=50.0)
        df = pd.DataFrame({"amount": amount, "customer_id": range(n)})

        result = analyze_leakage_and_cardinality(df, "amount")

        assert "customer_id" in result.identifier_features
        assert "customer_id" in result.high_cardinality_features
        assert result.concern_level in ("warning", "blocker")

    # -- robustness: never raises --------------------------------------

    def test_leakage_handles_empty_dataframe(self):
        result = analyze_leakage_and_cardinality(pd.DataFrame(), "amount")
        assert result.concern_level == "none"
        assert result.perfect_correlation_features == []

    def test_leakage_handles_missing_target_column(self):
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = analyze_leakage_and_cardinality(df, "amount")
        assert result.concern_level == "none"

    def test_leakage_no_false_positive_on_normal_features(self):
        """Ordinary, unrelated numeric/categorical columns -> no leakage,
        no cardinality flags."""
        n = 50
        amount = _sine_series(n, period=10, amplitude=5.0, base=20.0)
        category = ["A", "B", "C"] * 17
        df = pd.DataFrame({"amount": amount, "category": category[:n]})

        result = analyze_leakage_and_cardinality(df, "amount")

        assert result.perfect_correlation_features == []
        assert result.high_cardinality_features == []
        assert result.identifier_features == []
        assert result.concern_level == "none"


# ---------------------------------------------------------------------------
# 3.5 Readiness Scorer
# ---------------------------------------------------------------------------

class TestReadinessScorer:
    def _clean_df(self, n: int = 800) -> pd.DataFrame:
        dates = pd.date_range("2022-01-01", periods=n, freq="D")
        amount = _sine_series(n, period=30, amplitude=10.0, base=100.0)
        return pd.DataFrame({"date": dates, "amount": amount})

    def test_readiness_score_ready(self):
        """Good temporal, regular intervals, clean target -> 'ready'."""
        df = self._clean_df()

        result = score_readiness(df, "amount", "date")

        assert result.blockers == []
        assert result.overall_score >= 80
        assert result.verdict == "ready"

    def test_readiness_score_not_ready_blocker(self):
        """Any blocker -> 'not_ready' even if score is high."""
        dates = list(pd.date_range("2022-01-01", periods=800, freq="D"))
        dates[500] = dates[499]  # single duplicate timestamp -> IntervalAnalysis blocker
        amount = _sine_series(800, period=30, amplitude=10.0, base=100.0)
        df = pd.DataFrame({"date": dates, "amount": amount})

        result = score_readiness(df, "amount", "date")

        assert result.blockers  # a blocker exists (the duplicate timestamp)
        assert result.overall_score >= 80  # sub-scores are barely dented by 1 duplicate
        assert result.verdict == "not_ready"  # blocker overrides a high score

    def test_readiness_blockers_not_averaged(self):
        """Blockers are reported separately, not folded into the score."""
        dates = list(pd.date_range("2022-01-01", periods=800, freq="D"))
        dates[500] = dates[499]  # inject a blocker (duplicate timestamp)
        amount = _sine_series(800, period=30, amplitude=10.0, base=100.0)
        df = pd.DataFrame({"date": dates, "amount": amount})

        result = score_readiness(df, "amount", "date")

        # overall_score must equal the plain weighted sum of the four
        # sub-scores -- i.e. blockers are never subtracted/averaged into it.
        expected_overall = round(
            result.temporal_score * 0.30
            + result.interval_score * 0.20
            + result.target_score * 0.30
            + result.leakage_score * 0.20,
            2,
        )
        assert result.overall_score == expected_overall
        assert result.blockers  # the blocker still exists, reported separately
        assert result.verdict == "not_ready"

    def test_readiness_weights_sum_to_one(self):
        from data_quality_engine.phase2.readiness import scorer as scorer_module

        total = (
            scorer_module.WEIGHT_TEMPORAL
            + scorer_module.WEIGHT_INTERVAL
            + scorer_module.WEIGHT_TARGET
            + scorer_module.WEIGHT_LEAKAGE
        )
        assert total == pytest.approx(1.0)

    # -- robustness: never raises --------------------------------------

    def test_readiness_handles_empty_dataframe(self):
        result = score_readiness(pd.DataFrame(), "amount", "date")
        assert result.verdict == "not_ready"
        assert result.blockers

    def test_readiness_handles_missing_columns(self):
        df = pd.DataFrame({"other": [1, 2, 3]})
        result = score_readiness(df, "amount", "date")
        assert result.verdict == "not_ready"
        assert 0.0 <= result.overall_score <= 100.0


# ---------------------------------------------------------------------------
# 3.6 Report Integration
# ---------------------------------------------------------------------------

class TestReportIntegration:
    def _minimal_score_dict(self) -> dict:
        return {
            "data_quality_score": 88.0,
            "scorable_weight_fraction": 1.0,
            "dimension_scores": {},
            "dimensions_excluded": [],
            "privacy_risk": None,
        }

    def _readiness_dict(self, df: pd.DataFrame, target: str, date: str) -> dict:
        from dataclasses import asdict

        from data_quality_engine.phase2.readiness.scorer import score_readiness

        rs = score_readiness(df, target, date)
        d = asdict(rs)
        d["temporal"] = asdict(analyze_temporal_sufficiency(df, date))
        d["interval"] = asdict(analyze_interval_regularity(df, date))
        d["target"] = asdict(analyze_target_integrity(df, target))
        d["leakage"] = asdict(analyze_leakage_and_cardinality(df, target))
        return d

    def test_report_includes_ml_readiness_section(self):
        from data_quality_engine.engine.reporting.report_generator import build_report_data

        dates = pd.date_range("2022-01-01", periods=800, freq="D")
        amount = _sine_series(800, period=30, amplitude=10.0, base=100.0)
        df = pd.DataFrame({"date": dates, "amount": amount})
        readiness = self._readiness_dict(df, "amount", "date")

        report = build_report_data(
            filepath="fake.xlsx",
            sheet_name="Sheet1",
            df_shape=(800, 2),
            header_row=0,
            processing_time_seconds=1.0,
            classification={"date": "date", "amount": "measurement"},
            check_results_by_name={},
            pii_summary_by_column={},
            fuzzy_results=None,
            score=self._minimal_score_dict(),
            readiness=readiness,
        )

        assert "ml_readiness" in report
        block = report["ml_readiness"]
        assert block is not None
        assert block["verdict"] == "READY"
        assert "ML Model Readiness Assessment" in block["text"]
        assert "Temporal Sufficiency" in block["text"]
        assert "Interval Regularity" in block["text"]
        assert "Target Integrity" in block["text"]
        assert "Leakage & Cardinality" in block["text"]
        assert "Recommendations:" in block["text"]

    def test_report_omits_ml_readiness_when_not_provided(self):
        """No readiness data passed (M3 wasn't run) -> section is None,
        never a block of placeholder/'N/A' data."""
        from data_quality_engine.engine.reporting.report_generator import build_report_data

        report = build_report_data(
            filepath="fake.xlsx",
            sheet_name="Sheet1",
            df_shape=(10, 1),
            header_row=0,
            processing_time_seconds=1.0,
            classification={},
            check_results_by_name={},
            pii_summary_by_column={},
            fuzzy_results=None,
            score=self._minimal_score_dict(),
        )

        assert report["ml_readiness"] is None

    def test_report_ml_readiness_reflects_blockers(self):
        """A readiness result with blockers renders NOT_READY, and the
        blocking reasons make it into the report block."""
        from data_quality_engine.engine.reporting.report_generator import build_report_data

        s = pd.Series([50.0] * 100)  # near-constant target -> blocker
        dates = pd.date_range("2022-01-01", periods=100, freq="D")
        df = pd.DataFrame({"date": dates, "amount": s})
        readiness = self._readiness_dict(df, "amount", "date")

        report = build_report_data(
            filepath="fake.xlsx",
            sheet_name="Sheet1",
            df_shape=(100, 2),
            header_row=0,
            processing_time_seconds=1.0,
            classification={"date": "date", "amount": "measurement"},
            check_results_by_name={},
            pii_summary_by_column={},
            fuzzy_results=None,
            score=self._minimal_score_dict(),
            readiness=readiness,
        )

        block = report["ml_readiness"]
        assert block["verdict"] == "NOT_READY"
        assert block["blockers"]
        assert block["target"]["status"] == "[BLOCKED]"
