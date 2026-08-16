"""
Session management for Phase 2's database layer.

Design:
    - SQLite by default (zero-setup, one file, perfect for development and
      for a single analyst running this locally).
    - PostgreSQL in production, by setting DQE_DATABASE_URL.
    - Tables are created automatically on first use — no manual migration
      step needed until M8 introduces Alembic for real schema changes.

Usage:
    from backend.database import init_db, get_session

    init_db()  # call once at startup
    with get_session() as session:
        session.add(some_row)
        session.commit()
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import Base

# Load .env from repo root so DQE_DATABASE_URL is available regardless of CWD.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Populated by init_db(); intentionally module-level so the rest of the
# app can just import get_session() without passing an engine everywhere.
_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def _default_sqlite_url() -> str:
    """Local dev fallback when DQE_DATABASE_URL is not set."""
    repo_root: Path = Path(__file__).resolve().parents[2]
    db_path = repo_root / "phase2_dev.db"
    return f"sqlite:///{db_path.as_posix()}"


def _database_url_from_env() -> str | None:
    """Primary connection string from environment (12-factor config)."""
    return os.environ.get("DQE_DATABASE_URL") or None
def init_db(
    environment: str = "development",
    database_url: str | None = None,
    echo: bool = False,
) -> Engine:
    """
    Initialize the Phase 2 database engine and create tables if missing.

    Args:
        environment: "development" (SQLite, default) or "production"
                     (expects DQE_DATABASE_URL to point at PostgreSQL).
        database_url: explicit override. If omitted, falls back to the
                     DQE_DATABASE_URL environment variable, and finally to a
                     local SQLite file for development.
        echo: if True, SQLAlchemy logs every SQL statement (debugging only).

    Returns:
        The SQLAlchemy Engine, in case the caller needs it directly.
    """
    global _engine, _SessionFactory

    url = database_url or _database_url_from_env()
    if not url:
        if environment == "production":
            raise ValueError(
                "environment='production' requires DQE_DATABASE_URL to be set "
                "(e.g. postgresql://user:pass@host/dbname)."
            )
        url = _default_sqlite_url()

    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {
            "check_same_thread": False,
            # SQLite serializes writers at the file level. Phase 2 M4 (the
            # REST API) is the first caller to genuinely run concurrent
            # DB sessions against this engine -- a background pipeline
            # thread writing RunRecord/RunManifest/history rows while an
            # HTTP request thread polls run status at the same time.
            # Python's sqlite3 driver defaults to a 5-second busy timeout,
            # but under load (a tight status-polling loop, or several
            # runs/report-writes overlapping) that's not always enough,
            # and a caller that hits it gets an immediate
            # "database is locked" OperationalError -- which, several
            # layers up, several of Phase 2's "never raises" functions
            # were swallowing as a generic failure rather than retrying,
            # which looked exactly like a hang from the caller's side.
            # 30s gives SQLite's own internal retry loop enough room to
            # wait out a short write instead of erroring immediately.
            "timeout": 30,
        }
    _engine = create_engine(url, echo=echo, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):
        # WAL mode lets readers proceed concurrently with a writer instead
        # of blocking each other on every single statement (SQLite's
        # default rollback-journal mode does full-database write locks).
        # This is what actually fixes concurrent-access contention here;
        # the busy_timeout above is the backstop for whatever contention
        # remains even under WAL (e.g. two writers).
        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    Base.metadata.create_all(_engine)

    # expire_on_commit=False is required because get_session() commits AND
    # closes the session before control returns to the caller. With the
    # SQLAlchemy default (expire_on_commit=True), every attribute on an ORM
    # object is marked "expired" the instant commit() runs, so the very next
    # attribute access (e.g. user.id in authroutes.register()) tries to
    # lazily reload from the DB -- but the session is already closed, which
    # raises sqlalchemy.orm.exc.DetachedInstanceError. Turning this off means
    # committed attribute values simply stay cached on the instance, which is
    # exactly what every "with get_session() as session: ... return row"
    # caller in this codebase (auth.py, routes.py, jobs.py, history.py)
    # already assumes. This does not change query correctness: each request
    # still opens a brand-new session via get_session()/get_session_dependency(),
    # so there's no risk of reading stale data across requests -- the only
    # thing that changes is that attributes read *after* a session closes no
    # longer trigger a (doomed) implicit reload.
    _SessionFactory = sessionmaker(
        bind=_engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
    )
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


def get_session_factory() -> sessionmaker:
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionFactory


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Context-managed session with automatic commit/rollback:

        with get_session() as session:
            session.add(row)
            # commits automatically on clean exit, rolls back on exception
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_dependency() -> Iterator[Session]:
    """
    Generator-style session for frameworks that want a dependency to yield
    a session and close it afterwards (e.g. FastAPI's `Depends`, added in
    M4). Kept here now so M4 doesn't need to touch this file at all.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
