"""Verify business-key duplicate findings feed the uniqueness composite dimension."""

from __future__ import annotations

import pandas as pd

from data_quality_engine.engine.checks.duplicates import check_duplicates_frame
from data_quality_engine.engine.scoring import compute_data_quality_score


def _composite_for_df(df: pd.DataFrame) -> float:
    dup_results = check_duplicates_frame(df)
    key_results = [r for r in dup_results if r.check_name == "duplicate_keys"]
    assert key_results, "expected at least one business-key duplicate check"

    dimension_results = {
        "completeness": [],
        "validity": [],
        "type_reliability": [],
        "consistency": [],
        "uniqueness": dup_results,
        "schema_quality": [],
        "outlier_risk": [],
        "freshness": [],
        "privacy_sensitivity": [],
    }
    out = compute_data_quality_score(dimension_results)
    return out["data_quality_score"], out["dimension_scores"]["uniqueness"]


def test_business_key_duplicate_lowers_uniqueness_score():
    clean = pd.DataFrame(
        {
            "order_id": ["ORD-1", "ORD-2", "ORD-3"],
            "product_code": ["A", "B", "C"],
            "quantity": [10, 20, 30],
        }
    )
    duped = pd.DataFrame(
        {
            "order_id": ["ORD-1", "ORD-2", "ORD-1"],
            "product_code": ["A", "B", "C"],
            "quantity": [10, 20, 30],
        }
    )

    score_clean, uniq_clean = _composite_for_df(clean)
    score_dup, uniq_dup = _composite_for_df(duped)

    assert uniq_clean["score"] == 100.0
    assert uniq_dup["score"] < 100.0
    assert score_dup < score_clean
    assert any(r.status == "failed" and r.check_name == "duplicate_keys" for r in check_duplicates_frame(duped))
