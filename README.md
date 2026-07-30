# Data-quality-engine

Building a rule-based Data Quality Engine that turns messy client Excel/CSV files into clean, explainable data quality reports — automated header detection, missing value/duplicate/outlier checks, and dimension-based scoring, with zero AI black-boxing so every result is traceable. Currently building out Phase 1 (core checks) with PII masking and full reporting coming next.


## Setup
 
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```
 
## Tasks
 
**Task 1 — Header Detection**
- `main.py` / `engine/ingestion.py` — detects header row, asks for confirmation
- `notebooks/01_task1_header_detection.ipynb` — exploratory version
  
**Task 2 — Core Profiling**
- `main.py` / `engine/checks/` — missing values, duplicates, type mismatch
- `notebooks/02_task2_core_profiling.ipynb` — exploratory version
  
**Task 3 — Outlier Detection**
- `run_task3_detailed.py` — IQR (default) + optional KNN
- `notebooks/03_task3_outlier_detection.ipynb` — exploratory version

## Formats supported
 
`.xlsx`/`.xlsm` (openpyxl, calamine fallback), `.xls` (xlrd), `.csv`
 
## Run
 
```bash
# Task 1 + 2 on one file
python main.py "path/to/your_file.xlsx"
 
# Task 1 + 2 on a specific sheet
python main.py "path/to/your_file.xlsx" --sheet "Sheet Name"
 
# Task 1 + 2 batch run (edit file paths inside the script first)
.\run_all_task1_task2.ps1
 
# Task 3 demo (edit file path inside the script first)
python run_task3_detailed.py
```
 
> Note: this repo doesn't include the dataset — point the commands above at your own `.xlsx`/`.xls`/`.csv` file. `run_all_task1_task2.ps1` and `run_task3_detailed.py` have hardcoded file paths near the top that you'll need to update to match your own files.
 
## Tests
 
```bash
python -m pytest tests -q
```
