"""Tier 1 — deterministic canonical lookup (cheapest)."""

from __future__ import annotations

from dataclasses import dataclass, field

from data_quality_engine.phase2.entity_resolution.normalize import apply_aliases, safe_normalize


@dataclass
class LookupTable:
    """
    In-memory lookup: exact + normalized keys → canonical value.

    Sources: configured canonical list, aliases, and (optionally) DB rows
    loaded via ``load_db_mappings``.
    """

    entity_type: str
    _exact: dict[str, str] = field(default_factory=dict)
    _normalized: dict[str, str] = field(default_factory=dict)

    def add_canonical(self, canonical: str) -> None:
        if not canonical or not str(canonical).strip():
            return
        c = str(canonical).strip()
        self._exact[c] = c
        self._normalized[safe_normalize(c)] = c

    def add_alias(self, alias: str, canonical: str) -> None:
        if not alias or not canonical:
            return
        self.add_canonical(canonical)
        a = str(alias).strip()
        self._exact[a] = str(canonical).strip()
        self._normalized[safe_normalize(a)] = str(canonical).strip()

    def add_mapping(self, source: str, canonical: str) -> None:
        self.add_alias(source, canonical)

    @classmethod
    def from_config(
        cls,
        entity_type: str,
        canonicals: list[str] | tuple[str, ...],
        aliases: dict[str, str] | None = None,
    ) -> LookupTable:
        table = cls(entity_type=entity_type)
        for c in canonicals:
            table.add_canonical(c)
        for alias, target in (aliases or {}).items():
            table.add_alias(alias, target)
        return table

    def lookup(self, value: str) -> tuple[str | None, str, float]:
        """
        Returns (canonical, match_kind, confidence).

        match_kind: ``exact`` | ``normalized`` | ``alias`` | ``miss``
        """
        if not value or not str(value).strip():
            return None, "miss", 0.0
        raw = str(value).strip()
        if raw in self._exact:
            return self._exact[raw], "exact", 1.0
        norm = safe_normalize(raw)
        if norm in self._normalized:
            return self._normalized[norm], "normalized", 0.99
        return None, "miss", 0.0
