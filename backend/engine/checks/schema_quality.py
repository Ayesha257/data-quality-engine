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
  - SOX audit-trail completeness (check_audit_trail_completeness)

This does not duplicate column_classifier.py: classify_columns() looks at
*values* to decide a column's role (identifier/measurement/...); this check
looks only at the *name* itself, independent of role.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd

from backend.compliance.fuzzy_columns import find_matching_columns
from backend.engine.compliance.compliance_status import sanitize_details
from backend.engine.models import CheckResult

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


# ---------------------------------------------------------------------------
# SOX Audit Trail Schema Check
# ---------------------------------------------------------------------------

AUDIT_TRAIL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "creation": (
        "created by",
        "created at",
        "create date",
        "created date",
        "created time",
        "creation date",
        "creation time",
        "creator",
        "created on",
        "create time",
        "created dt",
        "create dt",
        "entered by",
        "entered at",
        "record created at",
        "record created by",
        "inserted at",
        "inserted by",
        "creation timestamp",
        "createdby",
        "createdat",
    ),
    "approval": (
        "approved by",
        "reviewed by",
        "approved at",
        "approved date",
        "approver",
        "reviewer",
        "approval date",
        "approval status",
        "reviewed at",
        "reviewed date",
        "authorized by",
        "sign off by",
        "signed off by",
        "audited by",
        "approvedby",
        "reviewedby",
        "appr by",
        "approver id",
    ),
    "modification": (
        "modified at",
        "updated at",
        "modified by",
        "updated by",
        "last modified at",
        "last updated at",
        "modified date",
        "updated date",
        "last modified date",
        "last updated date",
        "modification date",
        "update time",
        "modified time",
        "change date",
        "changed by",
        "changed at",
        "mod dt",
        "upd date",
        "last modified",
        "last updated",
        "modifiedby",
        "updatedby",
        "modifiedat",
        "updatedat",
    ),
}


def classify_audit_trail_columns(
    column_names: Iterable[Any] | pd.DataFrame,
) -> dict[str, list[str]]:
    """Fuzzy-match column names against the three SOX audit trail categories:

    - creation (created_by, created_at, create_date, etc.)
    - approval (approved_by, reviewed_by, approved_at, etc.)
    - modification (modified_at, updated_at, modified_by, etc.)

    Returns {category: [matching column names]}.
    """
    if isinstance(column_names, pd.DataFrame):
        names = list(column_names.columns)
    elif column_names is not None:
        names = [str(c) for c in column_names]
    else:
        names = []

    return {
        category: find_matching_columns(names, keywords)
        for category, keywords in AUDIT_TRAIL_CATEGORIES.items()
    }


def check_audit_trail_completeness(
    column_names: Iterable[Any] | pd.DataFrame,
) -> CheckResult:
    """SOX Audit Trail Header Completeness Check -> Schema Quality dimension.

    Evaluates the presence of audit-trail headers across 3 core categories:
    1. Creation (e.g. created_by, created_at)
    2. Approval (e.g. approved_by, reviewed_by)
    3. Modification (e.g. modified_at, updated_at)

    Scores based on how many of the 3 categories have matching columns:
    - 3/3 matched: quality_ratio = 1.0 (Full Coverage, status="passed")
    - 2/3 matched: quality_ratio = 0.667 (Partial Coverage, status="failed")
    - 1/3 matched: quality_ratio = 0.333 (Partial Coverage, status="failed")
    - 0/3 matched: quality_ratio = 0.0 (Zero Coverage, status="failed")

    Confidence: "high" (schema-level check).
    """
    try:
        matches = classify_audit_trail_columns(column_names)
        matched_categories = [cat for cat, cols in matches.items() if cols]
        missing_categories = [cat for cat, cols in matches.items() if not cols]
        matched_count = len(matched_categories)
        total_categories = len(AUDIT_TRAIL_CATEGORIES)

        quality_ratio = round(matched_count / float(total_categories), 4)
        issues_found = total_categories - matched_count
        status = "passed" if issues_found == 0 else "failed"

        details = sanitize_details(
            {
                "rule": "audit_trail_headers",
                "regulation": "SOX",
                "confidence": "high",
                "method": "schema_check",
                "score": quality_ratio,
                "coverage": f"{matched_count}/{total_categories}",
                "matched_categories_count": matched_count,
                "total_categories_count": total_categories,
                "categories_checked": list(AUDIT_TRAIL_CATEGORIES.keys()),
                "matched_categories": matched_categories,
                "missing_categories": missing_categories,
                "category_matches": matches,
            }
        )

        return CheckResult(
            check_name="audit_trail_completeness",
            status=status,
            column=None,
            issues_found=issues_found,
            dimension="schema_quality",
            quality_ratio=quality_ratio,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name="audit_trail_completeness",
            status="error",
            column=None,
            issues_found=0,
            dimension="schema_quality",
            quality_ratio=0.0,
            details=sanitize_details({"error": str(exc)}),
        )
