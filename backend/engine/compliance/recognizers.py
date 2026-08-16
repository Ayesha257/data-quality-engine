"""
Phase 2 M9 — HIPAA-specific recognizer specs and helpers (PHASE2_HIPAA_PHI_PLAN.md §4.2).

Pattern definitions for MRN, beneficiary ID, fax, VIN, device serial, and
license numbers. Registered into Phase 1 detect_pii.py (plan §4.3) — this
module is the single source of truth for regex shapes and column hints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# PII type labels shared with engine/pii/detect_pii.py (must stay in sync).
TYPE_MRN = "MRN"
TYPE_BENEFICIARY_ID = "BENEFICIARY_ID"
TYPE_FAX = "FAX"
TYPE_VIN = "VIN"
TYPE_DEVICE_SERIAL = "DEVICE_SERIAL"
TYPE_LICENSE_CERT = "LICENSE_CERT"
TYPE_UNIQUE_ID = "UNIQUE_ID"

# Column-hint boost: weak regex hits become actionable when the column name
# confirms intent (plan §4.2 — +0.25 capped at 0.99).
COLUMN_HINT_BOOST = 0.25
COLUMN_HINT_BOOST_THRESHOLD = 0.75

# High-cardinality column-only flag when >90% unique values and name matches
# MRN / beneficiary / unique-id hints (plan §4.2).
HIGH_CARDINALITY_RATIO = 0.90

# Age >= 90 is PHI under Safe Harbor item #3 (ages over 89).
AGE_PHI_THRESHOLD = 90

_AGE_COLUMN_HINTS = ("age", "patient_age", "member_age")

# ISO 3779 transliteration (I, O, Q excluded from VIN alphabet).
_VIN_TRANSLITERATION: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}
# Position weights for the VIN check-digit (position 9, index 8).
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)


@dataclass(frozen=True)
class HipaaRecognizerSpec:
    pii_type: str
    patterns: tuple[tuple[str, str, float], ...]  # (name, regex, score)
    column_hints: tuple[str, ...]


MRN_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_MRN,
    patterns=(
        ("mrn_numeric", r"\b\d{6,10}\b", 0.55),
        ("mrn_alphanum", r"\b[A-Z]{0,3}\d{6,12}\b", 0.65),
        ("mrn_mixed", r"\b[A-Z0-9]{2,4}-?[A-Z0-9]{4,10}\b", 0.60),
    ),
    column_hints=(
        "mrn",
        "medical_record",
        "med_rec",
        "patient_id",
        "patient_number",
        "chart_number",
        "chart_no",
        "emr_id",
        "ehr_id",
        "hospital_number",
    ),
)

BENEFICIARY_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_BENEFICIARY_ID,
    patterns=(
        ("beneficiary", r"\b[A-Z0-9]{8,14}\b", 0.55),
        ("medicare_hicn", r"\b\d{3}-?\d{2}-?\d{4}[A-Z0-9]?\b", 0.70),
    ),
    column_hints=(
        "beneficiary",
        "member_id",
        "subscriber_id",
        "policy_id",
        "insurance_id",
        "health_plan",
        "plan_id",
        "medicaid_id",
        "medicare",
        "hicn",
        "mbi",
    ),
)

FAX_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_FAX,
    patterns=(
        (
            "fax_us",
            r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)",
            0.80,
        ),
    ),
    column_hints=("fax", "facsimile"),
)

VIN_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_VIN,
    patterns=(("vin", r"\b[A-HJ-NPR-Z0-9]{17}\b", 0.92),),
    column_hints=("vin", "vehicle_id", "vehicle_ident", "chassis"),
)

DEVICE_SERIAL_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_DEVICE_SERIAL,
    patterns=(
        ("device_serial", r"\b[A-Z0-9]{8,20}\b", 0.50),
        ("udi", r"\b\(01\)\d{14}\b", 0.85),
    ),
    column_hints=(
        "serial",
        "device_id",
        "device_serial",
        "implant",
        "udi",
        "lot_number",
        "model_serial",
        "equipment_id",
    ),
)

LICENSE_CERT_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_LICENSE_CERT,
    patterns=(
        ("npi", r"\b\d{10}\b", 0.88),
        ("dea", r"\b[A-Z]{2}\d{7}\b", 0.90),
        ("generic_license", r"\b[A-Z0-9-]{5,15}\b", 0.50),
    ),
    column_hints=(
        "npi",
        "dea",
        "license",
        "licence",
        "cert",
        "certificate",
        "credential",
        "provider_id",
        "physician_id",
    ),
)

UNIQUE_ID_SPEC = HipaaRecognizerSpec(
    pii_type=TYPE_UNIQUE_ID,
    patterns=(
        (
            "uuid",
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            0.95,
        ),
    ),
    column_hints=(
        "uuid",
        "guid",
        "unique_id",
        "record_id",
        "claim_id",
        "encounter_id",
    ),
)

ALL_HIPAA_SPECS: tuple[HipaaRecognizerSpec, ...] = (
    MRN_SPEC,
    BENEFICIARY_SPEC,
    FAX_SPEC,
    VIN_SPEC,
    DEVICE_SERIAL_SPEC,
    LICENSE_CERT_SPEC,
    UNIQUE_ID_SPEC,
)

# Specs eligible for column-only high-cardinality heuristic (plan §4.2).
_COLUMN_ONLY_SPECS: tuple[HipaaRecognizerSpec, ...] = (
    MRN_SPEC,
    BENEFICIARY_SPEC,
    UNIQUE_ID_SPEC,
)

_COMPILED: dict[str, list[tuple[re.Pattern[str], float]]] | None = None


def _compiled_patterns() -> dict[str, list[tuple[re.Pattern[str], float]]]:
    global _COMPILED
    if _COMPILED is None:
        compiled: dict[str, list[tuple[re.Pattern[str], float]]] = {}
        for spec in ALL_HIPAA_SPECS:
            compiled[spec.pii_type] = [
                (re.compile(pat, re.I), score) for _, pat, score in spec.patterns
            ]
        _COMPILED = compiled
    return _COMPILED


def column_name_matches_hints(column_name: str | None, hints: tuple[str, ...]) -> bool:
    if not column_name:
        return False
    lowered = str(column_name).strip().lower().replace("_", " ")
    return any(hint in lowered for hint in hints)


def apply_column_hint_boost(score: float, column_name: str | None, spec: HipaaRecognizerSpec) -> float:
    """Boost weak regex confidence when the column header confirms intent."""
    if score >= COLUMN_HINT_BOOST_THRESHOLD:
        return score
    if column_name_matches_hints(column_name, spec.column_hints):
        return min(0.99, score + COLUMN_HINT_BOOST)
    return score


def spec_for_pii_type(pii_type: str) -> HipaaRecognizerSpec | None:
    for spec in ALL_HIPAA_SPECS:
        if spec.pii_type == pii_type:
            return spec
    return None


def infer_hipaa_types_from_column(column_name: str | None) -> set[str]:
    """Column-header hints for Phase 1 _infer_expected_types extension."""
    hints: set[str] = set()
    for spec in ALL_HIPAA_SPECS:
        if column_name_matches_hints(column_name, spec.column_hints):
            hints.add(spec.pii_type)
    return hints


def vin_checksum_valid(vin: str) -> bool:
    """
    ISO 3779 check-digit validation for 17-character VINs.
    Rejects invalid check digits to reduce false positives on random alnum strings.
    """
    vin = vin.upper()
    if len(vin) != 17 or not _VIN_PATTERN.fullmatch(vin):
        return False
    total = 0
    for idx, char in enumerate(vin):
        value = _VIN_TRANSLITERATION.get(char)
        if value is None:
            return False
        total += value * _VIN_WEIGHTS[idx]
    remainder = total % 11
    check_char = "X" if remainder == 10 else str(remainder)
    return vin[8] == check_char


def detect_age_over_89(series, column_name: str | None) -> int:
    """
    Count rows where numeric age >= 90 (Safe Harbor #3).
    Only runs when column_name hints contain age / patient_age / member_age.
    Never logs actual age values — count only.
    """
    import pandas as pd

    if series is None or not column_name_matches_hints(column_name, _AGE_COLUMN_HINTS):
        return 0
    try:
        numeric = pd.to_numeric(series, errors="coerce")
    except Exception:
        return 0
    return int((numeric >= AGE_PHI_THRESHOLD).sum())


def should_apply_column_only_heuristic(
    column_name: str | None,
    row_count: int,
    unique_count: int,
    spec: HipaaRecognizerSpec,
) -> bool:
    """
    Column-only flag when header matches and >90% cardinality (plan §4.2).
    Used when regex finds nothing but the column shape suggests an identifier.
    """
    if row_count <= 0 or spec not in _COLUMN_ONLY_SPECS:
        return False
    if not column_name_matches_hints(column_name, spec.column_hints):
        return False
    return (unique_count / row_count) > HIGH_CARDINALITY_RATIO


def hipaa_regex_hits(
    text: str,
    allowed_types: set[str],
) -> list[dict[str, Any]]:
    """
    Value-level HIPAA regex hits gated by allowed_types from detect_pii_in_series.
    VIN matches require ISO 3779 checksum pass (plan §4.2).
    """
    if not text or not allowed_types:
        return []
    hits: list[dict[str, Any]] = []
    compiled = _compiled_patterns()
    for pii_type in allowed_types:
        patterns = compiled.get(pii_type)
        if not patterns:
            continue
        for pattern, score in patterns:
            for match in pattern.finditer(text):
                value = match.group()
                if pii_type == TYPE_VIN and not vin_checksum_valid(value):
                    continue
                hits.append(
                    {
                        "type": pii_type,
                        "start": match.start(),
                        "end": match.end(),
                        "value": value,
                        "score": score,
                    }
                )
    return hits


def register_hipaa_presidio_recognizers(analyzer: Any) -> None:
    """
    Register HIPAA PatternRecognizer instances on an existing Presidio analyzer.
    Called from detect_pii._presidio_analyzer() — one shared analyzer instance.
    """
    from presidio_analyzer import Pattern, PatternRecognizer

    for spec in ALL_HIPAA_SPECS:
        patterns = [Pattern(name, regex, score) for name, regex, score in spec.patterns]
        recognizer = PatternRecognizer(
            supported_entity=spec.pii_type,
            patterns=patterns,
            name=f"hipaa_{spec.pii_type.lower()}_recognizer",
        )
        analyzer.registry.add_recognizer(recognizer)
