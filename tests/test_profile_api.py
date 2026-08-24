"""
Profile API integration tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.database import get_session, init_db
from backend.database.models import User
from backend.services.auth.auth import create_access_token, derive_client_id


@pytest.fixture()
def profile_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_profile.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DQE_DATABASE_URL", db_url)
    init_db(database_url=db_url)

    from backend.app import app

    with TestClient(app) as client:
        yield client


def _seed_user(
    *,
    email: str,
    password_hash: str = "test-hash",
    full_name: str | None = None,
) -> tuple[User, str]:
    email = email.strip().lower()
    with get_session() as session:
        user = User(
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            client_id=derive_client_id(email),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user_id=user.id, email=user.email, client_id=user.client_id)
        return user, token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestProfileApi:
    def test_get_profile_requires_auth(self, profile_client):
        resp = profile_client.get("/v1/profile")
        assert resp.status_code == 401

    def test_get_profile_returns_account_details(self, profile_client):
        user, token = _seed_user(
            email="profile@example.com",
            full_name="Profile User",
        )
        resp = profile_client.get("/v1/profile", headers=_auth_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "profile@example.com"
        assert body["full_name"] == "Profile User"
        assert body["client_id"] == user.client_id
        assert "created_at" in body

    def test_patch_profile_updates_full_name(self, profile_client):
        _, token = _seed_user(email="patch@example.com")
        headers = _auth_headers(token)
        resp = profile_client.patch("/v1/profile", headers=headers, json={"full_name": "Updated Name"})
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Updated Name"

        me = profile_client.get("/v1/auth/me", headers=headers)
        assert me.json()["full_name"] == "Updated Name"

    def test_change_password_rejects_wrong_current(self, profile_client, monkeypatch):
        _, token = _seed_user(email="pwd@example.com")
        monkeypatch.setattr(
            "backend.services.profile.profile.verify_password",
            lambda plain, hashed: plain == "secret123",
        )
        headers = _auth_headers(token)
        resp = profile_client.post(
            "/v1/profile/change-password",
            headers=headers,
            json={"current_password": "wrong", "new_password": "newsecret1"},
        )
        assert resp.status_code == 401

    def test_change_password_updates_hash(self, profile_client, monkeypatch):
        user, token = _seed_user(email="rotate@example.com", password_hash="old-hash")
        stored = {"hash": user.password_hash}

        def fake_verify(plain, hashed):
            return plain == "secret123" and hashed == stored["hash"]

        def fake_hash(plain):
            stored["hash"] = f"hashed:{plain}"
            return stored["hash"]

        monkeypatch.setattr("backend.services.profile.profile.verify_password", fake_verify)
        monkeypatch.setattr("backend.services.profile.profile.hash_password", fake_hash)

        headers = _auth_headers(token)
        resp = profile_client.post(
            "/v1/profile/change-password",
            headers=headers,
            json={"current_password": "secret123", "new_password": "newsecret1"},
        )
        assert resp.status_code == 204
        assert stored["hash"] == "hashed:newsecret1"

    def test_profile_stats_empty(self, profile_client):
        _, token = _seed_user(email="stats@example.com")
        resp = profile_client.get("/v1/profile/stats", headers=_auth_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "total_runs": 0,
            "completed_runs": 0,
            "failed_runs": 0,
            "average_score": None,
        }
