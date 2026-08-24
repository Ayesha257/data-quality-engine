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

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text as sql_text

from backend.config.settings import SETTINGS
from backend import get_rule_resolver
from backend.engine import api_prompt
from backend.services import jobs
from backend.services.auth.auth import require_client_access, resolve_api_key
from backend.schemas.api import (
    ClientRuleSetResponse,
    EntityResolutionAnalyzeResponse,
    EntityResolutionResultsResponse,
    EntityResolutionSheetResult,
    FileUploadResponse,
    RuleDryRunResponse,
    RuleSaveRequest,
    RuleSaveResponse,
    RunConfirmRequest,
    RunConfirmResponse,
    RunListResponse,
    RunResultsResponse,
    RunStatusResponse,
    RunSummary,
    SheetResult,
)
from backend.database import get_session
from backend.database.models import (
    RunManifest,
    RunRecord,
    RunStatus,
)
from backend.services.rules import RuleResolutionError
from backend.schemas.models import (
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
    include_hipaa: bool = Query(
        False,
        description="Include the HIPAA PHI compliance section in the main report "
        "(default: False). The standalone compliance report "
        "(GET /v1/runs/{run_id}/compliance-report) is unaffected by this flag "
        "-- it always reflects the HIPAA analysis for this run.",
    ),
    gemini_api_key: str | None = Query(
        None, description="Optional: overrides the GEMINI_API_KEY env var for this run's report"
    ),
    interactive: bool = Query(
        False,
        description="If true, pause and wait for a human to confirm each sheet's "
        "detected header row via POST /v1/runs/{run_id}/confirm instead of "
        "auto-accepting it (default: False, same behavior as before).",
    ),
) -> FileUploadResponse:
    """
    Upload a file and start processing it in the background.

    Accepts .xlsx/.xls/.xlsm/.csv, any layout, any client -- header
    detection, column classification, and every check downstream is
    already dataset-agnostic (see engine/ingestion.py, column_classifier.py).
    This endpoint's only job is: validate the envelope (extension, size),
    persist the bytes, create a PENDING run record, and hand off to the
    background executor. Poll GET /v1/runs/{run_id}/status for progress --
    when interactive=true, status can pause at "awaiting_confirmation";
    see get_run_status()'s and confirm_run()'s docstrings.
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
        include_hipaa=include_hipaa,
        interactive=interactive,
    )

    return FileUploadResponse(
        run_id=run_id,
        status=RunStatus.PENDING,
        client_id=client_id,
        file_name=file.filename,
        sheet_name=sheet_name,
        created_at=created_at,
    )


@router.post(
    "/v1/analyze/entity-resolution",
    response_model=EntityResolutionAnalyzeResponse,
    status_code=202,
)
async def analyze_entity_resolution(
    client_id: str = Query(..., description="Which client this run belongs to"),
    authenticated_client_id: str = Depends(resolve_api_key),
    file: UploadFile = File(...),
    sheet_name: str | None = Query(
        None, description="Optional: process only this sheet (default: all visible sheets)"
    ),
    gemini_api_key: str | None = Query(
        None, description="Optional: overrides the GEMINI_API_KEY env var for this run's report"
    ),
) -> EntityResolutionAnalyzeResponse:
    """
    Upload a file and start a full pipeline run with entity resolution (M6).

    Same background execution model as POST /v1/files/upload, but this
    endpoint always generates the AI-enhanced HTML report (including the
    M6 section) and is the dedicated entry point for entity-resolution
    analysis. Poll GET /v1/entity-resolution/results/{run_id} for M6
    payloads once the run completes.
    """
    _validate_client_id(client_id)
    require_client_access(authenticated_client_id, client_id)

    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXTENSIONS):
        raise HTTPException(
            400, f"Only {', '.join(_ALLOWED_EXTENSIONS)} files are accepted."
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
        write_report=True,
        gemini_api_key=gemini_api_key,
    )

    return EntityResolutionAnalyzeResponse(
        run_id=run_id,
        status=RunStatus.PENDING,
        client_id=client_id,
        file_name=file.filename,
        sheet_name=sheet_name,
        created_at=created_at,
    )


def _entity_resolution_from_manifest(manifest: RunManifest | None) -> list[EntityResolutionSheetResult]:
    sheets: list[EntityResolutionSheetResult] = []
    for entry in (manifest.extra or {}).get("sheets", []) if manifest else []:
        er = entry.get("entity_resolution") or {}
        sheets.append(
            EntityResolutionSheetResult(
                sheet_name=entry.get("sheet_name", ""),
                enabled=bool(er.get("enabled")),
                summary=er.get("summary") or {},
                columns=er.get("columns") or {},
                review_queue=er.get("review_queue") or [],
                entity_resolution_auto=entry.get("entity_resolution_auto"),
                entity_resolution_review=entry.get("entity_resolution_review"),
                entity_resolution_no_match=entry.get("entity_resolution_no_match"),
                error=entry.get("error"),
            )
        )
    return sheets


@router.get(
    "/v1/entity-resolution/results/{run_id}",
    response_model=EntityResolutionResultsResponse,
)
def get_entity_resolution_results(
    run_id: str, authenticated_client_id: str = Depends(resolve_api_key)
) -> EntityResolutionResultsResponse:
    """Return entity-resolution (M6) payloads for a run from its manifest."""
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        sheets = _entity_resolution_from_manifest(manifest)

    return EntityResolutionResultsResponse(
        run_id=run.id,
        status=run.status,
        client_id=run.client_id,
        file_name=run.file_name,
        sheets=sheets,
        error_message=run.error_message,
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


@router.post("/v1/runs/{run_id}/confirm", response_model=RunConfirmResponse)
def confirm_run(
    run_id: str,
    body: RunConfirmRequest,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> RunConfirmResponse:
    """
    Answer the checkpoint currently sitting in
    GET /v1/runs/{run_id}/status's pending_confirmation field.

    Only valid while the run's status is "awaiting_confirmation" (i.e. it
    was started with interactive=true and is paused at a sheet's
    header-row checkpoint -- see engine/api_prompt.py). Wakes the paused
    background thread, which resumes the pipeline for that sheet and, for
    a multi-sheet file, may pause again at the next sheet's checkpoint.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    if run.status != RunStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            409,
            f"Run '{run_id}' is not awaiting confirmation (status={run.status.value}).",
        )
    if not body.accept and body.override_header_row is None:
        raise HTTPException(
            422, "override_header_row is required when accept is false."
        )

    resolved = api_prompt.submit_answer(
        run_id,
        {"accept": body.accept, "override_header_row": body.override_header_row},
    )
    if not resolved:
        # The checkpoint's wait already timed out (or this run never
        # actually paused) between the status the client saw and this
        # request landing -- report that plainly rather than pretending
        # the answer was applied.
        raise HTTPException(
            409,
            f"Run '{run_id}' is no longer waiting on a confirmation "
            "(it may have timed out or already been answered).",
        )

    with get_session() as session:
        run = session.get(RunRecord, run_id)
        # APIPrompt flips this back to RUNNING itself once its wait()
        # unblocks, but set it here too so the response the caller gets
        # back reflects the resumed state immediately rather than a
        # possible one-tick-stale AWAITING_CONFIRMATION if they poll
        # status before the worker thread has woken up.
        if run is not None and run.status == RunStatus.AWAITING_CONFIRMATION:
            run.status = RunStatus.RUNNING
        status = run.status if run is not None else RunStatus.RUNNING

    return RunConfirmResponse(run_id=run_id, status=status)


