# Data Quality Engine — Phase 1 Technical Plan

**Project:** Data Quality Engine
**Author:** Ayesha Amer
**Phase:** Phase 1 — Core Engine (no AI, rule-based, must be reliable and correct on its own)
**Status:** Ready for implementation
**Audience:** This document is written to be read by both a human developer and an AI coding assistant (e.g. Claude Code). Every module below includes exact inputs, outputs, function signatures, and behavior rules so that code generated from this plan is consistent, testable, and matches the chosen architecture — no guessing required.

---

## 0. One-Paragraph Summary (read this first)

The Data Quality Engine takes a messy client Excel file, detects its real header row (with user confirmation), lets the user confirm how much data will be processed, runs eight independent data-quality checks (missing values, type mismatches, duplicates, outliers, PII, format consistency, encoding, cross-column logic), turns every check into a 0–100 score across seven standard data-quality dimensions, and produces a human-readable Data Quality Report. No AI is used anywhere in Phase 1 — every decision is deterministic, logged, and explainable. The system is built as small, independent, testable Python modules, not one big script, so Phase 2 (the AI explanation layer) can be added later without touching this core.

---

## 1. Core Design Principles (do not violate these when generating code)

1. **Never silently guess.** At two points — header detection and processing scope — the system must pause and ask the user to confirm before continuing. This is implemented as a callback/prompt interface (see Section 4.1), not a hardcoded `input()` call, so it works in a CLI, a notebook, or a future API/UI.
2. **Every check is an independent function.** Each check takes a `pandas.DataFrame` (or a single column) and returns a structured result object. No check depends on another check having run first, except where explicitly noted (e.g. header detection must run before everything).
3. **No check may crash the pipeline.** Every check is wrapped so that if it throws an exception, the pipeline logs the failure and continues with the remaining checks, marking that check as `"status": "failed"` in the output rather than stopping.
4. **PII is masked, not just flagged**, before it can appear in any report, log, or print statement.
5. **Everything is logged** in structured JSON, one log file per pipeline run.
6. **No AI/LLM calls anywhere in Phase 1.** If a function signature has a parameter like `use_ai`, it must default to `False` and Phase 1 code must never set it to `True`.

---

## 2. Chosen Tools (Phase 1 — final, based on completed testing + 2026 re-validation)

Only tools that were actually tested and chosen are listed as **primary**. Where a better production-grade alternative exists, it is adopted below with rationale. Every check stays behind a thin interface so the library can be swapped without changing `pipeline.py`.

| Task | Primary tool (chosen) | Runner-up / fallback | Notes for implementation |
|---|---|---|---|
|1 Header row detection | Custom heuristic (pandas + rules) | openpyxl manual scan | 100% accuracy in testing; no extra dependency |
|2 Core profiling (missing/dtype/duplicates) | `pandas` built-in (`.isna()`, `.dtypes`, `.duplicated()`) for **exact** row-level issues | `whylogs` (optional fast profile summary) | Exact issue indices are required for the report; whylogs is great for streaming summaries but approximate — keep it optional, not the source of truth |
|3 Outlier detection | IQR method (custom, via `numpy`/`pandas`) — **default** | `PyOD` KNN (`method="knn"`, optional) | IQR had 4/4 catch rate, 0 false positives; explainable and maintainable. PyOD KNN is implemented modularly for comparison/future phases but is **not** Phase 1 default. |
|4 PII detection & masking | **Microsoft Presidio** (`presidio-analyzer` + `presidio-anonymizer`) + custom regex recognizers (CNIC / local phone) | Raw `spaCy` NER + hand-rolled masker | Presidio wraps spaCy, has a proper anonymizer (avoids the overlap-garble bug), checksum validators (e.g. Luhn for cards), and easy custom recognizers. Still fix overlap resolution explicitly in `mask_pii()` (see 4.5) |
|5 Fuzzy text matching / standardization | `RapidFuzz` | `TheFuzz` | Matched TheFuzz's 100% accuracy, faster (C++ backed) — still best choice |
|6 Date/format consistency | `dateutil.parser` | `pandas.to_datetime(errors='coerce')` | dateutil caught 7/8 mixed formats vs pandas' 1/8; fails loudly instead of silently returning `NaT` |
|7 Encoding detection & repair | `chardet` ≥7 (detect) + `ftfy` (repair) | `charset-normalizer` | chardet 7 rewrite: ~99% accuracy and much faster than older chardet; still pair with ftfy for mojibake repair. Flag confidence < 0.8 |

