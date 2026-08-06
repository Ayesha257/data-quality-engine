"""
Session management for Phase 2's database layer.

Design:
    - SQLite by default (zero-setup, one file, perfect for development and
      for a single analyst running this locally).
    - PostgreSQL in production, just by setting DATABASE_URL.
    - Tables are created automatically on first use — no manual migration
      step needed until M8 introduces Alembic for real schema changes.

Usage:
    from data_quality_engine.phase2.database import init_db, get_session

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

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.phase2.database.models import Base

# Populated by init_db(); intentionally module-level so the rest of the
# app can just import get_session() without passing an engine everywhere.
_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def _default_sqlite_url() -> str:
    repo_root: Path = SETTINGS["logs_dir"].parent
    db_path = repo_root / "phase2_dev.db"
    return f"sqlite:///{db_path}"


def init_db(
    environment: str = "development",
    database_url: str | None = None,
    echo: bool = False,
) -> Engine:
    """
    Initialize the Phase 2 database engine and create tables if missing.

    Args:
        environment: "development" (SQLite, default) or "production"
                     (expects DATABASE_URL to point at PostgreSQL).
        database_url: explicit override. If omitted, falls back to the
                     DATABASE_URL environment variable, and finally to a
                     local SQLite file for development.
        echo: if True, SQLAlchemy logs every SQL statement (debugging only).

    Returns:
        The SQLAlchemy Engine, in case the caller needs it directly.
    """
    global _engine, _SessionFactory

    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        if environment == "production":
            raise ValueError(
                "environment='production' requires DATABASE_URL to be set "
                "(e.g. postgresql://user:pass@host/dbname)."
            )
        url = _default_sqlite_url()

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, echo=echo, connect_args=connect_args, future=True)

    Base.metadata.create_all(_engine)

    _SessionFactory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
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
