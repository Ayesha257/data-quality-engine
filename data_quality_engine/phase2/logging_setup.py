"""
Phase 2 structured logging + run manifests.

This sits alongside (does NOT replace) Phase 1's
`data_quality_engine.engine.logging_utils`. The difference:

    - engine/logging_utils.py  -> low-level JSONL event log for one run,
                                   used internally by Phase 1 checks.
    - phase2/logging_setup.py  -> per-run JSONL log PLUS a "manifest":
                                   one JSON file that snapshots everything
                                   about a run (client, file, ruleset
                                   version, checks executed, timing,
                                   outcome) for audit/compliance purposes,
                                   and can be queried by client_id later.

Nothing here changes how Phase 1 logs internally.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def new_run_id() -> str:
    """Generate a fresh, globally-unique run id."""
    return str(uuid.uuid4())


class Phase2JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": getattr(record, "run_id", None),
            "client_id": getattr(record, "client_id", None),
            "step": getattr(record, "step", None),
            "level": record.levelname,
            "message": record.getMessage(),
            "details": getattr(record, "details", {}) or {},
        }
        return json.dumps(payload, default=str)


@dataclass
class LoggingConfig:
    """Holds the resolved logging paths. Returned by init_logging()."""

    logs_dir: Path
    manifests_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.manifests_dir = self.logs_dir / "manifests"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)


_config: LoggingConfig | None = None


def init_logging(logs_dir: str | Path = "logs/") -> LoggingConfig:
    """Create (if needed) the logs/ and logs/manifests/ directories and
    remember them for the rest of this process."""
    global _config
    _config = LoggingConfig(logs_dir=Path(logs_dir))
    return _config


def get_logging_config() -> LoggingConfig:
    if _config is None:
        raise RuntimeError("Logging not initialized. Call init_logging() first.")
    return _config


def get_run_logger(run_id: str, client_id: str | None = None) -> logging.Logger:
    """One JSONL log file per run: logs/phase2_run_{run_id}.jsonl"""
    cfg = get_logging_config()

    logger = logging.getLogger(f"dqe.phase2.run.{run_id}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    already_attached = any(
        isinstance(h, logging.FileHandler) and getattr(h, "run_id", None) == run_id
        for h in logger.handlers
    )
    if not already_attached:
        handler = logging.FileHandler(
            cfg.logs_dir / f"phase2_run_{run_id}.jsonl", encoding="utf-8"
        )
        handler.setFormatter(Phase2JsonlFormatter())
        handler.run_id = run_id  # type: ignore[attr-defined]
        logger.addHandler(handler)

    # NOTE: deliberately NOT wrapping this in logging.LoggerAdapter — its
    # default process() replaces the `extra` dict passed at call time
    # instead of merging it, which would silently drop run_id/step/details
    # from every log_event() call. client_id is passed explicitly through
    # log_event()'s `extra` instead.
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    run_id: str,
    step: str,
    client_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    logger.log(
        level,
        message,
        extra={"run_id": run_id, "client_id": client_id, "step": step, "details": details or {}},
    )


def write_run_manifest(
    run_id: str,
    client_id: str,
    file_name: str,
    ruleset_version: str | None,
    checks_run: list[str],
    status: str,
    started_at: str,
    completed_at: str | None = None,
    environment: str = "development",
    extra: dict[str, Any] | None = None,
) -> Path:
    """
    Write one manifest JSON file per run: an at-a-glance audit record of
    what happened, independent of the line-by-line JSONL event log.
    Returns the path written.
    """
    cfg = get_logging_config()
    manifest = {
        "run_id": run_id,
        "client_id": client_id,
        "file_name": file_name,
        "ruleset_version": ruleset_version,
        "checks_run": checks_run,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "environment": environment,
        "extra": extra or {},
        "manifest_written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = cfg.manifests_dir / f"manifest_{run_id}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def read_run_manifest(run_id: str) -> dict[str, Any]:
    cfg = get_logging_config()
    path = cfg.manifests_dir / f"manifest_{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No manifest found for run_id={run_id!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def iter_all_manifests() -> Iterator[dict[str, Any]]:
    cfg = get_logging_config()
    for path in sorted(cfg.manifests_dir.glob("manifest_*.json")):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue


def query_runs_by_client(client_id: str) -> list[dict[str, Any]]:
    """
    Return every manifest belonging to a given client, newest first.
    This reads from the manifest files directly, so it works even without
    a database session — handy for quick audits or when M4's API isn't
    running yet.
    """
    matches = [m for m in iter_all_manifests() if m.get("client_id") == client_id]
    matches.sort(key=lambda m: m.get("started_at") or "", reverse=True)
    return matches
