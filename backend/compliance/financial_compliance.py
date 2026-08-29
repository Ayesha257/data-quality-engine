"""
Financial compliance scanning and resolution coordinator for PCI-DSS, GLBA, and SOX.

Orchestrates detection, classifies findings by confidence tier (high, medium, low),
gates low-confidence findings behind Human-in-the-Loop (HITL) confirmation via APIPrompt,
and produces resolved findings ready for multi-framework compliance reporting.
"""

from __future__ import annotations

from typing import Any
import pandas as pd

from backend.compliance.pci_dss_detectors import (
    _is_expiry_column,
    detect_card_expiry,
    detect_cvv_column,
    detect_pan,
)
from backend.compliance.glba_detectors import (
    classify_glba_keyword_columns,
    detect_routing_number,
)
from backend.engine.checks.schema_quality import check_audit_trail_completeness
from backend.engine.column_classifier import is_datetime_column


def _filter_pan_candidates(series: pd.Series) -> pd.Series:
    """Fast candidate pre-filter for PAN detection to avoid unnecessary row loops."""
    if series.empty:
        return series
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series.iloc[0:0]
    str_s = series.astype(str)
    return series[str_s.str.len() >= 13]


def _filter_routing_candidates(series: pd.Series) -> pd.Series:
    """Fast candidate pre-filter for ABA routing numbers to avoid unnecessary row loops."""
    if series.empty:
        return series
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series.iloc[0:0]
    str_s = series.astype(str)
    return series[str_s.str.len().between(8, 15)]


def scan_pci_dss_findings(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for PCI DSS findings categorized by confidence tier."""
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"high": high, "medium": medium, "low": low}

    # 1. PAN (Primary Account Number) -> High confidence
    for col in df.columns:
        series = df[col].dropna()
        candidates = _filter_pan_candidates(series)
        if candidates.empty:
            continue
        matches = 0
        masked_samples = []
        for val in candidates:
            res = detect_pan(val)
            if res.get("match"):
                matches += 1
                if len(masked_samples) < 5 and res.get("masked_value"):
                    masked_samples.append(res["masked_value"])
        if matches > 0:
            high.append({
                "rule": "pci_pan",
                "field_name": "PAN",
                "display_name": "Primary Account Number",
                "regulation": "PCI_DSS",
                "column_name": str(col),
                "confidence": "high",
                "issues_found": matches,
                "total_rows": len(df),
                "masked_samples": masked_samples,
                "description": f"Found {matches} PAN (credit/debit card) value(s) matching Luhn checksum in column '{col}'.",
            })

    # 2. Card Expiry -> Medium confidence
    # Name-gate once per column (same gate detect_card_expiry uses) so we do
    # not walk every cell on unrelated columns.
    for col in df.columns:
        col_name = str(col)
        if not _is_expiry_column(col_name):
            continue
        series = df[col].dropna()
        matches = 0
        samples = []
        for val in series:
            res = detect_card_expiry(val, col_name)
            if res.get("match"):
                matches += 1
                if len(samples) < 5 and res.get("normalized_value"):
                    samples.append(res["normalized_value"])
        if matches > 0:
            medium.append({
                "rule": "pci_card_expiry",
                "field_name": "card_expiry",
                "display_name": "Card Expiration Date",
                "regulation": "PCI_DSS",
                "column_name": str(col),
                "confidence": "medium",
                "issues_found": matches,
                "total_rows": len(df),
                "samples": samples,
                "description": f"Found {matches} card expiration date value(s) in column '{col}'.",
            })

    # 3. CVV Column -> Low confidence (Requires HITL)
    for col in df.columns:
        res = detect_cvv_column(str(col))
        if res.get("match"):
            low.append({
                "rule": "pci_cvv",
                "field_name": "cvv",
                "guessed_field": "cvv",
                "display_name": "Card Verification Value (CVV/CVC)",
                "regulation": "PCI_DSS",
                "column_name": str(col),
                "confidence": "low",
                "issues_found": 1,
                "total_rows": len(df),
                "description": f"Column '{col}' matches CVV/CVC/security code naming conventions.",
            })

    return {"high": high, "medium": medium, "low": low}


def scan_glba_findings(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for GLBA findings categorized by confidence tier."""
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"high": high, "medium": medium, "low": low}

    # 1. Routing Number -> High confidence
    for col in df.columns:
        series = df[col].dropna()
        candidates = _filter_routing_candidates(series)
        if candidates.empty:
            continue
        matches = 0
        masked_samples = []
        for val in candidates:
            res = detect_routing_number(val)
            if res.get("match"):
                matches += 1
                if len(masked_samples) < 5 and res.get("masked_value"):
                    masked_samples.append(res["masked_value"])
        if matches > 0:
            high.append({
                "rule": "glba_routing_number",
                "field_name": "routing_number",
                "display_name": "ABA Bank Routing Number",
                "regulation": "GLBA",
                "column_name": str(col),
                "confidence": "high",
                "issues_found": matches,
                "total_rows": len(df),
                "masked_samples": masked_samples,
                "description": f"Found {matches} 9-digit ABA routing number(s) passing checksum in column '{col}'.",
            })

    # 2. GLBA Keyword Columns -> Low confidence (Requires HITL)
    keyword_matches = classify_glba_keyword_columns(list(df.columns))
    category_labels = {
        "bank_account_number": "Bank Account Number",
        "loan_application_data": "Loan Application Data",
        "credit_history_data": "Credit History Data",
        "tax_return_data": "Tax Return Data",
    }
    for category, matched_cols in keyword_matches.items():
        for col in matched_cols:
            low.append({
                "rule": f"glba_{category}",
                "field_name": category,
                "guessed_field": category,
                "display_name": category_labels.get(category, category.replace("_", " ").title()),
                "regulation": "GLBA",
                "column_name": str(col),
                "confidence": "low",
                "issues_found": 1,
                "total_rows": len(df),
                "description": f"Column '{col}' name suggests {category_labels.get(category, category)}.",
            })

    return {"high": high, "medium": medium, "low": low}


