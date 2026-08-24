"""Tests for consistency.py (Consistency dimension)."""

from __future__ import annotations

import pandas as pd

from backend.engine.checks.consistency import (
    check_consistency,
    check_consistency_frame,
)
from backend.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_DATE,
    ROLE_IDENTIFIER,
    ROLE_MEASUREMENT,
)


def test_flags_case_variants_of_same_value():
    s = pd.Series(["Paid", "paid", "PAID", "Paid"], name="Status")
    result = check_consistency(s, role=ROLE_CATEGORICAL)
    assert result.status == "failed"
    assert result.issues_found == 2  # "paid" and "PAID" are the minority variants
    assert result.details["inconsistent_value_groups"] == 1


def test_flags_whitespace_variants():
    s = pd.Series(["Lahore", "Lahore ", " Lahore", "Lahore"], name="City")
    result = check_consistency(s, role=ROLE_CATEGORICAL)
    assert result.status == "failed"
    assert result.issues_found == 2


def test_clean_categorical_column_passes():
    s = pd.Series(["GBP", "USD", "GBP", "EUR"], name="Inv Currency")
    result = check_consistency(s, role=ROLE_CATEGORICAL)
    assert result.status == "passed"
    assert result.issues_found == 0


def test_skips_measurement_role():
    s = pd.Series([1.5, 2.75, 3.0], name="Unit Cost")
    result = check_consistency(s, role=ROLE_MEASUREMENT)
    assert result.status == "passed"
    assert result.details["reason"] == "skipped_non_categorical_column"


def test_skips_date_role():
    s = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"]), name="Invoice Date")
    result = check_consistency(s, role=ROLE_DATE)
    assert result.status == "passed"
    assert result.details["reason"] == "skipped_non_categorical_column"


def test_identifier_role_is_eligible():
    s = pd.Series(["INV001", "inv001", "INV002"], name="Invoice No")
    result = check_consistency(s, role=ROLE_IDENTIFIER)
    assert result.status == "failed"
    assert result.issues_found == 1


def test_all_null_column():
    s = pd.Series([None, None], name="Notes")
    result = check_consistency(s, role=ROLE_CATEGORICAL)
    assert result.status == "passed"
    assert result.details["reason"] == "all_null_or_empty"


def test_bad_input_returns_error():
    result = check_consistency("not a series")  # type: ignore[arg-type]
    assert result.status == "error"


def test_frame_reuses_provided_roles_without_reclassifying():
    df = pd.DataFrame(
        {
            "Status": ["Paid", "paid"],
            "Unit Cost": [1.0, 2.0],
        }
    )
    roles = {"Status": ROLE_CATEGORICAL, "Unit Cost": ROLE_MEASUREMENT}
    results = check_consistency_frame(df, roles=roles)
    by_col = {r.column: r for r in results}
    assert by_col["Status"].status == "failed"
    assert by_col["Unit Cost"].details["reason"] == "skipped_non_categorical_column"


def test_frame_classifies_when_roles_not_provided():
    df = pd.DataFrame({"Status": ["Paid", "paid", "Paid"]})
    results = check_consistency_frame(df)
    assert len(results) == 1
    assert results[0].column == "Status"


def test_frame_no_columns():
    df = pd.DataFrame()
    results = check_consistency_frame(df)
    assert len(results) == 1
    assert results[0].details["reason"] == "no_columns"


def test_dimension_is_consistency():
    s = pd.Series(["Paid", "paid"], name="Status")
    result = check_consistency(s, role=ROLE_CATEGORICAL)
    assert result.dimension == "consistency"
