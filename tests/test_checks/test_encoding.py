"""Tests for encoding detection (chardet) + mojibake repair (ftfy)."""

from __future__ import annotations

import pandas as pd

from backend.engine.checks.encoding import (
    check_encoding,
    repair_encoding,
    repair_encoding_frame,
)


def test_check_encoding_utf8_passes():
    raw = "hello café résumé".encode("utf-8")
    result = check_encoding(raw)
    assert result.check_name == "encoding"
    assert result.column is None
    assert result.dimension == "consistency"
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details.get("low_confidence") is False
    assert result.details.get("encoding") is not None
    assert "confidence" in result.details
    assert result.details["confidence"] >= result.details["confidence_threshold"]


def test_check_encoding_latin1_bytes_behavior():
    # Non-ASCII latin-1 payload — chardet may name latin-1/ISO-8859-1/windows-1252
    # or report low confidence depending on version/sample. Assert threshold rule
    # only: status reflects low_confidence, never crash.
    raw = "Café Niño - £100".encode("latin-1")
    result = check_encoding(raw)
    assert result.status in {"passed", "failed"}
    assert result.dimension == "consistency"
    assert "confidence" in result.details
    assert result.details["low_confidence"] == (result.status == "failed")
    if result.status == "failed":
        assert result.details.get("flag") == "low confidence"
        assert result.issues_found == 1
    else:
        assert result.issues_found == 0
        assert result.details.get("encoding") is not None


def test_repair_encoding_mojibake():
    # Classic UTF-8 misread as latin-1/cp1252 then shown as Unicode
    garbled = "Ã©"  # intended: é
    fixed = repair_encoding(garbled)
    assert fixed == "é"
    assert fixed != garbled


def test_repair_encoding_frame_new_object_and_report():
    clean = pd.DataFrame({"a": ["ok", "fine"], "b": [1, 2]})
    dirty = pd.DataFrame(
        {
            "notes": ["plain", "Ã©lan", "café"],
            "id": [10, 20, 30],
        }
    )

    out_clean, report_clean = repair_encoding_frame(clean)
    assert out_clean is not clean
    assert report_clean["total_cells_changed"] == 0
    assert report_clean["columns_changed"] == {}
    assert list(out_clean["a"]) == list(clean["a"])

    out_dirty, report_dirty = repair_encoding_frame(dirty)
    assert out_dirty is not dirty
    assert report_dirty["total_cells_changed"] >= 1
    assert "notes" in report_dirty["columns_changed"]
    assert report_dirty["columns_changed"]["notes"] >= 1
    assert "id" not in report_dirty["columns_changed"]
    # Original untouched
    assert dirty.loc[1, "notes"] == "Ã©lan"
    assert "é" in str(out_dirty.loc[1, "notes"])


def test_check_encoding_bad_input_returns_error_not_raise():
    result = check_encoding("not-bytes")  # type: ignore[arg-type]
    assert result.status == "error"
    assert "error" in result.details
    assert result.column is None
    assert result.dimension == "consistency"


def test_repair_encoding_frame_bad_input_returns_error_not_raise():
    out, report = repair_encoding_frame("not-a-dataframe")  # type: ignore[arg-type]
    assert report.get("status") == "error"
    assert "error" in report
    assert report["total_cells_changed"] == 0
    assert isinstance(out, pd.DataFrame)



def test_check_encoding_empty_bytes_failed_low_confidence():
    result = check_encoding(b"")
    assert result.status == "failed"
    assert result.details.get("low_confidence") is True
    assert result.details.get("reason") == "empty_bytes"


def test_check_encoding_respects_sample_size():
    # Large payload; sample_size=50 must be reflected in details
    raw = ("x" * 10_000 + "café").encode("utf-8")
    result = check_encoding(raw, sample_size=50)
    assert result.status in {"passed", "failed", "error"}
    assert result.details.get("sample_length") == 50
    assert result.details.get("byte_length") == len(raw)


def test_check_encoding_low_confidence_runs_fallback(monkeypatch):
    from backend.engine.checks import encoding as enc_mod

    monkeypatch.setattr(enc_mod, "_chardet_detect_cached", lambda _sample: ("ascii", 0.1))
    raw = "Café".encode("utf-8")
    result = check_encoding(raw, sample_size=100)
    assert result.status == "failed"
    assert result.details["low_confidence"] is True
    assert "fallback_tried" in result.details
    assert result.details.get("recommended_encoding") in {
        "utf-8-sig",
        "cp1252",
        "latin-1",
        None,
    } or result.details.get("fallback_ok") in {True, False}


def test_chardet_cache_reuses_identical_sample():
    from backend.engine.checks.encoding import _chardet_detect_cached

    _chardet_detect_cached.cache_clear()
    sample = b"hello world cafe"
    _chardet_detect_cached(sample)
    _chardet_detect_cached(sample)
    info = _chardet_detect_cached.cache_info()
    assert info.hits >= 1

