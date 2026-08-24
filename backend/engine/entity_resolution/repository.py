"""Database access for Tier 1 canonical mappings."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.database.models import CanonicalMapping
from backend.engine.entity_resolution.lookup import LookupTable


def load_lookup_from_db(
    session: Session,
    *,
    client_id: str,
    column_role: str,
) -> LookupTable:
    """Load persisted mappings into a Tier 1 lookup table."""
    table = LookupTable(entity_type=column_role)
    rows = (
        session.query(CanonicalMapping)
        .filter_by(client_id=client_id, column_role=column_role)
        .all()
    )
    for row in rows:
        table.add_mapping(row.source_value, row.canonical_value)
    return table


def upsert_mapping(
    session: Session,
    *,
    client_id: str,
    column_role: str,
    source_value: str,
    canonical_value: str,
    confidence: float,
    source: str,
) -> CanonicalMapping:
    existing = (
        session.query(CanonicalMapping)
        .filter_by(
            client_id=client_id,
            column_role=column_role,
            source_value=source_value,
        )
        .one_or_none()
    )
    if existing:
        existing.canonical_value = canonical_value
        existing.confidence = confidence
        existing.source = source
        session.add(existing)
        return existing
    row = CanonicalMapping(
        client_id=client_id,
        column_role=column_role,
        source_value=source_value,
        canonical_value=canonical_value,
        confidence=confidence,
        source=source,
    )
    session.add(row)
    return row
