"""PII-safe display for entity-resolution logs and API responses."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[^@]+@[^@]+\.[^@]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-().]{6,}\d)")


def looks_like_pii(value: str) -> bool:
    if not value or len(value) < 3:
        return False
    if _EMAIL_RE.search(value):
        return True
    if _PHONE_RE.search(value) and sum(c.isdigit() for c in value) >= 7:
        return True
    return False


def safe_display_value(value: str, *, show_last: int = 2) -> str:
    """Mask sensitive-looking values; leave benign tokens unchanged."""
    if not value:
        return value
    if not looks_like_pii(value):
        return value
    if "@" in value:
        local, _, domain = value.partition("@")
        if local:
            return f"{local[0]}***@{domain}"
        return "***@" + domain
    digits = sum(c.isdigit() for c in value)
    if digits >= 7:
        tail = value[-show_last:] if show_last else ""
        return "*" * max(1, len(value) - len(tail)) + tail
    if len(value) <= 4:
        return "****"
    return value[:2] + "***"