def scan_sox_findings(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for SOX audit-trail and timestamp findings (High confidence)."""
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"high": high, "medium": medium, "low": low}

    # 1. SOX Audit Trail Completeness Check
    audit_res = check_audit_trail_completeness(df)
    matched_cats = audit_res.details.get("matched_categories", [])
    missing_cats = audit_res.details.get("missing_categories", [])
    if audit_res.status == "passed" or matched_cats:
        high.append({
            "rule": "sox_audit_trail_headers",
            "field_name": "audit_trail_headers",
            "display_name": "Audit Trail Header Completeness",
            "regulation": "SOX",
            "column_name": ", ".join(matched_cats) if matched_cats else "None",
            "confidence": "high",
            "status": audit_res.status,
            "issues_found": audit_res.issues_found,
            "coverage": audit_res.details.get("coverage", "0/3"),
            "matched_categories": matched_cats,
            "missing_categories": missing_cats,
            "description": f"Audit trail coverage: {audit_res.details.get('coverage', '0/3')} categories present ({', '.join(matched_cats) if matched_cats else 'None'}).",
        })

    # 2. Transaction Timestamp check
    for col in df.columns:
        if is_datetime_column(df[col], str(col)):
            high.append({
                "rule": "sox_transaction_timestamp",
                "field_name": "transaction_timestamp",
                "display_name": "Transaction Timestamp Presence",
                "regulation": "SOX",
                "column_name": str(col),
                "confidence": "high",
                "issues_found": 0,
                "description": f"Column '{col}' identified as valid transaction timestamp.",
            })

    return {"high": high, "medium": medium, "low": low}


def run_compliance_scan(
    df: pd.DataFrame,
    regulation: str,
    prompt: Any | None = None,
    resolved_decisions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """
    Scans DataFrame for the specified financial regulation (PCI_DSS, GLBA, or SOX),
    or delegates to privacy_compliance for privacy regulations (GDPR, CCPA).
    Returns resolved findings grouped into confidence tiers.
    """
    reg_norm = str(regulation).upper().replace("-", "_")

    if reg_norm in ("GDPR", "CCPA"):
        from backend.compliance.privacy_compliance import run_privacy_scan
        return run_privacy_scan(
            df,
            regulation=reg_norm,
            prompt=prompt,
            resolved_decisions=resolved_decisions,
        )

    if reg_norm == "PCI_DSS":
        scanned = scan_pci_dss_findings(df)
    elif reg_norm == "GLBA":
        scanned = scan_glba_findings(df)
    elif reg_norm == "SOX":
        scanned = scan_sox_findings(df)
    else:
        scanned = {"high": [], "medium": [], "low": []}

    high = scanned["high"]
    medium = scanned["medium"]
    low = scanned["low"]

    confirmed: list[dict[str, Any]] = []

    # Process low-confidence findings through HITL if pre-resolved decisions provided
    # or prompt is interactive CLI (avoiding blocking automated background API pipelines)
    if low:
        decisions: dict[str, bool] = {}
        if resolved_decisions is not None:
            decisions = resolved_decisions
        elif prompt is not None and getattr(prompt, "is_interactive", False) and hasattr(prompt, "confirm_compliance"):
            decisions = prompt.confirm_compliance(low)

        for finding in low:
            col = finding.get("column_name")
            if col and decisions.get(col, False):
                confirmed_item = dict(finding)
                confirmed_item["confidence"] = "confirmed"
                confirmed_item["status"] = "confirmed"
                confirmed.append(confirmed_item)

    disclaimer = (
        f"This report flags compliance-relevant data patterns. "
        f"It does not certify legal compliance with {regulation}."
    )

    all_resolved = [*high, *medium, *confirmed]

    return {
        "regulation": regulation,
        "disclaimer": disclaimer,
        "confidence_tiers": {
            "High Confidence": high,
            "Medium Confidence": medium,
            "Confirmed (User-Verified)": confirmed,
        },
        "resolved_findings": all_resolved,
        "low_findings_pending": low if (resolved_decisions is None) else [],
    }