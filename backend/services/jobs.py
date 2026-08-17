"""
Phase 2 -- M4: background execution of run_pipeline() for the REST API.

Design choice: a thread pool, not Redis/RQ.
--------------------------------------------
PHASE2_PLAN.md sketches Redis + RQ workers, but nothing in this codebase
today runs multiple processes or needs cross-machine job distribution --
main.py's run_pipeline() is a single-process, synchronous, per-file
pipeline, exactly like every other module in backend/. Adding
a message broker would mean standing up infrastructure this project
doesn't otherwise use, for a benefit (multi-machine scaling) nothing here
asks for. A bounded ThreadPoolExecutor gives the same observable contract
the plan describes -- upload returns immediately with a run_id, status is
pollable, results appear when done -- using only what's already a
dependency of this project (the standard library + SQLAlchemy).
run_pipeline() is I/O- and pandas/numpy-bound, not CPU-bound Python, so
threads (not multiprocessing) are the right tool: no pickling of
DataFrames across a process boundary, no per-worker cold start.

If this ever needs to scale past one machine, swap _EXECUTOR.submit(...)
in enqueue_run() for a real queue -- nothing else in this module or in
routes.py would need to change, since callers only ever interact through
enqueue_run() / get run status via the database.

Generic by construction.
-------------------------
This module never inspects file contents, column names, or client-specific
logic -- it only moves bytes to disk and calls run_pipeline() with
whatever client_id / target_column / date_column the request specified.
Every dataset-specific decision (header detection, column classification,
which checks apply) already lives inside run_pipeline() and the Phase 1
engine it calls; duplicating any of that logic here would be exactly the
kind of dataset-specific special-casing the brief asked to avoid.
"""

from __future__ import annotations

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.config.settings import SETTINGS
from backend.engine.headless_prompt import HeadlessPrompt
from backend.database import get_session
from backend.database.models import RunRecord, RunStatus

logger = logging.getLogger("dqe.phase2.api.jobs")


