"""Excel/CSV ingestion and header-row detection.

Supports the Easby teacher dataset formats:
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


def _calamine_installed() -> bool:
    try:
        import python_calamine  # noqa: F401

        return True
    except ImportError:
        return False


def _read_excel_via_calamine_direct(path: Path) -> dict[str, pd.DataFrame]:
    """
    Direct python-calamine read (bypasses pandas engine registry).
    Needed for Sage X3 exports whose stylesheets crash openpyxl
    (e.g. ValueError: Duplicate position 0.0 on gradient fills).
    """
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(str(path))
    sheets: dict[str, pd.DataFrame] = {}
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python(skip_empty_area=False)
        if not rows:
            sheets[name] = pd.DataFrame()
            continue
        width = max(len(r) for r in rows)
        normalized = [list(r) + [None] * (width - len(r)) for r in rows]
        sheets[name] = pd.DataFrame(normalized, dtype=object)
    return sheets


def _read_excel_raw(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    errors: list[str] = []
    last_exc: Exception | None = None

    if suffix in _EXCEL_XLRD:
        engines = ["xlrd"]
    else:
        # calamine first: openpyxl dies on some Sage X3 stylesheets
        # (malformed gradient fill stops). openpyxl remains the fallback.
        engines = ["calamine", "openpyxl"]

    for engine in engines:
        if engine == "calamine" and not _calamine_installed():
            errors.append(
                "calamine: package 'python-calamine' is not installed "
                "(pip install python-calamine). Required for Sage X3 / "
                "broken-stylesheet .xlsx files."
            )
            continue
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

    # Last resort: talk to python-calamine directly (pandas engine quirk / version)
    if suffix in _EXCEL_OPENPYXL and _calamine_installed():
        try:
            return _read_excel_via_calamine_direct(path)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            errors.append(f"python_calamine.direct: {exc}")

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


def _row_non_null_values(row: pd.Series) -> list[str]:
    vals: list[str] = []
    for v in row.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        text = str(v).strip()
        if text:
            vals.append(text)
    return vals


def _is_credible_header_row(row: pd.Series, score: float, n_cols: int) -> bool:
    """
    Reject blank / pure-numeric junk rows that can win on type_consistency alone
    (seen on leftover pivot helper sheets with interleaved blank rows).
    """
    vals = _row_non_null_values(row)
    if not vals:
        return False
    # A real header needs some string labels, not only IDs/numbers
    stringy = sum(1 for v in vals if not _NUMERIC_LIKE_RE.match(v) and not _ID_LIKE_RE.match(v))
    if stringy == 0:
        return False
    # Absolute floor: empty/near-empty sheets often score slightly negative
    if score < 0.15:
        return False
    # Extremely sparse relative to wide sheets is not a header
    if len(vals) < max(1, min(2, n_cols // 10)) and n_cols > 5:
        return False
    return True


def detect_header_row(
    raw_df: pd.DataFrame,
    max_scan_rows: int | None = None,
) -> int:
    """
    Custom heuristic:
    Score = string_density + type_consistency_below + header_label_score - sparsity_penalty
    Prefer rows that look like labels sitting above typed data.

    Returns 0-based int, or -1 when no credible header exists (headerless /
    leftover numeric scrap sheets). Caller must confirm with the user.
    """
    if raw_df is None or raw_df.empty:
        raise ValueError("Cannot detect header row on an empty DataFrame.")

    scan = max_scan_rows if max_scan_rows is not None else SETTINGS["max_header_scan_rows"]
    lookahead = SETTINGS["header_type_consistency_lookahead"]
    scan = min(int(scan), len(raw_df))

    best_idx = 0
    best_score = float("-inf")
    scored: list[tuple[int, float]] = []

    for i in range(scan):
        row = raw_df.iloc[i]
        vals = _row_non_null_values(row)
        non_null = len(vals)
        cols = raw_df.shape[1]

        # Blank rows must never win (they previously beat numeric scrap via consistency)
        if non_null == 0:
            scored.append((i, float("-inf")))
            continue

        density = _non_null_string_density(row)
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
        scored.append((i, score))
        if score > best_score:
            best_score = score
            best_idx = i

    if not _is_credible_header_row(raw_df.iloc[best_idx], best_score, raw_df.shape[1]):
        # Try next-best credible candidate before declaring headerless
        ranked = sorted(scored, key=lambda t: t[1], reverse=True)
        for idx, score in ranked:
            if _is_credible_header_row(raw_df.iloc[idx], score, raw_df.shape[1]):
                return int(idx)
        return -1

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

    def _rows(start: int, end: int) -> list[dict[str, Any]]:
        out = []
        for idx in range(start, end):
            out.append({"row_index": idx, "values": raw_df.iloc[idx].tolist()})
        return out

    # header_row == -1 means "no credible header" (headerless sheet)
    if header_row < 0:
        return {
            "detected_header_row": -1,
            "headerless": True,
            "note": (
                "No credible header row found — sheet looks like numeric scrap "
                "or has no label row. Will load with synthetic column names."
            ),
            "rows_above": [],
            "header": {"row_index": -1, "values": [f"unnamed_{i}" for i in range(raw_df.shape[1])]},
            "rows_below": _rows(0, min(n, context_rows)),
        }

    if header_row >= n:
        raise IndexError(f"header_row {header_row} out of range for DataFrame of length {n}")

    above_start = max(0, header_row - context_rows)
    below_end = min(n, header_row + 1 + context_rows)

    return {
        "detected_header_row": header_row,
        "headerless": False,
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

    header_row == -1 loads the sheet as headerless (synthetic unnamed_* columns,
    keeps all non-blank data rows).
    """
    if raw_df is None or raw_df.empty:
        raise ValueError("Cannot load header from an empty DataFrame.")

    # Headerless: leftover scrap sheets with no label row
    if header_row < 0:
        names = _unique_names([f"unnamed_{i}" for i in range(raw_df.shape[1])])
        body = raw_df.copy()
        body.columns = names
        body = body.reset_index(drop=True)
        body = body.dropna(how="all").reset_index(drop=True)
        return body

    if header_row >= len(raw_df):
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
