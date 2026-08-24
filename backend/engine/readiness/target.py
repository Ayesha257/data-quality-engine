"""
Phase 2 — M3.3: Target Integrity (PHASE2_PLAN.md, "3.3 Target Integrity").

Checks whether the column the client wants to forecast is actually
usable: how much of it is missing or zero, whether it varies at all,
and how many values are extreme outliers.

Blocker thresholds (PHASE2_PLAN.md 3.3):
    - nulls:    warn  > 10%,  block > 30%
    - zeros:    block > 50%  (no signal left to forecast)
    - variance: block when near-zero (constant series is unforecastable)
    - outliers: warn  > 20%,  block > 30%

Never raises: bad input always comes back as a TargetAnalysis with
`sufficient=False` and an explanatory `blockers` entry, not an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

NULL_PCT_BLOCK = 30.0
ZERO_PCT_BLOCK = 50.0
OUTLIER_PCT_BLOCK = 30.0
NEAR_ZERO_VARIANCE = 1e-9
# Fraction of non-null values that must parse as numeric before the
# column is treated as numeric (allows a handful of stray text cells
# without failing the whole column).
NUMERIC_COERCION_MIN_RATIO = 0.99
# Any non-finite (+inf/-inf) value blocks forecasting outright -- Prophet
# (and every stats function downstream) breaks on infinities, and their
# presence usually signals a source-data error (e.g. a #DIV/0! formula
# result Excel/pandas surfaces as inf) rather than a real observation.
INFINITE_VALUES_BLOCK = 0


def _empty_result(column_name: str, blockers: list[str]) -> "TargetAnalysis":
    return TargetAnalysis(
        column_name=column_name,
        data_type="unknown",
        null_count=0,
        null_pct=0.0,
        zero_count=0,
        zero_pct=0.0,
        infinite_count=0,
        infinite_pct=0.0,
        outlier_count=0,
        outlier_pct=0.0,
        variance=0.0,
        mean=0.0,
        min_value=0.0,
        max_value=0.0,
        sufficient=False,
        blockers=blockers,
    )


@dataclass
class TargetAnalysis:
    column_name: str
    data_type: str
    null_count: int
    null_pct: float
    zero_count: int
    zero_pct: float
    infinite_count: int
    infinite_pct: float
    outlier_count: int
    outlier_pct: float
    variance: float
    mean: float
    min_value: float
    max_value: float

    sufficient: bool
    blockers: list[str] = field(default_factory=list)


def analyze_target_integrity(
    df: pd.DataFrame,
    target_column: str,
) -> TargetAnalysis:
    """
    Check if `target_column` is suitable for forecasting.

    Never raises -- bad input (missing df/column, empty frame,
    non-numeric target) is reported via `blockers` with
    `sufficient=False` instead of an exception.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return _empty_result(
            target_column or "",
            ["Input data is empty or missing; cannot assess target integrity."],
        )

    if target_column not in df.columns:
        return _empty_result(target_column, [f"Target column '{target_column}' not found in data."])

    series = df[target_column]
    total = int(len(series))
    null_count = int(series.isna().sum())
    null_pct = round(100.0 * null_count / total, 2) if total else 0.0

    non_null = series.dropna()
    coerced = pd.to_numeric(non_null, errors="coerce")
    coerced_valid = coerced.dropna()

    is_numeric = len(non_null) > 0 and (len(coerced_valid) / len(non_null)) >= NUMERIC_COERCION_MIN_RATIO
    data_type = str(series.dtype) if pd.api.types.is_numeric_dtype(series) else (
        "numeric (coerced)" if is_numeric else str(series.dtype)
    )

    all_numeric = coerced_valid if is_numeric else pd.Series(dtype="float64")

    # Non-finite values (+inf/-inf) break every statistic below (variance
    # comes back NaN, which silently bypasses the near-zero-variance
    # blocker) and would break Prophet outright, so they are excluded from
    # the "clean" values used for stats and counted/flagged separately
    # instead of being treated as ordinary observations.
    is_finite_mask = np.isfinite(all_numeric) if len(all_numeric) else pd.Series(dtype=bool)
    infinite_count = int((~is_finite_mask).sum()) if len(all_numeric) else 0
    infinite_pct = round(100.0 * infinite_count / len(all_numeric), 2) if len(all_numeric) else 0.0
    values = all_numeric[is_finite_mask] if len(all_numeric) else all_numeric

    blockers: list[str] = []
    if not is_numeric:
        blockers.append(
            f"Target column '{target_column}' is not numeric or reliably convertible to numeric."
        )

    if infinite_count > INFINITE_VALUES_BLOCK:
        blockers.append(
            f"{infinite_count} non-finite value(s) (infinity) found in '{target_column}' -- "
            "likely a source-data error (e.g. a #DIV/0! formula result); "
            "fix or remove these before forecasting."
        )

    zero_count = int((values == 0).sum()) if len(values) else 0
    zero_pct = round(100.0 * zero_count / len(values), 2) if len(values) else 0.0

    mean = float(values.mean()) if len(values) else 0.0
    variance = float(values.var()) if len(values) > 1 else 0.0
    min_value = float(values.min()) if len(values) else 0.0
    max_value = float(values.max()) if len(values) else 0.0

    outlier_count = 0
    if len(values) >= 4:
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outlier_count = int(((values < lower) | (values > upper)).sum())
    outlier_pct = round(100.0 * outlier_count / len(values), 2) if len(values) else 0.0

    if null_pct > NULL_PCT_BLOCK:
        blockers.append(
            f"{null_pct}% of target values are null (blocks forecasting above {NULL_PCT_BLOCK}%)."
        )

    if zero_pct > ZERO_PCT_BLOCK:
        blockers.append(
            f"{zero_pct}% of target values are zero -- little/no signal to forecast "
            f"(blocks above {ZERO_PCT_BLOCK}%)."
        )

    if is_numeric and len(values) > 1 and variance < NEAR_ZERO_VARIANCE:
        blockers.append(
            f"Target column '{target_column}' has near-zero variance ({variance}); "
            "the series is effectively constant and unforecastable."
        )

    if outlier_pct > OUTLIER_PCT_BLOCK:
        blockers.append(
            f"{outlier_pct}% of target values are outliers (blocks above {OUTLIER_PCT_BLOCK}%)."
        )

    return TargetAnalysis(
        column_name=target_column,
        data_type=data_type,
        null_count=null_count,
        null_pct=null_pct,
        zero_count=zero_count,
        zero_pct=zero_pct,
        infinite_count=infinite_count,
        infinite_pct=infinite_pct,
        outlier_count=outlier_count,
        outlier_pct=outlier_pct,
        variance=variance,
        mean=mean,
        min_value=min_value,
        max_value=max_value,
        sufficient=len(blockers) == 0,
        blockers=blockers,
    )