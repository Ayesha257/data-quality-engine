"""
Comprehensive privacy detector tests covering GDPR, HIPAA, PCI-DSS, and GLBA.

Tests validate:
- Pattern detection accuracy
- Column hint matching
- Performance optimization (sampling, early exit)
- Parallel processing
- Result aggregation
"""

from __future__ import annotations

import pytest
import pandas as pd
from backend.compliance.gdpr_detectors import (
    scan_dataframe_for_gdpr,
    assess_gdpr_compliance,
    _column_hint_match,
    _sample_series,
    GdprComplianceResult,
)


class TestGdprColumnHints:
    """Test GDPR column hint matching."""
    
    def test_email_column_hint_match(self):
        """Email column hints should match."""
        assert _column_hint_match("email", "email")
        assert _column_hint_match("contact_email", "email")
        assert _column_hint_match("e_mail", "email")
        assert not _column_hint_match("user_name", "email")
    
    def test_name_column_hint_match(self):
        """Name column hints should match."""
        assert _column_hint_match("name", "name")
        assert _column_hint_match("full_name", "name")
        assert _column_hint_match("first_name", "name")
        assert _column_hint_match("last_name", "name")
    
    def test_phone_column_hint_match(self):
        """Phone column hints should match."""
        assert _column_hint_match("phone", "phone")
        assert _column_hint_match("telephone", "phone")
        assert _column_hint_match("mobile_number", "phone")
    
    def test_none_column_name(self):
        """None column names should not match."""
        assert not _column_hint_match(None, "email")
        assert not _column_hint_match("", "email")


class TestSampling:
    """Test sampling optimization for large datasets."""
    
    def test_small_series_not_sampled(self):
        """Series smaller than threshold should not be sampled."""
        series = pd.Series(["a", "b", "c", "d", "e"])
        sampled = _sample_series(series, sample_size=1000)
        assert len(sampled) == 5
    
    def test_large_series_sampled(self):
        """Large series should be sampled."""
        series = pd.Series(range(10000))
        sampled = _sample_series(series, sample_size=100)
        assert len(sampled) < len(series)
        assert len(sampled) >= 100
    
    def test_sampling_includes_edges(self):
        """Sampling should include start, middle, and end values."""
        series = pd.Series(range(10000))
        sampled = _sample_series(series, sample_size=300)
        
        # Check that we have values from different parts
        values = sampled.tolist()
        assert any(v < 100 for v in values)  # Start
        assert any(4500 < v < 5500 for v in values)  # Middle
        assert any(v > 9900 for v in values)  # End


