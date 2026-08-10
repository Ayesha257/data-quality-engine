"""
Phase 2 -- M4: /v1/auth/* endpoints (signup, login, current-user).

Add to app.py:

    from data_quality_engine.phase2.api.auth_routes import auth_router
    app.include_router(auth_router)

These three endpoints are the ENTIRE new login surface. Nothing else
about authorization changed: every existing endpoint in routes.py still
depends on `resolve_api_key` (now JWT-based, see auth.py) and still calls
`require_client_access` exactly as before.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from data_quality_engine.phase2.api.auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    register_user,
)
from data_quality_engine.phase2.api.auth_schemas import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    RegisterRequest,
)
from data_quality_engine.phase2.database.models import User

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


@auth_router.post("/register", response_model=AuthResponse, status_code=201)
def register(payload: RegisterRequest) -> AuthResponse:
    user = register_user(payload.email, payload.password, full_name=payload.full_name)
    token = create_access_token(user_id=user.id, email=user.email, client_id=user.client_id)
    return AuthResponse(
        access_token=token,
        client_id=user.client_id,
        email=user.email,
        full_name=user.full_name,
    )


@auth_router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest) -> AuthResponse:
    user = authenticate_user(payload.email, payload.password)
    token = create_access_token(user_id=user.id, email=user.email, client_id=user.client_id)
    return AuthResponse(
        access_token=token,
        client_id=user.client_id,
        email=user.email,
        full_name=user.full_name,
    )


@auth_router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=user.id, email=user.email, full_name=user.full_name, client_id=user.client_id)
