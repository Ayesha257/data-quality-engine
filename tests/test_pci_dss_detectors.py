"""Tests for backend.compliance.pci_dss_detectors.

Covers:
  * PAN: valid/invalid Luhn checksums across multiple card networks,
    formatting variants (spaces/dashes/embedded text/int/float inputs),
    masking correctness, and edge cases (None/NaN/empty/too short/too long).
  * card_expiry: valid/invalid MM/YY formats gated by fuzzy column-name
    matching across snake_case/camelCase/abbreviated variants.
  * cvv: column-name-only matching across snake_case/camelCase/abbreviated
    styles, with negative cases to prove no false positives.
  * Genericity: the same functions run correctly, unmodified, against two
    structurally different sample datasets (different column names,
    shapes, dtypes, casing conventions, and null patterns).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backend.compliance.pci_dss_detectors import (
    detect_card_expiry,
    detect_cvv_column,
    detect_pan,
    load_pci_dss_rules,
)

# ---------------------------------------------------------------------------
# Shared fixtures: well-known Luhn-valid test card numbers across networks
# (these are the standard publicly documented dummy/test numbers used by
# payment processors such as Stripe/PayPal sandboxes -- not real accounts).
# ---------------------------------------------------------------------------

VALID_CARDS = {
    "visa_16": "4111111111111111",
    "visa_16_alt": "4012888888881881",
    "mastercard_16": "5555555555554444",
    "amex_15": "378282246310005",
    "discover_16": "6011111111111117",
    "diners_14": "30569309025904",
    "jcb_16": "3530111333300000",
}

# Each derived by incrementing the last digit of a valid number by 1
# (mod 10), which always breaks the Luhn checksum.
INVALID_LUHN_CARDS = {
    "visa_16_bad": "4111111111111112",
    "mastercard_16_bad": "5555555555554445",
    "amex_15_bad": "378282246310006",
    "discover_16_bad": "6011111111111118",
}


# ===========================================================================
# detect_pan
# ===========================================================================


class TestDetectPan:
    @pytest.mark.parametrize("network,number", list(VALID_CARDS.items()))
    def test_valid_luhn_numbers_across_networks(self, network, number):
        result = detect_pan(number)
        assert result["match"] is True, f"{network} should match"
        assert result["confidence"] == "high"
        assert result["field_name"] == "PAN"
        assert result["detection_type"] == "regex_checksum"
        assert result["pan_length"] == len(number)

    @pytest.mark.parametrize("network,number", list(INVALID_LUHN_CARDS.items()))
    def test_invalid_luhn_numbers_across_networks(self, network, number):
        result = detect_pan(number)
        assert result["match"] is False, f"{network} should NOT match"
        assert result["confidence"] is None
        assert result["masked_value"] is None

    def test_masked_value_only_shows_last_four_digits(self):
        result = detect_pan("4111111111111111")
        assert result["masked_value"] == "**** **** **** 1111"
        # The full PAN must never appear anywhere in the result.
        assert "4111111111111111" not in str(result)
        for v in result.values():
            assert v != "4111111111111111"

    def test_spaced_and_dashed_formatting_both_detected(self):
        spaced = detect_pan("4111 1111 1111 1111")
        dashed = detect_pan("4111-1111-1111-1111")
        assert spaced["match"] is True
        assert dashed["match"] is True
        assert spaced["masked_value"] == dashed["masked_value"] == "**** **** **** 1111"

    def test_pan_embedded_in_free_text_is_found(self):
        result = detect_pan("Card on file: 4111111111111111 (primary)")
        assert result["match"] is True
        assert result["masked_value"] == "**** **** **** 1111"

    def test_integer_input(self):
        result = detect_pan(4111111111111111)
        assert result["match"] is True
        assert result["masked_value"] == "**** **** **** 1111"

    def test_float_input_whole_number_no_artifact(self):
        # Simulates pandas inferring a numeric dtype for a card-number column.
        result = detect_pan(4111111111111111.0)
        assert result["match"] is True
        assert result["masked_value"] == "**** **** **** 1111"

    @pytest.mark.parametrize(
        "value",
        [None, float("nan"), pd.NA, pd.NaT],
    )
    def test_missing_values_never_match(self, value):
        result = detect_pan(value)
        assert result["match"] is False
        assert result["confidence"] is None

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_empty_or_whitespace_only_string(self, value):
        result = detect_pan(value)
        assert result["match"] is False

    def test_too_short_digit_run_rejected(self):
        # 12 digits: below the 13-digit PCI minimum.
        result = detect_pan("123456789012")
        assert result["match"] is False

    def test_too_long_digit_run_rejected(self):
        # 20 contiguous digits: above the 19-digit PCI maximum, and no
        # 13-19 digit substring can be boundary-safe inside it.
        result = detect_pan("12345678901234567890")
        assert result["match"] is False

    def test_non_numeric_text_rejected(self):
        result = detect_pan("abcdefg-not-a-card")
        assert result["match"] is False

    def test_valid_length_but_failing_checksum_rejected(self):
        # 16 digits, correct length, deliberately bad checksum.
        result = detect_pan("1234567890123456")
        assert result["match"] is False

    def test_mixed_alpha_numeric_free_text_no_false_positive(self):
        result = detect_pan("Order ID: ORD-2024-0001-99887766")
        # Not a 13-19 digit boundary-safe run, so no match expected.
        assert result["match"] is False

    def test_does_not_mutate_input(self):
        original = "4111111111111111"
        detect_pan(original)
        assert original == "4111111111111111"


# ===========================================================================
# detect_card_expiry
# ===========================================================================


class TestDetectCardExpiry:
    @pytest.mark.parametrize(
        "column_name",
        [
            "expiry",
            "Expiry",
            "expiry_date",
            "ExpiryDate",
            "exp_date",
            "expDate",
            "ExpDate",
            "EXP_DATE",
            "valid_thru",
            "ValidThru",
            "validThru",
            "valid_through",
            "card_expiry",
            "cardExpiry",
            "CardExpiryDate",
            "cc_exp",
            "ccExp",
            "expiration",
            "expiration_date",
            "Card Expiry Date",
        ],
    )
    def test_column_name_variants_match(self, column_name):
        result = detect_card_expiry("12/25", column_name)
        assert result["column_matched"] is True, column_name
        assert result["match"] is True, column_name
        assert result["confidence"] == "medium"

    @pytest.mark.parametrize(
        "column_name",
        [
            "notes",
            "description",
            "customer_name",
            "experience_years",
            "expense_amount",
            "amount",
            "order_date",
            "id",
        ],
    )
    def test_unrelated_column_names_do_not_match(self, column_name):
        result = detect_card_expiry("12/25", column_name)
        assert result["column_matched"] is False, column_name
        assert result["match"] is False, column_name

    @pytest.mark.parametrize(
        "value",
        [
            "12/25",
            "01/99",
            "12-25",
            " 12 / 25 ",
            "01/2025",
            "12/2099",
        ],
    )
    def test_valid_expiry_formats(self, value):
        result = detect_card_expiry(value, "expiry_date")
        assert result["match"] is True, value
        assert result["confidence"] == "medium"

    @pytest.mark.parametrize(
        "value",
        [
            "13/25",  # invalid month
            "00/25",  # invalid month
            "25/12",  # swapped month/day
            "1225",  # no separator
            "12/2",  # incomplete year
            "AB/CD",  # non-numeric
            "12//25",  # malformed separator
            "",
            "   ",
        ],
    )
    def test_invalid_expiry_formats(self, value):
        result = detect_card_expiry(value, "expiry_date")
        assert result["match"] is False, value

    @pytest.mark.parametrize("value", [None, float("nan"), pd.NA])
    def test_missing_values_never_match_even_with_good_column(self, value):
        result = detect_card_expiry(value, "expiry_date")
        assert result["match"] is False
        assert result["column_matched"] is True

    def test_valid_value_with_bad_column_name_is_not_flagged(self):
        # Value alone is never sufficient -- column name is the gate.
        result = detect_card_expiry("12/25", "model_year")
        assert result["match"] is False
        assert result["column_matched"] is False

    def test_normalized_value_uses_two_digit_year(self):
        result = detect_card_expiry("01/2025", "expiry_date")
        assert result["normalized_value"] == "01/25"

    def test_none_column_name_never_matches(self):
        result = detect_card_expiry("12/25", None)
        assert result["column_matched"] is False
        assert result["match"] is False


# ===========================================================================
# detect_cvv_column
# ===========================================================================


class TestDetectCvvColumn:
    @pytest.mark.parametrize(
        "column_name",
        [
            "cvv",
            "CVV",
            "Cvv",
            "cvv_number",
            "cvvNumber",
            "CVVNumber",
            "cvv2",
            "CVV2",
            "cvc",
            "CVC",
            "cvc2",
            "CVC2",
            "card_cvc",
            "cardCvc",
            "CardCVC",
            "security_code",
            "SecurityCode",
            "securityCode",
            "SECURITY_CODE",
            "csc",
            "CSC",
            "cvn",
            "CVN",
            "card_verification_value",
            "cardVerificationCode",
        ],
    )
    def test_cvv_column_name_variants_match(self, column_name):
        result = detect_cvv_column(column_name)
        assert result["match"] is True, column_name
        assert result["confidence"] == "low"
        assert result["field_name"] == "cvv"
        assert result["detection_type"] == "column_keyword"

    @pytest.mark.parametrize(
        "column_name",
        [
            "customer_id",
            "description",
            "expiry_date",
            "amount",
            "csc_report",  # contains "csc" but not as the whole name
            "customer_service_center",
            "notes",
            "product_name",
            "",
            "   ",
            None,
        ],
    )
    def test_non_cvv_column_names_do_not_match(self, column_name):
        result = detect_cvv_column(column_name)
        assert result["match"] is False, column_name
        assert result["confidence"] is None

    def test_never_inspects_row_values(self):
        # Function signature only accepts a column name -- there is no
        # value parameter, which structurally guarantees CVV values are
        # never read or stored by this detector.
        import inspect

        sig = inspect.signature(detect_cvv_column)
        assert list(sig.parameters.keys()) == ["column_name"]


# ===========================================================================
# compliance_rules.json <-> code consistency
# ===========================================================================


class TestComplianceRulesConfig:
    def test_pci_dss_rules_load_and_have_three_entries(self):
        framework = load_pci_dss_rules()
        rules = framework["rules"]
        assert len(rules) == 3
        field_names = {r["field_name"] for r in rules}
        assert field_names == {"PAN", "card_expiry", "cvv"}

    def test_rule_confidences_match_spec(self):
        framework = load_pci_dss_rules()
        by_field = {r["field_name"]: r for r in framework["rules"]}
        assert by_field["PAN"]["confidence"] == "high"
        assert by_field["PAN"]["detection_type"] == "regex_checksum"
        assert by_field["card_expiry"]["confidence"] == "medium"
        assert by_field["card_expiry"]["detection_type"] == "regex"
        assert by_field["cvv"]["confidence"] == "low"
        assert by_field["cvv"]["detection_type"] == "column_keyword"

    def test_stub_frameworks_present_and_empty(self):
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "config"
            / "compliance_rules.json"
        )
        if not path.is_file():
            path = (
                Path(__file__).resolve().parents[1]
                / "backend"
                / "config"
                / "compliance_rules.json"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        for stub in ("GDPR", "CCPA"):
            assert stub in data["frameworks"]
            assert data["frameworks"][stub]["rules"] == []
        for populated in ("PCI_DSS", "GLBA", "SOX"):
            assert populated in data["frameworks"]
            assert len(data["frameworks"][populated]["rules"]) > 0


# ===========================================================================
# Genericity: two structurally different sample datasets
# ===========================================================================


def _run_pan_scan(df: pd.DataFrame, column: str) -> list[dict]:
    return [detect_pan(v) for v in df[column]]


def _run_expiry_scan(df: pd.DataFrame, column: str) -> list[dict]:
    return [detect_card_expiry(v, column) for v in df[column]]


class TestGenericityAcrossDatasets:
    """Proves the detectors are not tuned to one specific dataset shape.

    Dataset A: an e-commerce "orders" export -- snake_case columns,
    string-typed card numbers, some nulls, mixed real/invalid data.

    Dataset B: a legacy banking "transactions" export -- differently
    cased/abbreviated columns, numeric (float) card numbers courtesy of
    Excel/pandas auto-typing, a completely empty column, and no PCI
    columns at all in one table to prove no false positives leak in.
    """

    @pytest.fixture
    def dataset_a_orders(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "order_id": ["O-1001", "O-1002", "O-1003", "O-1004", "O-1005"],
                "customer_name": ["Ayesha", "Bilal", "Chen", "Diego", "Elena"],
                "card_number": [
                    "4111 1111 1111 1111",  # valid Visa
                    "5555555555554444",  # valid Mastercard
                    "1234567890123456",  # invalid checksum
                    None,  # null
                    "378282246310005",  # valid Amex (15 digits)
                ],
                "card_expiry_date": ["12/25", "01/26", "07/24", None, "13/25"],
                "cvv_number": ["123", "456", "789", None, "1234"],
                "order_total": [199.99, 45.50, 12.00, 300.00, 89.99],
            }
        )

    @pytest.fixture
    def dataset_b_transactions(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "TxnID": [1, 2, 3, 4],
                "PAN": [
                    4111111111111111.0,  # numeric dtype (Excel auto-typed)
                    6011111111111117.0,  # valid Discover
                    3530111333300000.0,  # valid JCB
                    float("nan"),  # missing
                ],
                "ValidThru": ["11/27", "02/28", "bad-date", None],
                "CSC": ["321", "654", "987", None],
                "EmptyColumn": [None, None, None, None],
                "AccountHolder": ["Ali Khan", "Fatima N.", "R. Gomez", "S. Iqbal"],
            }
        )

    def test_dataset_a_pan_detection(self, dataset_a_orders):
        results = _run_pan_scan(dataset_a_orders, "card_number")
        matches = [r["match"] for r in results]
        assert matches == [True, True, False, False, True]
        assert results[0]["masked_value"] == "**** **** **** 1111"

    def test_dataset_a_expiry_detection(self, dataset_a_orders):
        results = _run_expiry_scan(dataset_a_orders, "card_expiry_date")
        matches = [r["match"] for r in results]
        # 12/25 valid, 01/26 valid, 07/24 valid, None missing, 13/25 invalid month
        assert matches == [True, True, True, False, False]
        assert all(r["column_matched"] for r in results)

    def test_dataset_a_cvv_column_flagged(self, dataset_a_orders):
        result = detect_cvv_column("cvv_number")
        assert result["match"] is True
        # Non-PCI columns in the same dataset must not be flagged.
        assert detect_cvv_column("order_total")["match"] is False
        assert detect_cvv_column("customer_name")["match"] is False

    def test_dataset_b_pan_detection_numeric_dtype(self, dataset_b_transactions):
        results = _run_pan_scan(dataset_b_transactions, "PAN")
        matches = [r["match"] for r in results]
        assert matches == [True, True, True, False]
        assert results[0]["masked_value"] == "**** **** **** 1111"
        assert results[3]["match"] is False  # NaN

    def test_dataset_b_expiry_detection_abbreviated_column(
        self, dataset_b_transactions
    ):
        results = _run_expiry_scan(dataset_b_transactions, "ValidThru")
        matches = [r["match"] for r in results]
        # "ValidThru" must fuzzy-match despite PascalCase + abbreviation.
        assert all(r["column_matched"] for r in results)
        assert matches == [True, True, False, False]

    def test_dataset_b_cvv_column_abbreviated_uppercase(self, dataset_b_transactions):
        result = detect_cvv_column("CSC")
        assert result["match"] is True
        assert result["confidence"] == "low"

    def test_dataset_b_empty_column_never_false_positive(self, dataset_b_transactions):
        for v in dataset_b_transactions["EmptyColumn"]:
            assert detect_pan(v)["match"] is False
        assert detect_cvv_column("EmptyColumn")["match"] is False
        assert detect_card_expiry("anything", "EmptyColumn")["column_matched"] is False

    def test_dataset_b_no_pan_false_positives_on_txn_id(self, dataset_b_transactions):
        # Small integers should never be mistaken for a PAN.
        for v in dataset_b_transactions["TxnID"]:
            assert detect_pan(v)["match"] is False

    def test_dataset_b_non_pci_text_column_no_false_positive(
        self, dataset_b_transactions
    ):
        for v in dataset_b_transactions["AccountHolder"]:
            assert detect_pan(v)["match"] is False
            assert detect_cvv_column(v)["match"] is False

    def test_both_datasets_together_prove_no_cross_contamination(
        self, dataset_a_orders, dataset_b_transactions
    ):
        # Running the same stateless functions against both datasets back
        # to back must not leak state between calls.
        a_results = _run_pan_scan(dataset_a_orders, "card_number")
        b_results = _run_pan_scan(dataset_b_transactions, "PAN")
        assert [r["match"] for r in a_results] == [True, True, False, False, True]
        assert [r["match"] for r in b_results] == [True, True, True, False]


# ===========================================================================
# Additional edge cases: mixed types within a single column
# ===========================================================================


class TestMixedTypeColumns:
    def test_column_with_mixed_str_int_float_none(self):
        series = pd.Series(
            [
                "4111111111111111",
                4012888888881881,
                5555555555554444.0,
                None,
                "not-a-card",
                float("nan"),
            ]
        )
        results = [detect_pan(v) for v in series]
        assert [r["match"] for r in results] == [True, True, True, False, False, False]

    def test_expiry_column_with_mixed_types(self):
        series = pd.Series(["12/25", 1225, None, "01/26"])
        results = [detect_card_expiry(v, "expiry") for v in series]
        # Integer 1225 stringifies to "1225" which has no separator, so it
        # correctly fails the strict MM/YY format check.
        assert [r["match"] for r in results] == [True, False, False, True]
