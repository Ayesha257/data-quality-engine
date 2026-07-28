"""Duplicate-row check -> Uniqueness dimension.

Tool choice (plan Section 2, Task 2): pandas .duplicated() for exact indices.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from data_quality_engine.engine.models import CheckResult


def check_duplicates(
    df: pd.DataFrame,
    subset: Sequence[str] | None = None,
) -> list[CheckResult]:
    """
    Flag duplicate rows (keep='first').
    If subset is provided, duplicates are evaluated on those key columns only.
    dimension = "uniqueness"
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.empty:
            return [
                CheckResult(
                    check_name="duplicates",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "empty_dataframe"},
                    dimension="uniqueness",
                )
            ]

        if subset is not None:
            missing = [c for c in subset if c not in df.columns]
            if missing:
                raise KeyError(f"subset columns not in dataframe: {missing}")

        dup_mask = df.duplicated(subset=list(subset) if subset else None, keep="first")
        dup_idx = df.index[dup_mask].tolist()
        issues = len(dup_idx)
        status = "passed" if issues == 0 else "failed"
        return [
            CheckResult(
                check_name="duplicates",
                status=status,
                column=",".join(subset) if subset else None,
                issues_found=issues,
                details={
                    "duplicate_count": issues,
                    "total_rows": len(df),
                    "subset": list(subset) if subset else None,
                    "row_indices": dup_idx[:100],
                    "row_indices_truncated": issues > 100,
                },
                dimension="uniqueness",
            )
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name="duplicates",
                status="error",
                column=None,
                issues_found=0,
                details={"error": str(exc)},
                dimension="uniqueness",
            )
        ]
