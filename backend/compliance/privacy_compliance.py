"""
Privacy compliance scanner for GDPR and CCPA/CPRA.

GDPR and CCPA share a few identifier detectors (SSN, email, IP) because those
patterns are personal data under both laws, then diverge:

- GDPR (identifiability of a natural person): national ID, date of birth
  (column-name gated), name + location linkage.
- CCPA/CPRA (California PI categories): telephone numbers, precise geolocation,
  unique personal identifiers (device/cookie/household/account IDs).

No Human-in-the-Loop pause. Medium-confidence GDPR findings are dual-gated in
the detector and auto-included in the Medium Confidence report section.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from backend.compliance.privacy_detectors import (
    _is_dob_column_name,
    _is_phone_column_name,
    detect_dob_candidate,
    detect_email,
    detect_ip_address,
    detect_name_geolocation_columns,
    detect_national_id,
    detect_phone,
    detect_precise_geolocation_columns,
    detect_ssn,
    detect_unique_identifier_columns,
)

logger = logging.getLogger("dqe.compliance.privacy")

_MAX_VALIDATE = 8000


def _skip_dtype(series: pd.Series) -> bool:
    return pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(series)


def _scan_values(
    series: pd.Series,
    detect_fn: Callable[[Any], dict[str, Any]],
    *,
    cheap_regex: str,
    sample_key: str = "masked_value",
) -> tuple[int, list[str]]:
    """Vectorized pre-filter then validate candidates. Fast on wide/tall frames."""
    s = series.dropna()
    if s.empty or _skip_dtype(s):
        return 0, []
    str_s = s.astype(str)
    candidates = str_s[str_s.str.contains(cheap_regex, regex=True, na=False)]
    if candidates.empty:
        return 0, []
    if len(candidates) > _MAX_VALIDATE:
        candidates = candidates.iloc[:_MAX_VALIDATE]
    matches = 0
    samples: list[str] = []
    for val in candidates:
        res = detect_fn(val)
        if res.get("match"):
            matches += 1
            sample = res.get(sample_key) or res.get("normalized_value")
            if sample and len(samples) < 5:
                samples.append(str(sample))
    return matches, samples


def _finding(
    *,
    rule: str,
    field_name: str,
    display_name: str,
    regulation: str,
    column_name: str,
    confidence: str,
    issues_found: int,
    total_rows: int,
    description: str,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "rule": rule,
        "field_name": field_name,
        "display_name": display_name,
        "regulation": regulation,
        "column_name": column_name,
        "confidence": confidence,
        "issues_found": issues_found,
        "total_rows": total_rows,
        "description": description,
    }
    item.update(extra)
    return item


def scan_privacy_findings(
    df: pd.DataFrame, regulation: str = "GDPR"
) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for GDPR or CCPA findings categorized by confidence tier."""
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"high": high, "medium": medium, "low": low}

    reg_name = str(regulation).upper().replace("-", "_")
    if reg_name == "CCPA":
        prefix = "ccpa"
    else:
        prefix = "gdpr"
        reg_name = "GDPR"

    n_rows = len(df)
    cols = [str(c) for c in df.columns]

    # Shared identifier values (legal overlap, separate rule IDs / report copy).
    for col in df.columns:
        col_name = str(col)
        series = df[col]

        matches, samples = _scan_values(series, detect_ssn, cheap_regex=r"\d{3}")
        if matches:
            high.append(_finding(
                rule=f"{prefix}_ssn",
                field_name="ssn",
                display_name="Social Security Number (SSN)",
                regulation=reg_name,
                column_name=col_name,
                confidence="high",
                issues_found=matches,
                total_rows=n_rows,
                description=f"Found {matches} US Social Security Number(s) in column '{col_name}'.",
                masked_samples=samples,
            ))

        matches, samples = _scan_values(series, detect_email, cheap_regex=r"@")
        if matches:
            high.append(_finding(
                rule=f"{prefix}_email",
                field_name="email",
                display_name="Email Address",
                regulation=reg_name,
                column_name=col_name,
                confidence="high",
                issues_found=matches,
                total_rows=n_rows,
                description=f"Found {matches} email address value(s) in column '{col_name}'.",
                masked_samples=samples,
            ))

        matches, samples = _scan_values(
            series,
            detect_ip_address,
            cheap_regex=r"[.:]",
            sample_key="normalized_value",
        )
        if matches:
            ip_label = (
                "IP Address (device identifier)"
                if reg_name == "CCPA"
                else "IP Address (IPv4 / IPv6)"
            )
            high.append(_finding(
                rule=f"{prefix}_ip_address",
                field_name="ip_address",
                display_name=ip_label,
                regulation=reg_name,
                column_name=col_name,
                confidence="high",
                issues_found=matches,
                total_rows=n_rows,
                description=f"Found {matches} network IP address(es) in column '{col_name}'.",
                samples=samples,
            ))

    if reg_name == "GDPR":
        for col in df.columns:
            col_name = str(col)
            series = df[col]
            matches, samples = _scan_values(series, detect_national_id, cheap_regex=r"\d{5}")
            if matches:
                high.append(_finding(
                    rule="gdpr_national_id",
                    field_name="national_id",
                    display_name="National Identity Number",
                    regulation="GDPR",
                    column_name=col_name,
                    confidence="high",
                    issues_found=matches,
                    total_rows=n_rows,
                    description=f"Found {matches} National Identity Number(s) in column '{col_name}'.",
                    masked_samples=samples,
                ))

            if not _is_dob_column_name(col_name):
                continue
            series_nn = series.dropna()
            if series_nn.empty:
                continue
            if len(series_nn) > _MAX_VALIDATE:
                series_nn = series_nn.iloc[:_MAX_VALIDATE]
            matches = 0
            samples = []
            for val in series_nn:
                res = detect_dob_candidate(val, col_name)
                if res.get("match"):
                    matches += 1
                    if len(samples) < 5 and res.get("normalized_value"):
                        samples.append(res["normalized_value"])
            if matches:
                medium.append(_finding(
                    rule="gdpr_date_of_birth",
                    field_name="date_of_birth",
                    display_name="Date of Birth",
                    regulation="GDPR",
                    column_name=col_name,
                    confidence="medium",
                    issues_found=matches,
                    total_rows=n_rows,
                    description=(
                        f"Column '{col_name}' matches date-of-birth patterns with "
                        f"{matches} valid date value(s)."
                    ),
                    samples=samples,
                ))

        geo_res = detect_name_geolocation_columns(cols)
        if geo_res.get("match"):
            medium.append(_finding(
                rule="gdpr_full_name_geolocation",
                field_name="full_name_geolocation",
                display_name="Name and Geolocation Linkage",
                regulation="GDPR",
                column_name=", ".join(
                    geo_res.get("name_columns", []) + geo_res.get("geo_columns", [])
                ),
                confidence="medium",
                issues_found=1,
                total_rows=n_rows,
                description=geo_res.get(
                    "description", "Dataset contains both name and geolocation columns."
                ),
                name_columns=geo_res.get("name_columns", []),
                geo_columns=geo_res.get("geo_columns", []),
            ))

    else:
        for col in df.columns:
            col_name = str(col)
            if not _is_phone_column_name(col_name):
                continue
            series = df[col].dropna()
            if series.empty:
                continue
            if len(series) > _MAX_VALIDATE:
                series = series.iloc[:_MAX_VALIDATE]
            matches = 0
            samples = []
            for val in series:
                res = detect_phone(val, col_name)
                if res.get("match"):
                    matches += 1
                    if len(samples) < 5 and res.get("masked_value"):
                        samples.append(res["masked_value"])
            if matches:
                high.append(_finding(
                    rule="ccpa_phone",
                    field_name="phone",
                    display_name="Telephone Number",
                    regulation="CCPA",
                    column_name=col_name,
                    confidence="high",
                    issues_found=matches,
                    total_rows=n_rows,
                    description=f"Found {matches} telephone number(s) in column '{col_name}'.",
                    masked_samples=samples,
                ))

        geo_res = detect_precise_geolocation_columns(cols)
        if geo_res.get("match"):
            high.append(_finding(
                rule="ccpa_precise_geolocation",
                field_name="precise_geolocation",
                display_name="Precise Geolocation (CPRA sensitive)",
                regulation="CCPA",
                column_name=", ".join(geo_res.get("geo_columns", [])),
                confidence="high",
                issues_found=1,
                total_rows=n_rows,
                description=geo_res.get(
                    "description", "Precise geolocation columns present."
                ),
                geo_columns=geo_res.get("geo_columns", []),
            ))

        uid_res = detect_unique_identifier_columns(cols)
        if uid_res.get("match"):
            high.append(_finding(
                rule="ccpa_unique_personal_identifier",
                field_name="unique_personal_identifier",
                display_name="Unique Personal Identifier",
                regulation="CCPA",
                column_name=", ".join(uid_res.get("identifier_columns", [])),
                confidence="high",
                issues_found=1,
                total_rows=n_rows,
                description=uid_res.get(
                    "description", "Unique personal identifier columns present."
                ),
                identifier_columns=uid_res.get("identifier_columns", []),
            ))

    return {"high": high, "medium": medium, "low": low}


