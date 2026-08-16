"""Tier 2 — RapidFuzz candidate matching (wraps Phase 1 similarity)."""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz, process

from backend.engine.standardization.fuzzy_match import _similarity
from backend.engine.entity_resolution.candidates import narrow_candidates


class FuzzyMatcher:
    """Score unresolved values against canonical candidates via RapidFuzz."""

    def __init__(self, *, max_candidates: int = 25) -> None:
        self.max_candidates = max_candidates

    @staticmethod
    def _scorer(a: str, b: str, **_: Any) -> float:
        return _similarity(a, b)

    def best_match(
        self,
        value: str,
        canonicals: list[str],
    ) -> tuple[str | None, float]:
        """
        Returns (best_candidate, score_0_to_1).

        Uses ``narrow_candidates`` first, then ``process.extractOne``.
        """
        if not value or not canonicals:
            return None, 0.0
        pool = narrow_candidates(value, canonicals, max_candidates=self.max_candidates)
        if not pool:
            return None, 0.0
        result = process.extractOne(
            value,
            pool,
            scorer=self._scorer,
        )
        if not result:
            return None, 0.0
        candidate, score, _ = result
        return candidate, round(float(score) / 100.0, 4)

    def ratio(self, a: str, b: str) -> float:
        return round(_similarity(a, b) / 100.0, 4)
