"""
Pydantic schemas for Phase 2.

These are the shapes that cross boundaries — API request/response bodies
(M4), what gets written to/read from the database (M1 tables above), and
what gets handed to the LLM layer (M2). Centralizing them here means every
layer validates the same way instead of each module inventing its own
rules for "what's a valid client_id".
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CLIENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{2,64}$")


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DispositionType(str, Enum):
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    FALSE_POSITIVE = "false_positive"
    NEEDS_REVIEW = "needs_review"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# --------------------------------------------------------------------------
# Shared field validators, reused via mixins
# --------------------------------------------------------------------------

class ClientScopedModel(BaseModel):
    """Mixin for any schema that carries a client_id, so the validation
    rule for what makes a valid client_id lives in exactly one place."""

    client_id: str

    @field_validator("client_id")
    @classmethod
    def _validate_client_id(cls, value: str) -> str:
        if not _CLIENT_ID_PATTERN.match(value):
            raise ValueError(
                "client_id must be 2-64 characters, letters/digits/underscore/hyphen only"
            )
        return value


class FileNamedModel(BaseModel):
    file_name: str

    @field_validator("file_name")
    @classmethod
    def _validate_file_name(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value:
            raise ValueError("file_name must be a plain file name, no path separators")
        allowed_ext = (".xlsx", ".xls", ".xlsm", ".csv")
        if not value.lower().endswith(allowed_ext):
            raise ValueError(f"file_name must end with one of {allowed_ext}")
        return value


# --------------------------------------------------------------------------
# Rule resolution schemas
# --------------------------------------------------------------------------

class BusinessRule(BaseModel):
    rule_id: str
    description: str
    condition: str = Field(..., description="Human-readable or DSL condition string")
    severity: Severity = Severity.MEDIUM


class RuleSet(BaseModel):
    client_id: str
    version: str
    thresholds: dict[str, float] = Field(default_factory=dict)
    business_rules: list[BusinessRule] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")  # future threshold keys shouldn't break validation


# --------------------------------------------------------------------------
# Run lifecycle schemas
# --------------------------------------------------------------------------

class RunCreateRequest(ClientScopedModel, FileNamedModel):
    sheet_name: str | None = None


class RunRecordSchema(BaseModel):
    id: str
    client_id: str
    file_name: str
    sheet_name: str | None = None
    status: RunStatus
    ruleset_version: str | None = None
    rows_processed: int | None = None
    cols_processed: int | None = None
    overall_score: float | None = None
    dimension_scores: dict[str, float] | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)  # lets this read straight from the ORM row


class RunManifestSchema(BaseModel):
    run_id: str
    client_id: str
    file_name: str
    ruleset_version: str | None
    checks_run: list[str]
    status: str
    started_at: str
    completed_at: str | None = None
    environment: str = "development"
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Finding schemas (bridge between Phase 1's CheckResult and everything else)
# --------------------------------------------------------------------------

class FindingSummary(BaseModel):
    """A trimmed-down, JSON-safe view of a Phase 1 CheckResult."""

    check_name: str
    column: str | None
    status: str
    issues_found: int
    dimension: str = ""
    quality_ratio: float | None = None

    @field_validator("quality_ratio")
    @classmethod
    def _validate_ratio(cls, value: float | None) -> float | None:
        if value is not None and not (0.0 <= value <= 1.0):
            raise ValueError("quality_ratio must be between 0.0 and 1.0")
        return value


# --------------------------------------------------------------------------
# Entity resolution schemas (tables exist now, used starting M6)
# --------------------------------------------------------------------------

class CanonicalMappingCreate(ClientScopedModel):
    column_role: str
    source_value: str
    canonical_value: str
    confidence: float = 1.0
    source: str = "manual"

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value


class CanonicalMappingSchema(CanonicalMappingCreate):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Feedback schemas (tables exist now, used starting M7)
# --------------------------------------------------------------------------

class DispositionCreate(BaseModel):
    run_id: str
    finding_id: str
    disposition: DispositionType
    note: str | None = None
    created_by: str | None = None


class DispositionSchema(DispositionCreate):
    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RatingCreate(BaseModel):
    run_id: str
    finding_id: str
    rating: int
    comment: str | None = None
    created_by: str | None = None

    @field_validator("rating")
    @classmethod
    def _validate_rating(cls, value: int) -> int:
        if not (1 <= value <= 5):
            raise ValueError("rating must be between 1 and 5")
        return value


class RatingSchema(RatingCreate):
    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Misc / operational schemas
# --------------------------------------------------------------------------

class HealthCheckResponse(BaseModel):
    status: str = "ok"
    database: str = "connected"
    details: dict[str, Any] = Field(default_factory=dict)
