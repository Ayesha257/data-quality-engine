"""Validity check -> Validity dimension (teacher rubric, 20% -- largest weight).

Distinct from type_mismatch.py ("is this cell stored as the right Python
type?" -> rubric "Type Reliability") and outliers.py ("is this a
statistical outlier?" -> rubric "Outlier Risk"). Validity here means: does
the *value itself* follow the rules a human would expect, independent of
storage type or statistical distribution. Per the rubric's own examples:
non-numeric revenue, invalid dates, negative age, invalid emails.

Four independent sub-checks, one CheckResult per column (plus one per
cross-column rule):

1. measurement columns whose *name* implies non-negative semantics
   (qty, quantity, count, age, stock, units, weight, hours, days) ->
   flag negative values.
2. measurement columns matching Easby's known suspicious-zero list
   (domain_rules.suspicious_zero_columns_present -- previously unused
   scaffolding, now wired in) -> flag zero values as suspicious, not just
   "valid but zero".
3. date columns -> flag individual unparseable cells (a column can be
   *mostly* dates and still have a handful of malformed ones -- that's a
   validity problem even though the column's dominant type is fine) and
   implausible dates (year outside a sane configurable window).
4. any column whose name suggests an email field -> flag values that
   don't match the same email pattern detect_pii.py already uses (import,
   not duplicate).

Plus, at the frame level: cross-column date-order rules from
domain_rules.default_cross_column_rules() (e.g. Expected Delivery Date
must be >= Order Date) -- another previously-unused module finally doing
real work.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd

from data_quality_engine.config.domain_rules import (
    default_cross_column_rules,
    suspicious_zero_columns_present,
)
from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.column_classifier import (
    ROLE_DATE,
    ROLE_MEASUREMENT,
    classify_columns,
)
from data_quality_engine.engine.models import CheckResult
from data_quality_engine.engine.pii.detect_pii import _EMAIL_RE

_NON_NEGATIVE_NAME_RE = re.compile(
    r"\b(qty|quantity|count|age|stock|units?|weight|hours?|days?)\b", re.I
)
_EMAIL_NAME_RE = re.compile(r"email", re.I)

_MIN_YEAR = 1990
_MAX_YEAR_AHEAD = 1  # allow up to 1 year in the future (e.g. forward orders)

_SAMPLE_CAP = 100


def _error_result(col_name: str | None, message: str) -> CheckResult:
    return CheckResult(
        check_name="validity",
        status="error",
        column=col_name,
        issues_found=0,
        details={"error": message},
        dimension="validity",
    )


def _passed_skip(col_name: str | None, reason: str, **extra: Any) -> CheckResult:
    return CheckResult(
        check_name="validity",
        status="passed",
        column=col_name,
        issues_found=0,
        details={"reason": reason, **extra},
        dimension="validity",
    )


def _result(col_name: str | None, issue_idx: list[Any], rule: str, extra: dict | None = None) -> CheckResult:
    issues = len(issue_idx)
    status = "passed" if issues == 0 else "failed"
    details = {
        "rule": rule,
        "row_indices": issue_idx[:_SAMPLE_CAP],
        "row_indices_truncated": issues > _SAMPLE_CAP,
    }
    if extra:
        details.update(extra)
    return CheckResult(
        check_name="validity",
        status=status,
        column=col_name,
        issues_found=issues,
        details=details,
        dimension="validity",
    )


def _check_non_negative(series: pd.Series, col_name: str) -> CheckResult | None:
    if not _NON_NEGATIVE_NAME_RE.search(col_name):
        return None
    numeric = pd.to_numeric(series, errors="coerce")
    mask = numeric.notna() & (numeric < 0)
    issue_idx = series.index[mask].tolist()
    return _result(col_name, issue_idx, "negative_value_not_allowed")


def _check_suspicious_zero(series: pd.Series, col_name: str, suspicious_cols: set[str]) -> CheckResult | None:
    if col_name not in suspicious_cols:
        return None
    numeric = pd.to_numeric(series, errors="coerce")
    mask = numeric.notna() & (numeric == 0)
    issue_idx = series.index[mask].tolist()
    return _result(col_name, issue_idx, "suspicious_zero_value")


def _check_date_column(series: pd.Series, col_name: str) -> CheckResult:
    non_null = series.dropna()
    if non_null.empty:
        return _passed_skip(col_name, "all_null_or_empty")

    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
    unparseable_idx = non_null.index[parsed.isna()].tolist()

    now_year = datetime.now().year
    valid_years = parsed.dt.year
    implausible_mask = valid_years.notna() & (
        (valid_years < _MIN_YEAR) | (valid_years > now_year + _MAX_YEAR_AHEAD)
    )
    implausible_idx = non_null.index[implausible_mask].tolist()

    issue_idx = sorted(set(unparseable_idx) | set(implausible_idx))
    return _result(
        col_name,
        issue_idx,
        "invalid_or_implausible_date",
        extra={
            "unparseable_count": len(unparseable_idx),
            "implausible_count": len(implausible_idx),
            "valid_year_range": [_MIN_YEAR, now_year + _MAX_YEAR_AHEAD],
        },
    )


def _check_email_format(series: pd.Series, col_name: str) -> CheckResult | None:
    if not _EMAIL_NAME_RE.search(col_name):
        return None
    non_null = series.dropna()
    if non_null.empty:
        return _passed_skip(col_name, "all_null_or_empty")
    invalid_idx = [
        idx for idx, v in non_null.items() if not _EMAIL_RE.search(str(v))
    ]
    return _result(col_name, invalid_idx, "invalid_email_format")


def _check_cross_column_date_rules(
    df: pd.DataFrame, rules: list[dict[str, Any]] | None = None
) -> list[CheckResult]:
    rules = rules if rules is not None else default_cross_column_rules()
    results: list[CheckResult] = []
    for rule in rules:
        left, right, op = rule.get("left"), rule.get("right"), rule.get("op", ">=")
        rule_name = rule.get("name", f"{left}_{op}_{right}")
        col_label = f"{left} vs {right}"
        if left not in df.columns or right not in df.columns:
            results.append(
                _passed_skip(
                    col_label, "skipped_columns_not_present", rule=rule_name
                )
            )
            continue
        try:
            left_dates = pd.to_datetime(df[left], errors="coerce", format="mixed")
            right_dates = pd.to_datetime(df[right], errors="coerce", format="mixed")
            both_present = left_dates.notna() & right_dates.notna()
            if op == ">=":
                violation = both_present & (left_dates < right_dates)
            elif op == "<=":
                violation = both_present & (left_dates > right_dates)
            elif op == ">":
                violation = both_present & (left_dates <= right_dates)
            elif op == "<":
                violation = both_present & (left_dates >= right_dates)
            else:
                results.append(_error_result(col_label, f"unsupported operator: {op}"))
                continue
            issue_idx = df.index[violation].tolist()
            results.append(
                _result(
                    col_label,
                    issue_idx,
                    f"cross_column_date_rule:{rule_name}",
                    extra={"left": left, "right": right, "op": op},
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(_error_result(col_label, str(exc)))
    return results


def check_validity_frame(
    df: pd.DataFrame,
    roles: dict[str, str] | None = None,
    cross_column_rules: list[dict[str, Any]] | None = None,
) -> list[CheckResult]:
    """
    Run all validity sub-checks across df. One CheckResult per applicable
    rule per column, plus one per cross-column date-order rule. Columns
    with no applicable validity rule still get a single "passed/skipped"
    CheckResult, matching the exhaustive per-column reporting style used
    by missing_values.py / type_mismatch.py.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.shape[1] == 0:
            return [
                CheckResult(
                    check_name="validity",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_columns"},
                    dimension="validity",
                )
            ]

        column_roles = roles if roles is not None else classify_columns(df)
        suspicious_cols = set(suspicious_zero_columns_present(df))

        results: list[CheckResult] = []
        for col in df.columns:
            col_name = str(col)
            role = column_roles.get(col)
            series = df[col]
            applied: list[CheckResult] = []

            if role == ROLE_MEASUREMENT:
                r = _check_non_negative(series, col_name)
                if r is not None:
                    applied.append(r)
                r = _check_suspicious_zero(series, col_name, suspicious_cols)
                if r is not None:
                    applied.append(r)

            if role == ROLE_DATE:
                applied.append(_check_date_column(series, col_name))

            r = _check_email_format(series, col_name)
            if r is not None:
                applied.append(r)

            if not applied:
                results.append(
                    _passed_skip(col_name, "no_validity_rule_for_role", role=role)
                )
            else:
                results.extend(applied)

        results.extend(_check_cross_column_date_rules(df, cross_column_rules))
        return results
    except Exception as exc:  # noqa: BLE001
        return [_error_result(None, str(exc))]
