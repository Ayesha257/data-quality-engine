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

from backend.config.settings import SETTINGS

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

# ZIP local / empty / spanned headers (OOXML .xlsx etc.) and OLE compound (.xls).
_OOXML_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _peek_workbook_kind(path: Path) -> str | None:
    """
    Cheap content sniff: read at most 8 bytes and classify binary Excel.

    Returns ``\"ooxml\"``, ``\"ole\"``, or ``None`` if the file does not
    start with a known workbook signature (so extension-based routing
    should proceed as usual).
    """
    with path.open("rb") as fh:
        head = fh.read(8)
    if any(head.startswith(m) for m in _OOXML_MAGICS):
        return "ooxml"
    if head.startswith(_OLE_MAGIC):
        return "ole"
    return None


def _note_extension_mismatch(path: Path, kind: str) -> None:
    """Surface mislabeled extensions in the CLI report (and the logger)."""
    import logging

    if kind == "ooxml":
        label = "an Excel workbook (OOXML)"
    else:
        label = "a legacy Excel workbook (OLE/.xls)"
    msg = (
        f"Note: {path.name} has a {path.suffix.lower()} extension but its "
        f"contents are {label}. Reading it as Excel instead."
    )
    print(msg)
    # info (not warning): default lastResort only emits WARNING+ to stderr,
    # which would duplicate the print in the CLI report.
    logging.getLogger(__name__).info(msg)


def _encoding_decode_ok(sample: bytes, encoding: str | None) -> bool:
    """True only when ``encoding`` is a real codec that strict-decodes sample."""
    if not encoding or not isinstance(encoding, str):
        return False
    try:
        sample.decode(encoding, errors="strict")
        return True
    except (LookupError, UnicodeDecodeError, TypeError, ValueError):
        return False


def _sniff_csv_encoding(path: Path) -> str:
    """
    Detect CSV encoding via checks/encoding.check_encoding (chardet + optional
    fallback recommendation). Samples the first 100_000 bytes — same limit as
    before this module existed; chardet logic now lives in one place.

    Never trusts chardet blindly: the chosen encoding must strict-decode the
    sample. ``encoding=None`` (even at confidence 1.0) and any encoding that
    fails the probe fall through to utf-8-sig → cp1252 → latin-1.
    """
    sample = path.read_bytes()[:100_000]
    try:
        from backend.engine.checks.encoding import (
            check_encoding,
            _try_fallback_decodes,
        )

        result = check_encoding(sample)
        details = result.details or {}
        enc = details.get("encoding")

        if _encoding_decode_ok(sample, enc):
            # Preserve prior low-confidence preference when the chardet
            # encoding itself still decodes cleanly.
            if details.get("low_confidence") and details.get("recommended_encoding"):
                return str(details["recommended_encoding"])
            return str(enc)

        # Probe failed (or encoding was None): use recommended / run fallbacks.
        recommended = details.get("recommended_encoding")
        if not recommended:
            recommended = _try_fallback_decodes(sample).get("recommended_encoding")
        return str(recommended) if recommended else "utf-8"
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


