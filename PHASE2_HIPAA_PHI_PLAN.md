# Phase 2 — HIPAA PHI Compliance Check (M9)

**Project:** Data Quality Engine  
**Author:** Ayesha Amer  
**Phase:** Phase 2 — Module 9 (Compliance Layer)  
**Status:** Complete — wired into `main.py` (`run_pipeline`, after Task 4 PII, before Task 6 scoring); 15/15 tests passing in `tests/test_hipaa_compliance.py`  
**Baseline:** Phase 1 `detect_pii.py` / `mask_pii.py` (Presidio + custom regex); Phase 2 M3 Readiness pattern (`CheckResult`-compatible, fault-tolerant, no AI)  
**Audience:** Human developers and AI coding assistants. Every module below includes exact inputs, outputs, function signatures, and behavior rules so generated code is consistent, testable, and matches the chosen architecture — no guessing required.

---

## 0. One-Paragraph Summary (read this first)

The HIPAA PHI Compliance Check is a **mapping and scoring layer** on top of Phase 1 PII detection — it does **not** re-implement entity recognition from scratch. It takes Presidio/custom-regex hits already produced by `detect_pii.py`, adds a small set of HIPAA-specific custom recognizers (MRN, beneficiary ID, VIN, device serial, fax, age>89), maps every hit onto the **18 official HIPAA identifiers** defined by HHS, and produces a dataset-level compliance posture (`PHI_DETECTED`, `NO_PHI_DETECTED`, or `PARTIAL_SCOPE`) with **counts per identifier per column only** — never raw PHI values. Identifiers #16 (biometric) and #17 (full-face photos) are explicitly **out of scope** for tabular Excel input and are called out in every report. This module plugs into the pipeline immediately after Phase 1 PII detection, before scoring, and appears as a separate **Compliance** section in the report — not as an eighth rubric dimension. It does **not** certify legal HIPAA compliance; it only flags the presence of PHI-like identifiers in structured data.

---

## 1. Core Design Principles (do not violate these when generating code)

1. **Reuse Phase 1 PII detection.** Call `detect_pii()` / `detect_pii_in_series()` and extend the analyzer registry — do not duplicate Presidio setup or overlap resolution logic.
2. **Mapping layer, not a second scanner.** The HIPAA module's primary job is `Presidio/custom type → HIPAA identifier # → confidence → counts`. Value-level scanning for HIPAA-only types (MRN, VIN, etc.) lives in new recognizers registered alongside existing ones in `detect_pii.py`.
3. **No check may crash the pipeline.** Every public function is wrapped so exceptions become `status="error"` results or a safe fallback `HipaaComplianceResult`, never an uncaught raise.
4. **PII is masked before it can appear anywhere.** Reuse `mask_pii.py` exclusively. Logs, reports, and `CheckResult.details` contain **counts and category labels only** — matching plan.md Section 4.7 rule: *"PII summary: counts only, never actual values."*
5. **No AI/LLM calls.** Deterministic, rule-based, explainable — same as Phase 1 and M3.
6. **Explicit scope limits in output.** #16 and #17 must appear in every compliance report as *"Not assessed — structured-data pipeline; no image/audio input."* Never silently omit them.
7. **Non-certification disclaimer.** Every report section must state that this scan detects PHI-like identifiers only and does **not** determine organizational HIPAA compliance, BAAs, encryption-at-rest, access controls, or breach notification obligations.

---

## 2. HIPAA Identifier Scope — Detectability in Tabular Excel

The 18 identifiers below follow the [HHS Safe Harbor de-identification standard](https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html). For each, we state whether it is realistically detectable in a **tabular Excel dataset** processed by this engine.

