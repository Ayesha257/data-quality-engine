# Data Quality Engine

Rule-based Phase 1 engine that turns messy client Excel/CSV files into explainable data-quality reports. Every decision is deterministic and logged — no AI black boxes.

**Author:** Ayesha Amer  
**Status:** Tasks 1–6 + plan.md Task 5 (RapidFuzz standardization) implemented

---

## What it does

| Step | What runs |
|------|-----------|
| **Task 1** | Header-row detection + human confirmation |
| **Task 2** | Missing values, duplicates, type mismatches |
| **Task 3** | Outlier detection (IQR default; optional KNN) with column-role awareness |
| **Task 4** | PII detection + masking (privacy risk reported separately) |
| **Task 5 (plan.md)** | Fuzzy text standardization via RapidFuzz (`standardize_values`) |
| **Rubric dims** | Schema quality, case/whitespace consistency, validity, freshness |
| **Scoring** | Weighted 8-dimension Data Quality Score (+ separate privacy risk) |

Column classification runs after header confirmation so measurement checks (outliers, freshness, etc.) never treat invoice numbers or phone fields as statistics. Fuzzy standardization runs after PII and only on configured text roles (`categorical`, `free_text` by default).

---

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Use the project venv as the Jupyter kernel when running notebooks.

---

## Run the pipeline

```bash
# Full pipeline on one file
python main.py "path/to/your_file.xlsx"

# One sheet only
python main.py "path/to/your_file.xlsx" --sheet "Sheet Name"

# Task 3 deep-dive (IQR + optional KNN)
python run_task3_detailed.py "path/to/your_file.xlsx" --sheet "Sheet Name"

# Batch run over local dataset folder (edit paths in the script first)
.\run_all_task1_task2.ps1
```

Point commands at your own `.xlsx` / `.xls` / `.csv`. A small fixture lives at `src/sample_data/sample_data.xlsx` for quick demos.

Formats: `.xlsx`/`.xlsm` (openpyxl, calamine fallback), `.xls` (xlrd), `.csv`.

---

## Fuzzy standardization (plan.md Task 5)

```python
from data_quality_engine.engine.standardization import (
    standardize_values,
    apply_standardization,
)

mapping = standardize_values(df["status"], threshold=90)  # {original: canonical}
cleaned = apply_standardization(df["status"], mapping=mapping)
```

Config knobs in `data_quality_engine/config/settings.py`:

- `fuzzy_threshold` (default 90)
- `fuzzy_case_insensitive` (default True)
- `fuzzy_max_unique` (default 500)
- `fuzzy_eligible_roles` (default `categorical`, `free_text`)

---

## Project layout

```
data_quality_engine/
  config/          # thresholds, rubric weights, domain rules
  engine/
    ingestion.py   # read + header detection
    checkpoint.py  # human-in-the-loop confirms
    column_classifier.py
    checks/        # missing, duplicates, types, outliers, schema, …
    pii/           # detect + mask
    standardization/  # RapidFuzz fuzzy_match (plan Task 5)
    scoring.py     # 8-dimension composite score
main.py            # CLI entrypoint
notebooks/         # task walkthroughs (import the package where possible)
tests/             # pytest suite
plan.md            # Phase 1 technical plan
```

---

## Notebooks

| Notebook | Focus |
|----------|--------|
| `notebooks/01_task1_header_detection.ipynb` | Header heuristics (exploratory + package) |
| `notebooks/02_task2_core_profiling.ipynb` | Missing / types / duplicates |
| `notebooks/03_task3_outlier_detection.ipynb` | IQR + optional KNN |
| `notebooks/04_task4_pii_detection_masking.ipynb` | PII detect/mask (imports package) |
| `notebooks/05_task5_fuzzy_standardization.ipynb` | RapidFuzz standardization (plan Task 5) |
| `notebooks/schema_consistency_validity_freshness_scoring.ipynb` | Rubric dims + composite score |

See `notebooks/README.md`. Source of truth for demos: package code under `data_quality_engine/`.

---

## Tests

```bash
python -m pytest tests -q
```

---

## Design notes (for review)

- Checks return a shared `CheckResult` and fail soft (`status="error"`) instead of crashing the run.
- PII samples printed by the CLI are already masked; privacy risk is **not** subtracted from the quality score.
- Role-skipped columns (e.g. outliers on identifiers) are excluded from dimension pass-ratios so scores are not artificially inflated.
- Fuzzy standardization feeds the consistency dimension alongside case/whitespace consistency; the CLI reports mappings without rewriting the working frame until you call `apply_standardization`.
- Phase 1 intentionally omits PDF reporting and encoding repair wiring — those remain in `plan.md` for later build-order items.
