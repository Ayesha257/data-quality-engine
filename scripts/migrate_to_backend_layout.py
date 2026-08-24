#!/usr/bin/env python3
"""One-shot repo layout migration: backend/* -> backend/*.

Run from repository root:
    python scripts/migrate_to_backend_layout.py

Pure file moves + import path rewrites. Does not change logic.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "backend"
BACKEND = REPO / "backend"

SKIP_DIRS = {
    "frontend",
    "venv",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "backend",  # idempotent
}

# (source relative to REPO, destination relative to REPO)
MOVES: list[tuple[str, str]] = [
    # config
    ("backend/config", "backend/config"),
    # engine core
    ("backend/engine/checks", "backend/engine/checks"),
    ("backend/engine/pii", "backend/engine/pii"),
    ("backend/engine/standardization", "backend/engine/standardization"),
    ("backend/engine/reporting", "backend/engine/reports"),
    ("backend/engine/checkpoint.py", "backend/engine/checkpoint.py"),
    ("backend/engine/column_classifier.py", "backend/engine/column_classifier.py"),
    ("backend/engine/ingestion.py", "backend/engine/ingestion.py"),
    ("backend/engine/logging_utils.py", "backend/engine/logging_utils.py"),
    ("backend/engine/models.py", "backend/engine/models.py"),
    ("backend/engine/report.py", "backend/engine/report.py"),
    ("backend/engine/scoring.py", "backend/engine/scoring.py"),
    ("backend/engine/__init__.py", "backend/engine/__init__.py"),
    # phase2 engine modules
    ("backend/phase2/compliance", "backend/engine/compliance"),
    ("backend/phase2/entity_resolution", "backend/engine/entity_resolution"),
    ("backend/phase2/readiness", "backend/engine/readiness"),
    ("backend/phase2/ai_explainer.py", "backend/engine/ai_explanation/ai_explainer.py"),
    ("backend/phase2/enhanced_report.py", "backend/engine/ai_explanation/enhanced_report.py"),
    ("backend/phase2/api/headless_prompt.py", "backend/engine/headless_prompt.py"),
    # database
    ("backend/phase2/database/__init__.py", "backend/database/__init__.py"),
    ("backend/phase2/database/models.py", "backend/database/models.py"),
    ("backend/phase2/logging_setup.py", "backend/database/logging_setup.py"),
    ("backend/phase2/history.py", "backend/database/history.py"),
    # routes / schemas / services
    ("backend/phase2/api/routes.py", "backend/routes/routes.py"),
    ("backend/phase2/api/schemas.py", "backend/schemas/api.py"),
    ("backend/phase2/schemas/models.py", "backend/schemas/models.py"),
    ("backend/phase2/schemas/__init__.py", "backend/schemas/__init__.py"),
    ("backend/phase2/api/jobs.py", "backend/services/jobs.py"),
    ("backend/phase2/rules.py", "backend/services/rules.py"),
    ("backend/phase2/api/auth.py", "backend/services/auth/auth.py"),
    ("backend/phase2/api/auth_routes.py", "backend/routes/authroutes.py"),
    ("backend/phase2/api/auth_schemas.py", "backend/schemas/authschema.py"),
    # app + CLI
    ("backend/phase2/api/app.py", "backend/app.py"),
    ("backend/phase2/__init__.py", "backend/__init__.py"),
    ("main.py", "backend/main.py"),
]

# Longest/most-specific replacements first.
IMPORT_REPLACEMENTS: list[tuple[str, str]] = [
    ("backend.engine.reports.", "backend.engine.reports."),
    ("backend.engine.reports", "backend.engine.reports"),
    ("backend.engine.entity_resolution.", "backend.engine.entity_resolution."),
    ("backend.engine.compliance.", "backend.engine.compliance."),
    ("backend.engine.readiness.", "backend.engine.readiness."),
    ("backend.schemas.authschema", "backend.schemas.authschema"),
    ("backend.routes.authroutes", "backend.routes.authroutes"),
    ("backend.services.auth.auth", "backend.services.auth.auth"),
    ("backend.engine.headless_prompt", "backend.engine.headless_prompt"),
    ("backend.schemas.api", "backend.schemas.api"),
    ("backend.routes.routes", "backend.routes.routes"),
    ("backend.services.jobs", "backend.services.jobs"),
    ("backend.app", "backend.app"),
    ("backend.database.", "backend.database."),
    ("backend.schemas.", "backend.schemas."),
    ("backend.engine.ai_explanation.ai_explainer", "backend.engine.ai_explanation.ai_explainer"),
    ("backend.engine.ai_explanation.enhanced_report", "backend.engine.ai_explanation.enhanced_report"),
    ("backend.database.logging_setup", "backend.database.logging_setup"),
    ("backend.database.history", "backend.database.history"),
    ("backend.services.rules", "backend.services.rules"),
    ("backend.routes", "backend.routes"),
    ("backend", "backend"),
    ("backend.engine.", "backend.engine."),
    ("backend.config.", "backend.config."),
    ("backend", "backend"),
    ("uvicorn backend.app:app", "uvicorn backend.app:app"),
    ("from backend.main import run_pipeline", "from backend.main import run_pipeline"),
    ("import backend.main as main", "import backend.main as main"),
]

TEXT_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".ps1", ".toml", ".ini", ".cfg"}


def move_path(src: Path, dst: Path) -> None:
    if not src.exists():
        if dst.exists():
            return
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if src.is_dir():
            for child in src.rglob("*"):
                rel = child.relative_to(src)
                target = dst / rel
                if child.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(child), str(target))
            shutil.rmtree(src, ignore_errors=True)
        else:
            return
    else:
        shutil.move(str(src), str(dst))


def apply_moves() -> None:
    for src_rel, dst_rel in MOVES:
        src = REPO / src_rel.replace("/", "\\") if False else REPO / src_rel
        dst = REPO / dst_rel
        print(f"MOVE {src_rel} -> {dst_rel}")
        move_path(src, dst)


def write_extra_init_files() -> None:
    (BACKEND / "engine" / "ai_explanation" / "__init__.py").write_text(
        '"""AI explanation layer (Gemini + enhanced HTML reports)."""\n',
        encoding="utf-8",
    )
    (BACKEND / "routes" / "__init__.py").write_text(
        '"""FastAPI HTTP route modules."""\n',
        encoding="utf-8",
    )
    (BACKEND / "services" / "__init__.py").write_text(
        '"""Application services (auth, background jobs, rules)."""\n',
        encoding="utf-8",
    )
    (BACKEND / "services" / "auth" / "__init__.py").write_text(
        '"""Authentication service: JWT, password hashing, user lookup."""\n',
        encoding="utf-8",
    )


def rewrite_imports_in_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    original = text
    for old, new in IMPORT_REPLACEMENTS:
        text = text.replace(old, new)
    # auth_router import path
    text = text.replace(
        "from backend.routes.authroutes import auth_router",
        "from backend.routes.authroutes import auth_router",
    )
    text = text.replace(
        "from backend.routes.routes import router",
        "from backend.routes.routes import router",
    )
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def rewrite_all_imports() -> list[Path]:
    changed: list[Path] = []
    for path in REPO.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in TEXT_EXTENSIONS:
            continue
        if rewrite_imports_in_file(path):
            changed.append(path)
    return changed


def write_root_main_shim() -> None:
    shim = REPO / "main.py"
    shim.write_text(
        '"""Backward-compatible CLI entrypoint at repository root."""\n'
        "from backend.main import *  # noqa: F403\n"
        "from backend.main import build_parser, run_pipeline, run_task1_task2\n",
        encoding="utf-8",
    )


def cleanup_old_package() -> None:
    if PKG.exists():
        shutil.rmtree(PKG, ignore_errors=True)


def main() -> None:
    BACKEND.mkdir(exist_ok=True)
    apply_moves()
    write_extra_init_files()
    changed = rewrite_all_imports()
    write_root_main_shim()
    cleanup_old_package()
    print(f"\nRewrote imports in {len(changed)} files.")
    print("Migration complete.")


if __name__ == "__main__":
    main()
