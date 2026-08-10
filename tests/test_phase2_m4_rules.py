"""
Phase 2 -- M4 (Client Rules Management, PHASE2_PLAN.md §4.6) integration
tests.

Run just these with:
    pytest tests/test_phase2_m4_rules.py -v

Covers, end to end via FastAPI's TestClient:
    - GET  /v1/clients/{client_id}/rules           (resolve base + override)
    - POST /v1/clients/{client_id}/rules/dry-run    (validate, never saves)
    - POST /v1/clients/{client_id}/rules            (save a new version)
    - auth/authorization scoping (reuses the same X-API-Key model as
      tests/test_phase2_m4_api.py -- see that file / auth.py for the
      full auth test matrix; this file only re-checks that these three
      new endpoints are actually wired to it, not the whole matrix again)

Isolation: every test gets its own temporary rules config directory (own
base_rules.yaml, own empty clients/ tree) via monkeypatch + tmp_path, so
nothing here ever reads or writes the repo's real config/ directory.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml
from fastapi.testclient import TestClient

from data_quality_engine.phase2.api import jobs
from data_quality_engine.phase2.database import init_db

ADMIN_API_KEY = "test-admin-key"
SCOPED_API_KEY_A = "test-scoped-key-a"  # bound to client_a only

BASE_RULES_YAML = textwrap.dedent(
    """
    client_id: __base__
    version: v1.0
    thresholds:
      iqr_multiplier: 1.5
      fuzzy_threshold: 90
      min_acceptable_overall_score: 60.0
    business_rules:
      - rule_id: no_negative_amounts
        description: "Monetary columns should not contain negative values."
        condition: "value < 0 for columns matching '*Amt*'"
        severity: medium
    """
)


@pytest.fixture()
def rules_client(tmp_path, monkeypatch):
    """
    Isolated app instance for rules-management tests: its own tmp
    rules_config_dir (with a minimal, known base_rules.yaml) so expected
    threshold/business_rule counts are deterministic, plus the same
    admin/scoped API key setup used in test_phase2_m4_api.py.
    """
    from data_quality_engine.config.settings import SETTINGS

    config_dir = tmp_path / "config"
    (config_dir / "clients").mkdir(parents=True)
    (config_dir / "base_rules.yaml").write_text(BASE_RULES_YAML, encoding="utf-8")

    db_path = tmp_path / "test_phase2_m4_rules.db"
    init_db(database_url=f"sqlite:///{db_path}")

    monkeypatch.setitem(SETTINGS, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setitem(SETTINGS, "reports_dir", tmp_path / "reports")
    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")
    monkeypatch.setitem(SETTINGS, "rules_config_dir", config_dir)

    monkeypatch.setenv("DQE_API_KEYS", f"{ADMIN_API_KEY}:*,{SCOPED_API_KEY_A}:client_a")

    jobs.configure_executor(max_workers=1)

    from data_quality_engine.phase2.api.app import app

    with TestClient(app, headers={"X-API-Key": ADMIN_API_KEY}) as client:
        yield client


# ---------------------------------------------------------------------------
# GET /v1/clients/{client_id}/rules
# ---------------------------------------------------------------------------

class TestGetActiveRules:
    def test_client_with_no_override_gets_base_ruleset(self, rules_client):
        resp = rules_client.get("/v1/clients/acme_corp/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_id"] == "acme_corp"
        assert body["thresholds"]["iqr_multiplier"] == 1.5
        assert len(body["business_rules"]) == 1

    def test_invalid_client_id_is_422(self, rules_client):
        resp = rules_client.get("/v1/clients/not a valid id!/rules")
        assert resp.status_code == 422

    def test_requires_api_key(self, rules_client):
        resp = rules_client.get("/v1/clients/acme_corp/rules", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_scoped_key_cannot_read_another_clients_rules(self, rules_client):
        resp = rules_client.get(
            "/v1/clients/client_b/rules", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 403

    def test_scoped_key_can_read_its_own_clients_rules(self, rules_client):
        resp = rules_client.get(
            "/v1/clients/client_a/rules", headers={"X-API-Key": SCOPED_API_KEY_A}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/clients/{client_id}/rules/dry-run
# ---------------------------------------------------------------------------

class TestDryRunRules:
    def test_valid_candidate_reports_counts_and_preview(self, rules_client):
        candidate = yaml.safe_dump(
            {
                "thresholds": {"fuzzy_threshold": 95},
                "business_rules": [
                    {
                        "rule_id": "id_columns_unique",
                        "description": "IDs must be unique.",
                        "condition": "duplicate rate > 0",
                        "severity": "high",
                    }
                ],
            }
        )
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules/dry-run", json={"rules_yaml": candidate}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["error"] is None
        # merged with base's 3 thresholds, overriding fuzzy_threshold -> still 3 keys
        assert body["thresholds"] == 3
        assert body["business_rules"] == 1
        assert body["resolved"]["thresholds"]["fuzzy_threshold"] == 95
        # base's untouched threshold survives the merge
        assert body["resolved"]["thresholds"]["iqr_multiplier"] == 1.5

    def test_malformed_yaml_is_reported_as_invalid_not_a_500(self, rules_client):
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules/dry-run",
            json={"rules_yaml": "thresholds: [this is not: a mapping"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["error"]
        assert body["resolved"] is None

    def test_wrong_shaped_thresholds_is_invalid(self, rules_client):
        candidate = yaml.safe_dump({"thresholds": ["not", "a", "mapping"]})
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules/dry-run", json={"rules_yaml": candidate}
        )
        body = resp.json()
        assert body["valid"] is False
        assert "thresholds" in body["error"]

    def test_dry_run_never_writes_a_file(self, rules_client, tmp_path):
        candidate = yaml.safe_dump({"thresholds": {"fuzzy_threshold": 99}})
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules/dry-run", json={"rules_yaml": candidate}
        )
        assert resp.status_code == 200
        client_dir = tmp_path / "config" / "clients" / "acme_corp"
        assert not client_dir.exists()

    def test_requires_api_key(self, rules_client):
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules/dry-run",
            json={"rules_yaml": "thresholds: {}"},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/clients/{client_id}/rules  (save)
# ---------------------------------------------------------------------------

class TestSaveClientRules:
    def test_first_save_creates_version_1(self, rules_client, tmp_path):
        candidate = yaml.safe_dump({"thresholds": {"fuzzy_threshold": 95}})
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules", json={"rules_yaml": candidate}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["client_id"] == "acme_corp"
        assert body["version"] == 1
        saved = tmp_path / "config" / "clients" / "acme_corp" / "rules_v1.yaml"
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == candidate

    def test_second_save_increments_to_version_2_without_overwriting_v1(
        self, rules_client, tmp_path
    ):
        first = yaml.safe_dump({"thresholds": {"fuzzy_threshold": 95}})
        second = yaml.safe_dump({"thresholds": {"fuzzy_threshold": 97}})
        rules_client.post("/v1/clients/acme_corp/rules", json={"rules_yaml": first})
        resp = rules_client.post("/v1/clients/acme_corp/rules", json={"rules_yaml": second})
        assert resp.status_code == 201
        assert resp.json()["version"] == 2

        client_dir = tmp_path / "config" / "clients" / "acme_corp"
        assert (client_dir / "rules_v1.yaml").read_text(encoding="utf-8") == first
        assert (client_dir / "rules_v2.yaml").read_text(encoding="utf-8") == second

    def test_saved_ruleset_is_immediately_active_via_get(self, rules_client):
        candidate = yaml.safe_dump({"thresholds": {"fuzzy_threshold": 95}})
        rules_client.post("/v1/clients/acme_corp/rules", json={"rules_yaml": candidate})

        resp = rules_client.get("/v1/clients/acme_corp/rules")
        assert resp.json()["thresholds"]["fuzzy_threshold"] == 95

    def test_invalid_candidate_is_422_and_writes_nothing(self, rules_client, tmp_path):
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules",
            json={"rules_yaml": "thresholds: [not, a, mapping]"},
        )
        assert resp.status_code == 422
        client_dir = tmp_path / "config" / "clients" / "acme_corp"
        assert not client_dir.exists()

    def test_scoped_key_cannot_save_rules_for_a_different_client(self, rules_client):
        resp = rules_client.post(
            "/v1/clients/client_b/rules",
            json={"rules_yaml": "thresholds: {}"},
            headers={"X-API-Key": SCOPED_API_KEY_A},
        )
        assert resp.status_code == 403

    def test_scoped_key_can_save_rules_for_its_own_client(self, rules_client):
        resp = rules_client.post(
            "/v1/clients/client_a/rules",
            json={"rules_yaml": "thresholds: {}"},
            headers={"X-API-Key": SCOPED_API_KEY_A},
        )
        assert resp.status_code == 201

    def test_requires_api_key(self, rules_client):
        resp = rules_client.post(
            "/v1/clients/acme_corp/rules",
            json={"rules_yaml": "thresholds: {}"},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401
