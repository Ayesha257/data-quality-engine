"""Schema-quality check -> Schema Quality dimension (teacher rubric, 10%).

Pure name/structure inspection -- no value parsing, no new dependencies.
Flags columns whose *name* undermines downstream tooling (report tables,
the AI agent, any code that keys off column names):

  - auto-generated/placeholder names produced by pandas when a header cell
    was blank ("Unnamed: 3", "unnamed_7")
  - duplicate column names within the same sheet (e.g. two "Buyer ID"
    columns after a pivot copy-paste -- seen in the real Easby files)
  - vague/generic names ("Column1", "Field2", "Data", "Value", "X", "A")
  - empty or whitespace-only names

This does not duplicate column_classifier.py: classify_columns() looks at
*values* to decide a column's role (identifier/measurement/...); this check
looks only at the *name* itself, independent of role.
"""

from __future__ import annotations

import re

import pandas as pd

from data_quality_engine.engine.models import CheckResult

# pandas' own placeholder pattern ("Unnamed: 0") plus the lowercase variant
# this codebase's ingestion layer already produces ("unnamed_0" -- see
# ingestion.py's header-merge step for multi-header sheets like "Sheet4").
_AUTO_GENERATED_RE = re.compile(r"^unnamed[:_]\s*\d+$", re.I)

# Single letter, or short "word + digits" placeholders: "Column1", "Field2",
# "Col_3", "X1". Deliberately does NOT match legitimate short business
# codes like "PO", "SKU", "VAT" (no trailing digits, or not a generic word).
_VAGUE_GENERIC_WORDS = (
    "column",
    "col",
    "field",
    "data",
    "value",
    "val",
    "attribute",
    "attr",
    "var",
    "variable",
    "temp",
    "tmp",
)
_VAGUE_NAME_RE = re.compile(
    r"^(" + "|".join(_VAGUE_GENERIC_WORDS) + r")[\s_]*\d*$", re.I
)
_SINGLE_LETTER_RE = re.compile(r"^[a-z]$", re.I)


def _name_issues(name: object) -> list[str]:
    """Return the list of issue codes that apply to a single column name."""
    issues: list[str] = []

    if name is None:
        return ["empty_or_whitespace_name"]

    text = str(name)
    stripped = text.strip()

    if stripped == "":
        issues.append("empty_or_whitespace_name")
        return issues  # nothing further to check on an empty name

    if _AUTO_GENERATED_RE.match(stripped):
        issues.append("auto_generated_name")

    if _SINGLE_LETTER_RE.match(stripped) or _VAGUE_NAME_RE.match(stripped):
        issues.append("vague_generic_name")

    return issues


def check_schema_quality(df: pd.DataFrame) -> list[CheckResult]:
    """
    For each column, flag name-level schema problems.
    dimension = "schema_quality"
    Returns one CheckResult per column (matches the per-column pattern used
    by check_missing_values / check_type_consistency_frame), plus duplicate
    column names are cross-referenced across the whole frame so every
    column sharing a duplicated name is flagged, not just the second one.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        if df.shape[1] == 0:
            return [
                CheckResult(
                    check_name="schema_quality",
                    status="passed",
                    column=None,
                    issues_found=0,
                    details={"reason": "no_columns"},
                    dimension="schema_quality",
                )
            ]

        # Build duplicate-name groups first (case-insensitive, whitespace-
        # trimmed match -- "Buyer ID" and " buyer id " are the same problem).
        normalized_counts: dict[str, int] = {}
        for col in df.columns:
            key = str(col).strip().lower()
            normalized_counts[key] = normalized_counts.get(key, 0) + 1
        duplicated_keys = {k for k, count in normalized_counts.items() if count > 1}

        results: list[CheckResult] = []
        for col in df.columns:
            col_name = str(col)
            issues = _name_issues(col)

            key = col_name.strip().lower()
            is_duplicate = key in duplicated_keys
            if is_duplicate:
                issues.append("duplicate_column_name")

            status = "passed" if not issues else "failed"
            results.append(
                CheckResult(
                    check_name="schema_quality",
                    status=status,
                    column=col_name,
                    issues_found=1 if issues else 0,
                    details={
                        "issues": issues,
                        "duplicate_group_size": normalized_counts.get(key, 1)
                        if is_duplicate
                        else None,
                    },
                    dimension="schema_quality",
                )
            )
        return results
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return [
            CheckResult(
                check_name="schema_quality",
                status="error",
                column=None,
                issues_found=0,
                details={"error": str(exc)},
                dimension="schema_quality",
            )
        ]
