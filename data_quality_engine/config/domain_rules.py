"""Easby / teacher-dataset domain helpers: reference lists + cross-column rules."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.ingestion import (
    detect_header_row,
    load_with_confirmed_header,
    read_excel_file,
)

# Company-name tokens that almost never appear in ERP *code* columns.
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
# Short alphanumeric tokens typical of customer/supplier/product codes
# (e.g. C00001, DEAD, S123). Whitespace or long free text => not a code.
_CODE_RE = re.compile(r"^[A-Za-z0-9_./-]{1,20}$")
_SHAPE_SAMPLE = 80
_SHAPE_MAJORITY = 0.5
_MISMATCH_REASON = (
    "reference type mismatch, not compared "
    "(column values are not shaped like the reference set)"
)


def _load_master(path: Path, preferred_sheet: str | None = None) -> pd.DataFrame:
    sheets = read_excel_file(path)
    raw = None
    if preferred_sheet and preferred_sheet in sheets:
        raw = sheets[preferred_sheet]
    else:
        for name, frame in sheets.items():
            if "sage" in name.lower() or name.lower().endswith(".ds."):
                raw = frame
                break
        if raw is None:
            raw = next(iter(sheets.values()))
    header = detect_header_row(raw)
    return load_with_confirmed_header(raw, header)


def _extract_codes(df: pd.DataFrame, col: Any) -> tuple[str, ...]:
    return tuple(sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()}))


def _find_column(df: pd.DataFrame, *candidates: str) -> Any | None:
    """Exact (casefolded, stripped) match first; substring fallback so
    header-naming drift in the master file (e.g. "Product Code" instead of
    "Product") doesn't silently return zero reference values."""
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate in cols_lower:
            return cols_lower[candidate]
    for candidate in candidates:
        for lowered, original in cols_lower.items():
            if candidate in lowered:
                return original
    return None


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


def _is_name_shaped_value(value: str) -> bool:
    lower = f" {value.lower()} "
    if any(tok in lower for tok in _NAME_TOKENS):
        return True
    # Multi-word free text (company / person names) is name-shaped.
    return (" " in value) and len(value) > 5


def _is_code_shaped_value(value: str) -> bool:
    if _is_name_shaped_value(value):
        return False
    return bool(_CODE_RE.match(value))


def infer_value_shape(values: Iterable[Any]) -> str:
    """
    Classify a sample of values as ``\"code\"``, ``\"name\"``, or ``\"unknown\"``.

    Used to stop auto-matching a name column (e.g. Main Customer = \"Guru
    Systems Ltd\") against a code reference list (Customer No. = C00001).
    """
    sample = _non_empty_strings(values, limit=_SHAPE_SAMPLE)
    if not sample:
        return "unknown"
    name_votes = sum(1 for v in sample if _is_name_shaped_value(v))
    code_votes = sum(1 for v in sample if _is_code_shaped_value(v))
    n = len(sample)
    if name_votes / n >= _SHAPE_MAJORITY:
        return "name"
    if code_votes / n >= _SHAPE_MAJORITY:
        return "code"
    return "unknown"


def _pick_compatible_reference(
    series: pd.Series,
    *,
    code_ref: tuple[str, ...] | list[str],
    name_ref: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[str] | None, str | None]:
    """
    Choose a reference list whose value *shape* matches ``series``.

    Returns (ref_values, None) on success, or (None, reason) to skip.
    Preference: code-shaped candidates -> code_ref; name-shaped -> name_ref
    when available (Customer List \"Company Name\"), else skip with a clear
    mismatch reason instead of comparing incompatible representations.
    """
    cand_shape = infer_value_shape(series.tolist())
    code_list = list(code_ref) if code_ref else []
    name_list = list(name_ref) if name_ref else []
    code_shape = infer_value_shape(code_list) if code_list else "unknown"
    name_shape = infer_value_shape(name_list) if name_list else "unknown"

    if cand_shape == "code" and code_list and code_shape in {"code", "unknown"}:
        return code_list, None
    if cand_shape == "name" and name_list and name_shape in {"name", "unknown"}:
        return name_list, None
    if cand_shape == "name" and not name_list:
        return None, _MISMATCH_REASON
    if cand_shape == "code" and not code_list:
        return None, _MISMATCH_REASON
    # Ambiguous column: only attach a ref when a sample value actually
    # appears in the reference set (avoids 100% false fails on free text).
    if cand_shape == "unknown":
        sample = set(_non_empty_strings(series.tolist(), limit=_SHAPE_SAMPLE))
        if code_list and sample & set(code_list):
            return code_list, None
        if name_list and sample & set(name_list):
            return name_list, None
        return None, _MISMATCH_REASON
    return None, _MISMATCH_REASON


# NOTE: loaders are defensive -- renamed columns / missing masters degrade to
# an empty reference set rather than crashing. Auto-matching also checks
# value *shape* (see reference_lists_for_frame) so a name column is never
# silently compared to a code list.
@lru_cache(maxsize=4)
def load_customer_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Customer List.xls"
    if not path.exists():
        return tuple()
    try:
        df = _load_master(path)
        col = _find_column(df, "customer no.", "customer no", "customer code")
        if col is None:
            return tuple()
        return _extract_codes(df, col)
    except Exception:
        return tuple()