@router.delete("/v1/runs/{run_id}", status_code=204)
def delete_run(
    run_id: str, authenticated_client_id: str = Depends(resolve_api_key)
) -> Response:
    """
    Delete a run:
      - Authenticate and ensure client access (404 if missing, 403 if wrong client).
      - Collect file paths from RunManifest (uploaded file, report_path, compliance_report_path, pdf_report_path).
      - Remove RunRecord and cascade to child rows (RunManifest, Disposition, Rating).
      - Delete uploaded file and all report HTML/PDF files from disk safely (missing files never 500).
      - Return 204 No Content.
    """
    import shutil

    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    files_to_delete: list[Path] = []
    upload_dir = jobs.uploads_dir() / run_id
    if upload_dir.exists():
        files_to_delete.append(upload_dir)

    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        if manifest is not None:
            for s in (manifest.extra or {}).get("sheets", []):
                for key in ("report_path", "compliance_report_path", "pdf_report_path"):
                    p = s.get(key)
                    if p:
                        files_to_delete.append(Path(p))

        run_in_db = session.get(RunRecord, run_id)
        if run_in_db is not None:
            session.delete(run_in_db)

    for path in files_to_delete:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - missing files must never cause a 500
            pass

    return Response(status_code=204)


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


@router.get("/v1/runs/{run_id}/compliance-report")
def get_run_compliance_report(
    run_id: str,
    sheet_name: str | None = None,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> FileResponse:
    """
    Download the standalone Compliance Report for a completed run.

    This is a separate report from GET /v1/runs/{run_id}/report -- it
    contains compliance findings only (currently HIPAA; structured to grow
    additional regulation sections later, see
    engine/compliance/report.py), regardless of whether the main report's
    own include_hipaa flag was used to include or omit its HIPAA section
    at upload time. Same lookup/auth/scoping pattern as get_run_report,
    just keyed on `compliance_report_path`.
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
        if s.get("compliance_report_path")
        and (sheet_name is None or s.get("sheet_name") == sheet_name)
    ]
    if not candidates:
        raise HTTPException(
            404,
            "No compliance report available for this run"
            + (f" / sheet '{sheet_name}'" if sheet_name else "")
            + ". Was write_report=True passed at upload time?",
        )

    report_path = Path(candidates[0]["compliance_report_path"])
    if not report_path.exists():
        raise HTTPException(410, f"Compliance report file no longer exists on disk: {report_path}")

    return FileResponse(report_path, media_type="text/html", filename=report_path.name)


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
# --- FRONTEND PATCH: two small edits to backend/phase2/api/routes.py ---
#
# 1) Add RunSummary + RunListResponse to the existing import block near the
#    top of the file, i.e. change:
#
#        from backend.schemas.api import (
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
#        from backend.schemas.api import (
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
        summaries = []
        for r in runs:
            manifest = session.query(RunManifest).filter(RunManifest.run_id == r.id).one_or_none()
            has_comp = False
            if manifest and manifest.extra:
                sheets = manifest.extra.get("sheets", [])
                has_comp = any(bool(s.get("compliance_report_path")) for s in sheets if isinstance(s, dict))
            s_obj = RunSummary.model_validate(r)
            s_obj.has_compliance_report = has_comp
            summaries.append(s_obj)
    return RunListResponse(client_id=client_id, runs=summaries)
