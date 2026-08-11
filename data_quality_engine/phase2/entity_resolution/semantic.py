"""Tier 3 — semantic fallback via sentence-transformers (lazy-loaded)."""

from __future__ import annotations

import math
from typing import Any

from data_quality_engine.phase2.entity_resolution.candidates import narrow_candidates

_MODEL_CACHE: dict[str, Any] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SemanticMatcher:
    """
    all-MiniLM-L6-v2 embeddings — only invoked when Tier 1 & 2 fail.

    Model is downloaded once (pretrained, no fine-tuning) and cached.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", *, max_candidates: int = 15) -> None:
        self.model_name = model_name
        self.max_candidates = max_candidates

    def _model(self):
        if self.model_name not in _MODEL_CACHE:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is required for Tier 3 semantic matching. "
                    "Install with: pip install sentence-transformers"
                ) from exc
            _MODEL_CACHE[self.model_name] = SentenceTransformer(self.model_name)
        return _MODEL_CACHE[self.model_name]

    def best_match(
        self,
        value: str,
        canonicals: list[str],
    ) -> tuple[str | None, float]:
        if not value or not canonicals:
            return None, 0.0
        pool = narrow_candidates(value, canonicals, max_candidates=self.max_candidates)
        if not pool:
            return None, 0.0
        try:
            model = self._model()
        except ImportError:
            return None, 0.0
        query_vec = model.encode([value], normalize_embeddings=True)[0]
        cand_vecs = model.encode(pool, normalize_embeddings=True)
        best_idx = 0
        best_score = -1.0
        for i, vec in enumerate(cand_vecs):
            score = float(_cosine(list(query_vec), list(vec)))
            if score > best_score:
                best_score = score
                best_idx = i
        if best_score < 0:
            return None, 0.0
        return pool[best_idx], round(best_score, 4)

    @classmethod
    def clear_cache(cls) -> None:
        _MODEL_CACHE.clear()
