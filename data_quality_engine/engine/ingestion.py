"""Excel/CSV ingestion and header-row detection.

Supports the Easby dataset formats:
  .xlsx/.xlsm via openpyxl (calamine fallback for broken stylesheets)
  .xls via xlrd
  .csv via pandas (encoding sniffed with chardet)

Tool choice (plan Section 2, Task 1): custom heuristic over pandas rows.
No silent guessing -- callers must confirm the returned index via checkpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re

import pandas as pd

from data_quality_engine.config.settings import SETTINGS

_EXCEL_OPENPYXL = {".xlsx", ".xlsm", ".xltx", ".xltm"}
_EXCEL_XLRD = {".xls"}
_CSV = {".csv"}
_SUPPORTED = _EXCEL_OPENPYXL | _EXCEL_XLRD | _CSV

_HEADER_TOKEN_RE = re.compile(
    r"(name|code|date|desc|qty|quantity|address|customer|supplier|product|"
    r"ref|amount|price|cost|site|order|invoice|phone|fax|country|city|post|"
    r"number|line|status|currency|margin|stock|lot|pack|value|terms|vat)",
    re.I,
)
_ID_LIKE_RE = re.compile(r"^[A-Z]{1,6}\d{3,}[A-Z0-9-]*$", re.I)
_NUMERIC_LIKE_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _sniff_csv_encoding(path: Path) -> str:
    sample = path.read_bytes()[:100_000]
    try:
        import chardet

        guess = chardet.detect(sample) or {}
        enc = guess.get("encoding") or "utf-8"
        return enc
    except Exception:
        return "utf-8"


def _read_csv_raw(path: Path) -> dict[str, pd.DataFrame]:
    enc = _sniff_csv_encoding(path)
    # sep=None + engine=python enables sniffer; fall back to comma
    try:
        df = pd.read_csv(
            path,
            header=None,
            dtype=object,
            encoding=enc,
            sep=None,
            engine="python",
            on_bad_lines="skip",
        )
    except Exception:
        df = pd.read_csv(
            path,
            header=None,
            dtype=object,
            encoding=enc,
            on_bad_lines="skip",
        )
    return {path.stem: df}


def _read_excel_raw(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    errors: list[str] = []

    if suffix in _EXCEL_XLRD:
        engines = ["xlrd"]
    else:
        # calamine first for stubborn/corrupt Sage X3 exports, then openpyxl
        engines = ["calamine", "openpyxl"]

    last_exc: Exception | None = None
    for engine in engines:
        try:
            sheets = pd.read_excel(
                path,
                sheet_name=None,
                header=None,
                engine=engine,
                dtype=object,
            )
            return sheets
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            errors.append(f"{engine}: {exc}")

    raise ValueError(
        "Unable to read workbook with available engines. "
        + " | ".join(errors)
    ) from last_exc


def read_excel_file(filepath: str | Path) -> dict[str, pd.DataFrame]:
    """
    Reads all sheets (or a single CSV as one sheet) with NO header assumption
    (header=None so header detection can inspect raw rows).
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED:
        raise ValueError(
            f"Unsupported file type '{path.suffix}'. "
            f"Expected one of: {sorted(_SUPPORTED)}"
        )

    max_mb = SETTINGS["max_file_size_mb"]
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        raise ValueError(
            f"File is {size_mb:.1f} MB which exceeds the configured limit of {max_mb} MB."
        )

    if suffix in _CSV:
        return _read_csv_raw(path)
    return _read_excel_raw(path)


