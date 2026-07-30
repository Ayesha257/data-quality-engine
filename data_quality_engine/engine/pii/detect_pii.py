"""PII detection via Presidio + regex recognizers.

Tool choice (plan Section 2, Task 4): Microsoft Presidio Analyzer.
Runtime behavior is regex-first with optional Presidio enrichment.
This keeps the pipeline deterministic and resilient when NLP models
are unavailable, while still supporting richer entity coverage.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from data_quality_engine.config.settings import SETTINGS

# Entity type labels used across detect/mask/report
TYPE_PHONE = "PHONE"
TYPE_MOBILE = "MOBILE"
TYPE_CNIC = "CNIC"
TYPE_SSN = "SSN"
TYPE_CARD = "CARD"
TYPE_NAME = "NAME"
TYPE_EMAIL = "EMAIL"
TYPE_PASSPORT = "PASSPORT"
TYPE_BANK_ACCOUNT = "BANK_ACCOUNT"
TYPE_IBAN = "IBAN"
TYPE_DRIVER_LICENSE = "DRIVER_LICENSE"
TYPE_DOB = "DOB"
TYPE_ADDRESS = "ADDRESS"
TYPE_POSTAL_CODE = "POSTAL_CODE"
TYPE_IP_ADDRESS = "IP_ADDRESS"
TYPE_URL = "URL"
TYPE_USERNAME = "USERNAME"

# More specific types win on equal-length overlaps
_TYPE_PRIORITY = {
    TYPE_IBAN: 7,
    TYPE_CARD: 6,
    TYPE_CNIC: 5,
    TYPE_SSN: 5,
    TYPE_EMAIL: 4,
    TYPE_PASSPORT: 4,
    TYPE_BANK_ACCOUNT: 4,
    TYPE_PHONE: 3,
    TYPE_MOBILE: 3,
    TYPE_IP_ADDRESS: 3,
    TYPE_URL: 3,
    TYPE_POSTAL_CODE: 2,
    TYPE_DRIVER_LICENSE: 2,
    TYPE_DOB: 2,
    TYPE_USERNAME: 2,
    TYPE_ADDRESS: 2,
    TYPE_NAME: 2,
}

# Pakistan CNIC: 12345-1234567-1 (with or without dashes)
_CNIC_RE = re.compile(r"\b\d{5}-?\d{7}-?\d\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# PK mobiles + UK landline/mobile (Easby Customer List) + generic intl
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?92[\s-]?)?0?3\d{2}[\s-]?\d{7}(?!\d)"
    r"|(?<!\d)(?:\+?44[\s-]?)?0?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)"
    r"|(?<!\d)\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]\d{3,4}[\s-]?\d{3,4}(?!\d)"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Rough card: 13-19 digits, optional spaces/dashes (Luhn validated below)
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_PASSPORT_RE = re.compile(r"\b[A-Z]{1,2}\d{6,8}\b")
_BANK_ACCOUNT_RE = re.compile(r"\b\d{10,18}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_DRIVER_LICENSE_RE = re.compile(r"\b[A-Z0-9-]{6,20}\b")
_DOB_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)
_POSTAL_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}\b", re.I)
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"]+\b", re.I)
_USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.-]{3,32}\b")
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}[A-Za-z]?\s+[A-Za-z0-9.\- ]{3,60}\s+"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Block|Sector|Phase|Town|Colony)\b\.?",
    re.I,
)

_BASELINE_TYPES = {
    TYPE_EMAIL,
    TYPE_PHONE,
    TYPE_MOBILE,
    TYPE_CNIC,
    TYPE_SSN,
    TYPE_CARD,
    TYPE_IBAN,
    TYPE_IP_ADDRESS,
    TYPE_URL,
}


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13 or len(nums) > 19:
        return False
    checksum = 0
    parity = len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def _add_hit(
    hits: list[dict[str, Any]],
    pii_type: str,
    start: int,
    end: int,
    value: str,
    score: float,
) -> None:
    hits.append(
        {
            "type": pii_type,
            "start": start,
            "end": end,
            "value": value,
            "score": score,
        }
    )


def _regex_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for m in _CNIC_RE.finditer(text):
        _add_hit(hits, TYPE_CNIC, m.start(), m.end(), m.group(), 0.95)
    for m in _SSN_RE.finditer(text):
        _add_hit(hits, TYPE_SSN, m.start(), m.end(), m.group(), 0.95)
    for m in _EMAIL_RE.finditer(text):
        _add_hit(hits, TYPE_EMAIL, m.start(), m.end(), m.group(), 0.99)
    for m in _PHONE_RE.finditer(text):
        value = m.group()
        digits = re.sub(r"\D", "", value)
        # Guardrail: prevent plain long numeric values from being misread as phones.
        if (
            value.strip().isdigit()
            and not digits.startswith(("0", "44", "92"))
            and len(digits) >= 10
        ):
            continue
        pii_type = TYPE_MOBILE if re.search(r"(?:^|\D)03\d{2}", value) else TYPE_PHONE
        _add_hit(hits, pii_type, m.start(), m.end(), value, 0.85)
    for m in _CARD_RE.finditer(text):
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        if _luhn_ok(digits):
            _add_hit(hits, TYPE_CARD, m.start(), m.end(), raw, 0.9)
    for m in _IBAN_RE.finditer(text):
        _add_hit(hits, TYPE_IBAN, m.start(), m.end(), m.group(), 0.95)
    for m in _PASSPORT_RE.finditer(text):
        _add_hit(hits, TYPE_PASSPORT, m.start(), m.end(), m.group(), 0.75)
    for m in _BANK_ACCOUNT_RE.finditer(text):
        window = text[max(0, m.start() - 12) : min(len(text), m.end() + 12)].lower()
        if any(key in window for key in ("account", "a/c", "acc", "iban", "bank")):
            _add_hit(hits, TYPE_BANK_ACCOUNT, m.start(), m.end(), m.group(), 0.75)
    for m in _DRIVER_LICENSE_RE.finditer(text):
        window = text[max(0, m.start() - 16) : min(len(text), m.end() + 4)].lower()
        if any(key in window for key in ("license", "licence", "dl", "driver")):
            _add_hit(hits, TYPE_DRIVER_LICENSE, m.start(), m.end(), m.group(), 0.7)
    for m in _DOB_RE.finditer(text):
        window = text[max(0, m.start() - 12) : m.start()].lower()
        if any(key in window for key in ("dob", "birth", "born")):
            _add_hit(hits, TYPE_DOB, m.start(), m.end(), m.group(), 0.75)
    for m in _POSTAL_RE.finditer(text):
        window = text[max(0, m.start() - 16) : min(len(text), m.end() + 8)].lower()
        if any(key in window for key in ("zip", "postal", "postcode", "post code")):
            _add_hit(hits, TYPE_POSTAL_CODE, m.start(), m.end(), m.group(), 0.72)
    for m in _IPV4_RE.finditer(text):
        _add_hit(hits, TYPE_IP_ADDRESS, m.start(), m.end(), m.group(), 0.95)
    for m in _IPV6_RE.finditer(text):
        _add_hit(hits, TYPE_IP_ADDRESS, m.start(), m.end(), m.group(), 0.9)
    for m in _URL_RE.finditer(text):
        _add_hit(hits, TYPE_URL, m.start(), m.end(), m.group(), 0.9)
    for m in _USERNAME_RE.finditer(text):
        _add_hit(hits, TYPE_USERNAME, m.start(), m.end(), m.group(), 0.7)
    for m in _ADDRESS_RE.finditer(text):
        _add_hit(hits, TYPE_ADDRESS, m.start(), m.end(), m.group(), 0.72)
    return hits


def resolve_overlaps(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Keep longer / more specific matches; drop overlapping shorter ones.
    This prevents the known garble bug from double-masking.
    """
    if not matches:
        return []

    ranked = sorted(
        matches,
        key=lambda m: (
            -(m["end"] - m["start"]),
            -_TYPE_PRIORITY.get(m["type"], 0),
            -float(m.get("score") or 0),
            m["start"],
        ),
    )
    kept: list[dict[str, Any]] = []
    for cand in ranked:
        overlaps = False
        for k in kept:
            if cand["start"] < k["end"] and cand["end"] > k["start"]:
                overlaps = True
                break
        if not overlaps:
            kept.append(cand)
    return sorted(kept, key=lambda m: m["start"])


