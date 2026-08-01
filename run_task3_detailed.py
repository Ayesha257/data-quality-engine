"""Task 3 deep-dive: IQR (default) vs optional PyOD KNN on one sheet."""

from __future__ import annotations

import argparse
from pathlib import Path

from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.ingestion import (
    detect_header_row,
    load_with_confirmed_header,
    read_excel_file,
)
from data_quality_engine.engine.checks.outliers import detect_outliers_frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Task 3 outlier detection (IQR + optional KNN).")
    parser.add_argument(
        "filepath",
        nargs="?",
        default=None,
        help="Path to .xlsx/.xls/.csv (defaults to sample_data if present)",
    )
    parser.add_argument("--sheet", default=None, help="Optional sheet name")
    parser.add_argument("--skip-knn", action="store_true", help="Skip the slower KNN comparison pass")
    args = parser.parse_args()

    if args.filepath:
        path = Path(args.filepath)
    else:
        candidates = [
            SETTINGS["dataset_dir"] / "Booked Orders copy.csv",
            Path("src/sample_data/sample_data.xlsx"),
            Path("sample_data/sample_data.xlsx"),
        ]
        path = next((p for p in candidates if p.exists()), candidates[-1])

    if not path.exists():
        raise FileNotFoundError(
            f"File not found: {path}\n"
            "Pass an explicit path: python run_task3_detailed.py path/to/file.xlsx"
        )

    sheets = read_excel_file(path)
    sheet = args.sheet or next(iter(sheets))
    if sheet not in sheets:
        raise KeyError(f"Sheet '{sheet}' not found. Available: {list(sheets.keys())}")

    raw = sheets[sheet]
    header = detect_header_row(raw)
    df = load_with_confirmed_header(raw, header)

    print(f"FILE: {path.name}")
    print(f"SHEET: {sheet}")
    print(f"HEADER ROW: {header}")
    print(f"SHAPE: {df.shape}")

    print("\n=== IQR (default) ===")
    for r in detect_outliers_frame(df, method="iqr"):
        d = r.details
        print(f"\nColumn: {r.column}")
        print(f"Status: {r.status}")
        print(f"Method: {d.get('method')}")
        if d.get("reason"):
            print(f"Reason: {d.get('reason')} (role={d.get('classified_role', '-')})")
            continue
        print(f"Q1={d.get('q1')} Q3={d.get('q3')} IQR={d.get('iqr')}")
        print(f"Lower={d.get('lower_bound')} Upper={d.get('upper_bound')}")
        print(f"Outliers={d.get('outlier_count', r.issues_found)}  Pct={d.get('outlier_pct')}")
        print(f"Sample indices: {d.get('row_indices', [])[:10]}")

    if args.skip_knn:
        return

    print("\n=== KNN (optional) ===")
    for r in detect_outliers_frame(df, method="knn"):
        d = r.details
        print(
            f"{r.column}: status={r.status}, method={d.get('method')}, "
            f"outliers={d.get('outlier_count', r.issues_found)}"
        )


if __name__ == "__main__":
    main()
