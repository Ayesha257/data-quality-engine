"""Tests for freshness.py (Freshness dimension)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from data_quality_engine.engine.checks.freshness import (
    check_freshness,
    check_freshness_frame,
)
from data_quality_engine.engine.column_classifier import ROLE_DATE, ROLE_MEASUREMENT


def test_fresh_date_column_passes():
    as_of = datetime(2024, 6, 30)
    s = pd.Series(["2024-06-01", "2024-06-20"], name="Order Date")
    result = check_freshness(s, role=ROLE_DATE, freshness_days=90, as_of=as_of)
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details["max_date"] == "2024-06-20"


def test_stale_date_column_fails():
    as_of = datetime(2024, 12, 31)
    s = pd.Series(["2024-01-01", "2024-01-15"], name="Order Date")
    result = check_freshness(s, role=ROLE_DATE, freshness_days=90, as_of=as_of)
    assert result.status == "failed"
    assert result.issues_found == 1
    assert result.details["is_stale"] is True


def test_custom_threshold_respected():
    as_of = datetime(2024, 2, 1)
    s = pd.Series(["2024-01-01"], name="Order Date")
    # 31 days old: stale at 10-day threshold, fresh at 90-day threshold
    stale = check_freshness(s, role=ROLE_DATE, freshness_days=10, as_of=as_of)
    fresh = check_freshness(s, role=ROLE_DATE, freshness_days=90, as_of=as_of)
    assert stale.status == "failed"
    assert fresh.status == "passed"


def test_skips_non_date_role():
    s = pd.Series([1, 2, 3], name="Qty")
    result = check_freshness(s, role=ROLE_MEASUREMENT)
    assert result.status == "passed"
    assert result.details["reason"] == "skipped_non_date_column"


def test_all_null_column():
    s = pd.Series([None, None], name="Order Date")
    result = check_freshness(s, role=ROLE_DATE)
    assert result.status == "passed"
    assert result.details["reason"] == "all_null_or_empty"


def test_no_parseable_dates():
    s = pd.Series(["not a date", "also not"], name="Order Date")
    result = check_freshness(s, role=ROLE_DATE)
    assert result.status == "passed"
    assert result.details["reason"] == "no_parseable_dates"


def test_bad_input_returns_error():
    result = check_freshness("not a series")  # type: ignore[arg-type]
    assert result.status == "error"


def test_frame_runs_only_on_date_columns():
    df = pd.DataFrame({"Order Date": ["2024-01-01"], "Qty": [5]})
    roles = {"Order Date": ROLE_DATE, "Qty": ROLE_MEASUREMENT}
    results = check_freshness_frame(df, roles=roles, as_of=datetime(2024, 1, 5))
    by_col = {r.column: r for r in results}
    assert by_col["Qty"].details["reason"] == "skipped_non_date_column"
    assert by_col["Order Date"].status == "passed"


def test_frame_no_columns():
    df = pd.DataFrame()
    results = check_freshness_frame(df)
    assert len(results) == 1
    assert results[0].details["reason"] == "no_columns"


def test_dimension_is_freshness():
    s = pd.Series(["2024-01-01"], name="Order Date")
    result = check_freshness(s, role=ROLE_DATE)
    assert result.dimension == "freshness"
