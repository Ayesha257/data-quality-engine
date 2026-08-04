"""Tests for duplicate-row / duplicate-key checks (one-to-many parent keys & multi-signal confidence)."""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.checks.duplicates import (
    check_duplicates_frame,
    infer_uniqueness_keys,
    uniqueness_evidence,
)


def test_one_to_many_parent_key_uses_line_compound_key():
    """Order Number + Line: shared parent keys are not false-flagged alone."""
    df = pd.DataFrame(
        {
            "Order Number": ["SON-1", "SON-1", "SON-1", "SON-2", "SON-2"],
            "Line": [1, 2, 3, 1, 2],
            "Qty": [1, 1, 1, 1, 1],
        }
    )
    results = check_duplicates_frame(df)
    key_results = [r for r in results if r.check_name == "duplicate_keys"]
    assert key_results
    compound = [r for r in key_results if r.details.get("subset") == ["Order Number", "Line"]]
    assert compound
    assert compound[0].status == "passed"
    assert compound[0].issues_found == 0


def test_one_to_many_genuine_duplicate_key_plus_line_still_flagged():
    df = pd.DataFrame(
        {
            "Order Number": ["SON-1", "SON-1", "SON-1"],
            "Line": [1, 1, 2],
            "Qty": [10, 20, 30],
        }
    )
    results = check_duplicates_frame(df)
    key_results = [r for r in results if r.check_name == "duplicate_keys"]
    compound = [r for r in key_results if r.details.get("subset") == ["Order Number", "Line"]]
    assert compound
    assert compound[0].status == "failed"
    assert compound[0].issues_found >= 1


def test_one_to_many_parent_key_skipped_without_line_column():
    df = pd.DataFrame(
        {
            "Order Number": ["SON-1"] * 8 + ["SON-2"] * 2,
            "Qty": list(range(10)),
        }
    )
    results = check_duplicates_frame(df)
    key_results = [r for r in results if r.check_name == "duplicate_keys"]
    assert key_results
    skipped = [
        r
        for r in key_results
        if r.details.get("reason") == "likely_one_to_many_parent_key"
    ]
    assert skipped
    assert skipped[0].status == "passed"
    assert skipped[0].issues_found == 0


def test_descriptive_columns_not_flagged_as_duplicate_keys():
    """City, Country, Product Description, Category must NOT be inferred as uniqueness keys."""
    df = pd.DataFrame(
        {
            "Customer No.": [f"C{i:03d}" for i in range(50)],
            "City": ["Lahore", "Karachi", "Lahore", "Islamabad"] * 12 + ["Lahore", "Karachi"],
            "Country": ["Pakistan"] * 50,
            "Product Description": ["Widget A", "Widget B"] * 25,
            "Category": ["Electronics", "Hardware"] * 25,
        }
    )

    for desc_col in ["City", "Country", "Product Description", "Category"]:
        ev = uniqueness_evidence(df, desc_col)
        assert ev["expected_unique"] is False
        assert ev["score"] < 0.60

    inferred_keys = infer_uniqueness_keys(df)
    assert "City" not in inferred_keys
    assert "Country" not in inferred_keys
    assert "Product Description" not in inferred_keys
    assert "Category" not in inferred_keys
    assert "Customer No." in inferred_keys
