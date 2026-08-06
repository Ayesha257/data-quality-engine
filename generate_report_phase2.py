"""
Phase 2 entrypoint: runs the exact same Phase 1 pipeline as
`generate_report.py` (nothing there is modified), then additionally
produces an AI-enhanced HTML report with an "Inspect" button on every
finding that shows a plain-language AI explanation.

Usage
-----
    python generate_report_phase2.py "path/to/file.xlsx" --out reports
    python generate_report_phase2.py "path/to/file.csv" --sheet "Sheet1"

Environment
-----------
    GEMINI_API_KEY   Your Gemini API key. If unset (or the AI call fails
                      for any reason), the Inspect button still works --
                      it just shows a rule-based explanation built from
                      Phase 1's own findings instead of an AI one. This
                      script never errors out because of the AI layer.
    GEMINI_MODEL      Optional. Defaults to "gemini-2.0-flash".

Output
------
    Exactly what `generate_report.py` already produces (PDF + HTML),
    PLUS one more file: "<stem>_data_quality_report_ai.html", which is
    the same report with Inspect buttons added.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_quality_engine.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    load_with_confirmed_header,
)
from data_quality_engine.engine.column_classifier import classify_columns
from data_quality_engine.engine.checks.missing_values import check_missing_values
from data_quality_engine.engine.checks.duplicates import check_duplicates
from data_quality_engine.engine.checks.type_mismatch import check_type_consistency_frame
from data_quality_engine.engine.checks.outliers import detect_outliers_frame
from data_quality_engine.engine.checks.schema_quality import check_schema_quality
from data_quality_engine.engine.checks.consistency import check_consistency_frame
from data_quality_engine.engine.checks.validity import check_validity_frame
from data_quality_engine.engine.checks.freshness import check_freshness_frame
from data_quality_engine.engine.pii.detect_pii import detect_pii_in_series
from data_quality_engine.engine.scoring import compute_data_quality_score
from data_quality_engine.engine.reporting.report_generator import build_report_data
from data_quality_engine.engine.reporting.pdf_report import generate_pdf_report
from data_quality_engine.engine.reporting.html_report import generate_html_report

from data_quality_engine.phase2.enhanced_report import generate_ai_enhanced_html_report


def build_report_data_for_file(filepath: str, sheet_name: str | None):
    """Identical orchestration to generate_report.py's
    run_and_generate_reports(), stopping just short of writing files, so
    Phase 2 can reuse the same report_data dict for the AI layer without
    ever touching generate_report.py or the engine/ package."""
    t0 = time.time()

    sheets = read_excel_file(filepath)
    sname = sheet_name or list(sheets.keys())[0]
    raw_df = sheets[sname]
    header_row = detect_header_row(raw_df)
    df = load_with_confirmed_header(raw_df, header_row)

    roles = classify_columns(df)

    missing = check_missing_values(df)
    dup = check_duplicates(df)
    type_results = check_type_consistency_frame(df)
    outlier_results = detect_outliers_frame(df)
    schema_results = check_schema_quality(df)
    consistency_results = check_consistency_frame(df, roles=roles)
    validity_results = check_validity_frame(df, roles=roles)
    freshness_results = check_freshness_frame(df, roles=roles)

    pii_summary_by_column = {str(c): detect_pii_in_series(df[c]) for c in df.columns}

    check_results_by_name = {
        "missing_values": missing,
        "duplicates": dup,
        "type_mismatch": type_results,
        "outliers": outlier_results,
        "schema_quality": schema_results,
        "consistency": consistency_results,
        "validity": validity_results,
        "freshness": freshness_results,
    }

    dimension_results = {
        "completeness": missing,
        "type_reliability": type_results,
        "uniqueness": dup,
        "outlier_risk": outlier_results,
        "schema_quality": schema_results,
        "consistency": consistency_results,
        "validity": validity_results,
        "freshness": freshness_results,
    }
    score = compute_data_quality_score(dimension_results, pii_summary_by_column=pii_summary_by_column)

    elapsed = time.time() - t0

    report_data = build_report_data(
        filepath=filepath,
        sheet_name=sname,
        df_shape=df.shape,
        header_row=header_row,
        processing_time_seconds=elapsed,
        classification=roles,
        check_results_by_name=check_results_by_name,
        pii_summary_by_column=pii_summary_by_column,
        fuzzy_results=None,
        score=score,
    )
    return report_data, df.shape


def run_and_generate_reports_phase2(
    filepath: str,
    sheet_name: str | None,
    out_dir: str,
    *,
    api_key: str | None = None,
):
    report_data, df_shape = build_report_data_for_file(filepath, sheet_name)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filepath).stem.replace(" ", "_")
    pdf_path = out_dir / f"{stem}_data_quality_report.pdf"
    html_path = out_dir / f"{stem}_data_quality_report.html"
    ai_html_path = out_dir / f"{stem}_data_quality_report_ai.html"

    # Standard Phase 1 outputs -- unchanged, always produced regardless of
    # whether the AI layer below succeeds or fails.
    generate_pdf_report(report_data, str(pdf_path))
    generate_html_report(report_data, str(html_path))

    # Phase 2: same report, plus Inspect buttons. Never throws -- worst
    # case this file ends up identical in content to html_path, with
    # rule-based explanations behind every Inspect button instead of AI.
    generate_ai_enhanced_html_report(report_data, str(ai_html_path), api_key=api_key)

    print(f"Rows/Cols: {df_shape}")
    print(f"Data Quality Score: {report_data['score'].get('overall')}")
    print(f"PDF:               {pdf_path}")
    print(f"HTML (Phase 1):    {html_path}")
    print(f"HTML (Phase 2 AI): {ai_html_path}")
    return str(pdf_path), str(html_path), str(ai_html_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Phase 1 reports plus an AI-enhanced (Inspect button) report."
    )
    parser.add_argument("filepath", help="Path to the .xlsx/.xls/.csv file")
    parser.add_argument("--sheet", default=None, help="Sheet name (omit to use the first sheet)")
    parser.add_argument("--out", default="reports", help="Output directory (default: reports)")
    parser.add_argument(
        "--gemini-api-key",
        default=None,
        help="Gemini API key. If omitted, falls back to the GEMINI_API_KEY env var, "
        "and if that's also unset the Inspect button uses rule-based explanations.",
    )
    args = parser.parse_args()

    run_and_generate_reports_phase2(args.filepath, args.sheet, args.out, api_key=args.gemini_api_key)
