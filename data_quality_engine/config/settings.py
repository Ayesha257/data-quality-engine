"""Central thresholds and limits for Phase 1 (tuned for Easby dataset)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "OneDrive_1_26-01-2026 - latest data set"

SETTINGS = {
    # Ingestion
    "max_header_scan_rows": 15,
    "header_type_consistency_lookahead": 5,
    "merge_parent_header": True,
    "max_file_size_mb": 200,  # Goods Receipt Report.xls ~87MB
    # Outliers (IQR)
    "iqr_multiplier": 1.5,
    # Encoding
    "encoding_confidence_threshold": 0.8,
    # Fuzzy match
    "fuzzy_threshold": 90,
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