@lru_cache(maxsize=1)
def _presidio_analyzer():
    """Lazy-load Presidio. Returns None if unavailable."""
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        nlp_engine = provider.create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

        cnic_recognizer = PatternRecognizer(
            supported_entity="CNIC",
            patterns=[Pattern("cnic", r"\b\d{5}-?\d{7}-?\d\b", 0.9)],
            name="cnic_recognizer",
        )
        analyzer.registry.add_recognizer(cnic_recognizer)
        return analyzer
    except Exception:
        return None


_PRESIDIO_TYPE_MAP = {
    "PERSON": TYPE_NAME,
    "PHONE_NUMBER": TYPE_PHONE,
    "EMAIL_ADDRESS": TYPE_EMAIL,
    "CREDIT_CARD": TYPE_CARD,
    "CNIC": TYPE_CNIC,
}


@lru_cache(maxsize=20000)
def _detect_pii_cached(text: str) -> tuple:
    hits: list[dict[str, Any]] = _regex_hits(text)

    # Presidio NER is expensive on bulk ERP files; enable via settings
    use_presidio = bool(SETTINGS.get("pii_use_presidio", False)) and len(text) <= 120
    analyzer = _presidio_analyzer() if use_presidio else None
    if analyzer is not None:
        try:
            results = analyzer.analyze(text=text, language="en")
            for r in results:
                mapped = _PRESIDIO_TYPE_MAP.get(r.entity_type)
                if not mapped:
                    continue
                hits.append(
                    {
                        "type": mapped,
                        "start": int(r.start),
                        "end": int(r.end),
                        "value": text[r.start : r.end],
                        "score": float(r.score),
                    }
                )
        except Exception:
            pass

    resolved = resolve_overlaps(hits)
    return tuple(
        (
            m["type"],
            m["start"],
            m["end"],
            m["value"],
            float(m.get("score") or 0.0),
        )
        for m in resolved
    )


