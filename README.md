# Data Quality Engine

<<<<<<< Updated upstream
Rule-based Phase 1 engine that turns messy client Excel/CSV files into explainable data-quality reports. Every decision is deterministic and logged — no AI black boxes.

**Author:** Ayesha Amer  
**Status:** Tasks 1–5 implemented
=======
Rule-based data quality pipeline for Excel/CSV files — profiling, scoring, PII/HIPAA checks, and client-ready HTML/PDF reports. Phase 1 makes all decisions; Phase 2 adds AI explanations, API, entity resolution, and compliance scanning.

**Author:** Ayesha Amer

**Status:** Phase 1 complete · Phase 2: M1–M2, M4 (API), M6 (entity resolution), M9 (HIPAA PHI) implemented
>>>>>>> Stashed changes

---

## What it does

<<<<<<< Updated upstream
| Step | What runs |
|------|-----------|
| **Task 1** | Header-row detection + human confirmation |
| **Encoding Check** | CSV raw-byte encoding via chardet (+ ftfy repair helpers); skipped for Excel |
| **Task 2** | Missing values, duplicates (full-row + business keys), type mismatches |
| **Task 3** | Outlier detection (IQR default; optional KNN) with column-role awareness |
| **Task 4** | PII detection + masking (privacy risk reported separately) |
| **Task 5** | Fuzzy text standardization via RapidFuzz (`standardize_values`) |
| **Dimensions** | Schema quality, case/whitespace consistency, validity, freshness |
| **Scoring** | Weighted 8-dimension Data Quality Score (+ separate privacy risk) |

Column classification runs after header confirmation so measurement checks (outliers, freshness, etc.) never treat invoice numbers or phone fields as statistics. Fuzzy standardization runs after PII and only on configured text roles (`categorical`, `free_text` by default).
=======
**Ingestion & profiling** — header detection, column roles, missing values, duplicates, types, outliers, schema/consistency/validity/freshness.

**PII & compliance** — Presidio + regex detection, masking, privacy risk score. HIPAA PHI scan maps findings to HHS Safe Harbor identifiers with a separate exposure score and compliance-adjusted headline score.

**Standardization & resolution** — RapidFuzz fuzzy matching; 3-tier entity resolution (lookup → RapidFuzz → semantic) for values like cities/countries.

**Reporting** — weighted 9-dimension Data Quality Score, executive summary, per-check breakdowns, optional Gemini “Inspect” explanations, score trend per client.

**Optional** — referential integrity (master files), ML readiness (`--target-column` + `--date-column`), FastAPI backend + React frontend.
>>>>>>> Stashed changes

---

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
python -m spacy download en_core_web_lg   # optional; for Presidio / entity resolution NER
```

<<<<<<< Updated upstream
Use the project venv as the Jupyter kernel when running notebooks.
=======
Optional `.env` (do not commit):

```dotenv
GEMINI_API_KEY=your-key-here
DQE_API_KEYS=dqe_yourkey:your_client_id
DQE_CORS_ORIGINS=http://localhost:5173
```
>>>>>>> Stashed changes

---

## Run

<<<<<<< Updated upstream
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

## Fuzzy standardization 

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
=======
**CLI — full pipeline + report**

```bash
python main.py "path/to/file.xlsx" --report
python main.py "path/to/file.xlsx" --sheet "Sheet1" --report --report-dir reports
```

**Phase 2 report script (same pipeline, explicit output dir)**

```bash
python generate_report_phase2.py "path/to/file.xlsx" --out reports --client-id my_client
```

**API + frontend**

```bash
uvicorn data_quality_engine.phase2.api.app:app --reload --host 127.0.0.1 --port 8000

cd frontend
npm install
npm run dev
```

Open reports via a local server (not `file://`):

```bash
cd reports && python -m http.server 8080
```

Supported formats: `.xlsx`, `.xlsm`, `.xls`, `.csv`.
>>>>>>> Stashed changes

---

## Project layout

```
data_quality_engine/
<<<<<<< Updated upstream
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
=======
  engine/           # ingestion, checks, PII, scoring, reporting
  config/           # settings, business rules, client rule YAML
  phase2/
    compliance/     # M9 HIPAA PHI scan
    entity_resolution/  # M6 cascade resolver
    api/              # M4 FastAPI
    ai_explainer.py   # M2 Inspect explanations
    readiness/        # M3 ML readiness (opt-in)
main.py             # CLI entrypoint
frontend/           # React console
tests/
>>>>>>> Stashed changes
```

Plans: `plan.md`, `PHASE2_PLAN.md`, `PHASE2_HIPAA_PHI_PLAN.md`

---

## Notebooks

| Notebook | Focus |
|----------|--------|
| `notebooks/01_task1_header_detection.ipynb` | Header heuristics (exploratory + package) |
| `notebooks/02_task2_core_profiling.ipynb` | Missing / types / duplicates |
| `notebooks/03_task3_outlier_detection.ipynb` | IQR + optional KNN |
| `notebooks/04_task4_pii_detection_masking.ipynb` | PII detect/mask (imports package) |
| `notebooks/05_task5_fuzzy_standardization.ipynb` | RapidFuzz standardization (Task 5) |
| `notebooks/schema_consistency_validity_freshness_scoring.ipynb` | Dimensions + composite score |

See `notebooks/README.md`. Source of truth for demos: package code under `data_quality_engine/`.

---

## Tests

```bash
python -m pytest tests -q
```

Key suites: `test_main_pipeline.py`, `test_hipaa_compliance.py`, `test_phase2_m6_entity_resolution.py`, `test_scoring.py`, `test_report_display.py`

---

## Design notes

<<<<<<< Updated upstream
- Checks return a shared `CheckResult` and fail soft (`status="error"`) instead of crashing the run.
- PII samples printed by the CLI are already masked; privacy risk is **not** subtracted from the quality score.
- Role-skipped columns (e.g. outliers on identifiers) are excluded from dimension pass-ratios so scores are not artificially inflated.
- Fuzzy standardization feeds the consistency dimension alongside case/whitespace consistency; the CLI reports mappings without rewriting the working frame until you call `apply_standardization`.
- CSV **Encoding Check** uses chardet (with optional low-confidence fallbacks) and is skipped for Excel; `ingestion._sniff_csv_encoding` delegates to `check_encoding`.
- Phase 1 intentionally omits PDF reporting wiring — that remains in `plan.md` for later build-order items.
=======
- Phase 1 checks are deterministic; AI only explains findings, never changes scores.
- HIPAA exposure applies a composite ceiling; reports show both headline and compliance-adjusted scores when relevant.
- Entity resolution is non-destructive — it suggests canonical values; original data is not overwritten.
- Checks fail soft per column/sheet so one bad sheet does not abort the whole workbook.
>>>>>>> Stashed changes
