"""M6 Entity Resolution — data models and configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ResolutionDecision(str, Enum):
    AUTO_MATCH = "auto_match"
    REVIEW = "review"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class EntityTypeConfig:
    """Per-entity-type configuration (city, region, product_code, …)."""

    entity_type: str
    columns: tuple[str, ...] = ()
    canonicals: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    eligible_roles: tuple[str, ...] = ("categorical", "free_text")


@dataclass(frozen=True)
class EntityResolutionThresholds:
    """Confidence gates — same semantics across tiers (0.0–1.0)."""

    fuzzy_auto: float = 0.85
    fuzzy_review: float = 0.75
    semantic_auto: float = 0.78
    semantic_review: float = 0.70
    tier1_confidence: float = 1.0
    tier1_normalized_confidence: float = 0.99


@dataclass(frozen=True)
class EntityResolutionConfig:
    enabled: bool = True
    entity_types: dict[str, EntityTypeConfig] = field(default_factory=dict)
    thresholds: EntityResolutionThresholds = field(default_factory=EntityResolutionThresholds)
    semantic_model: str = "all-MiniLM-L6-v2"
    max_fuzzy_candidates: int = 25
    max_semantic_candidates: int = 15
    use_ner_for_free_text: bool = True
    skip_person_entities: bool = True


@dataclass
class ResolvedValue:
    """Explainable resolution outcome for one raw entity value."""

    original_value: str
    normalized_value: str
    canonical_value: str | None
    entity_type: str
    tier: int | None
    similarity_score: float
    confidence: float
    decision: ResolutionDecision
    requires_review: bool
    candidate: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data

    @property
    def safe_for_log(self) -> dict[str, Any]:
        """PII-safe view — masks original when flagged."""
        from data_quality_engine.phase2.entity_resolution.privacy import safe_display_value

        data = self.to_dict()
        if self.evidence.get("pii_sensitive"):
            data["original_value"] = safe_display_value(self.original_value)
            if data.get("candidate"):
                data["candidate"] = safe_display_value(str(data["candidate"]))
        return data
