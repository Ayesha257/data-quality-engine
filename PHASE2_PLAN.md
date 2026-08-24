# Phase 2: Intelligence Layer — Complete Implementation Plan

**Status:** Ready for implementation  
**Baseline:** Phase 1 complete (29 tests passing, fully functional)  
**Target:** Production-ready Phase 2 by end of project timeline  
**Approach:** Milestone-driven, feature-complete per milestone, additive (no Phase 1 changes)

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Milestone Roadmap](#milestone-roadmap)
4. [M1: Foundations (IN PROGRESS)](#m1-foundations-complete)
5. [M2: AI Explanation Layer](#m2-ai-explanation-layer)
6. [M3: ML Readiness Assessment](#m3-ml-readiness-assessment)
7. [M4: REST API](#m4-rest-api)
8. [M5: Recommendation Engine](#m5-recommendation-engine)
9. [M6: Entity Resolution](#m6-entity-resolution)
10. [M7: Feedback Loop](#m7-feedback-loop)
11. [M8: Hardening & Deployment](#m8-hardening--deployment)
12. [Testing Strategy](#testing-strategy)
13. [Integration Points](#integration-points)
14. [Deployment Checklist](#deployment-checklist)

---

## Overview

### Purpose

Phase 2 adds an **intelligence layer** on top of Phase 1's deterministic validation engine. It does NOT replace Phase 1—instead, it explains Phase 1's findings, assesses model readiness, provides recommendations, and improves with feedback.

### Design Principle

> **AI explains findings; Phase 1 makes decisions.**
>
> Every issue Phase 2 reports has already been detected by Phase 1. AI never decides whether data is valid—it only writes plain-language explanations of findings Phase 1 already made.

### Key Features

- **Structured audit trail** — Every run logged, manifested, and reproducible
- **Per-client rules** — Versioned rulesets, not hardcoded thresholds
- **AI explanations** — Plain-language context for non-technical users
- **Model readiness** — Prophet-specific precondition checks
- **Entity resolution** — Multi-tier normalization (lookup → fuzzy → semantic)
- **REST API** — Service-oriented architecture
- **Feedback loop** — Continuous improvement without retraining

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1 (Deterministic)                                     │
│ ├─ Header detection & confirmation                         │
│ ├─ Data quality checks (missing, duplicates, outliers, etc) │
│ ├─ PII detection & masking                                 │
│ ├─ Fuzzy standardization                                   │
│ └─ Scoring & reporting                                     │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 2 (Intelligence Layer)                                │
│ ├─ M1: Structured logging, run manifest, rule resolution   │
│ ├─ M2: AI explanations (LLM)                               │
│ ├─ M3: ML readiness assessment                             │
│ ├─ M4: REST API                                            │
│ ├─ M5: Recommendations                                     │
│ ├─ M6: Entity resolution (cascade)                         │
│ ├─ M7: Feedback & learning                                 │
│ └─ M8: Hardening & deployment                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Prerequisites & Setup

### System Requirements

- Python 3.10+
- PostgreSQL (production; SQLite for development)
- Redis (for job queue, M4+)
- ~500MB disk (models, logs, database)

### Environment Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Download spaCy model (for PII detection)
python -m spacy download en_core_web_sm
```

### Initialize Phase 2 (M1 Complete)

```python
# In your application startup (main.py or __main__)
from backend import (
    init_db_session,
    init_logging,
    init_rule_resolver,
)

# These are already implemented (M1 complete)
init_db_session(environment="development")
init_logging(logs_dir="logs/")
init_rule_resolver(config_dir="config/")
```

### Project Structure

```
data-quality-engine/
├── backend/
│   ├── phase2/               ← Phase 2 code (M1 complete, M2–8 ready)
│   ├── engine/               ← Phase 1 (do NOT modify)
│   └── config/
│       ├── base_rules.yaml   ← M1 complete
│       └── clients/
│           └── <client_id>/
│               └── rules_v1.yaml
├── tests/
│   └── test_phase2_m1_setup.py  ← M1 tests (13 passing)
├── logs/                     ← Created by M1
├── migrations/               ← Alembic (M8)
├── requirements.txt          ← Updated with Phase 2 deps
└── PHASE2_*.md              ← Phase 2 documentation
```

---

## Milestone Roadmap

### High-Level Timeline

```
M1: Foundations                  ✅ COMPLETE
├─ Database, logging, rules      ✅ Code done
├─ Pydantic schemas              ✅ Code done
└─ 13 integration tests          ✅ All passing

M2: AI Explanation               ⏳ READY TO START
├─ Summary compaction
├─ LLM integration
└─ Fallback template text

M3: ML Readiness                 ⏳ AFTER M2
├─ Temporal sufficiency
├─ Interval regularity
└─ Target integrity checks

M4: REST API                     ⏳ AFTER M3
├─ FastAPI app
├─ Async execution (RQ)
└─ OpenAPI docs

M5: Recommendations              ⏳ OPTIONAL (M4+)
├─ Remediation catalog
└─ Projected improvements

M6: Entity Resolution            ⏳ OPTIONAL (M4+)
├─ Lookup table
├─ Fuzzy matching
└─ Semantic resolution

M7: Feedback Loop                ⏳ OPTIONAL (M4+)
├─ Disposition capture
└─ Learning from feedback

M8: Hardening & Deployment       ⏳ LAST (all features)
├─ Docker packaging
├─ Security hardening
└─ Alembic migrations
```

### Effort Estimates

| Milestone | Effort | Duration | Notes |
|-----------|--------|----------|-------|
| M1 | High (setup) | 6 hours | ✅ COMPLETE |
| M2 | High (LLM) | 12–16 hours | Chain-of-thought prompting |
| M3 | Medium | 8–10 hours | Statistical analysis |
| M4 | High (async) | 16–20 hours | Job queue + workers |
| M5 | Medium | 8–10 hours | Logic-based suggestions |
| M6 | Medium–High | 10–12 hours | Three tiers, semantic model |
| M7 | Low | 6–8 hours | Query + feedback store |
| M8 | Medium | 10–12 hours | Docker, security, ops |

**Total: ~90–120 hours for M1–M8**

---

## M1: Foundations (COMPLETE ✅)

**Status:** ✅ Implementation complete, all tests passing

### What's Done

#### 1.1 Database Layer
- ✅ SQLAlchemy ORM models (RunRecord, CanonicalMapping, Dispositions, Ratings)
- ✅ Session management with dependency injection
- ✅ SQLite (dev) + PostgreSQL (prod) support
- ✅ Automatic table creation on startup

**Files:**
```
backend/phase2/database/
├── __init__.py           ← Session factory
└── models.py             ← ORM models (5 tables)
```

**Test:** `tests/test_phase2_m1_setup.py::TestDatabaseSetup`

#### 1.2 Structured Logging
- ✅ JSON JSONL logging per run
- ✅ Run manifest snapshots (audit trail)
- ✅ Run querying by client_id
- ✅ UUID generation for run IDs

**File:** `backend/phase2/logging_setup.py` (310 lines)

**Test:** `tests/test_phase2_m1_setup.py::TestLoggingSetup`

#### 1.3 Rule Resolution
- ✅ Base ruleset (YAML) for all clients
- ✅ Per-client overrides with schema validation
- ✅ Intelligent merging (base + client)
- ✅ Dry-run mode for testing
- ✅ Caching for performance

**Files:**
```
backend/phase2/rules.py          ← RuleResolver
config/base_rules.yaml                       ← Default thresholds
config/clients/<id>/rules_v1.yaml            ← Client overrides (template)
```

**Test:** `tests/test_phase2_m1_setup.py::TestRuleResolution`

#### 1.4 Pydantic Schemas
- ✅ 20+ validation models
- ✅ Shared across API, database, LLM
- ✅ Field validators (client_id, file_name, etc.)
- ✅ Enums for RunStatus lifecycle

**File:** `backend/phase2/schemas/models.py` (340 lines)

**Test:** `tests/test_phase2_m1_setup.py::TestPydanticSchemas`

### Verification

```bash
# Run M1 tests (should all pass)
pytest tests/test_phase2_m1_setup.py -v

# Initialize M1 components
python -c "
from backend import init_db_session, init_logging, init_rule_resolver
init_db_session()
log_setup = init_logging()
resolver = init_rule_resolver()
print('✓ M1 setup verified')
"
```

### Exit Criteria (ALL MET ✅)
- [x] Database tables created on startup
- [x] Logging writes JSON per run
- [x] Run manifest written at completion
- [x] Base rules load and merge with client overrides
- [x] Pydantic schemas validate input
- [x] 13 integration tests pass
- [x] No Phase 1 code modified
- [x] Documentation complete

---

## M2: AI Explanation Layer

**Status:** ⏳ Ready to implement  
**Effort:** 12–16 hours  
**Dependencies:** M1 complete

### Purpose

Transform structured Phase 1 findings into plain-language explanations for non-technical users. Answers:
- "Why is this finding important?"
- "What likely caused this issue?"
- "What business risk does it create?"

### Design

```
Phase 1 Results (structured)
        ↓
M2 Summary Compaction (findings → JSON payload)
        ↓
LLM Call (GPT-4o mini with strict JSON schema)
        ↓
Output Validation (Pydantic)
        ↓
Report Integration (Phase 1 tables + AI text)
        ↓
(Fallback to template text if LLM fails)
```

### Implementation Steps

#### 2.1 Summary Compaction
**Goal:** Convert Phase 1 results → compact JSON payload (token-bounded)

**File:** `backend/phase2/ai/compactor.py` (NEW)

**Code Structure:**
```python
def compact_findings(
    check_results: list[CheckResult],
    quality_scores: dict[str, float],
    client_id: str,
    column_count: int,
    row_count: int,
) -> CompactedSummary:
    """
    Convert Phase 1 results into a structured summary.
    
    Rules:
    - Group similar issues (e.g., "11 columns > 50% null")
    - Mask PII values (show only type & count, not value)
    - Cap token budget (max 2000 tokens before LLM call)
    - Include only actionable findings (not every detail)
    """
```

**Features:**
- Group identical findings by check + dimension
- Deduplicate: "column A missing 10%", "column B missing 15%" → "2 columns exceed null threshold"
- Mask PII: Never include actual values, just "[EMAIL], [PHONE_NUMBER], etc."
- Include: column names, thresholds, counts, sample values (masked)

**Testing:**
```python
def test_compact_findings_groups_identical_checks():
    """Verify that identical findings are grouped."""
    
def test_compact_findings_masks_pii():
    """Verify no unmasked PII in payload."""
    
def test_compact_findings_respects_token_budget():
    """Verify payload stays under token limit."""
```

#### 2.2 LLM Integration
**Goal:** Call GPT-4o mini with strict JSON schema output

**File:** `backend/phase2/ai/explainer.py` (NEW)

**Code Structure:**
```python
class ExplainerConfig:
    """Configuration for AI layer."""
    provider: str = "openai"          # "openai" | "anthropic" | "local"
    model: str = "gpt-4o-mini"        # Model ID
    temperature: float = 0.3          # Lower = more consistent
    max_tokens: int = 1000
    api_key: str = os.getenv("OPENAI_API_KEY")

def explain_findings(
    compacted_summary: CompactedSummary,
    ruleset: ClientRuleSet,
    config: ExplainerConfig,
    use_cache: bool = True,
) -> ExplanationOutput:
    """
    Call LLM to explain findings.
    
    Returns: ExplanationOutput with issue_id, explanation, cause, impact, confidence
    Falls back to template text if LLM fails.
    """
```

**Features:**
- One call per report (cost control)
- Strict JSON schema mode (JSON guaranteed)
- Caching: same payload hash → cached response
- Retry with backoff on rate limit
- Fallback to template text on any error

**Prompt Structure:**
```
System: You are a data quality expert explaining findings to business users.
        Never invent facts. Stick to the data provided.
        Output only valid JSON, no markdown.

User: 
  Dataset: {filename} ({row_count} rows, {column_count} columns)
  Client: {client_id}
  Quality Score: {quality_score}/100
  
  Summary of Findings:
  {compacted_findings}
  
  Active Rules:
  {ruleset_version}
  
  For each finding, provide:
  1. Plain-language explanation
  2. Likely cause
  3. Business impact
  4. Confidence (0–1)
```

**Testing:**
```python
def test_explain_findings_returns_valid_json():
    """Mock LLM and verify JSON output."""
    
def test_explain_findings_falls_back_to_template():
    """Mock LLM failure; verify fallback text used."""
    
def test_explain_findings_uses_cache():
    """Verify same payload returns cached response."""
    
def test_explain_findings_masks_pii():
    """Verify no unmasked values in LLM call."""
```

#### 2.3 Output Validation
**Goal:** Validate LLM output before use

**File:** `backend/phase2/ai/schemas.py` (NEW)

**Pydantic Models:**
```python
class ExplanationItem(BaseModel):
    finding_id: str
    issue_description: str       # Plain text explanation
    likely_cause: str            # Why this happened
    business_impact: str         # What matters
    confidence: float = Field(ge=0.0, le=1.0)

class ExplanationOutput(BaseModel):
    run_id: str
    client_id: str
    timestamp: datetime
    explanations: list[ExplanationItem]
    model_used: str
    tokens_used: int
    cost_usd: float
```

**Validators:**
- All fields non-empty
- confidence in [0, 1]
- No unmasked PII values
- tokens_used > 0

**Testing:**
```python
def test_explanation_output_validates():
    """Valid output passes schema."""
    
def test_explanation_output_rejects_invalid():
    """Invalid output (missing field, bad confidence) rejected."""
```

#### 2.4 Integration with Reporting
**Goal:** Render AI explanations in the report

**File:** `backend/engine/reporting/report_generator.py` (MODIFY)

**Changes:**
```python
def generate_report(
    phase1_results: dict,
    phase2_explanations: ExplanationOutput | None = None,  # NEW
    ...
) -> Report:
    """Generate report with Phase 1 tables + Phase 2 explanations."""
    
    # Phase 1: scores, dimension breakdowns, tables
    # Phase 2: AI explanations (if present)
    # Fallback: template text (if Phase 2 disabled or failed)
```

**Report Section (M2 New):**
```
Data Quality Analysis

OVERALL QUALITY SCORE: 82/100

Phase 1 Findings (Deterministic)
[Existing tables: missing values, duplicates, outliers, etc.]

Interpretation & Business Context (Phase 2 AI Explanations)
├─ Finding: 8 columns exceed 50% null threshold
│  └─ Explanation: [AI-generated plain text]
│     - Likely Cause: [AI analysis]
│     - Business Impact: [AI interpretation]
│     - Confidence: 92%
│
├─ Finding: "Lahore" spelled 5 ways
│  └─ Explanation: [AI-generated]
│     ...
```

**Testing:**
```python
def test_report_includes_phase2_explanations():
    """AI explanations appear in report."""
    
def test_report_degrades_to_template_text():
    """If Phase 2 unavailable, report still complete with template text."""
```

### Exit Criteria

- [ ] Summary compaction groups identical findings
- [ ] LLM integration with GPT-4o mini (or fallback)
- [ ] Strict JSON schema validation
- [ ] Response caching by payload hash
- [ ] Fallback template text (if LLM fails)
- [ ] Report integration (Phase 1 + AI explanations)
- [ ] 10+ test cases (compaction, LLM mocking, fallback, caching)
- [ ] Cost per report < $0.01 (token-bounded)
- [ ] No Phase 1 changes

### Configuration

**Environment Variables (.env):**
```
OPENAI_API_KEY=sk-...          # For M2
ANTHROPIC_API_KEY=sk-ant-...   # Fallback provider (optional)
AI_TEMPERATURE=0.3
AI_MAX_TOKENS=1000
```

**Config File (config/ai_config.yaml):**
```yaml
ai_provider: openai
ai_model: gpt-4o-mini
ai_enabled: true
ai_fallback_to_template: true
ai_cache_responses: true
ai_temperature: 0.3
ai_max_tokens: 1000

# Cost control
ai_monthly_budget_usd: 50
ai_per_report_budget_usd: 0.01

# Prompt examples (few-shot learning)
few_shot_examples:
  - finding: "50% null in 'Amount' column"
    explanation: "This column has many missing values..."
```

### Dependencies

Already in requirements.txt:
- `openai>=1.3`
- `pydantic>=2.0`

---

## M3: ML Readiness Assessment

**Status:** ⏳ Ready after M2  
**Effort:** 8–10 hours  
**Dependencies:** M1 complete (M2 optional)

### Purpose

Assess whether data is ready for Prophet forecasting before investing time in model building. Answers:
- "Can we forecast with this data?"
- "What's blocking us?"
- "How much history do we have?"

### Design

```
Phase 1 Results + Scoring
        ↓
Temporal Sufficiency (observations, span, seasonal cycles)
        ↓
Interval Regularity (frequency, gaps, duplicates)
        ↓
Target Integrity (nulls, zeros, outliers, variance)
        ↓
Leakage Detection (perfect correlation with target)
        ↓
Readiness Sub-Scores + Blockers
        ↓
Report: Verdict + Recommended Actions
```

### Implementation Steps

#### 3.1 Temporal Analysis
**File:** `backend/phase2/readiness/temporal.py` (NEW)

**Code Structure:**
```python
@dataclass
class TemporalAnalysis:
    total_observations: int
    date_range_days: int
    implied_frequency: str          # 'daily', 'weekly', 'monthly', etc.
    seasonal_cycles_detected: int   # count of full cycles
    sufficient: bool
    blockers: list[str]             # If not sufficient

def analyze_temporal_sufficiency(
    df: pd.DataFrame,
    date_column: str,
    frequency: str | None = None,
) -> TemporalAnalysis:
    """
    Check temporal preconditions for Prophet.
    
    Rules:
    - Need at least 2 full seasonal cycles (e.g., 2 years of daily data)
    - Minimum 30 observations
    - Date column must be valid and monotonic
    """
```

**Prophet Preconditions:**
- Minimum observations: 30
- Minimum span: 2 × seasonal period (e.g., 2 years for yearly seasonality)
- Date column: contiguous, monotonically increasing, no duplicates
- Frequency: auto-detected or specified

**Testing:**
```python
def test_temporal_sufficient_with_2_years_daily():
    """730+ observations, daily, 2 full years → sufficient."""
    
def test_temporal_insufficient_with_1_year():
    """365 observations, daily, 1 year → blocker: need 2 cycles."""
    
def test_temporal_detects_frequency():
    """Auto-detect daily, weekly, monthly, yearly."""
```

#### 3.2 Interval Regularity
**File:** `backend/phase2/readiness/intervals.py` (NEW)

**Code Structure:**
```python
@dataclass
class IntervalAnalysis:
    inferred_frequency: str
    observations_expected: int
    observations_actual: int
    missing_intervals: int
    gap_size_max_days: int
    duplicate_timestamps: int
    regularity_score: float        # 0–1
    sufficient: bool
    blockers: list[str]

def analyze_interval_regularity(
    df: pd.DataFrame,
    date_column: str,
) -> IntervalAnalysis:
    """
    Check if observations are regularly spaced.
    
    Prophet needs regular intervals. Gaps and duplicates break forecasting.
    """
```

**Checks:**
- Infer frequency (daily, weekly, monthly, etc.)
- Count missing periods (expected vs. actual)
- Find longest gap
- Flag duplicate timestamps
- Compute regularity score (1.0 = perfect)

**Testing:**
```python
def test_regularity_perfect_daily():
    """Daily data with no gaps → score 1.0, no blockers."""
    
def test_regularity_with_weekend_gaps():
    """Weekday data (weekends missing) → detected, regularity_score < 1.0."""
    
def test_regularity_duplicate_timestamps():
    """Duplicate timestamps → blocker, score penalized."""
```

#### 3.3 Target Integrity
**File:** `backend/phase2/readiness/target.py` (NEW)

**Code Structure:**
```python
@dataclass
class TargetAnalysis:
    column_name: str
    data_type: str
    null_count: int
    null_pct: float
    zero_count: int
    zero_pct: float
    outlier_count: int
    outlier_pct: float
    variance: float
    mean: float
    min_value: float
    max_value: float
    
    sufficient: bool
    blockers: list[str]

def analyze_target_integrity(
    df: pd.DataFrame,
    target_column: str,
) -> TargetAnalysis:
    """
    Check if target column is suitable for forecasting.
    
    Blockers:
    - > 10% nulls
    - > 50% zeros (no signal)
    - Near-zero variance (constant series, unforecastable)
    - > 30% outliers
    """
```

**Checks:**
- Null ratio (warn if > 10%, block if > 30%)
- Zero ratio (warn if > 50%)
- Variance (block if nearly zero)
- Outlier density (warn if > 20%, block if > 30%)
- Data type (must be numeric or convertible)

**Testing:**
```python
def test_target_sufficient_with_low_nulls():
    """< 5% nulls, 10% zeros, high variance → sufficient."""
    
def test_target_blocker_near_constant():
    """Variance nearly zero → blocker: unforecastable."""
    
def test_target_blocker_too_many_nulls():
    """> 30% nulls → blocker: data too sparse."""
```

#### 3.4 Leakage & Cardinality
**File:** `backend/phase2/readiness/leakage.py` (NEW)

**Code Structure:**
```python
@dataclass
class LeakageAnalysis:
    perfect_correlation_features: list[str]  # Correlated 1.0 with target
    high_cardinality_features: list[str]     # Unique count ≈ row count
    identifier_features: list[str]           # ID columns (useless)
    concern_level: str                       # 'none' | 'warning' | 'blocker'

def analyze_leakage_and_cardinality(
    df: pd.DataFrame,
    target_column: str,
) -> LeakageAnalysis:
    """
    Detect features that leak information or have too many unique values.
    
    Leakage: feature perfectly predicts target → invalid for forecasting.
    Cardinality: feature has unique_count ≈ row_count → no pattern.
    """
```

**Checks:**
- Correlation with target (flag if abs(corr) > 0.99)
- Cardinality (flag if unique_count / row_count > 0.95)
- ID columns (regex: *_id, *_no, *_code with high cardinality)

**Testing:**
```python
def test_leakage_detected():
    """Feature = target + constant → perfect correlation, flagged."""
    
def test_high_cardinality_flagged():
    """100 rows, 99 unique values → flagged."""
    
def test_identifier_column_detected():
    """'customer_id' with 100/100 unique → flagged as identifier."""
```

#### 3.5 Readiness Scorer
**File:** `backend/phase2/readiness/scorer.py` (NEW)

**Code Structure:**
```python
@dataclass
class ReadinessScore:
    overall_score: float                 # 0–100
    temporal_score: float
    interval_score: float
    target_score: float
    leakage_score: float
    
    verdict: str                         # 'ready' | 'caution' | 'not_ready'
    blockers: list[str]                  # Hard failures
    warnings: list[str]                  # Soft issues
    recommendations: list[str]           # Actions to improve

def score_readiness(
    df: pd.DataFrame,
    target_column: str,
    date_column: str,
) -> ReadinessScore:
    """
    Compute Prophet readiness score.
    
    Score = weighted sum of sub-scores.
    Blockers are reported separately (not averaged into score).
    """
```

**Scoring:**
```
Weights:
  Temporal:    30% (must have enough history)
  Interval:    20% (regularity matters)
  Target:      30% (quality of what we're forecasting)
  Leakage:     20% (data integrity)

Verdict:
  ≥ 80 + no blockers   → "ready"
  ≥ 60 + no blockers   → "caution" (proceed with care)
  < 60 OR has blockers → "not_ready"
```

**Testing:**
```python
def test_readiness_score_ready():
    """Good temporal, regular intervals, clean target → 'ready'."""
    
def test_readiness_score_not_ready_blocker():
    """Any blocker → 'not_ready' even if score high."""
    
def test_readiness_blockers_not_averaged():
    """Blockers reported separately, not folded into score."""
```

#### 3.6 Report Integration
**File:** `backend/engine/reporting/report_generator.py` (MODIFY)

**New Section in Report:**
```
ML Model Readiness Assessment

Recommendation: [READY | CAUTION | NOT_READY]
Overall Score: [80]/100

Temporal Sufficiency: [score] [blocker status]
  - Observations: 365 (minimum: 30)
  - Date Range: 365 days
  - Seasonal Cycles: 1 [WARNING: Need 2+ cycles]
  - Conclusion: Insufficient history for 1-year seasonality

Interval Regularity: [score] [blocker status]
  - Frequency: Daily
  - Missing Intervals: 0
  - Duplicate Timestamps: 0
  - Regularity Score: 1.0
  - Conclusion: Perfectly regular intervals

Target Integrity: [score] [blocker status]
  - Column: 'Amount'
  - Null %: 2.1% [OK]
  - Zero %: 5.3% [OK]
  - Variance: 12,534 [OK]
  - Outliers: 1.2% [OK]
  - Conclusion: Target quality acceptable

Leakage & Cardinality: [score] [blocker status]
  - Perfect Correlation Features: None
  - High Cardinality Features: None
  - Conclusion: No leakage detected

Recommendations:
  1. Collect 1 more year of data (need 2 full seasonal cycles)
  2. Monitor for data quality degradation
```

### Exit Criteria

- [ ] Temporal analysis (observations, span, seasonal cycles)
- [ ] Interval regularity (frequency, gaps, duplicates)
- [ ] Target integrity (nulls, zeros, variance, outliers)
- [ ] Leakage detection (perfect correlation, high cardinality)
- [ ] Readiness score (weighted sub-scores)
- [ ] Blockers reported separately (not averaged)
- [ ] Report integration (new section in output)
- [ ] 12+ test cases
- [ ] No Phase 1 changes

---

## M4: REST API

**Status:** ⏳ Ready after M3  
**Effort:** 16–20 hours  
**Dependencies:** M1 complete, M2 optional, M3 optional

### Purpose

Expose the engine as an HTTP service. Enables:
- Dashboard integration
- Scheduled jobs
- External client systems
- Multi-tenant isolation

### Architecture

```
FastAPI Application
├─ Endpoints (routes/\*.py)
├─ Authentication & Authorization
├─ Request Validation (Pydantic)
├─ Background Jobs (RQ workers)
└─ Database (SQLAlchemy)

    ↓
RQ Workers (Redis queue)
├─ Process long-running checks
├─ Pipeline orchestration
└─ Report generation

    ↓
Results stored → Returned to client
```

### Implementation Steps

#### 4.1 FastAPI Application
**File:** `backend/phase2/api/app.py` (NEW)

**Code Structure:**
```python
from fastapi import FastAPI, Depends, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

app = FastAPI(
    title="Data Quality Engine API",
    version="2.0.0",
    description="Intelligent data quality assessment",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Lifespan: initialize Phase 2 components
@app.lifespan
async def lifespan(app: FastAPI):
    # Setup
    init_db_session()
    init_logging()
    init_rule_resolver()
    # Startup code here
    yield
    # Shutdown code here
```

**Middleware:**
- CORS (if needed)
- Request logging
- Error handling
- Rate limiting (M8)

**Health Checks:**
```python
@app.get("/health")
async def health_check():
    """
    Returns: {
      "status": "ok",
      "database": "connected",
      "redis": "connected",
      "models": "loaded"
    }
    """
```

#### 4.2 File Upload Endpoint
**File:** `backend/phase2/api/routes.py` (NEW)

**Endpoint:**
```python
@app.post("/v1/files/upload", response_model=FileUploadResponse)
async def upload_file(
    client_id: str = Query(...),
    file: UploadFile = File(...),
    sheet_name: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Upload a file for analysis.
    
    Request:
      POST /v1/files/upload?client_id=client_a
      file: orders.xlsx
      
    Response:
      {
        "run_id": "abc-123",
        "status": "pending",
        "created_at": "2026-08-06T..."
      }
    """
    # Validate
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(400, "Only .xlsx, .xls, .csv allowed")
    
    if file.size > 200 * 1024 * 1024:  # 200MB
        raise HTTPException(413, "File too large")
    
    # Store file (unique name)
    run_id = str(uuid.uuid4())
    file_path = Path("uploads") / f"{run_id}_{file.filename}"
    file_path.parent.mkdir(exist_ok=True)
    
    contents = await file.read()
    with open(file_path, 'wb') as f:
        f.write(contents)
    
    # Queue job
    job = queue.enqueue(
        'backend.routes.workers.run_pipeline',
        run_id=run_id,
        client_id=client_id,
        file_path=str(file_path),
        sheet_name=sheet_name,
    )
    
    # Create run record
    run = RunRecord(
        run_id=run_id,
        client_id=client_id,
        file_name=file.filename,
        file_hash=hashlib.sha256(contents).hexdigest(),
        file_size_bytes=len(contents),
        status="pending",
    )
    db.add(run)
    db.commit()
    
    return FileUploadResponse(run_id=run_id, status="pending")
```

#### 4.3 Status Polling
**Endpoint:**
```python
@app.get("/v1/runs/{run_id}/status", response_model=RunStatusResponse)
async def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
):
    """
    Poll run status.
    
    Response:
      {
        "run_id": "abc-123",
        "status": "checking",  # pending→ingesting→checking→scoring→complete
        "progress_pct": 45,
        "current_stage": "duplicate detection",
        "started_at": "...",
        "updated_at": "..."
      }
    """
    run = db.query(RunRecord).filter_by(run_id=run_id).first()
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    
    job = queue.fetch_job(run_id)
    
    return RunStatusResponse(
        run_id=run_id,
        status=RunStatus(run.status),
        progress_pct=job.meta.get('progress', 0) if job else 100,
        current_stage=job.meta.get('stage') if job else None,
        started_at=run.started_at,
        updated_at=datetime.utcnow(),
    )
```

#### 4.4 Results Retrieval
**Endpoint:**
```python
@app.get("/v1/runs/{run_id}/results", response_model=ReportResponse)
async def get_run_results(
    run_id: str,
    db: Session = Depends(get_db),
):
    """
    Get results of a completed run.
    
    Response:
      {
        "run_id": "abc-123",
        "quality_score": 82.5,
        "readiness_verdict": "ready",
        "issues_by_dimension": {
          "completeness": 5,
          "validity": 2,
          ...
        },
        "report_html_url": "/v1/runs/abc-123/report.html"
      }
    """
    run = db.query(RunRecord).filter_by(run_id=run_id).first()
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    
    if run.status != "complete":
        raise HTTPException(400, f"Run not complete yet: {run.status}")
    
    return ReportResponse(
        run_id=run_id,
        quality_score=run.quality_score,
        readiness_verdict=run.readiness_verdict,
        issues_by_dimension=json.loads(run.dimension_scores),
        report_html_url=f"/v1/runs/{run_id}/report.html",
    )
```

#### 4.5 Report Generation
**Endpoint:**
```python
@app.get("/v1/runs/{run_id}/report.{format}")
async def get_report(
    run_id: str,
    format: str = "html",  # html | pdf | json
    db: Session = Depends(get_db),
):
    """
    Download generated report.
    
    Formats:
      - HTML (interactive)
      - PDF (print-friendly)
      - JSON (machine-readable)
    """
    run = db.query(RunRecord).filter_by(run_id=run_id).first()
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    
    if format == "html":
        report_path = Path("reports") / f"{run_id}.html"
        if not report_path.exists():
            raise HTTPException(404, "Report not generated yet")
        return FileResponse(report_path, media_type="text/html")
    
    elif format == "pdf":
        report_path = Path("reports") / f"{run_id}.pdf"
        if not report_path.exists():
            raise HTTPException(404, "Report not generated yet")
        return FileResponse(report_path, media_type="application/pdf")
    
    elif format == "json":
        return {
            "run_id": run_id,
            "quality_score": run.quality_score,
            "dimensions": json.loads(run.dimension_scores),
        }
```

#### 4.6 Client Rules Management
**Endpoint:**
```python
@app.get("/v1/clients/{client_id}/rules", response_model=ClientRuleSet)
async def get_active_rules(client_id: str):
    """Get active ruleset for a client."""
    resolver = get_rule_resolver()
    ruleset = resolver.resolve(client_id)
    return ruleset

@app.post("/v1/clients/{client_id}/rules/dry-run")
async def dry_run_rules(
    client_id: str,
    rules_yaml: str = Body(...),
):
    """
    Test new ruleset without saving.
    
    Response:
      {
        "valid": true,
        "error": null,
        "thresholds": 8,
        "business_rules": 3
      }
    """
    resolver = get_rule_resolver()
    is_valid, error = resolver.dry_run(client_id, rules_yaml)
    return {
        "valid": is_valid,
        "error": error,
    }
```

#### 4.7 Background Workers
**File:** `backend/phase2/api/workers.py` (NEW)

**Worker:**
```python
import redis
from rq import Queue, Worker

redis_conn = redis.Redis(host='localhost', port=6379)
queue = Queue(connection=redis_conn)

def run_pipeline(run_id: str, client_id: str, file_path: str, sheet_name: str | None):
    """
    Background job: complete pipeline execution.
    
    Stages:
    1. Ingestion (load file, detect header)
    2. PII masking
    3. Data quality checks (Phase 1)
    4. Scoring
    5. Rule evaluation
    6. ML readiness (M3)
    7. AI explanations (M2)
    8. Report generation
    9. Cleanup
    """
    job = queue.connection.get_current_job()
    
    try:
        # Setup
        logger = get_log_setup().get_logger(run_id)
        ruleset = get_rule_resolver().resolve(client_id)
        db = next(get_db())
        run = db.query(RunRecord).filter_by(run_id=run_id).first()
        
        # Stage 1: Ingestion
        job.meta['stage'] = 'ingestion'
        job.meta['progress'] = 10
        job.save_meta()
        
        df = load_excel_file(file_path, sheet_name)
        run.data_rows = len(df)
        run.data_columns = len(df.columns)
        run.status = "ingesting"
        db.commit()
        
        # Stage 2: PII Masking
        job.meta['stage'] = 'pii masking'
        job.meta['progress'] = 20
        job.save_meta()
        
        df = mask_pii_dataframe(df)
        
        # Stage 3: Checks (Phase 1)
        job.meta['stage'] = 'data quality checks'
        job.meta['progress'] = 40
        job.save_meta()
        
        check_results = run_phase1_checks(df)
        
        # Stage 4: Scoring
        job.meta['stage'] = 'scoring'
        job.meta['progress'] = 60
        job.save_meta()
        
        scores = score_results(check_results)
        run.quality_score = scores['overall']
        run.dimension_scores = json.dumps(scores['dimensions'])
        
        # Stage 5: ML Readiness (optional, M3)
        if READINESS_ENABLED:
            job.meta['stage'] = 'readiness assessment'
            job.meta['progress'] = 70
            job.save_meta()
            readiness = assess_readiness(df)
            run.readiness_score = readiness.overall_score
            run.readiness_verdict = readiness.verdict
        
        # Stage 6: AI Explanations (optional, M2)
        if AI_ENABLED:
            job.meta['stage'] = 'ai explanations'
            job.meta['progress'] = 75
            job.save_meta()
            explanations = explain_findings(check_results, ruleset)
        else:
            explanations = None
        
        # Stage 7: Report Generation
        job.meta['stage'] = 'report generation'
        job.meta['progress'] = 85
        job.save_meta()
        
        report_html = generate_html_report(
            check_results, scores, explanations, ruleset
        )
        report_pdf = generate_pdf_report(
            check_results, scores, explanations, ruleset
        )
        
        # Cleanup
        Path(file_path).unlink()  # Delete uploaded file
        
        # Completion
        run.status = "complete"
        run.completed_at = datetime.utcnow()
        run.log_file_path = f"logs/run_{run_id}.jsonl"
        db.commit()
        
        job.meta['progress'] = 100
        job.meta['stage'] = 'complete'
        job.save_meta()
        
    except Exception as e:
        logger.exception("Pipeline failed", exc_info=True)
        run.status = "failed"
        run.error_message = str(e)
        db.commit()
        job.meta['error'] = str(e)
        job.save_meta()
        raise
```

### Testing

**Tests:**
```python
def test_upload_file_success():
    """File upload creates run and queues job."""
    
def test_upload_file_invalid_format():
    """Non-xlsx/xls/csv rejected."""
    
def test_status_polling():
    """Poll status progresses from pending → complete."""
    
def test_get_results_not_ready():
    """Results unavailable while run in progress."""
    
def test_get_results_success():
    """Results retrieved after completion."""
    
def test_get_report_formats():
    """HTML, PDF, JSON formats generated."""
    
def test_rules_dry_run():
    """New ruleset tested without saving."""
    
def test_api_validation():
    """Invalid input (bad client_id, oversized file) rejected."""
```

### Deployment (M4)

**Dependencies:**
- Redis (for job queue)
- PostgreSQL (production)

**Startup:**
```bash
# Start API
uvicorn backend.app:app --reload

# Start workers (separate process)
rq worker -c backend.routes.config
```

**Environment:**
```
DATABASE_URL=postgresql://...
REDIS_URL=redis://localhost:6379
OPENAI_API_KEY=sk-...
```

### Exit Criteria

- [ ] FastAPI app with 8+ endpoints
- [ ] File upload with validation
- [ ] Async job execution (RQ)
- [ ] Status polling
- [ ] Results retrieval
- [ ] Report download (HTML, PDF, JSON)
- [ ] Client rules management
- [ ] 15+ API tests
- [ ] OpenAPI docs generated
- [ ] No Phase 1 changes

---

## M5: Recommendation Engine

**Status:** ⏳ Optional after M4  
**Effort:** 8–10 hours  
**Dependencies:** M1 complete, M4 (REST API)

### Purpose

Suggest actions to improve data quality, with estimated impact.

### Implementation

**File:** `backend/phase2/models/recommender.py` (NEW)

**Logic:**
```python
def generate_recommendations(
    check_results: list[CheckResult],
    quality_scores: dict[str, float],
    ruleset: ClientRuleSet,
) -> list[Recommendation]:
    """
    Suggest concrete actions with estimated impact.
    
    Example:
      Issue: 'Amount' column 50% null
      Recommendation: "Remove rows where Amount is null (or impute with median)"
      Estimated Impact: completeness +15%, overall score +5 points
      Effort: Low (2 hours data cleaning)
    """
```

**Recommendation Types:**
1. **Data Cleaning** — Remove/fix problematic rows/columns
2. **Data Imputation** — Fill missing values
3. **Standardization** — Apply fuzzy matching
4. **Schema Fixes** — Rename columns, fix types
5. **Collection** — Gather missing data

**Prioritization:**
- High impact + low effort → Recommended first
- Low impact + high effort → Listed last
- Estimated: effort in hours, impact in points

---

## M6: Entity Resolution (Multi-Tier Cascade)

**Status:** ⏳ Optional after M4  
**Effort:** 10–12 hours  
**Dependencies:** M1 complete

### Purpose

Resolve entity variants (Lahore/LHR/lhr → Lahore) using a three-tier cascade for cost control.

### Architecture

```
Input: "LHR"
    ↓
Tier 1: Lookup Table (instant, free)
  "LHR" → "Lahore" (from previous runs)
    ↓ (if not found)
Tier 2: Fuzzy Matching (fast, cheap)
  RapidFuzz: "LHR" ≈ "Lahore" (similarity 85%)
    ↓ (if below threshold)
Tier 3: Semantic Matching (slower, more accurate)
  all-MiniLM embeddings: "LHR" semantic ≈ "Lahore"
    ↓ (if unresolved)
Review Queue: User confirmation needed
```

### Implementation

**Files:**
```
backend/phase2/entity_resolution/
├── cascade.py          ← Multi-tier orchestration
├── lookup.py           ← Tier 1: Lookup table
├── fuzzy.py            ← Tier 2: RapidFuzz
└── semantic.py         ← Tier 3: Embeddings
```

**Code:**
```python
class EntityResolutionCascade:
    def resolve(
        self,
        values: list[str],
        entity_type: str,  # 'city', 'region', 'product_code'
    ) -> dict[str, ResolvedValue]:
        """
        Resolve entity values through three tiers.
        
        Returns:
        {
          "LHR": {
            "canonical": "Lahore",
            "confidence": 0.95,
            "tier": 1,
            "requires_review": False
          },
          "lhr": {
            "canonical": "Lahore",
            "confidence": 0.88,
            "tier": 2,
            "requires_review": False
          },
          "unknown_city": {
            "canonical": None,
            "confidence": 0.0,
            "tier": None,
            "requires_review": True  # Queue for user
          }
        }
        """
```

---

## M7: Feedback Loop

**Status:** ⏳ Optional after M4  
**Effort:** 6–8 hours  
**Dependencies:** M1 (database)

### Purpose

Capture user feedback and improve system over time without retraining.

### Components

1. **Finding Dispositions** — User marks findings as accepted/false positive/accepted-risk
2. **Entity Mapping Confirmations** — User approves suggested entity matches
3. **Explanation Ratings** — Rate AI-generated explanations (1–5 stars)

### Learning Loop

```
User Feedback
    ↓
Store in Database (RunRecord → dispositions, mappings, ratings)
    ↓
Aggregate (false positive patterns, approved mappings)
    ↓
Improve System
  - Threshold suggestions (if many false positives on same check)
  - Mapping improvements (reuse approved mappings)
  - Few-shot examples (high-rated explanations as examples)
    ↓
Next Run (benefits from feedback)
```

---

## M8: Hardening & Deployment

**Status:** ⏳ Last milestone (M1–M7 complete)  
**Effort:** 10–12 hours  
**Dependencies:** M1–M7 complete

### Components

#### 8.1 Docker Packaging
**File:** `Dockerfile` (NEW)

```dockerfile
# Multi-stage build
FROM python:3.10-slim as builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# Download models (baked into image)
RUN python -m spacy download en_core_web_sm

# Runtime stage
FROM python:3.10-slim

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY backend/ ./backend/
COPY config/ ./config/

ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0"]
```

#### 8.2 Security Hardening
- Rate limiting per tenant
- Input validation (Pydantic)
- Secrets in environment only
- HTTPS enforced
- CSRF protection
- Tenant isolation

#### 8.3 Alembic Migrations
**Setup:**
```bash
alembic init migrations
```

**Usage:**
```bash
# Create migration
alembic revision --autogenerate -m "Add readiness_score column"

# Apply migration
alembic upgrade head
```

#### 8.4 Monitoring & Logging
- Health checks (database, Redis, models)
- Structured logs shipped to external service
- Performance metrics
- Error alerts

#### 8.5 Documentation
- API documentation (OpenAPI/Swagger)
- Deployment guide
- Ops runbook
- Troubleshooting guide

---

## Testing Strategy

### Unit Tests (Per Module)
```
tests/
├── phase2/
│   ├── test_m1_database.py           ✓ (13 tests, complete)
│   ├── test_m2_explainer.py          (mock LLM)
│   ├── test_m3_readiness.py          (analytical)
│   ├── test_m4_api.py                (endpoints)
│   ├── test_m5_recommender.py        (logic)
│   ├── test_m6_entity_resolution.py  (cascade)
│   └── test_m7_feedback.py           (learning)
```

### Integration Tests
- Full pipeline end-to-end
- Database + logging + rules
- API upload → completion → report
- Fallback paths (if M2/M3 disabled)

### Contract Tests (FastAPI)
- Valid/invalid request payloads
- Error responses (400, 404, 500)
- Response schemas

### Golden-File Tests
- Phase 1 results unchanged by Phase 2
- Report format consistency

### Property-Based Tests (Hypothesis)
- Scores always in [0, 100]
- Manifests always valid JSON
- No unmasked PII in outputs

### Evaluation Set (AI Quality)
- ~30 findings with reference explanations
- Scored on accuracy, readability, hallucination
- Regression suite: re-score when prompt changes

### Coverage Target
- **M1–M4:** 85% code coverage
- **M5–M8:** 75% code coverage (more exploratory)
- **CI:** Run on every commit

---

## Integration Points

### Phase 1 → Phase 2 Calls

**In orchestrator (Phase 2's main entry point):**
```python
# Load Phase 1 modules
from backend.engine.ingestion import load_with_confirmed_header
from backend.engine.checks import run_all_checks
from backend.engine.scoring import score_results
from backend.engine.pii import mask_pii_dataframe

# Phase 1 pipeline
df = load_with_confirmed_header(file_path, sheet_name)
df = mask_pii_dataframe(df)
check_results = run_all_checks(df)
quality_scores = score_results(check_results)

# Phase 2 wrappers
ruleset = get_rule_resolver().resolve(client_id)
explanations = explain_findings(check_results, ruleset)  # M2
readiness = assess_readiness(df)                        # M3
report = generate_report(check_results, explanations, readiness)
```

**Phase 1 Code: NO CHANGES**
- Phase 2 only calls Phase 1 functions
- Never modifies Phase 1 modules
- Phase 1 fully functional standalone

---

## Deployment Checklist

### Pre-Deployment (M1–M8 Complete)

#### Code Quality
- [ ] All tests passing (pytest)
- [ ] Coverage > 80% (M1–M4), > 75% (M5–M8)
- [ ] Type hints on all functions
- [ ] Docstrings complete
- [ ] No hardcoded secrets
- [ ] No debug print statements

#### Security
- [ ] Environment variables for secrets
- [ ] Input validation (Pydantic)
- [ ] Rate limiting configured
- [ ] CORS configured
- [ ] HTTPS enforced
- [ ] Database credentials encrypted

#### Documentation
- [ ] API documentation complete
- [ ] Deployment guide written
- [ ] Ops runbook created
- [ ] Troubleshooting guide written
- [ ] Examples for each endpoint

#### Database
- [ ] Migrations tested (dev → prod)
- [ ] Backup strategy documented
- [ ] Alembic version control

#### Operations
- [ ] Health checks configured
- [ ] Logging to external service
- [ ] Error alerting set up
- [ ] Performance metrics enabled
- [ ] Monitoring dashboard created

### Deployment Steps

1. **Prepare environment**
   ```bash
   export DATABASE_URL=postgresql://...
   export OPENAI_API_KEY=sk-...
   export REDIS_URL=redis://...
   ```

2. **Build Docker image**
   ```bash
   docker build -t dqe:2.0 .
   ```

3. **Run database migrations**
   ```bash
   docker run dqe:2.0 alembic upgrade head
   ```

4. **Start services**
   ```bash
   # API
   docker run -p 8000:8000 dqe:2.0
   
   # Workers (separate container)
   docker run dqe:2.0 rq worker
   ```

5. **Verify**
   ```bash
   curl http://localhost:8000/health
   # {"status": "ok", "database": "connected", ...}
   ```

### Post-Deployment

- [ ] Health checks passing
- [ ] Test upload → report flow
- [ ] Logs being shipped correctly
- [ ] Alerts configured and tested
- [ ] Backup tested
- [ ] Ops team trained

---

## Configuration Reference

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost/dqe
SQLALCHEMY_ECHO=false  # Set to true for debugging

# Logging
LOGS_DIR=logs/
LOG_LEVEL=INFO

# Phase 2: AI Explanations (M2)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...  # Optional fallback
AI_ENABLED=true
AI_TEMPERATURE=0.3
AI_MAX_TOKENS=1000
AI_MONTHLY_BUDGET_USD=50
AI_PER_REPORT_BUDGET_USD=0.01

# Phase 2: ML Readiness (M3)
READINESS_ENABLED=true
MIN_OBSERVATIONS=30
MIN_SEASONAL_CYCLES=2

# Phase 2: REST API (M4)
REDIS_URL=redis://localhost:6379
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4

# Phase 2: Feedback (M7)
FEEDBACK_ENABLED=true
FEW_SHOT_EXAMPLES_COUNT=5

# Security
SECRET_KEY=your-secret-key-here
ENVIRONMENT=development  # development | production
```

### Configuration Files

**config/base_rules.yaml** (M1):
```yaml
client_id: __base__
version: v1.0
thresholds: [...]
business_rules: [...]
```

**config/ai_config.yaml** (M2):
```yaml
ai_provider: openai
ai_model: gpt-4o-mini
ai_enabled: true
ai_fallback_to_template: true
ai_cache_responses: true
```

---

## Success Criteria by Milestone

### M1: Foundations ✅
- [x] Database schema + ORM
- [x] JSON logging + manifests
- [x] Rule resolution engine
- [x] Pydantic schemas
- [x] 13 tests passing
- [x] No Phase 1 changes

### M2: AI Explanations
- [ ] Summary compaction
- [ ] LLM integration
- [ ] JSON schema validation
- [ ] Fallback template
- [ ] Report integration
- [ ] 10+ tests

### M3: ML Readiness
- [ ] Temporal analysis
- [ ] Interval regularity
- [ ] Target integrity
- [ ] Leakage detection
- [ ] Readiness score
- [ ] 12+ tests

### M4: REST API
- [ ] FastAPI app
- [ ] 8+ endpoints
- [ ] Async execution (RQ)
- [ ] OpenAPI docs
- [ ] 15+ tests
- [ ] Deployed

### M5–M8: Optional Features
- [ ] (As per feature checklist)

---

## References

1. **Data Quality Engine Plan** — `Data_Quality_Engine_Plan.docx` (Appendix: Phase 2)
2. **M1 Setup Guide** — `PHASE2_SETUP.md`
3. **Quick Start** — `PHASE2_QUICK_START.md`
4. **Implementation Summary** — `PHASE2_IMPLEMENTATION_SUMMARY.md`
5. **API Documentation** — (Auto-generated at `/docs` after M4)
6. **Deployment Guide** — (To be written during M8)

---

## Timeline Example

**Assuming 40 hours/week:**

| Week | Milestone | Effort | Status |
|------|-----------|--------|--------|
| 1 | M1 (Foundations) | 6 hours | ✅ COMPLETE |
| 2–3 | M2 (AI Explanations) | 16 hours | ⏳ Ready |
| 3–4 | M3 (ML Readiness) | 10 hours | ⏳ Ready |
| 5 | M4 (REST API) | 20 hours | ⏳ Ready |
| 6 | M5 (Recommendations) | 10 hours | ⏳ Optional |
| 6–7 | M6 (Entity Resolution) | 12 hours | ⏳ Optional |
| 7 | M7 (Feedback) | 8 hours | ⏳ Optional |
| 8 | M8 (Hardening) | 12 hours | ⏳ Last |

**Total: ~90–120 hours**
**Timeline: 2–3 months (full-time), 4–6 months (part-time)**

---

**Ready to begin! Start with M2 after M1 verification.**
