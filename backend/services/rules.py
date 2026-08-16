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


@dataclass
class DryRunResult:
    """Result of validating a candidate ruleset without saving it (M4
    §4.6). `resolved` is what the effective ruleset WOULD be if this
    candidate were saved and merged over base_rules.yaml -- useful for a
    UI to show a live preview before committing to a save."""

    valid: bool
    error: str | None
    thresholds: int = 0
    business_rules: int = 0
    resolved: dict[str, Any] | None = None


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

    # -- M4 client rules management (§4.6) ------------------------------

    def dry_run(self, client_id: str, rules_yaml: str) -> DryRunResult:
        """
        Validate a candidate ruleset WITHOUT writing anything to disk or
        touching the cache -- exactly the "test new ruleset without
        saving" behavior PHASE2_PLAN.md §4.6 specifies.

        Never raises: any parse or structural error comes back as
        `DryRunResult(valid=False, error=...)` so the HTTP layer can
        return it as a normal 200 response body (a syntactically invalid
        candidate ruleset is an expected, everyday input here -- not a
        server error).
        """
        try:
            candidate = yaml.safe_load(rules_yaml) or {}
        except yaml.YAMLError as exc:
            return DryRunResult(valid=False, error=f"Invalid YAML: {exc}")

        if not isinstance(candidate, dict):
            return DryRunResult(
                valid=False, error="Ruleset must be a YAML mapping at the top level."
            )

        try:
            _validate_ruleset(candidate, f"dry-run for client '{client_id}'")
        except RuleResolutionError as exc:
            return DryRunResult(valid=False, error=str(exc))

        try:
            base = self._load_base()
        except RuleResolutionError as exc:
            # Base ruleset itself is broken -- this candidate can't be
            # meaningfully previewed, but that's a server-side config
            # problem, not something wrong with the candidate.
            return DryRunResult(valid=False, error=f"Cannot resolve base ruleset: {exc}")

        resolved = _deep_merge(base, candidate)
        resolved["client_id"] = client_id
        resolved["version"] = candidate.get("version", "dry-run")
        resolved.setdefault("thresholds", {})
        resolved.setdefault("business_rules", [])

        return DryRunResult(
            valid=True,
            error=None,
            thresholds=len(resolved["thresholds"]),
            business_rules=len(resolved["business_rules"]),
            resolved=resolved,
        )

    def list_versions(self, client_id: str) -> list[int]:
        """All version numbers currently saved for a client, ascending.
        Empty list if the client has no override directory yet."""
        client_dir = self.config_dir / "clients" / client_id
        if not client_dir.exists():
            return []
        versions = []
        for path in client_dir.glob("rules_v*.yaml"):
            m = _VERSION_PATTERN.search(path.name)
            if m:
                versions.append(int(m.group(1)))
        return sorted(versions)

    def save_client_ruleset(self, client_id: str, rules_yaml: str) -> tuple[int, Path]:
        """
        Validate `rules_yaml` (same rules as dry_run) and, if valid,
        write it as the next version for this client:
        config/clients/<client_id>/rules_v{N+1}.yaml -- never overwrites
        an existing version file, so every save is independently
        auditable/rollback-able (an older run's ruleset_snapshot can
        always be re-read even after a client's rules change again).

        Raises RuleResolutionError (never writes anything) if the
        candidate fails validation -- callers should call dry_run()
        first if they want a non-raising preview.

        Returns (new_version_number, path_written). Clears this client's
        resolve() cache so the very next resolve() call picks up the
        change immediately.
        """
        result = self.dry_run(client_id, rules_yaml)
        if not result.valid:
            raise RuleResolutionError(result.error or "Ruleset failed validation.")

        client_dir = self.config_dir / "clients" / client_id
        client_dir.mkdir(parents=True, exist_ok=True)

        existing_versions = self.list_versions(client_id)
        next_version = (existing_versions[-1] + 1) if existing_versions else 1
        dest = client_dir / f"rules_v{next_version}.yaml"

        # Guard against a race where another request wrote this exact
        # version between list_versions() and here -- fail rather than
        # silently clobber a concurrently-saved ruleset.
        if dest.exists():
            raise RuleResolutionError(
                f"rules_v{next_version}.yaml for client '{client_id}' already exists "
                "(concurrent save?); retry."
            )

        dest.write_text(rules_yaml, encoding="utf-8")
        self.clear_cache(client_id)
        return next_version, dest


def init_rule_resolver(config_dir: str | Path = "config/") -> RuleResolver:
    return RuleResolver(config_dir=Path(config_dir))
