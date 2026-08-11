"""Column-level entity resolution service for the pipeline."""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_quality_engine.engine.column_classifier import ROLE_FREE_TEXT, ROLE_PII
from data_quality_engine.phase2.entity_resolution.cascade import EntityResolutionCascade
from data_quality_engine.phase2.entity_resolution.config import load_entity_resolution_config
from data_quality_engine.phase2.entity_resolution.lookup import LookupTable
from data_quality_engine.phase2.entity_resolution.models import (
    EntityResolutionConfig,
    EntityTypeConfig,
    ResolutionDecision,
    ResolvedValue,
)
from data_quality_engine.phase2.entity_resolution.ner import extract_entities_from_text
from data_quality_engine.phase2.entity_resolution.normalize import unique_non_null
from data_quality_engine.phase2.entity_resolution.privacy import safe_display_value
from data_quality_engine.phase2.entity_resolution.repository import load_lookup_from_db


def _match_column(col: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    col_cf = col.casefold()
    return any(p.casefold() == col_cf for p in patterns)


def _infer_entity_type(
    column: str,
    config: EntityResolutionConfig,
) -> EntityTypeConfig | None:
    for spec in config.entity_types.values():
        if _match_column(column, spec.columns):
            return spec
    return None


def _build_canonical_pool(
    spec: EntityTypeConfig,
    series: pd.Series,
    lookup: LookupTable,
) -> list[str]:
    observed = unique_non_null(series.astype(str).tolist())
    pool: list[str] = list(spec.canonicals)
    pool.extend(observed)
    pool.extend(lookup._exact.values())  # noqa: SLF001
    # De-dupe preserving order
    return list(dict.fromkeys(p for p in pool if p))


def resolve_column(
    series: pd.Series,
    *,
    column: str,
    column_role: str,
    entity_spec: EntityTypeConfig,
    config: EntityResolutionConfig,
    lookup: LookupTable | None = None,
) -> dict[str, ResolvedValue]:
    """Resolve unique values in one column — never mutates ``series``."""
    if column_role == ROLE_PII:
        return {}

    lookup = lookup or LookupTable.from_config(
        entity_spec.entity_type,
        entity_spec.canonicals,
        entity_spec.aliases,
    )
    canonicals = _build_canonical_pool(entity_spec, series, lookup)
    cascade = EntityResolutionCascade(
        entity_type=entity_spec.entity_type,
        lookup=lookup,
        canonicals=canonicals,
        config=config,
    )

    values = unique_non_null(series.tolist())
    results: dict[str, ResolvedValue] = {}

    if column_role == ROLE_FREE_TEXT and config.use_ner_for_free_text:
        for raw in values:
            mentions = extract_entities_from_text(
                raw,
                skip_person=config.skip_person_entities,
            )
            if mentions:
                for mention in mentions:
                    results[mention] = cascade.resolve_one(mention)
            else:
                results[raw] = cascade.resolve_one(raw)
    else:
        for raw in values:
            results[raw] = cascade.resolve_one(str(raw))

    return results


def resolve_dataframe(
    df: pd.DataFrame,
    classification: dict[str, str],
    *,
    config: EntityResolutionConfig | None = None,
    client_id: str | None = None,
    session: Any | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run M6 on eligible columns. Returns summary dict for pipeline/report/API.

    Original dataframe is never modified.
    """
    config = config or load_entity_resolution_config(rules)
    if not config.enabled or not config.entity_types:
        return {
            "enabled": False,
            "columns": {},
            "summary": {
                "auto_match": 0,
                "review": 0,
                "no_match": 0,
                "total_values": 0,
            },
            "review_queue": [],
        }

    columns_out: dict[str, Any] = {}
    review_queue: list[dict[str, Any]] = []
    counts = {"auto_match": 0, "review": 0, "no_match": 0, "total_values": 0}

    for col in df.columns:
        col_str = str(col)
        role = classification.get(col_str, classification.get(col, "unknown"))
        spec = _infer_entity_type(col_str, config)
        if spec is None:
            continue
        if role not in spec.eligible_roles:
            continue

        lookup = LookupTable.from_config(spec.entity_type, spec.canonicals, spec.aliases)
        if session is not None and client_id:
            db_table = load_lookup_from_db(session, client_id=client_id, column_role=spec.entity_type)
            for src, canon in db_table._exact.items():  # noqa: SLF001
                lookup.add_mapping(src, canon)

        resolved = resolve_column(
            df[col],
            column=col_str,
            column_role=role,
            entity_spec=spec,
            config=config,
            lookup=lookup,
        )
        if not resolved:
            continue

        col_summary = {
            "entity_type": spec.entity_type,
            "column_role": role,
            "resolutions": {k: v.to_dict() for k, v in resolved.items()},
        }
        columns_out[col_str] = col_summary

        for raw, rv in resolved.items():
            counts["total_values"] += 1
            if rv.decision == ResolutionDecision.AUTO_MATCH:
                counts["auto_match"] += 1
            elif rv.decision == ResolutionDecision.REVIEW:
                counts["review"] += 1
                review_queue.append(
                    {
                        "column": col_str,
                        "entity_type": spec.entity_type,
                        "original_value": safe_display_value(raw),
                        "candidate": safe_display_value(rv.candidate or ""),
                        "confidence": rv.confidence,
                        "tier": rv.tier,
                        "decision": rv.decision.value,
                    }
                )
            else:
                counts["no_match"] += 1

    return {
        "enabled": True,
        "columns": columns_out,
        "summary": counts,
        "review_queue": review_queue,
    }
