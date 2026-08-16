"""Tests for PII detection, overlap resolution, and masking."""

from __future__ import annotations

import pandas as pd

from backend.engine.pii.detect_pii import (
    TYPE_ADDRESS,
    TYPE_BANK_ACCOUNT,
    TYPE_CARD,
    TYPE_CNIC,
    TYPE_DOB,
    TYPE_DRIVER_LICENSE,
    TYPE_EMAIL,
    TYPE_IBAN,
    TYPE_IP_ADDRESS,
    TYPE_MOBILE,
    TYPE_PASSPORT,
    TYPE_PASSWORD,
    TYPE_PHONE,
    TYPE_POSTAL_CODE,
    TYPE_SSN,
    TYPE_URL,
    TYPE_USERNAME,
    detect_pii,
    detect_pii_in_series,
    resolve_overlaps,
)
from backend.engine.pii.mask_pii import mask_pii


def test_detect_email_and_phone():
    text = "Contact ali@example.com or 03001234567"
    hits = detect_pii(text)
    types = {h["type"] for h in hits}
    assert "EMAIL" in types
    assert "MOBILE" in types


def test_detect_cnic():
    text = "CNIC 42101-1234567-1 on file"
    hits = detect_pii(text)
    assert any(h["type"] == "CNIC" for h in hits)


def test_detect_extended_entities():
    text = (
        "SSN 123-45-6789, passport AB1234567, "
        "IBAN PK36SCBL0000001123456702, "
        "account 123456789012, dl DL-12345678, "
        "dob 1990-01-02, zip 90210, "
        "server 192.168.1.10, site https://example.com, "
        "user @talha_dev, address 221B Baker Street"
    )
    types = {h["type"] for h in detect_pii(text)}
    assert TYPE_SSN in types
    assert TYPE_PASSPORT in types
    assert TYPE_IBAN in types
    assert TYPE_BANK_ACCOUNT in types
    assert TYPE_DRIVER_LICENSE in types
    assert TYPE_DOB in types
    assert TYPE_POSTAL_CODE in types
    assert TYPE_IP_ADDRESS in types
    assert TYPE_URL in types
    assert TYPE_USERNAME in types
    assert TYPE_ADDRESS in types


def test_overlap_keeps_longer_match():
    matches = [
        {"type": "NAME", "start": 0, "end": 4, "value": "John", "score": 0.5},
        {"type": "PHONE", "start": 2, "end": 12, "value": "hn5551234", "score": 0.9},
    ]
    resolved = resolve_overlaps(matches)
    assert len(resolved) == 1
    assert resolved[0]["type"] == "PHONE"


def test_mask_partial_phone_and_full_email():
    text = "ali@example.com / 03001234567"
    hits = detect_pii(text)
    masked = mask_pii(text, hits, mode="partial")
    assert "ali@example.com" not in masked
    assert "a***@example.com" in masked
    assert "4567" in masked  # last 4 of phone kept
    assert "03001234567" not in masked


def test_mask_reverse_order_no_garble():
    text = "AA 03001234567 BB"
    hits = [
        {"type": "PHONE", "start": 3, "end": 14, "value": "03001234567", "score": 0.9},
        {"type": "NAME", "start": 0, "end": 2, "value": "AA", "score": 0.5},
    ]
    masked = mask_pii(text, hits, mode="partial")
    assert "[NAME]" in masked
    assert "4567" in masked
    assert "03001234567" not in masked


def test_mask_cnic_shape():
    text = "CNIC 35202-1234567-1"
    masked = mask_pii(text, detect_pii(text), mode="partial")
    assert "35202-1234567-1" not in masked
    assert "*****-*******-1" in masked


def test_mask_card_keeps_last_four():
    text = "card 4111 1111 1111 1111"
    masked = mask_pii(text, detect_pii(text), mode="partial")
    assert "1111" in masked
    assert "4111 1111 1111 1111" not in masked


