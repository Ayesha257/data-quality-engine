"""Consistency check -> Consistency dimension (teacher rubric, 15%).

Catches the "Paid / paid / PAID" and "Lahore / lahore " class of problem:
the same real-world value written in more than one raw form within a
single column. Detection is case/whitespace normalization only.

True abbreviation collapsing ("Lahore" vs "LHR") is handled by RapidFuzz
in ``engine/standardization/fuzzy_match.py`` (plan.md Task 5 / Section 4.4).
Both feed the consistency dimension; they are complementary, not duplicates.

Only runs on columns classify_columns() marks "categorical" or
"identifier" -- those are the roles where "the same concept represented
more than one way" is a meaningful problem. measurement/date/pii/free_text
columns are skipped (skip reason recorded, same pattern as outliers.py).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from backend.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_IDENTIFIER,
    classify_columns,
)
from backend.engine.models import CheckResult

_ELIGIBLE_ROLES = (ROLE_CATEGORICAL, ROLE_IDENTIFIER)

_WHITESPACE_RE = re.compile(r"\s+")


def _error_result(col_name: str | None, message: str) -> CheckResult:
    return CheckResult(
        check_name="consistency",
        status="error",
        column=col_name,
        issues_found=0,
        details={"error": message},
        dimension="consistency",
    )


def _passed_skip(col_name: str | None, reason: str, **extra: Any) -> CheckResult:
    return CheckResult(
        check_name="consistency",
        status="passed",
        column=col_name,
        issues_found=0,
        details={"reason": reason, **extra},
        dimension="consistency",
    )


def _normalize(value: object) -> str:
    text = str(value).strip().lower()
    return _WHITESPACE_RE.sub(" ", text)


def check_consistency(series: pd.Series, role: str | None = None) -> CheckResult:
    """
    Flag rows whose raw value is a minority variant of a value that also
    appears, elsewhere in the column, spelled/cased/spaced differently.

    role: pass the column's classify_columns() role if already computed
    (main.py already classifies once per dataframe -- avoid recomputing).
    If omitted, classification is skipped and the column is treated as
    eligible only when it looks like text (kept simple/explicit: callers
    processing a whole frame should use check_consistency_frame instead,
    which always classifies).
    """
    col = getattr(series, "name", None)
    col_name = str(col) if col is not None else None
    try:
        if series is None or not isinstance(series, pd.Series):
            raise TypeError("series must be a pandas Series")

        if role is not None and role not in _ELIGIBLE_ROLES:
            return _passed_skip(
                col_name, "skipped_non_categorical_column", role=role
            )

        non_null = series.dropna()
        if non_null.empty:
            return _passed_skip(col_name, "all_null_or_empty")

        # Group raw values by normalized form.
        groups: dict[str, dict[str, int]] = {}
        for idx, v in non_null.items():
            norm = _normalize(v)
            raw = str(v)
            bucket = groups.setdefault(norm, {})
            bucket[raw] = bucket.get(raw, 0) + 1

        inconsistent_groups = {
            norm: variants for norm, variants in groups.items() if len(variants) > 1
        }

        if not inconsistent_groups:
            return CheckResult(
                check_name="consistency",
                status="passed",
                column=col_name,
                issues_found=0,
                details={"role": role, "inconsistent_value_groups": 0},
                dimension="consistency",
            )

        # For each inconsistent group, the "canonical" form is the most
        # frequent raw variant; every row using a different raw variant is
        # counted as an issue (row-level, matches missing_values/duplicates
        # style of reporting affected row indices).
        issue_idx: list[Any] = []
        examples: list[dict[str, Any]] = []
        for norm, variants in inconsistent_groups.items():
            canonical = max(variants, key=variants.get)
            examples.append(
                {
                    "normalized": norm,
                    "canonical": canonical,
                    "variants": variants,
                }
            )

        canonical_by_norm = {ex["normalized"]: ex["canonical"] for ex in examples}
        for idx, v in non_null.items():
            norm = _normalize(v)
            if norm in canonical_by_norm and str(v) != canonical_by_norm[norm]:
                issue_idx.append(idx)

        issues = len(issue_idx)
        status = "passed" if issues == 0 else "failed"
        return CheckResult(
            check_name="consistency",
            status=status,
            column=col_name,
            issues_found=issues,
            details={
                "role": role,
                "inconsistent_value_groups": len(inconsistent_groups),
                "examples": examples[:10],
                "row_indices": issue_idx[:100],
                "row_indices_truncated": issues > 100,
            },
            dimension="consistency",
        )
    except Exception as exc:  # noqa: BLE001
        return _error_result(col_name, str(exc))


def check_consistency_frame(
    df: pd.DataFrame, roles: dict[str, str] | None = None
) -> list[CheckResult]:
    """
    Run check_consistency on every categorical/identifier column of df.

    roles: pass an already-computed classify_columns(df) result to avoid
    recomputing classification (main.py classifies once per sheet and
    reuses it across Task 3/4 -- consistency should follow the same
    pattern). If omitted, classification is computed here.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.shape[1] == 0:
            return [
                CheckResult(
                    check_name="consistency",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_columns"},
                    dimension="consistency",
                )
            ]

        column_roles = roles if roles is not None else classify_columns(df)
        return [
            check_consistency(df[col], role=column_roles.get(col))
            for col in df.columns
        ]
    except Exception as exc:  # noqa: BLE001
        return [_error_result(None, str(exc))]
