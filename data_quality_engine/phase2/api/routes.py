"""
Phase 2 -- M4: HTTP endpoints.

Every endpoint here is generic across clients and datasets: nothing in
this file references a specific column, sheet name, or file layout.
client_id and file_name are the only per-request identity; everything
about *what's inside* the file is Phase 1/M2/M3's job, not this layer's.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text as sql_text

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.phase2 import get_rule_resolver
from data_quality_engine.phase2.api import jobs
from data_quality_engine.phase2.api.auth import require_client_access, resolve_api_key
from data_quality_engine.phase2.api.schemas import (
    ClientRuleSetResponse,
    FileUploadResponse,
    RuleDryRunResponse,
    RuleSaveRequest,
    RuleSaveResponse,
    RunListResponse,
    RunResultsResponse,
    RunStatusResponse,
    RunSummary,
    SheetResult,
)
from data_quality_engine.phase2.database import get_session
from data_quality_engine.phase2.database.models import (
    RunManifest,
    RunRecord,
    RunStatus,
)
from data_quality_engine.phase2.rules import RuleResolutionError
from data_quality_engine.phase2.schemas.models import (
    ClientScopedModel,
    FileNamedModel,
)

router = APIRouter()

_ALLOWED_EXTENSIONS = (".xlsx", ".xls", ".xlsm", ".csv")
_MAX_UPLOAD_BYTES = int(SETTINGS.get("max_file_size_mb", 200)) * 1024 * 1024


def _validate_client_id(client_id: str) -> str:
    """Reuse the exact same rule M1 already defined for client_id, instead
    of a second, possibly-drifting regex living in the HTTP layer."""
    try:
        ClientScopedModel(client_id=client_id)
    except Exception as exc:  # noqa: BLE001 - surfaced as a 422 below
        raise HTTPException(422, f"Invalid client_id: {exc}") from exc
    return client_id


def _validate_file_name(file_name: str) -> str:
    try:
        FileNamedModel(file_name=file_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Invalid file_name: {exc}") from exc
    return file_name


@router.get("/health")
def health_check() -> dict[str, Any]:
    """Liveness/readiness probe. Actually touches the database instead of
    trusting that init_db() succeeded once at startup, so this reflects
    the DB's *current* reachability."""
    db_status = "connected"
    try:
        with get_session() as session:
            session.execute(sql_text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = f"unreachable: {exc}"
    return {"status": "ok" if db_status == "connected" else "degraded", "database": db_status}


@router.post("/v1/files/upload", response_model=FileUploadResponse, status_code=202)
async def upload_file(
    client_id: str = Query(..., description="Which client this run belongs to"),
    authenticated_client_id: str = Depends(resolve_api_key),
    file: UploadFile = File(...),
    sheet_name: str | None = Query(
        None, description="Optional: process only this sheet (default: all visible sheets)"
    ),
    target_column: str | None = Query(
        None, description="Optional, paired with date_column: enables M3 ML readiness"
    ),
    date_column: str | None = Query(
        None, description="Optional, paired with target_column: enables M3 ML readiness"
    ),
    write_report: bool = Query(True, description="Generate the AI-enhanced HTML report"),
    gemini_api_key: str | None = Query(
        None, description="Optional: overrides the GEMINI_API_KEY env var for this run's report"
    ),
) -> FileUploadResponse:
    """
    Upload a file and start processing it in the background.

    Accepts .xlsx/.xls/.xlsm/.csv, any layout, any client -- header
    detection, column classification, and every check downstream is
    already dataset-agnostic (see engine/ingestion.py, column_classifier.py).
    This endpoint's only job is: validate the envelope (extension, size),
    persist the bytes, create a PENDING run record, and hand off to the
    background executor. Poll GET /v1/runs/{run_id}/status for progress.
    """
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)

    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSIONS):
        raise HTTPException(
            400, f"Only {', '.join(_ALLOWED_EXTENSIONS)} files are accepted."
        )
    if (target_column is None) != (date_column is None):
        raise HTTPException(
            400, "target_column and date_column must be supplied together, or not at all."
        )

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit."
        )
    if not contents:
        raise HTTPException(400, "Uploaded file is empty.")

    run_id = uuid.uuid4().hex
    dest_dir = jobs.uploads_dir() / run_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file.filename
    dest_path.write_bytes(contents)

    with get_session() as session:
        run = RunRecord(
            id=run_id,
            client_id=client_id,
            file_name=file.filename,
            sheet_name=sheet_name,
            status=RunStatus.PENDING,
        )
        session.add(run)
        session.flush()
        created_at = run.started_at

    jobs.enqueue_run(
        run_id=run_id,
        file_path=str(dest_path),
        sheet_name=sheet_name,
        client_id=client_id,
        target_column=target_column,
        date_column=date_column,
        write_report=write_report,
        gemini_api_key=gemini_api_key,
    )

    return FileUploadResponse(
        run_id=run_id,
        status=RunStatus.PENDING,
        client_id=client_id,
        file_name=file.filename,
        sheet_name=sheet_name,
        created_at=created_at,
    )


