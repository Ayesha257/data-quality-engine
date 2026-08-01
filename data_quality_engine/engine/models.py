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
