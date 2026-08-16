"""
Phase 2 M9 — HIPAA compliance scanner (PHASE2_HIPAA_PHI_PLAN.md §4.5).

Wires identifiers, recognizers, and compliance_status together column-by-column.
Runs after detect_pii_in_series, before scoring — never re-scans cell values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.logging import get_logger, log_event
from backend.engine.models import CheckResult
from backend.engine.compliance.compliance_status import (
    ColumnHipaaSummary,
    aggregate_identifier_counts,
    build_detection_methods,
    collect_warnings,
    compliance_disclaimer,
    compliance_scope_note,
    counts_by_column,
    derive_compliance_status,
    identifiers_not_assessed,
    map_pii_summary_to_hipaa,
    sanitize_details,
)
from backend.engine.compliance.identifiers import SCOPE_LABEL

CHECK_NAME = "hipaa_phi"


@dataclass
class HipaaComplianceResult:
    """
    Aggregate compliance posture — mirrors M3 ReadinessScore pattern.
    Never contains raw PHI values.
    """

    status: str  # PHI_DETECTED | NO_PHI_DETECTED | error
    scope: str  # Always PARTIAL_SCOPE for Excel pipeline
    identifier_counts: dict[str, int] = field(default_factory=dict)
    counts_by_column: dict[str, dict[str, int]] = field(default_factory=dict)
    columns_with_phi: list[str] = field(default_factory=list)
    identifiers_found: list[str] = field(default_factory=list)
    identifiers_not_assessed: list[str] = field(default_factory=list)
    detection_methods: dict[str, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    disclaimer: str = field(default_factory=compliance_disclaimer)


def _map_all_columns(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
) -> dict[str, ColumnHipaaSummary]:
    summaries: dict[str, ColumnHipaaSummary] = {}
    for col, pii_summary in (pii_summary_by_column or {}).items():
        if not isinstance(pii_summary, dict):
            continue
        summaries[str(col)] = map_pii_summary_to_hipaa(
            pii_summary,
            column_name=str(col),
            row_count=row_count,
        )
    return summaries


def _build_result_from_summaries(
    column_summaries: dict[str, ColumnHipaaSummary],
) -> HipaaComplianceResult:
    id_counts = aggregate_identifier_counts(column_summaries)
    col_counts = counts_by_column(column_summaries)
    columns_with_phi = sorted(
        col for col, counts in col_counts.items() if sum(counts.values()) > 0
    )
    identifiers_found = sorted(
        hipaa_id for hipaa_id, count in id_counts.items() if count > 0
    )
    return HipaaComplianceResult(
        status=derive_compliance_status(id_counts),
        scope=SCOPE_LABEL,
        identifier_counts=id_counts,
        counts_by_column=col_counts,
        columns_with_phi=columns_with_phi,
        identifiers_found=identifiers_found,
        identifiers_not_assessed=identifiers_not_assessed(),
        detection_methods=build_detection_methods(column_summaries),
        warnings=collect_warnings(column_summaries),
        disclaimer=compliance_disclaimer(),
    )


def _log_assessment(
    logger: logging.Logger | None,
    run_id: str | None,
    result: HipaaComplianceResult,
) -> None:
    if logger is None or not run_id:
        return
    total_hits = sum(result.identifier_counts.values())
    log_event(
        logger,
        logging.INFO,
        "HIPAA assessment complete",
        run_id=run_id,
        step="hipaa_compliance",
        details={
            "status": result.status,
            "scope": result.scope,
            "identifiers_found": result.identifiers_found,
            "identifier_counts": result.identifier_counts,
            "columns_with_phi": len(result.columns_with_phi),
            "total_hits": total_hits,
        },
    )


def assess_hipaa_compliance(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
    *,
    run_id: str | None = None,
) -> HipaaComplianceResult:
    """
    Main entry point. Maps Phase 1 PII summaries to HIPAA identifiers
    and computes dataset-level posture. Never raises. Never logs raw PHI.
    """
    logger = get_logger(run_id) if run_id else None
    try:
        column_summaries = _map_all_columns(pii_summary_by_column, row_count)
        result = _build_result_from_summaries(column_summaries)
        _log_assessment(logger, run_id, result)
        return result
    except Exception as exc:  # noqa: BLE001 — compliance must never crash pipeline
        if logger and run_id:
            log_event(
                logger,
                logging.ERROR,
                "HIPAA assessment failed",
                run_id=run_id,
                step="hipaa_compliance",
                details={"error": str(exc)},
            )
        return HipaaComplianceResult(
            status="error",
            scope=SCOPE_LABEL,
            identifiers_not_assessed=identifiers_not_assessed(),
            blockers=[f"HIPAA assessment failed: {exc}"],
            disclaimer=compliance_disclaimer(),
        )


def assess_hipaa_compliance_as_check_results(
    pii_summary_by_column: dict[str, dict[str, Any]],
    row_count: int,
    *,
    run_id: str | None = None,
) -> list[CheckResult]:
    """
    CheckResult-compatible wrapper for pipeline consumers.

    Returns one file-level CheckResult plus one per column with HIPAA hits.
    dimension is intentionally empty — NOT a rubric dimension (plan §5.2).
    """
    try:
        result = assess_hipaa_compliance(
            pii_summary_by_column,
            row_count,
            run_id=run_id,
        )
        if result.status == "error":
            return [
                CheckResult(
                    check_name=CHECK_NAME,
                    status="error",
                    column=None,
                    issues_found=0,
                    dimension="",
                    details=sanitize_details(
                        {
                            "hipaa_status": "error",
                            "scope": result.scope,
                            "scope_note": compliance_scope_note(),
                            "identifiers_not_assessed": result.identifiers_not_assessed,
                            "blockers": result.blockers,
                            "disclaimer": result.disclaimer,
                        }
                    ),
                )
            ]

        file_issues = sum(result.identifier_counts.values())
        file_status = "failed" if result.status == "PHI_DETECTED" else "passed"
        results: list[CheckResult] = [
            CheckResult(
                check_name=CHECK_NAME,
                status=file_status,
                column=None,
                issues_found=file_issues,
                dimension="",
                details=sanitize_details(
                    {
                        "hipaa_status": result.status,
                        "scope": result.scope,
                        "scope_note": compliance_scope_note(),
                        "identifier_counts": result.identifier_counts,
                        "identifiers_found": result.identifiers_found,
                        "identifiers_not_assessed": result.identifiers_not_assessed,
                        "detection_methods": result.detection_methods,
                        "columns_with_phi": result.columns_with_phi,
                        "warnings": result.warnings,
                        "disclaimer": result.disclaimer,
                    }
                ),
            )
        ]

        for col in result.columns_with_phi:
            col_counts = result.counts_by_column.get(col, {})
            col_issues = sum(col_counts.values())
            results.append(
                CheckResult(
                    check_name=CHECK_NAME,
                    status="failed",
                    column=col,
                    issues_found=col_issues,
                    dimension="",
                    details=sanitize_details(
                        {
                            "hipaa_status": "PHI_DETECTED",
                            "scope": result.scope,
                            "identifiers": col_counts,
                            "disclaimer": result.disclaimer,
                        }
                    ),
                )
            )
        return results
    except Exception as exc:  # noqa: BLE001
        logger = get_logger(run_id) if run_id else None
        if logger and run_id:
            log_event(
                logger,
                logging.ERROR,
                "HIPAA CheckResult conversion failed",
                run_id=run_id,
                step="hipaa_compliance",
                details={"error": str(exc)},
            )
        return [
            CheckResult(
                check_name=CHECK_NAME,
                status="error",
                column=None,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {
                        "hipaa_status": "error",
                        "scope": SCOPE_LABEL,
                        "blockers": [f"HIPAA CheckResult conversion failed: {exc}"],
                        "disclaimer": compliance_disclaimer(),
                    }
                ),
            )
        ]