def _read_excel_raw(
    path: Path,
    *,
    force_kind: str | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Read a workbook. ``force_kind`` is ``\"ole\"`` / ``\"ooxml\"`` when the
    caller detected a binary Excel payload behind a non-Excel extension.
    """
    suffix = path.suffix.lower()
    errors: list[str] = []
    last_exc: Exception | None = None

    use_ole = force_kind == "ole" or (
        force_kind is None and suffix in _EXCEL_XLRD
    )
    if use_ole:
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
    allow_calamine_direct = force_kind == "ooxml" or suffix in _EXCEL_OPENPYXL
    if allow_calamine_direct and _calamine_installed():
        try:
            return _read_excel_via_calamine_direct(path)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            errors.append(f"python_calamine.direct: {exc}")

    raise ValueError(
        "Unable to read workbook with available engines. "
        + " | ".join(errors)
    ) from last_exc


def get_sheet_visibility(filepath: str | Path) -> dict[str, bool]:
    """
    Best-effort sheet_name -> is_visible map for an Excel workbook.

    FIX (ISS-01 / ISS-02, Data Quality Engine Functional Validation & Report
    Audit): the report pipeline used to default to ``list(sheets.keys())[0]``
    with no regard for whether that first sheet was hidden or empty (e.g.
    Sage X3 exports ship a hidden ``Sage.X3.ReservedSheet`` config sheet as
    physical sheet 0). Callers should use this to skip hidden/veryHidden
    sheets when auto-selecting a default sheet.

    Returns an empty dict (meaning "visibility unknown, treat everything as
    visible") when the workbook kind doesn't expose this cheaply (.csv,
    calamine-only reads, or any read failure) -- callers must treat a
    missing key as visible=True, never as a reason to fail.
    """
    path = Path(filepath)
    suffix = path.suffix.lower()
    visibility: dict[str, bool] = {}

    if suffix in _EXCEL_OPENPYXL:
        try:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for ws in wb.worksheets:
                visibility[ws.title] = ws.sheet_state == "visible"
            wb.close()
            return visibility
        except Exception:
            # openpyxl can't parse some Sage X3 stylesheets (see
            # _read_excel_via_calamine_direct) -- fall through to the
            # zero-dependency XML peek below rather than give up.
            try:
                return _sheet_visibility_from_xml(path)
            except Exception:
                return {}

    if suffix in _EXCEL_XLRD:
        try:
            import xlrd

            book = xlrd.open_workbook(str(path), on_demand=True)
            for name in book.sheet_names():
                # xlrd: 0 visible, 1 hidden, 2 very hidden
                visibility[name] = book.sheet_visibility(name) == 0 if hasattr(
                    book, "sheet_visibility"
                ) else True
            return visibility
        except Exception:
            return {}

    return {}


def _sheet_visibility_from_xml(path: Path) -> dict[str, bool]:
    """
    Zero-dependency fallback: read sheet visibility straight out of
    ``xl/workbook.xml`` inside the .xlsx zip. Used when openpyxl itself
    cannot open the workbook (broken stylesheets) but we still want to
    avoid defaulting to a hidden sheet.
    """
    import zipfile

    visibility: dict[str, bool] = {}
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/workbook.xml").decode("utf-8", errors="ignore")
    for m in re.finditer(r"<sheet\b[^>]*/>", xml):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]*)"', tag)
        if not name_m:
            continue
        name = name_m.group(1)
        state_m = re.search(r'state="([^"]*)"', tag)
        state = state_m.group(1) if state_m else "visible"
        visibility[name] = state == "visible"
    return visibility


def read_excel_file(filepath: str | Path) -> dict[str, pd.DataFrame]:
    """
    Reads all sheets (or a single CSV as one sheet) with NO header assumption
    (header=None so header detection can inspect raw rows).

    Extension is the default router, but a cheap magic-byte peek overrides a
    ``.csv`` claim when the payload is actually OOXML or OLE Excel.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    if path.name.startswith("~$"):
        raise ValueError(
            f"'{path.name}' is a Microsoft Office lock/temp file, not a real "
            f"workbook (Office creates these automatically while the real "
            f"file is open, and sometimes leaves them behind after a crash). "
            f"It always fails to parse as a zip archive because it isn't one -- "
            f"that's not a bug in this pipeline. Close the file in Excel if "
            f"it's still open, delete this leftover '~$...' file if it "
            f"lingers, and re-upload the real "
            f"'{path.name[2:]}' instead."
        )

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

    # Content sniff before any CSV encoding/parse work (mislabeled exports).
    if suffix in _CSV:
        kind = _peek_workbook_kind(path)
        if kind in {"ooxml", "ole"}:
            _note_extension_mismatch(path, kind)
            return _read_excel_raw(path, force_kind=kind)
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
