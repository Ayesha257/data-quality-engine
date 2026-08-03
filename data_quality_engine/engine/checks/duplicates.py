"""Duplicate-row / duplicate-key checks -> Uniqueness dimension.

Tool choice (plan Section 2, Task 2): pandas ``.duplicated()`` for exact
row-level indices.

Plan Section 5 (Uniqueness): "How many duplicate rows **or duplicate keys**
exist". Full-row-only checks miss ERP ship-to / multi-address lists where the
same ``Customer No.`` appears on several non-identical rows. This module
therefore reports:

1. Exact **full-row** duplicates (every column equal).
2. **Business-key** duplicates on inferred / configured ID columns
   (e.g. ``Customer No.``, ``Supplier Code``).

``check_duplicates(df, subset=...)`` keeps the original single-check API.
``check_duplicates_frame(df)`` is what the CLI uses so both layers are visible.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.models import CheckResult

# Strong uniqueness-key name hints (ERP exports). Prefer these over generic
# "code" columns that may be categorical (e.g. Add. Code = HO/WHS).
_PRIMARY_KEY_NAME_RE = re.compile(
    r"(customer\s*no\.?|supplier\s*(code|no\.?)|product(\s*code)?|"
    r"invoice\s*(no\.?|number)|order\s*(no\.?|number)|\bsku\b|"
    r"(^|_)id$|(^|_)id\b)",
    re.I,
)
# Line/sequence columns that turn a parent order/invoice key into a row key.
_LINE_COL_NAME_RE = re.compile(
    r"(line(\s*(no\.?|number|#)?)|seq(uence)?(\s*(no\.?|number|#)?)|"
    r"item\s*(no\.?|number|#))",
    re.I,
)
# Assumption: if this share of rows are keep='first' extras on a single key,
# the column is a one-to-many parent key (line-item table), not a unique ID.
_ONE_TO_MANY_EXTRA_RATIO = 0.2


# Assumption: one-to-many parent-key promotion applies to order/invoice IDs
# (line-item tables), not customer/supplier master keys.
_PARENT_LINEITEM_KEY_RE = re.compile(
    r"(order\s*(no\.?|number)|invoice\s*(no\.?|number))",
    re.I,
)


def _find_line_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if _LINE_COL_NAME_RE.search(str(col).strip()):
            return col
    return None


def _is_likely_one_to_many_parent_key(df: pd.DataFrame, key: str) -> bool:
    if key not in df.columns or df.empty:
        return False
    if not _PARENT_LINEITEM_KEY_RE.search(str(key).strip()):
        return False
    extras = int(df.duplicated(subset=[key], keep="first").sum())
    return (extras / len(df)) >= _ONE_TO_MANY_EXTRA_RATIO


def _error_result(
    message: str,
    *,
    column: str | None = None,
    check_name: str = "duplicates",
) -> CheckResult:
    return CheckResult(
        check_name=check_name,
        status="error",
        column=column,
        issues_found=0,
        details={"error": message},
        dimension="uniqueness",
    )


def infer_uniqueness_keys(df: pd.DataFrame) -> list[str]:
    """
    Infer likely business-key columns for duplicate-key checks.

    Purpose
        Pick ID-like columns (Customer No., Supplier Code, ...) so uniqueness
        is evaluated on keys as well as full rows.

    Arguments
        df: Loaded dataframe (header already applied).

    Returns
        Ordered list of column names to treat as uniqueness keys. Empty if
        none match. Honours ``SETTINGS["duplicate_key_columns"]`` when set
        (explicit override; columns missing from ``df`` are skipped).

    Raises
        TypeError: If ``df`` is not a DataFrame.
    """
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    configured = SETTINGS.get("duplicate_key_columns")
    if configured:
        return [c for c in configured if c in df.columns]

    keys: list[str] = []
    for col in df.columns:
        name = str(col).strip()
        if _PRIMARY_KEY_NAME_RE.search(name):
            # Skip columns that are almost all null — not useful as keys.
            non_null = df[col].dropna()
            if non_null.empty:
                continue
            keys.append(col)
    return keys


def _normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    """
    Light normalization so trivial formatting differences do not hide
    exact duplicates: strip string cells, map empty strings to NA.
    Does not mutate the caller's frame.
    """
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            stripped = out[col].map(
                lambda v: v.strip() if isinstance(v, str) else v
            )
            out[col] = stripped.replace("", pd.NA)
    return out


def _duplicate_groups(
    df: pd.DataFrame,
    mask: pd.Series,
    subset: Sequence[str] | None,
    max_groups: int = 15,
) -> list[dict[str, Any]]:
    """Summarize duplicate sets for the report (value -> indices)."""
    if not mask.any():
        return []

    involved = df.loc[mask]
    groups: list[dict[str, Any]] = []

    if subset:
        grouped = involved.groupby(list(subset), dropna=False, sort=False)
        for key_vals, grp in grouped:
            if len(grp) < 2:
                continue
            if len(subset) == 1:
                key_repr: Any = key_vals
            else:
                key_repr = dict(zip(subset, key_vals if isinstance(key_vals, tuple) else (key_vals,)))
            groups.append(
                {
                    "key": key_repr,
                    "count": int(len(grp)),
                    "row_indices": grp.index.tolist()[:50],
                }
            )
            if len(groups) >= max_groups:
                break
    else:
        # Full-row: group by all columns (may be heavy; only on already-masked rows).
        cols = list(df.columns)
        grouped = involved.groupby(cols, dropna=False, sort=False)
        for _, grp in grouped:
            if len(grp) < 2:
                continue
            sample = {c: grp.iloc[0][c] for c in cols[:6]}
            groups.append(
                {
                    "key_preview": sample,
                    "count": int(len(grp)),
                    "row_indices": grp.index.tolist()[:50],
                }
            )
            if len(groups) >= max_groups:
                break

    return groups


def check_duplicates(
    df: pd.DataFrame,
    subset: Sequence[str] | None = None,
    *,
    normalize: bool | None = None,
) -> list[CheckResult]:
    """
    Flag duplicate rows (``keep='first'`` extras count as issues).

    Purpose
        Exact uniqueness check via pandas ``duplicated``. With ``subset``,
        only those key columns participate (duplicate *keys*). Without
        ``subset``, every column must match (duplicate *rows*).

    Arguments
        df: Input frame.
        subset: Optional key column(s). ``None`` = full-row comparison.
        normalize: Strip strings / treat ``""`` as null before comparing.
            Defaults to ``SETTINGS["duplicate_normalize_strings"]`` (True).

    Returns
        A one-element list with a ``CheckResult`` (``dimension="uniqueness"``).
        ``details`` includes ``row_indices``, ``duplicate_set_rows`` (all rows
        in a duplicate group, ``keep=False``), and ``duplicate_groups``.

    Raises
        Nothing — failures become ``status="error"``.
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

        do_norm = (
            SETTINGS.get("duplicate_normalize_strings", True)
            if normalize is None
            else bool(normalize)
        )
        work = _normalize_for_compare(df) if do_norm else df
        subset_list = list(subset) if subset else None

        dup_extra = work.duplicated(subset=subset_list, keep="first")
        dup_all = work.duplicated(subset=subset_list, keep=False)
        dup_idx = work.index[dup_extra].tolist()
        set_idx = work.index[dup_all].tolist()
        issues = len(dup_idx)
        status = "passed" if issues == 0 else "failed"

        groups = _duplicate_groups(work, dup_all, subset_list)

        check_name = "duplicates" if subset is None else "duplicate_keys"
        return [
            CheckResult(
                check_name=check_name,
                status=status,
                column=",".join(subset_list) if subset_list else None,
                issues_found=issues,
                details={
                    "duplicate_count": issues,
                    "duplicate_set_rows": len(set_idx),
                    "total_rows": len(df),
                    "subset": subset_list,
                    "normalized": do_norm,
                    "row_indices": dup_idx[:200],
                    "row_indices_truncated": issues > 200,
                    "duplicate_set_indices": set_idx[:200],
                    "duplicate_set_indices_truncated": len(set_idx) > 200,
                    "duplicate_groups": groups,
                    "unique_keys_repeated": len(groups),
                    "note": (
                        "row_indices are 0-based positions in the loaded "
                        "dataframe (after header confirmation), not Excel "
                        "row numbers."
                    ),
                },
                dimension="uniqueness",
            )
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _error_result(
                str(exc),
                column=",".join(subset) if subset else None,
                check_name="duplicates" if subset is None else "duplicate_keys",
            )
        ]


