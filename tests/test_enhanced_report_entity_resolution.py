"""Tests for M6 Inspect injection in enhanced HTML reports."""

from __future__ import annotations

from pathlib import Path

from backend.engine.reports.report_generator import build_report_data
from backend.engine.ai_explanation.enhanced_report import generate_ai_enhanced_html_report


def test_enhanced_report_includes_entity_resolution_inspect(tmp_path):
    report_data = build_report_data(
        filepath="sample.xlsx",
        sheet_name="Sheet1",
        df_shape=(10, 3),
        header_row=0,
        processing_time_seconds=1.0,
        classification={"City": "categorical", "Country": "categorical"},
        check_results_by_name={},
        pii_summary_by_column={},
        fuzzy_results={},
        entity_resolution={
            "enabled": True,
            "summary": {"auto_match": 2, "review": 1, "no_match": 0, "total_values": 3},
            "columns": {
                "City": {
                    "entity_type": "city",
                    "resolutions": {
                        "LHR": {
                            "decision": "auto_match",
                            "confidence": 0.99,
                            "candidate": "Lahore",
                            "tier": "lookup",
                        }
                    },
                }
            },
            "review_queue": [],
        },
        score={
            "overall": 85.0,
            "rating": "Good",
            "readiness": "Ready",
            "data_quality_score": 85.0,
            "dimension_scores": {},
        },
    )

    out = tmp_path / "report.html"
    generate_ai_enhanced_html_report(report_data, str(out), api_key=None)

    html = Path(out).read_text(encoding="utf-8")
    assert 'id="entity-resolution"' in html
    assert "Entity Resolution (M6)" in html
    assert 'data-check="entity_resolution"' in html
    assert "Inspect" in html
    assert '"entity_resolution"' in html