def scan_gdpr_findings(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for GDPR findings categorized by confidence tier."""
    return scan_privacy_findings(df, regulation="GDPR")


def scan_ccpa_findings(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Scan DataFrame for CCPA findings categorized by confidence tier."""
    return scan_privacy_findings(df, regulation="CCPA")


def run_privacy_scan(
    df: pd.DataFrame,
    regulation: str = "GDPR",
    prompt: Any | None = None,
    resolved_decisions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """
    Scan GDPR or CCPA without pausing for HITL.

    `prompt` and `resolved_decisions` are accepted for call-site compatibility
    and ignored: dual-gated medium findings are auto-included.
    """
    del prompt, resolved_decisions
    reg_norm = str(regulation).upper().replace("-", "_")
    if reg_norm != "CCPA":
        reg_norm = "GDPR"
    scanned = scan_privacy_findings(df, regulation=reg_norm)

    high = scanned["high"]
    medium = scanned["medium"]

    disclaimer = (
        f"This report flags compliance-relevant data patterns. "
        f"It does not certify legal compliance with {reg_norm}."
    )

    return {
        "regulation": reg_norm,
        "disclaimer": disclaimer,
        "confidence_tiers": {
            "High Confidence": high,
            "Medium Confidence": medium,
            "Confirmed (User-Verified)": [],
        },
        "resolved_findings": [*high, *medium],
        "low_findings_pending": [],
    }
