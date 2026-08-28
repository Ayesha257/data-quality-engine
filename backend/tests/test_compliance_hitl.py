"""
Tests for Human-in-the-Loop (HITL) confirmation of low-confidence financial compliance findings.

Covers:
1. Run correctly pauses (AWAITING_CONFIRMATION) when a low-confidence finding is generated (CVV / GLBA keywords).
2. Confirm path: confirmed finding appears in final resolved results.
3. Reject path: rejected finding is discarded and does NOT appear in final results.
4. High/medium confidence findings (PAN, Card Expiry, Routing Number, SOX) bypass pause entirely.
5. Multiple simultaneous low-confidence findings in one run are all captured and resolved correctly.
6. APIPrompt lifecycle and submit_answer resolution.
"""

from __future__ import annotations

import threading
import time
import pandas as pd
import pytest

from backend.compliance.financial_compliance import (
    run_compliance_scan,
    scan_pci_dss_findings,
    scan_glba_findings,
    scan_sox_findings,
)
from backend.database import get_session, init_db
from backend.database.models import RunRecord, RunStatus
from backend.engine.api_prompt import APIPrompt, submit_answer


@pytest.fixture(autouse=True)
def ensure_db():
    init_db()


@pytest.fixture
def run_id_in_db():
    """Create a temporary RunRecord in database to test DB status transitions."""
    rid = f"test-run-{time.time_ns()}"
    with get_session() as session:
        run = RunRecord(
            id=rid,
            client_id="test_client",
            file_name="test_financial.xlsx",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
    yield rid
    with get_session() as session:
        r = session.get(RunRecord, rid)
        if r:
            session.delete(r)
            session.commit()


class TestComplianceHitlFlow:
    def test_low_confidence_cvv_triggers_pause(self, run_id_in_db):
        """A run correctly pauses when a low-confidence CVV column finding is generated."""
        df = pd.DataFrame({
            "customer_name": ["Alice", "Bob"],
            "cvv": ["123", "456"],
        })

        prompt = APIPrompt(run_id_in_db)
        prompt.set_context("Sheet1")

        # Start scan on worker thread because prompt.confirm_compliance will pause (block)
        scan_output = {}

        def worker():
            scan_output["result"] = run_compliance_scan(df, "PCI_DSS", prompt=prompt)

        t = threading.Thread(target=worker)
        t.start()

        # Wait briefly for thread to hit checkpoint and pause
        time.sleep(0.2)

        # Verify DB status is AWAITING_CONFIRMATION
        with get_session() as session:
            run = session.get(RunRecord, run_id_in_db)
            assert run.status == RunStatus.AWAITING_CONFIRMATION
            assert run.pending_confirmation is not None
            assert run.pending_confirmation.get("prompt_type") == "COMPLIANCE_COLUMN_CONFIRM"
            assert run.pending_confirmation.get("column_name") == "cvv"
            assert run.pending_confirmation.get("regulation") == "PCI_DSS"
            assert run.pending_confirmation.get("confidence") == "low"

        # Submit confirm decision
        resolved = submit_answer(
            run_id_in_db,
            {"decisions": [{"column_name": "cvv", "confirmed": True}]},
        )
        assert resolved is True

        t.join(timeout=3.0)
        assert not t.is_alive()

        # Verify final scan output includes confirmed finding
        res = scan_output["result"]
        assert len(res["confidence_tiers"]["Confirmed (User-Verified)"]) == 1
        assert res["confidence_tiers"]["Confirmed (User-Verified)"][0]["column_name"] == "cvv"
        assert res["confidence_tiers"]["Confirmed (User-Verified)"][0]["field_name"] == "cvv"

    def test_low_confidence_rejected_finding_is_discarded(self, run_id_in_db):
        """Reject path: rejected finding is discarded and does NOT appear in final resolved results."""
        df = pd.DataFrame({
            "customer_name": ["Alice", "Bob"],
            "security_code": ["123", "456"],
        })

        prompt = APIPrompt(run_id_in_db)
        prompt.set_context("Sheet1")
        scan_output = {}

        def worker():
            scan_output["result"] = run_compliance_scan(df, "PCI_DSS", prompt=prompt)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)

        # Submit reject decision
        submit_answer(
            run_id_in_db,
            {"decisions": [{"column_name": "security_code", "confirmed": False}]},
        )
        t.join(timeout=3.0)

        res = scan_output["result"]
        # Confirmed list must be empty
        assert len(res["confidence_tiers"]["Confirmed (User-Verified)"]) == 0
        assert len(res["resolved_findings"]) == 0

    def test_high_and_medium_confidence_findings_bypass_pause(self, run_id_in_db):
        """High (PAN, Routing Number, SOX) and medium (Card Expiry) findings bypass pause entirely."""
        df_pci = pd.DataFrame({
            "card_num": ["4111 1111 1111 1111"],  # High (PAN, valid Luhn)
            "exp_date": ["12/28"],                 # Medium (Card Expiry)
        })

        prompt = APIPrompt(run_id_in_db)
        # Should complete synchronously without blocking on worker thread
        res = run_compliance_scan(df_pci, "PCI_DSS", prompt=prompt)

        # Verify DB was never set to AWAITING_CONFIRMATION
        with get_session() as session:
            run = session.get(RunRecord, run_id_in_db)
            assert run.status == RunStatus.RUNNING
            assert run.pending_confirmation is None

        # Verify findings are resolved immediately
        assert len(res["confidence_tiers"]["High Confidence"]) == 1
        assert res["confidence_tiers"]["High Confidence"][0]["field_name"] == "PAN"
        assert len(res["confidence_tiers"]["Medium Confidence"]) == 1
        assert res["confidence_tiers"]["Medium Confidence"][0]["field_name"] == "card_expiry"
        assert len(res["confidence_tiers"]["Confirmed (User-Verified)"]) == 0

    def test_glba_routing_number_high_bypasses_pause(self, run_id_in_db):
        """GLBA ABA routing number is high confidence and bypasses HITL pause."""
        df_glba = pd.DataFrame({
            "aba_col": ["021000021"],  # Federal Reserve Bank of NY valid routing number
        })
        prompt = APIPrompt(run_id_in_db)
        res = run_compliance_scan(df_glba, "GLBA", prompt=prompt)

        with get_session() as session:
            run = session.get(RunRecord, run_id_in_db)
            assert run.status == RunStatus.RUNNING

        assert len(res["confidence_tiers"]["High Confidence"]) == 1
        assert res["confidence_tiers"]["High Confidence"][0]["field_name"] == "routing_number"

    def test_multiple_simultaneous_low_confidence_findings_resolved(self, run_id_in_db):
        """Multiple simultaneous low-confidence findings in one run are all captured and resolved correctly."""
        df = pd.DataFrame({
            "bank_account_num": ["12345678"],
            "loan_amount": [50000],
            "credit_score": [720],
            "tax_id": ["XX-XXXXXXX"],
        })

        prompt = APIPrompt(run_id_in_db)
        prompt.set_context("FinancialSheet")
        scan_output = {}

        def worker():
            scan_output["result"] = run_compliance_scan(df, "GLBA", prompt=prompt)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)

        # Check pending confirmation payload
        with get_session() as session:
            run = session.get(RunRecord, run_id_in_db)
            assert run.status == RunStatus.AWAITING_CONFIRMATION
            findings = run.pending_confirmation.get("findings", [])
            assert len(findings) == 4
            cols = {f["column_name"] for f in findings}
            assert cols == {"bank_account_num", "loan_amount", "credit_score", "tax_id"}

        # Confirm bank_account_num and tax_id, reject loan_amount and credit_score
        decisions = [
            {"column_name": "bank_account_num", "confirmed": True},
            {"column_name": "tax_id", "confirmed": True},
            {"column_name": "loan_amount", "confirmed": False},
            {"column_name": "credit_score", "confirmed": False},
        ]
        submit_answer(run_id_in_db, {"decisions": decisions})
        t.join(timeout=3.0)

        res = scan_output["result"]
        confirmed = res["confidence_tiers"]["Confirmed (User-Verified)"]
        assert len(confirmed) == 2
        confirmed_cols = {f["column_name"] for f in confirmed}
        assert confirmed_cols == {"bank_account_num", "tax_id"}

    def test_direct_api_prompt_instantiation(self, run_id_in_db):
        """APIPrompt initialized directly with COMPLIANCE_COLUMN_CONFIRM behaves accurately."""
        prompt = APIPrompt(
            run_id=run_id_in_db,
            prompt_type="COMPLIANCE_COLUMN_CONFIRM",
            column_name="cvv",
            guessed_field="cvv",
            regulation="PCI_DSS",
            confidence="low",
        )

        decisions_out = {}

        def worker():
            decisions_out["decisions"] = prompt.confirm_compliance()

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)

        with get_session() as session:
            run = session.get(RunRecord, run_id_in_db)
            assert run.status == RunStatus.AWAITING_CONFIRMATION
            assert run.pending_confirmation["prompt_type"] == "COMPLIANCE_COLUMN_CONFIRM"
            assert run.pending_confirmation["column_name"] == "cvv"
            assert run.pending_confirmation["regulation"] == "PCI_DSS"

        submit_answer(run_id_in_db, {"decisions": [{"column_name": "cvv", "confirmed": True}]})
        t.join(timeout=3.0)

        assert decisions_out["decisions"] == {"cvv": True}


