"""
GLBA (Gramm-Leach-Bliley Act) detectors -- backend/config/compliance_rules.json.

Two detection tiers matching compliance_rules.json's GLBA section:

1. regex_checksum / high confidence
   detect_routing_number(value): 9-digit shape + ABA weighted-checksum (3, 7, 1)
   validation. A pass here is a real, structurally confirmed match -- not a name-based guess.

2. column_keyword / low confidence
   classify_glba_keyword_columns(column_names): fuzzy name-only matching against keyword lists for:
   - bank_account_number
   - loan_application_data
   - credit_history_data
   - tax_return_data
   These are candidate indicators based on column headers alone -- no value inspection occurs,
   so every hit is confidence="low" (guesses for human review, not confirmed findings).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.compliance.fuzzy_columns import find_matching_columns
from backend.engine.compliance.compliance_status import sanitize_details
from backend.engine.models import CheckResult

CHECK_NAME = "glba"

# ---------------------------------------------------------------------------
# Rule Configuration Loader
# ---------------------------------------------------------------------------


def _find_compliance_rules_path() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "compliance_rules.json",
        Path(__file__).resolve().parents[2] / "backend" / "config" / "compliance_rules.json",
        Path("backend/config/compliance_rules.json").resolve(),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def load_glba_rules() -> dict[str, Any]:
    """Load GLBA rules definition from compliance_rules.json."""
    path = _find_compliance_rules_path()
    content = json.loads(path.read_text(encoding="utf-8"))
    frameworks = content.get("frameworks") or content.get("regulations") or {}
    return frameworks.get("GLBA", {})


# ---------------------------------------------------------------------------
# 1. Routing Number Detector (regex_checksum / high confidence)
# ---------------------------------------------------------------------------

_ROUTING_NUMBER_RE = re.compile(r"^\d{9}$")
# ABA routing number weighted checksum: (3, 7, 1) repeated across 9 digits
_ABA_WEIGHTS: tuple[int, ...] = (3, 7, 1, 3, 7, 1, 3, 7, 1)
_CLEAN_RE = re.compile(r"[\s\-._/]+")


class DetectionResult(dict):
    """Dictionary supporting both item and attribute access for detection results."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'DetectionResult' object has no attribute '{name}'")


def _aba_checksum_valid(digits: str) -> bool:
    """Validate a 9-digit string using the ABA weighted (3, 7, 1) mod-10 checksum."""
    if not digits.isdigit() or len(digits) != 9:
        return False
    if digits == "000000000":
        return False
    total = sum(int(d) * w for d, w in zip(digits, _ABA_WEIGHTS))
    return total % 10 == 0


def detect_routing_number(value: Any) -> DetectionResult:
    """Detect and validate a 9-digit ABA bank routing number.

    Shape check (9 digits) followed by the ABA weighted (3, 7, 1) mod-10
    checksum. Both must pass for match=True / confidence="high".
    """
    if value is None:
        return DetectionResult(
            match=False,
            is_match=False,
            field_name="routing_number",
            field="routing_number",
            detection_type="regex_checksum",
            confidence=None,
            reason="empty_value",
            masked_value=None,
        )

    # Handle float / NaN / NaT / pandas missing values
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return DetectionResult(
                match=False,
                is_match=False,
                field_name="routing_number",
                field="routing_number",
                detection_type="regex_checksum",
                confidence=None,
                reason="empty_value",
                masked_value=None,
            )
        if value.is_integer():
            int_val = int(value)
            # If Excel/pandas auto-typed a leading-zero routing number (8 digits)
            val_str = str(int_val).zfill(9) if 10000000 <= int_val < 100000000 else str(int_val)
        else:
            val_str = str(value)
    elif isinstance(value, int):
        val_str = str(value).zfill(9) if 10000000 <= value < 100000000 else str(value)
    else:
        if pd.isna(value):
            return DetectionResult(
                match=False,
                is_match=False,
                field_name="routing_number",
                field="routing_number",
                detection_type="regex_checksum",
                confidence=None,
                reason="empty_value",
                masked_value=None,
            )
        val_str = str(value).strip()

    if not val_str:
        return DetectionResult(
            match=False,
            is_match=False,
            field_name="routing_number",
            field="routing_number",
            detection_type="regex_checksum",
            confidence=None,
            reason="empty_value",
            masked_value=None,
        )

    cleaned = _CLEAN_RE.sub("", val_str)

    if not _ROUTING_NUMBER_RE.match(cleaned):
        return DetectionResult(
            match=False,
            is_match=False,
            field_name="routing_number",
            field="routing_number",
            detection_type="regex_checksum",
            confidence=None,
            reason="not_nine_digits",
            masked_value=None,
        )

    if not _aba_checksum_valid(cleaned):
        return DetectionResult(
            match=False,
            is_match=False,
            field_name="routing_number",
            field="routing_number",
            detection_type="regex_checksum",
            confidence=None,
            reason="checksum_failed",
            masked_value=None,
        )

    masked = f"*****{cleaned[-4:]}"
    return DetectionResult(
        match=True,
        is_match=True,
        field_name="routing_number",
        field="routing_number",
        detection_type="regex_checksum",
        confidence="high",
        reason="checksum_valid",
        masked_value=masked,
    )