def _is_stringy(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return False


def _non_null_string_density(row: pd.Series) -> float:
    non_null = [
        v
        for v in row.tolist()
        if not (v is None or (isinstance(v, float) and pd.isna(v)))
    ]
    if not non_null:
        return 0.0
    stringy = sum(1 for v in non_null if _is_stringy(v))
    return stringy / len(non_null)


def _type_consistency_below(
    raw_df: pd.DataFrame,
    header_idx: int,
    lookahead: int,
) -> float:
    start = header_idx + 1
    end = min(start + lookahead, len(raw_df))
    if start >= end:
        return 0.0

    block = raw_df.iloc[start:end]
    col_scores: list[float] = []

    for col in block.columns:
        values = [
            v
            for v in block[col].tolist()
            if not (v is None or (isinstance(v, float) and pd.isna(v)))
        ]
        if len(values) < 2:
            col_scores.append(0.5 if values else 0.0)
            continue

        kinds = []
        for v in values:
            if isinstance(v, bool):
                kinds.append("bool")
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                kinds.append("number")
            elif isinstance(v, str):
                kinds.append("string")
            else:
                kinds.append(type(v).__name__)

        dominant = max(kinds.count(k) for k in set(kinds))
        col_scores.append(dominant / len(kinds))

    if not col_scores:
        return 0.0
    return sum(col_scores) / len(col_scores)


def _header_label_score(row: pd.Series) -> float:
    """
    Boost rows that look like column labels; penalize entity-code / numeric data rows.
    Critical for Easby Customer/Supplier lists where data rows are also string-dense.
    """
    vals = []
    for v in row.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        text = str(v).strip()
        if text:
            vals.append(text)
    if not vals:
        return 0.0

    token_hits = sum(1 for v in vals if _HEADER_TOKEN_RE.search(v))
    id_like = sum(1 for v in vals if _ID_LIKE_RE.match(v) or _NUMERIC_LIKE_RE.match(v))
    unique_ratio = len(set(vals)) / len(vals)
    return (token_hits / len(vals)) + (0.25 * unique_ratio) - (id_like / len(vals))


def detect_header_row(
    raw_df: pd.DataFrame,
    max_scan_rows: int | None = None,
) -> int:
    """
    Custom heuristic:
    Score = string_density + type_consistency_below + header_label_score - sparsity_penalty
    Prefer rows that look like labels sitting above typed data.
    Returns 0-based int. Caller must confirm with the user.
    """
    if raw_df is None or raw_df.empty:
        raise ValueError("Cannot detect header row on an empty DataFrame.")

    scan = max_scan_rows if max_scan_rows is not None else SETTINGS["max_header_scan_rows"]
    lookahead = SETTINGS["header_type_consistency_lookahead"]
    scan = min(int(scan), len(raw_df))

    best_idx = 0
    best_score = float("-inf")

    for i in range(scan):
        row = raw_df.iloc[i]
        density = _non_null_string_density(row)
        vals = [
            str(v).strip()
            for v in row.tolist()
            if not (v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "")
        ]
        non_null = len(vals)
        cols = raw_df.shape[1]
        # Sparse / title banners must not beat real header rows
        if non_null < max(3, cols // 3):
            sparsity_penalty = 1.5
        elif non_null < max(3, cols // 2):
            sparsity_penalty = 0.5
        else:
            sparsity_penalty = 0.0
        if non_null and (sum(len(v) for v in vals) / non_null) > 45 and non_null <= 3:
            sparsity_penalty += 1.0

        consistency = _type_consistency_below(raw_df, i, lookahead)
        label_score = _header_label_score(row)
        early_bonus = max(0.0, (scan - i) * 0.001)
        score = density + consistency + (1.25 * label_score) - sparsity_penalty + early_bonus
        if score > best_score:
            best_score = score
            best_idx = i

    return int(best_idx)


def merge_multirow_header(
    raw_df: pd.DataFrame,
    header_row: int,
    parent_rows: int = 1,
) -> list[str]:
    """
    Merge a parent label row (e.g. Booked Orders row 0) with the detected
    header row (row 1) into unique column names.
    """
    headers = raw_df.iloc[header_row].tolist()
    parents = None
    if parent_rows > 0 and header_row - parent_rows >= 0:
        parents = raw_df.iloc[header_row - parent_rows].tolist()

    merged: list[str] = []
    for i, h in enumerate(headers):
        child = ""
        if not (h is None or (isinstance(h, float) and pd.isna(h)) or str(h).strip() == ""):
            child = str(h).strip()
        parent = ""
        if parents is not None:
            p = parents[i]
            if not (p is None or (isinstance(p, float) and pd.isna(p)) or str(p).strip() == ""):
                parent = str(p).strip()
        if parent and child and parent.lower() != child.lower():
            name = f"{parent} {child}"
        else:
            name = child or parent or f"unnamed_{i}"
        merged.append(name)
    return merged


def _unique_names(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        base = name.strip() if name and str(name).strip() else f"unnamed_{i}"
        base = str(base)
        if base in seen:
            seen[base] += 1
            cleaned.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 0
            cleaned.append(base)
    return cleaned


def header_preview(
    raw_df: pd.DataFrame,
    header_row: int,
    context_rows: int = 5,
) -> dict[str, Any]:
    n = len(raw_df)
    if header_row < 0 or header_row >= n:
        raise IndexError(f"header_row {header_row} out of range for DataFrame of length {n}")

    above_start = max(0, header_row - context_rows)
    below_end = min(n, header_row + 1 + context_rows)

    def _rows(start: int, end: int) -> list[dict[str, Any]]:
        out = []
        for idx in range(start, end):
            out.append({"row_index": idx, "values": raw_df.iloc[idx].tolist()})
        return out

    return {
        "detected_header_row": header_row,
        "rows_above": _rows(above_start, header_row),
        "header": {"row_index": header_row, "values": raw_df.iloc[header_row].tolist()},
        "rows_below": _rows(header_row + 1, below_end),
    }


def load_with_confirmed_header(
    raw_df: pd.DataFrame,
    header_row: int,
    *,
    merge_parent_header: bool | None = None,
) -> pd.DataFrame:
    """
    Reindexes the raw dataframe using the confirmed header row.
    For Easby Booked Orders-style exports, optionally merges the parent
    label row above the detected header.
    """
    if raw_df is None or raw_df.empty:
        raise ValueError("Cannot load header from an empty DataFrame.")
    if header_row < 0 or header_row >= len(raw_df):
        raise IndexError(
            f"header_row {header_row} out of range for DataFrame of length {len(raw_df)}"
        )

    do_merge = (
        SETTINGS.get("merge_parent_header", True)
        if merge_parent_header is None
        else merge_parent_header
    )

    if do_merge and header_row > 0:
        # Only merge when the parent row is also string-dense (group labels)
        parent_density = _non_null_string_density(raw_df.iloc[header_row - 1])
        if parent_density >= 0.4:
            names = merge_multirow_header(raw_df, header_row, parent_rows=1)
        else:
            names = [
                (
                    f"unnamed_{i}"
                    if h is None or (isinstance(h, float) and pd.isna(h)) or str(h).strip() == ""
                    else str(h).strip()
                )
                for i, h in enumerate(raw_df.iloc[header_row].tolist())
            ]
    else:
        names = [
            (
                f"unnamed_{i}"
                if h is None or (isinstance(h, float) and pd.isna(h)) or str(h).strip() == ""
                else str(h).strip()
            )
            for i, h in enumerate(raw_df.iloc[header_row].tolist())
        ]

    cleaned = _unique_names(names)
    body = raw_df.iloc[header_row + 1 :].copy()
    body.columns = cleaned
    body = body.reset_index(drop=True)
    body = body.dropna(how="all").reset_index(drop=True)
    return body
