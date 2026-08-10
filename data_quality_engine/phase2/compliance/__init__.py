"""
Phase 2 — M9: HIPAA PHI Compliance Check (PHASE2_HIPAA_PHI_PLAN.md).

Mapping/scoring layer on top of Phase 1 detect_pii_in_series output.
Never re-scans raw cell values for reporting — counts and categories only.
"""

from __future__ import annotations

from data_quality_engine.phase2.compliance.compliance_status import (
    ColumnHipaaSummary,
    HipaaHit,
    map_pii_summary_to_hipaa,
)
from data_quality_engine.phase2.compliance.scanner import (
    HipaaComplianceResult,
    assess_hipaa_compliance,
    assess_hipaa_compliance_as_check_results,
)
from data_quality_engine.phase2.compliance.scoring import (
    HipaaComplianceScore,
    score_hipaa_compliance,
)

__all__ = [
    "HipaaHit",
    "ColumnHipaaSummary",
    "map_pii_summary_to_hipaa",
    "HipaaComplianceResult",
    "assess_hipaa_compliance",
    "assess_hipaa_compliance_as_check_results",
    "HipaaComplianceScore",
    "score_hipaa_compliance",
]
