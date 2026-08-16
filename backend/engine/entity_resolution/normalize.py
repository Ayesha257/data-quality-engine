"""Safe normalization for entity resolution (non-destructive)."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def safe_normalize(
    value: str,
    *,
    casefold: bool = True,
    strip_punctuation: bool = False,
    collapse_whitespace: bool = True,
) -> str:
    """
    Normalize for matching only — never overwrites the original value.

    Steps: Unicode NFKC → strip → optional casefold → optional punctuation
    removal → whitespace collapse.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if collapse_whitespace:
        text = _WS_RE.sub(" ", text)
    if strip_punctuation:
        text = _PUNCT_RE.sub("", text)
        text = _WS_RE.sub(" ", text).strip()
    if casefold:
        text = text.casefold()
    return text


def apply_aliases(value: str, aliases: dict[str, str]) -> str | None:
    """Return canonical alias target when ``value`` matches an alias key."""
    if not value or not aliases:
        return None
    direct = aliases.get(value)
    if direct:
        return direct
    norm = safe_normalize(value)
    for key, target in aliases.items():
        if safe_normalize(key) == norm:
            return target
    return None


def unique_non_null(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
