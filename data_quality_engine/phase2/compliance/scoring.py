"""
Phase 2 M9 — HIPAA exposure scoring (PHASE2_HIPAA_PHI_PLAN.md §4.6).

Optional numeric summary for dashboard/API — NOT folded into the 8-dimension
data-quality composite (same rule as privacy risk in engine/scoring.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from data_quality_engine.phase2.compliance.identifiers import HIGH_SENSITIVITY_HIPAA_IDS
from data_quality_engine.phase2.compliance.scanner import HipaaComplianceResult


@dataclass
class HipaaComplianceScore:
    exposure_score: float  # 0–100; higher = more PHI exposure
    identifiers_detected: int
    columns_affected: int
    severity: str  # none | low | medium | high


def _has_sensitive_identifier(identifiers_found: list[str]) -> bool:
    return any(hipaa_id in HIGH_SENSITIVITY_HIPAA_IDS for hipaa_id in identifiers_found)


def _has_ssn_name_date_combo(result: HipaaComplianceResult) -> bool:
    found = set(result.identifiers_found)
    return (
        "hipaa_07_ssn" in found
        and "hipaa_01_names" in found
        and "hipaa_03_dates" in found
    )


def _derive_severity(result: HipaaComplianceResult) -> str:
    if result.status != "PHI_DETECTED":
        return "none"

    id_count = len(result.identifiers_found)
    col_count = len(result.columns_with_phi)

    if id_count >= 4 or _has_ssn_name_date_combo(result):
        return "high"
    if _has_sensitive_identifier(result.identifiers_found):
        return "medium"
    if id_count >= 2 or col_count >= 2:
        return "medium"
    if id_count == 1 and col_count == 1:
        return "low"
    return "low"


def score_hipaa_compliance(result: HipaaComplianceResult) -> HipaaComplianceScore:
    """
    Derive exposure score from HipaaComplianceResult. Never raises.

    exposure_score formula (PHASE2_HIPAA_PHI_PLAN.md §4.6):
        base = min(100, 10 * len(identifiers_found))
        column_factor = min(30, 3 * len(columns_with_phi))
        volume_factor = min(40, log10(max(total_hits, 1)) * 15)
        sensitive_bonus = 20 if SSN/MRN/beneficiary present else 0
    """
    try:
        if result.status != "PHI_DETECTED":
            return HipaaComplianceScore(
                exposure_score=0.0,
                identifiers_detected=0,
                columns_affected=0,
                severity="none",
            )

        identifiers_detected = len(result.identifiers_found)
        columns_affected = len(result.columns_with_phi)
        total_hits = sum(result.identifier_counts.values())

        base = min(100.0, 10.0 * identifiers_detected)
        column_factor = min(30.0, 3.0 * columns_affected)
        volume_factor = min(40.0, math.log10(max(total_hits, 1)) * 15.0)
        sensitive_bonus = 20.0 if _has_sensitive_identifier(result.identifiers_found) else 0.0

        exposure_score = round(
            min(100.0, base + column_factor + volume_factor + sensitive_bonus),
            2,
        )
        severity = _derive_severity(result)

        return HipaaComplianceScore(
            exposure_score=exposure_score,
            identifiers_detected=identifiers_detected,
            columns_affected=columns_affected,
            severity=severity,
        )
    except Exception:
        return HipaaComplianceScore(
            exposure_score=0.0,
            identifiers_detected=0,
            columns_affected=0,
            severity="none",
        )