@lru_cache(maxsize=4)
def load_customer_names(dataset_dir: str | None = None) -> tuple[str, ...]:
    """Company names from Customer List.xls (for name-shaped FK columns)."""
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Customer List.xls"
    if not path.exists():
        return tuple()
    try:
        df = _load_master(path)
        col = _find_column(df, "company name", "customer name")
        if col is None:
            return tuple()
        return _extract_codes(df, col)
    except Exception:
        return tuple()


@lru_cache(maxsize=4)
def load_supplier_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Supplier List.xls"
    if not path.exists():
        return tuple()
    try:
        df = _load_master(path)
        col = _find_column(df, "supplier code", "supplier no.", "supplier no")
        if col is None:
            return tuple()
        return _extract_codes(df, col)
    except Exception:
        return tuple()


@lru_cache(maxsize=4)
def load_product_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    """Heavy: Product Data is ~75k rows. Cached after first load."""
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Product Data by Product Site.xlsx"
    if not path.exists():
        return tuple()
    try:
        df = _load_master(path, preferred_sheet="Sheet1")
        col = _find_column(df, "product", "product code", "sage x3 code")
        if col is None:
            return tuple()
        return _extract_codes(df, col)
    except Exception:
        return tuple()


def default_cross_column_rules() -> list[dict[str, Any]]:
    """Date-order rules common across Easby order books."""
    return [
        {
            "name": "expected_del_after_order",
            "left": "Expected Del Date",
            "op": ">=",
            "right": "Order Date",
        },
        {
            "name": "ship_after_order",
            "left": "Ship Date",
            "op": ">=",
            "right": "Order Date",
        },
        {
            "name": "expected_del_after_so_created",
            "left": "Expected  Del. Date",
            "op": ">=",
            "right": "SO Date Created",
        },
    ]


def reference_lists_for_frame(
    df: pd.DataFrame,
    dataset_dir: Path | None = None,
    *,
    include_products: bool = False,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Attach reference lists only for columns present in df whose *values*
    are shaped like the chosen reference set.

    Returns
        (matched, skipped) where ``matched`` maps column -> reference values
        and ``skipped`` maps column -> human-readable reason (e.g. name
        column vs code list with no name reference available).

    Product master is opt-in (large file) via include_products=True.
    """
    root = str(dataset_dir) if dataset_dir else None
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    refs: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}

    customer_codes = load_customer_codes(root)
    customer_names = load_customer_names(root)

    def _try_customer(col_key: str) -> None:
        col = str(cols_lower[col_key])
        if col in refs or col in skipped:
            return
        chosen, reason = _pick_compatible_reference(
            df[col],
            code_ref=customer_codes,
            name_ref=customer_names,
        )
        if chosen is not None:
            refs[col] = chosen
        elif reason:
            skipped[col] = reason

    for key in ("customer code", "customer no.", "main customer", "inv customer code"):
        if key in cols_lower:
            _try_customer(key)

    # Classic Order Book uses plain 'Code' next to customer Name
    if "code" in cols_lower and "name" in cols_lower:
        code_col = str(cols_lower["code"])
        if code_col not in refs and code_col not in skipped:
            chosen, reason = _pick_compatible_reference(
                df[code_col],
                code_ref=customer_codes,
                name_ref=customer_names,
            )
            if chosen is not None:
                refs[code_col] = chosen
            elif reason:
                skipped[code_col] = reason

    supplier_codes = load_supplier_codes(root)
    for key in ("supplier code", "last supplier", "acct nr."):
        if key in cols_lower:
            col = str(cols_lower[key])
            if col in refs or col in skipped:
                continue
            chosen, reason = _pick_compatible_reference(
                df[col],
                code_ref=supplier_codes,
                name_ref=None,
            )
            if chosen is not None:
                refs[col] = chosen
            elif reason:
                skipped[col] = reason

    if include_products:
        product_codes = load_product_codes(root)
        for key in ("sage x3 code", "product", "item", "delta nr."):
            if key in cols_lower:
                col = str(cols_lower[key])
                if col in refs or col in skipped:
                    continue
                chosen, reason = _pick_compatible_reference(
                    df[col],
                    code_ref=product_codes,
                    name_ref=None,
                )
                if chosen is not None:
                    refs[col] = chosen
                elif reason:
                    skipped[col] = reason

    return refs, skipped


def suspicious_zero_columns_present(df: pd.DataFrame) -> list[str]:
    wanted = [str(c).lower() for c in SETTINGS.get("suspicious_zero_columns", [])]
    found = []
    for col in df.columns:
        name = str(col).strip().lower()
        if name in wanted or any(
            w in name for w in ("amt-tax", "margin%", "standard cost", "net sell price")
        ):
            found.append(str(col))
    return found


def looks_like_contact_column(name: str) -> bool:
    lowered = str(name).lower()
    return any(
        tok in lowered
        for tok in (
            "phone",
            "landline",
            "fax",
            "mobile",
            "email",
            "toll",
        )
    )
