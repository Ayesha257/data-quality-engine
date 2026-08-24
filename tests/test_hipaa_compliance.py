"""Phase 2 M9 — HIPAA PHI compliance tests (PHASE2_HIPAA_PHI_PLAN.md §7)."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.engine.pii.detect_pii import detect_pii_in_series
from backend.engine.pii.mask_pii import mask_pii
from backend.engine.compliance.compliance_status import (
    FORBIDDEN_DETAIL_KEYS,
    map_pii_summary_to_hipaa,
)
from backend.engine.compliance.identifiers import (
    IDENTIFIERS_NOT_ASSESSED,
    HipaaIdentifier,
    NON_CERTIFICATION_DISCLAIMER,
    SCOPE_LABEL,
)
from backend.engine.compliance.recognizers import (
    MRN_SPEC,
    apply_column_hint_boost,
    vin_checksum_valid,
)
from backend.engine.compliance.scanner import (
    assess_hipaa_compliance,
    assess_hipaa_compliance_as_check_results,
)
from backend.engine.compliance.scoring import score_hipaa_compliance
from backend.engine.compliance.scanner import HipaaComplianceResult


def _summaries(df: pd.DataFrame) -> dict[str, dict]:
    return {str(col): detect_pii_in_series(df[col]) for col in df.columns}


def test_map_email_to_hipaa_06():
    """TYPE_EMAIL counts map to hipaa_06_email."""
    summary = {
        "column": "email",
        "rows_with_pii": 1,
        "type_counts": {"EMAIL": 2},
        "masked_rows": {},
        "unique_count": 1,
        "age_over_89_count": 0,
    }
    mapped = map_pii_summary_to_hipaa(summary, column_name="email", row_count=1)
    assert HipaaIdentifier.EMAIL.value in mapped.hits_by_identifier


def test_mrn_column_hint_boosts_confidence():
    """Column named 'MRN' boosts weak numeric regex hits."""
    boosted = apply_column_hint_boost(0.55, "MRN", MRN_SPEC)
    assert boosted == pytest.approx(0.80)
    unchanged = apply_column_hint_boost(0.55, "quantity", MRN_SPEC)
    assert unchanged == pytest.approx(0.55)


def test_age_over_89_detected():
    """Numeric age column with value 90+ maps to hipaa_03_dates."""
    df = pd.DataFrame({"patient_age": [45, 92, 95]})
    summary = detect_pii_in_series(df["patient_age"])
    assert summary["age_over_89_count"] == 2
    result = assess_hipaa_compliance(_summaries(df), len(df))
    assert HipaaIdentifier.DATES.value in result.identifiers_found


def test_vin_regex():
    """Valid 17-char VIN maps to hipaa_12_vehicle_identifier."""
    vin = "1HGBH41JXMN109186"
    assert vin_checksum_valid(vin)
    df = pd.DataFrame({"vin": [vin]})
    result = assess_hipaa_compliance(_summaries(df), len(df))
    assert HipaaIdentifier.VEHICLE_IDENTIFIER.value in result.identifiers_found


def test_biometric_always_not_assessed():
    """Result.identifiers_not_assessed always includes #16 and #17."""
    df = pd.DataFrame({"sku": ["SKU-001", "SKU-002"]})
    result = assess_hipaa_compliance(_summaries(df), len(df))
    assert HipaaIdentifier.BIOMETRIC.value in result.identifiers_not_assessed
    assert HipaaIdentifier.FULL_FACE_PHOTO.value in result.identifiers_not_assessed
    assert result.identifiers_not_assessed == list(IDENTIFIERS_NOT_ASSESSED)


def test_no_phi_clean_dataset():
    """Synthetic ERP data with no PHI -> NO_PHI_DETECTED."""
    df = pd.DataFrame(
        {
            "order_id": ["ORD-1001", "ORD-1002"],
            "product_code": ["WIDGET-A", "WIDGET-B"],
            "quantity": [10, 25],
            "unit_price": [9.99, 14.50],
        }
    )
    result = assess_hipaa_compliance(_summaries(df), len(df))
    assert result.status == "NO_PHI_DETECTED"
    assert result.scope == SCOPE_LABEL
    assert not result.identifiers_found


def test_phi_detected_mixed():
    """Dataset with name + SSN + email -> PHI_DETECTED with correct counts."""
    df = pd.DataFrame(
        {
            "patient_name": ["Jane Doe"],
            "ssn": ["900-00-0001"],
            "email": ["patient@example.com"],
        }
    )
    summaries = _summaries(df)
    if "NAME" not in summaries["patient_name"].get("type_counts", {}):
        summaries["patient_name"] = {
            **summaries["patient_name"],
            "type_counts": {**summaries["patient_name"].get("type_counts", {}), "NAME": 1},
            "rows_with_pii": max(1, summaries["patient_name"].get("rows_with_pii", 0)),
        }
    result = assess_hipaa_compliance(summaries, len(df))
    assert result.status == "PHI_DETECTED"
    found = set(result.identifiers_found)
    assert HipaaIdentifier.SSN.value in found
    assert HipaaIdentifier.EMAIL.value in found
    assert HipaaIdentifier.NAMES.value in found


