"""Tests for backend.compliance.glba_detectors.

Covers:
  * Routing Number: valid and invalid ABA checksums across multiple financial
    institutions, formatting variants (spaces/dashes/int/float inputs),
    masking correctness, and edge cases (None/NaN/empty/too short/too long).
  * Keyword columns: bank_account_number, loan_application_data,
    credit_history_data, and tax_return_data fuzzy matching across
    snake_case, camelCase, PascalCase, spaced, and abbreviated styles.
  * Low confidence assertion for all heuristic keyword matches.
  * CheckResult contract validation for compliance engine integration.
  * Genericity across two structurally different sample datasets.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.compliance.glba_detectors import (
    GLBA_KEYWORD_CATEGORIES,
    check_glba_compliance,
    check_glba_keyword_columns,
    check_glba_routing_numbers,
    classify_glba_keyword_columns,
    detect_routing_number,
    load_glba_rules,
)

# ---------------------------------------------------------------------------
# Valid ABA Routing Numbers across US Financial Institutions
# (Standard publicly known institution routing transit numbers)
# ---------------------------------------------------------------------------

VALID_ROUTING_NUMBERS = {
    "chase_ny": "021000021",
    "bofa_california": "121000358",
    "wells_fargo_sf": "121000248",
    "citibank_ny": "021000089",
    "fed_boston": "011000015",
    "fed_dallas": "111000025",
    "fed_chicago": "071000301",
    "pnc_bank": "043000096",
}

# Each invalid number derived by mutating the check digit to break mod-10
INVALID_CHECKSUM_ROUTING_NUMBERS = {
    "chase_bad": "021000022",
    "bofa_bad": "121000359",
    "wells_fargo_bad": "121000249",
    "citibank_bad": "021000080",
    "arbitrary_bad": "123456789",
}


# ===========================================================================
# 1. detect_routing_number
# ===========================================================================


class TestDetectRoutingNumber:
    @pytest.mark.parametrize("institution,number", list(VALID_ROUTING_NUMBERS.items()))
    def test_valid_aba_checksums_across_institutions(self, institution, number):
        result = detect_routing_number(number)
        assert result["match"] is True, f"{institution} should match"
        assert result["is_match"] is True
        assert result["confidence"] == "high"
        assert result["field_name"] == "routing_number"
        assert result["detection_type"] == "regex_checksum"
        assert result["reason"] == "checksum_valid"

    @pytest.mark.parametrize("institution,number", list(INVALID_CHECKSUM_ROUTING_NUMBERS.items()))
    def test_invalid_aba_checksums_rejected(self, institution, number):
        result = detect_routing_number(number)
        assert result["match"] is False, f"{institution} should NOT match"
        assert result["is_match"] is False
        assert result["confidence"] is None
        assert result["reason"] == "checksum_failed"

    def test_masked_value_shows_last_four_digits(self):
        result = detect_routing_number("121000358")
        assert result["masked_value"] == "*****0358"
        assert "121000358" != result["masked_value"]

    def test_dashed_and_spaced_formatting_detected(self):
        dashed = detect_routing_number("121-000-358")
        spaced = detect_routing_number("121 000 358")
        dotted = detect_routing_number("121.000.358")
        assert dashed["match"] is True
        assert spaced["match"] is True
        assert dotted["match"] is True
        assert dashed["masked_value"] == spaced["masked_value"] == "*****0358"

    def test_integer_input(self):
        result = detect_routing_number(121000358)
        assert result["match"] is True
        assert result["masked_value"] == "*****0358"

    def test_float_input_auto_typed_by_pandas(self):
        result = detect_routing_number(121000358.0)
        assert result["match"] is True
        assert result["masked_value"] == "*****0358"

    @pytest.mark.parametrize(
        "value",
        [None, float("nan"), float("inf"), pd.NA, pd.NaT],
    )
    def test_missing_values_never_match(self, value):
        result = detect_routing_number(value)
        assert result["match"] is False
        assert result["confidence"] is None
        assert result["reason"] == "empty_value"

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_empty_or_whitespace_string(self, value):
        result = detect_routing_number(value)
        assert result["match"] is False
        assert result["reason"] == "empty_value"

    def test_too_short_digits_rejected(self):
        # 8 digits
        result = detect_routing_number("12100035")
        assert result["match"] is False
        assert result["reason"] == "not_nine_digits"

    def test_too_long_digits_rejected(self):
        # 10 digits
        result = detect_routing_number("1210003580")
        assert result["match"] is False
        assert result["reason"] == "not_nine_digits"

    def test_non_numeric_text_rejected(self):
        result = detect_routing_number("ABCDEFGHI")
        assert result["match"] is False
        assert result["reason"] == "not_nine_digits"

    def test_supports_attribute_and_item_access(self):
        result = detect_routing_number("021000021")
        assert result.match is True
        assert result.is_match is True
        assert result.confidence == "high"
        assert result["match"] is True
        assert result["confidence"] == "high"


# ===========================================================================
# 2. classify_glba_keyword_columns
# ===========================================================================


class TestClassifyGlbaKeywordColumns:
    @pytest.mark.parametrize(
        "column_name",
        [
            "account_number",
            "AccountNumber",
            "accountNumber",
            "acct_no",
            "AcctNo",
            "acctNo",
            "acct_num",
            "bank_account",
            "BankAccount",
            "bank_acct",
            "BankAcctNumber",
            "routing_account",
            "checking_account",
            "CheckingAccount",
            "savings_account",
            "iban",
            "IBAN",
        ],
    )
    def test_bank_account_keyword_variants(self, column_name):
        matches = classify_glba_keyword_columns([column_name])
        assert column_name in matches["bank_account_number"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "loan_amount",
            "LoanAmount",
            "loanAmount",
            "loan_amt",
            "LoanAmt",
            "loan_application",
            "LoanApplication",
            "LoanApplicationAmount",
            "loan_application_amount",
            "principal_amount",
            "PrincipalAmt",
            "loan_term",
            "loan_type",
            "mortgage_amount",
            "MortgageAmount",
            "borrower_amount",
            "application_amount",
        ],
    )
    def test_loan_application_keyword_variants(self, column_name):
        matches = classify_glba_keyword_columns([column_name])
        assert column_name in matches["loan_application_data"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "credit_score",
            "CreditScore",
            "creditScore",
            "credit_history",
            "CreditHistory",
            "CreditHistoryScore",
            "credit_history_score",
            "fico_score",
            "FicoScore",
            "FICO",
            "fico",
            "credit_report",
            "credit_rating",
            "credit_bureau",
        ],
    )
    def test_credit_history_keyword_variants(self, column_name):
        matches = classify_glba_keyword_columns([column_name])
        assert column_name in matches["credit_history_data"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "tax_return",
            "TaxReturn",
            "tax_id",
            "TaxID",
            "TaxId",
            "tax_id_number",
            "agi",
            "AGI",
            "adjusted_gross_income",
            "AdjustedGrossIncome",
            "w2_income",
            "W2Income",
            "1099_income",
            "tax_filing",
            "tax_document",
            "filing_status",
            "FilingStatus",
        ],
    )
    def test_tax_return_keyword_variants(self, column_name):
        matches = classify_glba_keyword_columns([column_name])
        assert column_name in matches["tax_return_data"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "customer_id",
            "order_total",
            "product_name",
            "shipping_city",
            "country_code",
            "created_at",
            "notes",
            "description",
            "",
            None,
        ],
    )
    def test_unrelated_columns_do_not_match_any_category(self, column_name):
        matches = classify_glba_keyword_columns([column_name])
        for category, matched in matches.items():
            assert column_name not in matched, f"{column_name} should not match {category}"

    def test_check_glba_keyword_columns_returns_low_confidence_results(self):
        cols = ["account_number", "LoanAmount", "CreditScore", "TaxID"]
        results = check_glba_keyword_columns(cols)
        assert len(results) == 4
        for r in results:
            assert r.status == "failed"  # candidate regulated data present
            assert r.details["confidence"] == "low"
            assert r.details["regulation"] == "GLBA"
            assert r.details["method"] == "column_keyword"
            assert len(r.details["matched_columns"]) == 1


# ===========================================================================
# 3. GLBA CheckResult and Orchestration
# ===========================================================================


class TestGlbaCheckResults:
    def test_check_glba_routing_numbers_clean_column(self):
        df = pd.DataFrame({"order_id": [101, 102, 103], "quantity": [1, 2, 3]})
        res = check_glba_routing_numbers(df, "order_id")
        assert res.status == "passed"
        assert res.issues_found == 0
        assert res.quality_ratio == 1.0

    def test_check_glba_routing_numbers_flagged_column(self):
        df = pd.DataFrame(
            {
                "routing_code": [
                    "021000021",  # valid Chase
                    "121000358",  # valid BofA
                    "123456789",  # invalid
                ]
            }
        )
        res = check_glba_routing_numbers(df, "routing_code")
        assert res.status == "failed"
        assert res.issues_found == 2
        assert abs(res.quality_ratio - (1 / 3.0)) < 0.01

    def test_check_glba_compliance_orchestrator(self):
        df = pd.DataFrame(
            {
                "RoutingNumber": ["021000021", "121000358"],
                "BankAccountNumber": ["1234567890", "9876543210"],
                "LoanAmt": [250000.0, 500000.0],
                "CreditScore": [720, 810],
                "AGI": [95000.0, 140000.0],
                "CustomerName": ["Alice", "Bob"],
            }
        )
        results = check_glba_compliance(df)
        assert len(results) == len(df.columns) + 4  # 6 routing scans + 4 keyword categories
        failed_checks = [r for r in results if r.status == "failed"]
        assert len(failed_checks) >= 5


# ===========================================================================
# 4. Genericity Across Distinct Sample Datasets
# ===========================================================================


class TestGlbaGenericityAcrossDatasets:
    @pytest.fixture
    def dataset_retail_banking(self) -> pd.DataFrame:
        """Dataset A: Standard retail banking accounts export."""
        return pd.DataFrame(
            {
                "customer_id": ["CUST-001", "CUST-002", "CUST-003", "CUST-004"],
                "routing_number": [
                    "021000021",  # valid Chase
                    "121000358",  # valid BofA
                    "000000000",  # invalid
                    None,
                ],
                "bank_account_number": ["1122334455", "9988776655", "5544332211", None],
                "loan_application_amount": [50000.0, 120000.0, None, 15000.0],
                "credit_history_score": [680, 740, 590, None],
                "tax_id": ["12-3456789", "98-7654321", None, "45-6789012"],
                "account_status": ["ACTIVE", "ACTIVE", "DORMANT", "PENDING"],
            }
        )

    @pytest.fixture
    def dataset_mortgage_lending(self) -> pd.DataFrame:
        """Dataset B: Legacy mortgage lending export with PascalCase and float types."""
        return pd.DataFrame(
            {
                "AppID": [1001, 1002, 1003],
                "TransitRoutingNo": [
                    121000248.0,  # valid Wells Fargo (float auto-typed)
                    71000301.0,  # valid Fed Chicago
                    float("nan"),  # missing
                ],
                "CheckingAcct": ["CK-987", "CK-654", "CK-321"],
                "LoanAmt": [350000.0, 420000.0, 600000.0],
                "FICO": [750, 690, 820],
                "AdjustedGrossIncome": [115000.0, 98000.0, 185000.0],
                "EmptyCol": [None, None, None],
            }
        )

    def test_dataset_a_glba_analysis(self, dataset_retail_banking):
        # Routing number detection
        res = check_glba_routing_numbers(dataset_retail_banking, "routing_number")
        assert res.status == "failed"
        assert res.issues_found == 2

        # Keyword classification
        matches = classify_glba_keyword_columns(dataset_retail_banking.columns)
        assert "bank_account_number" in matches["bank_account_number"]
        assert "loan_application_amount" in matches["loan_application_data"]
        assert "credit_history_score" in matches["credit_history_data"]
        assert "tax_id" in matches["tax_return_data"]
        assert "account_status" not in matches["bank_account_number"]

    def test_dataset_b_glba_analysis(self, dataset_mortgage_lending):
        # Routing number numeric float detection
        res = check_glba_routing_numbers(dataset_mortgage_lending, "TransitRoutingNo")
        assert res.status == "failed"
        assert res.issues_found == 2

        # Abbreviated PascalCase keywords
        matches = classify_glba_keyword_columns(dataset_mortgage_lending.columns)
        assert "CheckingAcct" in matches["bank_account_number"]
        assert "LoanAmt" in matches["loan_application_data"]
        assert "FICO" in matches["credit_history_data"]
        assert "AdjustedGrossIncome" in matches["tax_return_data"]
        assert "EmptyCol" not in matches["loan_application_data"]

    def test_both_datasets_stateless_execution(
        self, dataset_retail_banking, dataset_mortgage_lending
    ):
        res_a = check_glba_compliance(dataset_retail_banking)
        res_b = check_glba_compliance(dataset_mortgage_lending)
        assert isinstance(res_a, list) and len(res_a) > 0
        assert isinstance(res_b, list) and len(res_b) > 0