class TestComplianceRestEndpoints:
    def test_compliance_confirm_and_pending_endpoints(self, run_id_in_db):
        """Test GET /compliance-confirmations and POST /compliance-confirm."""
        from fastapi.testclient import TestClient
        from backend.app import app
        from backend.tests.conftest import bearer_headers

        client = TestClient(app)
        headers = bearer_headers("test_client")

        prompt = APIPrompt(run_id_in_db)
        prompt.set_context("Sheet1")
        decisions_out = {}

        def worker():
            df = pd.DataFrame({"cvv": ["123", "456"]})
            decisions_out["res"] = run_compliance_scan(df, "PCI_DSS", prompt=prompt)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)

        # GET pending confirmations
        resp = client.get(f"/v1/runs/{run_id_in_db}/compliance-confirmations", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending"] is True
        assert data["prompt_type"] == "COMPLIANCE_COLUMN_CONFIRM"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["column_name"] == "cvv"

        # POST confirm decisions
        post_resp = client.post(
            f"/v1/runs/{run_id_in_db}/compliance-confirm",
            json={"decisions": [{"column_name": "cvv", "confirmed": True}]},
            headers=headers,
        )
        assert post_resp.status_code == 200
        post_data = post_resp.json()
        assert post_data["status"] == "running"
        assert post_data["resolved_count"] == 1

        t.join(timeout=3.0)
        assert "res" in decisions_out

    def test_compliance_confirm_on_non_awaiting_returns_409(self, run_id_in_db):
        from fastapi.testclient import TestClient
        from backend.app import app
        from backend.tests.conftest import bearer_headers

        client = TestClient(app)
        headers = bearer_headers("test_client")

        post_resp = client.post(
            f"/v1/runs/{run_id_in_db}/compliance-confirm",
            json={"decisions": [{"column_name": "cvv", "confirmed": True}]},
            headers=headers,
        )
        assert post_resp.status_code == 409