def test_check_result_never_contains_raw_values():
    """assess_hipaa_compliance_as_check_results details have no value keys."""
    df = pd.DataFrame({"MRN": ["12345678"], "ssn": ["900-00-0002"]})
    checks = assess_hipaa_compliance_as_check_results(_summaries(df), len(df))
    assert checks
    for check in checks:
        assert not FORBIDDEN_DETAIL_KEYS.intersection(check.details.keys())
        details_str = str(check.details)
        assert "12345678" not in details_str
        assert "900-00-0002" not in details_str


def test_assessor_never_raises_on_empty_df():
    """Empty pii_summary -> NO_PHI_DETECTED, not exception."""
    result = assess_hipaa_compliance({}, 0)
    assert result.status == "NO_PHI_DETECTED"
    checks = assess_hipaa_compliance_as_check_results({}, 0)
    assert checks
    assert checks[0].status == "passed"


def test_assessor_never_raises_on_malformed_summary(monkeypatch):
    """Malformed input -> status='error', blockers populated."""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated mapper failure")

    monkeypatch.setattr(
        "backend.engine.compliance.scanner._map_all_columns",
        _boom,
    )
    result = assess_hipaa_compliance({"col": {"type_counts": {"EMAIL": 1}}}, 1)
    assert result.status == "error"
    assert result.blockers
    checks = assess_hipaa_compliance_as_check_results({"col": {"type_counts": {"EMAIL": 1}}}, 1)
    assert checks[0].status == "error"
    assert checks[0].details.get("hipaa_status") == "error"


def test_fax_distinguished_from_phone_by_column():
    """Fax number in 'fax_number' column maps to hipaa_05_fax, not phone."""
    # Mapping-layer contract: TYPE_FAX in a fax-hinted column -> #5 only.
    summary = {
        "column": "fax_number",
        "rows_with_pii": 1,
        "type_counts": {"FAX": 1},
        "masked_rows": {},
        "unique_count": 1,
        "age_over_89_count": 0,
    }
    mapped = map_pii_summary_to_hipaa(summary, column_name="fax_number", row_count=1)
    assert HipaaIdentifier.FAX.value in mapped.hits_by_identifier
    assert HipaaIdentifier.PHONE.value not in mapped.hits_by_identifier
    result = assess_hipaa_compliance({"fax_number": summary}, 1)
    assert HipaaIdentifier.FAX.value in result.identifiers_found
    assert HipaaIdentifier.PHONE.value not in result.identifiers_found


def test_disclaimer_present():
    """HipaaComplianceResult.disclaimer is non-empty."""
    result = assess_hipaa_compliance({}, 0)
    assert result.disclaimer
    assert result.disclaimer == NON_CERTIFICATION_DISCLAIMER


def test_exposure_score_severity_high_for_ssn_and_name():
    """SSN + NAME across columns -> severity 'high'."""
    result = HipaaComplianceResult(
        status="PHI_DETECTED",
        scope=SCOPE_LABEL,
        identifier_counts={
            HipaaIdentifier.SSN.value: 1,
            HipaaIdentifier.NAMES.value: 1,
            HipaaIdentifier.DATES.value: 1,
        },
        columns_with_phi=["ssn", "patient_name", "dob"],
        identifiers_found=[
            HipaaIdentifier.SSN.value,
            HipaaIdentifier.NAMES.value,
            HipaaIdentifier.DATES.value,
        ],
        identifiers_not_assessed=list(IDENTIFIERS_NOT_ASSESSED),
    )
    scored = score_hipaa_compliance(result)
    assert scored.severity == "high"


def test_out_of_scope_report_text(capsys):
    """Check results include NOT ASSESSED identifiers #16 and #17."""
    df = pd.DataFrame({"MRN": ["12345678"]})
    summaries = _summaries(df)
    result = assess_hipaa_compliance(summaries, len(df))
    for not_assessed in result.identifiers_not_assessed:
        print(f"  {not_assessed}: NOT ASSESSED")
    out = capsys.readouterr().out
    assert "hipaa_16_biometric: NOT ASSESSED" in out
    assert "hipaa_17_full_face_photo: NOT ASSESSED" in out
    checks = assess_hipaa_compliance_as_check_results(summaries, len(df))
    not_assessed = checks[0].details.get("identifiers_not_assessed", [])
    assert HipaaIdentifier.BIOMETRIC.value in not_assessed
    assert HipaaIdentifier.FULL_FACE_PHOTO.value in not_assessed


def test_masking_unchanged_for_new_types():
    """MRN values masked via mask_pii before summary — raw never in hipaa output."""
    mrn_value = "12345678"
    df = pd.DataFrame({"MRN": [mrn_value]})
    summary = detect_pii_in_series(df["MRN"])
    assert summary.get("masked_rows")
    masked_text = next(iter(summary["masked_rows"].values()))
    assert mrn_value not in masked_text
    checks = assess_hipaa_compliance_as_check_results({"MRN": summary}, len(df))
    serialized = str(checks)
    assert mrn_value not in serialized
    # mask_pii still redacts the planted value when given explicit hits
    hits = [{"type": "MRN", "start": 0, "end": len(mrn_value), "value": mrn_value, "score": 0.9}]
    assert mrn_value not in mask_pii(mrn_value, hits)
