"""
Shared column-name fuzzy matching utility.

Neutral, dependency-free location used across schema quality checks and
compliance detectors.
"""

import functools
import re
from typing import Iterable

from rapidfuzz import fuzz

# snake_case/kebab-case separators -> space; camelCase/PascalCase boundary -> space
_SEPARATOR_RE = re.compile(r"[_\-./]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_FUZZY_THRESHOLD = 80


@functools.lru_cache(maxsize=2048)
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
    norm_keywords = [normalize_column_name(kw) for kw in keywords if kw]
    for norm_kw in norm_keywords:
        if not norm_kw:
            continue
        if norm_kw in normalized_name:
            return True
        score = max(
            fuzz.token_sort_ratio(normalized_name, norm_kw),
            fuzz.ratio(normalized_name, norm_kw),
        )
        if score >= threshold:
            return True
    return False


def find_matching_columns(
    column_names: object,
    keywords: tuple[str, ...] | list[str],
    *,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> list[str]:
    """Return the subset of column_names that fuzzy-match any keyword."""
    if not column_names:
        return []
    cols = list(column_names) if isinstance(column_names, (list, tuple, set)) else list(column_names)
    normalized_cols = [(str(col), normalize_column_name(col)) for col in cols]
    norm_keywords = [normalize_column_name(kw) for kw in keywords if kw]
    norm_keywords = [nk for nk in norm_keywords if nk]
    if not norm_keywords:
        return []

    matched = []
    for orig_name, norm_name in normalized_cols:
        if not norm_name:
            continue
        col_matched = False
        for norm_kw in norm_keywords:
            if norm_kw in norm_name:
                col_matched = True
                break
            score = max(
                fuzz.token_sort_ratio(norm_name, norm_kw),
                fuzz.ratio(norm_name, norm_kw),
            )
            if score >= threshold:
                col_matched = True
                break
        if col_matched:
            matched.append(orig_name)
    return matched
