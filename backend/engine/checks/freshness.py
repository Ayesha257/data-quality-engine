"""Freshness check -> Freshness dimension (teacher rubric, 5%, lowest weight).

Phase 1 Method per the rubric: "If date column exists, check date range and
recency." Kept intentionally simple to match that scope -- this is a
column-level check (is the data stale?), not a row-level one like most
other checks, since recency is a property of the whole column's date
range, not of any single cell.

Only runs on columns classify_columns() marks "date". Non-date columns are
skipped with a reason, same pattern as consistency.py/outliers.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.config.settings import SETTINGS
from backend.engine.column_classifier import ROLE_DATE, classify_columns
from backend.engine.models import CheckResult

_DEFAULT_FRESHNESS_DAYS = 90


def _error_result(col_name: str | None, message: str) -> CheckResult:
    return CheckResult(
        check_name="freshness",
        status="error",
        column=col_name,
        issues_found=0,
        details={"error": message},
        dimension="freshness",
    )


def _passed_skip(col_name: str | None, reason: str, **extra: Any) -> CheckResult:
    return CheckResult(
        check_name="freshness",
        status="passed",
        column=col_name,
        issues_found=0,
        details={"reason": reason, **extra},
        dimension="freshness",
    )


def check_freshness(
    series: pd.Series,
    role: str | None = None,
    freshness_days: int | None = None,
    as_of: datetime | None = None,
) -> CheckResult:
    """
    Flag a date column as stale if its most recent value is older than
    `freshness_days` (default: SETTINGS["freshness_days"], falling back to
    90 if not set). issues_found is 0 or 1 -- this is a single column-level
    verdict ("is this column fresh?"), not a per-row flag.
    """
    col = getattr(series, "name", None)
    col_name = str(col) if col is not None else None
    try:
        if series is None or not isinstance(series, pd.Series):
            raise TypeError("series must be a pandas Series")

        if role is not None and role != ROLE_DATE:
            return _passed_skip(col_name, "skipped_non_date_column", role=role)

        non_null = series.dropna()
        if non_null.empty:
            return _passed_skip(col_name, "all_null_or_empty")

        parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
        parsed = parsed.dropna()
        if parsed.empty:
            return _passed_skip(col_name, "no_parseable_dates")

        min_date, max_date = parsed.min(), parsed.max()
        threshold_days = (
            freshness_days
            if freshness_days is not None
            else SETTINGS.get("freshness_days", _DEFAULT_FRESHNESS_DAYS)
        )
        now = as_of or datetime.now()
        days_since_max = (now - max_date.to_pydatetime()).days

        is_stale = days_since_max > threshold_days
        return CheckResult(
            check_name="freshness",
            status="failed" if is_stale else "passed",
            column=col_name,
            issues_found=1 if is_stale else 0,
            details={
                "min_date": str(min_date.date()),
                "max_date": str(max_date.date()),
                "days_since_max": days_since_max,
                "freshness_threshold_days": threshold_days,
                "is_stale": is_stale,
            },
            dimension="freshness",
        )
    except Exception as exc:  # noqa: BLE001
        return _error_result(col_name, str(exc))


def check_freshness_frame(
    df: pd.DataFrame,
    roles: dict[str, str] | None = None,
    freshness_days: int | None = None,
    as_of: datetime | None = None,
) -> list[CheckResult]:
    """Run check_freshness on every date column of df."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.shape[1] == 0:
            return [
                CheckResult(
                    check_name="freshness",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_columns"},
                    dimension="freshness",
                )
            ]

        column_roles = roles if roles is not None else classify_columns(df)
        return [
            check_freshness(
                df[col],
                role=column_roles.get(col),
                freshness_days=freshness_days,
                as_of=as_of,
            )
            for col in df.columns
        ]
    except Exception as exc:  # noqa: BLE001
        return [_error_result(None, str(exc))]