def _infer_expected_types(column_name: str | None) -> set[str] | None:
    """Infer likely PII types from a column name."""
    if not column_name:
        return None
    name = str(column_name).strip().lower()
    if not name:
        return None

    hints: set[str] = set()
    if any(k in name for k in ("name", "customer", "person", "employee", "contact")):
        hints.add(TYPE_NAME)
    if any(k in name for k in ("email", "e-mail", "mail")):
        hints.add(TYPE_EMAIL)
    if any(k in name for k in ("phone", "mobile", "cell", "contact no", "tel")):
        hints.update({TYPE_PHONE, TYPE_MOBILE})
    if any(k in name for k in ("cnic", "national id", "nid")):
        hints.add(TYPE_CNIC)
    if "ssn" in name:
        hints.add(TYPE_SSN)
    if "passport" in name:
        hints.add(TYPE_PASSPORT)
    if any(k in name for k in ("card", "credit", "debit", "visa", "mastercard")):
        hints.add(TYPE_CARD)
    if any(k in name for k in ("account", "bank", "iban", "swift")):
        hints.update({TYPE_BANK_ACCOUNT, TYPE_IBAN})
    if any(k in name for k in ("license", "licence", "driver")):
        hints.add(TYPE_DRIVER_LICENSE)
    if any(k in name for k in ("dob", "birth", "date of birth")):
        hints.add(TYPE_DOB)
    if any(k in name for k in ("address", "street", "city", "location")):
        hints.add(TYPE_ADDRESS)
    if any(k in name for k in ("zip", "postal", "postcode", "post code")):
        hints.add(TYPE_POSTAL_CODE)
    if any(k in name for k in ("ip", "ipv4", "ipv6")):
        hints.add(TYPE_IP_ADDRESS)
    if any(k in name for k in ("url", "website", "link", "domain")):
        hints.add(TYPE_URL)
    if any(k in name for k in ("username", "user name", "handle", "login")):
        hints.add(TYPE_USERNAME)
    return hints or None


def detect_pii(text: str, allowed_types: set[str] | None = None) -> list[dict]:
    """
    Detect PII spans in text.
    Returns list of {type, start, end, value, score}, sorted by start,
    with overlaps already resolved.
    """
    if text is None:
        return []
    text = str(text)
    if not text.strip():
        return []
    all_hits = [
        {
            "type": t,
            "start": s,
            "end": e,
            "value": v,
            "score": score,
        }
        for (t, s, e, v, score) in _detect_pii_cached(text)
    ]
    # Column-hinted account fields may contain only digits with no context words.
    if allowed_types and TYPE_BANK_ACCOUNT in allowed_types:
        for m in _BANK_ACCOUNT_RE.finditer(text):
            all_hits.append(
                {
                    "type": TYPE_BANK_ACCOUNT,
                    "start": m.start(),
                    "end": m.end(),
                    "value": m.group(),
                    "score": 0.72,
                }
            )
        all_hits = resolve_overlaps(all_hits)
    if not allowed_types:
        return all_hits
    return [h for h in all_hits if h["type"] in allowed_types]


def detect_pii_in_series(series) -> dict[str, Any]:
    """
    Scan a pandas Series for PII. Returns counts-only summary plus
    per-row masked values (never raw PII in the summary).
    """
    import pandas as pd

    from data_quality_engine.engine.pii.mask_pii import mask_pii

    if series is None or not isinstance(series, pd.Series):
        raise TypeError("series must be a pandas Series")

    type_counts: dict[str, int] = {}
    masked_rows: dict[Any, str] = {}
    issue_rows = 0
    col_name = str(series.name) if series.name is not None else None
    inferred = _infer_expected_types(col_name)
    allowed_types = inferred or _BASELINE_TYPES

    for idx, value in series.items():
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        raw = str(value)
        found = detect_pii(raw, allowed_types=allowed_types)
        if not found:
            continue
        issue_rows += 1
        for m in found:
            type_counts[m["type"]] = type_counts.get(m["type"], 0) + 1
        masked_rows[idx] = mask_pii(raw, found)

    return {
        "column": col_name,
        "rows_with_pii": issue_rows,
        "type_counts": type_counts,
        "masked_rows": masked_rows,
        "allowed_types": sorted(allowed_types),
    }
