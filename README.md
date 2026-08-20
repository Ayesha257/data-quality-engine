# Data Quality Engine

A deterministic data-quality pipeline for messy client Excel/CSV files, wrapped in an AI explanation layer, a HIPAA/PHI compliance scanner, ML-readiness scoring, entity resolution, and a full REST API + web console.

Every finding — what's wrong, how severe it is, what to do about it — is computed by a rule-based Phase 1 engine. Nothing downstream (AI, API, frontend) is ever allowed to change a decision; it can only explain, serve, or display one.

---

## Purpose

Client data almost never arrives clean. Before it can be trusted for reporting, forecasting, or handoff to another system, someone has to answer: *what's actually wrong with this file, how bad is it, and is it safe to use?*

This engine answers that deterministically, then layers on plain-language explanations, a HIPAA-identifier scan for healthcare data, a readiness check for time-series forecasting, cross-file entity resolution, and a real API + web console around it.

## Architecture

```
Phase 1 — Deterministic Engine (backend/engine/)
Header detection → profiling → outliers → PII → standardization → 8-dimension scoring
Makes every decision. Runs standalone.
        │  (one-way: Phase 2 reads Phase 1, never the reverse)
        ▼
Phase 2 — Intelligence Layer
AI explanations (Gemini, w/ deterministic fallback) · HIPAA/PHI compliance scan
ML-readiness scoring · Entity resolution · Per-client rule versioning · Run logging
        ▼
FastAPI service (backend/app.py) — auth, uploads, run status, reports, rules
        ▼
React console (frontend/) — upload, run history, score dials, rule builder,
compliance & entity-resolution views
```

## Features

### Deterministic checks

| Step | What runs |
|---|---|
| Task 1 | Header-row detection + human confirmation |
| Encoding check | CSV raw-byte encoding via `chardet`; skipped for Excel |
| Task 2 | Missing values, duplicates, type mismatches |
| Task 3 | Outlier detection (IQR / optional KNN), column-role aware |
| Task 4 | PII detection + masking (privacy risk reported separately) |
| Task 5 | Fuzzy text standardization via RapidFuzz |
| Dimensions | Schema quality, consistency, validity, freshness |
| Scoring | Weighted 8-dimension Data Quality Score |

### Intelligence layer

| Milestone | What it adds |
|---|---|
| **M1 — Foundations** | Database + sessions, structured JSONL run logging, per-client versioned rule resolution, Pydantic schemas |
| **M2 — AI Explanation Layer** | Gemini-powered "Inspect" button on every check card, plain-language narration of Phase 1's findings; rate-limited with retry/backoff and automatic rule-based fallback if AI is unreachable — reports are never blocked |
| **M3 — ML Readiness** | Prophet-specific precondition checks (temporal sufficiency, interval regularity, target integrity, leakage) combined into a weighted readiness score and `ready` / `caution` / `not_ready` verdict |
| **M4 — REST API + Console** | FastAPI service with JWT auth, file upload, async runs, report/rule endpoints, backed by SQLAlchemy — paired with a React + Vite + Tailwind web console |
| **M6 — Entity Resolution** | Column-level resolution cascade: exact lookup table → fuzzy matching (RapidFuzz) → semantic fallback (sentence-transformers) |
| **M9 — HIPAA/PHI Compliance** | Maps PII hits plus new HIPAA-specific recognizers onto the 18 official HHS Safe Harbor identifiers; reports a dataset-level posture with counts per identifier per column only — never raw values |

## Tech stack

**Backend:** Python, FastAPI, SQLAlchemy, Pydantic, pandas/numpy, Presidio + spaCy (PII/NER), RapidFuzz, sentence-transformers, PyOD, Jinja2 + fpdf2 (reports), JWT (PyJWT + passlib)

**Frontend:** React 18, React Router, Vite, Tailwind CSS, Axios, Vitest + Testing Library

**AI:** Google Gemini (`gemini-2.5-flash` by default) for explanation generation only — never for detection or scoring

## Project layout

```
backend/
  app.py                     # FastAPI application factory
  main.py                    # Phase 1 pipeline orchestration
  config/                    # thresholds, rubric weights, per-client rule overrides
  engine/
    checks/                  # missing values, duplicates, outliers, type mismatch, validity, ...
    pii/                     # detect_pii.py, mask_pii.py
    compliance/              # HIPAA identifier mapping, scanning, scoring, reporting
    readiness/               # temporal / interval / target / leakage → Prophet readiness score
    entity_resolution/       # lookup → fuzzy → semantic cascade
    standardization/         # fuzzy text standardization
    ai_explanation/          # Gemini calls, prompts, retry, fallback text, report injection
    reports/                 # HTML/PDF report generation
  database/                  # SQLAlchemy models + sessions
  schemas/                   # Pydantic request/response/config models
  services/                  # auth, profile, jobs, per-client rules
  routes/                    # auth, profile, and core API routes

frontend/
  src/
    pages/                   # Login, Register, Upload, Runs, RunDetail, Rules, Compliance, Profile
    components/               # FileDrop, ScoreDial, DimensionBars, RuleBuilder, EntityResolutionPanel, ...

config/clients/               # per-client rule overrides
notebooks/                    # Phase 1 development notebooks
tests/                        # pytest suite (backend)
main.py                       # Phase 1 CLI entrypoint
generate_report_phase2.py     # Phase 2 AI-enhanced report CLI
plan.md / PHASE2_PLAN.md / PHASE2_HIPAA_PHI_PLAN.md   # technical plans
```

## Getting started

### Backend

```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in what you need (database URL, `DQE_JWT_SECRET`, and optionally `GEMINI_API_KEY` — reports and Inspect buttons still work without a key, using rule-based fallback text).

### Frontend

```bash
cd frontend
npm install
```

`vite.config.js` proxies `/api/*` to `http://127.0.0.1:8000` by default, so no `.env` is needed for local dev.

Supported input formats: `.xlsx` / `.xlsm`, `.xls`, `.csv`.

## Running it

**CLI, Phase 1 only:**
```bash
python main.py "path/to/your_file.xlsx"
```

**CLI, Phase 2 AI-enhanced report:**
```bash
python generate_report_phase2.py "path/to/your_file.xlsx"
```
Produces the Phase 1 PDF/HTML plus a `..._ai.html` report with an Inspect button on every check.

> Open generated reports via a local server, not by double-clicking — browsers block scripts on `file://` pages:
> ```bash
> cd reports && python -m http.server 8000
> ```

**Full stack (API + web console):**
```bash
uvicorn backend.app:app --reload      # API docs at /docs
cd frontend && npm run dev             # console
```

## Testing

```bash
python -m pytest tests -q     # backend suite
cd frontend && npm test        # frontend component tests
```

## Future improvements

- **Recommendation engine** — turn findings into a prioritized remediation catalog with projected score improvements, instead of leaving prioritization to the reader.
- **Hardening & deployment** — Docker packaging, production security review, and a proper deployment checklist so this can run outside a dev machine.
- **Async job queue** — move run execution off a background thread and onto a real queue (e.g. RQ/Celery) for heavier concurrent load.
- **Broader file support** — JSON and database-source ingestion alongside Excel/CSV.
  
