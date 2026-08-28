"""API-backed (human-in-the-loop) implementation of engine.checkpoint.UserPrompt.

HeadlessPrompt (headless_prompt.py) auto-accepts every checkpoint so a run
started over HTTP can complete without anyone present -- that's still the
default for the API and is what every existing test relies on.

APIPrompt is the opposite: it actually pauses the run and waits for a
human to answer, the same principle main.py's CLIPrompt already
implements for a terminal, just carried over the HTTP boundary instead of
stdin. It's opt-in per upload (see routes.py's `interactive` query param)
so nothing about the existing headless behavior changes unless a caller
asks for it.

How the pause works
--------------------
run_pipeline() runs on a ThreadPoolExecutor worker thread (see jobs.py).
When it hits engine/checkpoint.py's confirm_header_row(), that calls
prompt.confirm(message, details) and -- only if the answer is False --
prompt.ask_int(...) for an override. APIPrompt.confirm():

  1. Writes the checkpoint's details onto RunRecord.pending_confirmation
     and flips RunRecord.status to AWAITING_CONFIRMATION, so
     GET /v1/runs/{id}/status immediately reflects "waiting on you".
  2. Blocks the worker thread on a threading.Event scoped to this run_id.
  3. POST /v1/runs/{id}/confirm (routes.py) resolves that run's event via
     submit_answer() and the thread wakes up with the person's answer.

Blocking a worker thread while a human decides is intentional and safe
here: the executor is already bounded (jobs.py's _MAX_WORKERS), a paused
run just holds one slot rather than spinning, and every other endpoint
(status polling, other runs) goes through separate DB sessions/threads
and is unaffected.

Only the header-row checkpoint is exposed to a human this way today --
that's the one main.py's CLIPrompt actually prints per-sheet detection
detail for, and the one the person asked about. The processing-scope
checkpoint (confirm_processing_scope) auto-accepts the full detected
scope here exactly like HeadlessPrompt, so a run only ever pauses once
per sheet. That's a deliberate scope-limiting choice, not a limitation of
the mechanism below -- extending confirm() to also surface the scope
checkpoint would be a small, additive change (branch on
"rows"/"columns" in `details` the same way confirm() already branches on
"detected_header_row").
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend.engine.checkpoint import UserPrompt
from backend.engine.json_safe import json_safe

logger = logging.getLogger("dqe.phase2.api.api_prompt")

# How long a run will wait at a single checkpoint for a human to answer
# before giving up and falling back to the detected default (same
# behavior as HeadlessPrompt). This exists so an abandoned tab can't tie
# up a worker thread forever -- 30 minutes is generous for a person
# actually reviewing a preview, short enough that a forgotten run
# releases its executor slot within one work session.
CONFIRMATION_TIMEOUT_SECONDS = 30 * 60

_lock = threading.Lock()
_waiters: dict[str, threading.Event] = {}
_answers: dict[str, dict[str, Any]] = {}


def submit_answer(run_id: str, answer: dict[str, Any] | list[Any]) -> bool:
    """Called by POST /v1/runs/{run_id}/confirm or /v1/runs/{run_id}/compliance-confirm.
    Returns False if no checkpoint is currently waiting on this run_id (already answered,
    already timed out, or the run never paused in the first place) so
    the route can turn that into a 409 instead of silently no-op'ing."""
    with _lock:
        event = _waiters.get(run_id)
        if event is None:
            return False
        _answers[run_id] = answer if isinstance(answer, dict) else {"decisions": answer}
        event.set()
        return True


