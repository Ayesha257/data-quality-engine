"""Tests for missing_values, duplicates, type_mismatch (Task 3)."""

from __future__ import annotations

import pandas as pd

from backend.engine.checks.duplicates import (
    check_duplicates,
    check_duplicates_frame,
    infer_uniqueness_keys,
)
from backend.engine.checks.missing_values import check_missing_values
from backend.engine.checks.type_mismatch import (
    check_type_consistency,
    check_type_consistency_frame,
)


def test_missing_values_per_column():
    df = pd.DataFrame(
        {
            "Name": ["Ali", None, "Sara"],
            "Age": [30, 25, None],
        }
    )
    results = check_missing_values(df)
    by_col = {r.column: r for r in results}
    assert by_col["Name"].issues_found == 1
    assert by_col["Name"].status == "failed"
    assert by_col["Name"].dimension == "completeness"
    assert by_col["Age"].issues_found == 1


def test_missing_values_clean_passes():
    df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
    results = check_missing_values(df)
    assert all(r.status == "passed" for r in results)


def test_missing_values_bad_input_returns_error():
    results = check_missing_values("not a dataframe")  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0].status == "error"


def test_duplicates_detects_extra_rows():
    df = pd.DataFrame(
        {
            "Name": ["Ali", "Sara", "Ali"],
            "City": ["LHR", "KHI", "LHR"],
        }
    )
    results = check_duplicates(df)
    assert results[0].issues_found == 1
    assert results[0].status == "failed"
    assert results[0].dimension == "uniqueness"
    assert results[0].details["row_indices"] == [2]


def test_duplicates_subset_key():
    df = pd.DataFrame(
        {
            "id": [1, 1, 2],
            "name": ["A", "B", "C"],
        }
    )
    results = check_duplicates(df, subset=["id"])
    assert results[0].issues_found == 1


def test_duplicates_bad_subset_returns_error():
    df = pd.DataFrame({"A": [1]})
    results = check_duplicates(df, subset=["missing"])
    assert results[0].status == "error"


def test_duplicates_frame_reports_full_row_and_customer_key():
    df = pd.DataFrame(
        {
            "Customer No.": ["C1", "C1", "C2", "C2"],
            "Add. Code": ["HO", "WHS", "HO", "HO"],
            "City": ["LHR", "KHI", "LHR", "LHR"],
        }
    )
    # Row 2 and 3 are full-row duplicates; C1 and C2 are key duplicates
    results = check_duplicates_frame(df)
    by_name = {}
    for r in results:
        by_name.setdefault(r.check_name, []).append(r)

    full = by_name["duplicates"][0]
    assert full.issues_found == 1
    assert full.details["duplicate_set_rows"] == 2

    key = by_name["duplicate_keys"][0]
    assert key.column == "Customer No."
    assert key.issues_found == 2  # second C1 and second C2
    assert key.details["duplicate_set_rows"] == 4
    assert key.details["unique_keys_repeated"] >= 1


def test_compound_business_key_not_flagged_when_add_code_differs():
    """Same Customer No. + distinct Add. Code is NOT a compound-key duplicate."""
    df = pd.DataFrame(
        {
            "Customer No.": ["C00001", "C00001", "C00001"],
            "Add. Code": ["HO", "CIRCA", "JOHNS"],
            "City": ["LONDON", "MANCHESTER", "LEEDS"],
        }
    )
    # Explicit single-key behaviour still flags extras
    single = check_duplicates(df, subset=["Customer No."])[0]
    assert single.status == "failed"
    assert single.issues_found == 2

    # Compound key: each (Customer No., Add. Code) pair is unique
    compound = check_duplicates(df, subset=["Customer No.", "Add. Code"])[0]
    assert compound.status == "passed"
    assert compound.issues_found == 0
    assert compound.details["subset"] == ["Customer No.", "Add. Code"]

    frame = check_duplicates_frame(
        df, key_columns=["Customer No.", "Add. Code"]
    )
    key_results = [r for r in frame if r.check_name == "duplicate_keys"]
    assert len(key_results) == 1
    assert key_results[0].issues_found == 0
    assert key_results[0].column == "Customer No.,Add. Code"


def test_compound_key_still_flags_true_duplicates():
    df = pd.DataFrame(
        {
            "Customer No.": ["C1", "C1", "C1"],
            "Add. Code": ["HO", "HO", "WHS"],
            "City": ["A", "A", "B"],
        }
    )
    result = check_duplicates(df, subset=["Customer No.", "Add. Code"])[0]
    assert result.status == "failed"
    assert result.issues_found == 1



def test_duplicates_normalize_strips_whitespace():
    df = pd.DataFrame(
        {
            "Customer No.": ["A1", "A1"],
            "City": ["LHR", " LHR "],
        }
    )
    # Without normalize these differ; with normalize they match full-row
    results = check_duplicates(df, normalize=True)
    assert results[0].issues_found == 1


def test_infer_uniqueness_keys_finds_customer_no():
    df = pd.DataFrame(
        {
            "Customer No.": ["C1"],
            "Add. Code": ["HO"],
            "City": ["LHR"],
        }
    )
    keys = infer_uniqueness_keys(df)
    assert "Customer No." in keys
    assert "Add. Code" not in keys
    assert "City" not in keys



def test_type_mismatch_mixed_column():
    s = pd.Series([1, 2, "three", 4], name="Age")
    result = check_type_consistency(s)
    assert result.status == "failed"
    assert result.issues_found == 1
    assert result.details["dominant_type"] == "number"
    assert result.dimension == "validity"


def test_type_mismatch_uniform_passes():
    s = pd.Series(["a", "b", "c"], name="City")
    result = check_type_consistency(s)
    assert result.status == "passed"
    assert result.issues_found == 0


def test_type_mismatch_frame():
    df = pd.DataFrame({"A": [1, "x", 3], "B": ["p", "q", "r"]})
    results = check_type_consistency_frame(df)
    assert len(results) == 2
    by_col = {r.column: r for r in results}
    assert by_col["A"].status == "failed"
    assert by_col["B"].status == "passed"


def test_type_mismatch_bad_input_returns_error():
    result = check_type_consistency("nope")  # type: ignore[arg-type]
    assert result.status == "error"
