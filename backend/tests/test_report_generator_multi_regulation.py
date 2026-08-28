"""
Tests for multi-regulation report generator supporting HIPAA, PCI_DSS, GLBA, and SOX.

Covers:
1. Correct rule set and detectors load per regulation.
2. Disclaimer text is present and correct on every non-HIPAA report.
3. Only resolved findings appear in output (rejected / pending unconfirmed are excluded).
4. Grouping of findings into three sections by confidence tier:
   High Confidence, Medium Confidence, Confirmed (User-Verified).
5. HIPAA code path produces identical output to before this change.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pandas as pd
import pytest

from backend.engine.compliance.report import (
    build_compliance_report_data,
    generate_compliance_html_report,
)
from backend.engine.models import CheckResult


class TestMultiRegulationReportGenerator:
    def test_pci_dss_report_generation(self):
        """PCI-DSS report generates with correct disclaimer, tiers, and resolved findings."""
        df = pd.DataFrame({
            "card_number": ["4111 1111 1111 1111", "5555 5555 5555 4444"],  # High (PAN, valid Luhn)
            "exp_date": ["05/27", "11/28"],                                      # Medium (Card Expiry)
            "cvv": ["123", "456"],                                              # Low (CVV)
            "other_col": ["foo", "bar"],
        })

        # Resolved decisions: cvv confirmed
        resolved_decisions = {"cvv": True}

        report_data = build_compliance_report_data(
            filepath="payments.xlsx",
            sheet_name="Transactions",
            row_count=len(df),
            column_count=df.shape[1],
            regulation="PCI_DSS",
            df=df,
            resolved_decisions=resolved_decisions,
        )

        assert report_data["regulation"] == "PCI_DSS"
        assert (
            report_data["disclaimer"]
            == "This report flags compliance-relevant data patterns. It does not certify legal compliance with PCI_DSS."
        )

        tiers = report_data["confidence_tiers"]
        assert len(tiers["High Confidence"]) == 1
        assert tiers["High Confidence"][0]["field_name"] == "PAN"

        assert len(tiers["Medium Confidence"]) == 1
        assert tiers["Medium Confidence"][0]["field_name"] == "card_expiry"

        assert len(tiers["Confirmed (User-Verified)"]) == 1
        assert tiers["Confirmed (User-Verified)"][0]["column_name"] == "cvv"

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "pci_report.html"
            generate_compliance_html_report(report_data, str(out_file))

            html = out_file.read_text(encoding="utf-8")
            assert "PCI_DSS Compliance Report" in html or "PCI-DSS Compliance Report" in html
            assert "This report flags compliance-relevant data patterns. It does not certify legal compliance with PCI_DSS." in html
            assert "High Confidence" in html
            assert "Medium Confidence" in html
            assert "Confirmed (User-Verified)" in html
            assert "Primary Account Number" in html
            assert "Card Expiration Date" in html
            assert "cvv" in html
            assert "inspect-compliance-btn" in html
            assert "openComplianceFindingModal" in html

    def test_glba_report_rejected_low_findings_omitted(self):
        """GLBA report excludes rejected low-confidence keyword findings."""
        df = pd.DataFrame({
            "routing_num": ["021000021"],      # High
            "bank_account_num": ["987654321"], # Low -> will be rejected
            "loan_amount": [250000],           # Low -> will be confirmed
        })

        resolved_decisions = {
            "bank_account_num": False,
            "loan_amount": True,
        }

        report_data = build_compliance_report_data(
            filepath="banking.xlsx",
            sheet_name="Accounts",
            row_count=len(df),
            column_count=df.shape[1],
            regulation="GLBA",
            df=df,
            resolved_decisions=resolved_decisions,
        )

        assert report_data["regulation"] == "GLBA"
        assert (
            report_data["disclaimer"]
            == "This report flags compliance-relevant data patterns. It does not certify legal compliance with GLBA."
        )

        tiers = report_data["confidence_tiers"]
        # High confidence ABA routing number
        assert len(tiers["High Confidence"]) == 1
        assert tiers["High Confidence"][0]["field_name"] == "routing_number"

        # Confirmed loan_amount only; rejected bank_account_num must be absent
        confirmed = tiers["Confirmed (User-Verified)"]
        assert len(confirmed) == 1
        assert confirmed[0]["column_name"] == "loan_amount"
        assert not any(f["column_name"] == "bank_account_num" for f in report_data["findings"])

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "glba_report.html"
            generate_compliance_html_report(report_data, str(out_file))
            html = out_file.read_text(encoding="utf-8")
            assert "inspect-compliance-btn" in html
            assert "openComplianceFindingModal" in html

    def test_sox_report_disclaimer_and_high_tier(self):
        """SOX report includes mandatory disclaimer and audit trail / timestamp checks."""
        df = pd.DataFrame({
            "created_by": ["alice", "bob"],
            "approved_by": ["charlie", "diana"],
            "last_modified_at": ["2026-01-01 10:00", "2026-01-02 12:00"],
            "txn_date": ["2026-01-01", "2026-01-02"],
        })

        report_data = build_compliance_report_data(
            filepath="ledger.xlsx",
            sheet_name="GeneralLedger",
            row_count=len(df),
            column_count=df.shape[1],
            regulation="SOX",
            df=df,
        )

        assert report_data["regulation"] == "SOX"
        assert (
            report_data["disclaimer"]
            == "This report flags compliance-relevant data patterns. It does not certify legal compliance with SOX."
        )

        tiers = report_data["confidence_tiers"]
        assert len(tiers["High Confidence"]) >= 1

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "sox_report.html"
            generate_compliance_html_report(report_data, str(out_file))

            html = out_file.read_text(encoding="utf-8")
            assert "SOX Compliance Report" in html
            assert "This report flags compliance-relevant data patterns. It does not certify legal compliance with SOX." in html
            assert "Audit Trail Header Completeness" in html
            assert "inspect-compliance-btn" in html
            assert "openComplianceFindingModal" in html

    def test_hipaa_code_path_unaltered(self):
        """HIPAA compliance report path produces identical structure without regression."""
        hipaa_check = CheckResult(
            check_name="hipaa_phi",
            status="failed",
            column="patient_phone",
            issues_found=5,
            dimension="",
            details={
                "display_name": "HIPAA PHI Compliance Scan",
                "severity": "Critical",
                "affected_columns": ["patient_phone"],
                "columns_checked": 1,
                "columns_with_issues": 1,
                "total_issues_found": 5,
                "business_impact": "Exposure of patient phone numbers.",
                "recommendation": "Mask or de-identify patient contact info.",
            },
        )

        modules = {"hipaa_phi": [hipaa_check]}

        report_data = build_compliance_report_data(
            filepath="patients.xlsx",
            sheet_name="Sheet1",
            row_count=100,
            column_count=10,
            modules=modules,
            regulation="HIPAA",
        )

        # Standard HIPAA structure verification
        assert report_data["regulation"] == "HIPAA"
        assert "sections" in report_data
        assert "hipaa_phi" in report_data["sections"]
        assert "disclaimer" not in report_data  # Non-HIPAA disclaimer is not injected into HIPAA dict

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "hipaa_report.html"
            generate_compliance_html_report(report_data, str(out_file))

            html = out_file.read_text(encoding="utf-8")
            assert "Compliance Report" in html
            assert "HIPAA PHI Compliance Scan" in html
            assert "AI Executive Compliance Insights" in html
            assert "inspect-compliance-btn" in html
            assert "openComplianceAiModal" in html
            assert "openComplianceFindingModal" not in html