class APIPrompt(UserPrompt):
    """Pauses a background run and waits for a human's answer via the
    REST API. One instance is created per run (see jobs.py's _execute)."""

    def __init__(
        self,
        run_id: str,
        prompt_type: str | None = None,
        column_name: str | None = None,
        guessed_field: str | None = None,
        regulation: str | None = None,
        confidence: str | None = None,
        findings: list[dict[str, Any]] | None = None,
    ):
        self.run_id = run_id
        self.prompt_type = prompt_type
        self.column_name = column_name
        self.guessed_field = guessed_field
        self.regulation = regulation
        self.confidence = confidence
        self.findings = findings or []
        self._current_sheet: str | None = None
        self._pending_header_override: int | None = None

    def set_context(self, sheet_name: str) -> None:
        """Called by main.py's run_pipeline before each sheet's checkpoints
        so the confirmation payload can say which sheet it's about."""
        self._current_sheet = sheet_name

    def confirm(self, message: str, details: dict[str, Any]) -> bool:
        if "detected_header_row" not in details:
            # Processing-scope checkpoint (or anything else future
            # checkpoints add) -- auto-accept, see module docstring.
            return True

        payload = {
            "type": "header_row",
            "prompt_type": "HEADER_CONFIRM",
            "sheet_name": self._current_sheet,
            "message": message,
            "detected_header_row": details.get("detected_header_row"),
            "headerless": details.get("headerless"),
            "header_values": details.get("header_values"),
            "rows_above": details.get("rows_above"),
            "rows_below": details.get("rows_below"),
            "note": details.get("note"),
        }
        payload = json_safe(payload)

        event = threading.Event()
        with _lock:
            _waiters[self.run_id] = event
            _answers.pop(self.run_id, None)

        self._set_awaiting(payload)

        answered = event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)

        with _lock:
            _waiters.pop(self.run_id, None)
            answer = _answers.pop(self.run_id, None)

        if not answered or answer is None:
            logger.warning(
                "Run %s: header confirmation for sheet %r timed out after %ss; "
                "accepting the detected header row.",
                self.run_id,
                self._current_sheet,
                CONFIRMATION_TIMEOUT_SECONDS,
            )
            self._clear_awaiting()
            return True

        accept = bool(answer.get("accept", True))
        if not accept:
            self._pending_header_override = answer.get("override_header_row")
        return accept

    def confirm_compliance(
        self,
        findings: list[dict[str, Any]] | None = None,
        *,
        column_name: str | None = None,
        guessed_field: str | None = None,
        regulation: str | None = None,
        confidence: str | None = None,
    ) -> dict[str, bool]:
        """Pauses run for low-confidence compliance findings and waits for human review.
        Returns a dict mapping column_name -> is_confirmed (bool)."""
        finding_list: list[dict[str, Any]] = []
        if findings:
            finding_list.extend(findings)
        elif self.findings:
            finding_list.extend(self.findings)
        elif column_name or self.column_name:
            col = column_name or self.column_name
            g_field = guessed_field or self.guessed_field
            reg = regulation or self.regulation
            conf = confidence or self.confidence or "low"
            finding_list.append({
                "column_name": col,
                "guessed_field": g_field,
                "regulation": reg,
                "confidence": conf,
            })

        if not finding_list:
            return {}

        first_finding = finding_list[0]
        payload = {
            "type": "compliance_column",
            "prompt_type": "COMPLIANCE_COLUMN_CONFIRM",
            "sheet_name": self._current_sheet,
            "column_name": first_finding.get("column_name"),
            "guessed_field": first_finding.get("guessed_field"),
            "regulation": first_finding.get("regulation"),
            "confidence": first_finding.get("confidence", "low"),
            "findings": finding_list,
        }
        payload = json_safe(payload)

        event = threading.Event()
        with _lock:
            _waiters[self.run_id] = event
            _answers.pop(self.run_id, None)

        self._set_awaiting(payload)

        answered = event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)

        with _lock:
            _waiters.pop(self.run_id, None)
            answer = _answers.pop(self.run_id, None)

        if not answered or answer is None:
            logger.warning(
                "Run %s: compliance confirmation timed out after %ss; rejecting unconfirmed findings.",
                self.run_id,
                CONFIRMATION_TIMEOUT_SECONDS,
            )
            self._clear_awaiting()
            return {f["column_name"]: False for f in finding_list if "column_name" in f}

        # Parse decisions from answer payload
        decisions: dict[str, bool] = {}
        if isinstance(answer, dict):
            # Check if answer contains 'decisions' list or dict
            raw_decisions = answer.get("decisions")
            if isinstance(raw_decisions, list):
                for item in raw_decisions:
                    if isinstance(item, dict) and "column_name" in item:
                        col = item["column_name"]
                        confirmed = item.get("confirmed")
                        if confirmed is None:
                            confirmed = item.get("accept")
                        if confirmed is None and "decision" in item:
                            confirmed = str(item["decision"]).lower() in (
                                "confirm", "confirmed", "accept", "accepted", "true", "yes"
                            )
                        decisions[col] = bool(confirmed)
            elif isinstance(raw_decisions, dict):
                for col, val in raw_decisions.items():
                    decisions[col] = bool(val)
            elif "accept" in answer:
                # Single accept/reject applied to all findings
                val = bool(answer["accept"])
                for f in finding_list:
                    if "column_name" in f:
                        decisions[f["column_name"]] = val
            elif "confirmed" in answer:
                val = bool(answer["confirmed"])
                for f in finding_list:
                    if "column_name" in f:
                        decisions[f["column_name"]] = val

        # Default any unspecified finding to False (rejected)
        for f in finding_list:
            col = f.get("column_name")
            if col and col not in decisions:
                decisions[col] = False

        self._clear_awaiting()
        return decisions

    def ask_int(self, message: str, default: int | None = None) -> int:  # noqa: ARG002
        # Only reached after confirm() returned False for the header
        # checkpoint (the only checkpoint APIPrompt ever rejects on
        # behalf of the human) -- the override value already came in on
        # the same /confirm request, so this doesn't block again.
        if self._pending_header_override is not None:
            return self._pending_header_override
        return default if default is not None else -1

    def ask_text(self, message: str, default: str | None = None) -> str:  # noqa: ARG002
        return default if default is not None else ""

    def _set_awaiting(self, payload: dict[str, Any]) -> None:
        from backend.database import get_session
        from backend.database.models import RunRecord, RunStatus

        try:
            with get_session() as session:
                run = session.get(RunRecord, self.run_id)
                if run is not None:
                    run.status = RunStatus.AWAITING_CONFIRMATION
                    run.pending_confirmation = payload
        except Exception:  # noqa: BLE001 - never let bookkeeping crash the pipeline
            logger.exception("Could not mark run %s as awaiting confirmation", self.run_id)

    def _clear_awaiting(self) -> None:
        from backend.database import get_session
        from backend.database.models import RunRecord, RunStatus

        try:
            with get_session() as session:
                run = session.get(RunRecord, self.run_id)
                if run is not None:
                    run.status = RunStatus.RUNNING
                    run.pending_confirmation = None
        except Exception:  # noqa: BLE001
            logger.exception("Could not clear awaiting-confirmation state for run %s", self.run_id)
