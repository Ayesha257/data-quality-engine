"""
Phase 2 -- M4: HTTP endpoints.

Every endpoint here is generic across clients and datasets: nothing in
this file references a specific column, sheet name, or file layout.
client_id and file_name are the only per-request identity; everything
about *what's inside* the file is Phase 1/M2/M3's job, not this layer's.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
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
    ComplianceConfirmRequest,
    ComplianceConfirmResponse,
    ComplianceDecision,
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
    request: Request,
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
    compliance_modules: list[str] | None = Query(
        None,
        description="Opt-in compliance detectors to run: HIPAA, PCI_DSS, GLBA, SOX. "
        "Default is none — detectors do not run unless listed.",
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

    from backend.engine.compliance.opt_in import normalize_compliance_modules

    raw_modules: list[str] = []
    if compliance_modules:
        if isinstance(compliance_modules, (list, tuple, set)):
            raw_modules.extend(str(m) for m in compliance_modules if m is not None)
        else:
            raw_modules.append(str(compliance_modules))

    for qk, qv in request.query_params.multi_items():
        if qk in ("compliance_modules", "compliance_modules[]") or qk.startswith("compliance_modules["):
            if qv:
                raw_modules.append(qv)

    selected_modules = normalize_compliance_modules(
        raw_modules, include_hipaa=include_hipaa
    )

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
        compliance_modules=selected_modules,
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
    body: RunConfirmRequest | dict[str, Any],
    authenticated_client_id: str = Depends(resolve_api_key),
) -> RunConfirmResponse:
    """
    Answer the checkpoint currently sitting in
    GET /v1/runs/{run_id}/status's pending_confirmation field.

    Only valid while the run's status is "awaiting_confirmation" (i.e. it
    was started with interactive=true and is paused at a sheet's
    header-row checkpoint or compliance checkpoint -- see engine/api_prompt.py).
    Wakes the paused background thread.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    if run.status != RunStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            409,
            f"Run '{run_id}' is not awaiting confirmation (status={run.status.value}).",
        )

    # Check if pending confirmation is compliance-related
    pending = run.pending_confirmation or {}
    prompt_type = pending.get("prompt_type") or pending.get("type")

    if prompt_type in ("COMPLIANCE_COLUMN_CONFIRM", "compliance_column"):
        decisions = []
        if isinstance(body, dict):
            decisions = body.get("decisions", [])
        elif hasattr(body, "decisions"):
            decisions = getattr(body, "decisions")
        elif hasattr(body, "accept"):
            decisions = [{"accept": body.accept}]
        resolved = api_prompt.submit_answer(run_id, {"decisions": decisions})
    else:
        accept = body.accept if hasattr(body, "accept") else body.get("accept", True)
        override_header_row = (
            body.override_header_row
            if hasattr(body, "override_header_row")
            else body.get("override_header_row")
        )
        if not accept and override_header_row is None:
            raise HTTPException(
                422, "override_header_row is required when accept is false."
            )
        resolved = api_prompt.submit_answer(
            run_id,
            {"accept": accept, "override_header_row": override_header_row},
        )

    if not resolved:
        raise HTTPException(
            409,
            f"Run '{run_id}' is no longer waiting on a confirmation "
            "(it may have timed out or already been answered).",
        )

    with get_session() as session:
        run = session.get(RunRecord, run_id)
        if run is not None and run.status == RunStatus.AWAITING_CONFIRMATION:
            run.status = RunStatus.RUNNING
        status = run.status if run is not None else RunStatus.RUNNING

    return RunConfirmResponse(run_id=run_id, status=status)


