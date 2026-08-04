"""Redesigned Duplicate Detection & Multi-Signal Uniqueness Confidence Engine.

Separates duplicate detection into:
1. Full-row duplicates (entire row identical).
2. Business-key duplicates (only for columns expected to be unique).

Multi-Signal Uniqueness Confidence Scoring evaluates 6 signals:
- column_name (Key/ID hints vs Descriptive hints like City, Country, Desc, Category)
- semantic_role (primary_key, business_key, identifier vs category, free_text)
- datatype (integer/string code vs float/object text)
- uniqueness_ratio (nunique / non_null_count)
- repeated_value_frequency (top_value_count / non_null_count)
- cardinality

Descriptive columns (City, Country, Category, Product Description) receive low
confidence scores (< 0.60) and are NEVER falsely flagged as business-key duplicates.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.column_classifier import (
    ROLE_BUSINESS_KEY,
    ROLE_CATEGORY,
    ROLE_FREE_TEXT,
    ROLE_IDENTIFIER,
    ROLE_PRIMARY_KEY,
    classify_semantic_role,
    compute_column_uniqueness_confidence,
)
from data_quality_engine.engine.models import CheckResult

_PRIMARY_KEY_NAME_RE = re.compile(
    r"(customer\s*no\.?|supplier\s*(code|no\.?)|product(\s*code)?|"
    r"invoice\s*(no\.?|number)|order\s*(no\.?|number)|\bsku\b|"
    r"\bemail\b|e-?mail\s*address|account\s*(no\.?|number)|"
    r"(^|_)id$|(^|_)id\b)",
    re.I,
)

_DESCRIPTIVE_NAME_RE = re.compile(
    r"(city|country|description|desc|category|prod\s*desc|product\s*description|"
    r"address\s*line|name|state|region|status|type|group|comment|notes)",
    re.I,
)

_LINE_COL_NAME_RE = re.compile(
    r"(line(\s*(no\.?|number|#)?)|seq(uence)?(\s*(no\.?|number|#)?)|"
    r"item\s*(no\.?|number|#))",
    re.I,
)

_ONE_TO_MANY_EXTRA_RATIO = 0.20

_PARENT_LINEITEM_KEY_RE = re.compile(
    r"(order\s*(no\.?|number)|invoice\s*(no\.?|number))",
    re.I,
)


def _find_line_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if _LINE_COL_NAME_RE.search(str(col).strip()):
            return str(col)
    return None


def _is_likely_one_to_many_parent_key(df: pd.DataFrame, key: str) -> bool:
    if key not in df.columns or df.empty:
        return False
    if not _PARENT_LINEITEM_KEY_RE.search(str(key).strip()):
        return False
    extras = int(df.duplicated(subset=[key], keep="first").sum())
    return (extras / float(len(df))) >= _ONE_TO_MANY_EXTRA_RATIO


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


def uniqueness_evidence(df: pd.DataFrame, col: Any) -> dict[str, Any]:
    """Generate multi-signal uniqueness evidence and confidence score for col."""
    series = df[col]
    non_null = series.dropna()
    n = len(non_null)
    col_name = str(col).strip()

    name_match = bool(_PRIMARY_KEY_NAME_RE.search(col_name))
    is_descriptive = bool(_DESCRIPTIVE_NAME_RE.search(col_name))

    role = classify_semantic_role(series, col_name)
    confidence = compute_column_uniqueness_confidence(series, col_name, role)

    min_rows = int(SETTINGS.get("uniqueness_evidence_min_rows", 20))
    threshold = float(SETTINGS.get("uniqueness_key_min_score", 0.60))

    if n == 0:
        return {
            "column": col_name,
            "n": 0,
            "uniqueness_ratio": 0.0,
            "top_value_ratio": 0.0,
            "role": role,
            "name_match": name_match,
            "is_descriptive": is_descriptive,
            "score": 0.0,
            "threshold": threshold,
            "expected_unique": False,
            "reason": "no non-null values",
        }

    ratio = float(non_null.nunique()) / float(n)
    vc = non_null.value_counts()
    top_freq = float(vc.iloc[0]) / float(n) if not vc.empty else 0.0

    if is_descriptive:
        expected_unique = False
    elif n < min_rows:
        expected_unique = name_match and not is_descriptive
    else:
        expected_unique = confidence >= threshold

    return {
        "column": col_name,
        "n": n,
        "uniqueness_ratio": round(ratio, 4),
        "top_value_ratio": round(top_freq, 4),
        "role": role,
        "name_match": name_match,
        "is_descriptive": is_descriptive,
        "score": confidence,
        "threshold": threshold,
        "expected_unique": expected_unique,
    }


def _data_driven_identifier_candidates(
    df: pd.DataFrame, exclude: set[Any]
) -> list[Any]:
    min_rows = int(SETTINGS.get("uniqueness_evidence_min_rows", 20))
    found: list[Any] = []
    for col in df.columns:
        if col in exclude:
            continue
        non_null = df[col].dropna()
        n = len(non_null)
        if n < min_rows:
            continue
        ratio = float(non_null.nunique()) / float(n)
        if ratio < 0.98:
            continue
        role = classify_semantic_role(df[col], str(col))
        if role in {ROLE_CATEGORY, ROLE_FREE_TEXT}:
            continue
        found.append(col)
    return found


def infer_uniqueness_keys(
    df: pd.DataFrame, *, with_evidence: bool = False
) -> list[str] | tuple[list[str], dict[str, dict[str, Any]]]:
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    configured = SETTINGS.get("duplicate_key_columns")
    if configured:
        keys = [c for c in configured if c in df.columns]
        if with_evidence:
            evidence = {
                str(k): {"source": "configured_override", "expected_unique": True, "score": 1.0}
                for k in keys
            }
            return keys, evidence
        return keys

    name_candidates: list[Any] = []
    for col in df.columns:
        name = str(col).strip()
        if _PRIMARY_KEY_NAME_RE.search(name) and not _DESCRIPTIVE_NAME_RE.search(name):
            if df[col].dropna().empty:
                continue
            name_candidates.append(col)

    data_candidates = _data_driven_identifier_candidates(
        df, exclude=set(name_candidates)
    )

    keys: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}

    for col in name_candidates:
        ev = uniqueness_evidence(df, col)
        ev["source"] = "name_pattern"
        evidence[str(col)] = ev
        if ev["expected_unique"]:
            keys.append(col)

    for col in data_candidates:
        ev = uniqueness_evidence(df, col)
        ev["source"] = "data_driven"
        evidence[str(col)] = ev
        if ev["expected_unique"]:
            keys.append(col)

    if with_evidence:
        return keys, evidence
    return keys


def _normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
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
    if not mask.any():
        return []

    involved = df.loc[mask]
    groups: list[dict[str, Any]] = []

    if subset:
        grouped = involved.groupby(list(subset), dropna=False, sort=False)
        for key_vals, grp in grouped:
            if len(grp) < 2:
                continue
            key_repr: Any = (
                key_vals if len(subset) == 1 else dict(zip(subset, key_vals if isinstance(key_vals, tuple) else (key_vals,)))
            )
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
                    "note": "row_indices are 0-based positions in loaded dataframe.",
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
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        results: list[CheckResult] = []

        # 1. Full-row duplicates check (entire row identical)
        results.extend(check_duplicates(df, subset=None, normalize=normalize))

        # 2. Business-key duplicates check
        if key_columns is not None:
            key_list = [c for c in key_columns if c in df.columns]
            if key_list:
                results.extend(
                    check_duplicates(df, subset=key_list, normalize=normalize)
                )
        else:
            keys, evidence = infer_uniqueness_keys(df, with_evidence=True)
            for key in keys:
                key_ev = evidence.get(str(key))
                if _is_likely_one_to_many_parent_key(df, key):
                    line_col = _find_line_column(df)
                    if line_col is not None and line_col != key:
                        for r in check_duplicates(
                            df, subset=[key, line_col], normalize=normalize
                        ):
                            r.details["uniqueness_evidence"] = key_ev
                            results.append(r)
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
                                    "uniqueness_evidence": key_ev,
                                },
                                dimension="uniqueness",
                            )
                        )
                else:
                    for r in check_duplicates(df, subset=[key], normalize=normalize):
                        r.details["uniqueness_evidence"] = key_ev
                        results.append(r)

        return results
    except Exception as exc:  # noqa: BLE001
        return [_error_result(str(exc))]