| # | HIPAA Identifier | Detectable in Excel? | Primary Detection Method | Notes |
|---|---|---|---|---|
| 1 | **Names** | ✅ Yes | Presidio `PERSON` → `TYPE_NAME` | NER + column hints (`patient_name`, `provider`) |
| 2 | **Geographic subdivisions smaller than state** | ✅ Partial | Presidio `LOCATION` + `TYPE_ADDRESS`, `TYPE_POSTAL_CODE` | Street/city/ZIP detectable; full address quality varies. State/country alone are **not** PHI — do not flag state-only columns. |
| 3 | **Dates tied to an individual** (birth, admission, discharge, death; ages >89) | ✅ Yes | Presidio `DATE_TIME` + `TYPE_DOB` + age>89 heuristic | Flag dates in columns named `dob`, `admission_date`, `discharge_date`, `death_date`, or any date column when combined with a name/MRN column on the same row (row-level link is out of scope — column-level only). Ages >89: numeric column `age` with value ≥90, or DOB implying age ≥90. |
| 4 | **Phone numbers** | ✅ Yes | Presidio `PHONE_NUMBER` + existing `TYPE_PHONE`/`TYPE_MOBILE` regex | Already in Phase 1. |
| 5 | **Fax numbers** | ✅ Yes | New `TYPE_FAX` regex (same shape as phone) + column hint `fax` | Distinguished from phone by column name or `fax` keyword in adjacent text. |
| 6 | **Email addresses** | ✅ Yes | Presidio `EMAIL_ADDRESS` → `TYPE_EMAIL` | Already in Phase 1. |
| 7 | **SSN** | ✅ Yes | Presidio `US_SSN` + existing `TYPE_SSN` regex | Already in Phase 1. |
| 8 | **Medical record numbers** | ✅ Yes (heuristic) | New `TYPE_MRN` regex + column hints | No universal MRN format — see Section 4.2. |
| 9 | **Health plan beneficiary numbers** | ✅ Yes (heuristic) | New `TYPE_BENEFICIARY_ID` regex + column hints | Often alphanumeric 8–14 chars; column name is primary signal. |
| 10 | **Account numbers** | ✅ Yes (heuristic) | Existing `TYPE_BANK_ACCOUNT`, `TYPE_IBAN`, `TYPE_CARD` + column hints | Credit-card-shaped values map here; context words or column name required to avoid false positives on arbitrary long integers. |
| 11 | **Certificate/license numbers** | ✅ Yes (heuristic) | Existing `TYPE_DRIVER_LICENSE`, `TYPE_PASSPORT` + new `TYPE_LICENSE_CERT` | Professional/medical license columns (`npi`, `dea`, `license_no`). |
| 12 | **Vehicle identifiers / serial numbers** | ✅ Partial | New `TYPE_VIN` regex (ISO 3779) + column hints | VIN is well-formed; other vehicle IDs are heuristic. |
| 13 | **Device identifiers / serial numbers** | ✅ Partial | New `TYPE_DEVICE_SERIAL` regex + column hints | Implants, pumps, monitors — format varies widely; column name is primary signal. |
| 14 | **URLs** | ✅ Yes | Existing `TYPE_URL` regex | Already in Phase 1. |
| 15 | **IP addresses** | ✅ Yes | Existing `TYPE_IP_ADDRESS` regex | Already in Phase 1. |
| 16 | **Biometric identifiers** | ❌ **Out of scope** | — | Fingerprints, retinal scans, voiceprints require image/audio/binary input. **Must appear in report as "Not assessed."** |
| 17 | **Full-face photographs** | ❌ **Out of scope** | — | No image columns in Excel pipeline. **Must appear in report as "Not assessed."** |
| 18 | **Any other unique identifying number, characteristic, or code** | ✅ Partial | High-cardinality ID heuristic + `TYPE_CNIC` + generic `TYPE_UNIQUE_ID` | Catch-all for patient IDs, claim numbers, order IDs when column name + cardinality suggest identifier. |

