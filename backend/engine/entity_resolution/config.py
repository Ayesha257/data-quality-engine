"""Load M6 configuration from SETTINGS and client rules YAML."""

from __future__ import annotations

from typing import Any

from backend.config.settings import SETTINGS
from backend.engine.entity_resolution.models import (
    EntityResolutionConfig,
    EntityResolutionThresholds,
    EntityTypeConfig,
)


def _thresholds_from_dict(data: dict[str, Any] | None) -> EntityResolutionThresholds:
    data = data or {}
    return EntityResolutionThresholds(
        fuzzy_auto=float(data.get("fuzzy_auto", SETTINGS.get("entity_fuzzy_auto", 0.85))),
        fuzzy_review=float(data.get("fuzzy_review", SETTINGS.get("entity_fuzzy_review", 0.75))),
        semantic_auto=float(data.get("semantic_auto", SETTINGS.get("entity_semantic_auto", 0.78))),
        semantic_review=float(data.get("semantic_review", SETTINGS.get("entity_semantic_review", 0.70))),
    )


def load_entity_resolution_config(
    rules: dict[str, Any] | None = None,
) -> EntityResolutionConfig:
    """Merge SETTINGS defaults with optional resolved client rules dict."""
    rules = rules or {}
    block = rules.get("entity_resolution") or SETTINGS.get("entity_resolution") or {}
    if isinstance(block, dict) and block.get("enabled") is False:
        return EntityResolutionConfig(enabled=False)

    enabled = bool(block.get("enabled", SETTINGS.get("entity_resolution_enabled", True)))
    thresholds = _thresholds_from_dict(block.get("thresholds"))

    entity_types: dict[str, EntityTypeConfig] = {}
    for name, spec in (block.get("entity_types") or {}).items():
        if not isinstance(spec, dict):
            continue
        entity_types[str(name)] = EntityTypeConfig(
            entity_type=str(name),
            columns=tuple(str(c) for c in (spec.get("columns") or ())),
            canonicals=tuple(str(c) for c in (spec.get("canonicals") or ())),
            aliases={str(k): str(v) for k, v in (spec.get("aliases") or {}).items()},
            eligible_roles=tuple(
                str(r) for r in (spec.get("eligible_roles") or ("categorical", "free_text"))
            ),
        )

    return EntityResolutionConfig(
        enabled=enabled,
        entity_types=entity_types,
        thresholds=thresholds,
        semantic_model=str(
            block.get("semantic_model", SETTINGS.get("entity_semantic_model", "all-MiniLM-L6-v2"))
        ),
        max_fuzzy_candidates=int(
            block.get("max_fuzzy_candidates", SETTINGS.get("entity_max_fuzzy_candidates", 25))
        ),
        max_semantic_candidates=int(
            block.get("max_semantic_candidates", SETTINGS.get("entity_max_semantic_candidates", 15))
        ),
        use_ner_for_free_text=bool(block.get("use_ner_for_free_text", True)),
        skip_person_entities=bool(block.get("skip_person_entities", True)),
    )
