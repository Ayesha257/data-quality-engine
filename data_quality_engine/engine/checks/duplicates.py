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

Which columns count as case (2) is decided by evidence, not by name alone:
see ``uniqueness_evidence`` / ``infer_uniqueness_keys`` below. A column name
that "sounds like an ID" (e.g. "Product Code", "Country Code") is only
treated as a uniqueness key once its uniqueness ratio, repeated-value
frequency, and inferred column role also support that. This is what keeps a
legitimately repeated ``Product Description`` (or a categorical "*Code"
column) from being flagged as a duplicate-key violation.

``check_duplicates(df, subset=...)`` keeps the original single-check API.
``check_duplicates_frame(df)`` is what the CLI uses so both layers are visible.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_FREE_TEXT,
    ROLE_IDENTIFIER,
    ROLE_MEASUREMENT,
    classify_column,
)
from data_quality_engine.engine.models import CheckResult

# Strong uniqueness-key name hints (ERP exports). Prefer these over generic
# "code" columns that may be categorical (e.g. Add. Code = HO/WHS).
_PRIMARY_KEY_NAME_RE = re.compile(
    r"(customer\s*no\.?|supplier\s*(code|no\.?)|product(\s*code)?|"
    r"invoice\s*(no\.?|number)|order\s*(no\.?|number)|\bsku\b|"
    r"\bemail\b|e-?mail\s*address|account\s*(no\.?|number)|"
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


def uniqueness_evidence(df: pd.DataFrame, col: Any) -> dict[str, Any]:
    """
    Score how much evidence there is that ``col`` is *meant* to be unique
    per row (a business key / identifier), instead of trusting the column
    name alone.

    Purpose
        A column named "Product Code" or "Country Code" is not automatically
        a uniqueness key just because "Code" sounds important -- a repeated
        Product Description is normal, a repeated Invoice Number usually
        isn't. This combines several independent signals into one score so
        that decision is evidence-based and auditable (each signal is
        visible in the returned dict, and ends up in the report's
        ``details`` for transparency):

        - ``name_match``: does the column name match known business-key
          naming patterns (Customer No., SKU, Email, ...)? Necessary but
          not sufficient on its own.
        - ``uniqueness_ratio``: distinct values / non-null values. An
          intended identifier should be close to 1.0; a repeated status or
          category column will not be.
        - ``top_value_ratio``: share of rows taken by the single most
          common value. A column dominated by one repeated value (e.g. a
          category masquerading as a "code") is unlikely to be a real key,
          even if fairly high-cardinality overall.
        - ``role``: output of ``column_classifier.classify_column`` --
          "identifier" supports uniqueness intent, while "categorical",
          "free_text" or "measurement" argue against it (business meaning:
          descriptions/notes/measurements are expected to repeat).

    Arguments
        df: Loaded dataframe.
        col: Column to score (must be in ``df.columns``).

    Returns
        Dict with the individual signals, the combined ``score`` (0-1), the
        ``threshold`` it was compared against, and the final boolean
        ``expected_unique`` verdict.

        Below ``SETTINGS["uniqueness_evidence_min_rows"]`` non-null values,
        ratio/frequency evidence is too noisy to trust either way, so the
        verdict falls back to the name-pattern signal alone.
    """
    series = df[col]
    non_null = series.dropna()
    n = len(non_null)
    name_match = bool(_PRIMARY_KEY_NAME_RE.search(str(col).strip()))
    min_rows = int(SETTINGS.get("uniqueness_evidence_min_rows", 20))
    threshold = float(SETTINGS.get("uniqueness_key_min_score", 0.6))

    if n == 0:
        return {
            "column": str(col),
            "n": 0,
            "uniqueness_ratio": 0.0,
            "top_value_ratio": 0.0,
            "role": None,
            "name_match": name_match,
            "score": 0.0,
            "threshold": threshold,
            "expected_unique": False,
            "reason": "no non-null values",
        }

    ratio = float(non_null.nunique()) / float(n)
    role = classify_column(series, col)
    value_counts = non_null.value_counts()
    top_value_ratio = float(value_counts.iloc[0]) / float(n) if not value_counts.empty else 0.0

    score = 0.0
    if ratio >= 0.98:
        score += 0.50
    elif ratio >= 0.90:
        score += 0.30
    elif ratio >= 0.70:
        score += 0.10

    if role == ROLE_IDENTIFIER:
        score += 0.25
    elif role in (ROLE_CATEGORICAL, ROLE_FREE_TEXT, ROLE_MEASUREMENT):
        score -= 0.20

    if name_match:
        score += 0.25

    # One value repeated across a meaningful share of rows is strong
    # evidence against uniqueness intent, but only trust this once there
    # are enough rows for the ratio to mean anything.
    if n >= min_rows and top_value_ratio >= 0.05:
        score -= 0.35

    score = max(0.0, min(1.0, score))

    if n < min_rows:
        # Not enough data to confirm or veto with statistics either way --
        # fall back to the name signal (existing, well-tested behaviour).
        expected_unique = name_match
    else:
        expected_unique = name_match and score >= threshold

    return {
        "column": str(col),
        "n": n,
        "uniqueness_ratio": round(ratio, 4),
        "top_value_ratio": round(top_value_ratio, 4),
        "role": role,
        "name_match": name_match,
        "score": round(score, 3),
        "threshold": threshold,
        "expected_unique": expected_unique,
    }


def _data_driven_identifier_candidates(
    df: pd.DataFrame, exclude: set[Any]
) -> list[Any]:
    """
    Find columns that *behave* like identifiers even when their name gives
    no hint (renamed exports, generic headers). Only fires with enough rows
    to trust the statistics, so it never changes behaviour on small
    hand-built test frames.
    """
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
        if classify_column(df[col], col) != ROLE_IDENTIFIER:
            continue
        found.append(col)
    return found


def infer_uniqueness_keys(
    df: pd.DataFrame, *, with_evidence: bool = False
) -> list[str] | tuple[list[str], dict[str, dict[str, Any]]]:
    """
    Infer likely business-key columns for duplicate-key checks.

    Purpose
        Pick columns that are actually *evidenced* to be intended-unique
        (Customer No., Supplier Code, Email, ...) rather than any column
        whose name merely "sounds important". A column is only kept when:
        (1) its name matches a known business-key pattern, or it is
        statistically identifier-shaped even without a matching name, AND
        (2) ``uniqueness_evidence`` confirms it (uniqueness ratio,
        repeated-value frequency, and classifier role all considered) --
        see that function's docstring for the full reasoning. This is what
        stops a repeated "Product Description" or a categorical "*Code"
        column from being auto-flagged as a duplicate-key violation.

    Arguments
        df: Loaded dataframe (header already applied).
        with_evidence: When True, also return the per-column evidence dict
            (useful for the report / CLI to show *why* a column was or
            wasn't picked).

    Returns
        Ordered list of column names to treat as uniqueness keys (empty if
        none qualify). When ``with_evidence=True``, returns
        ``(keys, evidence)`` where ``evidence`` maps column name -> the dict
        from ``uniqueness_evidence`` (plus ``source`` for configured /
        name-matched / data-driven columns).

        Honours ``SETTINGS["duplicate_key_columns"]`` when set (explicit
        override skips all scoring -- that is a deliberate business-rule
        decision by the caller, not a guess).

    Raises
        TypeError: If ``df`` is not a DataFrame.
    """
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    configured = SETTINGS.get("duplicate_key_columns")
    if configured:
        keys = [c for c in configured if c in df.columns]
        if with_evidence:
            evidence = {
                str(k): {"source": "configured_override", "expected_unique": True}
                for k in keys
            }
            return keys, evidence
        return keys

    name_candidates: list[Any] = []
    for col in df.columns:
        name = str(col).strip()
        if _PRIMARY_KEY_NAME_RE.search(name):
            # Skip columns that are almost all null — not useful as keys.
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
        total_rows = len(df)
        # Graded ratio: fraction of rows that are NOT a duplicate extra.
        # 5 duplicate rows out of 533 (99.1% unique) must not score the same
        # as e.g. 400/533 duplicated -- both previously collapsed this whole
        # check to a single failed=0 result in scoring.
        quality_ratio = 1.0 - (issues / total_rows) if total_rows else 1.0

        groups = _duplicate_groups(work, dup_all, subset_list)

        check_name = "duplicates" if subset is None else "duplicate_keys"
        return [
            CheckResult(
                check_name=check_name,
                status=status,
                column=",".join(subset_list) if subset_list else None,
                issues_found=issues,
                quality_ratio=round(quality_ratio, 6),
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
            keys, evidence = infer_uniqueness_keys(df, with_evidence=True)
            for key in keys:
                key_evidence = evidence.get(str(key))
                if _is_likely_one_to_many_parent_key(df, key):
                    line_col = _find_line_column(df)
                    if line_col is not None and line_col != key:
                        for r in check_duplicates(
                            df, subset=[key, line_col], normalize=normalize
                        ):
                            r.details["uniqueness_evidence"] = key_evidence
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
                                    "uniqueness_evidence": key_evidence,
                                },
                                dimension="uniqueness",
                            )
                        )
                else:
                    for r in check_duplicates(df, subset=[key], normalize=normalize):
                        r.details["uniqueness_evidence"] = key_evidence
                        results.append(r)

        return results
    except Exception as exc:  # noqa: BLE001
        return [_error_result(str(exc))]