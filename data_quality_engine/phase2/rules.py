"""
Rule resolution: per-client thresholds and business rules, without
hardcoding anything in Python. Adding a new client is a new YAML file,
never a code change.

Layout expected under config_dir (default: "config/"):

    config/
    ├── base_rules.yaml              <- defaults for every client
    └── clients/
        └── <client_id>/
            ├── rules_v1.yaml
            └── rules_v2.yaml        <- resolver picks the highest version

Merging rule: client values override base values key-by-key (a "deep
merge" — a client can override just one threshold without repeating the
whole base file). Lists (e.g. business_rules) are replaced wholesale by
the client's list if present, not concatenated — that keeps merge
behaviour predictable and easy to reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_VERSION_PATTERN = re.compile(r"rules_v(\d+)\.yaml$")


class RuleResolutionError(Exception):
    """Raised when a ruleset can't be loaded or fails basic validation."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` on top of `base`. Dicts merge key-by-key;
    any other type (including lists) is replaced outright by override's value."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_ruleset(ruleset: dict[str, Any], source: str) -> None:
    """Minimal structural validation — enough to fail loudly on a typo'd
    YAML file instead of silently running with the wrong thresholds."""
    if "thresholds" in ruleset and not isinstance(ruleset["thresholds"], dict):
        raise RuleResolutionError(f"{source}: 'thresholds' must be a mapping.")
    if "business_rules" in ruleset and not isinstance(ruleset["business_rules"], list):
        raise RuleResolutionError(f"{source}: 'business_rules' must be a list.")


@dataclass
class RuleResolver:
    config_dir: Path
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _base_cache: dict[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.config_dir = Path(self.config_dir)

    # -- loading -----------------------------------------------------

    def _load_base(self) -> dict[str, Any]:
        if self._base_cache is not None:
            return self._base_cache
        base_path = self.config_dir / "base_rules.yaml"
        if not base_path.exists():
            raise RuleResolutionError(f"Base ruleset not found at {base_path}")
        with open(base_path, "r", encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}
        _validate_ruleset(base, str(base_path))
        self._base_cache = base
        return base

    def _latest_client_file(self, client_id: str) -> Path | None:
        client_dir = self.config_dir / "clients" / client_id
        if not client_dir.exists():
            return None
        candidates = []
        for path in client_dir.glob("rules_v*.yaml"):
            m = _VERSION_PATTERN.search(path.name)
            if m:
                candidates.append((int(m.group(1)), path))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0])
        return candidates[-1][1]

    def _load_client_override(self, client_id: str) -> tuple[dict[str, Any], str | None]:
        path = self._latest_client_file(client_id)
        if path is None:
            return {}, None
        with open(path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        _validate_ruleset(override, str(path))
        version = override.get("version") or _VERSION_PATTERN.search(path.name).group(0)
        return override, version

    # -- public API ----------------------------------------------------

    def resolve(self, client_id: str, dry_run: bool = False, use_cache: bool = True) -> dict[str, Any]:
        """
        Resolve the effective ruleset for a client: base_rules.yaml merged
        with config/clients/<client_id>/rules_vN.yaml (highest N), if any.

        Args:
            client_id: which client's overrides to apply. A client with no
                       override folder simply gets the base ruleset back.
            dry_run:   if True, resolve and validate but never touch the
                       cache — useful in tests that want a clean resolve
                       every time without perturbing cached state used
                       elsewhere.
            use_cache: if True (default), reuse a previously resolved
                       ruleset for this client_id instead of re-reading
                       from disk.

        Returns:
            A merged ruleset dict with at least "client_id" and "version"
            keys, plus whatever "thresholds" / "business_rules" resulted
            from the merge.
        """
        if use_cache and not dry_run and client_id in self._cache:
            return self._cache[client_id]

        base = self._load_base()
        override, client_version = self._load_client_override(client_id)
        merged = _deep_merge(base, override)

        merged["client_id"] = client_id
        merged["version"] = client_version or merged.get("version", "base")
        merged.setdefault("thresholds", {})
        merged.setdefault("business_rules", [])

        if not dry_run:
            self._cache[client_id] = merged
        return merged

    def clear_cache(self, client_id: str | None = None) -> None:
        if client_id is None:
            self._cache.clear()
        else:
            self._cache.pop(client_id, None)


def init_rule_resolver(config_dir: str | Path = "config/") -> RuleResolver:
    return RuleResolver(config_dir=Path(config_dir))
