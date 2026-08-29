"""
Unit and integration tests for Privacy compliance detectors (GDPR & CCPA).

Covers:
- Valid/invalid SSN ranges (area 000/666/900-999, group 00, serial 0000, masking)
- Valid/invalid IPv4 and IPv6 addresses
- DOB true positives and explicit false-positive rejection for generic date columns
- Name + Geolocation linkage co-occurrence across varied column naming styles
- National ID (CNIC) and Email reuse
- End-to-end multi-dataset scans for GDPR and CCPA
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.compliance.privacy_compliance import (
    run_privacy_scan,
    scan_ccpa_findings,
    scan_gdpr_findings,
    scan_privacy_findings,
)
from backend.compliance.privacy_detectors import (
    check_ccpa_compliance,
    check_gdpr_compliance,
    check_privacy_compliance,
    check_privacy_dob,
    check_privacy_ip_address,
    check_privacy_name_geolocation,
    check_privacy_ssn,
    detect_dob_candidate,
    detect_email,
    detect_ip_address,
    detect_name_geolocation_columns,
    detect_national_id,
    detect_ssn,
    load_privacy_rules,
)
from backend.engine.models import CheckResult


class TestSSNDetector:
    """Tests for detect_ssn."""

    @pytest.mark.parametrize(
        "val,expected_masked",
        [
            ("123-45-6789", "***-**-6789"),
            ("012-34-5678", "***-**-5678"),
            ("899-12-3456", "***-**-3456"),
            ("123 45 6789", "***-**-6789"),
            ("123456789", "***-**-6789"),
            (123456789, "***-**-6789"),
        ],
    )
    def test_valid_ssn(self, val, expected_masked):
        res = detect_ssn(val)
        assert res["match"] is True
        assert res["confidence"] == "high"
        assert res["masked_value"] == expected_masked

    @pytest.mark.parametrize(
        "invalid_ssn",
        [
            "000-12-3456",  # Area 000 invalid
            "666-12-3456",  # Area 666 invalid
            "900-12-3456",  # Area 900+ invalid
            "950-12-3456",  # Area 950 invalid
            "999-12-3456",  # Area 999 invalid
            "123-00-4567",  # Group 00 invalid
            "123-45-0000",  # Serial 0000 invalid
            "12-345-6789",  # Malformed pattern
            "12345",  # Too short
            "12345678901",  # Too long
            "abc-de-fghi",  # Non-numeric
            "123.45.6789",  # Invalid separator
            None,
            "",
            float("nan"),
        ],
    )
    def test_invalid_ssn(self, invalid_ssn):
        res = detect_ssn(invalid_ssn)
        assert res["match"] is False


class TestIPAddressDetector:
    """Tests for detect_ip_address."""

    @pytest.mark.parametrize(
        "val,ip_version",
        [
            ("192.168.1.1", "IPv4"),
            ("10.0.0.1", "IPv4"),
            ("172.16.254.1", "IPv4"),
            ("8.8.8.8", "IPv4"),
            ("255.255.255.255", "IPv4"),
            ("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "IPv6"),
            ("::1", "IPv6"),
            ("fe80::1ff:fe23:4567:890a", "IPv6"),
        ],
    )
    def test_valid_ip(self, val, ip_version):
        res = detect_ip_address(val)
        assert res["match"] is True
        assert res["confidence"] == "high"
        assert res["ip_version"] == ip_version

    @pytest.mark.parametrize(
        "invalid_ip",
        [
            "256.1.1.1",  # Octet > 255
            "1.2.3.4.5",  # 5 octets
            "192.168.1",  # 3 octets
            "v1.2.3.4",  # Version string
            "1.0.0",  # Semver
            "999.999.999.999",  # Invalid octets
            "abc.def.ghi.jkl",
            "",
            None,
            12345,
            float("nan"),
        ],
    )
    def test_invalid_ip(self, invalid_ip):
        res = detect_ip_address(invalid_ip)
        assert res["match"] is False


class TestDOBDetector:
    """Tests for detect_dob_candidate."""

    @pytest.mark.parametrize(
        "col_name,val",
        [
            ("dob", "1990-05-15"),
            ("DOB", "05/15/1990"),
            ("birth_date", "1985-12-01"),
            ("Birth Date", "Dec 01, 1985"),
            ("date_of_birth", "2000-01-20"),
            ("Date of Birth", "20/01/2000"),
            ("birthday", "1995-07-30"),
            ("birth_dt", "1992-03-14"),
        ],
    )
    def test_dob_true_positive(self, col_name, val):
        res = detect_dob_candidate(val, col_name)
        assert res["match"] is True
        assert res["confidence"] == "medium"
        assert res["field_name"] == "date_of_birth"

    @pytest.mark.parametrize(
        "col_name,val",
        [
            ("created_at", "1990-05-15"),
            ("create_date", "1990-05-15"),
            ("creation_date", "1990-05-15"),
            ("updated_at", "1990-05-15"),
            ("modified_at", "1990-05-15"),
            ("transaction_date", "2024-01-01"),
            ("order_date", "2023-11-20"),
            ("invoice_date", "2023-11-20"),
            ("ship_date", "2023-11-20"),
            ("delivery_date", "2023-11-20"),
            ("due_date", "2023-11-20"),
            ("start_date", "2023-11-20"),
            ("end_date", "2023-11-20"),
            ("posting_date", "2023-11-20"),
            ("eff_date", "2023-11-20"),
        ],
    )
    def test_dob_false_positive_rejection_for_generic_dates(self, col_name, val):
        """Generic dates must NEVER match as DOB."""
        res = detect_dob_candidate(val, col_name)
        assert res["match"] is False

    def test_dob_invalid_date_in_dob_col(self):
        res = detect_dob_candidate("not-a-date", "date_of_birth")
        assert res["match"] is False


class TestNameGeolocationDetector:
    """Tests for detect_name_geolocation_columns."""

    @pytest.mark.parametrize(
        "columns",
        [
            ["first_name", "last_name", "city", "country"],
            ["customer_name", "latitude", "longitude"],
            ["full_name", "address_line_1", "zip_code"],
            ["contact_name", "postal_code", "state"],
            ["patient_name", "street", "city"],
            ["user_name", "geo_location"],
        ],
    )
    def test_name_geo_linkage_positive(self, columns):
        res = detect_name_geolocation_columns(columns)
        assert res["match"] is True
        assert res["confidence"] == "medium"
        assert len(res["name_columns"]) >= 1
        assert len(res["geo_columns"]) >= 1

    @pytest.mark.parametrize(
        "columns",
        [
            ["first_name", "last_name", "age", "gender"],  # Name only, no geo
            ["city", "state", "zip", "country"],  # Geo only, no name
            ["company_name", "city", "state"],  # Company name (not person), no match
            ["product_name", "warehouse_address"],  # Product name (not person)
            ["file_name", "table_name", "coordinates"],  # File name (not person)
            [],
        ],
    )
    def test_name_geo_linkage_negative(self, columns):
        res = detect_name_geolocation_columns(columns)
        assert res["match"] is False


class TestNationalIDAndEmailReused:
    """Tests for detect_national_id and detect_email."""

    def test_national_id_cnic(self):
        res = detect_national_id("35201-1234567-1")
        assert res["match"] is True
        assert res["confidence"] == "high"
        assert res["masked_value"] == "35201-*******-1"

    def test_email(self):
        res = detect_email("john.doe@example.com")
        assert res["match"] is True
        assert res["confidence"] == "high"
        assert "jo***@example.com" in res["masked_value"]


class TestPrivacyComplianceScans:
    """End-to-end dataset scan and HITL verification tests."""

    def test_rules_configuration_loaded(self):
        gdpr_rules = load_privacy_rules("GDPR")
        assert gdpr_rules["full_name"] == "General Data Protection Regulation"
        assert len(gdpr_rules["rules"]) >= 6

        ccpa_rules = load_privacy_rules("CCPA")
        assert "California" in ccpa_rules["full_name"]
        assert len(ccpa_rules["rules"]) >= 6

    def test_dataset_a_customer_profile(self):
        """Dataset 1: Customer profiles with SSN, Email, DOB, and Address."""
        df = pd.DataFrame({
            "full_name": ["Alice Smith", "Bob Jones", "Charlie Brown"],
            "ssn": ["123-45-6789", "987-65-4321", "456-78-1234"],
            "email": ["alice@test.com", "bob@test.com", "charlie@test.com"],
            "dob": ["1990-01-15", "1985-05-20", "1978-11-30"],
            "ip_address": ["192.168.1.50", "10.0.0.12", "172.16.0.4"],
            "city": ["Seattle", "Austin", "Boston"],
        })

        res = scan_gdpr_findings(df)
        assert len(res["high"]) == 3  # SSN, Email, IP Address
        assert len(res["medium"]) == 2  # DOB, Name+Geo linkage

        # Verify automated dual-gated flow: high and medium are auto-included without pipeline pauses
        scan_res = run_privacy_scan(df, regulation="GDPR")
        assert len(scan_res["resolved_findings"]) == 5  # 3 high + 2 medium auto-included
        assert len(scan_res["confidence_tiers"]["High Confidence"]) == 3
        assert len(scan_res["confidence_tiers"]["Medium Confidence"]) == 2

    def test_dataset_b_ecommerce_transactions(self):
        """Dataset 2: Transactions with created_at, IP, customer_name, and no DOB."""
        df = pd.DataFrame({
            "transaction_id": ["TXN-101", "TXN-102", "TXN-103"],
            "customer_name": ["David Clark", "Emma Watson", "Frank Miller"],
            "created_at": ["2024-01-10", "2024-01-11", "2024-01-12"],
            "ip_address": ["198.51.100.42", "203.0.113.195", "192.0.2.1"],
            "delivery_address": ["123 Main St", "456 Oak Ave", "789 Pine Rd"],
        })

        res = scan_ccpa_findings(df)
        assert len(res["high"]) == 1  # IP Address
        # CCPA does not flag created_at as date of birth
        ccpa_rules = [f["rule"] for f in res["high"] + res["medium"]]
        assert "ccpa_ip_address" in ccpa_rules
        assert "ccpa_date_of_birth" not in ccpa_rules


class TestPrivacyCheckResultWrappers:
    """Tests for CheckResult return contract functions."""

    def test_check_privacy_ssn_pass_and_fail(self):
        df_clean = pd.DataFrame({"clean": ["abc", "123"]})
        res_clean = check_privacy_ssn(df_clean, "clean", regulation="GDPR")
        assert isinstance(res_clean, CheckResult)
        assert res_clean.status == "passed"
        assert res_clean.issues_found == 0

        df_dirty = pd.DataFrame({"ssn": ["123-45-6789", "clean"]})
        res_dirty = check_privacy_ssn(df_dirty, "ssn", regulation="GDPR")
        assert isinstance(res_dirty, CheckResult)
        assert res_dirty.status == "failed"
        assert res_dirty.issues_found == 1

    def test_check_privacy_ip_address_pass_and_fail(self):
        df = pd.DataFrame({"ip": ["192.168.1.1", "invalid_ip"]})
        res = check_privacy_ip_address(df, "ip", regulation="CCPA")
        assert isinstance(res, CheckResult)
        assert res.status == "failed"
        assert res.issues_found == 1

    def test_check_privacy_dob_gated_and_rejected(self):
        df_dob = pd.DataFrame({"dob": ["1990-05-15"]})
        res = check_privacy_dob(df_dob, "dob", regulation="GDPR")
        assert isinstance(res, CheckResult)
        assert res.status == "failed"
        assert res.issues_found == 1

        df_created = pd.DataFrame({"created_at": ["1990-05-15"]})
        res_created = check_privacy_dob(df_created, "created_at", regulation="GDPR")
        assert res_created.status == "passed"
        assert res_created.issues_found == 0

    def test_check_privacy_name_geolocation(self):
        res = check_privacy_name_geolocation(["first_name", "city"], regulation="GDPR")
        assert isinstance(res, CheckResult)
        assert res.status == "failed"
        assert res.issues_found == 1

        res_no = check_privacy_name_geolocation(["first_name", "age"], regulation="GDPR")
        assert res_no.status == "passed"
        assert res_no.issues_found == 0

    def test_check_gdpr_and_ccpa_compliance_orchestrators(self):
        df = pd.DataFrame({
            "full_name": ["Alice"],
            "ssn": ["123-45-6789"],
            "city": ["Seattle"],
        })
        gdpr_results = check_gdpr_compliance(df)
        assert isinstance(gdpr_results, list)
        assert len(gdpr_results) > 0
        assert all(isinstance(r, CheckResult) for r in gdpr_results)

        ccpa_results = check_ccpa_compliance(df)
        assert isinstance(ccpa_results, list)
        assert len(ccpa_results) > 0
        assert all(isinstance(r, CheckResult) for r in ccpa_results)

