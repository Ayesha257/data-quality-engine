"""Semantic Column Classifier & Reusable Column Metadata Layer.

Categorizes every column into rich business semantic roles:
- primary_key: Main row primary identifier
- business_key: Domain business key (Customer No., SKU)
- foreign_key: Reference key pointing to master data
- identifier: Reference numbers, code tokens
- category: Low-cardinality text (City, Country, Category, Status)
- measurement: Continuous/discrete numeric values (Qty, Weight)
- date: Date/time timestamps
- boolean: Flag values (True/False, Y/N, 1/0)
- free_text: High-cardinality descriptive text (Notes, Descriptions)
- contact: Phone, Email, Fax, Contact numbers
- financial: Price, Revenue, Tax, Margin, Discount, Amount
- pii: Personally Identifiable Information

Provides a reusable ColumnMetadata layer containing:
- semantic_role
- uniqueness_confidence
- business_importance
- sensitivity_level
- quality_score
- validation_rules
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backend.engine.pii.detect_pii import (
    _infer_expected_types,
    detect_pii_in_series,
)

# Rich Semantic Roles
ROLE_PRIMARY_KEY = "primary_key"
ROLE_BUSINESS_KEY = "business_key"
ROLE_FOREIGN_KEY = "foreign_key"
ROLE_IDENTIFIER = "identifier"
ROLE_CATEGORY = "category"
ROLE_MEASUREMENT = "measurement"
ROLE_DATE = "date"
ROLE_BOOLEAN = "boolean"
ROLE_FREE_TEXT = "free_text"
ROLE_CONTACT = "contact"
ROLE_FINANCIAL = "financial"
ROLE_PII = "pii"

# Legacy Super-Roles for 100% Backward Compatibility
ROLE_CATEGORICAL = "categorical"

LEGACY_ROLE_MAP = {
    ROLE_PRIMARY_KEY: "identifier",
    ROLE_BUSINESS_KEY: "identifier",
    ROLE_FOREIGN_KEY: "identifier",
    ROLE_IDENTIFIER: "identifier",
    ROLE_CATEGORY: "categorical",
    ROLE_MEASUREMENT: "measurement",
    ROLE_FINANCIAL: "measurement",
    ROLE_DATE: "date",
    ROLE_BOOLEAN: "categorical",
    ROLE_FREE_TEXT: "free_text",
    ROLE_CONTACT: "pii",
    ROLE_PII: "pii",
}

_PRIMARY_KEY_NAME_RE = re.compile(
    r"(customer\s*no\.?|supplier\s*(code|no\.?)|product(\s*code)?|"
    r"invoice\s*(no\.?|number)|order\s*(no\.?|number)|\bsku\b|"
    r"\bemail\b|e-?mail\s*address|account\s*(no\.?|number)|"
    r"(^|_)id$|(^|_)id\b)",
    re.I,
)

_IDENTIFIER_NAME_RE = re.compile(
    r"(_id\b|\bid\b|\bcode\b|code$|\bno\b|no\.|number|\binv\b|invoice|"
    r"\bref\b|reference|\bsku\b|\bpo\b|order\s*no)",
    re.I,
)

_PHONE_CONTACT_KEYWORDS = (
    "fax",
    "tel",
    "phone",
    "mobile",
    "toll free",
    "tollfree",
    "landline",
    "cell",
    "contact",
)

_FINANCIAL_NAME_RE = re.compile(
    r"(price|cost|amount|revenue|tax|margin|discount|balance|net|gross|total|amt|val|value|rate)",
    re.I,
)

_DESCRIPTIVE_NAME_RE = re.compile(
    r"(city|country|desc|description|category|state|region|status|type|group|comment|notes|address)",
    re.I,
)

_SAMPLE_SIZE = 200
_HIGH_CARDINALITY_RATIO = 0.90
_LOW_CARDINALITY_RATIO = 0.50


@dataclass
class ColumnMetadata:
    """Reusable metadata container for a single DataFrame column."""

    column_name: str
    semantic_role: str
    uniqueness_confidence: float = 0.0
    business_importance: str = "MEDIUM"
    sensitivity_level: str = "INTERNAL"
    quality_score: float = 100.0
    validation_rules: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def legacy_role(self) -> str:
        return LEGACY_ROLE_MAP.get(self.semantic_role, "categorical")


def _cardinality_ratio(non_null: pd.Series) -> float:
    n = len(non_null)
    if n == 0:
        return 0.0
    return float(non_null.nunique()) / float(n)


def _top_value_ratio(non_null: pd.Series) -> float:
    n = len(non_null)
    if n == 0:
        return 0.0
    vc = non_null.value_counts()
    return float(vc.iloc[0]) / float(n) if not vc.empty else 0.0


def _has_phone_contact_name_hint(col_name: str | None) -> bool:
    if not col_name:
        return False
    name = str(col_name).strip().lower().replace("_", " ")
    return any(k in name for k in _PHONE_CONTACT_KEYWORDS)


def _looks_like_phone_number_values(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False
    sample = non_null.head(_SAMPLE_SIZE)
    numeric = pd.to_numeric(sample, errors="coerce")
    valid = numeric.dropna()
    if valid.empty or (float(len(valid)) / float(len(sample))) < 0.8:
        return False
    phone_like = 0
    for value in valid:
        if abs(float(value) - round(float(value))) > 1e-9:
            continue
        as_int = int(abs(round(float(value))))
        if as_int == 0:
            continue
        if len(str(as_int)) >= 9:
            phone_like += 1
    return (phone_like / float(len(valid))) > 0.8


def _is_pii_column(series: pd.Series, col_name: str | None) -> tuple[bool, str]:
    name = str(col_name or "").strip()
    if _has_phone_contact_name_hint(col_name):
        if not series.dropna().empty:
            if "email" in name.lower():
                return True, ROLE_PII
            return True, ROLE_CONTACT
    hints = _infer_expected_types(col_name)
    if hints:
        non_null = series.dropna()
        if not non_null.empty:
            sample = non_null.astype(str).head(_SAMPLE_SIZE)
            summary = detect_pii_in_series(pd.Series(sample.to_numpy(), name=col_name))
            if summary.get("rows_with_pii", 0) > 0:
                return True, ROLE_PII
    if _looks_like_phone_number_values(series):
        return True, ROLE_CONTACT
    return False, ""


def _is_date_column(series: pd.Series, col_name: str | None = None) -> bool:
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
    threshold = 0.5 if (col_name and re.search(r"(date|time|timestamp|\bdob\b)", str(col_name), re.I)) else 0.8
    return ratio >= threshold


def is_datetime_column(series: pd.Series, col_name: str | None = None) -> bool:
    """True if series contains datetime values or timestamps.

    Reused by SOX transaction_timestamp compliance rule.
    """
    return _is_date_column(series, col_name)


def _looks_like_code(value: object) -> bool:
    s = str(value).strip()
    return bool(s and len(s) <= 20 and s.count(" ") <= 1)


def _is_identifier_column(series: pd.Series, col_name: str | None, non_null: pd.Series) -> bool:
    name = str(col_name) if col_name is not None else ""
    if _IDENTIFIER_NAME_RE.search(name):
        return True
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return False
    if non_null.empty:
        return False
    ratio = _cardinality_ratio(non_null)
    if ratio < _HIGH_CARDINALITY_RATIO:
        return False
    sample = non_null.astype(str).head(_SAMPLE_SIZE)
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


def classify_semantic_role(series: pd.Series, col_name: str | None = None) -> str:
    """Classify a single column into one of 12 rich semantic roles."""
    name = col_name if col_name is not None else getattr(series, "name", None)
    name_str = str(name or "").strip()
    non_null = series.dropna()

    if non_null.empty:
        return ROLE_CATEGORY

    # 1. PII / Contact Check
    is_pii, pii_type = _is_pii_column(series, name)
    if is_pii:
        return pii_type

    # 2. Date Check
    if _is_date_column(series, name):
        return ROLE_DATE

    # 3. Identifier Check
    if _is_identifier_column(series, name, non_null):
        if _PRIMARY_KEY_NAME_RE.search(name_str) and _cardinality_ratio(non_null) >= 0.98:
            return ROLE_PRIMARY_KEY
        if "code" in name_str.lower() or "sku" in name_str.lower():
            return ROLE_BUSINESS_KEY
        return ROLE_IDENTIFIER

    # 4. Measurement / Financial Check
    if _is_measurement_column(series, non_null):
        if _FINANCIAL_NAME_RE.search(name_str):
            return ROLE_FINANCIAL
        return ROLE_MEASUREMENT

    # 5. Category vs Boolean vs Free Text
    ratio = _cardinality_ratio(non_null)
    if non_null.nunique() <= 2 and ratio <= 0.1:
        vals = set(non_null.astype(str).str.strip().str.lower().unique())
        if vals.issubset({"true", "false", "1", "0", "y", "n", "yes", "no", "t", "f"}):
            return ROLE_BOOLEAN

    if ratio <= _LOW_CARDINALITY_RATIO:
        return ROLE_CATEGORY
    return ROLE_FREE_TEXT


def compute_column_uniqueness_confidence(
    series: pd.Series, col_name: str, semantic_role: str
) -> float:
    name = str(col_name).strip()
    non_null = series.dropna()
    n = len(non_null)

    if n == 0:
        return 0.0

    name_match = bool(_PRIMARY_KEY_NAME_RE.search(name))
    is_descriptive = bool(_DESCRIPTIVE_NAME_RE.search(name))

    ratio = _cardinality_ratio(non_null)
    top_freq = _top_value_ratio(non_null)

    score = 0.0
    if name_match:
        score += 0.30
    if is_descriptive:
        score -= 0.40

    if semantic_role in {ROLE_PRIMARY_KEY, ROLE_BUSINESS_KEY}:
        score += 0.35
    elif semantic_role == ROLE_IDENTIFIER:
        score += 0.20
    elif semantic_role in {ROLE_CATEGORY, ROLE_FREE_TEXT, ROLE_MEASUREMENT, ROLE_FINANCIAL}:
        score -= 0.30

    if ratio >= 0.98:
        score += 0.35
    elif ratio >= 0.90:
        score += 0.20
    elif ratio >= 0.70:
        score += 0.10
    elif ratio < 0.50:
        score -= 0.20

    if n >= 20 and top_freq >= 0.05:
        score -= 0.30

    if pd.api.types.is_float_dtype(series) or pd.api.types.is_bool_dtype(series):
        score -= 0.20

    if non_null.nunique() <= 10 and n > 20:
        score -= 0.30

    return max(0.0, min(1.0, round(score, 3)))


def build_column_metadata_layer(
    df: pd.DataFrame, rules: list[dict[str, Any]] | None = None
) -> dict[str, ColumnMetadata]:
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    rules_list = rules or []
    layer: dict[str, ColumnMetadata] = {}

    for col in df.columns:
        col_name = str(col)
        series = df[col]
        role = classify_semantic_role(series, col_name)
        confidence = compute_column_uniqueness_confidence(series, col_name, role)

        importance = "MEDIUM"
        if role in {ROLE_PRIMARY_KEY, ROLE_BUSINESS_KEY, ROLE_FINANCIAL}:
            importance = "CRITICAL" if role != ROLE_BUSINESS_KEY else "HIGH"

        sensitivity = "INTERNAL"
        if role == ROLE_PII:
            sensitivity = "RESTRICTED"
        elif role == ROLE_CONTACT:
            sensitivity = "CONFIDENTIAL"

        bound_rules = [
            r for r in rules_list if str(r.get("column", "")).lower() == col_name.lower()
        ]

        layer[col_name] = ColumnMetadata(
            column_name=col_name,
            semantic_role=role,
            uniqueness_confidence=confidence,
            business_importance=importance,
            sensitivity_level=sensitivity,
            quality_score=100.0,
            validation_rules=bound_rules,
            details={
                "cardinality": int(series.nunique()),
                "non_null_count": int(series.dropna().count()),
                "uniqueness_ratio": round(_cardinality_ratio(series.dropna()), 4),
            },
        )
    return layer


def classify_column(series: pd.Series, col_name: str | None = None) -> str:
    """Legacy API returning one of 6 legacy roles."""
    role = classify_semantic_role(series, col_name)
    return LEGACY_ROLE_MAP.get(role, "categorical")


def classify_columns(df: pd.DataFrame) -> dict[str, str]:
    """Legacy API returning dict mapping column -> legacy role string."""
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    return {str(col): classify_column(df[col], col) for col in df.columns}
