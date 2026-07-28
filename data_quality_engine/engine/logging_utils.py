"""Structured JSONL logging — one file per pipeline run."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_quality_engine.config.settings import SETTINGS


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": getattr(record, "run_id", None),
            "step": getattr(record, "step", None),
            "level": record.levelname,
            "message": record.getMessage(),
            "details": getattr(record, "details", {}) or {},
        }
        return json.dumps(payload, default=str)


def get_logger(run_id: str) -> logging.Logger:
    """
    One JSON log file per run: logs/run_{run_id}.jsonl
    Each log line: {"timestamp", "run_id", "step", "level", "message", "details"}
    """
    logs_dir: Path = SETTINGS["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"dqe.run.{run_id}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "run_id", None) == run_id
        for h in logger.handlers
    ):
        handler = logging.FileHandler(logs_dir / f"run_{run_id}.jsonl", encoding="utf-8")
        handler.setFormatter(JsonlFormatter())
        handler.run_id = run_id  # type: ignore[attr-defined]
        logger.addHandler(handler)

    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    run_id: str,
    step: str,
    details: dict[str, Any] | None = None,
) -> None:
    logger.log(
        level,
        message,
        extra={"run_id": run_id, "step": step, "details": details or {}},
    )