**Dataset scope verdict:**
- `NO_PHI_DETECTED` — zero hits across all assessable identifiers (#1–15, #18).
- `PHI_DETECTED` — one or more hits in any assessable identifier.
- `PARTIAL_SCOPE` — assessable identifiers scanned, but #16 and/or #17 cannot be evaluated (always true for Excel — use this status when **any** PHI is found **or** as the default scope label when reporting, since #16/#17 are never assessable). Implementation rule: **`PARTIAL_SCOPE` is always appended to the scope note** because the pipeline inherently cannot assess #16/#17; use `PHI_DETECTED` / `NO_PHI_DETECTED` as the primary posture for assessable identifiers.

---

## 3. Project Structure

```
data_quality_engine/
├── engine/
│   └── pii/
│       ├── detect_pii.py          # EXTEND: register HIPAA-specific recognizers
│       └── mask_pii.py            # REUSE unchanged (add tokens for new TYPE_*)
└── phase2/
    └── compliance/
        ├── __init__.py
        ├── hipaa_identifiers.py   # enum + Presidio/custom → HIPAA # mapping
        ├── recognizers.py         # Pattern definitions for new TYPE_* (also imported by detect_pii)
        ├── mapper.py              # map_pii_hits_to_hipaa()
        ├── assessor.py            # assess_hipaa_compliance() — main entry
        └── scorer.py              # score_hipaa_compliance() → HipaaComplianceResult
```

**Rule for AI code generation:** Phase 2 compliance code lives under `phase2/compliance/` and **calls into** `engine/pii/` — never the reverse. New recognizers are defined in `phase2/compliance/recognizers.py` but **registered** from `detect_pii.py` via a single import to keep one analyzer instance.

---

## 4. Module-by-Module Specification

### 4.1 HIPAA Identifier Registry (`phase2/compliance/hipaa_identifiers.py`)

```python
from enum import Enum

class HipaaIdentifier(str, Enum):
    """Official HHS Safe Harbor identifiers 1–18."""
    NAMES = "hipaa_01_names"
    GEO_SUBSTATE = "hipaa_02_geo_substate"
    DATES = "hipaa_03_dates"
    PHONE = "hipaa_04_phone"
    FAX = "hipaa_05_fax"
    EMAIL = "hipaa_06_email"
    SSN = "hipaa_07_ssn"
    MEDICAL_RECORD = "hipaa_08_medical_record"
    HEALTH_PLAN_BENEFICIARY = "hipaa_09_health_plan_beneficiary"
    ACCOUNT_NUMBER = "hipaa_10_account_number"
    LICENSE_CERTIFICATE = "hipaa_11_license_certificate"
    VEHICLE_IDENTIFIER = "hipaa_12_vehicle_identifier"
    DEVICE_IDENTIFIER = "hipaa_13_device_identifier"
    URL = "hipaa_14_url"
    IP_ADDRESS = "hipaa_15_ip_address"
    BIOMETRIC = "hipaa_16_biometric"           # out of scope — report only
    FULL_FACE_PHOTO = "hipaa_17_full_face_photo"  # out of scope — report only
    OTHER_UNIQUE_ID = "hipaa_18_other_unique_id"

ASSESSABLE_IDENTIFIERS: frozenset[HipaaIdentifier]  # all except BIOMETRIC, FULL_FACE_PHOTO
OUT_OF_SCOPE_IDENTIFIERS: frozenset[HipaaIdentifier] = frozenset({
    HipaaIdentifier.BIOMETRIC,
    HipaaIdentifier.FULL_FACE_PHOTO,
})

# Maps internal detect_pii TYPE_* labels → one or more HipaaIdentifier values.
# Many-to-many allowed (e.g. TYPE_CARD → ACCOUNT_NUMBER).
PII_TYPE_TO_HIPAA: dict[str, list[HipaaIdentifier]]
```

**`PII_TYPE_TO_HIPAA` (canonical mapping):**

| Internal `TYPE_*` | HIPAA Identifier(s) |
|---|---|
| `NAME` | `hipaa_01_names` |
| `ADDRESS`, `POSTAL_CODE` | `hipaa_02_geo_substate` |
| `DOB`, `DATE_TIME`* | `hipaa_03_dates` |
| `PHONE`, `MOBILE` | `hipaa_04_phone` |
| `FAX` | `hipaa_05_fax` |
| `EMAIL` | `hipaa_06_email` |
| `SSN` | `hipaa_07_ssn` |
| `MRN` | `hipaa_08_medical_record` |
| `BENEFICIARY_ID` | `hipaa_09_health_plan_beneficiary` |
| `BANK_ACCOUNT`, `IBAN`, `CARD` | `hipaa_10_account_number` |
| `DRIVER_LICENSE`, `PASSPORT`, `LICENSE_CERT` | `hipaa_11_license_certificate` |
| `VIN` | `hipaa_12_vehicle_identifier` |
| `DEVICE_SERIAL` | `hipaa_13_device_identifier` |
| `URL` | `hipaa_14_url` |
| `IP_ADDRESS` | `hipaa_15_ip_address` |
| `CNIC`, `UNIQUE_ID` | `hipaa_18_other_unique_id` |

\* `DATE_TIME` from Presidio maps to `hipaa_03_dates` **only** when column hint confirms individual-linked date (`dob`, `birth`, `admission`, `discharge`, `death`, `service_date`) or `TYPE_DOB` context window matched. Otherwise ignore generic date columns (e.g. `order_date` on a product catalog sheet) unless `allowed_types` from column classifier includes date-of-person.

---

### 4.2 New Custom Recognizers (`phase2/compliance/recognizers.py`)

These extend Phase 1 — register as Presidio `PatternRecognizer` instances **and** as regex fallbacks in `_regex_hits()` (same pattern as CNIC). Add corresponding `TYPE_*` constants to `detect_pii.py`.

```python
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class HipaaRecognizerSpec:
    pii_type: str           # e.g. "MRN"
    patterns: list[tuple[str, str, float]]  # (name, regex, score)
    column_hints: list[str] # lowercase substrings

# --- Realistic patterns (heuristic — no universal standard) ---

MRN_SPEC = HipaaRecognizerSpec(
    pii_type="MRN",
    patterns=[
        ("mrn_numeric", r"\b\d{6,10}\b", 0.55),           # weak alone; needs column hint
        ("mrn_alphanum", r"\b[A-Z]{0,3}\d{6,12}\b", 0.65),
        ("mrn_mixed", r"\b[A-Z0-9]{2,4}-?[A-Z0-9]{4,10}\b", 0.60),
    ],
    column_hints=[
        "mrn", "medical_record", "med_rec", "patient_id", "patient_number",
        "chart_number", "chart_no", "emr_id", "ehr_id", "hospital_number",
    ],
)

BENEFICIARY_SPEC = HipaaRecognizerSpec(
    pii_type="BENEFICIARY_ID",
    patterns=[
        ("beneficiary", r"\b[A-Z0-9]{8,14}\b", 0.55),
        ("medicare_hicn", r"\b\d{3}-?\d{2}-?\d{4}[A-Z0-9]?\b", 0.70),  # legacy HICN shape
    ],
    column_hints=[
        "beneficiary", "member_id", "subscriber_id", "policy_id", "insurance_id",
        "health_plan", "plan_id", "medicaid_id", "medicare", "hicn", "mbi",
    ],
)

FAX_SPEC = HipaaRecognizerSpec(
    pii_type="FAX",
    patterns=[
        ("fax_us", r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", 0.80),
    ],
    column_hints=["fax", "facsimile"],
)

VIN_SPEC = HipaaRecognizerSpec(
    pii_type="VIN",
    patterns=[
        # ISO 3779: 17 chars, no I/O/Q
        ("vin", r"\b[A-HJ-NPR-Z0-9]{17}\b", 0.92),
    ],
    column_hints=["vin", "vehicle_id", "vehicle_ident", "chassis"],
)

DEVICE_SERIAL_SPEC = HipaaRecognizerSpec(
    pii_type="DEVICE_SERIAL",
    patterns=[
        ("device_serial", r"\b[A-Z0-9]{8,20}\b", 0.50),   # weak alone
        ("udi", r"\b\(01\)\d{14}\b", 0.85),              # GS1 UDI (partial)
    ],
    column_hints=[
        "serial", "device_id", "device_serial", "implant", "udi", "lot_number",
        "model_serial", "equipment_id",
    ],
)

LICENSE_CERT_SPEC = HipaaRecognizerSpec(
    pii_type="LICENSE_CERT",
    patterns=[
        ("npi", r"\b\d{10}\b", 0.88),                     # NPI is exactly 10 digits
        ("dea", r"\b[A-Z]{2}\d{7}\b", 0.90),
        ("generic_license", r"\b[A-Z0-9-]{5,15}\b", 0.50),
    ],
    column_hints=[
        "npi", "dea", "license", "licence", "cert", "certificate", "credential",
        "provider_id", "physician_id",
    ],
)

UNIQUE_ID_SPEC = HipaaRecognizerSpec(
    pii_type="UNIQUE_ID",
    patterns=[
        ("uuid", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", 0.95),
    ],
    column_hints=[
        "uuid", "guid", "unique_id", "record_id", "claim_id", "encounter_id",
    ],
)

ALL_HIPAA_SPECS: list[HipaaRecognizerSpec]  # ordered list of all specs above
```

**Column-header confidence boost:** When a regex hit's score < 0.75 **and** the column name matches a spec's `column_hints`, boost effective score by +0.25 (cap at 0.99). When column hint matches but **no** regex hit, and the column has high cardinality (`unique_count / row_count > 0.90`) and name matches `MRN`, `BENEFICIARY`, or `UNIQUE_ID` hints, emit a **column-level-only** flag with `issues_found = row_count` and `detection_method = "column_header_heuristic"` — do not iterate cell values into logs.

**Age >89 rule (`hipaa_03_dates` extension):**

```python
def detect_age_over_89(series: "pandas.Series", column_name: str | None) -> int:
    """
    Returns count of rows where numeric age >= 90.
    Only runs when column_name hints contain: age, patient_age, member_age.
    Never logs the actual age values — count only.
    """
```

---

### 4.3 Extend Phase 1 PII Detection (`engine/pii/detect_pii.py`)

**Changes (minimal diff):**

1. Import `ALL_HIPAA_SPECS` from `phase2/compliance/recognizers.py` (lazy import to avoid circular deps at module load — or duplicate pattern constants in `detect_pii.py` and treat `recognizers.py` as the spec source of truth).
2. Add `TYPE_MRN`, `TYPE_BENEFICIARY_ID`, `TYPE_FAX`, `TYPE_VIN`, `TYPE_DEVICE_SERIAL`, `TYPE_LICENSE_CERT`, `TYPE_UNIQUE_ID` constants.
3. Extend `_infer_expected_types()` with HIPAA column hints from each spec.
4. Register new Presidio `PatternRecognizer` instances in `_presidio_analyzer()`.
5. Extend `_regex_hits()` with the same patterns, gated by column hints when scanning via `detect_pii(text, allowed_types=...)`.
6. Extend `_PRESIDIO_TYPE_MAP`:

```python
_PRESIDIO_TYPE_MAP = {
    "PERSON": TYPE_NAME,
    "PHONE_NUMBER": TYPE_PHONE,
    "EMAIL_ADDRESS": TYPE_EMAIL,
    "CREDIT_CARD": TYPE_CARD,
    "US_SSN": TYPE_SSN,
    "LOCATION": TYPE_ADDRESS,
    "DATE_TIME": TYPE_DOB,       # mapped to DOB type; HIPAA layer disambiguates
    "URL": TYPE_URL,
    "IP_ADDRESS": TYPE_IP_ADDRESS,
    "CNIC": TYPE_CNIC,
    # new:
    "MRN": TYPE_MRN,
    "BENEFICIARY_ID": TYPE_BENEFICIARY_ID,
    "FAX": TYPE_FAX,
    "VIN": TYPE_VIN,
    "DEVICE_SERIAL": TYPE_DEVICE_SERIAL,
    "LICENSE_CERT": TYPE_LICENSE_CERT,
}
```

**`detect_pii_in_series()` contract unchanged.** It already returns counts-only summaries with masked row values — the HIPAA layer consumes this shape.

---

### 4.4 Mapping Layer (`phase2/compliance/mapper.py`)

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class HipaaHit:
    hipaa_id: str               # HipaaIdentifier value
    source_type: str            # internal TYPE_* that produced the hit
    count: int                  # aggregated count (never the raw value)
    confidence: float           # 0.0–1.0 after column-hint boost
    detection_method: str       # "presidio" | "regex" | "column_header_heuristic" | "age_heuristic"

@dataclass
class ColumnHipaaSummary:
    column: str
    hits_by_identifier: dict[str, list[HipaaHit]]  # hipaa_id -> hits
    total_phi_rows: int         # rows with at least one HIPAA-mapped hit in this column

def map_pii_summary_to_hipaa(
    pii_summary: dict[str, Any],
    *,
    column_name: str,
    row_count: int,
) -> ColumnHipaaSummary:
    """
    Map one detect_pii_in_series() summary to HIPAA identifiers.

    Input pii_summary shape (from detect_pii_in_series):
        {
            "column": str,
            "rows_with_pii": int,
            "type_counts": {"EMAIL": 12, "PHONE": 8, ...},
            "masked_rows": {...},   # ignored here — never forwarded
            "allowed_types": [...],
        }

    Rules:
    - Increment count from type_counts via PII_TYPE_TO_HIPAA.
    - Apply column-hint boost / column-only heuristic per Section 4.2.
    - Run detect_age_over_89 when column hints match.
    - Never include masked_rows or raw values in output.
    - Never raises — bad input returns empty ColumnHipaaSummary.
    """

def map_dataframe_pii_to_hipaa(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
) -> dict[str, ColumnHipaaSummary]:
    """Map every column's PII summary. Never raises."""
```

---

### 4.5 Compliance Assessor (`phase2/compliance/assessor.py`)

Produces `CheckResult`-compatible output for pipeline uniformity **and** a richer aggregate object for reporting.

```python
from dataclasses import dataclass, field
from typing import Any

from data_quality_engine.engine.models import CheckResult

@dataclass
class HipaaComplianceResult:
    """
    Aggregate compliance posture — mirrors M3 ReadinessScore pattern.
    Never contains raw PHI values.
    """
    status: str
    # "PHI_DETECTED"     — one or more assessable identifiers found
    # "NO_PHI_DETECTED"  — zero assessable identifier hits
    # "error"            — assessment failed; see blockers

    scope: str
    # Always "PARTIAL_SCOPE" for Excel pipeline (cannot assess #16, #17).
    # Report text must explain this explicitly.

    identifier_counts: dict[str, int]
    # hipaa_id -> total count across all columns (e.g. {"hipaa_06_email": 42})

    counts_by_column: dict[str, dict[str, int]]
    # column -> {hipaa_id: count}

    columns_with_phi: list[str]
    identifiers_found: list[str]   # hipaa_ids with count > 0
    identifiers_not_assessed: list[str]  # always includes hipaa_16, hipaa_17

    detection_methods: dict[str, str]
    # hipaa_id -> primary method used ("presidio", "regex", "column_header_heuristic")

    blockers: list[str] = field(default_factory=list)   # assessment failures only
    warnings: list[str] = field(default_factory=list)   # e.g. "MRN detected via weak heuristic"
    disclaimer: str = (
        "This scan detects the presence of PHI-like identifiers in structured "
        "data only. It does NOT determine whether your organization is HIPAA "
        "compliant, whether a BAA is in place, or whether applicable "
        "safeguards (encryption, access controls, audit logging) are satisfied."
    )

def assess_hipaa_compliance(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
    *,
    run_id: str | None = None,
) -> HipaaComplianceResult:
    """
    Main entry point. Maps Phase 1 PII summaries to HIPAA identifiers
    and computes dataset-level posture.

    Never raises. Never logs raw PHI. Uses structured JSON logging when
    run_id is provided:
        {"step": "hipaa_compliance", "level": "INFO",
         "message": "HIPAA assessment complete",
         "details": {"status": "PHI_DETECTED", "identifiers_found": [...],
                     "total_hits": 123}}   # counts only

    Fault tolerance: any exception -> HipaaComplianceResult(status="error",
        blockers=["HIPAA assessment failed: <msg>"])
    """

def assess_hipaa_compliance_as_check_results(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
    *,
    run_id: str | None = None,
) -> list[CheckResult]:
    """
    CheckResult-compatible wrapper for pipeline/scoring consumers.

    Returns:
    - One file-level CheckResult (column=None) with overall posture.
    - One CheckResult per column that has any HIPAA-mapped hits.

    CheckResult fields:
        check_name = "hipaa_phi"
        status = "failed" if PHI detected in that column else "passed"
               (file-level: "failed" if PHI_DETECTED, "passed" if NO_PHI_DETECTED,
                "error" on failure)
        column = column name or None for file-level
        issues_found = total HIPAA hit count for that scope
        dimension = ""          # intentionally empty — NOT a rubric dimension
        quality_ratio = None
        details = {
            "hipaa_status": "PHI_DETECTED" | "NO_PHI_DETECTED" | "error",
            "scope": "PARTIAL_SCOPE",
            "identifier_counts": {"hipaa_06_email": 12, ...},  # counts only
            "identifiers_not_assessed": ["hipaa_16_biometric", "hipaa_17_full_face_photo"],
            "detection_methods": {"hipaa_08_medical_record": "column_header_heuristic"},
            "disclaimer": "<non-certification text>",
            # per-column results also include:
            # "identifiers": {"hipaa_04_phone": 8, ...}
        }

    Never puts raw values, masked values, or sample strings in details.
    """
```

---

### 4.6 Compliance Scorer (`phase2/compliance/scorer.py`)

Optional numeric summary for dashboard/API — **not** folded into the 8-dimension data-quality composite (same rule as PII privacy risk in `scoring.py`).

```python
@dataclass
class HipaaComplianceScore:
    exposure_score: float    # 0–100; higher = more PHI exposure (inverse of "cleanliness")
    identifiers_detected: int
    columns_affected: int
    severity: str            # "none" | "low" | "medium" | "high"
    # none:   NO_PHI_DETECTED
    # low:    1 identifier type, 1 column
    # medium: 2–3 identifier types OR any SSN/MRN/biometric-class hits
    # high:   4+ identifier types OR SSN + name + date combo across columns

def score_hipaa_compliance(result: HipaaComplianceResult) -> HipaaComplianceScore:
    """
    Derive a single exposure score from HipaaComplianceResult.
    Never raises.

    exposure_score formula (deterministic):
        base = min(100, 10 * len(result.identifiers_found))
        column_factor = min(30, 3 * len(result.columns_with_phi))
        volume_factor = min(40, log10(max(total_hits, 1)) * 15)
        sensitive_bonus = 20 if any id in result.identifiers_found
                            for id in (SSN, MEDICAL_RECORD, HEALTH_PLAN_BENEFICIARY))
                            else 0
        exposure_score = min(100, base + column_factor + volume_factor + sensitive_bonus)
    """
```

---

### 4.7 Masking Behavior (`engine/pii/mask_pii.py`)

**No new masking logic required.** Extend `_token_for()` / `_FULL_ALWAYS` if new types need full redaction:

| New `TYPE_*` | Mask mode |
|---|---|
| `MRN`, `BENEFICIARY_ID`, `UNIQUE_ID` | partial (last 4) |
| `FAX` | partial (same as phone) |
| `VIN` | partial (last 4) |
| `DEVICE_SERIAL`, `LICENSE_CERT` | partial (last 3) |

**Pipeline rule (unchanged from plan.md Section 4.5):** `mask_pii()` runs inside `detect_pii_in_series()` before any value is stored. The HIPAA module receives **only** the counts dict from summaries — never re-expands `masked_rows` for reporting.

---

## 5. Pipeline Integration

### 5.1 Execution Order

Insert **after Phase 1 PII detection, before scoring and report generation**:

```
1. Ingestion + header confirmation
2. Column classification
3. Phase 1 quality checks (missing, duplicates, outliers, ...)
4. PII detection: pii_summary_by_column = {col: detect_pii_in_series(df[col])}
   └─ masking happens inside detect_pii_in_series — no raw PHI beyond this point
5. ★ HIPAA compliance assessment (NEW)
   └─ hipaa_result = assess_hipaa_compliance(pii_summary_by_column, len(df))
   └─ hipaa_checks = assess_hipaa_compliance_as_check_results(...)
6. Fuzzy standardization (if enabled)
7. Data quality scoring (unchanged — HIPAA not in rubric)
8. Report generation (Phase 1 + Compliance section + optional M2/M3 sections)
```

**Orchestrator touchpoints:**

| File | Change |
|---|---|
| `main.py` | After Task 4 PII block, call `assess_hipaa_compliance_as_check_results`; pass results to report builder. |
| `generate_report_phase2.py` | Same hook after `pii_summary_by_column` built. |
| `phase2/api/workers.py` | Add stage `'hipaa compliance'` at ~25% progress between PII masking and quality checks. |

### 5.2 Scoring Model — PII in Composite; HIPAA Exposure Ceiling

**PII (`privacy_sensitivity`)** is a weighted rubric dimension (default weight **10%**).
Task 4 summaries are auto-materialized into `privacy_sensitivity` CheckResults inside
`compute_data_quality_score()` when `pii_summary_by_column` is supplied.

**HIPAA (M9)** remains a **separate exposure score** (not a rubric dimension), per the
original plan — but it **does affect the headline composite** via a **proportional
ceiling** so elevated PHI exposure cannot coexist with a score of 100:

`cap = min(proportional_cap, severity_floor)`

| Component | Formula |
|---|---|
| proportional_cap | `100 - (exposure_score/100) * (100 - 59)` |
| severity_floor | high → 70, medium → 74, low → 89 |

The **stricter** (lower) value wins. High-volume exposure keeps the proportional
cap; moderate exposure with sensitive identifiers uses the severity floor.

**Critical dimension labels** (≥50% of a dimension's checks failed — same threshold
as `report_generator._severity_from_ratio`) are shown in reports for visibility but
**do not** apply a flat composite cap. PII prevalence is reflected proportionally
via the weighted `privacy_sensitivity` dimension (e.g. 1/10 PII columns ≈ 90% dim
score vs 8/10 ≈ 20%).

| Layer | Where it appears | In weighted rubric? | Affects headline score? |
|---|---|---|---|
| Phase 1 PII (`detect_pii`) | Report → "Sensitive Data Assessment" | Yes → `privacy_sensitivity` | Yes |
| HIPAA PHI (M9) | Report → "HIPAA PHI Compliance Scan" | No (separate exposure score) | Yes (via ceiling) |
| M3 ML Readiness | Report → "ML Model Readiness Assessment" | No | No |

`CheckResult.dimension` for `check_name="hipaa_phi"` remains `""`. HIPAA CheckResults
are **not** passed into `compute_data_quality_score()` as a rubric dimension — only
the `HipaaComplianceScore` exposure object is passed for ceiling logic.

### 5.3 Report Section (template text — no AI)

Add to `report_generator.py` / `html_report.py` / `pdf_report.py`:

```
HIPAA PHI Compliance Scan

Scope: PARTIAL_SCOPE — Identifiers #16 (biometric) and #17 (full-face
photographs) cannot be assessed in structured Excel/CSV data. This
pipeline accepts no image or audio input.

