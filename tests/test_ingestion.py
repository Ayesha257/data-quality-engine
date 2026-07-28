"""Tests for Excel ingestion and header detection (Task 1)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data_quality_engine.engine.ingestion import (
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
