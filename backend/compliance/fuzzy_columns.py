"""
Shared column-*name* fuzzy matching helper.

Used by:
- pci_dss_detectors (card_expiry and cvv column matching)
- glba_detectors.classify_glba_keyword_columns (GLBA column_keyword rules)
- backend.engine.checks.schema_quality.check_audit_trail_completeness
  (SOX audit_trail_headers schema_check rule)

This is name-only matching (no value inspection), independent of, and not
a duplicate of, backend.engine.standardization.fuzzy_match (which
fuzzy-matches *values* within a column for the consistency dimension).

Uses rapidfuzz so column-name matching degrades cleanly across arbitrary
naming conventions (snake_case, camelCase, PascalCase, spaced, hyphenated).
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# snake_case/kebab-case separators -> space; camelCase/PascalCase boundary -> space
_SEPARATOR_RE = re.compile(r"[_\-./]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_FUZZY_THRESHOLD = 80


def normalize_column_name(name: object) -> str:
    """Normalize a column name to lowercase space-separated tokens.

    "CardExpiryDate"        -> "card expiry date"
    "CVVNumber"             -> "cvv number"
    "exp_date"              -> "exp date"
    "ValidThru"             -> "valid thru"
    "LoanApplicationAmount" -> "loan application amount"
    "loan_amt"              -> "loan amt"
    "Credit-History Score"  -> "credit history score"
    """
    if name is None:
        return ""
    text = str(name).strip()
    if not text:
        return ""
    text = _ACRONYM_BOUNDARY_RE.sub(" ", text)
    text = _CAMEL_BOUNDARY_RE.sub(" ", text)
    text = _SEPARATOR_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip().lower()
    return text


def keyword_matches_name(
    normalized_name: str,
    keyword: str,
    *,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> bool:
    """True if a single normalized keyword phrase fuzzy-matches the column name."""
    if not normalized_name or not keyword:
        return False
    normalized_keyword = normalize_column_name(keyword)
    if not normalized_keyword:
        return False
    # Fast path: exact match or keyword substring containment
    if normalized_keyword in normalized_name:
        return True
    score = max(
        fuzz.token_sort_ratio(normalized_name, normalized_keyword),
        fuzz.ratio(normalized_name, normalized_keyword),
    )
    return score >= threshold


def column_matches_keywords(
    column_name: object,
    keywords: tuple[str, ...] | list[str],
    *,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> bool:
    """True if column_name fuzzy-matches any keyword in the given list."""
    normalized_name = normalize_column_name(column_name)
    if not normalized_name:
        return False
    return any(
        keyword_matches_name(normalized_name, kw, threshold=threshold)
        for kw in keywords
    )


def find_matching_columns(
    column_names: object,
    keywords: tuple[str, ...] | list[str],
    *,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> list[str]:
    """Return the subset of column_names that fuzzy-match any keyword."""
    if not column_names:
        return []
    return [
        str(col)
        for col in column_names
        if column_matches_keywords(col, tuple(keywords), threshold=threshold)
    ]
