# Data Quality Engine

A deterministic data-quality pipeline for messy client Excel/CSV files, wrapped in an AI explanation layer, regulatory compliance scanners (HIPAA/PHI, PCI-DSS, GLBA, SOX), ML-readiness scoring, entity resolution, and a full REST API + web console.

Every finding — what's wrong, how severe it is, what to do about it — is computed by a rule-based deterministic engine. Nothing downstream (AI, API, frontend) is ever allowed to change a decision; it can only explain, serve, or display one.

---

## Architecture

```
Phase 1 — Deterministic Engine (backend/engine/)
Header detection → profiling → outliers → PII → standardization → 8-dimension scoring
Makes every decision. Runs standalone.
        │  (one-way: Phase 2 reads Phase 1, never the reverse)
        ▼
Phase 2 — Intelligence Layer
AI explanations (Gemini, w/ deterministic fallback) · Compliance scans (HIPAA, PCI-DSS, GLBA, SOX)
ML-readiness scoring · Entity resolution · Per-client rule versioning · Run logging
        ▼
FastAPI service (backend/app.py) — auth, uploads, run status, reports, rules
        ▼
React console (frontend/) — upload, run history, score dials, rule builder,
compliance & entity-resolution views
```

## Features

### Deterministic Checks
- **Ingestion & profiling** — header detection, column classification, missing values, duplicates, types, outliers, schema quality, consistency, validity, freshness.
- **PII & Compliance** — Presidio + regex detection, masking, privacy risk score. Multi-framework compliance support:
  - **HIPAA PHI**: 18 Safe Harbor identifiers with exposure scoring and Safe Harbor posture reporting.
  - **PCI-DSS**: Primary Account Numbers (PAN) with Luhn checksum validation, card expiry, and CVV column governance.
  - **GLBA**: ABA routing numbers with mod-10 weighted checksum validation and customer NPI keyword detection.
  - **SOX**: Audit trail header completeness (creation, approval, modification) and transaction timestamp verification.
- **Standardization & Resolution** — RapidFuzz fuzzy text matching; 3-tier entity resolution (lookup → RapidFuzz → semantic embeddings) for master data normalization.
- **Reporting** — 8-dimension Data Quality Score, executive summaries, per-check breakdowns, interactive Gemini AI explanations, and standalone compliance reports.

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy, Pydantic, pandas/numpy, Presidio + spaCy, RapidFuzz, sentence-transformers, PyOD, Jinja2 + fpdf2, JWT (PyJWT + passlib)
- **Frontend:** React 18, React Router, Vite, Tailwind CSS, Axios, Vitest + Testing Library
- **AI:** Google Gemini (`gemini-2.5-flash`) for explanation generation only — never for detection or scoring.

## Getting Started

### Backend
```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### Running Backend API & Frontend Console
```bash
# Terminal 1: Backend API
uvicorn backend.app:app --reload

# Terminal 2: Frontend Console
cd frontend
npm install
npm run dev
```

### Running CLI Scan & Report
```bash
python main.py "path/to/file.xlsx" --report
```

## Testing

```bash
# Run backend pytest suite
pytest tests/ -v
pytest backend/tests/ -v

# Run frontend tests
cd frontend && npm test
```