**Also required (reporting / plumbing):** `openpyxl`, `reportlab` or `fpdf2` (PDF), `Jinja2` (report templates), `pytest` (tests).

**Implementation rule for AI code generation:** Build each check behind a small internal interface (e.g. `detect_outliers(series) -> CheckResult`) so the underlying tool can be swapped later without changing the calling code in the pipeline.

---

## 3. Project Structure

```
data_quality_engine/                 # installable Python package
├── __init__.py
├── main.py                          # CLI entrypoint — runs the full pipeline
├── config/
│   ├── __init__.py
│   └── settings.py                  # thresholds, file size limits, PII masking rules
├── engine/
│   ├── __init__.py
│   ├── models.py                    # shared CheckResult dataclass (Section 4.3)
│   ├── logging_utils.py             # get_logger → logs/run_{id}.jsonl
│   ├── ingestion.py                 # Excel reading + header detection
│   ├── checkpoint.py                # human-in-the-loop confirmation logic
│   ├── checks/
│   │   ├── __init__.py
│   │   ├── missing_values.py
│   │   ├── type_mismatch.py
│   │   ├── duplicates.py
│   │   ├── outliers.py
│   │   ├── referential_integrity.py
│   │   ├── format_consistency.py
│   │   ├── encoding.py
│   │   └── cross_column_logic.py
│   ├── pii/
│   │   ├── __init__.py
│   │   ├── detect_pii.py
│   │   └── mask_pii.py
│   ├── standardization/
│   │   ├── __init__.py
│   │   └── fuzzy_match.py
│   ├── scoring/
│   │   ├── __init__.py
│   │   └── metrics.py               # converts check results into 0-100 scores
│   ├── reporting/
│   │   ├── __init__.py
│   │   └── report_generator.py      # builds the final Data Quality Report
│   └── pipeline.py                  # orchestrates all steps in order
├── logs/                            # one JSONL log file per run (gitignored contents)
├── reports/                         # generated PDF/XLSX reports (gitignored contents)
├── sample_data/                     # real/messy Excel samples for manual runs
├── tests/
│   ├── test_ingestion.py
│   ├── test_checkpoint.py
│   ├── test_checks/
│   │   └── (one test file per check, same names as above)
│   ├── test_pii.py
│   ├── test_scoring.py
│   ├── test_fuzzy_match.py
│   └── fixtures/                    # sample messy Excel files for testing
├── notebooks/                       # exploratory tool comparisons
├── requirements.txt
├── plan.md
└── README.md
```

**Rule for AI code generation:** Generate one file at a time, matching this structure exactly. Every file under `checks/` must expose exactly one public function following the shared contract in Section 4.3. Shared types live in `engine/models.py` — do not redefine `CheckResult` in each check file.

---

## 4. Module-by-Module Specification

### 4.1 Human-in-the-Loop Interface (`engine/checkpoint.py`)

Both checkpoints share one interface so the confirmation mechanism isn't duplicated:

```python
from typing import Callable, Any

class UserPrompt:
    """
    Abstraction over how confirmation is asked.
    In Phase 1 CLI mode, this prints to console and reads input().
    Later (API/UI), this can be swapped for a different implementation
    without changing pipeline.py.
    """
    def confirm(self, message: str, details: dict) -> bool:
        """Show `message` and `details` to the user; return True if confirmed."""
        raise NotImplementedError

class CLIPrompt(UserPrompt):
    def confirm(self, message: str, details: dict) -> bool:
        print(message)
        for k, v in details.items():
            print(f"  {k}: {v}")
        response = input("Proceed? (y/n): ").strip().lower()
        return response == "y"
```

**Checkpoint 1 — Header row confirmation**
- Input: detected header row index + a preview (first 5 rows above and below it).
- Message: "I believe row {N} is the header. Here's what I found:"
- If user says no: allow manual override (ask for the correct row index).

**Checkpoint 2 — Processing scope confirmation**
- Input: total row count, column count, estimated processing time.
- Message: "This file has {rows} rows and {cols} columns. Estimated processing time: {est}."
- If user wants to narrow scope: accept a row range or column subset.

---

### 4.2 Ingestion (`engine/ingestion.py`)

