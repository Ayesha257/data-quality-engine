"""
Phase 2 -- M4: User authentication (signup / login / JWT session).

This REPLACES the old manual "X-API-Key + Client ID" scheme. There is no
admin-issued key anymore. A person creates their own account with an
email + password, and everything downstream (client_id scoping, per-run
authorization) is derived automatically from that account -- nothing
else in the pipeline needs to change because we keep producing the same
`client_id: str` value every other module already expects.

Flow
----
1. POST /v1/auth/register  {email, password}      -> creates a User row,
   client_id is auto-generated from the email (e.g. "jane@acme.com" ->
   "jane_acme_com"), returns an access token immediately (auto-login).
2. POST /v1/auth/login     {email, password}       -> returns an access
   token.
3. Every other protected endpoint takes `Authorization: Bearer <token>`
   instead of `X-API-Key`. The token's `sub` claim is the user's id and
   its `client_id` claim is what resolve_api_key() returns -- so
   `require_client_access()` and every route in routes.py keep working
   completely unchanged.
4. GET /v1/auth/me returns the current user's profile (used by the
   frontend on page load / refresh to restore the session).

Passwords are hashed with bcrypt (via passlib) -- never stored or logged
in plaintext. Tokens are signed JWTs (HS256) using DQE_JWT_SECRET; if
that env var is unset, a random secret is generated at process startup
(fine for local/dev -- it just means restarting the server invalidates
existing sessions, which is safer than a hardcoded default secret).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException
from passlib.context import CryptContext
from sqlalchemy import select

from backend.database import get_session
from backend.database.models import User

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logger = logging.getLogger("dqe.phase2.api.auth")

ADMIN_SCOPE = "*"  # kept for backwards-compat with require_client_access() callers

_JWT_ALGO = "HS256"
_JWT_SECRET = os.environ.get("DQE_JWT_SECRET") or secrets.token_urlsafe(48)
_ACCESS_TOKEN_TTL = timedelta(hours=int(os.environ.get("DQE_JWT_TTL_HOURS", "24") or 24))

# Workaround for passlib 1.7.4 compatibility with bcrypt 4.x/5.x
try:
    import bcrypt
    _orig_hashpw = bcrypt.hashpw
    def _safe_hashpw(password, salt):
        if isinstance(password, bytes) and len(password) > 72:
            password = password[:72]
        return _orig_hashpw(password, salt)
    bcrypt.hashpw = _safe_hashpw
except Exception:
    pass

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(HTTPException):
    """Thin wrapper so route code can just `raise AuthError(...)`."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(plain_password, password_hash)
    except Exception:  # noqa: BLE001 - malformed hash, treat as no match
        return False


# --------------------------------------------------------------------------
# client_id derivation
# --------------------------------------------------------------------------


def derive_client_id(email: str) -> str:
    """Turn an email into a slug that satisfies the existing
    ClientScopedModel validation used throughout routes.py (see
    schemas/models.py) -- lowercase alnum/underscore, no dots or @."""
    slug = re.sub(r"[^a-z0-9]+", "_", email.strip().lower()).strip("_")
    return slug or "user"


# --------------------------------------------------------------------------
# JWT issue / verify
# --------------------------------------------------------------------------


def create_access_token(*, user_id: str, email: str, client_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "client_id": client_id,
        "iat": now,
        "exp": now + _ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGO)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGO])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "Session expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, "Invalid or malformed session token.") from exc


# --------------------------------------------------------------------------
# Signup / login (used by the /v1/auth/* routes)
# --------------------------------------------------------------------------


def register_user(email: str, password: str, *, full_name: str | None = None) -> User:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError(422, "Please enter a valid email address.")
    if len(password) < 8:
        raise AuthError(422, "Password must be at least 8 characters.")

    client_id = derive_client_id(email)

    with get_session() as session:
        existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise AuthError(409, "An account with that email already exists.")

        # Guard against a (very unlikely) client_id collision from two
        # different local-parts slugging to the same value.
        suffix = 0
        base_client_id = client_id
        while (
            session.execute(select(User).where(User.client_id == client_id)).scalar_one_or_none()
            is not None
        ):
            suffix += 1
            client_id = f"{base_client_id}_{suffix}"

        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name=(full_name or "").strip() or None,
            client_id=client_id,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def authenticate_user(email: str, password: str) -> User:
    email = email.strip().lower()
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            # Same message for "no such user" and "wrong password" so a
            # caller can't enumerate registered emails.
            raise AuthError(401, "Incorrect email or password.")
        return user


def get_user_by_id(user_id: str) -> User | None:
    with get_session() as session:
        return session.get(User, user_id)


# --------------------------------------------------------------------------
# FastAPI dependency -- drop-in replacement for the old resolve_api_key()
# --------------------------------------------------------------------------


def resolve_api_key(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """
    Kept under the old name so every existing route in routes.py (which
    does `authenticated_client_id: str = Depends(resolve_api_key)`)
    continues to work with zero changes. Now validates a JWT bearer
    token instead of a static API key, and returns the client_id encoded
    in that token.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Missing bearer token.")
    payload = decode_access_token(token)
    client_id = payload.get("client_id")
    if not client_id:
        raise HTTPException(401, "Token does not carry a client_id.")
    return client_id


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Full user object for endpoints that want more than just client_id
    (e.g. GET /v1/auth/me)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    user = get_user_by_id(payload.get("sub", ""))
    if user is None:
        raise HTTPException(401, "User no longer exists.")
    return user


def require_client_access(authenticated_client_id: str, requested_client_id: str) -> None:
    """
    Unchanged in behavior from the previous version: every regular user
    is scoped to exactly one client_id (their own, derived from their
    email at signup), so this just enforces "you can only touch your own
    data". The ADMIN_SCOPE path is kept only for any future superuser
    feature; ordinary signup never grants it.
    """
    if authenticated_client_id == ADMIN_SCOPE:
        return
    if authenticated_client_id != requested_client_id:
        raise HTTPException(
            403,
            f"You are not authorized to access client '{requested_client_id}'.",
        )
