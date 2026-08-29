"""
Ultra-Fast GDPR Scanner — Optimized for Quick Compliance Checks

Designed for speed (< 1 second on typical datasets):
- Aggressive sampling (10% of data)
- Minimal pattern matching
- Early exit strategies
- Cached pattern compilation
- HITL pre-filtering to remove false positives

Use this for:
- Quick compliance assessment
- Pre-screening before full audit
- Real-time compliance dashboards
- Performance-critical pipelines

For detailed analysis, use: backend/compliance/gdpr_detectors.py
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
import pandas as pd
import logging

# Ultra-fast pattern set (most common GDPR issues)
FAST_PATTERNS = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}",
    "phone": r"(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
    "ssn": r"\d{3}-\d{2}-\d{4}",
    "credit_card": r"(?:\d{4}[-\s]?){3}\d{4}",
    "ip_address": r"(?:\d{1,3}\.){3}\d{1,3}",
}


@lru_cache(maxsize=1)
def _get_fast_patterns() -> dict[str, re.Pattern[str]]:
    """Cache compiled fast patterns."""
    return {
        key: re.compile(pattern, re.IGNORECASE)
        for key, pattern in FAST_PATTERNS.items()
    }


def _filter_false_positives(results: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """
    Filter out obvious false positives before HITL review.
    
    Removes:
    - Unnamed columns
    - Generic column names
    - Temporary/test columns
    """
    skip_patterns = {'unnamed', 'column_', 'field_', 'temp', 'test', 'tmp', '_tmp'}
    
    filtered = {}
    for col_name, patterns in results.items():
        # Skip unnamed/generic columns
        if any(pattern in col_name.lower() for pattern in skip_patterns):
            continue
        filtered[col_name] = patterns
    
    return filtered


def fast_scan_column(
    series: pd.Series,
    pattern_types: set[str] | None = None,
) -> dict[str, int]:
    """
    Ultra-fast column scan with aggressive sampling.
    
    Args:
        series: Pandas Series to scan
        pattern_types: Specific patterns to check (None = all)
    
    Returns:
        Dictionary of pattern_type -> count
    """
    if series is None or series.empty:
        return {}
    
    # Sample only 10% of data (minimum 100 rows)
    sample_size = max(100, int(len(series) * 0.1))
    if len(series) > sample_size:
        sample = series.sample(n=sample_size, random_state=42)
    else:
        sample = series
    
    patterns = _get_fast_patterns()
    results = {}
    
    # Convert to string and filter empty values
    str_series = sample.fillna("").astype(str)
    
    for pattern_type, pattern in patterns.items():
        if pattern_types and pattern_type not in pattern_types:
            continue
        
        try:
            matches = str_series.str.contains(pattern, regex=True, na=False).sum()
            # Scale back to full dataset estimate
            if matches > 0:
                estimated = int(matches * (len(series) / len(sample)))
                results[pattern_type] = max(1, estimated)  # At least 1
        except Exception as e:
            logging.debug(f"Error scanning {pattern_type}: {e}")
            continue
    
    return results


def fast_scan_dataframe(
    df: pd.DataFrame,
    max_columns: int | None = None,
) -> dict[str, dict[str, int]]:
    """
    Ultra-fast DataFrame scan.
    
    Scans up to max_columns with aggressive sampling.
    Best for quick compliance dashboards.
    
    Args:
        df: DataFrame to scan
        max_columns: Limit columns scanned (None = all)
    
    Returns:
        {column_name: {pattern_type: count}}
    """
    if df is None or df.empty:
        return {}
    
    results = {}
    columns_to_scan = df.columns[:max_columns] if max_columns else df.columns
    
    for col in columns_to_scan:
        column_results = fast_scan_column(df[col])
        if column_results:
            results[col] = column_results
    
    return results


def quick_compliance_check(
    df: pd.DataFrame,
    strict: bool = False,
    apply_hitl_filter: bool = True,
) -> str:
    """
    One-liner compliance check: COMPLIANT or NON_COMPLIANT.
    
    Args:
        df: DataFrame to check
        strict: If True, any PII = NON_COMPLIANT
        apply_hitl_filter: Apply HITL filtering to skip false positives
    
    Returns:
        "COMPLIANT" or "NON_COMPLIANT"
    """
    try:
        results = fast_scan_dataframe(df, max_columns=20)
        
        if not results:
            return "COMPLIANT"
        
        # Filter out false positives if enabled
        if apply_hitl_filter:
            results = _filter_false_positives(results)
            if not results:
                return "COMPLIANT"
        
        if strict:
            return "NON_COMPLIANT" if results else "COMPLIANT"
        
        # Non-strict: only credit card or SSN = non-compliant
        for col_results in results.values():
            if col_results.get("credit_card", 0) > 0:
                return "NON_COMPLIANT"
            if col_results.get("ssn", 0) > 0:
                return "NON_COMPLIANT"
        
        return "COMPLIANT"
    
    except Exception as e:
        logging.error(f"Quick check failed: {e}")
        return "UNKNOWN"


def estimate_scan_time(df: pd.DataFrame) -> float:
    """Estimate scan time in seconds."""
    if df.empty:
        return 0.0
    
    # ~0.01s per 1000 sampled rows
    sample_size = max(100, int(len(df) * 0.1))
    patterns_count = len(FAST_PATTERNS)
    columns_count = len(df.columns)
    
    # Rough estimate: 0.01s per 10K rows * patterns * sqrt(columns)
    base_time = (sample_size / 10000) * patterns_count * (columns_count ** 0.5)
    return max(0.1, min(2.0, base_time))


if __name__ == "__main__":
    # Example usage
    df = pd.DataFrame({
        "name": ["John Doe", "Jane Smith"],
        "email": ["john@example.com", "jane@test.org"],
        "phone": ["555-1234", "555-5678"],
        "product": ["Widget", "Gadget"],
    })
    
    print(f"Quick Check: {quick_compliance_check(df)}")
    print(f"Detailed Results: {fast_scan_dataframe(df)}")
    print(f"Est. Scan Time: {estimate_scan_time(df):.2f}s")