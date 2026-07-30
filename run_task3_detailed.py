from pathlib import Path
from data_quality_engine.engine.ingestion import read_excel_file, detect_header_row, load_with_confirmed_header
from data_quality_engine.engine.checks.outliers import detect_outliers_frame
path = Path(r"OneDrive_1_26-01-2026 - latest data set\Booked Orders.xlsx")
sheet = "Booked Orders (New - YTD)"
sheets = read_excel_file(path)
raw = sheets[sheet]
header = detect_header_row(raw)
df = load_with_confirmed_header(raw, header)
print(f"FILE: {path.name}")
print(f"SHEET: {sheet}")
print(f"HEADER ROW: {header}")
print(f"SHAPE: {df.shape}")
print("\n=== IQR (default) ===")
iqr_results = detect_outliers_frame(df, method="iqr")
for r in iqr_results:
    d = r.details
    print(f"\nColumn: {r.column}")
    print(f"Status: {r.status}")
    print(f"Method: {d.get('method')}")
    print(f"Q1={d.get('q1')} Q3={d.get('q3')} IQR={d.get('iqr')}")
    print(f"Lower={d.get('lower_bound')} Upper={d.get('upper_bound')}")
    print(f"Outliers={d.get('outlier_count', r.issues_found)}  Pct={d.get('outlier_pct')}")
    print(f"Sample indices: {d.get('row_indices', [])[:10]}")
print("\n=== KNN (optional) ===")
knn_results = detect_outliers_frame(df, method="knn")
for r in knn_results:
    d = r.details
    print(f"{r.column}: status={r.status}, method={d.get('method')}, outliers={d.get('outlier_count', r.issues_found)}")
