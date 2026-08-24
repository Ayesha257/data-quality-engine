"""Ensure the package root is importable during pytest runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = ROOT / "tests" / "fixtures"
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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
