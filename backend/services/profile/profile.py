"""
Profile service — account read/update and usage stats.

Routes live in backend/routes/profileroutes.py; schemas in
backend/schemas/profileschema.py. Auth (JWT, password hashing) stays in
backend/services/auth/auth.py.
"""

from __future__ import annotations

from sqlalchemy import func, select

from backend.database import get_session
from backend.database.models import RunRecord, RunStatus, User
from backend.services.auth.auth import AuthError, hash_password, verify_password


def user_to_profile_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "client_id": user.client_id,
        "created_at": user.created_at,
    }


def get_profile(user_id: str) -> dict:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise AuthError(404, "User not found.")
        return user_to_profile_dict(user)


def update_profile(user_id: str, *, full_name: str | None) -> dict:
    normalized = (full_name or "").strip() or None
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise AuthError(404, "User not found.")
        user.full_name = normalized
        session.commit()
        session.refresh(user)
        return user_to_profile_dict(user)


def change_password(user_id: str, *, current_password: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise AuthError(422, "Password must be at least 8 characters.")
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise AuthError(404, "User not found.")
        if not verify_password(current_password, user.password_hash):
            raise AuthError(401, "Current password is incorrect.")
        user.password_hash = hash_password(new_password)
        session.commit()


def get_profile_stats(client_id: str) -> dict:
    with get_session() as session:
        total_runs = session.execute(
            select(func.count()).select_from(RunRecord).where(RunRecord.client_id == client_id)
        ).scalar_one()
        completed_runs = session.execute(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.client_id == client_id, RunRecord.status == RunStatus.COMPLETED)
        ).scalar_one()
        failed_runs = session.execute(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.client_id == client_id, RunRecord.status == RunStatus.FAILED)
        ).scalar_one()
        avg_score = session.execute(
            select(func.avg(RunRecord.overall_score)).where(
                RunRecord.client_id == client_id,
                RunRecord.status == RunStatus.COMPLETED,
                RunRecord.overall_score.is_not(None),
            )
        ).scalar_one()

    return {
        "total_runs": int(total_runs or 0),
        "completed_runs": int(completed_runs or 0),
        "failed_runs": int(failed_runs or 0),
        "average_score": round(float(avg_score), 2) if avg_score is not None else None,
    }
