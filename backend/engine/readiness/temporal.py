"""
Phase 2 — M3.1: Temporal Analysis (PHASE2_PLAN.md, "3.1 Temporal Analysis").

Checks whether a dataset has enough history for Prophet forecasting:
enough total observations, and at least two full seasonal cycles for
the frequency the data implies.

Never raises: a missing/empty/unparseable date column always comes back
as a TemporalAnalysis with `sufficient=False` and an explanatory entry
in `blockers`, not an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Prophet preconditions (PHASE2_PLAN.md 3.1)
MIN_OBSERVATIONS = 30
MIN_SEASONAL_CYCLES = 2

# Approximate calendar-day length of "one seasonal cycle" for each
# inferred frequency. Yearly seasonality (365 days) is the standard
# assumption for business/forecasting data at every sub-yearly grain;
# for yearly-grain data itself there is no shorter cycle to measure
# against, so a "cycle" there is simply one full year of span too.
SEASONAL_PERIOD_DAYS = {
    "daily": 365,
    "weekly": 365,
    "monthly": 365,
    "quarterly": 365,
    "yearly": 365,
}


@dataclass
class TemporalAnalysis:
    total_observations: int
    date_range_days: int
    implied_frequency: str  # 'daily', 'weekly', 'monthly', etc.
    seasonal_cycles_detected: int  # count of full cycles
    sufficient: bool
    blockers: list[str] = field(default_factory=list)


def _infer_frequency(sorted_unique_dates: pd.Series) -> str:
    """Infer a coarse frequency label from the median gap between sorted,
    de-duplicated observation dates. Deliberately tolerant of small
    real-world jitter (e.g. month-end vs. month-start dates) rather than
    requiring an exact match."""
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


def analyze_temporal_sufficiency(
    df: pd.DataFrame,
    date_column: str,
    frequency: str | None = None,
) -> TemporalAnalysis:
    """
    Check temporal preconditions for Prophet.

    Rules (PHASE2_PLAN.md 3.1):
        - Need at least 2 full seasonal cycles (e.g., 2 years of daily data)
        - Minimum 30 observations
        - Date column must be valid and monotonic

    Args:
        df: input frame.
        date_column: name of the column holding observation dates.
        frequency: optional override ('daily'/'weekly'/'monthly'/
            'quarterly'/'yearly'); if omitted, the frequency is inferred
            from the data itself.

    Returns:
        TemporalAnalysis. Never raises -- bad input (missing df/column,
        empty frame, unparseable dates) is reported via `blockers` with
        `sufficient=False` instead of an exception.
    """
    blockers: list[str] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        blockers.append("Input data is empty or missing; cannot assess temporal sufficiency.")
        return TemporalAnalysis(0, 0, "unknown", 0, False, blockers)

    if date_column not in df.columns:
        blockers.append(f"Date column '{date_column}' not found in data.")
        return TemporalAnalysis(0, 0, "unknown", 0, False, blockers)

    parsed = pd.to_datetime(df[date_column], errors="coerce", format="mixed")
    valid = parsed.dropna()

    if valid.empty:
        blockers.append(f"Date column '{date_column}' has no valid, parseable date values.")
        return TemporalAnalysis(0, 0, "unknown", 0, False, blockers)

    total_observations = int(len(valid))
    sorted_dates = valid.sort_values().reset_index(drop=True)
    unique_sorted_dates = pd.Series(sorted(sorted_dates.unique()))
    date_range_days = int((sorted_dates.iloc[-1] - sorted_dates.iloc[0]).days)

    implied_frequency = frequency or _infer_frequency(unique_sorted_dates)

    period_days = SEASONAL_PERIOD_DAYS.get(implied_frequency)
    if period_days:
        seasonal_cycles_detected = int(date_range_days // period_days)
    else:
        # Irregular/unknown cadence: no reliable seasonal period to
        # measure cycles against.
        seasonal_cycles_detected = 0

    if total_observations < MIN_OBSERVATIONS:
        blockers.append(
            f"Only {total_observations} observation(s) found "
            f"(minimum {MIN_OBSERVATIONS} required)."
        )

    if seasonal_cycles_detected < MIN_SEASONAL_CYCLES:
        blockers.append(
            f"Only {seasonal_cycles_detected} full seasonal cycle(s) detected for "
            f"'{implied_frequency}' data (minimum {MIN_SEASONAL_CYCLES} required)."
        )

    if not sorted_dates.is_monotonic_increasing:
        # sorted_dates is sorted by construction, so this only ever flags
        # a genuinely degenerate comparison (e.g. tz-mixed values); kept
        # as a defensive check, not expected to fire in practice.
        blockers.append(f"Date column '{date_column}' could not be ordered consistently.")

    return TemporalAnalysis(
        total_observations=total_observations,
        date_range_days=date_range_days,
        implied_frequency=implied_frequency,
        seasonal_cycles_detected=seasonal_cycles_detected,
        sufficient=len(blockers) == 0,
        blockers=blockers,
    )
