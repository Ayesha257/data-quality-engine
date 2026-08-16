"""Text standardization via fuzzy matching (plan.md Section 4.4 / Task 5)."""

from backend.engine.standardization.fuzzy_match import (
    apply_standardization,
    check_fuzzy_standardization,
    check_fuzzy_standardization_frame,
    standardize_frame,
    standardize_values,
)

__all__ = [
    "apply_standardization",
    "check_fuzzy_standardization",
    "check_fuzzy_standardization_frame",
    "standardize_frame",
    "standardize_values",
]
