"""
Phase 2 — Intelligence Layer.

Milestone 1 (Foundations) lives here:
    - database/   SQLAlchemy models + session management
    - schemas/    Pydantic validation models
    - rules.py    Per-client rule resolution (base + overrides)
    - logging_setup.py   Structured JSONL logs + run manifests

Nothing in Phase 2 modifies Phase 1. Phase 1's `engine/` package works
completely standalone; Phase 2 only ever calls INTO it, never the other
way around.

Typical startup, e.g. in main.py or a new phase2 orchestrator:

    from data_quality_engine.phase2 import init_db_session, init_logging, init_rule_resolver

    init_db_session(environment="development")
    init_logging(logs_dir="logs/")
    resolver = init_rule_resolver(config_dir="config/")
"""

from __future__ import annotations

from pathlib import Path

from data_quality_engine.phase2.database import get_session, init_db
from data_quality_engine.phase2.logging_setup import (
    LoggingConfig,
    get_logging_config,
    get_run_logger,
    init_logging,
    log_event,
    new_run_id,
    query_runs_by_client,
    read_run_manifest,
    write_run_manifest,
)
from data_quality_engine.phase2.rules import RuleResolver
from data_quality_engine.phase2.rules import init_rule_resolver as _build_rule_resolver

__all__ = [
    "init_db_session",
    "init_logging",
    "init_rule_resolver",
    "get_session",
    "get_logging_config",
    "get_run_logger",
    "log_event",
    "new_run_id",
    "query_runs_by_client",
    "read_run_manifest",
    "write_run_manifest",
    "RuleResolver",
    "LoggingConfig",
]


def init_db_session(environment: str = "development", database_url: str | None = None):
    """Thin, memorable alias for database.init_db(), matching the name
    used in PHASE2_PLAN.md's startup example."""
    return init_db(environment=environment, database_url=database_url)


_rule_resolver_singleton: RuleResolver | None = None


def get_rule_resolver() -> RuleResolver:
    """Return the resolver created by the most recent init_rule_resolver()
    call, so other modules don't have to pass one around manually."""
    if _rule_resolver_singleton is None:
        raise RuntimeError("Rule resolver not initialized. Call init_rule_resolver() first.")
    return _rule_resolver_singleton


def init_rule_resolver(config_dir: str | Path = "config/") -> RuleResolver:
    global _rule_resolver_singleton
    _rule_resolver_singleton = _build_rule_resolver(config_dir=config_dir)
    return _rule_resolver_singleton
