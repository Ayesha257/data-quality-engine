"""
PCI-DSS detectors -- backend/config/compliance_rules.json.

Implements detection rules for PCI DSS-scoped cardholder data and sensitive
authentication data:
1. PAN (Primary Account Number): 13-19 digit sequence + standard Luhn checksum
   (mod 10). Value-level detection, masked to only display last 4 digits.
2. card_expiry: MM/YY (or MM/YYYY) regex, gated by column-name fuzzy match.
3. cvv: Column-name-only detection (CVV/CVC/security code). Never inspects or stores
   cell values.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from backend.engine.utils.fuzzy_matching import normalize_column_name

# ---------------------------------------------------------------------------
# Rule Configuration Loader
# ---------------------------------------------------------------------------


def _find_compliance_rules_path() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "compliance_rules.json",
        Path(__file__).resolve().parents[2] / "backend" / "config" / "compliance_rules.json",
        Path("backend/config/compliance_rules.json").resolve(),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def load_pci_dss_rules() -> dict[str, Any]:
    """Load PCI DSS rules definition from compliance_rules.json."""
    path = _find_compliance_rules_path()
    content = json.loads(path.read_text(encoding="utf-8"))
    return content["frameworks"]["PCI_DSS"]


# ---------------------------------------------------------------------------
# 1. PAN (Primary Account Number) Detector
# ---------------------------------------------------------------------------

_PAN_RE = re.compile(r"(?<!\d)(?:[0-9][ -]?){13,19}(?!\d)")


def _luhn_checksum(digits: str) -> bool:
    """Validate digits string using the standard Luhn (mod 10) algorithm."""
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    reverse_digits = digits[::-1]
    for i, char in enumerate(reverse_digits):
        n = int(char)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def detect_pan(value: Any) -> dict[str, Any]:
    """Detect and validate a Primary Account Number (PAN) via regex + Luhn.

    Returns a dict with match details. Masks all but the last 4 digits.
    Never exposes full card number in the returned dictionary.
    """
    if value is None:
        return {
            "match": False,
            "field_name": "PAN",
            "detection_type": "regex_checksum",
            "confidence": None,
            "pan_length": 0,
            "masked_value": None,
        }

    # Handle float / NaN / NaT / pandas missing values
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return {
                "match": False,
                "field_name": "PAN",
                "detection_type": "regex_checksum",
                "confidence": None,
                "pan_length": 0,
                "masked_value": None,
            }
        if value.is_integer():
            val_str = str(int(value))
        else:
            val_str = str(value)
    elif isinstance(value, int):
        val_str = str(value)
    else:
        # Check pandas NA / NaT types
        if pd.isna(value):
            return {
                "match": False,
                "field_name": "PAN",
                "detection_type": "regex_checksum",
                "confidence": None,
                "pan_length": 0,
                "masked_value": None,
            }
        val_str = str(value)

    if not val_str or not val_str.strip():
        return {
            "match": False,
            "field_name": "PAN",
            "detection_type": "regex_checksum",
            "confidence": None,
            "pan_length": 0,
            "masked_value": None,
        }

    # Search for 13-19 digit candidate patterns
    for match in _PAN_RE.finditer(val_str):
        raw_match = match.group()
        digits = re.sub(r"\D", "", raw_match)
        if 13 <= len(digits) <= 19 and _luhn_checksum(digits):
            masked = f"**** **** **** {digits[-4:]}"
            return {
                "match": True,
                "field_name": "PAN",
                "detection_type": "regex_checksum",
                "confidence": "high",
                "pan_length": len(digits),
                "masked_value": masked,
            }

    return {
        "match": False,
        "field_name": "PAN",
        "detection_type": "regex_checksum",
        "confidence": None,
        "pan_length": 0,
        "masked_value": None,
    }


# ---------------------------------------------------------------------------
# 2. Card Expiry Detector
# ---------------------------------------------------------------------------

_EXPIRY_RE = re.compile(r"^\s*(0[1-9]|1[0-2])\s*[/\\-]\s*(\d{2}|\d{4})\s*$")

_EXPIRY_COLUMN_PHRASES = (
    "expiry",
    "expiration",
    "exp date",
    "exp dt",
    "expdt",
    "expdate",
    "valid thru",
    "validthru",
    "valid through",
    "card exp",
    "cardexp",
    "card expiry",
    "cc exp",
    "ccexp",
)


def _is_expiry_column(column_name: object) -> bool:
    """Return True if column_name matches card expiry naming conventions."""
    if column_name is None:
        return False
    norm = normalize_column_name(column_name)
    if not norm:
        return False
    tokens = norm.split()
    # Explicit exclusions
    if "expense" in tokens or "experience" in tokens:
        return False
    if norm in _EXPIRY_COLUMN_PHRASES:
        return True
    if any(phrase in norm for phrase in _EXPIRY_COLUMN_PHRASES):
        return True
    if norm == "exp":
        return True
    if "exp" in tokens and any(t in tokens for t in ("date", "dt", "card", "cc", "month", "yr", "year")):
        return True
    return False


def detect_card_expiry(value: Any, column_name: str | None) -> dict[str, Any]:
    """Detect card expiry date (MM/YY or MM/YYYY) gated by column name.

    Only matches if column name relates to card expiry AND value matches MM/YY pattern.
    """
    col_matched = _is_expiry_column(column_name)

    if not col_matched:
        return {
            "match": False,
            "column_matched": False,
            "field_name": "card_expiry",
            "detection_type": "regex",
            "confidence": None,
            "normalized_value": None,
        }

    if value is None or pd.isna(value):
        return {
            "match": False,
            "column_matched": True,
            "field_name": "card_expiry",
            "detection_type": "regex",
            "confidence": None,
            "normalized_value": None,
        }

    val_str = str(value).strip()
    if not val_str:
        return {
            "match": False,
            "column_matched": True,
            "field_name": "card_expiry",
            "detection_type": "regex",
            "confidence": None,
            "normalized_value": None,
        }

    m = _EXPIRY_RE.match(val_str)
    if m:
        month = m.group(1)
        year = m.group(2)
        normalized_value = f"{month}/{year[-2:]}"
        return {
            "match": True,
            "column_matched": True,
            "field_name": "card_expiry",
            "detection_type": "regex",
            "confidence": "medium",
            "normalized_value": normalized_value,
        }

    return {
        "match": False,
        "column_matched": True,
        "field_name": "card_expiry",
        "detection_type": "regex",
        "confidence": None,
        "normalized_value": None,
    }


# ---------------------------------------------------------------------------
# 3. CVV Column Detector
# ---------------------------------------------------------------------------

_VALID_CVV_COLUMN_NAMES = frozenset(
    {
        "cvv",
        "cvv2",
        "cvc",
        "cvc2",
        "cvn",
        "csc",
        "security code",
        "card verification value",
        "card verification code",
        "verification code",
        "cvv number",
        "cvc number",
        "card cvc",
        "card cvv",
        "card csc",
        "card security code",
        "security code number",
        "cvvnumber",
        "cvcnumber",
        "cardcvc",
        "cardcvv",
        "cardcsc",
        "securitycode",
    }
)


def detect_cvv_column(column_name: str | None) -> dict[str, Any]:
    """Detect if column represents a CVV/CVC/Security Code field.

    Accepts ONLY column_name; never inspects row values.
    """
    if column_name is None:
        return {
            "match": False,
            "field_name": "cvv",
            "detection_type": "column_keyword",
            "confidence": None,
            "column_name": None,
        }

    norm = normalize_column_name(column_name)
    if not norm:
        return {
            "match": False,
            "field_name": "cvv",
            "detection_type": "column_keyword",
            "confidence": None,
            "column_name": str(column_name),
        }

    # Strict match against known CVV column normalized names
    if norm in _VALID_CVV_COLUMN_NAMES:
        return {
            "match": True,
            "field_name": "cvv",
            "detection_type": "column_keyword",
            "confidence": "low",
            "column_name": str(column_name),
        }

    return {
        "match": False,
        "field_name": "cvv",
        "detection_type": "column_keyword",
        "confidence": None,
        "column_name": str(column_name),
    }