```python
def read_excel_file(filepath: str) -> dict[str, "pandas.DataFrame"]:
    """
    Reads all sheets from an Excel file using pandas + openpyxl.
    Returns {sheet_name: raw_dataframe} with NO header assumption applied yet
    (read with header=None so header detection can inspect raw rows).
    """

def detect_header_row(raw_df: "pandas.DataFrame", max_scan_rows: int = 10) -> int:
    """
    Custom heuristic (chosen tool):
    1. Scan the first `max_scan_rows` rows.
    2. For each row, compute:
       - non_null_string_density: fraction of non-null cells that are strings
       - type_consistency_below: how consistent the data types are in the
         column immediately below this row (next 5 rows)
    3. Score = non_null_string_density + type_consistency_below
    4. Return the index of the row with the highest score.
    Must return an int row index (0-based), never guess silently past this
    function — the caller is responsible for asking the user to confirm.
    """

def load_with_confirmed_header(raw_df, header_row: int) -> "pandas.DataFrame":
    """Re-loads/reindexes the dataframe using the confirmed header row."""
```

---

### 4.3 Shared Check Result Contract (all files under `engine/checks/`)

Every check function must return this exact structure so `scoring/metrics.py` can consume any check uniformly:

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class CheckResult:
    check_name: str                 # e.g. "missing_values"
    status: str                     # "passed" | "failed" | "error"
    column: str | None              # None if it's a file-level check
    issues_found: int
    details: dict[str, Any]         # check-specific details (e.g. per-row indices)
    dimension: str                  # which of the 7 quality dimensions this maps to (Section 5)
```

Each check file exposes one function, e.g.:

```python
# engine/checks/missing_values.py
def check_missing_values(df: "pandas.DataFrame") -> list[CheckResult]:
    """
    For each column: count nulls, compute % missing.
    dimension = "completeness"
    Returns one CheckResult per column.
    """
```

```python
# engine/checks/outliers.py
def detect_outliers(series: "pandas.Series", method: str = "iqr") -> CheckResult:
    """
    Chosen method: IQR.
    Q1 = series.quantile(0.25); Q3 = series.quantile(0.75); IQR = Q3 - Q1
    Lower bound = Q1 - 1.5*IQR ; Upper bound = Q3 + 1.5*IQR
    Any value outside bounds -> flagged as outlier.
    dimension = "validity"
    """
```

```python
# engine/checks/format_consistency.py
def check_date_formats(series: "pandas.Series") -> CheckResult:
    """
    Chosen tool: dateutil.parser (loud failure over silent NaT).
    Attempt to parse every value with dateutil.parser.parse().
    On failure, catch the exception and record the row + raw value
    in details["unparseable"] — never let it crash the pipeline.
    dimension = "validity"
    """
```

```python
# engine/checks/encoding.py
def check_encoding(raw_bytes: bytes) -> CheckResult:
    """
    chardet.detect(raw_bytes) -> {"encoding": ..., "confidence": ...}
    If confidence < 0.8, flag as "low confidence" in details.
    dimension = "consistency"
    """

def repair_encoding(text: str) -> str:
    """Use ftfy.fix_text(text) to repair mojibake. Returns repaired string."""
```

The remaining checks (`duplicates.py`, `type_mismatch.py`, `referential_integrity.py`, `cross_column_logic.py`) follow the identical `CheckResult` contract:
- `duplicates.py` → `check_duplicates(df)` → dimension `"uniqueness"`
- `type_mismatch.py` → `check_type_consistency(series)` → dimension `"validity"`
- `referential_integrity.py` → `check_referential_integrity(df, key_column, reference_values)` → dimension `"integrity"`
- `cross_column_logic.py` → `check_cross_column_rules(df, rules: list[dict])` → dimension `"integrity"` (rules are config-driven, e.g. `{"if": "start_date", "then": "end_date >= start_date"}`)

---

### 4.4 Fuzzy Matching / Standardization (`engine/standardization/fuzzy_match.py`)

```python
def standardize_values(series: "pandas.Series", threshold: int = 90) -> dict[str, str]:
    """
    Chosen tool: RapidFuzz.
    1. Get unique values in the series.
    2. Cluster values whose rapidfuzz.fuzz.ratio() >= threshold.
    3. Within each cluster, pick the most frequent value as canonical.
    4. Return a mapping {original_value: canonical_value}.
    Caller applies this mapping via series.map(mapping).
    dimension = "consistency"
    """
```

---

### 4.5 PII Detection & Masking (`engine/pii/`)

```python
# detect_pii.py
def detect_pii(text: str) -> list[dict]:
    """
    Chosen tool: Microsoft Presidio Analyzer + custom PatternRecognizer
    for CNIC / local phone formats. spaCy remains the NLP backend under Presidio.
    Returns a list of {"type": "PHONE"|"CNIC"|"CARD"|"NAME"|"EMAIL"|..., "start": int,
    "end": int, "value": str, "score": float}, sorted by `start`.
    KNOWN ISSUE TO FIX DURING IMPLEMENTATION: when two matches overlap
    (e.g. a name adjacent to a phone number), resolve by keeping the
    longer/more specific match and discarding the overlapping shorter one
    BEFORE masking — do not mask both independently, since that is what
    causes garbled output in the current tested version.
    """