def check_duplicates_frame(
    df: pd.DataFrame,
    key_columns: Sequence[str] | None = None,
    *,
    normalize: bool | None = None,
) -> list[CheckResult]:
    """
    Run full-row duplicate check plus business-key uniqueness checks.

    Purpose
        Match plan Uniqueness ("duplicate rows or duplicate keys") in one call
        for the CLI / scoring layer.

    Arguments
        df: Input frame.
        key_columns: Optional uniqueness key. When provided, treated as a
            **single compound key** (all columns together), e.g.
            ``["Customer No.", "Add. Code"]``. When ``None``, falls back to
            ``infer_uniqueness_keys`` and runs one single-column key check
            per inferred column (legacy behaviour).
        normalize: Forwarded to ``check_duplicates``.

    Returns
        ``[full_row_result, ...key_results]``. Always at least the full-row
        result. Key checks are omitted when no keys are available.

    Raises
        Nothing — errors are captured as ``status="error"`` results.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        results: list[CheckResult] = []
        results.extend(check_duplicates(df, subset=None, normalize=normalize))

        if key_columns is not None:
            # Caller-supplied compound (or single) key — one subset check.
            key_list = [c for c in key_columns if c in df.columns]
            if key_list:
                results.extend(
                    check_duplicates(df, subset=key_list, normalize=normalize)
                )
        else:
            # Inferred single-column keys; promote or skip one-to-many parents.
            for key in infer_uniqueness_keys(df):
                if _is_likely_one_to_many_parent_key(df, key):
                    line_col = _find_line_column(df)
                    if line_col is not None and line_col != key:
                        results.extend(
                            check_duplicates(
                                df, subset=[key, line_col], normalize=normalize
                            )
                        )
                    else:
                        results.append(
                            CheckResult(
                                check_name="duplicate_keys",
                                status="passed",
                                column=str(key),
                                issues_found=0,
                                details={
                                    "reason": "likely_one_to_many_parent_key",
                                    "subset": [str(key)],
                                    "total_rows": len(df),
                                },
                                dimension="uniqueness",
                            )
                        )
                else:
                    results.extend(
                        check_duplicates(df, subset=[key], normalize=normalize)
                    )

        return results
    except Exception as exc:  # noqa: BLE001
        return [_error_result(str(exc))]
