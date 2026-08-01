"""Encoding detection & mojibake repair -> Consistency dimension.

Tool choice (plan.md Section 2, item 7): chardet >=7 for detection
(~99% accuracy, faster rewrite) + ftfy for mojibake repair. Pair them:
chardet names the encoding; ftfy fixes garbled text after a wrong decode.

Scope (important): ``check_encoding`` is only meaningful for **CSV** (and
similar text) inputs where raw bytes exist *before* decoding. ``.xlsx`` /
``.xls`` sheets are already decoded by openpyxl / xlrd / calamine by the
time a DataFrame exists — there is no meaningful file-level encoding to
inspect on an Excel-sourced frame. Do not call ``check_encoding`` on
Excel-derived DataFrames; call it with the CSV file's raw (or sampled)
bytes during / alongside CSV ingestion.

``repair_encoding`` / ``repair_encoding_frame`` operate on already-decoded
Unicode strings (fix mojibake). They may be applied to text cells from any
source when mojibake is present, but detection of *file* encoding remains
CSV-only.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pandas as pd

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.models import CheckResult

_DEFAULT_CONFIDENCE_THRESHOLD = 0.8
_DEFAULT_SAMPLE_SIZE = 100_000
# Common real-world mismatches when chardet confidence is low
_DEFAULT_FALLBACKS = ("utf-8-sig", "cp1252", "latin-1")


def _confidence_threshold() -> float:
    return float(
        SETTINGS.get("encoding_confidence_threshold", _DEFAULT_CONFIDENCE_THRESHOLD)
    )


def _default_sample_size() -> int:
    return int(SETTINGS.get("encoding_sample_size", _DEFAULT_SAMPLE_SIZE))


def _fallback_encodings() -> tuple[str, ...]:
    configured = SETTINGS.get("encoding_fallback_list")
    if configured:
        return tuple(str(x) for x in configured)
    return _DEFAULT_FALLBACKS


@lru_cache(maxsize=64)
def _chardet_detect_cached(sample: bytes) -> tuple[str | None, float]:
    """Cached chardet.detect for identical byte samples within a process."""
    import chardet

    guess = chardet.detect(sample) or {}
    encoding = guess.get("encoding")
    confidence = float(guess.get("confidence") or 0.0)
    return encoding, confidence


def _try_fallback_decodes(raw: bytes) -> dict[str, Any]:
    """
    Try utf-8-sig → cp1252 → latin-1; report the first that decodes cleanly.
    Does not change the primary chardet result — only adds a recommendation.
    """
    tried: list[dict[str, Any]] = []
    for enc in _fallback_encodings():
        try:
            raw.decode(enc)
            tried.append({"encoding": enc, "ok": True})
            return {
                "fallback_tried": tried,
                "recommended_encoding": enc,
                "fallback_ok": True,
            }
        except UnicodeDecodeError as exc:
            tried.append({"encoding": enc, "ok": False, "error": str(exc)})
    return {
        "fallback_tried": tried,
        "recommended_encoding": None,
        "fallback_ok": False,
    }


def check_encoding(
    raw_bytes: bytes,
    sample_size: int | None = None,
) -> CheckResult:
    """
    Detect the character encoding of raw CSV (text-file) bytes via chardet.

    Purpose
        File-level consistency check: name the encoding and whether chardet's
        confidence clears the configured threshold (plan default 0.8).

    Arguments
        raw_bytes: Undecoded file bytes (CSV / text only).
        sample_size: Max bytes to feed chardet (default 100_000 from settings,
            same limit historically used by ``ingestion._sniff_csv_encoding``).
            Pass a larger int or the full length if you intentionally want a
            deeper sample; ``0`` means "use entire ``raw_bytes``".

    Returns
        A single file-level ``CheckResult`` (``column=None``,
        ``dimension="consistency"``).
        ``status="passed"`` when confidence >= threshold;
        ``status="failed"`` when confidence is below threshold (details
        include ``low_confidence: True`` and, when possible,
        ``recommended_encoding`` from the fallback decode list);
        ``status="error"`` on bad input or unexpected failures (never raises).

    Raises
        Nothing — exceptions are captured into ``status="error"``.

    Notes
        Only for CSV / text bytes. Not applicable to already-parsed Excel
        DataFrames (see module docstring).
    """
    try:
        if not isinstance(raw_bytes, (bytes, bytearray)):
            raise TypeError("raw_bytes must be bytes (CSV/text file bytes only)")

        raw_full = bytes(raw_bytes)
        if len(raw_full) == 0:
            return CheckResult(
                check_name="encoding",
                status="failed",
                column=None,
                issues_found=1,
                details={
                    "encoding": None,
                    "confidence": 0.0,
                    "low_confidence": True,
                    "reason": "empty_bytes",
                    "byte_length": 0,
                    "sample_length": 0,
                },
                dimension="consistency",
            )

        limit = _default_sample_size() if sample_size is None else int(sample_size)
        sample = raw_full if limit <= 0 else raw_full[:limit]

        encoding, confidence = _chardet_detect_cached(sample)
        threshold = _confidence_threshold()
        low_confidence = confidence < threshold

        details: dict[str, Any] = {
            "encoding": encoding,
            "confidence": confidence,
            "confidence_threshold": threshold,
            "low_confidence": low_confidence,
            "byte_length": len(raw_full),
            "sample_length": len(sample),
            "sample_size_limit": limit if limit > 0 else None,
            "scope": "csv_text_bytes_only",
        }
        if low_confidence:
            details["flag"] = "low confidence"
            details.update(_try_fallback_decodes(sample))

        return CheckResult(
            check_name="encoding",
            status="failed" if low_confidence else "passed",
            column=None,
            issues_found=1 if low_confidence else 0,
            details=details,
            dimension="consistency",
        )
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return CheckResult(
            check_name="encoding",
            status="error",
            column=None,
            issues_found=0,
            details={"error": str(exc)},
            dimension="consistency",
        )


def repair_encoding(text: str) -> str:
    """
    Repair mojibake in a single Unicode string via ftfy.

    Purpose
        Fix garbled text that resulted from a wrong encoding decode
        (plan.md Section 2 / 4.3).

    Arguments
        text: Already-decoded string (possibly containing mojibake).

    Returns
        Repaired string from ``ftfy.fix_text``. Non-string inputs are
        coerced with ``str(...)`` so callers can pass cell values safely;
        ``None`` / NA-like values should be filtered by the frame helper.

    Raises
        Nothing intended for pipeline use — ftfy failures propagate only if
        ftfy itself raises; prefer ``repair_encoding_frame`` for batch work
        with per-cell isolation.
    """
    import ftfy

    if text is None:
        return text  # type: ignore[return-value]
    if not isinstance(text, str):
        text = str(text)
    return ftfy.fix_text(text)


def repair_encoding_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Apply ``repair_encoding()`` to every string/object cell.

    Purpose
        Make mojibake repair visible and logged across a DataFrame — same
        transparency idea as ``mask_pii`` (nothing silently altered without
        a countable, reportable trace).

    Arguments
        df: Input frame. Never mutated.

    Returns
        ``(new_df, report)`` where ``report`` includes
        ``columns_changed``, ``total_cells_changed``, ``columns_scanned``.
        On invalid input, never raises: empty DataFrame +
        ``report["status"] == "error"``.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        out = df.copy()
        columns_changed: dict[str, int] = {}
        columns_scanned = 0

        for col in out.columns:
            series = out[col]
            if not (
                pd.api.types.is_object_dtype(series)
                or pd.api.types.is_string_dtype(series)
            ):
                continue
            columns_scanned += 1
            changed = 0
            new_values = []
            for value in series:
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    new_values.append(value)
                    continue
                if not isinstance(value, str):
                    new_values.append(value)
                    continue
                try:
                    repaired = repair_encoding(value)
                except Exception:  # noqa: BLE001 - keep cell, continue frame
                    new_values.append(value)
                    continue
                if repaired != value:
                    changed += 1
                new_values.append(repaired)
            if changed:
                out[col] = new_values
                columns_changed[str(col)] = changed

        report = {
            "status": "ok",
            "columns_changed": columns_changed,
            "total_cells_changed": int(sum(columns_changed.values())),
            "columns_scanned": columns_scanned,
        }
        return out, report
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return pd.DataFrame(), {
            "status": "error",
            "error": str(exc),
            "columns_changed": {},
            "total_cells_changed": 0,
            "columns_scanned": 0,
        }
