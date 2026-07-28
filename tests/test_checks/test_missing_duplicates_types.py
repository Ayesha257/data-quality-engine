"""Tests for missing_values, duplicates, type_mismatch (Task 3)."""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.checks.duplicates import check_duplicates
from data_quality_engine.engine.checks.missing_values import check_missing_values
from data_quality_engine.engine.checks.type_mismatch import (
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
