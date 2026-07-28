"""Human-in-the-loop confirmation checkpoints.

Phase 1 uses CLIPrompt (console input). API/UI can swap UserPrompt later
without changing pipeline.py.
"""

from __future__ import annotations

from typing import Any


class UserPrompt:
    """
    Abstraction over how confirmation is asked.
    In Phase 1 CLI mode, this prints to console and reads input().
    Later (API/UI), this can be swapped for a different implementation
    without changing pipeline.py.
    """

    def confirm(self, message: str, details: dict[str, Any]) -> bool:
        """Show message and details to the user; return True if confirmed."""
        raise NotImplementedError

    def ask_int(self, message: str, default: int | None = None) -> int:
        """Ask the user for an integer (used for header override / row ranges)."""
        raise NotImplementedError

    def ask_text(self, message: str, default: str | None = None) -> str:
        """Ask the user for free text (e.g. comma-separated column subset)."""
        raise NotImplementedError


class CLIPrompt(UserPrompt):
    """Console-backed prompt for Phase 1 CLI runs."""

    def confirm(self, message: str, details: dict[str, Any]) -> bool:
        print(message)
        for key, value in details.items():
            print(f"  {key}: {value}")
        response = input("Proceed? (y/n): ").strip().lower()
        return response in {"y", "yes"}

    def ask_int(self, message: str, default: int | None = None) -> int:
        hint = f" [{default}]" if default is not None else ""
        while True:
            raw = input(f"{message}{hint}: ").strip()
            if not raw and default is not None:
                return default
            try:
                return int(raw)
            except ValueError:
                print("Please enter a whole number.")

    def ask_text(self, message: str, default: str | None = None) -> str:
        hint = f" [{default}]" if default is not None else ""
        raw = input(f"{message}{hint}: ").strip()
        if not raw and default is not None:
            return default
        return raw


def confirm_header_row(
    prompt: UserPrompt,
    detected_row: int,
    preview: dict[str, Any],
) -> int:
    """
    Checkpoint 1 - Header row confirmation.
    If the user rejects the detection, ask for a manual override index.
    """
    details = {
        "detected_header_row": detected_row,
        "header_values": preview.get("header", {}).get("values"),
        "rows_above": preview.get("rows_above"),
        "rows_below": preview.get("rows_below"),
    }
    message = (
        f"I believe row {detected_row} is the header. Here's what I found:"
    )
    if prompt.confirm(message, details):
        return detected_row
    return prompt.ask_int(
        "Enter the correct header row index (0-based)",
        default=detected_row,
    )


def confirm_processing_scope(
    prompt: UserPrompt,
    row_count: int,
    col_count: int,
    estimated_seconds: float | None = None,
) -> dict[str, Any]:
    """
    Checkpoint 2 - Processing scope confirmation.
    Returns a scope dict the pipeline can apply:
      {
        "confirmed": bool,
        "row_start": int | None,
        "row_end": int | None,   # exclusive
        "columns": list[str] | None,
      }
    """
    est = (
        f"{estimated_seconds:.1f}s"
        if estimated_seconds is not None
        else "unknown"
    )
    details = {
        "rows": row_count,
        "columns": col_count,
        "estimated_processing_time": est,
    }
    message = (
        f"This file has {row_count} rows and {col_count} columns. "
        f"Estimated processing time: {est}."
    )
    if prompt.confirm(message, details):
        return {
            "confirmed": True,
            "row_start": None,
            "row_end": None,
            "columns": None,
        }

    # User wants to narrow scope
    row_start = prompt.ask_int("Start row index (0-based, inclusive)", default=0)
    row_end = prompt.ask_int(
        "End row index (exclusive, blank = all remaining)",
        default=row_count,
    )
    cols_raw = prompt.ask_text(
        "Comma-separated columns to keep (blank = all)",
        default="",
    )
    columns = [c.strip() for c in cols_raw.split(",") if c.strip()] or None
    return {
        "confirmed": True,
        "row_start": row_start,
        "row_end": row_end,
        "columns": columns,
    }


def apply_scope(df, scope: dict[str, Any]):
    """Apply a scope dict from confirm_processing_scope to a DataFrame."""
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    out = df
    row_start = scope.get("row_start")
    row_end = scope.get("row_end")
    if row_start is not None or row_end is not None:
        start = 0 if row_start is None else row_start
        end = len(out) if row_end is None else row_end
        out = out.iloc[start:end]

    columns = scope.get("columns")
    if columns:
        missing = [c for c in columns if c not in out.columns]
        if missing:
            raise KeyError(f"Scope columns not found in dataframe: {missing}")
        out = out.loc[:, columns]

    return out.reset_index(drop=True)
