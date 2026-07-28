# data-quality-engine

Phase 1 -- rule-based data quality checks for messy Excel/CSV files.

Tuned for the Easby teacher dataset in:
`OneDrive_1_26-01-2026 - latest data set/`

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

## Run on the teacher dataset

```bash
# List known files
python -m data_quality_engine.main --list-dataset

# Non-interactive run (recommended first pass)
python -m data_quality_engine.main --dataset classic --yes --format xlsx
python -m data_quality_engine.main --dataset customers --yes
python -m data_quality_engine.main --dataset booked --yes --sheet "Booked Orders (New - YTD)"

# Or pass a full path
python -m data_quality_engine.main "OneDrive_1_26-01-2026 - latest data set\Stock Report.xls" --yes
```

Dataset keys: `booked`, `classic`, `customers`, `suppliers`, `stock`, `products`, `goods`, `invoices`, `openpo`

Reports -> `reports/`  
Logs -> `logs/`

## What this engine checks on these files

- Header detection (title rows, multi-row headers like Booked Orders)
- Missing values + suspicious zeros (e.g. GBP Amt-tax / costs that are 0)
- Duplicates, type mismatches, IQR outliers
- Date/format consistency + cross-column date order rules
- Encoding detect/repair
- Fuzzy text standardization
- PII mask (UK phones, emails, names) -- counts only in reports
- Referential integrity against Customer / Supplier / Product masters when present
- 7-dimension scoring + PDF/XLSX report

## Formats supported

`.xlsx` (openpyxl, calamine fallback), `.xls` (xlrd), `.csv`

## Tests

```bash
python -m pytest tests -q
```

See `plan.md` for architecture.
