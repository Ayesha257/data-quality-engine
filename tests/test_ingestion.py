"""Tests for Excel ingestion and header detection (Task 1)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.engine.ingestion import (
    detect_header_row,
    header_preview,
    load_with_confirmed_header,
    read_excel_file,
)


def _write_xlsx(path: Path, rows: list[list], sheet_name: str = "Sheet1") -> Path:
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
    return path


@pytest.fixture
def clean_header_xlsx(tmp_path: Path) -> Path:
    rows = [
        ["Name", "Age", "City"],
        ["Ali", 30, "Lahore"],
        ["Sara", 25, "Karachi"],
        ["Omar", 40, "Islamabad"],
    ]
    return _write_xlsx(tmp_path / "clean.xlsx", rows)


@pytest.fixture
def title_then_header_xlsx(tmp_path: Path) -> Path:
    rows = [
        ["Client Export - Q1 2024", None, None],
        [None, None, None],
        ["Name", "Age", "City"],
        ["Ali", 30, "Lahore"],
        ["Sara", 25, "Karachi"],
        ["Omar", 40, "Islamabad"],
        ["Noor", 22, "Multan"],
    ]
    return _write_xlsx(tmp_path / "titled.xlsx", rows)


def test_read_excel_file_returns_all_sheets(clean_header_xlsx: Path):
    sheets = read_excel_file(clean_header_xlsx)
    assert "Sheet1" in sheets
    assert sheets["Sheet1"].shape[0] == 4
    # No header applied yet -- first row is still data
    assert sheets["Sheet1"].iloc[0, 0] == "Name"


def test_read_excel_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        read_excel_file("does_not_exist.xlsx")


def test_read_excel_file_bad_suffix(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_excel_file(bad)


def test_detect_header_row_when_first_row_is_header(clean_header_xlsx: Path):
    raw = read_excel_file(clean_header_xlsx)["Sheet1"]
    assert detect_header_row(raw) == 0


def test_detect_header_row_skips_title_rows(title_then_header_xlsx: Path):
    raw = read_excel_file(title_then_header_xlsx)["Sheet1"]
    assert detect_header_row(raw) == 2


def test_detect_header_row_empty_raises():
    with pytest.raises(ValueError):
        detect_header_row(pd.DataFrame())


def test_load_with_confirmed_header(title_then_header_xlsx: Path):
    raw = read_excel_file(title_then_header_xlsx)["Sheet1"]
    df = load_with_confirmed_header(raw, header_row=2)
    assert list(df.columns) == ["Name", "Age", "City"]
    assert len(df) == 4
    assert df.iloc[0]["Name"] == "Ali"


def test_load_with_confirmed_header_unique_duplicate_names(tmp_path: Path):
    rows = [
        ["Name", "Name", "Age"],
        ["A", "B", 1],
    ]
    path = _write_xlsx(tmp_path / "dup_cols.xlsx", rows)
    raw = read_excel_file(path)["Sheet1"]
    df = load_with_confirmed_header(raw, 0)
    assert list(df.columns) == ["Name", "Name_1", "Age"]


def test_load_with_confirmed_header_bad_index(clean_header_xlsx: Path):
    raw = read_excel_file(clean_header_xlsx)["Sheet1"]
    with pytest.raises(IndexError):
        load_with_confirmed_header(raw, 99)


def test_header_preview_shape(title_then_header_xlsx: Path):
    raw = read_excel_file(title_then_header_xlsx)["Sheet1"]
    preview = header_preview(raw, header_row=2, context_rows=2)
    assert preview["detected_header_row"] == 2
    assert preview["header"]["values"][0] == "Name"
    assert len(preview["rows_above"]) == 2
    assert len(preview["rows_below"]) == 2


def test_detect_header_row_headerless_numeric_scrap():
    """Leftover pivot helper: numbers interleaved with blanks — no real header."""
    raw = pd.DataFrame(
        [
            [100.0],
            [None],
            [200.0],
            [None],
            [300.0],
            [None],
            [400.0],
            [None],
            [500.0],
            [None],
            [600.0],
            [None],
            [700.0],
            [None],
            [800.0],
            [None],
        ]
    )
    assert detect_header_row(raw) == -1
    df = load_with_confirmed_header(raw, -1)
    assert list(df.columns) == ["unnamed_0"]
    assert len(df) == 8
    assert df["unnamed_0"].tolist() == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0]


def test_header_preview_headerless():
    raw = pd.DataFrame([[1], [None], [2]])
    preview = header_preview(raw, header_row=-1)
    assert preview["headerless"] is True
    assert preview["detected_header_row"] == -1


def test_blank_row_never_chosen_as_header():
    raw = pd.DataFrame(
        [
            [None, None, None],
            ["Name", "Age", "City"],
            ["Ali", 30, "Lahore"],
        ]
    )
    assert detect_header_row(raw) == 1


def test_calamine_helper_reports_install_status():
    from backend.engine.ingestion import _calamine_installed

    # Should be True in CI/dev after `pip install python-calamine`
    assert isinstance(_calamine_installed(), bool)


def test_csv_extension_with_ooxml_bytes_routes_to_excel(tmp_path: Path, capsys):
    """Mislabeled .csv that is actually OOXML must use the Excel reader."""
    xlsx = _write_xlsx(
        tmp_path / "real.xlsx",
        [["Name", "Age"], ["Ali", 30], ["Sara", 25]],
    )
    fake_csv = tmp_path / "Booked Orders copy.csv"
    fake_csv.write_bytes(xlsx.read_bytes())
    assert fake_csv.read_bytes()[:4] == b"PK\x03\x04"

    sheets = read_excel_file(fake_csv)
    out = capsys.readouterr().out
    assert "Booked Orders copy.csv" in out
    assert ".csv extension" in out
    assert "OOXML" in out
    assert "Reading it as Excel instead" in out
    assert "Sheet1" in sheets
    assert sheets["Sheet1"].iloc[0, 0] == "Name"
    # Must not look like the CSV path (single sheet named after stem only
    # with no Excel sheet title). Excel path preserves workbook sheet names.
    assert list(sheets.keys()) == ["Sheet1"]


def test_csv_extension_with_ole_bytes_routes_to_xlrd_path(
    tmp_path: Path, capsys, monkeypatch
):
    """Mislabeled .csv with OLE magic must call the xlrd Excel path, not CSV."""
    from backend.engine import ingestion as ing

    fake_csv = tmp_path / "legacy_export.csv"
    fake_csv.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)

    seen: dict = {}

    def spy_excel(path, *, force_kind=None):
        seen["force_kind"] = force_kind
        seen["path"] = Path(path)
        return {"Sheet1": pd.DataFrame([["Name"], ["Ali"]], dtype=object)}

    def boom_csv(path):
        raise AssertionError("_read_csv_raw must not be called for OLE-mislabeled CSV")

    monkeypatch.setattr(ing, "_read_excel_raw", spy_excel)
    monkeypatch.setattr(ing, "_read_csv_raw", boom_csv)

    sheets = read_excel_file(fake_csv)
    out = capsys.readouterr().out
    assert seen["force_kind"] == "ole"
    assert seen["path"] == fake_csv
    assert "legacy_export.csv" in out
    assert ".csv extension" in out
    assert "OLE" in out
    assert "Reading it as Excel instead" in out
    assert "Sheet1" in sheets


def test_sniff_csv_encoding_none_high_confidence_uses_fallback(
    tmp_path: Path, monkeypatch
):
    """encoding=None + confidence=1.0 must not be blindly trusted as utf-8."""
    from backend.engine.checks import encoding as enc_mod
    from backend.engine.ingestion import _sniff_csv_encoding
    from backend.engine.models import CheckResult

    path = tmp_path / "cp1252.csv"
    # 0xE9 = 'é' in cp1252; invalid as UTF-8.
    path.write_bytes(b"Name,City\nCaf\xe9,Paris\n")

    def fake_check(raw_bytes, sample_size=None):
        return CheckResult(
            check_name="encoding",
            status="passed",
            column=None,
            issues_found=0,
            details={
                "encoding": None,
                "confidence": 1.0,
                "confidence_threshold": 0.8,
                "low_confidence": False,
            },
            dimension="consistency",
        )

    monkeypatch.setattr(enc_mod, "check_encoding", fake_check)
    chosen = _sniff_csv_encoding(path)
    assert chosen == "cp1252"
    assert chosen != "utf-8"


def test_sniff_csv_encoding_utf8_unaffected(tmp_path: Path, monkeypatch):
    """Valid UTF-8 CSV: keep chardet encoding; do not run extra fallbacks."""
    from backend.engine.checks import encoding as enc_mod
    from backend.engine.ingestion import _sniff_csv_encoding

    path = tmp_path / "ok.csv"
    path.write_bytes("Name,Age\nAli,30\n".encode("utf-8"))

    calls = {"fallback": 0}
    real_fb = enc_mod._try_fallback_decodes

    def spy_fb(raw):
        calls["fallback"] += 1
        return real_fb(raw)

    monkeypatch.setattr(enc_mod, "_try_fallback_decodes", spy_fb)
    chosen = _sniff_csv_encoding(path)
    assert chosen.lower().replace("-", "") in {"utf8", "ascii"}
    assert calls["fallback"] == 0
