"""Outlier detection -> Validity dimension.

Phase 1 default: IQR (pandas/numpy) — explainable, fast, 0 false positives in testing.
Optional alternative: PyOD KNN — modular, enable via method="knn" for comparison/future use.

Interface stays stable: detect_outliers(series, method=...) -> CheckResult
so pipeline/API callers do not need to change when swapping methods.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.column_classifier import classify_columns
from data_quality_engine.engine.models import CheckResult

SUPPORTED_METHODS = ("iqr", "knn")


def _error_result(col_name: str | None, message: str) -> CheckResult:
    return CheckResult(
        check_name="outliers",
        status="error",
        column=col_name,
        issues_found=0,
        details={"error": message},
        dimension="validity",
    )


def _passed_skip(col_name: str | None, reason: str, **extra: Any) -> CheckResult:
    return CheckResult(
        check_name="outliers",
        status="passed",
        column=col_name,
        issues_found=0,
        details={"reason": reason, "method": extra.pop("method", "iqr"), **extra},
        dimension="validity",
    )


def _to_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _dominant_value_caveat(valid: pd.Series) -> dict[str, Any]:
    """
    If one value dominates the non-null numeric column, return caveat fields
    for CheckResult.details. Empty dict when concentration is below threshold.
    Does not change outlier counts — advisory only.
    """
    if valid.empty:
        return {}
    threshold = float(SETTINGS.get("outlier_dominant_value_ratio", 0.3))
    # mode() can return multiple; take the first most-frequent value
    counts = valid.value_counts(dropna=True)
    if counts.empty:
        return {}
    dominant_value = counts.index[0]
    dominant_count = int(counts.iloc[0])
    ratio = float(dominant_count) / float(len(valid))
    if ratio <= threshold:
        return {}
    # Preserve int-looking floats as int for readable reports
    display_value: Any = dominant_value
    try:
        as_float = float(dominant_value)
        if abs(as_float - round(as_float)) < 1e-9:
            display_value = int(round(as_float))
        else:
            display_value = as_float
    except (TypeError, ValueError):
        pass
    return {
        "dominant_value": display_value,
        "dominant_value_ratio": round(ratio, 4),
        "note": (
            "high concentration on one value -- IQR bounds may be "
            "unreliable for this distribution"
        ),
    }


def _detect_iqr(series: pd.Series, col_name: str | None) -> CheckResult:
    """
    IQR outlier detection (Phase 1 default).

    Lower = Q1 - 1.5 * IQR
    Upper = Q3 + 1.5 * IQR
    Values outside bounds (NaNs ignored) are flagged.
    """
    numeric = _to_numeric_series(series)
    valid = numeric.dropna()
    total_rows = int(len(series))
    numeric_count = int(len(valid))

    if numeric_count == 0:
        return _passed_skip(
            col_name,
            "no_numeric_values",
            method="iqr",
            total_rows=total_rows,
            numeric_count=0,
            outlier_pct=0.0,
        )

    # Need enough points to form meaningful quartiles
    if numeric_count < 4:
        return _passed_skip(
            col_name,
            "insufficient_numeric_values",
            method="iqr",
            total_rows=total_rows,
            numeric_count=numeric_count,
            outlier_pct=0.0,
        )

    multiplier = float(SETTINGS.get("iqr_multiplier", 1.5))
    q1 = float(valid.quantile(0.25))
    q3 = float(valid.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr

    # Constant column => IQR=0 => bounds collapse to Q1; no values outside
    outlier_mask = (numeric < lower) | (numeric > upper)
    outlier_mask = outlier_mask.fillna(False)
    outlier_idx = series.index[outlier_mask].tolist()
    outlier_vals = [float(v) for v in numeric[outlier_mask].tolist()]
    issues = len(outlier_idx)
    outlier_pct = round((issues / numeric_count) * 100.0, 4) if numeric_count else 0.0

    details: dict[str, Any] = {
        "method": "iqr",
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "lower_bound": lower,
        "upper_bound": upper,
        "outlier_count": issues,
        "outlier_pct": outlier_pct,
        "numeric_count": numeric_count,
        "total_rows": total_rows,
        "row_indices": outlier_idx[:100],
        "sample_values": outlier_vals[:20],
        "row_indices_truncated": issues > 100,
        "constant_column": iqr == 0.0,
    }
    # Additive caveat only — does not change issues_found / status
    details.update(_dominant_value_caveat(valid))

    return CheckResult(
        check_name="outliers",
        status="passed" if issues == 0 else "failed",
        column=col_name,
        issues_found=issues,
        details=details,
        dimension="validity",
    )


def _detect_knn(series: pd.Series, col_name: str | None) -> CheckResult:
    """
    Optional PyOD KNN outlier detector (comparison / future phases).

    Not the Phase 1 default. Requires optional dependency `pyod`.
    If pyod is missing, returns status="error" without crashing the pipeline.
    """
    try:
        from pyod.models.knn import KNN  # type: ignore
    except ImportError:
        return _error_result(
            col_name,
            "pyod is not installed. Install with: pip install pyod "
            "(optional; Phase 1 default remains IQR).",
        )

    numeric = _to_numeric_series(series)
    valid = numeric.dropna()
    total_rows = int(len(series))
    numeric_count = int(len(valid))

    if numeric_count == 0:
        return _passed_skip(
            col_name,
            "no_numeric_values",
            method="knn",
            total_rows=total_rows,
            numeric_count=0,
            outlier_pct=0.0,
        )

    # KNN needs enough neighbours
    n_neighbors = int(SETTINGS.get("outlier_knn_neighbors", 5))
    if numeric_count <= n_neighbors:
        return _passed_skip(
            col_name,
            "insufficient_numeric_values_for_knn",
            method="knn",
            total_rows=total_rows,
            numeric_count=numeric_count,
            n_neighbors=n_neighbors,
            outlier_pct=0.0,
        )

    contamination = float(SETTINGS.get("outlier_knn_contamination", 0.05))
    # Clamp contamination to valid PyOD range for small samples
    contamination = min(max(contamination, 1.0 / numeric_count), 0.5)

    X = valid.to_numpy(dtype=float).reshape(-1, 1)
    model = KNN(n_neighbors=n_neighbors, contamination=contamination)
    model.fit(X)
    labels = model.labels_  # 1 = outlier, 0 = inlier

    valid_index = valid.index.to_list()
    outlier_idx = [valid_index[i] for i, lab in enumerate(labels) if int(lab) == 1]
    outlier_vals = [float(valid.loc[i]) for i in outlier_idx]
    issues = len(outlier_idx)
    outlier_pct = round((issues / numeric_count) * 100.0, 4) if numeric_count else 0.0

    return CheckResult(
        check_name="outliers",
        status="passed" if issues == 0 else "failed",
        column=col_name,
        issues_found=issues,
        details={
            "method": "knn",
            "outlier_count": issues,
            "outlier_pct": outlier_pct,
            "numeric_count": numeric_count,
            "total_rows": total_rows,
            "n_neighbors": n_neighbors,
            "contamination": contamination,
            "row_indices": outlier_idx[:100],
            "sample_values": outlier_vals[:20],
            "row_indices_truncated": issues > 100,
        },
        dimension="validity",
    )


def detect_outliers(series: pd.Series, method: str = "iqr") -> CheckResult:
    """
    Detect outliers in a single column.

    method:
      - "iqr" (default, Phase 1)
      - "knn" (optional PyOD comparison)

    Never raises to the caller — returns CheckResult(status="error") on failure.
    dimension = "validity"
    """
    col = getattr(series, "name", None)
    col_name = str(col) if col is not None else None
    try:
        if series is None or not isinstance(series, pd.Series):
            raise TypeError("series must be a pandas Series")

        method_norm = (method or "iqr").strip().lower()
        if method_norm not in SUPPORTED_METHODS:
            raise ValueError(
                f"Unsupported outlier method: {method!r}. "
                f"Supported: {SUPPORTED_METHODS}. Phase 1 default is 'iqr'."
            )

        if method_norm == "iqr":
            return _detect_iqr(series, col_name)
        return _detect_knn(series, col_name)
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return _error_result(col_name, str(exc))


def detect_outliers_frame(df: pd.DataFrame, method: str = "iqr") -> list[CheckResult]:
    """
    Run outlier detection on every column that has at least one numeric value.
    Non-numeric / all-NaN columns are skipped (not treated as failures).
    Default method is IQR.
    """
    results: list[CheckResult] = []
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.empty or len(df.columns) == 0:
            return [
                CheckResult(
                    check_name="outliers",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "empty_dataframe", "method": method or "iqr"},
                    dimension="validity",
                )
            ]

        # Issue 1 fix: classify columns first so IQR/KNN never runs on
        # identifier-like columns (invoice numbers, customer codes, phone
        # numbers, postcodes) that happen to have numeric-looking values.
        classification = classify_columns(df)

        for col in df.columns:
            numeric = _to_numeric_series(df[col])
            if numeric.notna().sum() == 0:
                continue
            role = classification.get(col)
            if role is not None and role not in {"measurement", "financial"}:
                results.append(
                    _passed_skip(
                        str(col),
                        "skipped_non_measurement_column",
                        method=method or "iqr",
                        classified_role=role,
                    )
                )
                continue
            results.append(detect_outliers(df[col], method=method))

        if not results:
            return [
                CheckResult(
                    check_name="outliers",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_numeric_columns", "method": method or "iqr"},
                    dimension="validity",
                )
            ]
        return results
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name="outliers",
                status="error",
                column=None,
                issues_found=0,
                details={"error": str(exc)},
                dimension="validity",
            )
        ]
