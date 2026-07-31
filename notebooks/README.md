# Notebooks

Task walkthroughs with working cells and short notes. Prefer the **package** (`data_quality_engine/`) as the source of truth — early Task 1–3 notebooks keep small educational re-implementations so the heuristics stay readable, then show the same behaviour via package imports.

| File | Content |
|------|---------|
| `01_task1_header_detection.ipynb` | Header detection heuristics + sample cases |
| `02_task2_core_profiling.ipynb` | Missing / types / duplicates (+ optional whylogs note) |
| `03_task3_outlier_detection.ipynb` | IQR default + optional KNN + column roles |
| `04_task4_pii_detection_masking.ipynb` | Package-backed PII detection + masking |
| `05_task5_fuzzy_standardization.ipynb` | RapidFuzz standardization (plan.md Task 5 / Section 4.4) |
| `schema_consistency_validity_freshness_scoring.ipynb` | Schema / consistency / validity / freshness + composite score |

**How to run:** open any notebook → select the project `venv` kernel → Run All.

For a live CLI demo of the same logic: `python main.py path/to/file.xlsx`.
