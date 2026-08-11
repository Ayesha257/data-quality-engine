"""
Phase 2 M6 — Entity Resolution & Normalization.

Three-tier cascade (lookup → RapidFuzz → MiniLM semantic) with explainable
audit trails. spaCy NER is used only for free-text extraction support.
"""

from data_quality_engine.phase2.entity_resolution.cascade import EntityResolutionCascade
from data_quality_engine.phase2.entity_resolution.config import load_entity_resolution_config
from data_quality_engine.phase2.entity_resolution.models import (
    EntityResolutionConfig,
    ResolutionDecision,
    ResolvedValue,
)
from data_quality_engine.phase2.entity_resolution.service import resolve_dataframe

__all__ = [
    "EntityResolutionCascade",
    "EntityResolutionConfig",
    "ResolutionDecision",
    "ResolvedValue",
    "load_entity_resolution_config",
    "resolve_dataframe",
]
