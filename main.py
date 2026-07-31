from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from data_quality_engine.engine.checkpoint import (
    CLIPrompt,
    UserPrompt,
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
from data_quality_engine.engine.checks.outliers import detect_outliers_frame
from data_quality_engine.engine.column_classifier import classify_columns
from data_quality_engine.engine.pii.detect_pii import detect_pii_in_series
from data_quality_engine.engine.logging_utils import get_logger, log_event
import logging

def _print_classification_results(df):
    """
    Column classification, run right after Task 1 (header detection) and
    before any check that depends on knowing a column's role -- most
    importantly outliers, which must not run mean/std/IQR on identifier or
    PII columns.

    This is a *visible* pipeline step (previously classify_columns() was
    only called implicitly, inside detect_outliers_frame()). Task 3 still
    calls classify_columns() itself internally too -- that keeps
    detect_outliers_frame() correct and testable in isolation even if
    someone calls it directly outside this pipeline. The small re-computation
    cost here buys an explicit, inspectable step in the printed report.
    """
    print("\n=== Column Classification (runs after Task 1, before Task 2-4) ===")
    roles = classify_columns(df)

    print("-" * 70)
    print(f"{'Column':40} {'Role':>20}")
    print("-" * 70)
    role_counts: dict[str, int] = {}
    for col, role in roles.items():
        role_counts[role] = role_counts.get(role, 0) + 1
        print(f"{str(col):40} {role:>20}")
    print("-" * 70)
    print("Role counts:", ", ".join(f"{r}={c}" for r, c in sorted(role_counts.items())))
    print(
        "Why this matters: measurement columns get real IQR/KNN outlier stats; "
        "identifier/pii/date/categorical/free_text columns are skipped in Task 3 "
        "(see 'skipped_non_measurement_column' below) since mean/std on an invoice "
        "number or phone number is meaningless."
    )
    return roles


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

    return {
        "missing_columns_scanned": len(missing),
        "missing_columns_with_issues": sum(1 for r in missing if r.issues_found > 0),
        "duplicate_rows": dup.issues_found,
        "type_mismatch_columns_scanned": len(type_results),
        "type_mismatch_columns_with_issues": bad_cols,
    }


def _print_task3_results(df):
    """Task 3: outlier detection, printed in the same report style as Task 2."""
    print("\n=== Task 3 Results (Outlier Detection) ===")
    results = detect_outliers_frame(df)

    print("\n[4] Outliers  (Validity)")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Method':>10} {'Status':>10}")
    print("-" * 70)
    flagged_cols = 0
    skipped_cols = 0
    for r in results:
        if r.issues_found > 0:
            flagged_cols += 1
        if r.details.get("reason") == "skipped_non_measurement_column":
            skipped_cols += 1
        method = r.details.get("method", "-")
        print(f"{str(r.column):40} {r.issues_found:10d} {str(method):>10} {r.status:>10}")
        if r.details.get("reason"):
            print(f"  reason: {r.details['reason']} (role={r.details.get('classified_role', '-')})")
        sample = r.details.get("sample_values", [])
        if sample:
            print(f"  sample outlier values: {sample[:5]}")
    print("-" * 70)
    print(f"Columns with outliers   : {flagged_cols} / {len(results)}")
    print(f"Columns skipped (non-measurement): {skipped_cols} / {len(results)}")

    return {
        "outlier_columns_scanned": len(results),
        "outlier_columns_with_issues": flagged_cols,
        "outlier_columns_skipped": skipped_cols,
    }


def _print_task4_results(df):
    """
    Task 4: PII detection + masking, printed as a summary only.
    Never prints raw PII values -- only counts/types and the already-masked
    sample values produced by detect_pii_in_series()/mask_pii().
    """
    print("\n=== Task 4 Results (PII Detection & Masking) ===")

    print("\n[5] PII  (Sensitivity)")
    print("-" * 70)
    print(f"{'Column':40} {'Rows w/ PII':>12} {'Types found':>20}")
    print("-" * 70)
    total_rows_with_pii = 0
    flagged_cols = 0
    for col in df.columns:
        summary = detect_pii_in_series(df[col])
        rows_with_pii = summary.get("rows_with_pii", 0)
        if rows_with_pii > 0:
            flagged_cols += 1
            total_rows_with_pii += rows_with_pii
            type_counts = summary.get("type_counts", {})
            types_str = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))
            print(f"{str(col):40} {rows_with_pii:12d} {types_str:>20}")
            masked_rows = summary.get("masked_rows", {})
            preview = list(masked_rows.values())[:2]
            if preview:
                print(f"  masked sample: {preview}")
    print("-" * 70)
    print(f"Columns with PII: {flagged_cols} / {df.shape[1]}")
    print(f"Total rows with PII across flagged columns: {total_rows_with_pii}")

    return {
        "pii_columns_scanned": df.shape[1],
        "pii_columns_with_hits": flagged_cols,
        "pii_rows_with_hits": total_rows_with_pii,
    }


def run_task1_task2(filepath: str, sheet_name: str | None = None, prompt: UserPrompt | None = None):
    prompt = prompt or CLIPrompt()
    run_id = uuid.uuid4().hex[:12]
    logger = get_logger(run_id)

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
        classification = _print_classification_results(df)
        task2_summary = _print_top_results(df)
        task3_summary = _print_task3_results(df)
        task4_summary = _print_task4_results(df)

        checks_run = [
            "column_classification",
            "missing_values",
            "duplicates",
            "type_mismatch",
            "outliers",
            "pii",
        ]
        pass_count = (
            (task2_summary["missing_columns_scanned"] - task2_summary["missing_columns_with_issues"])
            + (1 if task2_summary["duplicate_rows"] == 0 else 0)
            + (task2_summary["type_mismatch_columns_scanned"] - task2_summary["type_mismatch_columns_with_issues"])
            + (task3_summary["outlier_columns_scanned"] - task3_summary["outlier_columns_with_issues"])
            + (task4_summary["pii_columns_scanned"] - task4_summary["pii_columns_with_hits"])
        )
        fail_count = (
            task2_summary["missing_columns_with_issues"]
            + (0 if task2_summary["duplicate_rows"] == 0 else 1)
            + task2_summary["type_mismatch_columns_with_issues"]
            + task3_summary["outlier_columns_with_issues"]
            + task4_summary["pii_columns_with_hits"]
        )
        log_event(
            logger,
            logging.INFO,
            "sheet_processed",
            run_id=run_id,
            step="pipeline",
            details={
                "file": str(Path(filepath).name),
                "sheet": sname,
                "checks_run": checks_run,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "classification_roles": {
                    role: sum(1 for r in classification.values() if r == role)
                    for role in sorted(set(classification.values()))
                },
            },
        )

    print("\nDone: Task 1-4 completed.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Phase 1 Task 1-4 (ingestion, core profiling, outliers, PII).")
    p.add_argument("filepath", help="Path to .xlsx/.xls/.csv file")
    p.add_argument("--sheet", default=None, help="Optional sheet name")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    path = Path(args.filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    run_task1_task2(str(path), args.sheet)