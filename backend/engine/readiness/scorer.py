"""
Phase 2 — M3.5: Readiness Scorer (PHASE2_PLAN.md, "3.5 Readiness Scorer").

Combines the four M3 analyses (temporal, interval, target, leakage) into
one weighted Prophet-readiness score and verdict. Blockers from every
sub-analysis are reported separately in `ReadinessScore.blockers` and
are never averaged into `overall_score` -- a high score with a blocker
is still `not_ready`.

Weights (PHASE2_PLAN.md 3.5):
    Temporal:    30%  (must have enough history)
    Interval:    20%  (regularity matters)
    Target:      30%  (quality of what we're forecasting)
    Leakage:     20%  (data integrity)

Verdict:
    >= 80 + no blockers  -> "ready"
    >= 60 + no blockers  -> "caution"
    < 60  OR has blockers -> "not_ready"

Never raises: any unexpected failure while combining the four
sub-analyses is caught and reported as a "not_ready" ReadinessScore with
the error recorded in `blockers`, not an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backend.engine.readiness.intervals import (
    IntervalAnalysis,
    analyze_interval_regularity,
)
from backend.engine.readiness.leakage import (
    LeakageAnalysis,
    analyze_leakage_and_cardinality,
)
from backend.engine.readiness.target import (
    NEAR_ZERO_VARIANCE,
    TargetAnalysis,
    analyze_target_integrity,
)
from backend.engine.readiness.temporal import (
    MIN_OBSERVATIONS,
    MIN_SEASONAL_CYCLES,
    TemporalAnalysis,
    analyze_temporal_sufficiency,
)

WEIGHT_TEMPORAL = 0.30
WEIGHT_INTERVAL = 0.20
WEIGHT_TARGET = 0.30
WEIGHT_LEAKAGE = 0.20

READY_THRESHOLD = 80.0
CAUTION_THRESHOLD = 60.0

_LEAKAGE_SUBSCORE = {"none": 100.0, "warning": 50.0, "blocker": 0.0}


@dataclass
class ReadinessScore:
    overall_score: float  # 0-100
    temporal_score: float
    interval_score: float
    target_score: float
    leakage_score: float

    verdict: str  # 'ready' | 'caution' | 'not_ready'
    blockers: list[str] = field(default_factory=list)  # Hard failures
    warnings: list[str] = field(default_factory=list)  # Soft issues
    recommendations: list[str] = field(default_factory=list)  # Actions to improve


def _temporal_subscore(temporal: TemporalAnalysis) -> float:
    obs_component = min(1.0, temporal.total_observations / MIN_OBSERVATIONS) * 50.0
    cycles_component = min(1.0, temporal.seasonal_cycles_detected / MIN_SEASONAL_CYCLES) * 50.0
    return round(max(0.0, min(100.0, obs_component + cycles_component)), 2)


def _interval_subscore(interval: IntervalAnalysis) -> float:
    return round(max(0.0, min(100.0, interval.regularity_score * 100.0)), 2)


def _target_subscore(target: TargetAnalysis) -> float:
    score = 100.0
    score -= min(score, target.null_pct)
    score -= min(score, target.zero_pct * 0.5)
    score -= min(score, target.outlier_pct)
    if target.variance < NEAR_ZERO_VARIANCE:
        score = min(score, 10.0)
    return round(max(0.0, min(100.0, score)), 2)


def _leakage_subscore(leakage: LeakageAnalysis) -> float:
    return _LEAKAGE_SUBSCORE.get(leakage.concern_level, 100.0)


def _collect_warnings(
    temporal: TemporalAnalysis,
    interval: IntervalAnalysis,
    target: TargetAnalysis,
    leakage: LeakageAnalysis,
) -> list[str]:
    warnings: list[str] = []

    if interval.missing_intervals > 0:
        warnings.append(
            f"{interval.missing_intervals} missing interval(s) detected for "
            f"'{interval.inferred_frequency}' cadence."
        )

    if 10.0 < target.null_pct <= 30.0:
        warnings.append(f"{target.null_pct}% of '{target.column_name}' values are null.")

    if 20.0 < target.outlier_pct <= 30.0:
        warnings.append(f"{target.outlier_pct}% of '{target.column_name}' values are outliers.")

    if leakage.concern_level == "warning":
        if leakage.high_cardinality_features:
            warnings.append(
                "High-cardinality feature(s) with little learnable pattern: "
                + ", ".join(leakage.high_cardinality_features)
            )
        if leakage.identifier_features:
            warnings.append(
                "Identifier-like column(s) detected (unlikely to help forecasting): "
                + ", ".join(leakage.identifier_features)
            )

    return warnings


def _collect_recommendations(
    temporal: TemporalAnalysis,
    interval: IntervalAnalysis,
    target: TargetAnalysis,
    leakage: LeakageAnalysis,
    blockers: list[str],
    warnings: list[str],
) -> list[str]:
    recommendations: list[str] = []

    if temporal.total_observations < MIN_OBSERVATIONS:
        recommendations.append(
            f"Collect at least {MIN_OBSERVATIONS - temporal.total_observations} more "
            "observation(s) before forecasting."
        )
    if temporal.seasonal_cycles_detected < MIN_SEASONAL_CYCLES:
        recommendations.append(
            f"Collect more historical data to cover at least {MIN_SEASONAL_CYCLES} full "
            f"seasonal cycles (currently {temporal.seasonal_cycles_detected})."
        )
    if interval.duplicate_timestamps > 0:
        recommendations.append("Remove or resolve duplicate timestamps before forecasting.")
    if interval.missing_intervals > 0:
        recommendations.append(
            "Fill or explain missing periods in the date index (interpolate, or confirm "
            "they reflect real gaps such as closures)."
        )
    if target.null_pct > 10.0:
        recommendations.append(
            f"Reduce nulls in '{target.column_name}' (currently {target.null_pct}%)."
        )
    if target.variance < NEAR_ZERO_VARIANCE:
        recommendations.append(
            f"'{target.column_name}' is effectively constant; forecasting isn't meaningful "
            "until it varies."
        )
    if target.outlier_pct > 20.0:
        recommendations.append(
            f"Review outliers in '{target.column_name}' before modeling "
            f"(currently {target.outlier_pct}%)."
        )
    if leakage.perfect_correlation_features:
        recommendations.append(
            "Remove or investigate feature(s) perfectly correlated with the target "
            "(possible leakage): " + ", ".join(leakage.perfect_correlation_features)
        )
    if leakage.high_cardinality_features or leakage.identifier_features:
        recommendations.append(
            "Drop or encode high-cardinality/identifier columns before modeling."
        )

    if not blockers and not warnings and not recommendations:
        recommendations.append("Data meets baseline Prophet readiness preconditions.")

    return recommendations


def score_readiness(
    df: pd.DataFrame,
    target_column: str,
    date_column: str,
) -> ReadinessScore:
    """
    Compute Prophet readiness score.

    Score = weighted sum of sub-scores. Blockers are reported separately
    (not averaged into the score). Never raises -- any unexpected failure
    is caught and returned as a "not_ready" ReadinessScore with the error
    described in `blockers`.
    """
    try:
        temporal = analyze_temporal_sufficiency(df, date_column)
        interval = analyze_interval_regularity(df, date_column)
        target = analyze_target_integrity(df, target_column)
        leakage = analyze_leakage_and_cardinality(df, target_column)

        temporal_score = _temporal_subscore(temporal)
        interval_score = _interval_subscore(interval)
        target_score = _target_subscore(target)
        leakage_score = _leakage_subscore(leakage)

        overall_score = round(
            temporal_score * WEIGHT_TEMPORAL
            + interval_score * WEIGHT_INTERVAL
            + target_score * WEIGHT_TARGET
            + leakage_score * WEIGHT_LEAKAGE,
            2,
        )

        blockers: list[str] = list(temporal.blockers) + list(interval.blockers) + list(target.blockers)
        if leakage.concern_level == "blocker":
            blockers.append(
                "Feature(s) perfectly correlated with target (possible leakage): "
                + ", ".join(leakage.perfect_correlation_features)
            )

        warnings = _collect_warnings(temporal, interval, target, leakage)
        recommendations = _collect_recommendations(
            temporal, interval, target, leakage, blockers, warnings
        )

        has_blockers = len(blockers) > 0
        if overall_score >= READY_THRESHOLD and not has_blockers:
            verdict = "ready"
        elif overall_score >= CAUTION_THRESHOLD and not has_blockers:
            verdict = "caution"
        else:
            verdict = "not_ready"

        return ReadinessScore(
            overall_score=overall_score,
            temporal_score=temporal_score,
            interval_score=interval_score,
            target_score=target_score,
            leakage_score=leakage_score,
            verdict=verdict,
            blockers=blockers,
            warnings=warnings,
            recommendations=recommendations,
        )
    except Exception as exc:  # noqa: BLE001 - readiness scoring must never raise on the caller
        return ReadinessScore(
            overall_score=0.0,
            temporal_score=0.0,
            interval_score=0.0,
            target_score=0.0,
            leakage_score=0.0,
            verdict="not_ready",
            blockers=[f"Readiness scoring failed unexpectedly: {exc}"],
            warnings=[],
            recommendations=["Investigate the error and re-run the readiness assessment."],
        )
