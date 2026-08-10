"""
Phase 2 database tables (SQLAlchemy ORM).

These tables are ADDITIVE to Phase 1 — Phase 1 never reads or writes a
database, it just returns CheckResult objects in memory. Phase 2 is what
persists a record of each run so it can be audited, queried, and later
improved with feedback (M7).

Tables:
    RunRecord           One row per pipeline run (client, file, status, scores).
    RunManifest         Full audit snapshot of what happened in a run (JSON).
    CanonicalMapping    Learned "messy value -> canonical value" mappings
                        (used by M6 entity resolution; schema is ready now
                        so nothing has to be migrated later).
    Disposition         A human's decision on a specific finding
                        ("accepted" / "dismissed" / "false_positive") — M7.
    Rating              A human's 1-5 rating + optional comment on a finding
                        or on an AI explanation — M7.

Nothing here runs automatically. Phase 1's pipeline still runs exactly as
before unless something explicitly imports and uses this module.
"""

from __future__ import annotations

import enum
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_utcnow_lock = threading.Lock()
_utcnow_last: datetime | None = None


def _utcnow() -> datetime:
    """
    Current UTC time, guaranteed strictly increasing within this process.

    Plain `datetime.now(timezone.utc)` can return the *same* value on two
    rapid successive calls whenever the OS clock's resolution is coarser
    than the gap between calls (this is a real, observed behavior, not
    just a theoretical edge case -- e.g. on Windows). Phase 2 relies on
    `completed_at` to determine "the most recent run" for a client/file
    (see phase2/history.py::get_score_trend, which orders by
    `completed_at DESC`). A tied timestamp between two runs saved back to
    back -- exactly what happens when a report is generated and its run
    is recorded, then immediately queried again -- makes that ordering
    ambiguous, and the database is free to return either row first. That
    previously caused history lookups to occasionally return a stale
    "previous" run instead of the one that was just saved.

    Nudging the clock forward by a microsecond whenever it would
    otherwise tie (or go backward, e.g. across a clock adjustment) keeps
    every timestamp generated here -- and therefore every ordering query
    that depends on one -- unambiguous, regardless of the underlying
    clock's resolution. This works for any dataset or run cadence because
    it doesn't depend on real-world timing at all, only on call order.
    """
    global _utcnow_last
    with _utcnow_lock:
        now = datetime.now(timezone.utc)
        if _utcnow_last is not None and now <= _utcnow_last:
            now = _utcnow_last + timedelta(microseconds=1)
        _utcnow_last = now
        return now


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Shared declarative base for every Phase 2 table."""


class User(Base):  # noqa: F821 - `Base` is defined earlier in models.py
    """A registered account. client_id is derived once at signup (see
    auth.derive_client_id) and never changes -- every run, rule set, and
    canonical mapping this user creates is scoped under it."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)  # noqa: F821
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped["datetime"] = mapped_column(DateTime(timezone=True), default=_utcnow)  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email} client_id={self.client_id}>"



class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DispositionType(str, enum.Enum):
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    FALSE_POSITIVE = "false_positive"
    NEEDS_REVIEW = "needs_review"


class RunRecord(Base):
    """One row per Phase 1 pipeline run, regardless of which client/file."""

    __tablename__ = "run_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    client_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.PENDING, nullable=False
    )

    ruleset_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    rows_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # e.g. {"completeness": 92.1, "validity": 88.4, ...}
    dimension_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    manifest: Mapped["RunManifest | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    dispositions: Mapped[list["Disposition"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"<RunRecord id={self.id} client={self.client_id} status={self.status}>"


class RunManifest(Base):
    """
    Full audit-trail snapshot for one run: which checks ran, which ruleset
    version was resolved, environment info, etc. This is the durable,
    queryable counterpart to the JSONL log file written by logging_setup.py.
    """

    __tablename__ = "run_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("run_records.id"), unique=True, nullable=False
    )

    checks_run: Mapped[list] = mapped_column(JSON, default=list)
    ruleset_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    environment: Mapped[str] = mapped_column(String(32), default="development")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped["RunRecord"] = relationship(back_populates="manifest")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RunManifest run_id={self.run_id}>"


class CanonicalMapping(Base):
    """
    Learned mapping from a messy source value to its canonical form,
    scoped per client (e.g. "LHR" -> "Lahore" for client "acme").
    Populated/used starting in M6 (Entity Resolution); the table exists
    from M1 so later milestones need zero schema migration.
    """

    __tablename__ = "canonical_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    client_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    column_role: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "city"
    source_value: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_value: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual|fuzzy|semantic

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CanonicalMapping {self.source_value!r} -> {self.canonical_value!r}>"


class Disposition(Base):
    """
    A human decision on a specific finding from a run, e.g. an analyst
    marking a flagged outlier as a false positive. Feeds M7 (Feedback Loop).
    """

    __tablename__ = "dispositions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_records.id"), nullable=False)
    finding_id: Mapped[str] = mapped_column(String(256), nullable=False)  # "check_name:column"
    disposition: Mapped[DispositionType] = mapped_column(Enum(DispositionType), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped["RunRecord"] = relationship(back_populates="dispositions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Disposition run_id={self.run_id} finding={self.finding_id} -> {self.disposition}>"


class Rating(Base):
    """A 1-5 human rating (optionally with a comment) on a finding or an
    AI-generated explanation. Feeds M7 (Feedback Loop)."""

    __tablename__ = "ratings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_records.id"), nullable=False)
    finding_id: Mapped[str] = mapped_column(String(256), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5, enforced in Pydantic layer
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped["RunRecord"] = relationship(back_populates="ratings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rating run_id={self.run_id} finding={self.finding_id} rating={self.rating}>"