def _get_run_or_404(run_id: str) -> RunRecord:
    with get_session() as session:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise HTTPException(404, f"Run '{run_id}' not found.")
        session.expunge(run)
        return run


@router.get("/v1/runs/{run_id}/status", response_model=RunStatusResponse)
def get_run_status(
    run_id: str, authenticated_client_id: str = Depends(resolve_api_key)
) -> RunStatusResponse:
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)
    return RunStatusResponse.model_validate(run)


@router.get("/v1/runs/{run_id}/results", response_model=RunResultsResponse)
def get_run_results(
    run_id: str, authenticated_client_id: str = Depends(resolve_api_key)
) -> RunResultsResponse:
    """
    Full results for a run, whatever state it's in:
      - PENDING/RUNNING: scores are null, sheets is empty -- poll status first
      - FAILED: error_message explains why, sheets may still show partial
        per-sheet detail if some sheets succeeded before the run overall
        failed to score
      - COMPLETED: overall_score + per-sheet breakdown, including each
        sheet's report_path if write_report was requested
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    sheets: list[SheetResult] = []
    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        if manifest is not None:
            for entry in (manifest.extra or {}).get("sheets", []):
                sheets.append(SheetResult(**entry))

    return RunResultsResponse(
        run_id=run.id,
        status=run.status,
        client_id=run.client_id,
        file_name=run.file_name,
        overall_score=run.overall_score,
        rows_processed=run.rows_processed,
        cols_processed=run.cols_processed,
        dimension_scores=run.dimension_scores,
        sheets=sheets,
        error_message=run.error_message,
    )


@router.get("/v1/runs/{run_id}/report")
def get_run_report(
    run_id: str,
    sheet_name: str | None = None,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> FileResponse:
    """
    Download the generated HTML report for a completed run.

    sheet_name selects which sheet's report to return when a file had
    several; omit it to get the first available report. Any file/client
    works identically here -- report_path came straight from what
    run_pipeline() actually wrote, never reconstructed from a naming
    convention this endpoint assumes.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(409, f"Run is '{run.status.value}', not completed yet.")

    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        sheet_entries = (manifest.extra or {}).get("sheets", []) if manifest else []

    candidates = [
        s for s in sheet_entries
        if s.get("report_path") and (sheet_name is None or s.get("sheet_name") == sheet_name)
    ]
    if not candidates:
        raise HTTPException(
            404,
            "No report available for this run"
            + (f" / sheet '{sheet_name}'" if sheet_name else "")
            + ". Was write_report=True passed at upload time?",
        )

    report_path = Path(candidates[0]["report_path"])
    if not report_path.exists():
        raise HTTPException(410, f"Report file no longer exists on disk: {report_path}")

    return FileResponse(report_path, media_type="text/html", filename=report_path.name)


@router.get("/v1/runs/{run_id}/report/pdf")
def get_run_report_pdf(
    run_id: str,
    sheet_name: str | None = None,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> FileResponse:
    """
    Download the generated PDF report for a completed run.

    Same lookup as get_run_report above, just keyed on `pdf_report_path`
    instead of `report_path` -- the PDF is a best-effort sibling output of
    the HTML report (see main.py's _write_ai_enhanced_report), so a sheet
    can have an HTML report with no PDF if fpdf2 rendering failed for it.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(409, f"Run is '{run.status.value}', not completed yet.")

    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        sheet_entries = (manifest.extra or {}).get("sheets", []) if manifest else []

    candidates = [
        s for s in sheet_entries
        if s.get("pdf_report_path") and (sheet_name is None or s.get("sheet_name") == sheet_name)
    ]
    if not candidates:
        raise HTTPException(
            404,
            "No PDF report available for this run"
            + (f" / sheet '{sheet_name}'" if sheet_name else "")
            + ". Was write_report=True passed at upload time?",
        )

    pdf_path = Path(candidates[0]["pdf_report_path"])
    if not pdf_path.exists():
        raise HTTPException(410, f"PDF report file no longer exists on disk: {pdf_path}")

    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)


# ---------------------------------------------------------------------------
# Client rules management (PHASE2_PLAN.md §4.6)
# ---------------------------------------------------------------------------

@router.get("/v1/clients/{client_id}/rules", response_model=ClientRuleSetResponse)
def get_active_rules(
    client_id: str, authenticated_client_id: str = Depends(resolve_api_key)
) -> ClientRuleSetResponse:
    """Effective ruleset for a client right now: base_rules.yaml merged
    with that client's highest-version override, if any exists."""
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)
    resolver = get_rule_resolver()
    ruleset = resolver.resolve(client_id)
    return ClientRuleSetResponse(**ruleset)


