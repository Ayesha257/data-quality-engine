"""
Phase 2 — M3.4: Leakage & Cardinality (PHASE2_PLAN.md, "3.4 Leakage & Cardinality").

Detects features that would silently invalidate a forecasting model:
columns that (near-)perfectly correlate with the target (leakage), and
columns with too many unique values to carry any learnable pattern
(high cardinality / identifier columns).

Checks (PHASE2_PLAN.md 3.4):
    - Correlation with target: flag if abs(corr) > 0.99
    - Cardinality: flag if unique_count / row_count > 0.95
    - ID columns: name matches *_id / *_no / *_code (or id/no/code alone),
      combined with high cardinality

Never raises: bad input (missing df/column, empty frame) always comes
back as an empty, non-alarming LeakageAnalysis (`concern_level='none'`)
rather than an exception.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

import pandas as pd

CORRELATION_THRESHOLD = 0.99
CARDINALITY_RATIO_THRESHOLD = 0.95

# Matches column names like customer_id, OrderNo, order_no, ProductCode,
# product_code, or bare id/no/code -- case-insensitive.
_ID_NAME_PATTERN = re.compile(
    r"(^id$|_id$|^no$|_no$|^code$|_code$|^id_|^no_|^code_)",
    re.IGNORECASE,
)


@dataclass
class LeakageAnalysis:
    perfect_correlation_features: list[str] = field(default_factory=list)
    high_cardinality_features: list[str] = field(default_factory=list)
    identifier_features: list[str] = field(default_factory=list)
    concern_level: str = "none"  # 'none' | 'warning' | 'blocker'


def analyze_leakage_and_cardinality(
    df: pd.DataFrame,
    target_column: str,
) -> LeakageAnalysis:
    """
    Detect features that leak information about, or carry no learnable
    pattern relative to, `target_column`.

    Never raises -- bad input (missing df/column, empty frame) yields an
    empty, non-alarming LeakageAnalysis instead of an exception.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return LeakageAnalysis()

    if target_column not in df.columns:
        return LeakageAnalysis()

    row_count = len(df)
    target_numeric = pd.to_numeric(df[target_column], errors="coerce")
    target_is_numeric = target_numeric.notna().any()

    perfect_correlation_features: list[str] = []
    high_cardinality_features: list[str] = []
    identifier_features: list[str] = []

    for col in df.columns:
        if col == target_column:
            continue

        series = df[col]

        # -- Correlation with target -----------------------------------
        if target_is_numeric:
            feature_numeric = pd.to_numeric(series, errors="coerce")
            if feature_numeric.notna().sum() >= 2:
                try:
                    # A constant feature/target has zero standard deviation,
                    # which makes correlation mathematically undefined --
                    # pandas/numpy compute it as a harmless NaN with a
                    # RuntimeWarning; suppress the warning, keep the NaN
                    # (filtered out by pd.notna() below).
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", category=RuntimeWarning)
                        corr = feature_numeric.corr(target_numeric)
                except Exception:  # noqa: BLE001 - correlation must never crash this check
                    corr = None
                if corr is not None and pd.notna(corr) and abs(corr) > CORRELATION_THRESHOLD:
                    perfect_correlation_features.append(col)

        # -- Cardinality --------------------------------------------------
        unique_count = series.nunique(dropna=True)
        is_high_cardinality = row_count > 0 and (unique_count / row_count) > CARDINALITY_RATIO_THRESHOLD
        if is_high_cardinality:
            high_cardinality_features.append(col)

        # -- ID-like column name + high cardinality -----------------------
        if is_high_cardinality and _ID_NAME_PATTERN.search(str(col)):
            identifier_features.append(col)

    if perfect_correlation_features:
        concern_level = "blocker"
    elif high_cardinality_features or identifier_features:
        concern_level = "warning"
    else:
        concern_level = "none"

    return LeakageAnalysis(
        perfect_correlation_features=perfect_correlation_features,
        high_cardinality_features=high_cardinality_features,
        identifier_features=identifier_features,
        concern_level=concern_level,
    )
