"""Central thresholds and limits for Phase 1 (tuned for Easby dataset)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_DATASET_CANDIDATES = (
    REPO_ROOT / "OneDrive_1_26-01-2026 - latest data set",
    REPO_ROOT / "src" / "sample_data",
    REPO_ROOT / "sample_data",
)
DATASET_DIR = next((p for p in _DATASET_CANDIDATES if p.exists()), _DATASET_CANDIDATES[0])

SETTINGS = {
    # Ingestion
    "max_header_scan_rows": 15,
    "header_type_consistency_lookahead": 5,
    "merge_parent_header": True,
    "max_file_size_mb": 200,  # Goods Receipt Report.xls ~87MB
    # Outliers — Phase 1 default is IQR; KNN is optional comparison via PyOD
    "iqr_multiplier": 1.5,
    "outlier_default_method": "iqr",
    "outlier_knn_neighbors": 5,
    "outlier_knn_contamination": 0.05,
    # When one value is this share (or more) of non-null numeric rows, IQR
    # details get a caveat — count/status unchanged (Credit Limit-style cols).
    "outlier_dominant_value_ratio": 0.3,
    # Encoding (CSV bytes only — plan.md Section 10 item 6 / Section 4.3)
    "encoding_confidence_threshold": 0.8,
    "encoding_sample_size": 100_000,
    "encoding_fallback_list": ["utf-8-sig", "cp1252", "latin-1"],
    # Fuzzy match / standardization (plan.md Task 5, Section 4.4)
    "fuzzy_threshold": 90,
    "fuzzy_max_unique": 500,
    # Only these classifier roles are standardized by default (identifiers /
    # PII / measurements / dates are excluded to avoid false merges).
    "fuzzy_eligible_roles": ["categorical", "free_text"],
    # Compare fuzz.ratio on casefolded strings so Paid/PAID cluster together;
    # canonical form remains the most frequent original spelling.
    "fuzzy_case_insensitive": True,
    # PII
    "pii_mask_mode": "partial",
    "pii_show_last_n": 4,
    # Presidio NER is powerful but slow on large ERP files; regex covers phones/emails/CNIC
    "pii_use_presidio": False,
    # Paths
    "logs_dir": REPO_ROOT / "logs",
    "reports_dir": REPO_ROOT / "reports",
    "dataset_dir": DATASET_DIR,
    # Phase 2 M4: REST API. Where uploaded files land before/while a run
    # processes them. Generic across any client/dataset -- files are
    # namespaced by run_id, never by content, so nothing here depends on
    # what's inside the file.
    "uploads_dir": REPO_ROOT / "uploads",
    # Phase 2 M4: client rules management (base_rules.yaml + per-client
    # overrides). Overridable in tests the same way uploads_dir/reports_dir
    # already are, so rule-management tests never touch the real config/
    # directory or its checked-in example_client ruleset.
    "rules_config_dir": REPO_ROOT / "config",
    # Scoring
    "dimensions": [
        "completeness",
        "validity",
        "uniqueness",
        "consistency",
        "accuracy",
        "integrity",
        "sensitivity",
    ],
    # Weights for the composite Data Quality Score (see scoring.py).
    # Nine rubric dimensions including privacy_sensitivity (PII). HIPAA M9
    # exposure is separate but applies a composite ceiling — not a 10th dim.
    "rubric_dimension_weights": {
        "completeness": 0.18,
        "validity": 0.18,
        "type_reliability": 0.14,
        "consistency": 0.14,
        "uniqueness": 0.09,
        "schema_quality": 0.09,
        "outlier_risk": 0.04,
        "freshness": 0.04,
        "privacy_sensitivity": 0.10,
    },
    # Duplicates / uniqueness (Task 2)
    # When set, these columns are always checked for duplicate keys in addition
    # to full-row duplicates. When null/omitted, keys are inferred from names
    # like "Customer No.", "Supplier Code", "Invoice No", etc.
    "duplicate_key_columns": None,
    "duplicate_normalize_strings": True,
    # Phase 2 M6 — Entity Resolution (lookup → RapidFuzz → semantic)
    "entity_resolution_enabled": True,
    "entity_fuzzy_auto": 0.85,
    "entity_fuzzy_review": 0.75,
    "entity_semantic_auto": 0.78,
    "entity_semantic_review": 0.70,
    "entity_semantic_model": "all-MiniLM-L6-v2",
    "entity_max_fuzzy_candidates": 25,
    "entity_max_semantic_candidates": 15,
    "entity_resolution": {
        "enabled": True,
        "entity_types": {
            "city": {
                "columns": ["City", "city"],
                "canonicals": ["Lahore", "Karachi", "Islamabad", "Rawalpindi"],
                "aliases": {"LHR": "Lahore", "KHI": "Karachi", "ISB": "Islamabad"},
                "eligible_roles": ["categorical", "free_text"],
            },
            "country": {
                "columns": ["Country", "country"],
                "canonicals": ["United Kingdom", "Pakistan", "United States"],
                "aliases": {"UK": "United Kingdom", "USA": "United States", "US": "United States"},
                "eligible_roles": ["categorical"],
            },
        },
    },
    # Evidence-based uniqueness inference (see infer_uniqueness_keys /
    # uniqueness_evidence in checks/duplicates.py). A column is only treated
    # as an "expected unique" business key when its combined evidence score
    # clears this bar -- a matching name alone (e.g. "*Code") is not enough.
    "uniqueness_key_min_score": 0.6,
    # Below this many non-null values, uniqueness ratio / repeated-value
    # frequency are too noisy to trust, so we fall back to the name-pattern
    # signal alone (this is why small hand-built test frames keep working).
    "uniqueness_evidence_min_rows": 20,
    # Easby: numeric fields where zeros are suspicious (completeness/validity)
    "suspicious_zero_columns": [
        "GBP Amt-tax",
        "Amt-tax",
        "Margin%",
        "Cost",
        "Net Sell Price GBP",
        "Line Cost Price GBP",
        "Avg Cost of Current Stock",
        "Cumulative Avg Cost (GBP)",
        "Standard Cost",
        "Usage Per Week",
    ],
}