```

```python
# mask_pii.py
def mask_pii(text: str, detected: list[dict], mode: str = "partial") -> str:
    """
    mode="partial": show only last 4 characters (e.g. phone/card numbers)
    mode="full": replace entire value with a fixed token (e.g. "[NAME]")
    Must process `detected` matches in reverse order of `start` index so
    that string-index replacement doesn't shift positions of earlier matches.
    Names -> full redaction. Phone/CNIC/Card -> partial masking by default.
    """
```

**Rule:** `mask_pii()` must always run before any value reaches `report_generator.py` or any log line. There must be no code path where raw PII is written to `logs/` or the final report.

---

### 4.6 Scoring (`engine/scoring/metrics.py`)

Seven standard dimensions (Section 5 has full definitions): **Completeness, Validity, Uniqueness, Consistency, Accuracy, Integrity, Sensitivity.**

```python
def score_dimension(results: list[CheckResult], dimension: str, total_rows: int) -> float:
    """
    For a given dimension, aggregate all CheckResults tagged with it:
      score = 100 * (1 - (total issues_found / total_rows))
    Clamp to [0, 100]. If total_rows == 0, return None (undefined).
    """

def score_column(column_results: list[CheckResult], total_rows: int) -> dict[str, float]:
    """Returns {dimension: score} for one column."""

def score_file(all_results: list[CheckResult], total_rows: int) -> dict:
    """
    Returns:
    {
        "overall_score": float,          # average of all 7 dimension scores
        "dimension_scores": {dim: float},
        "column_scores": {col_name: {dim: float}},
        "worst_columns": [top 3 columns by lowest overall score]
    }
    """
```

---

### 4.7 Reporting (`engine/reporting/report_generator.py`)

```python
def generate_report(file_score: dict, all_results: list[CheckResult], output_format: str = "pdf") -> str:
    """
    Builds a structured report with:
      1. Summary section: overall_score, one-line verdict, file metadata.
      2. Per-dimension breakdown (bar/table of all 7 scores).
      3. Per-column breakdown (worst columns first).
      4. Detailed issue list per check (grouped, not one row per error).
      5. PII summary (counts only, e.g. "3 phone numbers masked in column X" —
         never the actual masked or unmasked values).
    output_format: "pdf" | "xlsx" (structured so it can become a dashboard later)
    Returns the filepath of the generated report.
    No AI-generated text anywhere in Phase 1 — all language here is
    template-based (f-strings / Jinja2), not LLM output.
    """
```

---

### 4.8 Logging (used throughout)

```python
import logging, json

def get_logger(run_id: str) -> logging.Logger:
    """
    One JSON log file per run: logs/run_{run_id}.jsonl
    Each log line: {"timestamp":, "run_id":, "step":, "level":, "message":, "details": {}}
    Every check wraps its execution in try/except and logs failures at
    level "ERROR" with the exception message, then continues the pipeline.
    """
```

---

### 4.9 Pipeline Orchestration (`engine/pipeline.py`)

This is the single place that calls everything, in this exact order:

```python
def run_pipeline(filepath: str, prompt: UserPrompt) -> str:
    """
    1. sheets = read_excel_file(filepath)
    2. for each sheet:
       a. header_row = detect_header_row(raw_df)
       b. confirmed = prompt.confirm("Header row detected", {...})
          if not confirmed: header_row = ask for manual override
       c. df = load_with_confirmed_header(raw_df, header_row)
       d. scope_ok = prompt.confirm("Processing scope", {rows, cols, est_time})
          if not scope_ok: narrow df per user input
       e. results = []
          for each check function in engine/checks/*:
              try: results.extend(check_fn(df or column))
              except Exception as e: log error, append CheckResult(status="error")
       f. pii_results = detect_pii on all text columns; mask before storing anywhere
       g. standardized = standardize_values on relevant text columns
       h. file_score = score_file(results, total_rows)
       i. report_path = generate_report(file_score, results)
    3. Return report_path.
    Every step logs to get_logger(run_id).
    """
