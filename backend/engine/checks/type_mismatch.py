"""Type-consistency check -> Validity dimension.

Tool choice (plan Section 2, Task 2): pandas dtype inference + per-value probing.
Flags cells that do not match the column's dominant inferred type.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.engine.models import CheckResult


def _classify(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        # numeric-looking strings still count as string storage mismatches
        # only when the dominant type is number -- handled by caller
        return "string"
    return type(value).__name__


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not pd.isna(value)
    if isinstance(value, str):
        try:
            float(value.strip().replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def check_type_consistency(series: pd.Series) -> CheckResult:
    """
    Infer the dominant non-null type in the series and flag mismatched cells.
    dimension = "validity"
    """
    col = getattr(series, "name", None)
    col_name = str(col) if col is not None else None
    try:
        if series is None or not isinstance(series, pd.Series):
            raise TypeError("series must be a pandas Series")

        kinds: list[str] = []
        for v in series.tolist():
            k = _classify(v)
            if k is not None:
                kinds.append(k)

        if not kinds:
            return CheckResult(
                check_name="type_mismatch",
                status="passed",
                column=col_name,
                issues_found=0,
                details={"reason": "all_null_or_empty"},
                dimension="validity",
            )

        # Dominant type by frequency
        counts: dict[str, int] = {}
        for k in kinds:
            counts[k] = counts.get(k, 0) + 1
        dominant = max(counts, key=counts.get)

        mismatch_idx: list[Any] = []
        mismatch_values: list[Any] = []
        for idx, v in series.items():
            k = _classify(v)
            if k is None:
                continue
            if dominant == "number":
                if not _looks_numeric(v):
                    mismatch_idx.append(idx)
                    mismatch_values.append(v)
            elif k != dominant:
                mismatch_idx.append(idx)
                mismatch_values.append(v)

        issues = len(mismatch_idx)
        status = "passed" if issues == 0 else "failed"
        return CheckResult(
            check_name="type_mismatch",
            status=status,
            column=col_name,
            issues_found=issues,
            details={
                "dominant_type": dominant,
                "type_counts": counts,
                "row_indices": mismatch_idx[:100],
                "sample_values": [str(v) for v in mismatch_values[:20]],
                "row_indices_truncated": issues > 100,
            },
            dimension="validity",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name="type_mismatch",
            status="error",
            column=col_name,
            issues_found=0,
            details={"error": str(exc)},
            dimension="validity",
        )


def check_type_consistency_frame(df: pd.DataFrame) -> list[CheckResult]:
    """Run check_type_consistency on every column."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        return [check_type_consistency(df[col]) for col in df.columns]
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name="type_mismatch",
                status="error",
                column=None,
                issues_found=0,
                details={"error": str(exc)},
                dimension="validity",
            )
        ]
