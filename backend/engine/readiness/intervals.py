"""
Phase 2 — M3.2: Interval Regularity (PHASE2_PLAN.md, "3.2 Interval Regularity").

Prophet needs regularly-spaced observations. This module infers the
implied frequency of a date column, counts how many expected periods are
missing, finds the largest gap, flags duplicate timestamps, and rolls
all of that into a single 0-1 regularity score.

Never raises: bad input always comes back as an IntervalAnalysis with
`sufficient=False` and an explanatory `blockers` entry, not an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# pandas frequency aliases used to build the "expected" calendar for each
# inferred cadence.
_FREQ_ALIAS = {
    "daily": "D",
    "weekly": "W",
    "monthly": "MS",
    "quarterly": "QS",
    "yearly": "YS",
}


@dataclass
class IntervalAnalysis:
    inferred_frequency: str
    observations_expected: int
    observations_actual: int
    missing_intervals: int
    gap_size_max_days: int
    duplicate_timestamps: int
    regularity_score: float  # 0-1
    sufficient: bool
    blockers: list[str] = field(default_factory=list)


def _infer_frequency(sorted_unique_dates: pd.Series) -> str:
    """Same tolerant median-gap inference used in temporal.py. Duplicated
    intentionally so intervals.py has no dependency on temporal.py and
    each module stays independently testable."""
    if len(sorted_unique_dates) < 2:
        return "unknown"
    diffs_days = sorted_unique_dates.diff().dropna().dt.days
    if diffs_days.empty:
        return "unknown"
    median = float(diffs_days.median())
    if median <= 1.5:
        return "daily"
    if 5 <= median <= 9:
        return "weekly"
    if 25 <= median <= 35:
        return "monthly"
    if 80 <= median <= 100:
        return "quarterly"
    if 350 <= median <= 380:
        return "yearly"
    return "irregular"


def analyze_interval_regularity(
    df: pd.DataFrame,
    date_column: str,
) -> IntervalAnalysis:
    """
    Check if observations in `date_column` are regularly spaced.

    Prophet needs regular intervals; gaps and duplicates break
    forecasting. Never raises -- bad input (missing df/column, empty
    frame, unparseable dates) is reported via `blockers` with
    `sufficient=False` instead of an exception.
    """
    blockers: list[str] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        blockers.append("Input data is empty or missing; cannot assess interval regularity.")
        return IntervalAnalysis("unknown", 0, 0, 0, 0, 0, 0.0, False, blockers)

    if date_column not in df.columns:
        blockers.append(f"Date column '{date_column}' not found in data.")
        return IntervalAnalysis("unknown", 0, 0, 0, 0, 0, 0.0, False, blockers)

    parsed = pd.to_datetime(df[date_column], errors="coerce")
    valid = parsed.dropna()

    if valid.empty:
        blockers.append(f"Date column '{date_column}' has no valid, parseable date values.")
        return IntervalAnalysis("unknown", 0, 0, 0, 0, 0, 0.0, False, blockers)

    observations_actual = int(len(valid))
    unique_dates = valid.drop_duplicates()
    duplicate_timestamps = int(observations_actual - len(unique_dates))

    sorted_unique = pd.Series(sorted(unique_dates))
    inferred_frequency = _infer_frequency(sorted_unique)

    gap_size_max_days = 0
    if len(sorted_unique) >= 2:
        gaps = sorted_unique.diff().dropna().dt.days
        if not gaps.empty:
            gap_size_max_days = int(gaps.max())

    alias = _FREQ_ALIAS.get(inferred_frequency)
    if alias and len(sorted_unique) >= 2:
        expected_index = pd.date_range(start=sorted_unique.iloc[0], end=sorted_unique.iloc[-1], freq=alias)
        observations_expected = int(len(expected_index))
    else:
        # Irregular/unknown/single-point cadence: nothing reliable to
        # compare against, so treat what we saw as what was expected.
        observations_expected = int(len(sorted_unique))

    missing_intervals = max(0, observations_expected - len(sorted_unique))

    completeness = (
        min(1.0, len(sorted_unique) / observations_expected) if observations_expected > 0 else 1.0
    )
    duplicate_penalty = duplicate_timestamps / observations_actual if observations_actual else 0.0
    regularity_score = max(0.0, min(1.0, completeness - duplicate_penalty))

    if duplicate_timestamps > 0:
        blockers.append(
            f"{duplicate_timestamps} duplicate timestamp(s) found in '{date_column}'."
        )

    return IntervalAnalysis(
        inferred_frequency=inferred_frequency,
        observations_expected=observations_expected,
        observations_actual=observations_actual,
        missing_intervals=missing_intervals,
        gap_size_max_days=gap_size_max_days,
        duplicate_timestamps=duplicate_timestamps,
        regularity_score=round(regularity_score, 4),
        sufficient=len(blockers) == 0,
        blockers=blockers,
    )
