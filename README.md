# Data Quality Engine

A deterministic data-quality pipeline for messy client Excel/CSV files, wrapped in an AI explanation layer, regulatory compliance scanners (HIPAA/PHI, PCI-DSS, GLBA, SOX, GDPR, CCPA/CPRA), ML-readiness scoring, entity resolution, and a full REST API + web console.

Every finding — what's wrong, how severe it is, what to do about it — is computed by a rule-based deterministic engine. Nothing downstream (AI, API, frontend) is ever allowed to change a decision; it can only explain, serve, or display one. Where a detector can only make a heuristic guess rather than a verified match, the finding is routed through a human-in-the-loop confirmation step before it can appear in any report.

---

## Architecture

```
Phase 1 — Deterministic Engine (backend/engine/)
Header detection → profiling → outliers → PII → standardization → 8-dimension scoring
Makes every decision. Runs standalone.
        │  (one-way: Phase 2 reads Phase 1, never the reverse)
        ▼
Phase 2 — Intelligence Layer
AI explanations (Gemini, w/ deterministic fallback) · Compliance scans (HIPAA, PCI-DSS, GLBA, SOX, GDPR, CCPA)
Human-in-the-loop confirmation for low-confidence findings · ML-readiness scoring · Entity resolution
Per-client rule versioning · Run logging
        ▼
FastAPI service (backend/app.py) — auth, uploads, run status, reports, rules, compliance confirmation
        ▼
React console (frontend/) — upload, run history, score dials, rule builder,
compliance console (per-regulation reports, Inspect explanations, confirm/reject panel),
entity-resolution views
```

## Features

### Deterministic Checks
- **Ingestion & profiling** — header detection, column classification, missing values, duplicates, types, outliers, schema quality, consistency, validity, freshness.
- **PII detection** — Presidio + regex detection, masking, privacy risk score.

### Compliance Scanning

Every regulation below runs opt-in only (off by default, selected per-scan in Advanced Options) and produces a standalone compliance report grouping findings by confidence tier — **High Confidence** (checksum- or regex-validated), **Medium Confidence** (pattern + contextual match), and **Confirmed** (a low-confidence guess a user has verified). Every report carries a disclaimer that it flags compliance-relevant patterns and does not itself certify legal compliance.

| Regulation | Scope | What's detected | Detection method |
|---|---|---|---|
| **HIPAA** | Protected Health Information | 18 Safe Harbor identifiers | Regex + keyword matching, with Safe Harbor posture scoring |
| **PCI-DSS** | Payment card data | Primary Account Numbers (PAN), card expiry, CVV/CVC columns | PAN validated via **Luhn checksum**; expiry requires regex + column-name context; CVV is a column-presence flag only (CVV should never be stored) |
| **GLBA** | Nonpublic Personal Information (financial) | Bank routing numbers, bank account numbers, loan application data, credit history data, tax return data | Routing numbers validated via **ABA mod-10 weighted checksum**; account/loan/credit/tax fields via fuzzy column-name matching |
| **SOX** | Corporate financial audit trails | Transaction timestamps, audit-trail metadata completeness | Reuses existing datetime detection; schema-completeness check across creation/approval/modification columns |
| **GDPR** | EU personal data | SSNs/national IDs, email, phone, IP address, credit card, passport, driver's license, medical record numbers, name + geolocation combinations | Regex per identifier type, each with its own confidence score |
| **CCPA / CPRA** | California consumer personal data | Same identifier set as GDPR — the two regulations protect near-identical categories of personal data, so detection logic is shared; only the report's regulation label, title, and disclaimer differ | Same detectors as GDPR |

**Human-in-the-loop confirmation** — any finding below a reliability threshold (e.g. a column merely named `loan_amount` or `cvv`, or a GDPR/CCPA identifier below its high-confidence cutoff) pauses the run and asks the user to confirm or reject it via the API/console before it can enter a report. High-confidence, checksum- or strong-regex-validated findings skip this step and go straight to results.

- **Standardization & Resolution** — RapidFuzz fuzzy text matching; 3-tier entity resolution (lookup → RapidFuzz → semantic embeddings) for master data normalization.
- **Reporting** — 8-dimension Data Quality Score, executive summaries, per-check breakdowns, interactive Gemini AI explanations ("Inspect" buttons on both quality and compliance findings — what's wrong, why it matters, what to do), and standalone per-regulation compliance reports with HTML/PDF export.

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
