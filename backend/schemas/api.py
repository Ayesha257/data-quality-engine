"""
Phase 2 -- M4 REST API request/response schemas.

Deliberately thin: RunCreateRequest, RunRecordSchema, and
HealthCheckResponse already exist in phase2/schemas/models.py (M1) and
are reused as-is below rather than redefined. Only shapes that are
genuinely new to the HTTP layer (upload confirmation, status polling,
sheet-level results) live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.models import RunStatus


class FileUploadResponse(BaseModel):
    run_id: str
    status: RunStatus
    client_id: str
    file_name: str
    sheet_name: str | None = None
    created_at: datetime


class RunStatusResponse(BaseModel):
    run_id: str = Field(validation_alias="id")
    status: RunStatus
    client_id: str
    file_name: str
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    # Set only while status == "awaiting_confirmation". Shape (from
    # engine/api_prompt.py::APIPrompt.confirm()):
    #   {type: "header_row", sheet_name, message, detected_header_row,
    #    headerless, header_values, rows_above, rows_below, note}
    # rows_above/rows_below are each a list of {row_index, values}.
    pending_confirmation: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RunConfirmRequest(BaseModel):
    """Body for POST /v1/runs/{run_id}/confirm -- answers whatever
    checkpoint is currently in RunStatusResponse.pending_confirmation."""

    accept: bool = Field(
        description="True to accept the detected header row as-is. "
        "False to override it with override_header_row."
    )
    override_header_row: int | None = Field(
        default=None,
        description="Required when accept=False: the correct 0-based header "
        "row index, or -1 to load the sheet as headerless.",
    )


class RunConfirmResponse(BaseModel):
    run_id: str
    status: RunStatus


class SheetResult(BaseModel):
    """One entry of run_pipeline()'s per-sheet return value (main.py),
    passed straight through -- see its docstring for field meanings."""

    sheet_name: str
    rows: int | None = None
    columns: int | None = None
    data_quality_score: float | None = None
    privacy_risk_level: str | None = None
    ml_readiness_verdict: str | None = None
    ml_readiness_score: float | None = None
    entity_resolution_auto: int | None = None
    entity_resolution_review: int | None = None
    entity_resolution_no_match: int | None = None
    entity_resolution: dict[str, Any] | None = None
    report_path: str | None = None
    pdf_report_path: str | None = None
    compliance_report_path: str | None = None
    error: str | None = None


class DimensionScore(BaseModel):
    """One entry of the composite score's per-dimension breakdown, as
    produced by engine/scoring.py's `_score()` (see its
    `dimension_scores` dict: {dim: {score, passed, total, skipped,
    errored, weight, available}}). `score` is None when the dimension
    had no results and was excluded from the composite (see
    `available`)."""

    score: float | None = None
    passed: int = 0
    total: int = 0
    skipped: int = 0
    errored: int = 0
    weight: float = 0.0
    available: bool = False


class RunResultsResponse(BaseModel):
    run_id: str
    status: RunStatus
    client_id: str
    file_name: str
    overall_score: float | None = None
    rows_processed: int | None = None
    cols_processed: int | None = None
    dimension_scores: dict[str, DimensionScore] | None = None
    sheets: list[SheetResult] = Field(default_factory=list)
    error_message: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    extra: dict[str, Any] = Field(default_factory=dict)


class ClientRuleSetResponse(BaseModel):
    """Effective, resolved ruleset for a client -- base_rules.yaml merged
    with that client's highest-version override, if any. Mirrors
    phase2.rules.RuleResolver.resolve()'s return shape."""

    client_id: str
    version: str
    thresholds: dict[str, Any] = Field(default_factory=dict)
    business_rules: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class RuleDryRunResponse(BaseModel):
    """PHASE2_PLAN.md §4.6: validate a candidate ruleset without saving
    it. `resolved` is omitted when invalid (nothing meaningful to show)."""

    valid: bool
    error: str | None = None
    thresholds: int = 0
    business_rules: int = 0
    resolved: dict[str, Any] | None = None


class RuleSaveRequest(BaseModel):
    rules_yaml: str = Field(..., description="Raw YAML text for the new client ruleset version")


class RuleSaveResponse(BaseModel):
    client_id: str
    version: int
    path: str

# --- FRONTEND PATCH: paste this at the end of backend/phase2/api/schemas.py ---
#
# Why: the frontend's "Runs" history page needs to list every run for a
# client. Nothing in the current API returns more than one run at a time
# (get_run_status / get_run_results both take a single run_id) -- there's
# no way to enumerate a client's runs at all today, from the browser or
# from curl. This is the smallest addition that closes that gap; it
# reuses RunStatus and the same response conventions as the rest of this
# file rather than inventing new shapes.


class RunSummary(BaseModel):
    """One row in a client's run history -- deliberately lighter than
    RunResultsResponse (no dimension_scores/sheets) since list views
    render many of these at once."""

    run_id: str = Field(validation_alias="id")
    status: RunStatus
    file_name: str
    overall_score: float | None = None
    started_at: datetime
    completed_at: datetime | None = None
    has_compliance_report: bool = False

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RunListResponse(BaseModel):
    client_id: str
    runs: list[RunSummary] = Field(default_factory=list)


class EntityResolutionSheetResult(BaseModel):
    sheet_name: str
    enabled: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    columns: dict[str, Any] = Field(default_factory=dict)
    review_queue: list[dict[str, Any]] = Field(default_factory=list)
    entity_resolution_auto: int | None = None
    entity_resolution_review: int | None = None
    entity_resolution_no_match: int | None = None
    error: str | None = None


class EntityResolutionAnalyzeResponse(BaseModel):
    run_id: str
    status: RunStatus
    client_id: str
    file_name: str
    sheet_name: str | None = None
    created_at: datetime


class EntityResolutionResultsResponse(BaseModel):
    run_id: str
    status: RunStatus
    client_id: str
    file_name: str
    sheets: list[EntityResolutionSheetResult] = Field(default_factory=list)
    error_message: str | None = None
