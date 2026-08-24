"""Tests for SOX audit-trail completeness check -> Schema Quality dimension.

Covers:
  * Full audit-trail coverage (3/3 categories: creation, approval, modification).
  * Partial audit-trail coverage (2/3, 1/3 categories).
  * Zero audit-trail coverage (0/3 categories).
  * Keyword and fuzzy matching across snake_case, camelCase, PascalCase,
    spaced, and abbreviated conventions.
  * Integration with is_datetime_column for transaction_timestamp presence.
  * CheckResult contract validation (dimension="schema_quality", confidence="high").
  * Genericity across differently-structured tabular datasets.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.engine.checks.schema_quality import (
    AUDIT_TRAIL_CATEGORIES,
    check_audit_trail_completeness,
    classify_audit_trail_columns,
)
from backend.engine.column_classifier import is_datetime_column


# ===========================================================================
# 1. Audit Trail Completeness Scoring
# ===========================================================================


class TestAuditTrailCompleteness:
    def test_full_audit_trail_coverage_passes(self):
        columns = ["id", "amount", "created_by", "approved_by", "modified_at"]
        result = check_audit_trail_completeness(columns)

        assert result.status == "passed"
        assert result.issues_found == 0
        assert result.quality_ratio == 1.0
        assert result.dimension == "schema_quality"
        assert result.details["confidence"] == "high"
        assert result.details["coverage"] == "3/3"
        assert len(result.details["missing_categories"]) == 0
        assert set(result.details["matched_categories"]) == {
            "creation",
            "approval",
            "modification",
        }

    def test_two_of_three_categories_partial_coverage(self):
        # Missing approval
        columns = ["order_id", "created_at", "updated_at"]
        result = check_audit_trail_completeness(columns)

        assert result.status == "failed"
        assert result.issues_found == 1
        assert abs(result.quality_ratio - (2 / 3.0)) < 0.01
        assert result.details["coverage"] == "2/3"
        assert result.details["missing_categories"] == ["approval"]
        assert set(result.details["matched_categories"]) == {"creation", "modification"}

    def test_one_of_three_categories_partial_coverage(self):
        # Only creation present
        columns = ["customer_name", "created_date", "balance"]
        result = check_audit_trail_completeness(columns)

        assert result.status == "failed"
        assert result.issues_found == 2
        assert abs(result.quality_ratio - (1 / 3.0)) < 0.01
        assert result.details["coverage"] == "1/3"
        assert set(result.details["missing_categories"]) == {"approval", "modification"}
        assert result.details["matched_categories"] == ["creation"]

    def test_zero_coverage_flags_all_three_categories(self):
        columns = ["product_id", "product_name", "price", "category"]
        result = check_audit_trail_completeness(columns)

        assert result.status == "failed"
        assert result.issues_found == 3
        assert result.quality_ratio == 0.0
        assert result.details["coverage"] == "0/3"
        assert len(result.details["matched_categories"]) == 0
        assert len(result.details["missing_categories"]) == 3

    def test_accepts_pandas_dataframe_directly(self):
        df = pd.DataFrame(
            {
                "TxnID": [1, 2],
                "CreatedBy": ["Alice", "Bob"],
                "ReviewedBy": ["Charlie", "Diana"],
                "LastModified": ["2026-01-01", "2026-01-02"],
            }
        )
        result = check_audit_trail_completeness(df)
        assert result.status == "passed"
        assert result.quality_ratio == 1.0

    def test_empty_column_list_handles_gracefully(self):
        result = check_audit_trail_completeness([])
        assert result.status == "failed"
        assert result.issues_found == 3
        assert result.quality_ratio == 0.0

    def test_none_input_handles_gracefully(self):
        result = check_audit_trail_completeness(None)
        assert result.status == "failed"
        assert result.issues_found == 3
        assert result.quality_ratio == 0.0


# ===========================================================================
# 2. Fuzzy Column Name Matching Across Conventions
# ===========================================================================


class TestAuditTrailFuzzyColumnMatching:
    @pytest.mark.parametrize(
        "column_name",
        [
            "created_by",
            "CreatedBy",
            "createdBy",
            "created_at",
            "CreatedAt",
            "createdAt",
            "create_date",
            "creation_date",
            "CreationDate",
            "creation_time",
            "creator",
            "Creator",
            "created_dt",
            "create_dt",
            "entered_by",
            "record_created_at",
            "inserted_at",
            "Created By",
            "Creation Timestamp",
        ],
    )
    def test_creation_category_name_variants(self, column_name):
        matches = classify_audit_trail_columns([column_name])
        assert column_name in matches["creation"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "approved_by",
            "ApprovedBy",
            "approvedBy",
            "reviewed_by",
            "ReviewedBy",
            "reviewedBy",
            "approved_at",
            "approved_date",
            "ApprovedDate",
            "approver",
            "reviewer",
            "Reviewer",
            "approval_date",
            "approval_status",
            "reviewed_at",
            "authorized_by",
            "AuthorizedBy",
            "sign_off_by",
            "signed_off_by",
            "audited_by",
            "appr_by",
            "Approved By",
            "Reviewed By",
        ],
    )
    def test_approval_category_name_variants(self, column_name):
        matches = classify_audit_trail_columns([column_name])
        assert column_name in matches["approval"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "modified_at",
            "ModifiedAt",
            "modifiedAt",
            "updated_at",
            "UpdatedAt",
            "updatedAt",
            "modified_by",
            "ModifiedBy",
            "updated_by",
            "last_modified_at",
            "LastModifiedAt",
            "last_updated_at",
            "modified_date",
            "updated_date",
            "LastModifiedDate",
            "last_modified_date",
            "last_updated_date",
            "modification_date",
            "change_date",
            "changed_by",
            "mod_dt",
            "upd_date",
            "Last Modified",
            "Last Updated",
        ],
    )
    def test_modification_category_name_variants(self, column_name):
        matches = classify_audit_trail_columns([column_name])
        assert column_name in matches["modification"]

    @pytest.mark.parametrize(
        "column_name",
        [
            "order_total",
            "shipping_city",
            "product_description",
            "customer_email",
            "account_number",
            "is_active",
            "",
            None,
        ],
    )
    def test_unrelated_columns_do_not_match_audit_categories(self, column_name):
        matches = classify_audit_trail_columns([column_name])
        for cat, cols in matches.items():
            assert column_name not in cols


# ===========================================================================
# 3. Transaction Timestamp Reusable Helper Test
# ===========================================================================


class TestSoxTransactionTimestamp:
    def test_datetime_series_recognized(self):
        series = pd.Series(
            ["2026-01-15 10:30:00", "2026-01-15 11:45:00", "2026-01-15 14:00:00"]
        )
        assert is_datetime_column(series, "transaction_timestamp") is True

    def test_native_datetime_dtype_recognized(self):
        series = pd.to_datetime(pd.Series(["2026-01-15", "2026-01-16"]))
        assert is_datetime_column(series, "txn_date") is True

    def test_non_datetime_series_rejected(self):
        series = pd.Series(["Product A", "Product B", "Product C"])
        assert is_datetime_column(series, "description") is False


# ===========================================================================
# 4. Genericity Across Sample Datasets
# ===========================================================================


class TestSoxGenericityAcrossDatasets:
    def test_dataset_erp_journal_entries_full_sox_trail(self):
        df = pd.DataFrame(
            {
                "JournalEntryID": [1001, 1002, 1003],
                "AccountCode": ["1010", "2010", "4010"],
                "Debit": [5000.0, 0.0, 0.0],
                "Credit": [0.0, 2000.0, 3000.0],
                "CreatedBy": ["j_smith", "j_smith", "a_khan"],
                "CreatedAt": ["2026-01-01 08:00", "2026-01-01 08:05", "2026-01-01 09:00"],
                "ApprovedBy": ["c_controller", "c_controller", "c_controller"],
                "ApprovedDate": ["2026-01-02", "2026-01-02", "2026-01-02"],
                "LastModifiedDate": ["2026-01-03", "2026-01-03", "2026-01-03"],
            }
        )
        res = check_audit_trail_completeness(df)
        assert res.status == "passed"
        assert res.quality_ratio == 1.0
        assert is_datetime_column(df["CreatedAt"], "CreatedAt") is True

    def test_dataset_raw_inventory_snapshot_zero_audit_trail(self):
        df = pd.DataFrame(
            {
                "SKU": ["SKU-001", "SKU-002"],
                "Warehouse": ["WH-East", "WH-West"],
                "QtyOnHand": [150, 420],
                "ReorderPoint": [50, 100],
            }
        )
        res = check_audit_trail_completeness(df)
        assert res.status == "failed"
        assert res.issues_found == 3
        assert res.quality_ratio == 0.0
