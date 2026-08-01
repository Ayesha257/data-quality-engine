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
    # Weights for the composite Data Quality Score, matching the teacher's
    # 8-dimension rubric exactly (see scoring.py). Kept separate from the
    # "dimensions" list above -- that list is the older internal 7-dimension
    # set already used as CheckResult.dimension values by existing checks
    # (e.g. type_mismatch.py and outliers.py both report dimension=
    # "validity" internally); scoring.py maps check_name -> rubric
    # dimension explicitly rather than trusting CheckResult.dimension,
    # since the two dimension sets don't line up 1:1
    # (type_mismatch -> rubric "type_reliability", not "validity";
    # outliers -> rubric "outlier_risk", not "validity").
    "rubric_dimension_weights": {
        "completeness": 0.20,
        "validity": 0.20,
        "type_reliability": 0.15,
        "consistency": 0.15,
        "uniqueness": 0.10,
        "schema_quality": 0.10,
        "outlier_risk": 0.05,
        "freshness": 0.05,
    },
    # Duplicates / uniqueness (Task 2)
    # When set, these columns are always checked for duplicate keys in addition
    # to full-row duplicates. When null/omitted, keys are inferred from names
    # like "Customer No.", "Supplier Code", "Invoice No", etc.
    "duplicate_key_columns": None,
    "duplicate_normalize_strings": True,
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
