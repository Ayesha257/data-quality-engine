"""
Phase 2 — Profile endpoints: account details, stats, password change.

Add to app.py:

    from backend.routes.profileroutes import profile_router
    app.include_router(profile_router)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.database.models import User
from backend.schemas.profileschema import (
    ChangePasswordRequest,
    ProfileResponse,
    ProfileStatsResponse,
    ProfileUpdateRequest,
)
from backend.services.auth.auth import get_current_user
from backend.services.profile.profile import (
    change_password,
    get_profile,
    get_profile_stats,
    update_profile,
)

profile_router = APIRouter(prefix="/v1/profile", tags=["profile"])


@profile_router.get("", response_model=ProfileResponse)
def read_profile(user: User = Depends(get_current_user)) -> ProfileResponse:
    data = get_profile(user.id)
    return ProfileResponse(**data)


@profile_router.patch("", response_model=ProfileResponse)
def patch_profile(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
) -> ProfileResponse:
    data = update_profile(user.id, full_name=payload.full_name)
    return ProfileResponse(**data)


@profile_router.post("/change-password", status_code=204)
def post_change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
) -> None:
    change_password(
        user.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )


@profile_router.get("/stats", response_model=ProfileStatsResponse)
def read_profile_stats(user: User = Depends(get_current_user)) -> ProfileStatsResponse:
    data = get_profile_stats(user.client_id)
    return ProfileStatsResponse(**data)
