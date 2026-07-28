"""Easby / teacher-dataset domain helpers: reference lists + cross-column rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.ingestion import (
    detect_header_row,
    load_with_confirmed_header,
    read_excel_file,
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


@lru_cache(maxsize=4)
def load_customer_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Customer List.xls"
    if not path.exists():
        return tuple()
    df = _load_master(path)
    col = next((c for c in df.columns if "customer no" in str(c).lower()), None)
    if col is None:
        return tuple()
    return tuple(sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()}))


@lru_cache(maxsize=4)
def load_supplier_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Supplier List.xls"
    if not path.exists():
        return tuple()
    df = _load_master(path)
    col = next((c for c in df.columns if "supplier code" in str(c).lower()), None)
    if col is None:
        return tuple()
    return tuple(sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()}))


@lru_cache(maxsize=4)
def load_product_codes(dataset_dir: str | None = None) -> tuple[str, ...]:
    """Heavy: Product Data is ~75k rows. Cached after first load."""
    root = Path(dataset_dir) if dataset_dir else Path(SETTINGS["dataset_dir"])
    path = root / "Product Data by Product Site.xlsx"
    if not path.exists():
        return tuple()
    df = _load_master(path, preferred_sheet="Sheet1")
    col = next((c for c in df.columns if str(c).strip().lower() == "product"), None)
    if col is None:
        return tuple()
    return tuple(sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()}))


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
) -> dict[str, list[str]]:
    """
    Attach reference lists only for columns present in df.
    Product master is opt-in (large file) via include_products=True.
    """
    root = str(dataset_dir) if dataset_dir else None
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    refs: dict[str, list[str]] = {}

    for key in ("customer code", "customer no.", "main customer", "inv customer code"):
        if key in cols_lower:
            refs[str(cols_lower[key])] = list(load_customer_codes(root))

    # Classic Order Book uses plain 'Code' next to customer Name
    if "code" in cols_lower and "name" in cols_lower:
        # Only treat as customer code when a Name column exists (not product Code)
        code_col = cols_lower["code"]
        # Prefer not overriding if already mapped
        if str(code_col) not in refs:
            refs[str(code_col)] = list(load_customer_codes(root))

    for key in ("supplier code", "last supplier", "acct nr."):
        if key in cols_lower:
            refs[str(cols_lower[key])] = list(load_supplier_codes(root))

    if include_products:
        for key in ("sage x3 code", "product", "item", "delta nr."):
            if key in cols_lower:
                refs[str(cols_lower[key])] = list(load_product_codes(root))

    return refs


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