Posture: [PHI_DETECTED | NO_PHI_DETECTED]
Exposure Score: [0–100] ([none | low | medium | high])

Identifiers Found (counts only):
  #1  Names ............................ [count]  ([columns])
  #2  Geographic (sub-state) ........... [count]
  ...
  #16 Biometric ........................ NOT ASSESSED
  #17 Full-face photos ................. NOT ASSESSED
  #18 Other unique IDs ................. [count]

Columns Affected: [n] / [total]

⚠ Disclaimer: This scan detects PHI-like identifiers in the dataset.
It does NOT certify HIPAA compliance for your organization, policies,
or technical safeguards.

Recommended Actions (rule-based, not AI):
  - If SSN or MRN detected: restrict file access; mask before sharing.
  - If NO_PHI_DETECTED: still verify scope limits (#16, #17 not assessed).
```

---

## 6. Logging

```python
# Example JSONL line — counts and categories only
{
    "timestamp": "2026-08-11T01:00:00Z",
    "run_id": "abc-123",
    "step": "hipaa_compliance",
    "level": "INFO",
    "message": "HIPAA assessment complete",
    "details": {
        "status": "PHI_DETECTED",
        "scope": "PARTIAL_SCOPE",
        "identifiers_found": ["hipaa_01_names", "hipaa_07_ssn"],
        "identifier_counts": {"hipaa_01_names": 150, "hipaa_07_ssn": 12},
        "columns_with_phi": 3
    }
}
```

**Forbidden in logs:** `value`, `masked_rows`, sample strings, row indices paired with identifiable content.

---

## 7. Testing Plan (`tests/test_hipaa_compliance.py`)

Minimum 15 test cases:

```python
def test_map_email_to_hipaa_06():
    """TYPE_EMAIL counts map to hipaa_06_email."""

def test_mrn_column_hint_boosts_confidence():
    """Column named 'MRN' boosts weak numeric regex hits."""

def test_age_over_89_detected():
    """Numeric age column with value 90+ maps to hipaa_03_dates."""

def test_vin_regex():
    """Valid 17-char VIN maps to hipaa_12_vehicle_identifier."""

def test_biometric_always_not_assessed():
    """Result.identifiers_not_assessed always includes #16 and #17."""

def test_no_phi_clean_dataset():
    """Synthetic ERP data with no PHI -> NO_PHI_DETECTED."""

def test_phi_detected_mixed():
    """Dataset with name + SSN + email -> PHI_DETECTED with correct counts."""

def test_check_result_never_contains_raw_values():
    """assess_hipaa_compliance_as_check_results details have no value keys."""

def test_assessor_never_raises_on_empty_df():
    """Empty pii_summary -> NO_PHI_DETECTED, not exception."""

def test_assessor_never_raises_on_malformed_summary():
    """Malformed input -> status='error', blockers populated."""

def test_fax_distinguished_from_phone_by_column():
    """Fax number in 'fax_number' column maps to hipaa_05_fax, not phone."""

def test_disclaimer_present():
    """HipaaComplianceResult.disclaimer is non-empty."""

def test_exposure_score_severity_high_for_ssn_and_name():
    """SSN + NAME across columns -> severity 'high'."""

def test_out_of_scope_report_text():
    """Report builder includes 'NOT ASSESSED' for #16 and #17."""

def test_masking_unchanged_for_new_types():
    """MRN values masked via mask_pii before summary — raw never in hipaa output."""
```

Fixtures: extend `tests/fixtures/` with `hipaa_phi_sample.xlsx` containing **fake** names, MRNs, SSNs (synthetic only — e.g. SSN 900-00-0000 range reserved for testing).

---

## 8. Configuration (`config/base_rules.yaml` — optional overrides)

```yaml
hipaa_compliance:
  enabled: true
  # Minimum confidence to count a regex-only hit (before column boost)
  min_confidence: 0.55
  # Identifier types that trigger "high" severity on their own
  high_sensitivity_identifiers:
    - hipaa_07_ssn
    - hipaa_08_medical_record
    - hipaa_09_health_plan_beneficiary
  # Column hints merged with built-in lists (client-specific)
  extra_column_hints:
    hipaa_08_medical_record: ["site_patient_key"]
```

---

## 9. Explicit Non-Goals (do not implement)

1. **Legal HIPAA compliance certification.** This module does not evaluate policies, BAAs, risk assessments, training, breach notification, or administrative/physical/technical safeguard requirements.
2. **Biometric or photographic PHI detection (#16, #17).** No image parsing, no audio, no binary attachment scanning in Excel cells.
3. **Re-identification risk scoring / k-anonymity / l-diversity.** Counts only — no statistical disclosure control.
4. **Row-level linkage analysis.** We do not join name + date + ZIP on the same row to infer "this row is a re-identifiable person" — column-level detection only.
5. **Replacing Phase 1 PII check.** `detect_pii` remains the Sensitive Data Assessment; HIPAA is an additional compliance-oriented view.
6. **AI/LLM-generated compliance narratives.** Template text only.
7. **Storing raw or masked PHI in the database.** Run manifests store counts and identifier category labels only.

---

## 10. Suggested Build Order

1. `phase2/compliance/hipaa_identifiers.py` — enum + mapping table  
2. `phase2/compliance/recognizers.py` — pattern specs  
3. Extend `engine/pii/detect_pii.py` — new TYPE_* constants + recognizers + column hints  
4. Extend `engine/pii/mask_pii.py` — tokens for new types  
5. `phase2/compliance/mapper.py` — mapping unit tests  
6. `phase2/compliance/assessor.py` + `scorer.py`  
7. Wire into `main.py` / `generate_report_phase2.py` ✅ (`main.py` done — `_print_hipaa_compliance_results` in `run_pipeline`)  
8. Report templates (HTML/PDF Compliance section)  
9. `tests/test_hipaa_compliance.py` — full suite ✅ (15/15 passing)  
10. End-to-end run on `hipaa_phi_sample.xlsx` ✅ (`tests/fixtures/hipaa_phi_sample.xlsx`)

---

## 11. Exit Criteria

- [x] All 16 assessable identifiers mapped from Presidio/custom types  
- [x] #16 and #17 explicitly reported as "Not assessed" in every output format  
- [x] Non-certification disclaimer in every report  
- [x] `assess_hipaa_compliance_as_check_results()` returns valid `CheckResult` list  
- [x] No raw PHI in logs, details, or reports (counts only)  
- [x] Pipeline continues on assessment failure (`status="error"`)  
- [x] 15+ tests passing  
- [x] No separate HIPAA rubric dimension; exposure score separate with composite ceiling  
- [x] Phase 1 `detect_pii` overlap resolution preserved — no garble regression  
- [x] Wired into `main.py` `run_pipeline` after PII detection, before scoring  

---

*This plan is Phase 2 Module 9. It builds on Phase 1 Section 4.5 (PII) and follows the same architectural conventions as M3 ML Readiness (`phase2/readiness/`). It intentionally does not modify the Phase 1 scoring rubric.*
