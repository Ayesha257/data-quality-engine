"""Backward-compatible CLI entrypoint at repository root."""
from backend.main import *  # noqa: F403
from backend.main import build_parser, run_pipeline, run_task1_task2
