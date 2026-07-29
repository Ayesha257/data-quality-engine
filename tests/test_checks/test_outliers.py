"""Tests for outlier detection (Task 3) — IQR default + optional PyOD KNN."""

from __future__ import annotations

import pandas as pd
import pytest

from data_quality_engine.engine.checks.outliers import (
    detect_outliers,
    detect_outliers_frame,
)


def test_iqr_flags_clear_outlier():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1000], name="Amount")
    result = detect_outliers(s, method="iqr")
    assert result.status == "failed"
    assert result.issues_found >= 1
    assert result.details["method"] == "iqr"
    assert "q1" in result.details
    assert "q3" in result.details
    assert "iqr" in result.details
    assert "lower_bound" in result.details
    assert "upper_bound" in result.details
    assert "outlier_pct" in result.details
    assert 1000 in result.details["sample_values"]
    assert result.dimension == "validity"


def test_iqr_is_default_method():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1000], name="Amount")
    result = detect_outliers(s)  # no method arg
    assert result.details["method"] == "iqr"


def test_iqr_clean_series_passes():
    s = pd.Series([10, 12, 11, 13, 12, 14, 11, 13], name="Score")
    result = detect_outliers(s)
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details["outlier_pct"] == 0.0


def test_iqr_insufficient_values():
    s = pd.Series([1, 2, None], name="Tiny")
    result = detect_outliers(s)
    assert result.status == "passed"
    assert result.details["reason"] == "insufficient_numeric_values"


def test_iqr_single_value_column():
    s = pd.Series([42], name="One")
    result = detect_outliers(s)
    assert result.status == "passed"
    assert result.details["reason"] == "insufficient_numeric_values"


def test_iqr_constant_column_no_outliers():
    s = pd.Series([5, 5, 5, 5, 5, 5], name="Const")
    result = detect_outliers(s)
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details.get("constant_column") is True
    assert result.details["iqr"] == 0.0


def test_iqr_all_nan_skipped_as_passed():
    s = pd.Series([None, None, None], name="Empty")
    result = detect_outliers(s)
    assert result.status == "passed"
    assert result.details["reason"] == "no_numeric_values"


def test_iqr_negative_and_decimal_values():
    s = pd.Series([-10.5, -1.0, 0.0, 1.2, 2.3, 3.0, 4.1, 5.0, -999.9], name="Mixed")
    result = detect_outliers(s)
    assert result.status == "failed"
    assert result.issues_found >= 1
    assert any(abs(v - (-999.9)) < 1e-9 for v in result.details["sample_values"])


def test_iqr_large_spike():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 1e12], name="Huge")
    result = detect_outliers(s)
    assert result.status == "failed"
    assert result.issues_found >= 1


def test_iqr_bad_input_returns_error():
    result = detect_outliers("nope")  # type: ignore[arg-type]
    assert result.status == "error"


def test_unsupported_method_returns_error():
    s = pd.Series([1, 2, 3, 4, 5], name="A")
    result = detect_outliers(s, method="zscore")
    assert result.status == "error"


def test_detect_outliers_frame_skips_non_numeric():
    df = pd.DataFrame(
        {
            "City": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            "Age": [20, 21, 22, 23, 24, 25, 26, 27, 28, 200],
        }
    )
    results = detect_outliers_frame(df)
    assert len(results) == 1
    assert results[0].column == "Age"
    assert results[0].status == "failed"
    assert results[0].details["method"] == "iqr"


def test_detect_outliers_frame_empty():
    results = detect_outliers_frame(pd.DataFrame())
    assert len(results) == 1
    assert results[0].status == "passed"
    assert results[0].details["reason"] == "empty_dataframe"


def test_detect_outliers_frame_text_only():
    df = pd.DataFrame({"City": ["Lahore", "Karachi"], "Name": ["Ali", "Sara"]})
    results = detect_outliers_frame(df)
    assert len(results) == 1
    assert results[0].status == "passed"
    assert results[0].details["reason"] == "no_numeric_columns"


def test_detect_outliers_frame_bad_input():
    results = detect_outliers_frame("nope")  # type: ignore[arg-type]
    assert results[0].status == "error"


def test_knn_method_available_or_graceful():
    """KNN must either work (if pyod installed) or return error — never crash."""
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1000, 1100], name="Amount")
    result = detect_outliers(s, method="knn")
    assert result.status in {"passed", "failed", "error"}
    if result.status == "error":
        assert "pyod" in str(result.details.get("error", "")).lower()
    else:
        assert result.details["method"] == "knn"
        assert "row_indices" in result.details
        assert "outlier_count" in result.details


def test_knn_insufficient_values():
    pytest.importorskip("pyod")
    s = pd.Series([1, 2, 3], name="Tiny")
    result = detect_outliers(s, method="knn")
    assert result.status == "passed"
    assert "insufficient" in result.details.get("reason", "")
