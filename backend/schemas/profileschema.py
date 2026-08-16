"""Pydantic models for /v1/profile/* endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProfileResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    client_id: str
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(None, description="Display name shown in the app.")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, description="At least 8 characters.")


class ProfileStatsResponse(BaseModel):
    total_runs: int
    completed_runs: int
    failed_runs: int
    average_score: float | None = None
