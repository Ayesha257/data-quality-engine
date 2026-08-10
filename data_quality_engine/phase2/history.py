"""
Phase 2 — Run history & score trend (M2 addition).

Every time generate_report_phase2.py builds a report, this module saves a
RunRecord (schema already defined in M1's database/models.py, previously
unused) and looks up the most recent PRIOR run for the same client_id +
file_name so the report can show "score improved/declined by X since
last run" instead of a bare, context-free number.

Generic by construction: "file_name" is just whatever filename was
processed -- there's no dataset-specific logic here. A brand-new
client/file combination simply has no prior run to compare against,
which is a normal, handled case (trend=None), not an error.

Never blocks report generation: if the database is unavailable for any
reason, save_run() and get_score_trend() both fail soft (log-and-return-
None / log-and-skip) rather than raising, matching the same
never-break-the-report philosophy as ai_explainer.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from data_quality_engine.phase2.database import get_session, init_db
from data_quality_engine.phase2.database.models import RunRecord, RunStatus, _utcnow

logger = logging.getLogger("dqe.phase2.history")

_db_initialized = False


def ensure_db_ready() -> bool:
    """Initialize the Phase 2 database on first use. Returns False (and
    logs a warning) instead of raising if it can't be reached, so history
    tracking degrades gracefully rather than breaking report generation."""
    global _db_initialized
    if _db_initialized:
        return True
    try:
        init_db()
        _db_initialized = True
        return True
    except Exception as exc:  # noqa: BLE001 - history must never break the report
        logger.warning("Phase 2 database unavailable, run history disabled: %s", exc)
        return False


@dataclass
class ScoreTrend:
    """What changed since the last time this client/file combination was
    processed. previous_run_id is None the first time a file is seen."""

    previous_score: float | None
    current_score: float
    delta: float | None
    previous_run_id: str | None
    previous_run_at: str | None

    @property
    def direction(self) -> str:
        if self.delta is None:
            return "first_run"
        if self.delta > 0.05:
            return "improved"
        if self.delta < -0.05:
            return "declined"
        return "unchanged"

    def to_display_text(self) -> str:
        if self.direction == "first_run":
            return "First recorded run for this client/file — no prior score to compare."
        sign = "+" if self.delta >= 0 else ""
        return (
            f"Score {self.direction} {sign}{self.delta:.1f} points "
            f"(was {self.previous_score:.1f}, now {self.current_score:.1f}) "
            f"since the last run on {self.previous_run_at}."
        )


def save_run(
    client_id: str,
    file_name: str,
    overall_score: float,
    dimension_scores: dict[str, float] | None = None,
    rows_processed: int | None = None,
    cols_processed: int | None = None,
    ruleset_version: str | None = None,
) -> str | None:
    """
    Persist a RunRecord for this report generation. Returns the new run's
    id, or None if the database wasn't available (report generation still
    continues either way).
    """
    if not ensure_db_ready():
        return None
    try:
        with get_session() as session:
            run = RunRecord(
                client_id=client_id,
                file_name=file_name,
                status=RunStatus.COMPLETED,
                overall_score=overall_score,
                dimension_scores=dimension_scores or {},
                rows_processed=rows_processed,
                cols_processed=cols_processed,
                ruleset_version=ruleset_version,
                # Use the shared monotonic clock (models._utcnow), not a
                # bare datetime.now() call -- see its docstring. This is
                # what makes "most recent run" ordering below reliable
                # even when two runs are saved back to back.
                completed_at=_utcnow(),
            )
            session.add(run)
            session.flush()
            return run.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save run history for %s/%s: %s", client_id, file_name, exc)
        return None


def get_score_trend(
    client_id: str,
    file_name: str,
    current_score: float,
    exclude_run_id: str | None = None,
) -> ScoreTrend | None:
    """
    Look up the most recent COMPLETED run for this client_id + file_name
    (excluding the run just saved, if its id is known) and compute the
    delta against current_score.

    Returns None only if the database is unavailable -- a genuinely
    first-ever run still returns a ScoreTrend with direction="first_run",
    since that's useful, expected information for the report, not a
    missing-data case.
    """
    if not ensure_db_ready():
        return None
    try:
        with get_session() as session:
            query = (
                session.query(RunRecord)
                .filter(
                    RunRecord.client_id == client_id,
                    RunRecord.file_name == file_name,
                    RunRecord.status == RunStatus.COMPLETED,
                    RunRecord.overall_score.isnot(None),
                )
                # completed_at is generated from the monotonic clock in
                # models._utcnow(), so ties within a single process can't
                # happen; created_at is a second, independently-generated
                # monotonic timestamp kept as a tiebreaker for the
                # cross-process case (e.g. two app instances racing to
                # write to the same production database).
                .order_by(RunRecord.completed_at.desc(), RunRecord.created_at.desc())
            )
            if exclude_run_id:
                query = query.filter(RunRecord.id != exclude_run_id)
            previous = query.first()

            if previous is None:
                return ScoreTrend(
                    previous_score=None,
                    current_score=current_score,
                    delta=None,
                    previous_run_id=None,
                    previous_run_at=None,
                )
            return ScoreTrend(
                previous_score=previous.overall_score,
                current_score=current_score,
                delta=round(current_score - previous.overall_score, 2),
                previous_run_id=previous.id,
                previous_run_at=(
                    previous.completed_at.strftime("%Y-%m-%d %H:%M UTC")
                    if previous.completed_at
                    else "an earlier run"
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not compute score trend for %s/%s: %s", client_id, file_name, exc)
        return None


def record_run_and_get_trend(
    client_id: str,
    file_name: str,
    report_data: dict[str, Any],
) -> ScoreTrend | None:
    """
    Convenience wrapper for generate_report_phase2.py: computes the trend
    against history BEFORE saving (so "previous" never means "myself"),
    then saves the current run. Single call site, no ordering bugs.
    """
    score_block = report_data.get("score", {}) or {}
    current_score = score_block.get("overall")
    if current_score is None:
        return None

    trend = get_score_trend(client_id, file_name, current_score=float(current_score))

    overview = report_data.get("overview", {}) or {}
    save_run(
        client_id=client_id,
        file_name=file_name,
        overall_score=float(current_score),
        dimension_scores=score_block.get("dimension_scores"),
        rows_processed=overview.get("rows"),
        cols_processed=overview.get("columns"),
    )
    return trend