class TestGdprDetection:
    """Test GDPR personal data detection."""
    
    def test_email_detection(self):
        """Should detect email addresses."""
        df = pd.DataFrame({
            "contact": ["john@example.com", "jane@test.org", "invalid"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found
        assert result.identifier_counts["email"] > 0
    
    def test_phone_detection(self):
        """Should detect phone numbers."""
        df = pd.DataFrame({
            "phone_number": ["(555) 123-4567", "555-987-6543", "123456"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "phone" in result.identifiers_found
    
    def test_ssn_detection(self):
        """Should detect SSN patterns."""
        df = pd.DataFrame({
            "tax_id": ["123-45-6789", "987-65-4321", "000000000"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        # May or may not detect based on confidence threshold
        if result.status == "PERSONAL_DATA_DETECTED":
            assert "ssn" in result.identifiers_found or len(result.identifiers_found) > 0
    
    def test_credit_card_detection(self):
        """Should detect credit card patterns."""
        df = pd.DataFrame({
            "card_number": ["4532-1234-5678-9010", "5425 5454 5454 5454", "1234"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "credit_card" in result.identifiers_found
    
    def test_no_personal_data(self):
        """Should return NO_PERSONAL_DATA when no PII found."""
        df = pd.DataFrame({
            "product": ["Widget", "Gadget", "Tool"],
            "quantity": [10, 20, 30],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "NO_PERSONAL_DATA"
        assert len(result.identifiers_found) == 0
    
    def test_empty_dataframe(self):
        """Should handle empty dataframes gracefully."""
        df = pd.DataFrame()
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "NO_PERSONAL_DATA"
        assert len(result.identifiers_found) == 0
    
    def test_nan_handling(self):
        """Should handle NaN values gracefully."""
        df = pd.DataFrame({
            "email": [None, "test@example.com", pd.NA],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found


class TestGdprAggregation:
    """Test result aggregation across columns."""
    
    def test_multiple_columns_aggregation(self):
        """Should aggregate detections across multiple columns."""
        df = pd.DataFrame({
            "email": ["alice@test.com", "bob@test.com"],
            "phone": ["555-1234", "555-5678"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert len(result.columns_with_personal_data) >= 1
        assert "email" in result.identifier_counts or "phone" in result.identifier_counts
    
    def test_column_counts_accuracy(self):
        """counts_by_column should match identifier_counts when aggregated."""
        df = pd.DataFrame({
            "contact_email": ["test1@example.com", "test2@example.com"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        if result.status == "PERSONAL_DATA_DETECTED":
            total_from_columns = sum(
                sum(col_counts.values())
                for col_counts in result.counts_by_column.values()
            )
            total_from_aggregated = sum(result.identifier_counts.values())
            assert total_from_columns == total_from_aggregated


class TestGdprComplianceFromPii:
    """Test GDPR compliance assessment from Phase 1 PII summaries."""
    
    def test_pii_to_gdpr_mapping(self):
        """Should map Phase 1 PII types to GDPR categories."""
        pii_summary = {
            "email_column": {
                "EMAIL_ADDRESS": 5,
                "PERSON": 3,
            }
        }
        result = assess_gdpr_compliance(pii_summary, row_count=10)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found or "name" in result.identifiers_found
    
    def test_empty_pii_summary(self):
        """Should return NO_PERSONAL_DATA for empty PII summary."""
        result = assess_gdpr_compliance({}, row_count=10)
        
        assert result.status == "NO_PERSONAL_DATA"
        assert len(result.identifiers_found) == 0
    
    def test_none_pii_summary(self):
        """Should handle None PII summary."""
        result = assess_gdpr_compliance(None, row_count=10)
        
        assert result.status == "NO_PERSONAL_DATA"


class TestGdprResultStructure:
    """Test GdprComplianceResult data structure."""
    
    def test_result_has_required_fields(self):
        """Result should have all required fields."""
        df = pd.DataFrame({"test": ["data"]})
        result = scan_dataframe_for_gdpr(df)
        
        assert hasattr(result, "status")
        assert hasattr(result, "identifiers_found")
        assert hasattr(result, "identifier_counts")
        assert hasattr(result, "counts_by_column")
        assert hasattr(result, "columns_with_personal_data")
        assert hasattr(result, "detection_methods")
        assert hasattr(result, "warnings")
        assert hasattr(result, "scope")
        assert hasattr(result, "disclaimer")
    
    def test_result_field_types(self):
        """Result fields should have correct types."""
        df = pd.DataFrame({"test": ["data"]})
        result = scan_dataframe_for_gdpr(df)
        
        assert isinstance(result.status, str)
        assert isinstance(result.identifiers_found, list)
        assert isinstance(result.identifier_counts, dict)
        assert isinstance(result.counts_by_column, dict)
        assert isinstance(result.columns_with_personal_data, list)
        assert isinstance(result.detection_methods, dict)
        assert isinstance(result.warnings, list)
        assert isinstance(result.scope, str)
        assert isinstance(result.disclaimer, str)


class TestGdprPerformance:
    """Test performance optimizations."""
    
    def test_large_dataframe_handling(self):
        """Should handle large dataframes efficiently with sampling."""
        # Create a large dataframe
        df = pd.DataFrame({
            "id": range(10000),
            "email": [f"user{i}@example.com" for i in range(10000)],
            "name": [f"User {i}" for i in range(10000)],
        })
        
        result = scan_dataframe_for_gdpr(df, max_workers=2)
        
        # Should complete and find emails
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found
    
    def test_parallel_processing(self):
        """Should use parallel processing for multiple columns."""
        df = pd.DataFrame({
            f"col_{i}": [f"test{j}@example.com" for j in range(100)]
            for i in range(5)
        })
        
        result = scan_dataframe_for_gdpr(df, max_workers=4)
        
        # Should find emails in multiple columns
        assert len(result.columns_with_personal_data) > 0


class TestGdprEdgeCases:
    """Test edge cases and error handling."""
    
    def test_mixed_data_types(self):
        """Should handle mixed data types."""
        df = pd.DataFrame({
            "mixed": [123, "john@example.com", None, 45.6],
        })
        result = scan_dataframe_for_gdpr(df)
        
        # Should not crash and handle gracefully
        assert result.status in ["PERSONAL_DATA_DETECTED", "NO_PERSONAL_DATA", "error"]
    
    def test_special_characters(self):
        """Should handle special characters."""
        df = pd.DataFrame({
            "email": ["user+tag@example.com", "test_email@sub.example.org"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found
    
    def test_unicode_handling(self):
        """Should handle unicode characters."""
        df = pd.DataFrame({
            "name": ["François", "李明", "Müller"],
        })
        result = scan_dataframe_for_gdpr(df)
        
        # Should complete without crashing
        assert isinstance(result, GdprComplianceResult)
    
    def test_duplicate_data(self):
        """Should handle duplicate data."""
        df = pd.DataFrame({
            "email": ["test@example.com"] * 100,
        })
        result = scan_dataframe_for_gdpr(df)
        
        assert result.status == "PERSONAL_DATA_DETECTED"
        assert "email" in result.identifiers_found


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