@router.post("/v1/runs/{run_id}/compliance-confirm", response_model=ComplianceConfirmResponse)
@router.post("/v1/runs/{run_id}/compliance/confirm", response_model=ComplianceConfirmResponse)
def confirm_compliance_run(
    run_id: str,
    body: ComplianceConfirmRequest | list[dict[str, Any]] | dict[str, Any],
    authenticated_client_id: str = Depends(resolve_api_key),
) -> ComplianceConfirmResponse:
    """
    Submit confirm/reject decisions for pending low-confidence compliance findings
    (prompt_type="COMPLIANCE_COLUMN_CONFIRM").
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    if run.status != RunStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            409,
            f"Run '{run_id}' is not awaiting confirmation (status={run.status.value}).",
        )

    decisions: list[Any] = []
    if isinstance(body, list):
        decisions = body
    elif isinstance(body, dict):
        decisions = body.get("decisions", [])
    elif hasattr(body, "decisions"):
        raw = getattr(body, "decisions")
        if isinstance(raw, list):
            decisions = [
                d.model_dump() if hasattr(d, "model_dump") else d for d in raw
            ]
        elif isinstance(raw, dict):
            decisions = [{"column_name": k, "confirmed": v} for k, v in raw.items()]

    resolved = api_prompt.submit_answer(
        run_id,
        {"decisions": decisions},
    )
    if not resolved:
        raise HTTPException(
            409,
            f"Run '{run_id}' is no longer waiting on a confirmation "
            "(it may have timed out or already been answered).",
        )

    # Convert decisions to map: column_name -> confirmed (bool)
    decision_map: dict[str, bool] = {}
    for d in decisions:
        if isinstance(d, dict) and "column_name" in d:
            col = d["column_name"]
            c_val = d.get("confirmed")
            if c_val is None:
                c_val = d.get("accept")
            if c_val is None and "decision" in d:
                c_val = str(d["decision"]).lower() in ("confirm", "confirmed", "accept", "true", "yes")
            decision_map[col] = bool(c_val)

    with get_session() as session:
        run = session.get(RunRecord, run_id)
        if run is not None and run.status == RunStatus.AWAITING_CONFIRMATION:
            run.status = RunStatus.RUNNING
        manifest = session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        if manifest is not None:
            extra = dict(manifest.extra or {})
            prev = extra.get("compliance_decisions", {})
            prev.update(decision_map)
            extra["compliance_decisions"] = prev
            manifest.extra = extra
        else:
            session.add(
                RunManifest(
                    run_id=run_id,
                    checks_run=[],
                    ruleset_snapshot={},
                    extra={"compliance_decisions": decision_map},
                )
            )
        status = run.status if run is not None else RunStatus.RUNNING

    return ComplianceConfirmResponse(
        run_id=run_id,
        status=status,
        resolved_count=len(decisions),
    )


@router.get("/v1/runs/{run_id}/compliance-confirmations")
@router.get("/v1/runs/{run_id}/compliance/pending")
def get_compliance_confirmations(
    run_id: str,
    authenticated_client_id: str = Depends(resolve_api_key),
) -> dict[str, Any]:
    """
    Fetch any pending compliance HITL confirmations for the current run.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)

    if run.status != RunStatus.AWAITING_CONFIRMATION or not run.pending_confirmation:
        return {
            "run_id": run_id,
            "status": run.status.value,
            "pending": False,
            "findings": [],
        }

    pending = run.pending_confirmation
    findings = pending.get("findings", [])
    if not findings and pending.get("column_name"):
        findings = [{
            "column_name": pending.get("column_name"),
            "guessed_field": pending.get("guessed_field"),
            "regulation": pending.get("regulation"),
            "confidence": pending.get("confidence", "low"),
        }]

    return {
        "run_id": run_id,
        "status": run.status.value,
        "pending": True,
        "prompt_type": pending.get("prompt_type", "COMPLIANCE_COLUMN_CONFIRM"),
        "sheet_name": pending.get("sheet_name"),
        "findings": findings,
    }


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

        # Also clean up any regulation compliance reports generated for this file
        stem = Path(run.file_name).stem
        reports_dir = Path(SETTINGS.get("reports_dir", "reports"))
        if reports_dir.exists():
            for f in reports_dir.glob(f"{stem}_*_compliance_report.html"):
                files_to_delete.append(f)

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
    run_modules: list[str] = []
    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        if manifest is not None and manifest.extra:
            run_modules = list(manifest.extra.get("compliance_modules") or [])
            for entry in manifest.extra.get("sheets", []):
                sheets.append(SheetResult(**entry))
                if not run_modules and entry.get("compliance_modules"):
                    run_modules = list(entry["compliance_modules"])

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
        compliance_modules=run_modules,
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
    regulation: str = Query("HIPAA", description="Compliance framework: HIPAA, PCI_DSS, GLBA, or SOX"),
    authenticated_client_id: str = Depends(resolve_api_key),
) -> FileResponse:
    """
    Download the standalone Compliance Report for a completed run.

    Supports regulation parameter: HIPAA (default), PCI_DSS, GLBA, or SOX.
    """
    run = _get_run_or_404(run_id)
    require_client_access(authenticated_client_id, run.client_id)
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(409, f"Run is '{run.status.value}', not completed yet.")

    reg_norm = str(regulation or "HIPAA").upper().replace("-", "_")

    with get_session() as session:
        manifest = (
            session.query(RunManifest).filter(RunManifest.run_id == run_id).one_or_none()
        )
        sheet_entries = (manifest.extra or {}).get("sheets", []) if manifest else []
        compliance_decisions = (manifest.extra or {}).get("compliance_decisions", {}) if manifest else {}
        selected_modules = list((manifest.extra or {}).get("compliance_modules") or [])
        if not selected_modules:
            for s in sheet_entries:
                if isinstance(s, dict) and s.get("compliance_modules"):
                    selected_modules = list(s["compliance_modules"])
                    break

    candidates = [
        s for s in sheet_entries
        if s.get("report_path") and (sheet_name is None or s.get("sheet_name") == sheet_name)
    ]
    if not candidates:
        raise HTTPException(
            404,
            "No compliance report available for this run"
            + (f" / sheet '{sheet_name}'" if sheet_name else "")
            + ". Was write_report=True passed at upload time?",
        )

    if reg_norm not in selected_modules:
        raise HTTPException(
            404,
            f"Compliance module '{reg_norm}' was not selected for this scan. "
            f"Selected: {selected_modules or 'none'}.",
        )

    # Sole writer for compliance HTML. Scan-time generation of a leftover
    # HIPAA-only "{stem}_{sheet}_compliance_report.html" was removed from
    # run_pipeline -- that file had no regulation in the name, so a PCI/GLBA/
    # SOX request produced two reports (stale HIPAA + the requested one).
    from backend.engine.compliance.report import build_compliance_report_data, generate_compliance_html_report

    target_sheet = candidates[0] if candidates else (sheet_entries[0] if sheet_entries else {})
    sname = sheet_name or target_sheet.get("sheet_name", "Sheet1")
    stem = Path(run.file_name).stem
    safe_sheet = "".join(c if c.isalnum() else "_" for c in sname)
    reports_dir = Path(SETTINGS.get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    # Regulation is always in the filename. Never write the unparameterized
    # "{stem}_{sheet}_compliance_report.html" path used by the old pipeline helper.
    reg_report_path = reports_dir / f"{stem}_{safe_sheet}_{reg_norm.lower()}_compliance_report.html"

    stored_scan = (target_sheet.get("compliance_scans") or {}).get(reg_norm) if isinstance(target_sheet, dict) else None

    # --- Cache check -------------------------------------------------
    # This endpoint used to rebuild the report (and, for HIPAA, re-read and
    # re-parse the entire source file from disk) on EVERY GET request, even
    # when nothing about the run had changed since the last time it was
    # generated. That made "opening" an already-viewed report as slow as
    # generating it fresh. A report is only invalidated by two things: the
    # underlying scan data (stored_scan) or the user's HITL confirm/reject
    # decisions (compliance_decisions) -- so we fingerprint both and skip
    # regeneration when the fingerprint on disk still matches.
    cache_meta_path = reg_report_path.with_suffix(reg_report_path.suffix + ".meta.json")
    cache_signature = hashlib.sha256(
        json.dumps(
            {"stored_scan": stored_scan, "decisions": compliance_decisions},
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    if reg_report_path.exists() and cache_meta_path.exists():
        try:
            cached_signature = json.loads(cache_meta_path.read_text(encoding="utf-8")).get("signature")
        except Exception:
            cached_signature = None
        if cached_signature == cache_signature:
            return FileResponse(reg_report_path, media_type="text/html", filename=reg_report_path.name)
    # --- End cache check -----------------------------------------------

    df = None
    upload_dir = jobs.uploads_dir() / run_id
    if not upload_dir.exists():
        alt = jobs.uploads_dir() / run_id.replace("-", "")
        if alt.exists():
            upload_dir = alt

    # Reuse scan-time financial results so GET does not re-run detectors.
    # HIPAA still needs the dataframe (or modules) for its existing builder.
    if not stored_scan or reg_norm == "HIPAA":
        uploaded_files = list(upload_dir.glob("*")) if upload_dir.exists() else []
        if uploaded_files:
            src_file = uploaded_files[0]
            try:
                from backend.engine.ingestion import read_excel_file, load_with_confirmed_header
                import pandas as pd

                if src_file.suffix.lower() == ".csv":
                    df = pd.read_csv(src_file)
                else:
                    raw_sheets = read_excel_file(str(src_file))
                    raw_df = raw_sheets[sname] if sname in raw_sheets else next(iter(raw_sheets.values()))
                    header_row = target_sheet.get("header_row", 0)
                    if header_row is None:
                        header_row = 0
                    df = load_with_confirmed_header(raw_df, header_row)
            except Exception:
                pass

    report_data = build_compliance_report_data(
        filepath=str((upload_dir / run.file_name) if upload_dir.exists() else run.file_name),
        sheet_name=sname,
        row_count=len(df) if df is not None else (run.rows_processed or 0),
        column_count=df.shape[1] if df is not None else (run.cols_processed or 0),
        regulation=reg_norm,
        df=df,
        confidence_tiers=(stored_scan or {}).get("confidence_tiers") if stored_scan else None,
        resolved_findings=(stored_scan or {}).get("resolved_findings") if stored_scan else None,
        resolved_decisions=compliance_decisions,
    )
    generate_compliance_html_report(report_data, str(reg_report_path))
    cache_meta_path.write_text(json.dumps({"signature": cache_signature}), encoding="utf-8")

    return FileResponse(reg_report_path, media_type="text/html", filename=reg_report_path.name)


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
            modules: list[str] = []
            if manifest and manifest.extra:
                sheets = manifest.extra.get("sheets", [])
                modules = list(manifest.extra.get("compliance_modules") or [])
                if not modules:
                    for s in sheets:
                        if isinstance(s, dict) and s.get("compliance_modules"):
                            modules = list(s["compliance_modules"])
                            break
                has_comp = bool(modules) and any(
                    bool(s.get("report_path")) or bool(s.get("compliance_report_path"))
                    for s in sheets
                    if isinstance(s, dict)
                )
            s_obj = RunSummary.model_validate(r)
            s_obj.has_compliance_report = has_comp
            s_obj.compliance_modules = modules
            summaries.append(s_obj)
    return RunListResponse(client_id=client_id, runs=summaries)