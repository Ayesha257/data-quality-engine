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

from backend.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    load_with_confirmed_header,
)
from backend.engine.column_classifier import classify_columns
from backend.engine.checks.missing_values import check_missing_values
from backend.engine.checks.duplicates import check_duplicates
from backend.engine.checks.type_mismatch import check_type_consistency_frame
from backend.engine.checks.outliers import detect_outliers_frame
from backend.engine.checks.schema_quality import check_schema_quality
from backend.engine.checks.consistency import check_consistency_frame
from backend.engine.checks.validity import check_validity_frame
from backend.engine.checks.freshness import check_freshness_frame
from backend.engine.pii.detect_pii import detect_pii_in_series
from backend.engine.scoring import compute_data_quality_score
from backend.engine.reports.report_generator import build_report_data
from backend.engine.reports.pdf_report import generate_pdf_report
from backend.engine.reports.html_report import generate_html_report

from backend.engine.ai_explanation.enhanced_report import generate_ai_enhanced_html_report
from backend.database import history


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
    client_id: str = "default_client",
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

    # Phase 2 (M2 addition): compare this run's score against the most
    # recent prior run for this exact client_id + filename, then save
    # this run. Uses the file's own name as the identity key, so nothing
    # here is dataset-specific -- any client, any file, works the same
    # way. Never blocks report generation: if the DB is unreachable,
    # get_score_trend/save_run fail soft and trend stays None.
    trend = history.record_run_and_get_trend(
        client_id=client_id, file_name=Path(filepath).name, report_data=report_data
    )
    if trend is not None:
        print(f"Score trend: {trend.to_display_text()}")

    # Phase 2: same report, plus Inspect buttons (now covering every
    # Phase 1 check AND the PII/sensitive-data section) and a score-trend
    # banner. Never throws -- worst case this file ends up identical in
    # content to html_path, with rule-based explanations behind every
    # Inspect button instead of AI.
    generate_ai_enhanced_html_report(report_data, str(ai_html_path), api_key=api_key, trend=trend)

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
        "--client-id",
        default="default_client",
        help="Identifies which client this report belongs to, for score-trend "
        "history (default: 'default_client'). Use a real client id once you're "
        "processing files for more than one client so their trends don't mix.",
    )
    parser.add_argument(
        "--gemini-api-key",
        default=None,
        help="Gemini API key. If omitted, falls back to the GEMINI_API_KEY env var, "
        "and if that's also unset the Inspect button uses rule-based explanations.",
    )
    args = parser.parse_args()

    run_and_generate_reports_phase2(
        args.filepath, args.sheet, args.out, api_key=args.gemini_api_key, client_id=args.client_id
    )
