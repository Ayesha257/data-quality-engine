"""PII masking with overlap-safe reverse-index replacement."""

from __future__ import annotations

from typing import Any

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.pii.detect_pii import (
    TYPE_BANK_ACCOUNT,
    TYPE_CARD,
    TYPE_CNIC,
    TYPE_DOB,
    TYPE_EMAIL,
    TYPE_IBAN,
    TYPE_IP_ADDRESS,
    TYPE_MOBILE,
    TYPE_NAME,
    TYPE_PHONE,
    TYPE_POSTAL_CODE,
    TYPE_PASSPORT,
    TYPE_USERNAME,
    resolve_overlaps,
)


_FULL_ALWAYS = {TYPE_NAME, TYPE_DOB}


def _token_for(pii_type: str) -> str:
    return f"[{pii_type}]"


def _partial_mask(value: str, show_last: int) -> str:
    if show_last <= 0 or len(value) <= show_last:
        return "*" * len(value)
    return ("*" * (len(value) - show_last)) + value[-show_last:]


def _mask_email(value: str) -> str:
    """
    Keep first character of local part + full domain.
    Example: john.doe@gmail.com -> j***@gmail.com
    """
    if "@" not in value:
        return "[EMAIL]"
    local, domain = value.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _mask_cnic(value: str) -> str:
    """
    Preserve CNIC shape while masking most digits.
    Example: 35202-1234567-1 -> *****-*******-1
    """
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 13:
        return _partial_mask(value, 1)
    return "*****-*******-" + digits[-1]


def _mask_ip(value: str) -> str:
    """Mask IP while preserving rough family/readability."""
    if ":" in value:  # IPv6
        parts = value.split(":")
        if len(parts) <= 2:
            return "****"
        return ":".join(["****"] * (len(parts) - 2) + parts[-2:])
    parts = value.split(".")
    if len(parts) != 4:
        return "[IP_ADDRESS]"
    return "x.x.x." + parts[-1]


def mask_pii(
    text: str,
    detected: list[dict[str, Any]],
    mode: str | None = None,
) -> str:
    """
    mode="partial": show only last N characters (phone/card/CNIC)
    mode="full": replace entire value with a fixed token (e.g. "[NAME]")
    Names/emails always fully redacted.
    Processes matches in reverse start order after overlap resolution.
    """
    if text is None:
        return ""
    text = str(text)
    if not detected:
        return text

    resolved = resolve_overlaps(detected)
    use_mode = mode or SETTINGS["pii_mask_mode"]
    show_last = int(SETTINGS["pii_show_last_n"])

    out = text
    for match in sorted(resolved, key=lambda m: m["start"], reverse=True):
        start = int(match["start"])
        end = int(match["end"])
        value = out[start:end]
        pii_type = match.get("type", "PII")

        if use_mode == "full" or pii_type in _FULL_ALWAYS:
            replacement = _token_for(str(pii_type))
        elif pii_type == TYPE_EMAIL:
            replacement = _mask_email(value)
        elif pii_type == TYPE_CNIC:
            replacement = _mask_cnic(value)
        elif pii_type in {TYPE_PHONE, TYPE_MOBILE, TYPE_CARD, TYPE_BANK_ACCOUNT}:
            # Keep structural non-digits lightly; mask digit body partially
            digits = "".join(c for c in value if c.isdigit())
            if digits:
                masked_digits = _partial_mask(digits, show_last)
                # rebuild preserving separators roughly
                di = 0
                chars = []
                for ch in value:
                    if ch.isdigit():
                        chars.append(masked_digits[di] if di < len(masked_digits) else "*")
                        di += 1
                    else:
                        chars.append(ch)
                replacement = "".join(chars)
            else:
                replacement = _partial_mask(value, show_last)
        elif pii_type == TYPE_IBAN:
            compact = "".join(ch for ch in value if ch.isalnum())
            if len(compact) > 6:
                replacement = compact[:2] + "*" * (len(compact) - 6) + compact[-4:]
            else:
                replacement = _token_for(str(pii_type))
        elif pii_type in {TYPE_IP_ADDRESS}:
            replacement = _mask_ip(value)
        elif pii_type in {TYPE_PASSPORT, TYPE_POSTAL_CODE, TYPE_USERNAME}:
            replacement = _partial_mask(value, max(1, show_last - 1))
        else:
            replacement = _token_for(str(pii_type))

        out = out[:start] + replacement + out[end:]
    return out
