"""
HITL Pre-filtering Logic

Automatically filters out low-confidence and obvious false positives
before sending findings to Human-in-the-Loop review.

This prevents users from reviewing 100s of useless findings.

Example Usage:
    from backend.compliance.hitl_filters import filter_for_hitl
    
    findings = scan_dataframe_for_gdpr(df)
    hitl_ready = filter_for_hitl(findings.counts_by_column)
    
    if hitl_ready:
        show_hitl_review(hitl_ready)
    else:
        finalize_report(findings)  # No real findings, skip HITL
"""

from __future__ import annotations
from typing import Any


# Default patterns to always skip
DEFAULT_SKIP_PATTERNS = {
    'unnamed',
    'column_',
    'field_',
    'temp',
    'test',
    'debug',
    'tmp',
    '_tmp',
    'unnamed_',
}

# Default minimum confidence for HITL review
DEFAULT_MIN_CONFIDENCE = 0.85


class HITLFilter:
    """Pre-filter findings before sending to HITL review."""
    
    def __init__(
        self,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        skip_patterns: set[str] | None = None,
        skip_unnamed: bool = True,
    ):
        """
        Initialize HITL filter.
        
        Args:
            min_confidence: Minimum confidence threshold (0-1)
            skip_patterns: Patterns to skip (e.g., 'unnamed', 'test_')
            skip_unnamed: Skip 'unnamed_*' columns
        """
        self.min_confidence = min_confidence
        self.skip_patterns = skip_patterns or DEFAULT_SKIP_PATTERNS
        self.skip_unnamed = skip_unnamed
    
    def filter(self, findings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """
        Filter findings for HITL review.
        
        Args:
            findings: {column_name: {identifier_type: result}}
        
        Returns:
            Filtered findings ready for HITL
        """
        if not findings:
            return {}
        
        filtered = {}
        
        for column_name, detections in findings.items():
            # Skip unnamed columns
            if self.skip_unnamed and 'unnamed' in str(column_name).lower():
                continue
            
            # Skip matching patterns
            if self._should_skip(column_name):
                continue
            
            # Filter by confidence
            high_conf = self._filter_by_confidence(detections)
            
            if high_conf:
                filtered[column_name] = high_conf
        
        return filtered
    
    def _should_skip(self, column_name: str) -> bool:
        """Check if column matches skip patterns."""
        lower_name = str(column_name).lower()
        return any(pattern in lower_name for pattern in self.skip_patterns)
    
    def _filter_by_confidence(self, detections: dict[str, Any]) -> dict[str, Any]:
        """Filter detections by confidence threshold."""
        filtered = {}
        
        for id_type, result in detections.items():
            # Handle different result types
            confidence = self._get_confidence(result)
            
            if confidence >= self.min_confidence:
                filtered[id_type] = result
        
        return filtered
    
    def _get_confidence(self, result: Any) -> float:
        """Extract confidence from result (handles different formats)."""
        if hasattr(result, 'confidence'):
            return result.confidence
        elif isinstance(result, dict) and 'confidence' in result:
            return result['confidence']
        elif isinstance(result, (int, float)):
            return 0.0  # Raw counts have no confidence
        else:
            return 0.0
    
    def add_skip_pattern(self, pattern: str) -> None:
        """Add additional pattern to skip."""
        self.skip_patterns.add(pattern.lower())
    
    def remove_skip_pattern(self, pattern: str) -> None:
        """Remove skip pattern."""
        self.skip_patterns.discard(pattern.lower())
    
    def set_min_confidence(self, min_confidence: float) -> None:
        """Update minimum confidence threshold."""
        if 0.0 <= min_confidence <= 1.0:
            self.min_confidence = min_confidence
        else:
            raise ValueError("Confidence must be between 0.0 and 1.0")


# Convenience functions

def filter_for_hitl(
    findings: dict[str, dict[str, Any]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    skip_patterns: set[str] | None = None,
    skip_unnamed: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Quick filter function for HITL findings.
    
    Args:
        findings: {column_name: {identifier_type: result}}
        min_confidence: Minimum confidence (0-1)
        skip_patterns: Patterns to skip
        skip_unnamed: Skip 'unnamed_*' columns
    
    Returns:
        Filtered findings
    
    Example:
        hitl_findings = filter_for_hitl(
            result.counts_by_column,
            min_confidence=0.85
        )
    """
    filter_obj = HITLFilter(
        min_confidence=min_confidence,
        skip_patterns=skip_patterns,
        skip_unnamed=skip_unnamed
    )
    return filter_obj.filter(findings)


def should_send_to_hitl(
    findings: dict[str, dict[str, Any]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> bool:
    """
    Check if there are any real findings worth reviewing by humans.
    
    Args:
        findings: {column_name: {identifier_type: result}}
        min_confidence: Minimum confidence threshold
    
    Returns:
        True if findings warrant HITL review
    
    Example:
        if should_send_to_hitl(result.counts_by_column):
            show_hitl_ui()
        else:
            finalize_report()
    """
    filtered = filter_for_hitl(findings, min_confidence=min_confidence)
    return len(filtered) > 0


def count_filtered_findings(
    findings: dict[str, dict[str, Any]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, int]:
    """
    Count findings by type after filtering.
    
    Args:
        findings: {column_name: {identifier_type: result}}
        min_confidence: Minimum confidence threshold
    
    Returns:
        {identifier_type: count}
    
    Example:
        counts = count_filtered_findings(result.counts_by_column)
        print(f"Real findings: {counts}")  # {'email': 5, 'phone': 3}
    """
    filtered = filter_for_hitl(findings, min_confidence=min_confidence)
    counts = {}
    
    for column_results in filtered.values():
        for id_type, result in column_results.items():
            count = result.count if hasattr(result, 'count') else 1
            counts[id_type] = counts.get(id_type, 0) + count
    
    return counts


# Configuration presets

class HITLPresets:
    """Pre-configured filter settings."""
    
    # Strict: Only high-confidence findings
    STRICT = {
        'min_confidence': 0.95,
        'skip_patterns': DEFAULT_SKIP_PATTERNS,
        'skip_unnamed': True,
    }
    
    # Balanced: Default settings (recommended)
    BALANCED = {
        'min_confidence': 0.85,
        'skip_patterns': DEFAULT_SKIP_PATTERNS,
        'skip_unnamed': True,
    }
    
    # Loose: Allow lower confidence matches
    LOOSE = {
        'min_confidence': 0.75,
        'skip_patterns': DEFAULT_SKIP_PATTERNS,
        'skip_unnamed': True,
    }
    
    # No filtering: Send everything to HITL
    NO_FILTER = {
        'min_confidence': 0.0,
        'skip_patterns': set(),
        'skip_unnamed': False,
    }
    
    @staticmethod
    def get_filter(preset: str = 'BALANCED') -> HITLFilter:
        """
        Get pre-configured filter.
        
        Args:
            preset: 'STRICT', 'BALANCED', 'LOOSE', or 'NO_FILTER'
        
        Returns:
            Configured HITLFilter instance
        
        Example:
            filter_obj = HITLPresets.get_filter('STRICT')
            filtered = filter_obj.filter(findings)
        """
        config = getattr(HITLPresets, preset, HITLPresets.BALANCED)
        return HITLFilter(**config)


if __name__ == "__main__":
    # Example usage
    print("HITL Filtering Examples:\n")
    
    # Example findings
    example_findings = {
        'customer_email': {'email': type('obj', (), {'count': 5, 'confidence': 0.95})()},
        'phone_number': {'phone': type('obj', (), {'count': 3, 'confidence': 0.90})()},
        'unnamed_5': {'email': type('obj', (), {'count': 1, 'confidence': 0.70})()},
        'test_data': {'ssn': type('obj', (), {'count': 0, 'confidence': 0.60})()},
    }
    
    print("Original findings:", len(example_findings), "columns")
    
    # Filter with default settings
    filtered = filter_for_hitl(example_findings)
    print("After filtering:", len(filtered), "columns")
    print("Columns to review:", list(filtered.keys()))
    
    # Check if worth sending to HITL
    if should_send_to_hitl(example_findings):
        print("✓ Send to HITL review")
    else:
        print("✗ Skip HITL, auto-finalize")
    
    # Count findings
    counts = count_filtered_findings(example_findings)
    print("Filtered finding counts:", counts)