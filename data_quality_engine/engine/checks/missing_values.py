"""Missing-value check -> Completeness dimension.

Tool choice (plan Section 2, Task 2): pandas .isna() for exact row indices.
"""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.models import CheckResult


def check_missing_values(df: pd.DataFrame) -> list[CheckResult]:
    """
    For each column: count nulls, compute % missing.
    dimension = "completeness"
    Returns one CheckResult per column.
    """
    results: list[CheckResult] = []
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.empty or len(df.columns) == 0:
            return [
                CheckResult(
                    check_name="missing_values",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "empty_dataframe"},
                    dimension="completeness",
                )
            ]

        total_rows = len(df)
        for col in df.columns:
            mask = df[col].isna()
            missing_idx = df.index[mask].tolist()
            issues = len(missing_idx)
            pct = (issues / total_rows * 100.0) if total_rows else 0.0
            status = "passed" if issues == 0 else "failed"
            # Graded ratio: fraction of rows that ARE filled in this column.
            # A column missing 9/533 values (98.3% complete) must not score
            # the same as a column missing 500/533 (6.2% complete) -- both
            # were previously collapsed to status="failed" -> 0 in scoring.
            quality_ratio = 1.0 - (issues / total_rows) if total_rows else 1.0
            results.append(
                CheckResult(
                    check_name="missing_values",
                    status=status,
                    column=str(col),
                    issues_found=issues,
                    quality_ratio=round(quality_ratio, 6),
                    details={
                        "missing_count": issues,
                        "missing_pct": round(pct, 4),
                        "total_rows": total_rows,
                        "row_indices": missing_idx[:100],  # cap for report size
                        "row_indices_truncated": issues > 100,
                    },
                    dimension="completeness",
                )
            )
        return results
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return [
            CheckResult(
                check_name="missing_values",
                status="error",
                column=None,
                issues_found=0,
                details={"error": str(exc)},
                dimension="completeness",
            )
        ]