@router.post("/v1/clients/{client_id}/rules/dry-run", response_model=RuleDryRunResponse)
def dry_run_rules(
    client_id: str,
    body: RuleSaveRequest,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> RuleDryRunResponse:
    """
    Validate a candidate ruleset (raw YAML text) without saving it.
    Never a 4xx/5xx for a syntactically-or-structurally invalid
    candidate -- that's an expected, everyday result here, returned as
    `{"valid": false, "error": "..."}` in a normal 200 response, exactly
    per PHASE2_PLAN.md §4.6's example response shape.
    """
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)
    resolver = get_rule_resolver()
    result = resolver.dry_run(client_id, body.rules_yaml)
    return RuleDryRunResponse(
        valid=result.valid,
        error=result.error,
        thresholds=result.thresholds,
        business_rules=result.business_rules,
        resolved=result.resolved if result.valid else None,
    )


@router.post("/v1/clients/{client_id}/rules", response_model=RuleSaveResponse, status_code=201)
def save_client_rules(
    client_id: str,
    body: RuleSaveRequest,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> RuleSaveResponse:
    """
    Save a new versioned ruleset for a client:
    config/clients/<client_id>/rules_v{N+1}.yaml. Never overwrites an
    existing version -- every save is a new, independently auditable
    file. Invalid YAML/structure is rejected with 422 (same validation
    dry-run uses, so a client that dry-runs successfully is guaranteed
    to save successfully too).
    """
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)
    resolver = get_rule_resolver()
    try:
        version, path = resolver.save_client_ruleset(client_id, body.rules_yaml)
    except RuleResolutionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RuleSaveResponse(client_id=client_id, version=version, path=str(path))
# --- FRONTEND PATCH: two small edits to data_quality_engine/phase2/api/routes.py ---
#
# 1) Add RunSummary + RunListResponse to the existing import block near the
#    top of the file, i.e. change:
#
#        from data_quality_engine.phase2.api.schemas import (
#            ClientRuleSetResponse,
#            FileUploadResponse,
#            RuleDryRunResponse,
#            RuleSaveRequest,
#            RuleSaveResponse,
#            RunResultsResponse,
#            RunStatusResponse,
#            SheetResult,
#        )
#
#    to:
#
#        from data_quality_engine.phase2.api.schemas import (
#            ClientRuleSetResponse,
#            FileUploadResponse,
#            RuleDryRunResponse,
#            RuleSaveRequest,
#            RuleSaveResponse,
#            RunListResponse,
#            RunResultsResponse,
#            RunStatusResponse,
#            RunSummary,
#            SheetResult,
#        )
#
# 2) Paste the endpoint below anywhere after upload_file() and before
#    _get_run_or_404() (or anywhere else at module level -- order doesn't
#    matter to FastAPI, this placement just keeps upload/list/status/results
#    grouped together).


@router.get("/v1/clients/{client_id}/runs", response_model=RunListResponse)
def list_client_runs(
    client_id: str,
    limit: int = Query(50, ge=1, le=200),
    authenticated_client_id: str = Depends(resolve_api_key),
) -> RunListResponse:
    """
    Most-recent-first run history for a client. Powers the frontend's
    "Runs" list -- without this, a browser session has no way to
    rediscover past run_ids after a refresh (upload_file() is the only
    place a run_id is ever handed out).
    """
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)
    with get_session() as session:
        runs = (
            session.query(RunRecord)
            .filter(RunRecord.client_id == client_id)
            .order_by(RunRecord.started_at.desc())
            .limit(limit)
            .all()
        )
        summaries = [RunSummary.model_validate(r) for r in runs]
    return RunListResponse(client_id=client_id, runs=summaries)
