"""
GDPR Personal Data Detector — Fast & Efficient
Optimized scanning with early exit, caching, and parallel processing.

Key Optimizations:
1. Sampling-based initial detection for large datasets
2. Compiled regex patterns with early exit
3. Column hint boosting to reduce false positives
4. Parallel processing for multi-column datasets
5. Incremental detection with early termination
6. HITL pre-filtering to remove false positives
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from backend.logging import get_logger, log_event

# GDPR Special Categories of Personal Data
GDPR_IDENTIFIERS = {
    "name": (r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", 0.85),
    "email": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", 0.95),
    "phone": (r"(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", 0.90),
    "ssn": (r"\b\d{3}-\d{2}-\d{4}\b", 0.98),
    "credit_card": (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", 0.92),
    "ip_address": (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 0.80),
    "passport": (r"\b[A-Z]{1,2}\d{6,9}\b", 0.85),
    "drivers_license": (r"\b[A-Z]{1,2}\d{5,8}\b", 0.80),
    "medical_record": (r"\b(?:MRN|EMR|EHR)[-\s]?\d{6,10}\b", 0.88),
    "financial_account": (r"\b[A-Z]{2}\d{8,17}\b", 0.75),
}

# Column hints for GDPR identifiers
COLUMN_HINTS = {
    "name": ("name", "full_name", "first_name", "last_name", "subject", "person"),
    "email": ("email", "mail", "contact_email", "e_mail"),
    "phone": ("phone", "telephone", "mobile", "cell", "contact", "number"),
    "ssn": ("ssn", "social_security", "tax_id", "sin"),
    "credit_card": ("card", "credit_card", "cc_number", "account_number", "pan"),
    "ip_address": ("ip", "ip_address", "ipv4", "ipv6"),
    "passport": ("passport", "passport_number"),
    "drivers_license": ("license", "drivers_license", "dlicense", "dl_number"),
    "medical_record": ("mrn", "medical_record", "emr", "ehr", "patient"),
    "financial_account": ("account", "account_number", "iban", "swift"),
}

# Compile regex patterns once
@lru_cache(maxsize=1)
def _get_compiled_patterns() -> dict[str, re.Pattern[str]]:
    """Cache compiled regex patterns."""
    return {
        key: re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        for key, (pattern, _) in GDPR_IDENTIFIERS.items()
    }


@dataclass
class GdprDetectionResult:
    """Single identifier detection result."""
    identifier_type: str
    count: int
    confidence: float
    sample_values: list[str] = field(default_factory=list)
    detection_method: str = "pattern"
    column_hint_match: bool = False


@dataclass
class GdprComplianceResult:
    """Overall GDPR compliance scan result."""
    status: str  # PERSONAL_DATA_DETECTED | NO_PERSONAL_DATA | error
    identifiers_found: list[str] = field(default_factory=list)
    identifier_counts: dict[str, int] = field(default_factory=dict)
    counts_by_column: dict[str, dict[str, int]] = field(default_factory=dict)
    columns_with_personal_data: list[str] = field(default_factory=list)
    detection_methods: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    scope: str = "PARTIAL_SCOPE"
    disclaimer: str = "GDPR compliance assessment based on pattern detection only."


def _column_hint_match(column_name: str | None, identifier_type: str) -> bool:
    """Check if column name matches GDPR identifier hints."""
    if not column_name:
        return False
    lowered = str(column_name).strip().lower().replace("_", " ")
    hints = COLUMN_HINTS.get(identifier_type, ())
    return any(hint in lowered for hint in hints)


def _sample_series(series: pd.Series, sample_size: int = 1000) -> pd.Series:
    """Smart sampling: use full series if small, otherwise sample."""
    if len(series) <= sample_size:
        return series
    # Sample start, middle, and end for better coverage ensuring >= sample_size
    s1 = sample_size // 3
    s2 = sample_size // 3
    s3 = sample_size - s1 - s2
    half_s2 = s2 // 2
    mid = len(series) // 2
    indices = sorted(set(
        list(range(0, s1)) +
        list(range(mid - half_s2, mid - half_s2 + s2)) +
        list(range(len(series) - s3, len(series)))
    ))
    return series.iloc[indices]


def _detect_in_series(
    series: pd.Series,
    column_name: str | None,
    *,
    max_detections: int = 5,
) -> dict[str, GdprDetectionResult]:
    """Detect GDPR identifiers in a single series with early exit."""
    results = {}
    patterns = _get_compiled_patterns()
    
    # Convert to string, dropping NaN
    str_series = series.fillna("").astype(str)
    
    # Use sampling for large series
    sample_series = _sample_series(str_series, sample_size=5000)
    
    for identifier_type, pattern in patterns.items():
        matches = []
        sample_values = []
        
        # Early exit if we've found enough
        if len(results) >= max_detections:
            break
            
        try:
            column_match = _column_hint_match(column_name, identifier_type)
            # For name detection, if not matching column hint, require strict multi-word pattern
            for value in sample_series:
                if not isinstance(value, str) or not value.strip():
                    continue
                
                if identifier_type == "name" and not column_match:
                    # Only match multi-word names when column name doesn't specify name
                    words = value.strip().split()
                    if len(words) < 2 or not pattern.search(value):
                        continue
                elif not pattern.search(value):
                    continue

                matches.append(value)
                if len(sample_values) < 3:  # Keep first 3 samples
                    sample_values.append(value[:50])  # Truncate for safety
            
            if matches:
                base_score = GDPR_IDENTIFIERS[identifier_type][1]
                
                # Scale count based on sampling
                estimated_count = int(len(matches) * (len(str_series) / len(sample_series)))
                
                results[identifier_type] = GdprDetectionResult(
                    identifier_type=identifier_type,
                    count=estimated_count,
                    confidence=min(0.99, base_score + (0.1 if column_match else 0)),
                    sample_values=sample_values,
                    detection_method="pattern_sampling",
                    column_hint_match=column_match,
                )
        except Exception as e:
            logging.debug(f"Error scanning {identifier_type}: {e}")
            continue
    
    return results


def scan_dataframe_for_gdpr(
    df: pd.DataFrame,
    *,
    run_id: str | None = None,
    max_workers: int = 4,
) -> GdprComplianceResult:
    """
    Scan DataFrame for GDPR personal data with parallel processing.
    
    Optimizations:
    - Parallel column scanning
    - Smart sampling for large datasets
    - Early exit on detection
    - Column hint matching
    """
    logger = get_logger(run_id) if run_id else None
    
    if df is None or df.empty:
        return GdprComplianceResult(status="NO_PERSONAL_DATA")
    
    all_detections: dict[str, dict[str, GdprDetectionResult]] = {}
    
    try:
        # Parallel scanning with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_detect_in_series, df[col], col): col
                for col in df.columns
            }
            
            for future in as_completed(futures):
                col = futures[future]
                try:
                    detections = future.result()
                    if detections:
                        all_detections[col] = detections
                except Exception as e:
                    logging.debug(f"Error scanning column {col}: {e}")
                    continue
        
        # Aggregate results
        identifier_counts = {}
        counts_by_column = {}
        columns_with_data = []
        
        for col, detections in all_detections.items():
            col_counts = {}
            for id_type, result in detections.items():
                identifier_counts[id_type] = identifier_counts.get(id_type, 0) + result.count
                col_counts[id_type] = result.count
            
            if col_counts:
                counts_by_column[col] = col_counts
                columns_with_data.append(col)
        
        status = "PERSONAL_DATA_DETECTED" if identifier_counts else "NO_PERSONAL_DATA"
        
        return GdprComplianceResult(
            status=status,
            identifiers_found=sorted(identifier_counts.keys()),
            identifier_counts=identifier_counts,
            counts_by_column=counts_by_column,
            columns_with_personal_data=sorted(columns_with_data),
            detection_methods={
                id_type: "pattern_sampling"
                for id_type in identifier_counts.keys()
            },
        )
    
    except Exception as e:
        if logger and run_id:
            log_event(
                logger,
                logging.ERROR,
                "GDPR scan failed",
                run_id=run_id,
                step="gdpr_scan",
                details={"error": str(e)},
            )
        return GdprComplianceResult(
            status="error",
            warnings=[f"GDPR scan failed: {e}"],
        )


def assess_gdpr_compliance(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
    *,
    run_id: str | None = None,
) -> GdprComplianceResult:
    """
    Assess GDPR compliance based on Phase 1 PII summaries.
    Converts PII findings to GDPR personal data categories.
    """
    logger = get_logger(run_id) if run_id else None
    
    try:
        identifier_counts = {}
        counts_by_column = {}
        columns_with_data = []
        
        # Map Phase 1 PII types to GDPR categories
        pii_to_gdpr = {
            "PERSON": "name",
            "EMAIL_ADDRESS": "email",
            "PHONE_NUMBER": "phone",
            "SSN": "ssn",
            "CREDIT_CARD": "credit_card",
            "IP_ADDRESS": "ip_address",
            "MEDICAL_LICENSE": "medical_record",
            "PASSPORT": "passport",
            "DRIVER_LICENSE": "drivers_license",
            "BANK_ACCOUNT": "financial_account",
        }
        
        for col, pii_summary in (pii_summary_by_column or {}).items():
            if not isinstance(pii_summary, dict):
                continue
            
            col_counts = {}
            for pii_type, count in pii_summary.items():
                if isinstance(count, dict):
                    count = count.get("count", 0)
                
                gdpr_type = pii_to_gdpr.get(pii_type)
                if gdpr_type and count > 0:
                    identifier_counts[gdpr_type] = identifier_counts.get(gdpr_type, 0) + count
                    col_counts[gdpr_type] = count
            
            if col_counts:
                counts_by_column[col] = col_counts
                columns_with_data.append(col)
        
        status = "PERSONAL_DATA_DETECTED" if identifier_counts else "NO_PERSONAL_DATA"
        
        if logger and run_id:
            log_event(
                logger,
                logging.INFO,
                "GDPR assessment complete",
                run_id=run_id,
                step="gdpr_compliance",
                details={
                    "status": status,
                    "identifiers_found": list(identifier_counts.keys()),
                    "total_hits": sum(identifier_counts.values()),
                    "columns_affected": len(columns_with_data),
                },
            )
        
        return GdprComplianceResult(
            status=status,
            identifiers_found=sorted(identifier_counts.keys()),
            identifier_counts=identifier_counts,
            counts_by_column=counts_by_column,
            columns_with_personal_data=sorted(columns_with_data),
            detection_methods={
                id_type: "pii_mapping"
                for id_type in identifier_counts.keys()
            },
        )
    
    except Exception as e:
        if logger and run_id:
            log_event(
                logger,
                logging.ERROR,
                "GDPR assessment failed",
                run_id=run_id,
                step="gdpr_compliance",
                details={"error": str(e)},
            )
        return GdprComplianceResult(
            status="error",
            warnings=[f"GDPR assessment failed: {e}"],
        )


# ============================================================================
# HITL FILTERING - Auto-reject low confidence findings
# ============================================================================

def filter_findings_for_hitl(
    findings_dict: dict[str, dict[str, int | GdprDetectionResult]],
    *,
    min_confidence: float = 0.85,
    skip_unnamed: bool = True,
    skip_patterns: list[str] | None = None,
) -> dict[str, dict[str, int | GdprDetectionResult]]:
    """
    Filter findings to only show HIGH confidence items to HITL.
    
    Automatically rejects:
    - Low confidence ("Heuristic") matches
    - Unnamed columns
    - Generic/empty column names
    - Columns matching skip patterns
    
    Args:
        findings_dict: {column_name: {identifier_type: result}}
        min_confidence: Minimum confidence to send to HITL (default: 0.85)
        skip_unnamed: Skip 'unnamed_*' columns (default: True)
        skip_patterns: Additional patterns to skip (default: None)
    
    Returns:
        Filtered findings for HITL review
    
    Example:
        result = scan_dataframe_for_gdpr(df)
        hitl_findings = filter_findings_for_hitl(
            result.counts_by_column,
            min_confidence=0.85,
            skip_unnamed=True
        )
        if hitl_findings:
            show_hitl_review(hitl_findings)
    """
    if not findings_dict:
        return {}
    
    if skip_patterns is None:
        skip_patterns = [
            'unnamed',
            'column_',
            'field_',
            'temp',
            'test',
            'debug',
            'tmp',
        ]
    
    filtered = {}
    
    for column_name, detections in findings_dict.items():
        # Skip unnamed columns
        if skip_unnamed and 'unnamed' in str(column_name).lower():
            continue
        
        # Skip patterns
        if any(pattern in str(column_name).lower() for pattern in skip_patterns):
            continue
        
        # Filter detections by confidence
        high_conf_detections = {}
        
        for id_type, result in detections.items():
            confidence = _get_confidence(result, id_type)
            if confidence >= min_confidence:
                high_conf_detections[id_type] = result
        
        if high_conf_detections:
            filtered[column_name] = high_conf_detections
    
    return filtered


def _get_confidence(result: Any, id_type: str | None = None) -> float:
    """Extract confidence from result (handles different formats)."""
    if hasattr(result, 'confidence'):
        return float(result.confidence)
    elif isinstance(result, dict) and 'confidence' in result:
        return float(result['confidence'])
    elif id_type and id_type in GDPR_IDENTIFIERS and isinstance(result, (int, float)):
        return GDPR_IDENTIFIERS[id_type][1]
    elif isinstance(result, (int, float)):
        return 0.9 if result > 0 else 0.0
    else:
        return 0.0


def prepare_hitl_findings(
    gdpr_result: GdprComplianceResult,
    min_confidence: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Convert GDPR scan results to HITL review format.
    
    Only includes high-confidence findings that warrant user review.
    """
    hitl_findings = []
    
    filtered_counts = filter_findings_for_hitl(
        gdpr_result.counts_by_column,
        min_confidence=min_confidence,
        skip_unnamed=True,
    )
    
    for column_name, detections in filtered_counts.items():
        for id_type, count in detections.items():
            hitl_findings.append({
                'column_name': column_name,
                'identifier_type': id_type,
                'count': count if isinstance(count, int) else count.count,
                'confidence': GDPR_IDENTIFIERS[id_type][1],
                'regulation': 'GDPR',
                'guessed_field': id_type,
            })
    
    return hitl_findings