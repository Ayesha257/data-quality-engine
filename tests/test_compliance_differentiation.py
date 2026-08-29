"""
Compliance Framework Differentiation & Performance Verification Tests.

Validates that:
1. GDPR vs. CCPA execute distinct detection logic and output different findings.
   - GDPR captures: EU National IDs, DOB, Name + Location Linkage, etc.
   - CCPA captures: US Telephone numbers, CPRA Precise Geolocation, CCPA Unique Personal/Device IDs, etc.
2. Financial compliances (PCI-DSS, GLBA, SOX) are separate and output their distinct domain features.
3. Fast execution without blocking pauses.
"""

from __future__ import annotations

import time
import pandas as pd
import pytest

from backend.compliance.privacy_compliance import (
    run_privacy_scan,
    scan_gdpr_findings,
    scan_ccpa_findings,
)
from backend.compliance.financial_compliance import (
    run_compliance_scan,
    scan_pci_dss_findings,
    scan_glba_findings,
    scan_sox_findings,
)
from backend.engine.compliance.report import build_compliance_report_data, generate_compliance_html_report


@pytest.fixture
def multi_compliance_df():
    """Rich synthetic dataset with a mix of financial and privacy identifiers."""
    return pd.DataFrame({
        # GDPR-specific
        "national_id": ["12345-1234567-1", "23456-2345678-2", "34567-3456789-3"],
        "date_of_birth": ["1988-04-12", "1992-11-23", "2001-07-05"],
        "full_name": ["John Smith", "Alice Wonderland", "Bob Builder"],
        "city": ["London", "Paris", "Berlin"],
        
        # CCPA-specific
        "phone_number": ["(415) 555-1234", "(213) 555-6789", "(619) 555-9876"],
        "latitude": [37.7749, 34.0522, 32.7157],
        "longitude": [-122.4194, -118.2437, -117.1611],
        "device_id": ["dev_a1b2c3d4e5", "cookie_9988776655", "ad_id_x1y2z3"],

        # Shared (SSN, Email, IP)
        "ssn": ["123-45-6789", "987-65-4321", "555-55-5555"],
        "email": ["john@example.co.uk", "alice@california.gov", "bob@privacy.org"],
        "ip_address": ["192.168.1.1", "10.0.0.1", "172.16.0.1"],

        # Financial (PCI-DSS / GLBA / SOX)
        "card_number": ["4111111111111111", "5500000000000004", "4012888888881881"],
        "card_expiry": ["05/28", "12/26", "08/29"],
        "routing_number": ["011000015", "021000021", "121000358"],
        "created_by": ["admin_user", "service_acct", "analyst_1"],
        "created_at": ["2026-01-01 10:00:00", "2026-01-02 11:00:00", "2026-01-03 12:00:00"],
        "approved_by": ["manager_1", "auditor_2", "lead_3"],
        "modified_at": ["2026-01-04 14:00:00", "2026-01-05 15:00:00", "2026-01-06 16:00:00"],
    })


class TestPrivacyFrameworkDifferentiation:
    """Ensure GDPR and CCPA produce distinct, non-identical features and reports."""

    def test_gdpr_and_ccpa_produce_different_findings(self, multi_compliance_df):
        gdpr_res = scan_gdpr_findings(multi_compliance_df)
        ccpa_res = scan_ccpa_findings(multi_compliance_df)

        gdpr_rules = {f["rule"] for f in gdpr_res["high"] + gdpr_res["medium"]}
        ccpa_rules = {f["rule"] for f in ccpa_res["high"] + ccpa_res["medium"]}

        # GDPR-specific rules must be present in GDPR and absent from CCPA
        assert "gdpr_national_id" in gdpr_rules
        assert "gdpr_date_of_birth" in gdpr_rules
        assert "gdpr_full_name_geolocation" in gdpr_rules

        assert "ccpa_phone" not in gdpr_rules
        assert "ccpa_precise_geolocation" not in gdpr_rules
        assert "ccpa_unique_personal_identifier" not in gdpr_rules

        # CCPA-specific rules must be present in CCPA and absent from GDPR
        assert "ccpa_phone" in ccpa_rules
        assert "ccpa_precise_geolocation" in ccpa_rules
        assert "ccpa_unique_personal_identifier" in ccpa_rules

        assert "gdpr_national_id" not in ccpa_rules
        assert "gdpr_date_of_birth" not in ccpa_rules
        assert "gdpr_full_name_geolocation" not in ccpa_rules

        # Rules are different
        assert gdpr_rules != ccpa_rules

    def test_gdpr_and_ccpa_report_generation(self, multi_compliance_df, tmp_path):
        gdpr_data = build_compliance_report_data(
            filepath="test.csv",
            sheet_name="Sheet1",
            row_count=len(multi_compliance_df),
            column_count=multi_compliance_df.shape[1],
            regulation="GDPR",
            df=multi_compliance_df,
        )
        ccpa_data = build_compliance_report_data(
            filepath="test.csv",
            sheet_name="Sheet1",
            row_count=len(multi_compliance_df),
            column_count=multi_compliance_df.shape[1],
            regulation="CCPA",
            df=multi_compliance_df,
        )

        assert gdpr_data["regulation"] == "GDPR"
        assert ccpa_data["regulation"] == "CCPA"

        # Generate HTML files
        gdpr_html_path = tmp_path / "gdpr_report.html"
        ccpa_html_path = tmp_path / "ccpa_report.html"

        t0 = time.perf_counter()
        generate_compliance_html_report(gdpr_data, str(gdpr_html_path))
        generate_compliance_html_report(ccpa_data, str(ccpa_html_path))
        elapsed = time.perf_counter() - t0

        # Reports must render fast (< 1.5s total for both)
        assert elapsed < 1.5

        gdpr_content = gdpr_html_path.read_text(encoding="utf-8")
        ccpa_content = ccpa_html_path.read_text(encoding="utf-8")

        assert "GDPR Compliance Report" in gdpr_content
        assert "National Identity Number" in gdpr_content
        assert "Name and Geolocation Linkage" in gdpr_content

        assert "CCPA Compliance Report" in ccpa_content
        assert "Telephone Number" in ccpa_content
        assert "Precise Geolocation" in ccpa_content
        assert "Unique Personal Identifier" in ccpa_content


class TestFinancialFrameworkDifferentiation:
    """Ensure PCI-DSS, GLBA, and SOX execute distinct features."""

    def test_pci_dss_findings(self, multi_compliance_df):
        pci_res = scan_pci_dss_findings(multi_compliance_df)
        pci_rules = {f["rule"] for f in pci_res["high"] + pci_res["medium"]}

        assert "pci_pan" in pci_rules
        assert "pci_card_expiry" in pci_rules
        assert "glba_routing_number" not in pci_rules
        assert "sox_audit_trail_headers" not in pci_rules

    def test_glba_findings(self, multi_compliance_df):
        glba_res = scan_glba_findings(multi_compliance_df)
        glba_rules = {f["rule"] for f in glba_res["high"] + glba_res["medium"]}

        assert "glba_routing_number" in glba_rules
        assert "pci_pan" not in glba_rules
        assert "sox_audit_trail_headers" not in glba_rules

    def test_sox_findings(self, multi_compliance_df):
        sox_res = scan_sox_findings(multi_compliance_df)
        sox_rules = {f["rule"] for f in sox_res["high"] + sox_res["medium"]}

        assert "sox_audit_trail_headers" in sox_rules
        assert "pci_pan" not in sox_rules
        assert "glba_routing_number" not in sox_rules
