"""Ensure the package root is importable during pytest runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_XLSX = FIXTURES_DIR / "sample_data.xlsx"


def bearer_headers(client_id: str = "*") -> dict[str, str]:
    """Build a JWT Authorization header for API tests."""
    from backend.services.auth.auth import create_access_token

    token = create_access_token(
        user_id=f"test-{client_id}",
        email=f"test-{client_id}@example.com",
        client_id=client_id,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_live_ai_calls(monkeypatch):
    """Hard safety net: NEVER let a test make a real Gemini API call.

    Root cause of the M4 "deadlock": .env contains a real GEMINI_API_KEY,
    and ai_explainer.py's module-level load_dotenv() picks it up on import.
    Any test that runs the pipeline with write_report=True (the default)
    then makes real, rate-limited, retrying HTTPS calls to Google for every
    check -- which is slow (seconds to minutes), not actually stuck, but
    looks exactly like a hang in a test run with no per-test timeout.

    This fixture wipes the key from the environment for every test,
    unconditionally, regardless of what's in .env or the developer's shell.
    ai_explainer.get_api_key() then returns None and every check takes the
    already-tested, instant, zero-network fallback path.

    If a specific test wants to exercise the real AI path, it should
    explicitly monkeypatch.setenv("GEMINI_API_KEY", "...") itself -- this
    autouse fixture will have already cleared it by then.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)