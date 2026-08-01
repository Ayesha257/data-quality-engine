"""Tests for column role classification (Task 3 fix, Issue 1)."""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_DATE,
    ROLE_FREE_TEXT,
    ROLE_IDENTIFIER,
    ROLE_MEASUREMENT,
    ROLE_PII,
    classify_columns,
)
from data_quality_engine.engine.checks.outliers import detect_outliers_frame


def _erp_like_frame() -> pd.DataFrame:
    """Fixture mimicking inconsistent real-world ERP column names."""
    n = 25
    return pd.DataFrame(
        {
            # Name matches identifier pattern ("inv.no" -> "no")
            "inv.no": [f"INV{1000 + i}" for i in range(n)],
            # Name gives no hint at all; must rely on cardinality + shape
            "Dimension (CUS)": [f"CUS{100 + i:03d}" for i in range(n)],
            # Genuine continuous measurement, stored with a mostly-numeric name
            "Amount": [10.5, 12.0, 9.8, 11.2, 10.0, 13.1, 9.9, 200.0, 10.4, 11.1,
                       12.3, 10.9, 9.7, 11.6, 10.2, 12.8, 9.6, 10.1, 11.4, 10.8,
                       10.3, 11.0, 9.9, 10.6, 300.0],
            "email": [f"user{i}@example.com" for i in range(n)],
            "mobile_number": ["0300-1234567"] * n,
            "status": ["A", "B"] * 12 + ["A"],
            "order_date": [f"2024-01-{(i % 28) + 1:02d}" for i in range(n)],
            "notes": [f"This is a fairly long free text comment about order {i}" for i in range(n)],
        }
    )


def test_identifier_columns_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["inv.no"] == ROLE_IDENTIFIER
    # Inconsistent name with no id/code/number hint -- must fall back to
    # cardinality + code-shape, not name matching alone.
    assert roles["Dimension (CUS)"] == ROLE_IDENTIFIER


def test_measurement_column_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["Amount"] == ROLE_MEASUREMENT


def test_pii_columns_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["email"] == ROLE_PII
    assert roles["mobile_number"] == ROLE_PII


def test_categorical_column_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["status"] == ROLE_CATEGORICAL


def test_date_column_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["order_date"] == ROLE_DATE


def test_fax_numeric_phones_classify_as_pii_not_measurement():
    """BUG fix: Fax / Toll Free with numeric-looking phones must not get IQR."""
    df = pd.DataFrame(
        {
            "Fax": [14123456789, 441612345678, 0, 1616545969],
            "Toll Free": [8001234567, 8009876543, 8001112222, 8003334444],
            "Amount": [10.5, 12.0, 9.8, 200.0],
        }
    )
    roles = classify_columns(df)
    assert roles["Fax"] == ROLE_PII
    assert roles["Toll Free"] == ROLE_PII
    assert roles["Amount"] == ROLE_MEASUREMENT


def test_other_column_phone_integers_classify_as_pii():
    """Generic name 'Other' with UK-phone-shaped integers -> pii, not measurement."""
    df = pd.DataFrame(
        {
            "Other": [
                1488680200,
                1616545969,
                1217736672,
                4411488680200,
                8001234567,
            ],
        }
    )
    assert classify_columns(df)["Other"] == ROLE_PII


def test_other_column_currency_values_still_measurement():
    """Generic name 'Other' with small currency-like values must stay measurement."""
    df = pd.DataFrame({"Other": [12.50, 8.00, 3.25, 100.00, 0.99]})
    assert classify_columns(df)["Other"] == ROLE_MEASUREMENT



def test_dirty_date_named_column_still_classified_as_date():
    """Name hint + mostly-parseable values should win over identifier fallback."""
    df = pd.DataFrame(
        {
            "Date": [
                "2024-01-01",
                "01/02/2024",
                "2024-13-45",  # invalid
                "2024-01-04",
                "2024-01-05",
                "2024-01-06",
                "unknown",
            ]
        }
    )
    assert classify_columns(df)["Date"] == ROLE_DATE


def test_free_text_column_classified_correctly():
    roles = classify_columns(_erp_like_frame())
    assert roles["notes"] == ROLE_FREE_TEXT


def test_numeric_object_dtype_column_is_not_misread_as_identifier():
    """
    Regression: a column of unique numeric-looking values stored as
    object dtype (e.g. after generic header-detection loading) must be
    classified as measurement, not identifier, even though every value
    is unique.
    """
    s = pd.Series(["1000", "950", "1020", "5000", "980", "-50"], name="Sales")
    df = pd.DataFrame({"Sales": s})
    roles = classify_columns(df)
    assert roles["Sales"] == ROLE_MEASUREMENT


def test_empty_dataframe_classification():
    assert classify_columns(pd.DataFrame()) == {}


def test_outliers_frame_skips_identifier_but_runs_on_measurement():
    """
    outliers.py integration: a fabricated identifier column (sequential
    invoice numbers, numeric dtype so it would previously have been
    scanned) must be skipped, while a real measurement column with an
    actual spike still gets flagged by IQR.
    """
    df = pd.DataFrame(
        {
            "invoice_no": [100000 + i for i in range(15)],
            "Amount": [10, 11, 12, 9, 10, 11, 13, 10, 9, 12, 1000, 11, 10, 9, 12],
        }
    )
    results = {r.column: r for r in detect_outliers_frame(df)}

    assert results["invoice_no"].status == "passed"
    assert results["invoice_no"].issues_found == 0
    assert results["invoice_no"].details["reason"] == "skipped_non_measurement_column"
    assert results["invoice_no"].details["classified_role"] == "identifier"

    assert results["Amount"].status == "failed"
    assert results["Amount"].issues_found >= 1
    assert results["Amount"].details.get("reason") is None
