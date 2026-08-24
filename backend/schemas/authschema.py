"""
Pydantic models for /v1/auth/* endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str = Field(..., description="Your email address -- used to sign in.")
    password: str = Field(..., min_length=8, description="At least 8 characters.")
    full_name: str | None = Field(None, description="Optional display name.")


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    client_id: str
    email: str
    full_name: str | None = None


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    client_id: str