def _json_safe(value: Any) -> Any:
    """Recursively coerces numpy/pandas scalar types (int64, bool_,
    Timestamp, etc.) that leak out of the pandas-based pipeline into
    plain JSON-serializable Python types.

    Why this exists: SQLAlchemy's JSON column type serializes via the
    stdlib `json` module. `json.dumps` happily accepts `numpy.float64`
    (it subclasses `float`) but raises TypeError on `numpy.int64`,
    `numpy.bool_`, and `pandas.Timestamp` -- none of which are float
    subclasses. Before this helper, a single stray numpy int anywhere in
    a run's outcome dict (e.g. a readiness score, a verdict count) would
    make the whole manifest/dimension-score write raise, get caught by
    the caller's bare except, and silently leave that run's Sheets table
    and dimension breakdown permanently empty -- with no visible error
    to the user or an obvious way to recover short of re-running.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):  # covers numpy.float64 too (it IS a float)
        return value
    if hasattr(value, "item"):  # numpy scalar (int64, bool_, float32, ...)
        return value.item()
    if hasattr(value, "isoformat"):  # datetime/date/Timestamp
        return value.isoformat()
    return value

# Bounded so a burst of uploads can't spawn unbounded threads / open file
# handles / pandas workloads at once. 4 is a conservative default for a
# single-box deployment; override via configure_executor() at startup if
# the host has more headroom (e.g. in a real deployment's ASGI lifespan).
_MAX_WORKERS = 4
_EXECUTOR: ThreadPoolExecutor | None = None


def configure_executor(max_workers: int = _MAX_WORKERS) -> ThreadPoolExecutor:
    """(Re)create the shared background-job thread pool. Call once at app
    startup (see api/app.py's lifespan). Idempotent-safe to call again in
    tests with a fresh pool per test module."""
    global _EXECUTOR
    _EXECUTOR = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dqe-run")
    return _EXECUTOR


def get_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None or getattr(_EXECUTOR, "_shutdown", False):
        _EXECUTOR = configure_executor()
    return _EXECUTOR


def _mark_failed(run_id: str, message: str) -> None:
    try:
        with get_session() as session:
            run = session.get(RunRecord, run_id)
            if run is not None:
                run.status = RunStatus.FAILED
                run.error_message = message[:4000]  # Text column, but keep log/db payloads sane
    except Exception:  # noqa: BLE001 - never let bookkeeping failure mask the original error
        logger.exception("Could not mark run %s as failed", run_id)


def _mark_running(run_id: str) -> None:
    try:
        with get_session() as session:
            run = session.get(RunRecord, run_id)
            if run is not None:
                run.status = RunStatus.RUNNING
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark run %s as running", run_id)


def _mark_completed(
    run_id: str,
    *,
    rows_processed: int | None,
    cols_processed: int | None,
    overall_score: float | None,
    dimension_scores: dict[str, Any] | None,
) -> None:
    from backend.database.models import _utcnow  # shared monotonic clock

    try:
        with get_session() as session:
            run = session.get(RunRecord, run_id)
            if run is not None:
                run.status = RunStatus.COMPLETED
                run.rows_processed = rows_processed
                run.cols_processed = cols_processed
                run.overall_score = overall_score
                run.dimension_scores = dimension_scores or {}
                run.completed_at = _utcnow()
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark run %s as completed", run_id)


def _execute(
    *,
    run_id: str,
    file_path: str,
    sheet_name: str | None,
    client_id: str,
    target_column: str | None,
    date_column: str | None,
    reference_dir: str | None,
    include_products: bool,
    write_report: bool,
    report_dir: str | None,
    gemini_api_key: str | None,
    include_hipaa: bool = False,
) -> None:
    """Runs on a worker thread. Never raises -- every failure path updates
    the RunRecord instead, so a crashed run is always observable via the
    status endpoint rather than vanishing into a dead thread."""
    try:
        from backend.main import run_pipeline

        _mark_running(run_id)
        outcomes = run_pipeline(
            file_path,
            sheet_name,
            prompt=HeadlessPrompt(),
            reference_dir=reference_dir,
            include_products=include_products,
            write_report=write_report,
            report_dir=report_dir,
            target_column=target_column,
            date_column=date_column,
            client_id=client_id,
            gemini_api_key=gemini_api_key,
            include_hipaa=include_hipaa,
        )

        outcomes = outcomes or []
        failed_sheets = [o for o in outcomes if o.get("error")]
        scored = [o for o in outcomes if o.get("data_quality_score") is not None]

        if not outcomes:
            # Every sheet was hidden/empty/unreadable -- a real, reportable
            # outcome for this file, not a crash. Any dataset can produce
            # this (e.g. a workbook of only chart/config tabs).
            _mark_failed(run_id, "No sheets were processed (empty, headerless, or all hidden).")
            return

        if failed_sheets and not scored:
            # Nothing usable came out of any sheet.
            first_error = failed_sheets[0].get("error") or "unknown error"
            _mark_failed(run_id, f"All sheets failed. First error: {first_error}")
            return

        # Aggregate across sheets generically -- no sheet name or column name
        # is ever inspected here. Multi-sheet files get an average score
        # across successfully-scored sheets; single-sheet files (the common
        # case) just get that sheet's score.
        overall_score = (
            sum(o["data_quality_score"] for o in scored) / len(scored) if scored else None
        )
        total_rows = sum(o["rows"] for o in scored if o.get("rows") is not None) or None
        # cols_processed isn't summed across sheets (that's not a meaningful
        # number); report the max width seen, which is still useful signal
        # and never misleading the way a sum would be.
        col_values = [o["columns"] for o in scored if o.get("columns") is not None]
        total_cols = max(col_values) if col_values else None

        # Aggregate per-dimension scores across scored sheets. Each outcome now
        # carries its own `dimension_scores` dict (score/weight/available per
        # dimension, straight from engine/scoring.py). Single-sheet files just
        # pass that dict through; multi-sheet files average `score` across the
        # sheets where that dimension was `available`, and keep the max weight
        # seen (weight is a property of the rule config, not the data, so it's
        # identical across sheets in practice).
        dimension_scores: dict[str, Any] = {}
        per_dim: dict[str, list[dict[str, Any]]] = {}
        for o in scored:
            for dim, info in (o.get("dimension_scores") or {}).items():
                per_dim.setdefault(dim, []).append(info)

        for dim, infos in per_dim.items():
            available_infos = [i for i in infos if i.get("available") and isinstance(i.get("score"), (int, float))]
            if available_infos:
                avg_score = sum(i["score"] for i in available_infos) / len(available_infos)
                dimension_scores[dim] = {
                    "score": avg_score,
                    "available": True,
                    "weight": available_infos[0].get("weight", 0),
                }
            else:
                dimension_scores[dim] = {
                    "score": None,
                    "available": False,
                    "weight": infos[0].get("weight", 0) if infos else 0,
                }

        _mark_completed(
            run_id,
            rows_processed=total_rows,
            cols_processed=total_cols,
            overall_score=overall_score,
            dimension_scores=_json_safe(dimension_scores),
        )

        # Stash the richer per-sheet detail (report paths, readiness verdicts,
        # any partial failures) as a run manifest row -- generic JSON, no
        # dataset-specific shape assumed by the writer. Sanitized through
        # _json_safe first: outcomes come straight out of the pandas/numpy
        # pipeline and a single stray numpy scalar here used to silently wipe
        # out this entire block (see _json_safe's docstring).
        try:
            _write_manifest(run_id, _json_safe(outcomes))
        except Exception:  # noqa: BLE001 - manifest is supplementary, never fails the run
            logger.exception("Could not write manifest for run %s", run_id)

    except Exception as exc:  # noqa: BLE001 - top-level safety: unhandled exception always marks run FAILED
        logger.exception("Run %s failed with unhandled exception", run_id)
        _mark_failed(run_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}")


def _write_manifest(run_id: str, outcomes: list[dict[str, Any]]) -> None:
    from backend.database.models import RunManifest

    with get_session() as session:
        existing = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        checks_run = sorted(
            {
                "missing_values", "duplicates", "type_mismatch", "outliers", "pii",
                "fuzzy_standardization", "entity_resolution", "schema_quality",
                "consistency", "validity", "freshness", "scoring",
            }
        )
        payload = {"sheets": outcomes}
        if existing is not None:
            existing.checks_run = checks_run
            existing.extra = payload
        else:
            session.add(
                RunManifest(
                    run_id=run_id,
                    checks_run=checks_run,
                    ruleset_snapshot={},
                    extra=payload,
                )
            )


def enqueue_run(
    *,
    run_id: str,
    file_path: str,
    sheet_name: str | None,
    client_id: str,
    target_column: str | None = None,
    date_column: str | None = None,
    reference_dir: str | None = None,
    include_products: bool = False,
    write_report: bool = True,
    report_dir: str | None = None,
    gemini_api_key: str | None = None,
    include_hipaa: bool = False,
) -> None:
    """Submit a run to the background executor. Returns immediately --
    the caller (routes.py) already created the PENDING RunRecord and hands
    the client its run_id right away; this just schedules the work."""
    get_executor().submit(
        _execute,
        run_id=run_id,
        file_path=file_path,
        sheet_name=sheet_name,
        client_id=client_id,
        target_column=target_column,
        date_column=date_column,
        reference_dir=reference_dir,
        include_products=include_products,
        write_report=write_report,
        report_dir=report_dir,
        gemini_api_key=gemini_api_key,
        include_hipaa=include_hipaa,
    )


def uploads_dir() -> Path:
    d = Path(SETTINGS["uploads_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d
