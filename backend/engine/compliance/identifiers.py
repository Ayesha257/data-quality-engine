"""
Phase 2 M9 — HIPAA identifier registry (PHASE2_HIPAA_PHI_PLAN.md §4.1).

Canonical mapping from Phase 1 TYPE_* labels to the 18 HHS Safe Harbor
identifiers. #16 and #17 are always out-of-scope for tabular Excel input.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    BIOMETRIC = "hipaa_16_biometric"
    FULL_FACE_PHOTO = "hipaa_17_full_face_photo"
    OTHER_UNIQUE_ID = "hipaa_18_other_unique_id"


OUT_OF_SCOPE_IDENTIFIERS: frozenset[HipaaIdentifier] = frozenset(
    {
        HipaaIdentifier.BIOMETRIC,
        HipaaIdentifier.FULL_FACE_PHOTO,
    }
)

ASSESSABLE_IDENTIFIERS: frozenset[HipaaIdentifier] = frozenset(
    h for h in HipaaIdentifier if h not in OUT_OF_SCOPE_IDENTIFIERS
)

# Excel/CSV pipeline cannot assess biometrics or photographs — fixed scope note.
PARTIAL_SCOPE_REASON = (
    "Identifiers #16 (biometric) and #17 (full-face photographs) cannot be "
    "assessed in structured Excel/CSV data. This pipeline accepts no image or "
    "audio input."
)

SCOPE_LABEL = "PARTIAL_SCOPE"

NON_CERTIFICATION_DISCLAIMER = (
    "This scan detects the presence of PHI-like identifiers in structured "
    "data only. It does NOT determine whether your organization is HIPAA "
    "compliant, whether a BAA is in place, or whether applicable "
    "safeguards (encryption, access controls, audit logging) are satisfied."
)


@dataclass(frozen=True)
class HipaaIdentifierSpec:
    """Registry row: number, display name, detection method, assessability."""

    number: int
    hipaa_id: HipaaIdentifier
    name: str
    detection_method: str
    in_scope: bool
    out_of_scope_reason: str | None = None


IDENTIFIER_REGISTRY: tuple[HipaaIdentifierSpec, ...] = (
    HipaaIdentifierSpec(1, HipaaIdentifier.NAMES, "Names", "Presidio PERSON / TYPE_NAME", True),
    HipaaIdentifierSpec(
        2,
        HipaaIdentifier.GEO_SUBSTATE,
        "Geographic subdivisions smaller than state",
        "Presidio LOCATION + TYPE_ADDRESS / TYPE_POSTAL_CODE",
        True,
    ),
    HipaaIdentifierSpec(
        3,
        HipaaIdentifier.DATES,
        "Dates tied to an individual (incl. age >89)",
        "Presidio DATE_TIME + TYPE_DOB + age_heuristic",
        True,
    ),
    HipaaIdentifierSpec(
        4,
        HipaaIdentifier.PHONE,
        "Phone numbers",
        "Presidio PHONE_NUMBER + TYPE_PHONE / TYPE_MOBILE regex",
        True,
    ),
    HipaaIdentifierSpec(
        5,
        HipaaIdentifier.FAX,
        "Fax numbers",
        "TYPE_FAX regex + column hint",
        True,
    ),
    HipaaIdentifierSpec(
        6,
        HipaaIdentifier.EMAIL,
        "Email addresses",
        "Presidio EMAIL_ADDRESS / TYPE_EMAIL",
        True,
    ),
    HipaaIdentifierSpec(
        7,
        HipaaIdentifier.SSN,
        "Social Security numbers",
        "Presidio US_SSN + TYPE_SSN regex",
        True,
    ),
    HipaaIdentifierSpec(
        8,
        HipaaIdentifier.MEDICAL_RECORD,
        "Medical record numbers",
        "TYPE_MRN regex + column hints",
        True,
    ),
    HipaaIdentifierSpec(
        9,
        HipaaIdentifier.HEALTH_PLAN_BENEFICIARY,
        "Health plan beneficiary numbers",
        "TYPE_BENEFICIARY_ID regex + column hints",
        True,
    ),
    HipaaIdentifierSpec(
        10,
        HipaaIdentifier.ACCOUNT_NUMBER,
        "Account numbers",
        "TYPE_BANK_ACCOUNT / TYPE_IBAN / TYPE_CARD + column hints",
        True,
    ),
    HipaaIdentifierSpec(
        11,
        HipaaIdentifier.LICENSE_CERTIFICATE,
        "Certificate/license numbers",
        "TYPE_DRIVER_LICENSE / TYPE_PASSPORT / TYPE_LICENSE_CERT",
        True,
    ),
    HipaaIdentifierSpec(
        12,
        HipaaIdentifier.VEHICLE_IDENTIFIER,
        "Vehicle identifiers / serial numbers",
        "TYPE_VIN regex (ISO 3779 checksum) + column hints",
        True,
    ),
    HipaaIdentifierSpec(
        13,
        HipaaIdentifier.DEVICE_IDENTIFIER,
        "Device identifiers / serial numbers",
        "TYPE_DEVICE_SERIAL regex + column hints",
        True,
    ),
    HipaaIdentifierSpec(14, HipaaIdentifier.URL, "URLs", "TYPE_URL regex", True),
    HipaaIdentifierSpec(
        15,
        HipaaIdentifier.IP_ADDRESS,
        "IP addresses",
        "TYPE_IP_ADDRESS regex",
        True,
    ),
    HipaaIdentifierSpec(
        16,
        HipaaIdentifier.BIOMETRIC,
        "Biometric identifiers",
        "Not assessed",
        False,
        "Fingerprints, retinal scans, and voiceprints require image/audio/binary input.",
    ),
    HipaaIdentifierSpec(
        17,
        HipaaIdentifier.FULL_FACE_PHOTO,
        "Full-face photographs",
        "Not assessed",
        False,
        "No image columns in the Excel/CSV pipeline.",
    ),
    HipaaIdentifierSpec(
        18,
        HipaaIdentifier.OTHER_UNIQUE_ID,
        "Other unique identifying number/code",
        "TYPE_CNIC / TYPE_UNIQUE_ID + high-cardinality column heuristic",
        True,
    ),
)

IDENTIFIERS_NOT_ASSESSED: list[str] = [
    HipaaIdentifier.BIOMETRIC.value,
    HipaaIdentifier.FULL_FACE_PHOTO.value,
]

# Column-name substrings that tie a date column to an individual (plan §4.1 footnote).
INDIVIDUAL_DATE_COLUMN_HINTS: frozenset[str] = frozenset(
    {
        "dob",
        "birth",
        "date of birth",
        "admission",
        "discharge",
        "death",
        "service_date",
        "patient_date",
    }
)

# Maps internal detect_pii TYPE_* labels → one or more HipaaIdentifier values.
PII_TYPE_TO_HIPAA: dict[str, list[HipaaIdentifier]] = {
    "NAME": [HipaaIdentifier.NAMES],
    "ADDRESS": [HipaaIdentifier.GEO_SUBSTATE],
    "POSTAL_CODE": [HipaaIdentifier.GEO_SUBSTATE],
    "DOB": [HipaaIdentifier.DATES],
    "PHONE": [HipaaIdentifier.PHONE],
    "MOBILE": [HipaaIdentifier.PHONE],
    "FAX": [HipaaIdentifier.FAX],
    "EMAIL": [HipaaIdentifier.EMAIL],
    "SSN": [HipaaIdentifier.SSN],
    "MRN": [HipaaIdentifier.MEDICAL_RECORD],
    "BENEFICIARY_ID": [HipaaIdentifier.HEALTH_PLAN_BENEFICIARY],
    "BANK_ACCOUNT": [HipaaIdentifier.ACCOUNT_NUMBER],
    "IBAN": [HipaaIdentifier.ACCOUNT_NUMBER],
    "CARD": [HipaaIdentifier.ACCOUNT_NUMBER],
    "DRIVER_LICENSE": [HipaaIdentifier.LICENSE_CERTIFICATE],
    "PASSPORT": [HipaaIdentifier.LICENSE_CERTIFICATE],
    "LICENSE_CERT": [HipaaIdentifier.LICENSE_CERTIFICATE],
    "VIN": [HipaaIdentifier.VEHICLE_IDENTIFIER],
    "DEVICE_SERIAL": [HipaaIdentifier.DEVICE_IDENTIFIER],
    "URL": [HipaaIdentifier.URL],
    "IP_ADDRESS": [HipaaIdentifier.IP_ADDRESS],
    "CNIC": [HipaaIdentifier.OTHER_UNIQUE_ID],
    "UNIQUE_ID": [HipaaIdentifier.OTHER_UNIQUE_ID],
}

HIGH_SENSITIVITY_HIPAA_IDS: frozenset[str] = frozenset(
    {
        HipaaIdentifier.SSN.value,
        HipaaIdentifier.MEDICAL_RECORD.value,
        HipaaIdentifier.HEALTH_PLAN_BENEFICIARY.value,
    }
)


def hipaa_ids_for_pii_type(pii_type: str) -> list[HipaaIdentifier]:
    """Return mapped HIPAA identifiers for a Phase 1 TYPE_* label."""
    return list(PII_TYPE_TO_HIPAA.get(pii_type, []))


def column_suggests_individual_date(column_name: str | None) -> bool:
    """True when column name implies birth/admission/discharge/death dates."""
    if not column_name:
        return False
    lowered = str(column_name).strip().lower().replace("_", " ")
    return any(hint in lowered for hint in INDIVIDUAL_DATE_COLUMN_HINTS)
