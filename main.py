from __future__ import annotations

import argparse
from pathlib import Path

from data_quality_engine.engine.checkpoint import (
    CLIPrompt,
    confirm_header_row,
    confirm_processing_scope,
    apply_scope,
)
from data_quality_engine.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    header_preview,
    load_with_confirmed_header,
)
from data_quality_engine.engine.checks.missing_values import check_missing_values
from data_quality_engine.engine.checks.duplicates import check_duplicates
from data_quality_engine.engine.checks.type_mismatch import check_type_consistency_frame

def _print_top_results(df):
    print("\n=== Task 2 Results (Detailed) ===")
    print(f"Rows: {len(df)} | Columns: {df.shape[1]}")

    # ---- Missing ----
    missing = check_missing_values(df)
    print("\n[1] Missing Values  (Completeness)")
    print("-" * 70)
    print(f"{'Column':40} {'Missing':>10} {'%':>10} {'Status':>10}")
    print("-" * 70)
    total_missing = 0
    for r in missing:
        total_missing += r.issues_found
        pct = r.details.get("missing_pct", 0)
        print(f"{str(r.column):40} {r.issues_found:10d} {pct:10.4f} {r.status:>10}")
    print("-" * 70)
    print(f"TOTAL missing cells: {total_missing}")
    print(f"Columns with missing: {sum(1 for r in missing if r.issues_found > 0)} / {len(missing)}")

    # ---- Duplicates ----
    dup = check_duplicates(df)[0]
    print("\n[2] Duplicates  (Uniqueness)")
    print("-" * 70)
    print(f"Status          : {dup.status}")
    print(f"Duplicate rows  : {dup.issues_found}")
    print(f"Total rows      : {dup.details.get('total_rows')}")
    sample_idx = dup.details.get("row_indices", [])
    if sample_idx:
        print(f"Sample row idx  : {sample_idx[:20]}")
    else:
        print("Sample row idx  : none (no full-row duplicates)")

    # ---- Type mismatch ----
    type_results = check_type_consistency_frame(df)
    print("\n[3] Type Mismatch  (Validity)")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Dominant':>12} {'Status':>10}")
    print("-" * 70)
    bad_cols = 0
    for r in type_results:
        if r.issues_found > 0:
            bad_cols += 1
        dominant = r.details.get("dominant_type", "-")
        print(f"{str(r.column):40} {r.issues_found:10d} {str(dominant):>12} {r.status:>10}")
        sample = r.details.get("sample_values", [])
        if sample:
            print(f"  sample bad values: {sample[:5]}")
    print("-" * 70)
    print(f"Columns with type issues: {bad_cols} / {len(type_results)}")

    print("\n=== Summary ===")
    print(f"Missing-values check: {len(missing)} columns scanned")
    print(f"Duplicates check    : {dup.status} ({dup.issues_found} duplicate rows)")
    print(f"Type-mismatch check : {len(type_results)} columns scanned, {bad_cols} with issues")


def run_task1_task2(filepath: str, sheet_name: str | None = None):
    prompt = CLIPrompt()

    sheets = read_excel_file(filepath)
    if sheet_name:
        if sheet_name not in sheets:
            raise KeyError(f"Sheet '{sheet_name}' not found. Available: {list(sheets.keys())}")
        items = [(sheet_name, sheets[sheet_name])]
    else:
        items = list(sheets.items())

    for sname, raw_df in items:
        print("\n" + "=" * 80)
        print(f"Sheet: {sname}")

        # Task 1: detect + confirm header
        detected = detect_header_row(raw_df)
        preview = header_preview(raw_df, detected)
        header_row = confirm_header_row(prompt, detected, preview)

        df = load_with_confirmed_header(raw_df, header_row)

        # Task 2 checkpoint: scope confirm
        est = max(0.1, (len(df) * max(1, df.shape[1])) / 50000.0)
        scope = confirm_processing_scope(prompt, len(df), df.shape[1], est)
        df = apply_scope(df, scope)

        print(f"\nFinal shape after header+scope: {df.shape}")
        _print_top_results(df)

    print("\nDone: Task 1 + Task 2 completed.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Phase 1 Task 1+2 (ingestion + core profiling).")
    p.add_argument("filepath", help="Path to .xlsx/.xls/.csv file")
    p.add_argument("--sheet", default=None, help="Optional sheet name")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    path = Path(args.filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    run_task1_task2(str(path), args.sheet)