"""Tests for human-in-the-loop checkpoints (Task 2)."""

from __future__ import annotations

import pandas as pd
import pytest

from data_quality_engine.engine.checkpoint import (
    CLIPrompt,
    UserPrompt,
    apply_scope,
    confirm_header_row,
    confirm_processing_scope,
)


class FakePrompt(UserPrompt):
    """Deterministic prompt for tests -- no real stdin."""

    def __init__(
        self,
        *,
        confirm_answers: list[bool] | None = None,
        ints: list[int] | None = None,
        texts: list[str] | None = None,
    ):
        self._confirms = list(confirm_answers or [])
        self._ints = list(ints or [])
        self._texts = list(texts or [])

    def confirm(self, message: str, details: dict) -> bool:
        if not self._confirms:
            raise AssertionError(f"Unexpected confirm(): {message}")
        return self._confirms.pop(0)

    def ask_int(self, message: str, default: int | None = None) -> int:
        if not self._ints:
            if default is not None:
                return default
            raise AssertionError(f"Unexpected ask_int(): {message}")
        return self._ints.pop(0)

    def ask_text(self, message: str, default: str | None = None) -> str:
        if not self._texts:
            if default is not None:
                return default
            raise AssertionError(f"Unexpected ask_text(): {message}")
        return self._texts.pop(0)


def test_user_prompt_base_is_abstract():
    with pytest.raises(NotImplementedError):
        UserPrompt().confirm("x", {})


def test_confirm_header_row_accepts_detection():
    prompt = FakePrompt(confirm_answers=[True])
    preview = {
        "header": {"values": ["Name", "Age"]},
        "rows_above": [],
        "rows_below": [{"row_index": 1, "values": ["Ali", 30]}],
    }
    assert confirm_header_row(prompt, 0, preview) == 0


def test_confirm_header_row_manual_override():
    prompt = FakePrompt(confirm_answers=[False], ints=[2])
    preview = {"header": {"values": ["bad"]}, "rows_above": [], "rows_below": []}
    assert confirm_header_row(prompt, 0, preview) == 2


def test_confirm_header_row_headerless_accept():
    prompt = FakePrompt(confirm_answers=[True])
    preview = {
        "headerless": True,
        "header": {"values": ["unnamed_0"]},
        "note": "No credible header",
        "rows_above": [],
        "rows_below": [],
    }
    assert confirm_header_row(prompt, -1, preview) == -1


def test_confirm_processing_scope_full_file():
    prompt = FakePrompt(confirm_answers=[True])
    scope = confirm_processing_scope(prompt, row_count=100, col_count=5, estimated_seconds=1.2)
    assert scope["confirmed"] is True
    assert scope["row_start"] is None
    assert scope["columns"] is None


def test_confirm_processing_scope_narrowed():
    prompt = FakePrompt(
        confirm_answers=[False],
        ints=[10, 50],
        texts=["Name, Age"],
    )
    scope = confirm_processing_scope(prompt, row_count=100, col_count=5)
    assert scope["row_start"] == 10
    assert scope["row_end"] == 50
    assert scope["columns"] == ["Name", "Age"]


def test_apply_scope_rows_and_columns():
    df = pd.DataFrame(
        {
            "Name": ["A", "B", "C", "D"],
            "Age": [1, 2, 3, 4],
            "City": ["X", "Y", "Z", "W"],
        }
    )
    scoped = apply_scope(
        df,
        {"row_start": 1, "row_end": 3, "columns": ["Name", "City"]},
    )
    assert list(scoped.columns) == ["Name", "City"]
    assert scoped["Name"].tolist() == ["B", "C"]


def test_apply_scope_missing_column_raises():
    df = pd.DataFrame({"Name": [1]})
    with pytest.raises(KeyError):
        apply_scope(df, {"columns": ["Nope"]})


def test_cliprompt_confirm_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert CLIPrompt().confirm("ok?", {"a": 1}) is True


def test_cliprompt_confirm_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert CLIPrompt().confirm("ok?", {}) is False
