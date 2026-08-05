"""Shared result contract used by every check and by scoring/reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    check_name: str
    status: str  # "passed" | "failed" | "error"
    column: str | None
    issues_found: int
    details: dict[str, Any] = field(default_factory=dict)
    dimension: str = ""  # internal check tag; rubric scoring uses explicit keys
    quality_ratio: float | None = None
    # Optional graded pass-rate in [0.0, 1.0] for checks whose "failed" status
    # can vary in severity (e.g. 1/533 rows missing vs 500/533 rows missing).
    # Row/value-level checks (missing_values, duplicates, outliers) set this
    # to the actual fraction of clean rows/values so scoring.py can average a
    # continuous score instead of collapsing every column to a binary
    # pass=1/fail=0. Checks that are inherently binary at the column level
    # (schema_quality, freshness, type_mismatch, consistency, validity rule
    # checks) leave this as None, and scoring.py falls back to the original
    # status-based binary scoring for them -- unchanged behaviour.