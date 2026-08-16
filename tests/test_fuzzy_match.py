"""Tests for plan.md Task 5 — RapidFuzz fuzzy standardization (Section 4.4)."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.engine.standardization.fuzzy_match import (
    apply_standardization,
    check_fuzzy_standardization,
    check_fuzzy_standardization_frame,
    standardize_frame,
    standardize_values,
)


def test_standardize_values_clusters_near_duplicates():
    # colour/color score ~91 with case-insensitive ratio — above default 90
    s = pd.Series(
        ["colour", "color", "colour", "blue", "Blue"],
        name="shade",
    )
    mapping = standardize_values(s, threshold=90)
    assert mapping["color"] == mapping["colour"]
    assert mapping["Blue"] == mapping["blue"]
    # Most frequent spelling of the colour cluster is "colour" (2 vs 1)
    assert mapping["colour"] == "colour"
    assert mapping["color"] == "colour"


def test_standardize_values_identity_when_all_distinct():
    s = pd.Series(["alpha", "beta", "gamma"])
    mapping = standardize_values(s, threshold=90)
    assert mapping == {"alpha": "alpha", "beta": "beta", "gamma": "gamma"}


def test_standardize_values_empty_and_nulls():
    assert standardize_values(pd.Series([], dtype=object)) == {}
    assert standardize_values(pd.Series([None, None])) == {}


def test_standardize_values_invalid_inputs():
    with pytest.raises(TypeError):
        standardize_values(["not", "a", "series"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        standardize_values(pd.Series(["a"]), threshold=101)


def test_apply_standardization_preserves_nulls_and_index():
    s = pd.Series(["colour", None, "color"], index=[10, 11, 12], name="c")
    out = apply_standardization(s, threshold=90)
    assert out.index.tolist() == [10, 11, 12]
    assert pd.isna(out.iloc[1])
    assert out.iloc[0] == out.iloc[2]


def test_check_fuzzy_standardization_failed_and_passed():
    failed = check_fuzzy_standardization(
        pd.Series(["colour", "color", "colour"], name="shade"),
        threshold=90,
        role="categorical",
    )
    assert failed.status == "failed"
    assert failed.dimension == "consistency"
    assert failed.check_name == "fuzzy_standardization"
    assert failed.issues_found >= 1
    assert "color" in (failed.details.get("mapping_sample") or {})

    passed = check_fuzzy_standardization(
        pd.Series(["red", "green", "blue"], name="shade"),
        threshold=90,
        role="categorical",
    )
    assert passed.status == "passed"
    assert passed.issues_found == 0


def test_check_skips_non_eligible_roles():
    r = check_fuzzy_standardization(
        pd.Series(["a", "b"], name="amt"),
        role="measurement",
    )
    assert r.status == "passed"
    assert r.details["reason"] == "skipped_non_text_column"


def test_check_invalid_series_returns_error_status():
    r = check_fuzzy_standardization(None)  # type: ignore[arg-type]
    assert r.status == "error"


def test_check_frame_empty_and_role_aware(monkeypatch):
    empty = check_fuzzy_standardization_frame(pd.DataFrame())
    assert len(empty) == 1
    assert empty[0].details.get("reason") == "no_columns"

    df = pd.DataFrame(
        {
            "status": ["Paid", "PAID", "paid", "Open"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )
    roles = {"status": "categorical", "amount": "measurement"}
    results = check_fuzzy_standardization_frame(df, roles=roles, threshold=90)
    by_col = {r.column: r for r in results}
    assert by_col["amount"].details["reason"] == "skipped_non_text_column"
    assert by_col["status"].status == "failed"
    assert by_col["status"].issues_found >= 1


def test_standardize_frame_only_eligible_columns():
    df = pd.DataFrame(
        {
            "city": ["New York", "NewYork", "Boston"],
            "code": ["A1", "B2", "C3"],
        }
    )
    roles = {"city": "categorical", "code": "identifier"}
    maps = standardize_frame(df, roles=roles, threshold=90)
    assert "city" in maps
    assert "code" not in maps
    assert maps["city"]["NewYork"] == maps["city"]["New York"]


def test_case_insensitive_setting(monkeypatch):
    from backend.config.settings import SETTINGS

    s = pd.Series(["Paid", "PAID", "Paid"], name="status")
    monkeypatch.setitem(SETTINGS, "fuzzy_case_insensitive", True)
    mapping_on = standardize_values(s, threshold=90)
    assert mapping_on["PAID"] == mapping_on["Paid"]

    monkeypatch.setitem(SETTINGS, "fuzzy_case_insensitive", False)
    mapping_off = standardize_values(s, threshold=90)
    # Case-sensitive ratio("Paid","PAID") is low — they stay separate
    assert mapping_off["PAID"] == "PAID"
    assert mapping_off["Paid"] == "Paid"


def test_max_unique_cap(monkeypatch):
    from backend.config.settings import SETTINGS

    monkeypatch.setitem(SETTINGS, "fuzzy_max_unique", 3)
    # 5 distinct values; only top-3 by frequency are clustered
    s = pd.Series(["a"] * 10 + ["b"] * 8 + ["c"] * 6 + ["d"] * 2 + ["e"] * 1)
    mapping = standardize_values(s, threshold=90)
    assert len(mapping) == 3
    assert set(mapping) == {"a", "b", "c"}
