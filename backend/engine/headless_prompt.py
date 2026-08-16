"""
Headless (non-interactive) implementation of engine.checkpoint.UserPrompt.

main.py's CLIPrompt calls Python's input() at two checkpoints (header-row
confirmation, processing-scope confirmation). That's correct for a human
running the CLI, but a server process has no terminal to read from and
must never block on stdin -- an API request has to complete deterministically
from a single call, for any file, without a person in the loop.

HeadlessPrompt always accepts the pipeline's own detection (header row,
full processing scope) exactly as if a human had typed "y" at every
checkpoint. This is the only behavior that keeps the API generic across
"any dataset" per the brief: it never special-cases a column name, a
sheet name, or a file layout. If detection is wrong for a given file, that
surfaces the same way it already does for header-detection edge cases in
the CLI (see main.py's handling of detect_header_row raising ValueError
for headerless sheets) -- as a normal, per-sheet, non-fatal outcome, not
as this class trying to be clever about it.
"""

from __future__ import annotations

from typing import Any

from backend.engine.checkpoint import UserPrompt


class HeadlessPrompt(UserPrompt):
    """Auto-confirms every checkpoint. No stdin access, no blocking.

    Used exclusively by the REST API's background pipeline runner
    (api/jobs.py) so a run started over HTTP can complete without a human
    present, for whatever file was uploaded.
    """

    def confirm(self, message: str, details: dict[str, Any]) -> bool:  # noqa: ARG002
        return True

    def ask_int(self, message: str, default: int | None = None) -> int:  # noqa: ARG002
        # Never actually reached: confirm() always returns True, so
        # confirm_header_row / confirm_processing_scope never fall through
        # to the ask_* branches. Implemented anyway (rather than raising)
        # so this class fully satisfies the UserPrompt contract on its own
        # and stays safe if a future checkpoint calls it directly.
        return default if default is not None else 0

    def ask_text(self, message: str, default: str | None = None) -> str:  # noqa: ARG002
        return default if default is not None else ""
