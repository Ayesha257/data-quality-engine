"""Column role classification.

Added to fix a Task 3 bug: outlier detection was running IQR/KNN on any
column with at least one numeric value, including identifier-like columns
(invoice numbers, customer codes, phone numbers, postcodes) where outlier
stats are meaningless.

classify_columns() assigns each column exactly one role:
    "identifier"  - invoice/customer/order codes, reference numbers
    "measurement" - continuous/discrete numeric values suitable for outliers
    "categorical" - low-cardinality text (status, category, city, ...)
    "date"        - date/time values
    "pii"         - personally identifiable information
    "free_text"   - high-cardinality text that isn't an identifier (notes,
                    descriptions, addresses-as-comments, etc.)

Column names in real ERP exports are inconsistent (e.g. "Dimension (CUS)",
"inv.no"), so classification never relies on name matching alone -- it is
always combined with cardinality (unique / non-null-count) and, where
useful, dtype / parseability signals.

PII classification reuses the existing detection logic in
engine/pii/detect_pii.py (_infer_expected_types + detect_pii_in_series)
rather than duplicating any regex/heuristics here.
"""

from __future__ import annotations

import re

import pandas as pd

from data_quality_engine.engine.pii.detect_pii import (
    _infer_expected_types,
    detect_pii_in_series,
)

ROLE_IDENTIFIER = "identifier"
ROLE_MEASUREMENT = "measurement"
ROLE_CATEGORICAL = "categorical"
ROLE_DATE = "date"
ROLE_PII = "pii"
ROLE_FREE_TEXT = "free_text"

# Column-name hints for identifier-like fields. Deliberately broad; final
# call still depends on cardinality/dtype so name matches alone can't
# misclassify a genuinely continuous numeric column.
_IDENTIFIER_NAME_RE = re.compile(
    r"(_id\b|\bid\b|\bcode\b|code$|\bno\b|no\.|number|\binv\b|invoice|"
    r"\bref\b|reference|\bsku\b|\bpo\b|order\s*no)",
    re.I,
)

# Cap on rows sampled for PII confirmation / date parsing -- classification
# should stay fast even on large ERP sheets (Product Data ~75k rows).
_SAMPLE_SIZE = 200

# Ratio threshold above which an object/string column with short, code-like
# values is treated as an identifier even without a name-pattern match.
_HIGH_CARDINALITY_RATIO = 0.9

# Ratio threshold below which text is considered categorical rather than
# free text.
_LOW_CARDINALITY_RATIO = 0.5


def _cardinality_ratio(non_null: pd.Series) -> float:
    n = len(non_null)
    if n == 0:
        return 0.0
    return float(non_null.nunique()) / float(n)


def _looks_like_code(value: object) -> bool:
    """Short, space-free-ish token -> looks like a code/identifier value."""
    s = str(value).strip()
    if not s:
        return False
    if len(s) > 20:
        return False
    if s.count(" ") > 1:
        return False
    return True


def _is_pii_column(series: pd.Series, col_name: str | None) -> bool:
    """Reuse existing PII detection: name hints + a value-level check."""
    hints = _infer_expected_types(col_name)
    if not hints:
        return False
    non_null = series.dropna()
    if non_null.empty:
        return False
    sample = non_null.astype(str).head(_SAMPLE_SIZE)
    sample_series = pd.Series(sample.to_numpy(), name=col_name)
    summary = detect_pii_in_series(sample_series)
    return bool(summary.get("rows_with_pii", 0) > 0)


def _is_date_column(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if not pd.api.types.is_object_dtype(series) and not pd.api.types.is_string_dtype(series):
        return False
    non_null = series.dropna()
    if non_null.empty:
        return False
    sample = non_null.astype(str).head(_SAMPLE_SIZE)
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    ratio = float(parsed.notna().sum()) / float(len(sample))
    return ratio >= 0.8


def _is_identifier_column(series: pd.Series, col_name: str | None, non_null: pd.Series) -> bool:
    name = str(col_name) if col_name is not None else ""
    if _IDENTIFIER_NAME_RE.search(name):
        return True

    # Name-independent fallback for inconsistently-named code columns
    # (e.g. "Dimension (CUS)"). Only applies to text-like columns: numeric
    # dtype columns with high cardinality are more likely genuine
    # measurements (prices, weights) than identifiers.
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return False
    if non_null.empty:
        return False
    ratio = _cardinality_ratio(non_null)
    if ratio < _HIGH_CARDINALITY_RATIO:
        return False
    sample = non_null.astype(str).head(_SAMPLE_SIZE)

    # Guard against object-dtype columns that hold plain numbers (common
    # after generic header-detection loading, e.g. "Sales" read as object
    # with float/int values). Those are genuine measurements, not codes,
    # even though they're unique and "short". Codes are expected to be
    # non-numeric (letters/dashes) or the name must already have matched
    # above.
    numeric_ratio = float(pd.to_numeric(sample, errors="coerce").notna().mean())
    if numeric_ratio >= 0.8:
        return False

    return bool(sample.map(_looks_like_code).mean() >= 0.8)


def _is_measurement_column(series: pd.Series, non_null: pd.Series) -> bool:
    if non_null.empty:
        return False
    numeric = pd.to_numeric(non_null, errors="coerce")
    ratio = float(numeric.notna().sum()) / float(len(non_null))
    return ratio >= 0.9


def classify_column(series: pd.Series, col_name: str | None = None) -> str:
    """Classify a single column into one of the six roles."""
    name = col_name if col_name is not None else getattr(series, "name", None)
    non_null = series.dropna()

    if non_null.empty:
        return ROLE_CATEGORICAL

    if _is_pii_column(series, name):
        return ROLE_PII

    if _is_date_column(series):
        return ROLE_DATE

    if _is_identifier_column(series, name, non_null):
        return ROLE_IDENTIFIER

    if _is_measurement_column(series, non_null):
        return ROLE_MEASUREMENT

    # Remaining columns are text-like: split categorical vs free_text by
    # cardinality ratio.
    ratio = _cardinality_ratio(non_null)
    if ratio <= _LOW_CARDINALITY_RATIO:
        return ROLE_CATEGORICAL
    return ROLE_FREE_TEXT


def classify_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    Classify every column of df into a role:
    "identifier", "measurement", "categorical", "date", "pii", "free_text".
    """
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    roles: dict[str, str] = {}
    for col in df.columns:
        roles[col] = classify_column(df[col], col)
    return roles
