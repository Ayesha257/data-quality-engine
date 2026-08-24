"""Fuzzy text matching / standardization — plan.md Section 4.4 (Task 5).

Chosen tool (plan Section 2): RapidFuzz (`fuzz.ratio`), faster C++-backed
alternative to TheFuzz with equivalent accuracy in Phase 1 tool testing.

Public contract from the plan:

    standardize_values(series, threshold=90) -> dict[str, str]

Returns ``{original_value: canonical_value}``. The caller applies the
mapping via ``series.map(mapping)`` (or ``apply_standardization``).

Dimension: consistency (same real-world value written different ways,
e.g. ``Lahore`` vs ``LHR`` when similarity exceeds the threshold).

Design notes
------------
* Clustering is greedy and frequency-seeded: most common values become
  cluster anchors first, which keeps canonical forms stable and avoids
  O(n²) pairwise work on every unique pair when n is large.
* ``threshold``, unique-value caps, and eligible column roles are
  configuration-driven via ``SETTINGS`` (no hardcoded magic numbers in
  call sites).
* Never mutates the input series; returns a pure mapping (or a derived
  Series via ``apply_standardization``).
* Check wrappers return ``CheckResult`` so the CLI / scoring layer can
  report fuzzy inconsistencies without changing the plan signature.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

from backend.config.settings import SETTINGS
from backend.engine.column_classifier import (
    ROLE_CATEGORICAL,
    ROLE_FREE_TEXT,
    classify_columns,
)
from backend.engine.models import CheckResult

# plan.md Section 2 / 4.4 default; SETTINGS may override at call time
_DEFAULT_THRESHOLD = 90
_DEFAULT_MAX_UNIQUE = 500
_DEFAULT_ELIGIBLE_ROLES = (ROLE_CATEGORICAL, ROLE_FREE_TEXT)


def _threshold(value: int | None) -> int:
    if value is not None:
        return int(value)
    return int(SETTINGS.get("fuzzy_threshold", _DEFAULT_THRESHOLD))


def _max_unique() -> int:
    return int(SETTINGS.get("fuzzy_max_unique", _DEFAULT_MAX_UNIQUE))


def _eligible_roles() -> tuple[str, ...]:
    configured = SETTINGS.get("fuzzy_eligible_roles")
    if configured:
        return tuple(str(r) for r in configured)
    return _DEFAULT_ELIGIBLE_ROLES


def _error_result(col_name: str | None, message: str) -> CheckResult:
    return CheckResult(
        check_name="fuzzy_standardization",
        status="error",
        column=col_name,
        issues_found=0,
        details={"error": message},
        dimension="consistency",
    )


def _passed_skip(col_name: str | None, reason: str, **extra: Any) -> CheckResult:
    return CheckResult(
        check_name="fuzzy_standardization",
        status="passed",
        column=col_name,
        issues_found=0,
        details={"reason": reason, **extra},
        dimension="consistency",
    )


def _case_insensitive() -> bool:
    return bool(SETTINGS.get("fuzzy_case_insensitive", True))


def _similarity(a: str, b: str) -> float:
    if _case_insensitive():
        return float(fuzz.ratio(a.casefold(), b.casefold()))
    return float(fuzz.ratio(a, b))


def _cluster_uniques(
    counts: Counter[str],
    threshold: int,
) -> list[list[str]]:
    """
    Greedy frequency-seeded clustering.

    Walk unique strings from most to least frequent. Assign each value to
    the first existing cluster whose *canonical (most frequent) member*
    scores ``fuzz.ratio >= threshold``; otherwise start a new cluster.
    Within a finished cluster the canonical is always the highest-count
    member (seed), matching plan.md step 3.
    """
    # Most frequent first so popular spellings become anchors.
    ordered = [v for v, _ in counts.most_common()]
    clusters: list[list[str]] = []
    anchors: list[str] = []  # canonical candidate per cluster (first = highest freq)

    for value in ordered:
        placed = False
        for i, anchor in enumerate(anchors):
            if _similarity(value, anchor) >= threshold:
                clusters[i].append(value)
                placed = True
                break
        if not placed:
            clusters.append([value])
            anchors.append(value)

    return clusters


def standardize_values(
    series: pd.Series,
    threshold: int | None = None,
) -> dict[str, str]:
    """
    Build a fuzzy standardization mapping for a text column.

    Purpose
        Cluster near-duplicate string values with RapidFuzz and map each
        original value to the most frequent member of its cluster
        (plan.md Section 4.4).

    Arguments
        series: Column to standardize. Nulls are ignored.
        threshold: Minimum ``fuzz.ratio`` (0–100) to join a cluster.
            Defaults to ``SETTINGS["fuzzy_threshold"]`` (plan default 90).

    Returns
        ``{original_value: canonical_value}`` for every non-null unique
        string in ``series``. Identity mappings are included so callers
        can ``series.map(mapping)`` safely. Empty dict if there is nothing
        to standardize.

    Raises
        TypeError: If ``series`` is not a pandas Series.
        ValueError: If ``threshold`` is outside 0–100.

    Examples
        >>> s = pd.Series(["Lahore", "lahore", "LHR", "Karachi"])
        >>> standardize_values(s, threshold=80)  # doctest: +SKIP
    """
    if series is None or not isinstance(series, pd.Series):
        raise TypeError("series must be a pandas Series")

    thr = _threshold(threshold)
    if thr < 0 or thr > 100:
        raise ValueError(f"threshold must be in [0, 100], got {thr}")

    non_null = series.dropna()
    if non_null.empty:
        return {}

    # Stringify once; keep original string forms as mapping keys.
    as_str = non_null.astype(str)
    counts: Counter[str] = Counter(as_str.tolist())

    max_u = _max_unique()
    if len(counts) > max_u:
        # Keep the most frequent values only — rare tails rarely need
        # standardization and pairwise fuzzy cost grows with unique count.
        counts = Counter(dict(counts.most_common(max_u)))

    if len(counts) <= 1:
        only = next(iter(counts), None)
        return {only: only} if only is not None else {}

    clusters = _cluster_uniques(counts, thr)
    mapping: dict[str, str] = {}
    for cluster in clusters:
        canonical = max(cluster, key=lambda v: (counts[v], -len(v), v))
        for original in cluster:
            mapping[original] = canonical

    return mapping


def apply_standardization(
    series: pd.Series,
    mapping: dict[str, str] | None = None,
    threshold: int | None = None,
) -> pd.Series:
    """
    Apply a fuzzy mapping to a series (nulls preserved).

    Purpose
        Convenience wrapper around ``series.map(mapping)`` that builds the
        mapping via ``standardize_values`` when one is not supplied.

    Arguments
        series: Input column.
        mapping: Optional precomputed ``standardize_values`` result.
        threshold: Forwarded to ``standardize_values`` when ``mapping`` is None.

    Returns
        A new Series with standardized string values; original index kept.
        Nulls remain null.

    Raises
        TypeError: If ``series`` is not a pandas Series.
    """
    if series is None or not isinstance(series, pd.Series):
        raise TypeError("series must be a pandas Series")

    mapping = mapping if mapping is not None else standardize_values(series, threshold=threshold)
    if not mapping:
        return series.copy()

    def _map_one(value: object) -> object:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return value
        if pd.isna(value):
            return value
        key = str(value)
        return mapping.get(key, value)

    return series.map(_map_one)


def standardize_frame(
    df: pd.DataFrame,
    roles: dict[str, str] | None = None,
    threshold: int | None = None,
    columns: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """
    Run ``standardize_values`` on eligible text columns of a DataFrame.

    Purpose
        Frame-level helper for pipeline step (g) in plan.md Section 4.9.

    Arguments
        df: Input frame (not mutated).
        roles: Optional ``classify_columns`` result; computed if omitted.
        threshold: Fuzzy threshold override.
        columns: Optional explicit column subset; otherwise role-filtered.

    Returns
        ``{column_name: mapping}`` for every column that was processed.
        Skipped columns are omitted (not listed with empty maps).

    Raises
        TypeError: If ``df`` is not a DataFrame.
    """
    if df is None or not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    column_roles = roles if roles is not None else classify_columns(df)
    eligible = set(_eligible_roles())
    targets = columns if columns is not None else list(df.columns)

    out: dict[str, dict[str, str]] = {}
    for col in targets:
        if col not in df.columns:
            continue
        role = column_roles.get(col)
        if columns is None and role not in eligible:
            continue
        mapping = standardize_values(df[col], threshold=threshold)
        if mapping:
            out[str(col)] = mapping
    return out


def check_fuzzy_standardization(
    series: pd.Series,
    threshold: int | None = None,
    role: str | None = None,
) -> CheckResult:
    """
    Report fuzzy near-duplicates as a consistency ``CheckResult``.

    Purpose
        Wrap ``standardize_values`` for CLI / scoring: count how many rows
        would change under the mapping and expose cluster examples.

    Arguments
        series: Column to inspect.
        threshold: Fuzzy threshold override.
        role: Optional column role; non-eligible roles are skipped.

    Returns
        ``CheckResult`` with ``dimension="consistency"``.
        ``status="failed"`` when at least one row would be remapped;
        ``status="passed"`` when identity-only or skipped;
        ``status="error"`` on unexpected failures (never raises).

    Raises
        Nothing — errors are captured into ``status="error"``.
    """
    col = getattr(series, "name", None)
    col_name = str(col) if col is not None else None
    try:
        if series is None or not isinstance(series, pd.Series):
            raise TypeError("series must be a pandas Series")

        if role is not None and role not in _eligible_roles():
            return _passed_skip(
                col_name,
                "skipped_non_text_column",
                role=role,
                method="rapidfuzz",
            )

        thr = _threshold(threshold)
        mapping = standardize_values(series, threshold=thr)
        if not mapping:
            return _passed_skip(col_name, "all_null_or_empty", method="rapidfuzz")

        non_identity = {src: dst for src, dst in mapping.items() if src != dst}
        if not non_identity:
            return CheckResult(
                check_name="fuzzy_standardization",
                status="passed",
                column=col_name,
                issues_found=0,
                details={
                    "method": "rapidfuzz",
                    "threshold": thr,
                    "unique_values": len(mapping),
                    "clusters_collapsed": 0,
                    "mapping_sample": {},
                },
                dimension="consistency",
            )

        as_str = series.dropna().astype(str)
        changed_mask = as_str.map(lambda v: mapping.get(v, v) != v)
        issue_idx = as_str.index[changed_mask].tolist()
        issues = len(issue_idx)

        # Cluster summary for the report (canonical -> variants)
        reverse: dict[str, list[str]] = {}
        for src, dst in non_identity.items():
            reverse.setdefault(dst, []).append(src)

        clusters = [
            {
                "canonical": canon,
                "variants": sorted(variants),
                "similarity_examples": [
                    {
                        "value": v,
                        "ratio": round(_similarity(v, canon), 2),
                    }
                    for v in sorted(variants)[:5]
                ],
            }
            for canon, variants in list(reverse.items())[:10]
        ]

        return CheckResult(
            check_name="fuzzy_standardization",
            status="failed" if issues > 0 else "passed",
            column=col_name,
            issues_found=issues,
            details={
                "method": "rapidfuzz",
                "threshold": thr,
                "unique_values": len(mapping),
                "clusters_collapsed": len(reverse),
                "values_remapped": len(non_identity),
                "mapping_sample": dict(list(non_identity.items())[:20]),
                "clusters": clusters,
                "row_indices": issue_idx[:100],
                "row_indices_truncated": issues > 100,
                "role": role,
            },
            dimension="consistency",
        )
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return _error_result(col_name, str(exc))


def check_fuzzy_standardization_frame(
    df: pd.DataFrame,
    roles: dict[str, str] | None = None,
    threshold: int | None = None,
) -> list[CheckResult]:
    """
    Run ``check_fuzzy_standardization`` on every column of ``df``.

    Purpose
        Frame-level check for pipeline integration; skips non-eligible
        roles the same way outlier / consistency checks do.

    Arguments
        df: Input DataFrame.
        roles: Optional precomputed column roles.
        threshold: Fuzzy threshold override.

    Returns
        One ``CheckResult`` per column (including skip/pass/fail/error).

    Raises
        Nothing — frame-level errors become a single ``status="error"`` result.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if df.shape[1] == 0:
            return [
                CheckResult(
                    check_name="fuzzy_standardization",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_columns", "method": "rapidfuzz"},
                    dimension="consistency",
                )
            ]

        column_roles = roles if roles is not None else classify_columns(df)
        return [
            check_fuzzy_standardization(
                df[col],
                threshold=threshold,
                role=column_roles.get(col),
            )
            for col in df.columns
        ]
    except Exception as exc:  # noqa: BLE001
        return [_error_result(None, str(exc))]
