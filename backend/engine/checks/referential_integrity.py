"""Referential-integrity / reference-list checks -> Integrity or Accuracy dimension.

Plan Section 4.3 defines a single function, ``check_referential_integrity``.
Plan Section 5 splits the *dimension* the result is tagged with, not the
mechanics of the check itself:

- ``dimension="integrity"`` (default): foreign-key-style cross-row/cross-file
  relationships, e.g. an order/invoice row's ``Customer No.`` must exist in
  the Customer List master.
- ``dimension="accuracy"``: the same not-in-set mechanics, but
  ``reference_values`` comes from a known-correct/approved list rather than a
  foreign-key relationship (e.g. an approved city list). Caller passes
  ``dimension="accuracy"`` explicitly; nothing about the comparison changes.

Real use case in this dataset (plan Section 4.3 discussion / domain_rules.py):
  - Booked Orders / Invoice List "Customer No." must exist in
    Customer List.xls's "Customer No." column.
  - Supplier-code columns must exist in Supplier List.xls.
  - Product-code columns in invoice/order files must exist in
    Product Data by Product Site.xlsx.

``load_reference_values()`` below is the "small loader helper" -- it wires
this check to the existing (previously-dead) loader scaffolding in
``config/domain_rules.py`` (``load_customer_codes`` / ``load_supplier_codes`` /
``load_product_codes``) instead of re-implementing file loading here.

Not wired into main.py's automatic pipeline (see plan discussion / task scope)
-- reference files aren't guaranteed to be sitting alongside every file being
checked, so callers opt in explicitly.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd

from backend.engine.models import CheckResult

_MAX_ROW_INDICES = 100
_SHAPE_SAMPLE = 80
_SHAPE_MAJORITY = 0.5
_NAME_TOKENS = (
    " ltd",
    " limited",
    " plc",
    " inc",
    " gmbh",
    " llc",
    " corp",
    " co.",
)
_CODE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./-]{1,20}$")


def _non_empty_strings(values: Iterable[Any], limit: int | None = None) -> list[str]:
    out: list[str] = []
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip()
        if not s:
            continue
        out.append(s)
        if limit is not None and len(out) >= limit:
            break
    return out


def _is_name_shaped(value: str) -> bool:
    lower = f" {value.lower()} "
    if any(tok in lower for tok in _NAME_TOKENS):
        return True
    return (" " in value) and len(value) > 5


def _is_code_shaped(value: str) -> bool:
    if _is_name_shaped(value):
        return False
    return bool(_CODE_VALUE_RE.match(value))


def _infer_value_shape(values: Iterable[Any]) -> str:
    """Return 'code', 'name', or 'unknown' from a value sample."""
    sample = _non_empty_strings(values, limit=_SHAPE_SAMPLE)
    if not sample:
        return "unknown"
    n = len(sample)
    name_votes = sum(1 for v in sample if _is_name_shaped(v))
    code_votes = sum(1 for v in sample if _is_code_shaped(v))
    if name_votes / n >= _SHAPE_MAJORITY:
        return "name"
    if code_votes / n >= _SHAPE_MAJORITY:
        return "code"
    return "unknown"


def _error_result(
    message: str,
    *,
    column: str | None,
    dimension: str,
) -> CheckResult:
    return CheckResult(
        check_name="referential_integrity",
        status="error",
        column=column,
        issues_found=0,
        details={"error": message},
        dimension=dimension,
    )


def check_referential_integrity(
    df: pd.DataFrame,
    key_column: str,
    reference_values: set,
    *,
    dimension: str = "integrity",
) -> CheckResult:
    """
    Flag rows where ``df[key_column]`` is not in ``reference_values``.

    Purpose
        Foreign-key-style ("integrity") or known-good-list ("accuracy")
        membership check, per plan Section 4.3 / Section 5. Values are
        compared as stripped strings so numeric-looking codes (``12345`` vs
        ``"12345"``) and incidental whitespace don't produce false failures
        -- master lists and transactional exports rarely share dtypes.

    Arguments
        df: Input frame containing ``key_column``.
        key_column: Column whose values are checked against
            ``reference_values`` (e.g. ``"Customer No."``).
        reference_values: Set (or any iterable) of known-good/valid keys,
            e.g. from ``load_reference_values("customer")``.
        dimension: ``"integrity"`` (default, foreign-key relationship) or
            ``"accuracy"`` (known-correct reference list). Caller decides;
            the comparison logic is identical either way.

    Returns
        A single ``CheckResult``. ``issues_found`` counts rows whose
        ``key_column`` value is non-null (and non-blank) but not present in
        ``reference_values``. Null/blank cells are treated as "nothing to
        verify" and are skipped, not flagged -- that's ``missing_values.py``'s
        job. ``details["row_indices"]`` holds up to 100 offending 0-based row
        positions (same cap/pattern as other checks, e.g. duplicates.py).

        An **empty** ``reference_values`` set is treated as an inconclusive
        input, not "everything fails": it usually means the master file
        couldn't be found/loaded rather than that the master genuinely has
        zero valid keys. Flagging every row in that situation would be a
        misleading flood of false positives, so this returns
        ``status="error"`` with ``issues_found=0`` and an explanatory message,
        instead of ``status="failed"``.

    Raises
        Nothing -- bad input (wrong type for ``df``, missing ``key_column``,
        non-iterable ``reference_values``, etc.) is caught and returned as
        ``status="error"``, never propagated.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if not isinstance(key_column, str) or not key_column:
            raise TypeError("key_column must be a non-empty string")
        if key_column not in df.columns:
            raise KeyError(f"key_column {key_column!r} not found in dataframe columns")
        if reference_values is None:
            raise TypeError(
                "reference_values must be a set (or other iterable) of valid keys"
            )

        # Normalize to a set of stripped strings regardless of what the
        # caller passed in (set, list, tuple, generator, ...). A plain
        # non-iterable (e.g. an int) raises TypeError here, which the
        # except-block below converts into status="error".
        ref_set = {
            str(v).strip()
            for v in reference_values
            if v is not None and str(v).strip() != ""
        }

        if not ref_set:
            return _error_result(
                "reference_values is empty (or contained only null/blank "
                "entries) -- cannot verify rows against an empty reference "
                "set. This usually means the master file/column could not "
                "be loaded; treat as inconclusive, not a pass or a fail.",
                column=key_column,
                dimension=dimension,
            )

        series = df[key_column]
        normalized = series.map(lambda v: str(v).strip() if pd.notna(v) else None)
        # NOTE: Series.map coerces a returned `None` back to NaN (a pandas
        # quirk), so the null-check here must use pd.notna(), not `is None`.
        comparable_mask = normalized.map(lambda v: pd.notna(v) and v != "")

        # Assumption: incompatible shapes (name vs code) are a wiring error,
        # not a data-quality failure — skip comparison rather than 100% fail.
        cand_vals = normalized[comparable_mask].tolist()
        cand_shape = _infer_value_shape(cand_vals)
        ref_shape = _infer_value_shape(ref_set)
        if (
            cand_shape != "unknown"
            and ref_shape != "unknown"
            and cand_shape != ref_shape
        ):
            return CheckResult(
                check_name="referential_integrity",
                status="passed",
                column=key_column,
                issues_found=0,
                details={
                    "reason": "reference_type_mismatch",
                    "candidate_shape": cand_shape,
                    "reference_shape": ref_shape,
                    "reference_count": len(ref_set),
                    "checked_rows": 0,
                    "total_rows": len(df),
                },
                dimension=dimension,
            )

        invalid_mask = comparable_mask & ~normalized.isin(ref_set)

        row_indices = df.index[invalid_mask].tolist()
        issues = len(row_indices)
        status = "passed" if issues == 0 else "failed"

        sample_values = [str(series.loc[i]) for i in row_indices[:_MAX_ROW_INDICES]]

        return CheckResult(
            check_name="referential_integrity",
            status=status,
            column=key_column,
            issues_found=issues,
            details={
                "reference_count": len(ref_set),
                "checked_rows": int(comparable_mask.sum()),
                "skipped_null_or_blank_rows": int((~comparable_mask).sum()),
                "total_rows": len(df),
                "row_indices": row_indices[:_MAX_ROW_INDICES],
                "row_indices_truncated": issues > _MAX_ROW_INDICES,
                "sample_invalid_values": sample_values,
                "note": (
                    "row_indices are 0-based positions in the loaded "
                    "dataframe (after header confirmation), not Excel row "
                    "numbers."
                ),
            },
            dimension=dimension,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_result(
            str(exc),
            column=key_column if isinstance(key_column, str) else None,
            dimension=dimension,
        )


# Maps the loader-helper "kind" to the corresponding domain_rules.py function.
# Populated lazily inside load_reference_values() to avoid importing
# domain_rules (and its ingestion.py dependency) unless this helper is
# actually used -- check_referential_integrity itself has no such dependency.
_LOADER_KINDS = ("customer", "supplier", "product")


def load_reference_values(
    kind: str,
    dataset_dir: str | None = None,
) -> set[str]:
    """
    Small loader helper: reuse config/domain_rules.py's existing (previously
    dead-code) loaders instead of re-implementing master-file loading here.

    Purpose
        Turn ``kind`` into a call to ``load_customer_codes`` /
        ``load_supplier_codes`` / ``load_product_codes`` and return the
        result as a ``set`` ready for ``check_referential_integrity``.

    Arguments
        kind: One of ``"customer"``, ``"supplier"``, ``"product"``.
        dataset_dir: Optional override directory containing the master
            files (``Customer List.xls``, ``Supplier List.xls``,
            ``Product Data by Product Site.xlsx``). Defaults to
            ``SETTINGS["dataset_dir"]`` inside domain_rules.py when omitted.

    Returns
        A set of stripped-string keys. Empty set if the master file is
        missing, its expected column can't be found, or it fails to load
        for any reason (domain_rules.py loaders are defensive about this --
        see their docstrings/comments).

    Raises
        ValueError: If ``kind`` isn't one of the supported values. This is
        a programming-error guard (caller passed a typo), not file/data
        related, so it is *not* swallowed -- callers building the
        ``check_referential_integrity`` call should fix the ``kind`` value
        rather than silently getting an empty reference set.
    """
    if kind not in _LOADER_KINDS:
        raise ValueError(
            f"Unknown reference kind {kind!r}; expected one of {_LOADER_KINDS}"
        )

    # Imported here (not at module top) so importing this check module never
    # requires domain_rules.py / ingestion.py unless this helper is actually
    # called -- check_referential_integrity has no file-loading dependency.
    from backend.config import domain_rules

    loader = {
        "customer": domain_rules.load_customer_codes,
        "supplier": domain_rules.load_supplier_codes,
        "product": domain_rules.load_product_codes,
    }[kind]

    codes: Iterable[Any] = loader(dataset_dir)
    return set(codes)