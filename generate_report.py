"""Runs the full Phase 1 pipeline against a real file and generates both a
PDF and an HTML Data Quality Report. Reuses every existing check module --
this file only orchestrates and times the calls, it does not reimplement
any check logic.

FIXES applied per "Data Quality Engine Functional Validation & Report Audit"
(ISS-01, ISS-02, ISS-03, ISS-04):

  ISS-01/ISS-02 -- sheet selection used to be ``list(sheets.keys())[0]``,
  i.e. whichever sheet happened to be physically first in the workbook,
  with no regard for hidden state or whether it held real data. That
  produced a report built entirely from a hidden Sage X3 config sheet for
  one dataset, and silently dropped 5 of 6 year-sheets for another. This
  file now (a) skips hidden/very-hidden sheets and empty sheets when no
  --sheet is given, (b) processes *every* remaining visible/non-empty
  sheet (one report each) instead of truncating to the first, and (c)
  always tells the report which sheets exist vs. were analyzed/skipped,
  via ``sheet_disclosure`` in report_data.

  ISS-03 -- duplicate detection now runs check_duplicates_frame (full-row
  + inferred business-key duplicates) instead of bare check_duplicates
  (full-row only), matching what main.py's CLI path already does.

  ISS-04 -- fixed in report_generator.py (total_rows_with_pii is now a
  union of row indices, not a sum across columns).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from data_quality_engine.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    load_with_confirmed_header,
    get_sheet_visibility,
)
from data_quality_engine.engine.column_classifier import classify_columns
from data_quality_engine.engine.checks.missing_values import check_missing_values
from data_quality_engine.engine.checks.duplicates import check_duplicates_frame
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


def _is_nontrivial_sheet(raw_df: pd.DataFrame) -> bool:
    """True when a sheet has at least some real content worth analyzing."""
    if raw_df is None or raw_df.empty:
        return False
    non_empty = raw_df.dropna(how="all")
    return len(non_empty) > 0 and raw_df.shape[1] > 0


def _select_sheets_to_process(
    sheets: dict[str, pd.DataFrame],
    visibility: dict[str, bool],
    explicit_sheet: str | None,
) -> tuple[list[str], list[str], list[str]]:
    """
    Decide which sheet(s) to actually analyze.

    Returns (sheets_to_process, hidden_sheet_names, skipped_empty_sheet_names).

    - If the caller explicitly named a sheet (``--sheet``), that single
      sheet is used regardless of visibility/content -- an explicit
      request always wins.
    - Otherwise every visible, non-trivial sheet is processed (previously:
      only ``list(sheets.keys())[0]`` was ever processed).
    """
    if explicit_sheet:
        if explicit_sheet not in sheets:
            raise KeyError(f"Sheet '{explicit_sheet}' not found. Available: {list(sheets.keys())}")
        return [explicit_sheet], [], []

    hidden = [name for name in sheets if visibility.get(name, True) is False]
    candidates = [name for name in sheets if name not in hidden]

    empty = [name for name in candidates if not _is_nontrivial_sheet(sheets[name])]
    to_process = [name for name in candidates if name not in empty]

    # Degenerate case: every visible sheet is empty (or the workbook has
    # no visible sheets at all) -- fall back to the first sheet overall
    # rather than producing nothing, but this is still an explicit,
    # disclosed fallback rather than a silent guess.
    if not to_process:
        to_process = [next(iter(sheets.keys()))]

    return to_process, hidden, empty


def _process_single_sheet(
    filepath: str,
    sname: str,
    raw_df: pd.DataFrame,
    out_dir: Path,
    *,
    all_sheet_names: list[str],
    hidden_sheet_names: list[str],
    skipped_empty_sheet_names: list[str],
    sheets_processed: list[str],
    multi_output: bool,
) -> tuple[str, str, dict]:
    t0 = time.time()

    header_row = detect_header_row(raw_df)
    df = load_with_confirmed_header(raw_df, header_row)

    roles = classify_columns(df)

    missing = check_missing_values(df)
    # ISS-03 fix: full-row + inferred business-key duplicate detection,
    # not full-row only.
    dup = check_duplicates_frame(df, key_columns=None)
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

    other_sheets = [n for n in all_sheet_names if n != sname]
    sheet_disclosure = {
        "total_sheets_in_workbook": len(all_sheet_names),
        "all_sheet_names": all_sheet_names,
        "analyzed_sheet": sname,
        "other_sheets_in_workbook": other_sheets,
        "other_sheets_also_reported": [n for n in sheets_processed if n != sname],
        "hidden_sheet_names": hidden_sheet_names,
        "skipped_empty_sheet_names": skipped_empty_sheet_names,
    }

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
        sheet_disclosure=sheet_disclosure,
    )

    stem = Path(filepath).stem.replace(" ", "_")
    if multi_output:
        safe_sheet = "".join(c if c.isalnum() else "_" for c in sname)
        pdf_path = out_dir / f"{stem}_{safe_sheet}_data_quality_report.pdf"
        html_path = out_dir / f"{stem}_{safe_sheet}_data_quality_report.html"
    else:
        pdf_path = out_dir / f"{stem}_data_quality_report.pdf"
        html_path = out_dir / f"{stem}_data_quality_report.html"

    generate_pdf_report(report_data, str(pdf_path))
    generate_html_report(report_data, str(html_path))

    print(f"Sheet: {sname}  |  Rows/Cols: {df.shape}  |  Data Quality Score: {score.get('data_quality_score')}")
    print(f"  PDF:  {pdf_path}")
    print(f"  HTML: {html_path}")

    return str(pdf_path), str(html_path), report_data


def run_and_generate_reports(filepath: str, sheet_name: str | None, out_dir: str):
    """
    Generates a report for the requested sheet, or -- when no sheet is
    given -- for every visible, non-empty sheet in the workbook (see
    ``_select_sheets_to_process``). Returns the (pdf_path, html_path) of
    the *first* report generated, for backward compatibility with callers
    that only expect one file; ``run_and_generate_reports_all`` returns
    the full list.
    """
    results = run_and_generate_reports_all(filepath, sheet_name, out_dir)
    first = results[0]
    return first["pdf_path"], first["html_path"]


def run_and_generate_reports_all(filepath: str, sheet_name: str | None, out_dir: str) -> list[dict]:
    sheets = read_excel_file(filepath)
    visibility = get_sheet_visibility(filepath)  # {} if unknown -> treat all as visible

    to_process, hidden, skipped_empty = _select_sheets_to_process(sheets, visibility, sheet_name)

    if len(to_process) > 1:
        print(
            f"Workbook has {len(sheets)} sheet(s); analyzing {len(to_process)} visible/non-empty "
            f"sheet(s): {to_process}"
        )
    if hidden:
        print(f"Skipped hidden sheet(s): {hidden}")
    if skipped_empty:
        print(f"Skipped empty sheet(s): {skipped_empty}")

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    multi_output = len(to_process) > 1
    outputs = []
    for sname in to_process:
        pdf_path, html_path, report_data = _process_single_sheet(
            filepath,
            sname,
            sheets[sname],
            out_dir_path,
            all_sheet_names=list(sheets.keys()),
            hidden_sheet_names=hidden,
            skipped_empty_sheet_names=skipped_empty,
            sheets_processed=to_process,
            multi_output=multi_output,
        )
        outputs.append({"sheet": sname, "pdf_path": pdf_path, "html_path": html_path, "report_data": report_data})
    return outputs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a Data Quality Report (PDF + HTML) for a file.")
    parser.add_argument("filepath", help="Path to the .xlsx/.xls/.csv file")
    parser.add_argument(
        "--sheet",
        default=None,
        help="Sheet name (omit to auto-process every visible, non-empty sheet in the workbook)",
    )
    parser.add_argument("--out", default="reports", help="Output directory (default: reports)")
    args = parser.parse_args()

    run_and_generate_reports_all(args.filepath, args.sheet, args.out)
