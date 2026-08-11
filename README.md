# Data Quality Engine

Rule-based Phase 1 engine that turns messy client Excel/CSV files into explainable data-quality reports — extended in Phase 2 with an AI explanation layer that narrates those findings in plain language. All decisions (what's wrong, severity, what to do) are made deterministically by Phase 1; AI only explains, never decides.

**Author:** Ayesha Amer

**Status:** Phase 1 complete · Phase 2 M1 (foundations) + M2 (AI explanation layer) complete

---

## What it does

### Phase 1 — deterministic checks

| Step | What runs |
|------|-----------|
| **Task 1** | Header-row detection + human confirmation |
| **Encoding Check** | CSV raw-byte encoding via chardet; skipped for Excel |
| **Task 2** | Missing values, duplicates, type mismatches |
| **Task 3** | Outlier detection (IQR / optional KNN), column-role aware |
| **Task 4** | PII detection + masking (privacy risk reported separately) |
| **Task 5** | Fuzzy text standardization via RapidFuzz |
| **Dimensions** | Schema quality, consistency, validity, freshness |
| **Scoring** | Weighted 8-dimension Data Quality Score |

### Phase 2 — intelligence layer (built on top, never modifies Phase 1)

| Milestone | What it adds |
|---|---|
| **M1 — Foundations** | Database + session management, structured JSONL run logging, per-client business rule resolution, Pydantic schemas |
| **M2 — AI Explanation Layer** | Gemini-powered "Inspect" button on every check card, explaining Phase 1's findings in plain language |
| **M2 — Resilience** | Rate limiting + retry/backoff around Gemini calls; automatic fallback to rule-based text if AI is unreachable — report is never blocked |
| **M2 — PII Inspect Coverage** | PII section gets an AI explanation too, including the zero-findings case |
| **M2 — History & Trend** | Score is recorded per client/file; next run shows an improved/declined/unchanged trend |

**Design principle** (see `PHASE2_PLAN.md`): *"AI explains findings; Phase 1 makes decisions."* AI never decides severity or touches data — it only restates numbers Phase 1 already computed. If the AI call fails, the same deterministic explanation renders instead.

---

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### Phase 2 `.env` (never commit this file)

```dotenv
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=20
```

No key configured? Reports still generate — Inspect buttons just show the rule-based explanation.

---

## Run the pipeline

### Phase 1 only

```bash
python main.py "path/to/your_file.xlsx"
python main.py "path/to/your_file.xlsx" --sheet "Sheet Name"
```

### Phase 2 — AI-enhanced report

```bash
python generate_report_phase2.py "path/to/your_file.xlsx"
python generate_report_phase2.py "path/to/your_file.xlsx" --sheet "Sheet Name"
python generate_report_phase2.py "path/to/your_file.csv" --out my_reports
python generate_report_phase2.py "path/to/your_file.xlsx" --gemini-api-key "your-key-here"
```

Produces the Phase 1 PDF/HTML plus a Phase 2 `..._ai.html` report with an Inspect button on every check.

> Open generated reports via a local server, not by double-clicking (browsers block scripts on `file://` pages):
> ```bash
> cd reports && python -m http.server 8000
> # open http://localhost:8000/your_report.html
> ```

Supported formats: `.xlsx`/`.xlsm`, `.xls`, `.csv`.

---

## Project layout

```
data_quality_engine/
  config/                # Phase 1 thresholds, rubric weights, domain rules
  engine/                 # ingestion, checks, PII, standardization, scoring
  phase2/
    ai_explainer.py       # Gemini calls, prompts, retry, fallback text
    enhanced_report.py    # injects Inspect buttons into the HTML report
    database/             # SQLAlchemy models + sessions
    schemas/               # Pydantic models
    rules.py              # per-client rule resolution
    logging_setup.py      # structured JSONL logs
config/clients/           # per-client rule overrides
main.py                   # Phase 1 CLI entrypoint
generate_report_phase2.py # Phase 2 CLI entrypoint
notebooks/                 # task walkthroughs
tests/                     # pytest suite
plan.md / PHASE2_PLAN.md   # technical plans
```

---

## Tests

```bash
python -m pytest tests -q
pytest tests/test_main_pipeline.py -v          # Phase 1
pytest tests/test_phase2_m1_setup.py -v         # Phase 2 foundations
pytest tests/test_phase2_m2_additions.py -v     # Phase 2 AI layer
```

---

## Design notes (for review)

**Phase 1**
- Checks fail soft (`status="error"`) instead of crashing the run.
- Role-skipped columns (e.g. outliers on identifiers) are excluded from pass-ratios so scores aren't artificially inflated.
- A sheet with no detectable header is skipped with a message rather than aborting the whole file.

**Phase 2**
- `phase2/` only ever calls **into** `engine/`, never the other way — Phase 1 works standalone even without Phase 2 installed.
- `ai_explainer.py` never raises: any failure (missing key, network, rate limit, bad response) resolves to the same rule-based fallback built from Phase 1's own data.
- AI responses follow a fixed structure (`WHAT'S WRONG` / `WHY IT MATTERS` / `WHAT TO DO`); anything that doesn't match renders as plain text instead of breaking.
- Every AI call is logged with its outcome, so AI availability is auditable after the fact.
- `enhanced_report.py` never modifies Phase 1's report generator — it injects into the output, so worst case is still a complete, valid Phase 1 report.
