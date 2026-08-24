"""
Shared column-name fuzzy matching helper -- engine-level re-export.
"""

from __future__ import annotations

from backend.compliance.fuzzy_columns import (
    DEFAULT_FUZZY_THRESHOLD,
    column_matches_keywords,
    find_matching_columns,
    keyword_matches_name,
    normalize_column_name,
)

__all__ = [
    "DEFAULT_FUZZY_THRESHOLD",
    "normalize_column_name",
    "keyword_matches_name",
    "column_matches_keywords",
    "find_matching_columns",
]
