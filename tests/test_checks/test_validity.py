"""Tests for validity.py (Validity dimension)."""

from __future__ import annotations

import pandas as pd

from backend.engine.checks.validity import check_validity_frame
from backend.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_DATE,
    ROLE_MEASUREMENT,
    ROLE_PII,
)


def _find(results, col):
    return [r for r in results if r.column == col]


def test_flags_negative_quantity():
    df = pd.DataFrame({"Order Qty": [5, -3, 10]})
    results = check_validity_frame(df, roles={"Order Qty": ROLE_MEASUREMENT})
    matches = _find(results, "Order Qty")
    assert any(
        r.status == "failed" and r.details.get("rule") == "negative_value_not_allowed"
        for r in matches
    )


def test_does_not_flag_negative_in_unrelated_measurement_column():
    # "Line Margin" has no name-based non-negative expectation -- negative
    # margins are a completely normal real value in this dataset.
    df = pd.DataFrame({"Line Margin GBP": [5.0, -3.5, 10.0]})
    results = check_validity_frame(df, roles={"Line Margin GBP": ROLE_MEASUREMENT})
    matches = _find(results, "Line Margin GBP")
    assert not any(
        r.details.get("rule") == "negative_value_not_allowed" for r in matches
    )


def test_flags_suspicious_zero_in_known_column():
    df = pd.DataFrame({"Standard Cost": [1.5, 0.0, 2.0]})
    results = check_validity_frame(df, roles={"Standard Cost": ROLE_MEASUREMENT})
    matches = _find(results, "Standard Cost")
    assert any(
        r.status == "failed" and r.details.get("rule") == "suspicious_zero_value"
        for r in matches
    )


def test_flags_unparseable_date_cell():
    df = pd.DataFrame({"Order Date": ["2024-01-01", "not a date", "2024-01-03"]})
    results = check_validity_frame(df, roles={"Order Date": ROLE_DATE})
    matches = _find(results, "Order Date")
    assert len(matches) == 1
    assert matches[0].status == "failed"
    assert matches[0].details["unparseable_count"] == 1


def test_flags_implausible_year():
    df = pd.DataFrame({"Order Date": ["1899-01-01", "2024-06-01"]})
    results = check_validity_frame(df, roles={"Order Date": ROLE_DATE})
    matches = _find(results, "Order Date")
    assert matches[0].status == "failed"
    assert matches[0].details["implausible_count"] == 1


def test_clean_date_column_passes():
    df = pd.DataFrame({"Order Date": ["2024-01-01", "2024-06-15"]})
    results = check_validity_frame(df, roles={"Order Date": ROLE_DATE})
    matches = _find(results, "Order Date")
    assert matches[0].status == "passed"


def test_flags_invalid_email_format():
    df = pd.DataFrame({"Contact Email": ["a@b.com", "not-an-email", "c@d.org"]})
    results = check_validity_frame(df, roles={"Contact Email": ROLE_CATEGORICAL})
    matches = _find(results, "Contact Email")
    assert any(
        r.status == "failed" and r.details.get("rule") == "invalid_email_format"
        for r in matches
    )


def test_email_format_via_pii_summary_without_email_in_column_name():
    """BUG fix: Statements / POs columns hold emails but aren't named 'email'."""
    df = pd.DataFrame(
        {
            "Statements": ["ok@example.com", "not-an-email", "b@c.org"],
            "Notes": ["hello", "world", "text"],
        }
    )
    pii_summary = {
        "Statements": {
            "rows_with_pii": 2,
            "type_counts": {"EMAIL": 2},
        },
        "Notes": {"rows_with_pii": 0, "type_counts": {}},
    }
    results = check_validity_frame(
        df,
        roles={"Statements": ROLE_PII, "Notes": ROLE_CATEGORICAL},
        pii_summary_by_column=pii_summary,
    )
    statements = _find(results, "Statements")
    assert any(
        r.status == "failed" and r.details.get("rule") == "invalid_email_format"
        for r in statements
    )
    # Name fallback still required when no PII summary: Notes not checked
    notes = _find(results, "Notes")
    assert all(r.details.get("rule") != "invalid_email_format" for r in notes)



def test_column_with_no_applicable_rule_is_skipped():
    df = pd.DataFrame({"Notes": ["some free text", "more text"]})
    results = check_validity_frame(df, roles={"Notes": ROLE_CATEGORICAL})
    matches = _find(results, "Notes")
    assert len(matches) == 1
    assert matches[0].details["reason"] == "skipped_no_rule_for_role"


def test_cross_column_date_rule_flags_violation():
    df = pd.DataFrame(
        {
            "Expected Del Date": ["2024-01-01", "2024-05-01"],
            "Order Date": ["2024-01-05", "2024-04-01"],  # row 0 violates (del < order)
        }
    )
    rules = [
        {"name": "expected_del_after_order", "left": "Expected Del Date", "op": ">=", "right": "Order Date"}
    ]
    results = check_validity_frame(df, roles={}, cross_column_rules=rules)
    rule_results = [r for r in results if r.column == "Expected Del Date vs Order Date"]
    assert len(rule_results) == 1
    assert rule_results[0].status == "failed"
    assert rule_results[0].issues_found == 1


def test_cross_column_rule_skipped_when_columns_missing():
    df = pd.DataFrame({"Other Col": [1, 2]})
    rules = [{"name": "x", "left": "A", "op": ">=", "right": "B"}]
    results = check_validity_frame(df, roles={"Other Col": ROLE_CATEGORICAL}, cross_column_rules=rules)
    rule_results = [r for r in results if r.column == "A vs B"]
    assert rule_results[0].status == "passed"
    assert rule_results[0].details["reason"] == "skipped_columns_not_present"


def test_no_columns_dataframe():
    df = pd.DataFrame()
    results = check_validity_frame(df)
    assert len(results) == 1
    assert results[0].details["reason"] == "no_columns"


def test_bad_input_returns_error():
    results = check_validity_frame("not a dataframe")  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0].status == "error"


def test_classifies_when_roles_not_provided():
    df = pd.DataFrame({"Order Qty": [5, -3, 10]})
    results = check_validity_frame(df)
    matches = _find(results, "Order Qty")
    assert any(r.details.get("rule") == "negative_value_not_allowed" for r in matches)


def test_dimension_is_validity():
    df = pd.DataFrame({"Order Qty": [5, -3]})
    results = check_validity_frame(df, roles={"Order Qty": ROLE_MEASUREMENT})
    assert all(r.dimension == "validity" for r in results)
