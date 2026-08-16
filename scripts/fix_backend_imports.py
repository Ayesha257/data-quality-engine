#!/usr/bin/env python3
"""Rewrite backend.* imports to backend.* after layout migration."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Most specific patterns first.
REPLACEMENTS: list[tuple[str, str]] = [
    ("backend.schemas.authschema", "backend.schemas.authschema"),
    ("backend.routes.authroutes", "backend.routes.authroutes"),
    ("backend.services.auth.auth", "backend.services.auth.auth"),
    ("backend.engine.headless_prompt", "backend.engine.headless_prompt"),
    ("backend.schemas.api", "backend.schemas.api"),
    ("backend.services.jobs", "backend.services.jobs"),
    ("backend.routes.routes", "backend.routes.routes"),
    ("backend.app", "backend.app"),
    ("backend.engine.compliance.", "backend.engine.compliance."),
    ("backend.engine.entity_resolution.", "backend.engine.entity_resolution."),
    ("backend.engine.readiness.", "backend.engine.readiness."),
    ("backend.schemas.", "backend.schemas."),
    ("backend.database.", "backend.database."),
    ("backend.database.logging_setup", "backend.database.logging_setup"),
    ("backend.database.history", "backend.database.history"),
    ("backend.services.rules", "backend.services.rules"),
    ("backend.engine.ai_explanation.enhanced_report", "backend.engine.ai_explanation.enhanced_report"),
    ("backend.engine.ai_explanation.ai_explainer", "backend.engine.ai_explanation.ai_explainer"),
    ("from backend.engine.ai_explanation import ai_explainer", "from backend.engine.ai_explanation import ai_explainer"),
    ("from backend import", "from backend import"),
    ("backend.engine.reports.", "backend.engine.reports."),
    ("backend.engine.reports", "backend.engine.reports"),
    ("backend.engine.", "backend.engine."),
    ("backend.config.", "backend.config."),
    ('REPO_ROOT / "backend" / "config"', 'REPO_ROOT / "backend" / "config"'),
    ("uvicorn backend.app:app", "uvicorn backend.app:app"),
    ("backend", "backend"),
    ("backend", "backend"),
]

SKIP_DIRS = {"frontend", "venv", "node_modules", ".git", "__pycache__", ".pytest_cache", "backend"}


def rewrite_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    original = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed: list[Path] = []
    for path in REPO.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if rewrite_file(path):
            changed.append(path)
    print(f"Rewrote imports in {len(changed)} files.")
    for p in sorted(changed):
        print(f"  {p.relative_to(REPO)}")


if __name__ == "__main__":
    main()
