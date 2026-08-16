"""Candidate generation — avoid O(n²) all-pairs comparisons."""

from __future__ import annotations

from rapidfuzz import fuzz, process

from backend.engine.entity_resolution.normalize import safe_normalize


def _length_bucket(value: str) -> int:
    n = len(value)
    if n <= 3:
        return 0
    if n <= 6:
        return 1
    if n <= 12:
        return 2
    return 3


def narrow_candidates(
    query: str,
    canonicals: list[str],
    *,
    max_candidates: int = 25,
) -> list[str]:
    """
    Pre-filter canonical list before fuzzy/semantic scoring.

    Uses first-character prefix + length bucket, then RapidFuzz ``extract``
    as a bounded fallback — never compares every pair of dataset values.
    """
    if not query or not canonicals:
        return []
    if len(canonicals) <= max_candidates:
        return list(canonicals)

    q_norm = safe_normalize(query)
    q_first = q_norm[:1] if q_norm else ""
    bucket = _length_bucket(q_norm)

    filtered: list[str] = []
    for c in canonicals:
        c_norm = safe_normalize(c)
        if q_first and c_norm and c_norm[0] != q_first:
            continue
        if abs(_length_bucket(c_norm) - bucket) > 1:
            continue
        filtered.append(c)

    pool = filtered if len(filtered) >= 3 else list(canonicals)
    if len(pool) <= max_candidates:
        return pool[:max_candidates]

    matches = process.extract(
        query,
        pool,
        scorer=fuzz.ratio,
        limit=max_candidates,
    )
    return [m[0] for m in matches]
