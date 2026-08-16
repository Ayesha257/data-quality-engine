"""
Phase 2 M9 — HIPAA mapping and compliance status (PHASE2_HIPAA_PHI_PLAN.md §4.4).

Maps detect_pii_in_series() count summaries onto HHS identifiers.
Produces PHI_DETECTED / NO_PHI_DETECTED posture with fixed PARTIAL_SCOPE label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.engine.compliance.identifiers import (
    IDENTIFIERS_NOT_ASSESSED,
    NON_CERTIFICATION_DISCLAIMER,
    PARTIAL_SCOPE_REASON,
    SCOPE_LABEL,
    HipaaIdentifier,
    column_suggests_individual_date,
    hipaa_ids_for_pii_type,
)
from backend.engine.compliance.recognizers import (
    ALL_HIPAA_SPECS,
    TYPE_BENEFICIARY_ID,
    TYPE_MRN,
    TYPE_UNIQUE_ID,
    apply_column_hint_boost,
    column_name_matches_hints,
    should_apply_column_only_heuristic,
    spec_for_pii_type,
)

FORBIDDEN_DETAIL_KEYS = frozenset(
    {"value", "masked_rows", "raw", "sample", "masked_value", "values"}
)


@dataclass
class HipaaHit:
    hipaa_id: str
    source_type: str
    count: int
    confidence: float
    detection_method: str


@dataclass
class ColumnHipaaSummary:
    column: str
    hits_by_identifier: dict[str, list[HipaaHit]] = field(default_factory=dict)
    total_phi_rows: int = 0


def _default_confidence_for_type(pii_type: str) -> float:
    spec = spec_for_pii_type(pii_type)
    if spec and spec.patterns:
        return max(score for _, _, score in spec.patterns)
    # Presidio-backed / Phase 1 built-ins — treated as regex/presidio hybrid.
    return 0.85


def _detection_method_for_type(pii_type: str, confidence: float) -> str:
    if confidence >= 0.85:
        return "presidio" if pii_type in {"NAME", "EMAIL"} else "regex"
    if confidence >= 0.55:
        return "regex"
    return "column_header_heuristic"


def _should_map_dob_type(pii_type: str, column_name: str | None) -> bool:
    if pii_type == "DOB":
        return True
    # DATE_TIME mapped to DOB in Presidio layer — only count for individual dates.
    if pii_type == "DOB" or pii_type == "DATE_TIME":
        return column_suggests_individual_date(column_name)
    return True


def _add_hit(
    summary: ColumnHipaaSummary,
    hipaa_id: HipaaIdentifier,
    source_type: str,
    count: int,
    confidence: float,
    method: str,
) -> None:
    if count <= 0:
        return
    key = hipaa_id.value
    hit = HipaaHit(
        hipaa_id=key,
        source_type=source_type,
        count=count,
        confidence=confidence,
        detection_method=method,
    )
    summary.hits_by_identifier.setdefault(key, []).append(hit)


def map_pii_summary_to_hipaa(
    pii_summary: dict[str, Any],
    *,
    column_name: str,
    row_count: int,
) -> ColumnHipaaSummary:
    """
    Map one detect_pii_in_series() summary to HIPAA identifiers.

    Never includes masked_rows or raw values. Never raises — bad input
    returns an empty ColumnHipaaSummary.
    """
    try:
        if not isinstance(pii_summary, dict):
            return ColumnHipaaSummary(column=str(column_name or ""))

        col = str(pii_summary.get("column") or column_name or "")
        type_counts: dict[str, int] = dict(pii_summary.get("type_counts") or {})
        summary = ColumnHipaaSummary(column=col)
        summary.total_phi_rows = int(pii_summary.get("rows_with_pii") or 0)

        for pii_type, count in type_counts.items():
            if not _should_map_dob_type(pii_type, col):
                continue
            spec = spec_for_pii_type(pii_type)
            confidence = _default_confidence_for_type(pii_type)
            if spec:
                confidence = apply_column_hint_boost(confidence, col, spec)
            method = _detection_method_for_type(pii_type, confidence)
            for hipaa_id in hipaa_ids_for_pii_type(pii_type):
                _add_hit(summary, hipaa_id, pii_type, int(count), confidence, method)

        # Age >89 extension (count injected by detect_pii_in_series when applicable).
        age_count = int(pii_summary.get("age_over_89_count") or 0)
        if age_count > 0:
            _add_hit(
                summary,
                HipaaIdentifier.DATES,
                "AGE_OVER_89",
                age_count,
                0.90,
                "age_heuristic",
            )

        # Column-only high-cardinality heuristic when regex found nothing for MRN /
        # beneficiary / unique-id specs (plan §4.2).
        unique_count = int(pii_summary.get("unique_count") or 0)
        mapped_types = set(type_counts)
        if row_count > 0 and unique_count <= 0:
            unique_count = summary.total_phi_rows or row_count

        for spec in ALL_HIPAA_SPECS:
            if spec.pii_type in mapped_types:
                continue
            if not should_apply_column_only_heuristic(col, row_count, unique_count, spec):
                continue
            for hipaa_id in hipaa_ids_for_pii_type(spec.pii_type):
                _add_hit(
                    summary,
                    hipaa_id,
                    spec.pii_type,
                    row_count,
                    0.99,
                    "column_header_heuristic",
                )
            summary.total_phi_rows = max(summary.total_phi_rows, row_count)

        return summary
    except Exception:
        return ColumnHipaaSummary(column=str(column_name or ""))


def aggregate_identifier_counts(
    column_summaries: dict[str, ColumnHipaaSummary],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for col_summary in column_summaries.values():
        for hipaa_id, hits in col_summary.hits_by_identifier.items():
            totals[hipaa_id] = totals.get(hipaa_id, 0) + sum(h.count for h in hits)
    return totals


def counts_by_column(
    column_summaries: dict[str, ColumnHipaaSummary],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for col, col_summary in column_summaries.items():
        col_counts: dict[str, int] = {}
        for hipaa_id, hits in col_summary.hits_by_identifier.items():
            col_counts[hipaa_id] = sum(h.count for h in hits)
        if col_counts:
            out[col] = col_counts
    return out


def derive_compliance_status(identifier_counts: dict[str, int]) -> str:
    """PHI_DETECTED when any assessable identifier has hits; else NO_PHI_DETECTED."""
    assessable_hits = sum(
        count for hipaa_id, count in identifier_counts.items() if count > 0
    )
    return "PHI_DETECTED" if assessable_hits > 0 else "NO_PHI_DETECTED"


def build_detection_methods(
    column_summaries: dict[str, ColumnHipaaSummary],
) -> dict[str, str]:
    methods: dict[str, str] = {}
    for col_summary in column_summaries.values():
        for hipaa_id, hits in col_summary.hits_by_identifier.items():
            if hipaa_id in methods:
                continue
            if hits:
                methods[hipaa_id] = hits[0].detection_method
    return methods


def collect_warnings(column_summaries: dict[str, ColumnHipaaSummary]) -> list[str]:
    warnings: list[str] = []
    for col, col_summary in column_summaries.items():
        for hipaa_id, hits in col_summary.hits_by_identifier.items():
            for hit in hits:
                if hit.detection_method == "column_header_heuristic":
                    warnings.append(
                        f"{hipaa_id} flagged in column '{col}' via column-header "
                        "heuristic (high cardinality, weak or no regex match)."
                    )
                elif hit.confidence < 0.65:
                    warnings.append(
                        f"{hipaa_id} detected in column '{col}' via weak heuristic "
                        f"(confidence {hit.confidence:.2f})."
                    )
    return warnings


def compliance_scope_note() -> str:
    """Fixed PARTIAL_SCOPE explanation — #16/#17 never assessed in Excel pipeline."""
    return PARTIAL_SCOPE_REASON


def compliance_disclaimer() -> str:
    return NON_CERTIFICATION_DISCLAIMER


def identifiers_not_assessed() -> list[str]:
    return list(IDENTIFIERS_NOT_ASSESSED)


def sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    """Strip keys that could carry raw or masked PHI into CheckResult details."""
    return {k: v for k, v in details.items() if k not in FORBIDDEN_DETAIL_KEYS}


def column_only_eligible_types() -> tuple[str, ...]:
    return (TYPE_MRN, TYPE_BENEFICIARY_ID, TYPE_UNIQUE_ID)


def column_matches_any_hipaa_hint(column_name: str | None) -> bool:
    return any(
        column_name_matches_hints(column_name, spec.column_hints) for spec in ALL_HIPAA_SPECS
    )
