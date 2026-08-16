"""Runs the full Phase 1 pipeline against a real file and generates both a
PDF and an HTML Data Quality Report. Reuses every existing check module --
this file only orchestrates and times the calls, it does not reimplement
any check logic.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

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


def run_and_generate_reports(filepath: str, sheet_name: str | None, out_dir: str):
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

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filepath).stem.replace(" ", "_")
    pdf_path = out_dir / f"{stem}_data_quality_report.pdf"
    html_path = out_dir / f"{stem}_data_quality_report.html"

    generate_pdf_report(report_data, str(pdf_path))
    generate_html_report(report_data, str(html_path))

    print(f"Rows/Cols: {df.shape}")
    print(f"Data Quality Score: {score.get('data_quality_score')}")
    print(f"PDF:  {pdf_path}")
    print(f"HTML: {html_path}")
    return str(pdf_path), str(html_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a Data Quality Report (PDF + HTML) for a file.")
    parser.add_argument("filepath", help="Path to the .xlsx/.xls/.csv file")
    parser.add_argument("--sheet", default=None, help="Sheet name (omit to use the first sheet)")
    parser.add_argument("--out", default="reports", help="Output directory (default: reports)")
    args = parser.parse_args()

    run_and_generate_reports(args.filepath, args.sheet, args.out)