```

---

## 5. Data Quality Metrics — Definitions (used by `scoring/metrics.py`)

| Dimension | Definition | Fed by which checks |
|---|---|---|
| **Completeness** | How much data is missing, per column and per row | `missing_values.py` |
| **Validity** | Does data match expected type/format (dates as dates, numbers as numbers, values within a reasonable range) | `type_mismatch.py`, `outliers.py`, `format_consistency.py` |
| **Uniqueness** | How many duplicate rows or duplicate keys exist | `duplicates.py` |
| **Consistency** | Are the same real-world values written the same way (e.g. Lahore vs LHR); are character encodings clean | `fuzzy_match.py`, `encoding.py` |
| **Accuracy** | Where checkable, does data match a known-correct reference (e.g. an approved city list) | `referential_integrity.py` (reference-list mode) |
| **Integrity** | Do references between fields/rows hold up (foreign-key-style checks, cross-column logic) | `referential_integrity.py`, `cross_column_logic.py` |
| **Sensitivity** | Does the file contain PII that should be flagged/masked | `detect_pii.py` |

Each dimension → 0–100 score per column → averaged into one overall score per file. The report shows both the big picture (overall score) and the detail (per-dimension, per-column).

---

## 6. Step-by-Step Flow (matches `pipeline.py` exactly)

1. Read the Excel file (all sheets, all formats it contains).
2. Detect the header row → **confirm with user** (Checkpoint 1).
3. Show processing scope → **confirm with user** (Checkpoint 2).
4. Run all 8 data quality checks (each independently, each fault-tolerant).
5. Detect PII → mask it immediately, before anything is stored or logged.
6. Run fuzzy matching / text standardization on relevant columns.
7. Convert all check results into the 7 quality-dimension scores.
8. Produce the Data Quality Report (PDF/Excel).

---

## 7. Production Considerations (carried into Phase 1 code, not deferred)

- **Reliability:** every check wrapped in try/except; failures logged and skipped, never crash the run.
- **Security/Privacy:** PII masked before it leaves the local processing step; GDPR data-minimization principle applied even to internal logs.
- **Scalability:** checks run column-by-column so larger files don't require rewriting logic; processing-scope checkpoint prevents wasted compute on oversized runs.
- **Maintainability:** modules are separated (ingestion / checks / PII / scoring / reporting) so any one part can change independently; every non-obvious tool choice is documented inline as a comment referencing Section 2 of this plan.

---

## 8. Testing Plan (`tests/`)

- One test file per check module, using small hand-crafted DataFrames with known, injected issues (mirrors how the original tool research in Section 2 was validated).
- `tests/fixtures/` holds 3–5 sample messy Excel files: one clean, one with a title row before the header, one with mixed date formats, one with planted fake PII (fake names/numbers only, never real data).
- Minimum coverage target for Phase 1: every public function listed in Section 4 has at least one passing test and one test that feeds it bad/missing input to confirm it fails gracefully (returns `status="error"`, never raises uncaught).

---

## 9. Explicit Non-Goals for Phase 1 (do not implement these yet)

- No AI/LLM calls of any kind (explanations, detection, or otherwise).
- No business rules engine / per-client config beyond simple reference lists for `referential_integrity.py`.
- No ML-readiness / Prophet-forecasting-specific scoring.
- No entity-normalization NER layer beyond the RapidFuzz standardization already specified.
- No dashboard — reports are PDF/Excel files only, structured so a dashboard can be added later without redesigning the scoring layer.

---

## 10. Suggested Build Order (for incremental, testable progress)

1. `ingestion.py` (`read_excel_file`, `detect_header_row`, `load_with_confirmed_header`)
2. `checkpoint.py` (`UserPrompt`, `CLIPrompt`)
3. `checks/missing_values.py`, `checks/duplicates.py`, `checks/type_mismatch.py` (fastest wins, build momentum)
4. `checks/outliers.py` (IQR)
5. `checks/format_consistency.py` (dateutil)
6. `checks/encoding.py` (chardet + ftfy)
7. `standardization/fuzzy_match.py` (RapidFuzz)
8. `pii/detect_pii.py` + `pii/mask_pii.py` (fix the known overlap bug here)
9. `checks/referential_integrity.py`, `checks/cross_column_logic.py`
10. `scoring/metrics.py`
11. `reporting/report_generator.py`
12. `pipeline.py` — wire everything together
13. `main.py` — CLI entrypoint
14. Full end-to-end test on a real messy client-style Excel file

---

*This plan reflects Phase 1 only. Phase 2 (AI explanation layer, business rules engine, ML/Prophet readiness scoring, NER-based entity normalization) is intentionally out of scope here and will get its own plan once Phase 1 is complete and tested.*
