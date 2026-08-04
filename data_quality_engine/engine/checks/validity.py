"""Validity Check & Business Rule Validation Engine Integration.

Runs:
1. JSON-configurable Business Rule Engine (single-column and cross-column rules).
2. Semantic role-based validity checks (non-negative numeric, unparseable dates, email format).
3. Domain cross-column comparison rules.
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
from data_quality_engine.config.rule_engine import BusinessRuleEngine
from data_quality_engine.engine.column_classifier import (
    ROLE_DATE,
    ROLE_MEASUREMENT,
    build_column_metadata_layer,
    classify_columns,
)
from data_quality_engine.engine.models import CheckResult
from data_quality_engine.engine.pii.detect_pii import TYPE_EMAIL, _EMAIL_RE

_NON_NEGATIVE_NAME_RE = re.compile(
    r"\b(qty|quantity|count|age|stock|units?|weight|hours?|days?)\b",
    re.I,
)
_EMAIL_NAME_RE = re.compile(r"email", re.I)

_MIN_YEAR = 1990
_MAX_YEAR_AHEAD = 1
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


def _result(
    col_name: str | None, issue_idx: list[Any], rule: str, extra: dict | None = None
) -> CheckResult:
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


def _check_suspicious_zero(
    series: pd.Series, col_name: str, suspicious_cols: set[str]
) -> CheckResult | None:
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


def _check_email_format(
    series: pd.Series,
    col_name: str,
    *,
    force: bool = False,
) -> CheckResult | None:
    if not force and not _EMAIL_NAME_RE.search(col_name):
        return None
    non_null = series.dropna()
    if non_null.empty:
        return _passed_skip(col_name, "all_null_or_empty")
    invalid_idx = [
        idx for idx, v in non_null.items() if not _EMAIL_RE.search(str(v))
    ]
    return _result(col_name, invalid_idx, "invalid_email_format")


def _column_has_email_pii(
    col_name: str,
    role: str | None,
    pii_summary_by_column: dict[str, dict[str, Any]] | None,
) -> bool:
    if not pii_summary_by_column:
        return False
    summary = pii_summary_by_column.get(col_name)
    if summary is None:
        return False
    type_counts = summary.get("type_counts") or {}
    return bool(type_counts.get(TYPE_EMAIL, 0) > 0)


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
    pii_summary_by_column: dict[str, dict[str, Any]] | None = None,
    column_metadata: dict[str, Any] | None = None,
) -> list[CheckResult]:
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

        results: list[CheckResult] = []

        # 1. JSON Business Rule Engine Execution (Only when no explicit cross_column_rules passed)
        if cross_column_rules is None:
            rule_engine = BusinessRuleEngine()
            json_results = rule_engine.evaluate_rules(df, roles=roles)
            results.extend(json_results)

        # 2. Built-in Semantic Role & Heuristic Validity Checks
        meta_layer = column_metadata or build_column_metadata_layer(df)
        column_roles = roles if roles is not None else classify_columns(df)
        suspicious_cols = set(suspicious_zero_columns_present(df))

        for col in df.columns:
            col_name = str(col)
            role = column_roles.get(col)
            meta = meta_layer.get(col_name)
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

            force_email = _column_has_email_pii(
                col_name, role, pii_summary_by_column
            )
            r = _check_email_format(series, col_name, force=force_email)
            if r is not None:
                applied.append(r)

            if not applied and not results:
                results.append(
                    _passed_skip(col_name, "skipped_no_rule_for_role", role=role)
                )
            else:
                results.extend(applied)

        # 3. Domain Cross-Column Rules Fallback
        results.extend(_check_cross_column_date_rules(df, cross_column_rules))
        return results
    except Exception as exc:  # noqa: BLE001
        return [_error_result(None, str(exc))]
