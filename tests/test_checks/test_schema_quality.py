"""Tests for schema_quality.py (Schema Quality dimension)."""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.checks.schema_quality import check_schema_quality


def test_clean_names_all_pass():
    df = pd.DataFrame({"Customer Name": ["Ali"], "Invoice No": ["INV001"]})
    results = check_schema_quality(df)
    assert all(r.status == "passed" for r in results)
    assert all(r.issues_found == 0 for r in results)


def test_flags_pandas_style_unnamed_column():
    df = pd.DataFrame({"Row Labels": [1], "Unnamed: 3": [2]})
    results = check_schema_quality(df)
    by_col = {r.column: r for r in results}
    assert by_col["Unnamed: 3"].status == "failed"
    assert "auto_generated_name" in by_col["Unnamed: 3"].details["issues"]
    assert by_col["Row Labels"].status == "passed"


def test_flags_ingestion_style_unnamed_column():
    df = pd.DataFrame({"unnamed_2": [1], "unnamed_39": [2]})
    results = check_schema_quality(df)
    assert all(r.status == "failed" for r in results)
    assert all("auto_generated_name" in r.details["issues"] for r in results)


def test_flags_vague_generic_names():
    df = pd.DataFrame({"Column1": [1], "Data": [2], "X": [3], "Temp": [4]})
    results = check_schema_quality(df)
    assert all(r.status == "failed" for r in results)
    for r in results:
        assert "vague_generic_name" in r.details["issues"]


def test_does_not_flag_legit_short_business_codes():
    # PO, SKU, VAT are real short column names in this dataset's files and
    # must not be mistaken for vague placeholders.
    df = pd.DataFrame({"PO": [1], "SKU": [2], "VAT": [3]})
    results = check_schema_quality(df)
    assert all(r.status == "passed" for r in results)


def test_flags_duplicate_column_names_case_insensitive():
    df = pd.DataFrame(
        [[1, 2, 3]], columns=["Buyer ID", "Cost", "buyer id"]
    )
    results = check_schema_quality(df)
    dup_flags = [r for r in results if "duplicate_column_name" in r.details["issues"]]
    assert len(dup_flags) == 2
    assert all(r.column in ("Buyer ID", "buyer id") for r in dup_flags)
    non_dup = [r for r in results if r.column == "Cost"]
    assert non_dup[0].status == "passed"


def test_flags_empty_and_whitespace_names():
    df = pd.DataFrame([[1, 2]], columns=["Name", "   "])
    results = check_schema_quality(df)
    blank = [r for r in results if r.column == "   "][0]
    assert blank.status == "failed"
    assert "empty_or_whitespace_name" in blank.details["issues"]


def test_no_columns_dataframe():
    df = pd.DataFrame()
    results = check_schema_quality(df)
    assert len(results) == 1
    assert results[0].status == "passed"
    assert results[0].details["reason"] == "no_columns"


def test_bad_input_returns_error():
    results = check_schema_quality("not a dataframe")  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0].status == "error"


def test_dimension_is_schema_quality():
    df = pd.DataFrame({"Unnamed: 0": [1]})
    results = check_schema_quality(df)
    assert all(r.dimension == "schema_quality" for r in results)