def test_detect_pii_in_series_uses_column_hints():
    # account-like value should not be flagged as BANK_ACCOUNT in an unrelated column
    s_misc = pd.Series(["123456789012"], name="quantity")
    summary_misc = detect_pii_in_series(s_misc)
    assert summary_misc["rows_with_pii"] == 0

    # same value in account column should be flagged
    s_acc = pd.Series(["123456789012"], name="bank_account")
    summary_acc = detect_pii_in_series(s_acc)
    assert summary_acc["rows_with_pii"] == 1
    assert TYPE_BANK_ACCOUNT in summary_acc["type_counts"]


def test_detect_empty():
    assert detect_pii("") == []
    assert detect_pii(None) == []  # type: ignore[arg-type]


def test_password_column_detected_and_fully_masked():
    # Column-name-hinted, not regex-based -- arbitrary credential content.
    s = pd.Series(["Summer2024!", "hunter2", "p@ssW0rd"], name="Password")
    summary = detect_pii_in_series(s)
    assert summary["rows_with_pii"] == 3
    assert summary["type_counts"].get(TYPE_PASSWORD) == 3
    for masked in summary["masked_rows"].values():
        assert masked == "[PASSWORD]"


def test_pin_and_passcode_columns_also_detected_as_password():
    for col_name in ("portal_pin", "temp_passcode", "security_answer"):
        s = pd.Series(["abc123"], name=col_name)
        summary = detect_pii_in_series(s)
        assert summary["rows_with_pii"] == 1
        assert TYPE_PASSWORD in summary["type_counts"]


def test_unrelated_column_not_flagged_as_password():
    s = pd.Series(["abc123", "xyz789"], name="product_code")
    summary = detect_pii_in_series(s)
    assert TYPE_PASSWORD not in summary["type_counts"]


def test_datetime_column_not_flagged_as_ip_address():
    s = pd.Series(
        pd.to_datetime(["2020-01-06", "2020-01-07", "2021-03-15"]),
        name="Order Date",
    )
    summary = detect_pii_in_series(s)
    assert TYPE_IP_ADDRESS not in summary["type_counts"]
    assert summary["rows_with_pii"] == 0


def test_structured_business_code_column_not_flagged_phone_or_mobile():
    s = pd.Series(
        [
            "SON-2001DEL010000022",
            "SON-2001CUS010000001",
            "SON-2001DELHK0000003",
            "SON-2001DEL010000032",
            "SON-2001DEL010000027",
        ],
        name="Order Number",
    )
    summary = detect_pii_in_series(s)
    assert TYPE_PHONE not in summary["type_counts"]
    assert TYPE_MOBILE not in summary["type_counts"]
    assert TYPE_IP_ADDRESS not in summary["type_counts"]


def test_telephone_column_with_real_numbers_still_flagged_phone():
    s = pd.Series(
        ["01748850555", "01582723633", "01423810810"],
        name="Telephone",
    )
    summary = detect_pii_in_series(s)
    assert summary["rows_with_pii"] >= 1
    assert TYPE_PHONE in summary["type_counts"] or TYPE_MOBILE in summary["type_counts"]


def test_company_reg_column_not_flagged_as_phone_or_card():
    """UK company registration numbers must not match phone/mobile/card regex."""
    s = pd.Series(
        [
            "01537952",
            "04617032",
            "03426367",
            "6197756300009161",
            "75132218100015",
        ],
        name="Company Reg No.",
    )
    summary = detect_pii_in_series(s)
    assert summary["rows_with_pii"] == 0
    assert summary["type_counts"] == {}


def test_vat_and_eori_columns_skip_contact_regex_scan():
    for col_name in ("Customer VAT Number", "Customer EORI", "Ship Address VAT Number"):
        s = pd.Series(["GB123456789", "01537952"], name=col_name)
        summary = detect_pii_in_series(s)
        assert summary["rows_with_pii"] == 0, col_name
