"""Multi-tier entity resolution cascade (M6 core orchestrator)."""

from __future__ import annotations

from dataclasses import replace

from data_quality_engine.phase2.entity_resolution.fuzzy import FuzzyMatcher
from data_quality_engine.phase2.entity_resolution.lookup import LookupTable
from data_quality_engine.phase2.entity_resolution.models import (
    EntityResolutionConfig,
    EntityResolutionThresholds,
    ResolutionDecision,
    ResolvedValue,
)
from data_quality_engine.phase2.entity_resolution.normalize import safe_normalize
from data_quality_engine.phase2.entity_resolution.privacy import looks_like_pii
from data_quality_engine.phase2.entity_resolution.semantic import SemanticMatcher


def _decision_from_confidence(
    confidence: float,
    *,
    auto_threshold: float,
    review_threshold: float,
) -> ResolutionDecision:
    if confidence >= auto_threshold:
        return ResolutionDecision.AUTO_MATCH
    if confidence >= review_threshold:
        return ResolutionDecision.REVIEW
    return ResolutionDecision.NO_MATCH


class EntityResolutionCascade:
    """
    Tier 1 → Tier 2 → Tier 3 resolution with explainable evidence.

    Never mutates input values; never auto-merges below configured gates.
    """

    def __init__(
        self,
        entity_type: str,
        lookup: LookupTable,
        canonicals: list[str],
        config: EntityResolutionConfig | None = None,
        thresholds: EntityResolutionThresholds | None = None,
    ) -> None:
        self.entity_type = entity_type
        self.lookup = lookup
        self.canonicals = list(dict.fromkeys(c for c in canonicals if c))
        self.config = config or EntityResolutionConfig()
        self.thresholds = thresholds or self.config.thresholds
        self._fuzzy = FuzzyMatcher(max_candidates=self.config.max_fuzzy_candidates)
        self._semantic: SemanticMatcher | None = None

    def _semantic_matcher(self) -> SemanticMatcher:
        if self._semantic is None:
            self._semantic = SemanticMatcher(
                model_name=self.config.semantic_model,
                max_candidates=self.config.max_semantic_candidates,
            )
        return self._semantic

    def resolve_one(self, value: str) -> ResolvedValue:
        original = "" if value is None else str(value)
        stripped = original.strip()
        normalized = safe_normalize(stripped)
        pii_flag = looks_like_pii(stripped)

        base = ResolvedValue(
            original_value=original,
            normalized_value=normalized,
            canonical_value=None,
            entity_type=self.entity_type,
            tier=None,
            similarity_score=0.0,
            confidence=0.0,
            decision=ResolutionDecision.NO_MATCH,
            requires_review=True,
            candidate=None,
            evidence={"pii_sensitive": pii_flag},
        )

        if not stripped:
            return replace(
                base,
                decision=ResolutionDecision.NO_MATCH,
                requires_review=False,
                evidence={**base.evidence, "reason": "empty_value"},
            )

        # Tier 1 — deterministic lookup (exact / normalized / alias)
        canonical, kind, conf = self.lookup.lookup(stripped)
        if canonical is not None:
            decision = ResolutionDecision.AUTO_MATCH
            return replace(
                base,
                canonical_value=canonical,
                tier=1,
                similarity_score=conf,
                confidence=conf,
                decision=decision,
                requires_review=False,
                candidate=canonical,
                evidence={
                    **base.evidence,
                    "tier": 1,
                    "match_kind": kind,
                },
            )

        if not self.canonicals:
            return replace(
                base,
                evidence={**base.evidence, "reason": "no_canonicals"},
            )

        # Tier 2 — RapidFuzz
        fuzzy_candidate, fuzzy_score = self._fuzzy.best_match(stripped, self.canonicals)
        if fuzzy_candidate is not None and fuzzy_score >= self.thresholds.fuzzy_review:
            decision = _decision_from_confidence(
                fuzzy_score,
                auto_threshold=self.thresholds.fuzzy_auto,
                review_threshold=self.thresholds.fuzzy_review,
            )
            requires_review = decision != ResolutionDecision.AUTO_MATCH
            return replace(
                base,
                canonical_value=fuzzy_candidate if decision != ResolutionDecision.NO_MATCH else None,
                tier=2,
                similarity_score=fuzzy_score,
                confidence=fuzzy_score,
                decision=decision,
                requires_review=requires_review,
                candidate=fuzzy_candidate,
                evidence={
                    **base.evidence,
                    "tier": 2,
                    "matcher": "rapidfuzz",
                },
            )

        # Tier 3 — semantic (fallback only)
        sem_candidate, sem_score = self._semantic_matcher().best_match(stripped, self.canonicals)
        if sem_candidate is not None and sem_score >= self.thresholds.semantic_review:
            decision = _decision_from_confidence(
                sem_score,
                auto_threshold=self.thresholds.semantic_auto,
                review_threshold=self.thresholds.semantic_review,
            )
            requires_review = decision != ResolutionDecision.AUTO_MATCH
            return replace(
                base,
                canonical_value=sem_candidate if decision != ResolutionDecision.NO_MATCH else None,
                tier=3,
                similarity_score=sem_score,
                confidence=sem_score,
                decision=decision,
                requires_review=requires_review,
                candidate=sem_candidate,
                evidence={
                    **base.evidence,
                    "tier": 3,
                    "matcher": "all-MiniLM-L6-v2",
                },
            )

        return replace(
            base,
            candidate=fuzzy_candidate or sem_candidate,
            similarity_score=max(fuzzy_score, sem_score),
            confidence=max(fuzzy_score, sem_score),
            evidence={
                **base.evidence,
                "reason": "below_all_thresholds",
                "fuzzy_score": fuzzy_score,
                "semantic_score": sem_score,
            },
        )

    def resolve(self, values: list[str]) -> dict[str, ResolvedValue]:
        """Resolve many values — keys preserve the original raw string."""
        return {v: self.resolve_one(v) for v in values}