def check_glba_routing_numbers(df: pd.DataFrame, column: str) -> CheckResult:
    """Scan a single column's values for valid ABA routing numbers."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0:
            return CheckResult(
                check_name=CHECK_NAME,
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "routing_number", "confidence": "high", "reason": "no_data"}
                ),
            )

        matches = sum(1 for v in series if detect_routing_number(v)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total

        return CheckResult(
            check_name=CHECK_NAME,
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "routing_number",
                    "regulation": "GLBA",
                    "confidence": "high",
                    "method": "regex_checksum",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        return CheckResult(
            check_name=CHECK_NAME,
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


# ---------------------------------------------------------------------------
# 2. Keyword Column Classifier (column_keyword / low confidence)
# ---------------------------------------------------------------------------

GLBA_KEYWORD_CATEGORIES: dict[str, tuple[str, ...]] = {
    "bank_account_number": (
        "account number",
        "acct no",
        "acct number",
        "acct num",
        "account no",
        "account num",
        "account #",
        "bank account",
        "bank acct",
        "bank account number",
        "bank acct no",
        "bank acct number",
        "routing account",
        "checking account",
        "savings account",
        "deposit account",
        "iban",
    ),
    "loan_application_data": (
        "loan amount",
        "loan amt",
        "loan application",
        "loan app",
        "loan application amount",
        "principal amount",
        "principal amt",
        "loan term",
        "loan type",
        "mortgage amount",
        "mortgage amt",
        "borrower amount",
        "application amount",
        "loan balance",
        "loan rate",
    ),
    "credit_history_data": (
        "credit score",
        "credit history",
        "credit history score",
        "fico score",
        "fico",
        "credit report",
        "credit rating",
        "credit bureau",
        "credit limit",
    ),
    "tax_return_data": (
        "tax return",
        "tax id",
        "tax id number",
        "tax id no",
        "agi",
        "adjusted gross income",
        "w2 income",
        "1099 income",
        "w2",
        "1099",
        "tax filing",
        "tax document",
        "filing status",
        "tax bracket",
    ),
}


def classify_glba_keyword_columns(
    column_names: Iterable[Any],
) -> dict[str, list[str]]:
    """Fuzzy-match column names against GLBA keyword categories.

    Name-only heuristic -- no value inspection. Every category returned
    here is confidence="low" (per compliance_rules.json): candidates for human
    review, not confirmed findings. Handles varied naming conventions
    (snake_case, camelCase, PascalCase, spaced, hyphenated, abbreviated).

    Returns {category: [matching column names]} for every category in
    GLBA_KEYWORD_CATEGORIES, including empty lists for categories with no matches.
    """
    names = list(column_names) if column_names is not None else []
    return {
        category: find_matching_columns(names, keywords)
        for category, keywords in GLBA_KEYWORD_CATEGORIES.items()
    }


def check_glba_keyword_columns(column_names: Iterable[Any]) -> list[CheckResult]:
    """CheckResult wrapper around classify_glba_keyword_columns.

    One CheckResult per GLBA keyword category. status="failed" when the
    category has at least one matching column, "passed" when it has none.
    Confidence is always "low" in details.
    """
    try:
        matches = classify_glba_keyword_columns(column_names)
        results: list[CheckResult] = []
        for category, matched_columns in matches.items():
            status = "failed" if matched_columns else "passed"
            results.append(
                CheckResult(
                    check_name=CHECK_NAME,
                    status=status,
                    column=None,
                    issues_found=len(matched_columns),
                    dimension="",
                    details=sanitize_details(
                        {
                            "rule": category,
                            "regulation": "GLBA",
                            "confidence": "low",
                            "method": "column_keyword",
                            "matched_columns": matched_columns,
                        }
                    ),
                )
            )
        return results
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name=CHECK_NAME,
                status="error",
                column=None,
                issues_found=0,
                dimension="",
                details=sanitize_details({"error": str(exc)}),
            )
        ]


# ---------------------------------------------------------------------------
# 3. GLBA Full Compliance Orchestrator
# ---------------------------------------------------------------------------


def check_glba_compliance(df: pd.DataFrame) -> list[CheckResult]:
    """Run all GLBA rules against a DataFrame.

    - routing_number (regex_checksum/high): scanned against every column.
    - bank_account_number / loan_application_data / credit_history_data /
      tax_return_data (column_keyword/low): scanned once against the full column list.
    """
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        results: list[CheckResult] = []
        for column in df.columns:
            results.append(check_glba_routing_numbers(df, str(column)))
        results.extend(check_glba_keyword_columns(list(df.columns)))
        return results
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name=CHECK_NAME,
                status="error",
                column=None,
                issues_found=0,
                dimension="",
                details=sanitize_details({"error": str(exc)}),
            )
        ]
