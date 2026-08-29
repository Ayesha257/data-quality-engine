"""
Privacy compliance detectors -- GDPR and CCPA/CPRA.

Implements detection rules for cross-domain privacy regulations:
1. detect_ssn(value): 9-digit US SSN with invalid area (000, 666, 900-999), group (00),
   and serial (0000) exclusion. Masked to last 4 digits. High confidence.
2. detect_ip_address(value): IPv4 and IPv6 network address validation. High confidence.
3. detect_national_id(value): National identity number (CNIC/national ID), reusing
   backend.engine.pii.detect_pii._CNIC_RE by reference. High confidence.
4. detect_email(value): Email address detection, reusing
   backend.engine.pii.detect_pii._EMAIL_RE by reference. High confidence.
5. detect_dob_candidate(value, column_name): Date-of-birth candidate matching date
   format regex strictly gated by column-name fuzzy match (dob, birth, date_of_birth).
   Medium confidence.
6. detect_name_geolocation_columns(column_names): Flagged when both a person name-like
   column and a geolocation-like column (address, city, zip, lat/long) coexist.
   Medium confidence.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.engine.compliance.compliance_status import sanitize_details
from backend.engine.models import CheckResult
from backend.engine.pii.detect_pii import _CNIC_RE, _EMAIL_RE, _PHONE_RE
from backend.engine.utils.fuzzy_matching import (
    column_matches_keywords,
    find_matching_columns,
    normalize_column_name,
)

CHECK_NAME = "privacy"

# ---------------------------------------------------------------------------
# Rule Configuration Loader
# ---------------------------------------------------------------------------


def _find_compliance_rules_path() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "compliance_rules.json",
        Path(__file__).resolve().parents[2] / "backend" / "config" / "compliance_rules.json",
        Path("backend/config/compliance_rules.json").resolve(),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def load_privacy_rules(framework: str = "GDPR") -> dict[str, Any]:
    """Load GDPR or CCPA rules definition from compliance_rules.json."""
    path = _find_compliance_rules_path()
    content = json.loads(path.read_text(encoding="utf-8"))
    frameworks = content.get("frameworks") or content.get("regulations") or {}
    key = str(framework).upper().replace("-", "_")
    target = frameworks.get(key, frameworks.get("GDPR", {}))
    if isinstance(target, dict) and "alias_of" in target and target["alias_of"] in frameworks:
        aliased = frameworks[target["alias_of"]]
        merged = dict(aliased)
        merged.update({k: v for k, v in target.items() if k != "rules"})
        if "rules" not in target or not target["rules"]:
            merged["rules"] = aliased.get("rules", [])
        return merged
    return target


# ---------------------------------------------------------------------------
# Shared Detection Result Object
# ---------------------------------------------------------------------------


class DetectionResult(dict):
    """Dictionary supporting both item and attribute access for detection results."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'DetectionResult' object has no attribute '{name}'")


# ---------------------------------------------------------------------------
# 1. SSN (Social Security Number) Detector -- High Confidence
# ---------------------------------------------------------------------------

_SSN_FORMATTED_RE = re.compile(r"^\s*(\d{3})\s*[- ]\s*(\d{2})\s*[- ]\s*(\d{4})\s*$")
_SSN_DIGITS_RE = re.compile(r"^\s*(\d{3})(\d{2})(\d{4})\s*$")


def _is_valid_ssn(area: str, group: str, serial: str) -> bool:
    """Validate US SSN area, group, and serial number rules."""
    if not (len(area) == 3 and len(group) == 2 and len(serial) == 4):
        return False
    if not (area.isdigit() and group.isdigit() and serial.isdigit()):
        return False
    area_int = int(area)
    # Area cannot be 000, 666, or 900-999
    if area_int == 0 or area_int == 666 or 900 <= area_int <= 999:
        return False
    # Group cannot be 00
    if int(group) == 0:
        return False
    # Serial cannot be 0000
    if int(serial) == 0:
        return False
    return True


def detect_ssn(value: Any) -> DetectionResult:
    """Detect and validate a US Social Security Number (SSN).

    High confidence. Validates area (rejects 000, 666, 900-999), group (rejects 00),
    and serial (rejects 0000). Returns masked value showing only the last 4 digits.
    """
    if value is None:
        return DetectionResult(
            match=False,
            field_name="ssn",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return DetectionResult(
                match=False,
                field_name="ssn",
                detection_type="regex",
                confidence=None,
                masked_value=None,
            )
        if value.is_integer():
            int_val = int(value)
            val_str = str(int_val).zfill(9) if 10000000 <= int_val <= 999999999 else str(int_val)
        else:
            val_str = str(value)
    elif isinstance(value, int):
        val_str = str(value).zfill(9) if 10000000 <= value <= 999999999 else str(value)
    else:
        if pd.isna(value):
            return DetectionResult(
                match=False,
                field_name="ssn",
                detection_type="regex",
                confidence=None,
                masked_value=None,
            )
        val_str = str(value).strip()

    if not val_str:
        return DetectionResult(
            match=False,
            field_name="ssn",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    # 1. Try formatted SSN (XXX-XX-XXXX or XXX XX XXXX)
    m = _SSN_FORMATTED_RE.match(val_str)
    if m:
        area, group, serial = m.group(1), m.group(2), m.group(3)
        if _is_valid_ssn(area, group, serial):
            return DetectionResult(
                match=True,
                field_name="ssn",
                detection_type="regex",
                confidence="high",
                masked_value=f"***-**-{serial}",
            )

    # 2. Try unformatted 9-digit string
    m9 = _SSN_DIGITS_RE.match(val_str)
    if m9:
        area, group, serial = m9.group(1), m9.group(2), m9.group(3)
        if _is_valid_ssn(area, group, serial):
            return DetectionResult(
                match=True,
                field_name="ssn",
                detection_type="regex",
                confidence="high",
                masked_value=f"***-**-{serial}",
            )

    return DetectionResult(
        match=False,
        field_name="ssn",
        detection_type="regex",
        confidence=None,
        masked_value=None,
    )


# ---------------------------------------------------------------------------
# 2. IP Address Detector (IPv4 and IPv6) -- High Confidence
# ---------------------------------------------------------------------------

_IPV4_STRICT_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)


def detect_ip_address(value: Any) -> DetectionResult:
    """Detect and validate IPv4 and IPv6 network addresses.

    High confidence. Validates standard octet ranges (0-255) for IPv4 and valid hex
    groupings / compressed notation for IPv6.
    """
    if value is None:
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )

    if isinstance(value, float):
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )
    if isinstance(value, int):
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )
    if pd.isna(value):
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )

    val_str = str(value).strip()
    if not val_str:
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )

    # Exclude common software versions / build numbers (e.g., "v1.2.3.4", "1.0.0")
    if val_str.startswith("v") or val_str.startswith("V"):
        return DetectionResult(
            match=False,
            field_name="ip_address",
            detection_type="regex",
            confidence=None,
            ip_version=None,
            normalized_value=None,
        )

    # 1. Check strict IPv4 regex
    if _IPV4_STRICT_RE.match(val_str):
        try:
            ip = ipaddress.IPv4Address(val_str)
            return DetectionResult(
                match=True,
                field_name="ip_address",
                detection_type="regex",
                confidence="high",
                ip_version="IPv4",
                normalized_value=str(ip),
            )
        except ValueError:
            pass

    # 2. Check IPv6 if string contains colons
    if ":" in val_str:
        try:
            ip = ipaddress.IPv6Address(val_str)
            return DetectionResult(
                match=True,
                field_name="ip_address",
                detection_type="regex",
                confidence="high",
                ip_version="IPv6",
                normalized_value=str(ip),
            )
        except ValueError:
            pass

    return DetectionResult(
        match=False,
        field_name="ip_address",
        detection_type="regex",
        confidence=None,
        ip_version=None,
        normalized_value=None,
    )


# ---------------------------------------------------------------------------
# 3. National ID (CNIC / National Identity) -- High Confidence
# ---------------------------------------------------------------------------


def detect_national_id(value: Any) -> DetectionResult:
    """Detect national identification numbers (e.g. CNIC), referencing _CNIC_RE.

    High confidence.
    """
    if value is None or pd.isna(value):
        return DetectionResult(
            match=False,
            field_name="national_id",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    val_str = str(value).strip()
    if not val_str:
        return DetectionResult(
            match=False,
            field_name="national_id",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    m = _CNIC_RE.search(val_str)
    if m:
        raw_match = m.group()
        clean = raw_match.replace("-", "")
        masked = f"{clean[:5]}-*******-{clean[-1]}" if len(clean) == 13 else f"*****{clean[-4:]}"
        return DetectionResult(
            match=True,
            field_name="national_id",
            detection_type="regex",
            confidence="high",
            masked_value=masked,
        )

    return DetectionResult(
        match=False,
        field_name="national_id",
        detection_type="regex",
        confidence=None,
        masked_value=None,
    )


# ---------------------------------------------------------------------------
# 4. Email Address Detector -- High Confidence
# ---------------------------------------------------------------------------


def detect_email(value: Any) -> DetectionResult:
    """Detect email addresses referencing _EMAIL_RE.

    High confidence.
    """
    if value is None or pd.isna(value):
        return DetectionResult(
            match=False,
            field_name="email",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    val_str = str(value).strip()
    if not val_str:
        return DetectionResult(
            match=False,
            field_name="email",
            detection_type="regex",
            confidence=None,
            masked_value=None,
        )

    m = _EMAIL_RE.search(val_str)
    if m:
        email = m.group()
        user, domain = email.split("@", 1) if "@" in email else (email, "")
        masked = f"{user[:2]}***@{domain}" if len(user) > 2 else f"*@{domain}"
        return DetectionResult(
            match=True,
            field_name="email",
            detection_type="regex",
            confidence="high",
            masked_value=masked,
        )

    return DetectionResult(
        match=False,
        field_name="email",
        detection_type="regex",
        confidence=None,
        masked_value=None,
    )


# ---------------------------------------------------------------------------
# 5. Date of Birth Detector -- Medium Confidence (HITL Candidate)
# ---------------------------------------------------------------------------

_DOB_COLUMN_KEYWORDS = (
    "dob",
    "birth",
    "date_of_birth",
    "date of birth",
    "birth_date",
    "birth date",
    "birthdate",
    "birthday",
    "birth_dt",
    "birthdt",
    "dateofbirth",
)

_NON_DOB_DATE_KEYWORDS = (
    "created_at",
    "create_date",
    "created_date",
    "creation_date",
    "updated_at",
    "update_date",
    "modified_at",
    "modification_date",
    "order_date",
    "transaction_date",
    "invoice_date",
    "ship_date",
    "shipping_date",
    "delivery_date",
    "due_date",
    "start_date",
    "end_date",
    "expiry_date",
    "expiration_date",
    "so_date",
    "posting_date",
    "eff_date",
    "effective_date",
)

_DOB_FORMAT_RE = re.compile(
    r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})$",
    re.I,
)


def _is_dob_column_name(column_name: str) -> bool:
    """True ONLY if column name matches birth-specific keywords and not generic dates."""
    norm = normalize_column_name(column_name)
    if not norm:
        return False

    # Check negative list first: generic dates must NEVER be flagged as DOB
    for neg in _NON_DOB_DATE_KEYWORDS:
        if neg in norm:
            return False

    # Exact token / substring checks for birth keywords
    tokens = set(norm.split())
    if "dob" in tokens or "birthday" in tokens or "birthdate" in tokens or "dateofbirth" in tokens:
        return True

    return column_matches_keywords(norm, _DOB_COLUMN_KEYWORDS, threshold=80)


def detect_dob_candidate(value: Any, column_name: str) -> DetectionResult:
    """Detect date-of-birth candidate.

    Medium confidence. Dual-gated on birth-related column names and valid dates.
    Auto-included in the GDPR report (no HITL). Generic date columns are ignored.
    """
    if not _is_dob_column_name(column_name):
        return DetectionResult(
            match=False,
            field_name="date_of_birth",
            detection_type="regex_keyword",
            confidence=None,
            column_name=column_name,
            normalized_value=None,
        )

    if value is None or pd.isna(value):
        return DetectionResult(
            match=False,
            field_name="date_of_birth",
            detection_type="regex_keyword",
            confidence=None,
            column_name=column_name,
            normalized_value=None,
        )

    # Check datetime types
    if isinstance(value, (pd.Timestamp, pd.DatetimeIndex)):
        val_str = value.strftime("%Y-%m-%d")
        return DetectionResult(
            match=True,
            field_name="date_of_birth",
            detection_type="regex_keyword",
            confidence="medium",
            column_name=column_name,
            normalized_value=val_str,
        )

    val_str = str(value).strip()
    if not val_str or val_str.lower() in ("nan", "nat", "none", "null"):
        return DetectionResult(
            match=False,
            field_name="date_of_birth",
            detection_type="regex_keyword",
            confidence=None,
            column_name=column_name,
            normalized_value=None,
        )

    # Check date format regex
    if _DOB_FORMAT_RE.match(val_str):
        return DetectionResult(
            match=True,
            field_name="date_of_birth",
            detection_type="regex_keyword",
            confidence="medium",
            column_name=column_name,
            normalized_value=val_str,
        )

    # Try pandas date parser
    try:
        dt = pd.to_datetime(val_str, errors="coerce")
        if pd.notna(dt):
            # Plausible birth year between 1900 and present
            if 1900 <= dt.year <= 2026:
                return DetectionResult(
                    match=True,
                    field_name="date_of_birth",
                    detection_type="regex_keyword",
                    confidence="medium",
                    column_name=column_name,
                    normalized_value=dt.strftime("%Y-%m-%d"),
                )
    except Exception:
        pass

    return DetectionResult(
        match=False,
        field_name="date_of_birth",
        detection_type="regex_keyword",
        confidence=None,
        column_name=column_name,
        normalized_value=None,
    )


# ---------------------------------------------------------------------------
# 6. Name + Geolocation Linkage Detector -- Medium Confidence (HITL Candidate)
# ---------------------------------------------------------------------------

_NAME_KEYWORDS = [
    "name",
    "full_name",
    "fullname",
    "first_name",
    "firstname",
    "last_name",
    "lastname",
    "customer_name",
    "customername",
    "client_name",
    "clientname",
    "patient_name",
    "patientname",
    "user_name",
    "username",
    "contact_name",
    "contactname",
    "employee_name",
    "person_name",
    "sold_to_name",
]

_NON_PERSON_NAME_KEYWORDS = [
    "company",
    "org",
    "organization",
    "product",
    "item",
    "file",
    "table",
    "sheet",
    "vendor",
    "supplier",
    "bank",
    "site",
]

_GEO_KEYWORDS = [
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "address",
    "address_line_1",
    "address_line_2",
    "address_line_3",
    "street",
    "city",
    "zip",
    "zip_code",
    "zipcode",
    "postal_code",
    "postalcode",
    "post_code",
    "postcode",
    "state",
    "province",
    "country",
    "geolocation",
    "geo_location",
    "coordinates",
    "gps",
]

_NON_GEO_KEYWORDS = [
    "ip",
    "ipv4",
    "ipv6",
    "mac",
    "email",
    "mail",
    "web",
    "url",
    "server",
    "host",
    "socket",
    "network",
]


def detect_name_geolocation_columns(column_names: Iterable[object]) -> DetectionResult:
    """Detect if a dataset contains both person name-like and geolocation-like columns.

    Medium confidence. Auto-included in the GDPR report (no HITL).
    Flags only when at least one person name column AND at least one geolocation column
    coexist in the dataset headers.
    """
    cols = [str(c) for c in column_names if c is not None]
    if not cols:
        return DetectionResult(
            match=False,
            field_name="full_name_geolocation",
            detection_type="column_keyword",
            confidence=None,
            name_columns=[],
            geo_columns=[],
        )

    matched_names: list[str] = []
    for col in cols:
        norm = normalize_column_name(col)
        if not norm:
            continue
        # Exclude non-person names (company, product, file)
        if any(non in norm for non in _NON_PERSON_NAME_KEYWORDS):
            continue
        if column_matches_keywords(norm, _NAME_KEYWORDS, threshold=80):
            matched_names.append(col)

    matched_geos: list[str] = []
    for col in cols:
        norm = normalize_column_name(col)
        if not norm:
            continue
        # Exclude digital/network addresses (IP address, MAC address, email address)
        tokens = set(norm.split())
        if any(non in norm for non in _NON_GEO_KEYWORDS) or any(t in _NON_GEO_KEYWORDS for t in tokens):
            continue
        if column_matches_keywords(norm, _GEO_KEYWORDS, threshold=80):
            matched_geos.append(col)

    if matched_names and matched_geos:
        return DetectionResult(
            match=True,
            field_name="full_name_geolocation",
            detection_type="column_keyword",
            confidence="medium",
            name_columns=matched_names,
            geo_columns=matched_geos,
            description=(
                f"Dataset combines person name column(s) ({', '.join(matched_names)}) "
                f"with geolocation column(s) ({', '.join(matched_geos)}), creating personal data linkage."
            ),
        )

    return DetectionResult(
        match=False,
        field_name="full_name_geolocation",
        detection_type="column_keyword",
        confidence=None,
        name_columns=matched_names,
        geo_columns=matched_geos,
    )


# ---------------------------------------------------------------------------
# 7. CCPA-only: Telephone Number -- High Confidence (column-name gated)
# ---------------------------------------------------------------------------

_PHONE_COLUMN_KEYWORDS = (
    "phone",
    "phone_number",
    "phonenumber",
    "mobile",
    "mobile_number",
    "cell",
    "cell_phone",
    "telephone",
    "tel",
    "fax",
    "contact_number",
    "contact_no",
)


def _is_phone_column_name(column_name: str) -> bool:
    norm = normalize_column_name(column_name)
    if not norm:
        return False
    tokens = set(norm.split())
    if tokens & {"phone", "mobile", "cell", "telephone", "tel", "fax"}:
        return True
    return column_matches_keywords(norm, _PHONE_COLUMN_KEYWORDS, threshold=85)


_US_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)


def detect_phone(value: Any, column_name: str = "") -> DetectionResult:
    """Detect a telephone number (US, California, international). Column-name gated to avoid order-ID false positives."""
    if column_name and not _is_phone_column_name(column_name):
        return DetectionResult(
            match=False,
            field_name="phone",
            detection_type="regex_keyword",
            confidence=None,
            masked_value=None,
        )

    if value is None or pd.isna(value):
        return DetectionResult(
            match=False,
            field_name="phone",
            detection_type="regex_keyword",
            confidence=None,
            masked_value=None,
        )

    val_str = str(value).strip()
    if not val_str:
        return DetectionResult(
            match=False,
            field_name="phone",
            detection_type="regex_keyword",
            confidence=None,
            masked_value=None,
        )

    m = _PHONE_RE.search(val_str) or _US_PHONE_RE.search(val_str)
    if m:
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        masked = f"***{digits[-4:]}" if len(digits) >= 4 else "***"
        return DetectionResult(
            match=True,
            field_name="phone",
            detection_type="regex_keyword",
            confidence="high",
            masked_value=masked,
        )

    return DetectionResult(
        match=False,
        field_name="phone",
        detection_type="regex_keyword",
        confidence=None,
        masked_value=None,
    )


# ---------------------------------------------------------------------------
# 8. CCPA-only: Precise Geolocation (CPRA sensitive PI) -- High Confidence
# ---------------------------------------------------------------------------

_PRECISE_GEO_KEYWORDS = [
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "gps",
    "coordinates",
    "geolocation",
    "geo_location",
    "precise_location",
    "precise_geolocation",
]


def detect_precise_geolocation_columns(column_names: Iterable[object]) -> DetectionResult:
    """CPRA sensitive precise geolocation: lat/long/GPS/coordinates columns only."""
    cols = [str(c) for c in column_names if c is not None]
    matched: list[str] = []
    for col in cols:
        norm = normalize_column_name(col)
        if not norm:
            continue
        tokens = set(norm.split())
        if any(non in tokens for non in _NON_GEO_KEYWORDS):
            continue
        if column_matches_keywords(norm, _PRECISE_GEO_KEYWORDS, threshold=85):
            matched.append(col)

    if matched:
        return DetectionResult(
            match=True,
            field_name="precise_geolocation",
            detection_type="column_keyword",
            confidence="high",
            geo_columns=matched,
            description=(
                f"Column(s) {', '.join(matched)} match CPRA precise geolocation "
                "(latitude/longitude/GPS/coordinates)."
            ),
        )

    return DetectionResult(
        match=False,
        field_name="precise_geolocation",
        detection_type="column_keyword",
        confidence=None,
        geo_columns=[],
    )


# ---------------------------------------------------------------------------
# 9. CCPA-only: Unique Personal Identifier -- High Confidence
# ---------------------------------------------------------------------------

_UNIQUE_ID_KEYWORDS = [
    "device_id",
    "deviceid",
    "advertising_id",
    "advertisingid",
    "idfa",
    "gaid",
    "cookie_id",
    "cookieid",
    "client_id",
    "customer_id",
    "account_id",
    "unique_id",
    "uniqueid",
    "household_id",
    "householdid",
    "maid",
    "aaid",
]


def detect_unique_identifier_columns(column_names: Iterable[object]) -> DetectionResult:
    """CCPA unique personal identifiers from column names (device, cookie, household, account)."""
    cols = [str(c) for c in column_names if c is not None]
    matched: list[str] = []
    for col in cols:
        norm = normalize_column_name(col)
        if not norm:
            continue
        if column_matches_keywords(norm, _UNIQUE_ID_KEYWORDS, threshold=88):
            matched.append(col)

    if matched:
        return DetectionResult(
            match=True,
            field_name="unique_personal_identifier",
            detection_type="column_keyword",
            confidence="high",
            identifier_columns=matched,
            description=(
                f"Column(s) {', '.join(matched)} match CCPA unique personal identifier naming "
                "(device, advertising, cookie, customer/account, or household ID)."
            ),
        )

    return DetectionResult(
        match=False,
        field_name="unique_personal_identifier",
        detection_type="column_keyword",
        confidence=None,
        identifier_columns=[],
    )


# ---------------------------------------------------------------------------
# 10. CheckResult Wrappers (Matching GLBA / PCI DSS check contract)
# ---------------------------------------------------------------------------


def check_privacy_ssn(
    df: pd.DataFrame, column: str, regulation: str = "GDPR"
) -> CheckResult:
    """Scan a column for valid Social Security Numbers (SSN)."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0:
            return CheckResult(
                check_name=f"{regulation.lower()}_ssn",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "ssn", "regulation": regulation, "confidence": "high", "reason": "no_data"}
                ),
            )

        matches = sum(1 for v in series if detect_ssn(v)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total

        return CheckResult(
            check_name=f"{regulation.lower()}_ssn",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "ssn",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "regex",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_ssn",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_ip_address(
    df: pd.DataFrame, column: str, regulation: str = "GDPR"
) -> CheckResult:
    """Scan a column for IPv4 and IPv6 network addresses."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0:
            return CheckResult(
                check_name=f"{regulation.lower()}_ip_address",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "ip_address", "regulation": regulation, "confidence": "high", "reason": "no_data"}
                ),
            )

        matches = sum(1 for v in series if detect_ip_address(v)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total

        return CheckResult(
            check_name=f"{regulation.lower()}_ip_address",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "ip_address",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "regex",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_ip_address",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_dob(
    df: pd.DataFrame, column: str, regulation: str = "GDPR"
) -> CheckResult:
    """Scan a column for date of birth candidates (medium confidence)."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0 or not _is_dob_column_name(column):
            return CheckResult(
                check_name=f"{regulation.lower()}_date_of_birth",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "date_of_birth", "regulation": regulation, "confidence": "medium", "reason": "no_match"}
                ),
            )

        matches = sum(1 for v in series if detect_dob_candidate(v, column)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total

        return CheckResult(
            check_name=f"{regulation.lower()}_date_of_birth",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "date_of_birth",
                    "regulation": regulation,
                    "confidence": "medium",
                    "method": "regex_keyword",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_date_of_birth",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_name_geolocation(
    column_names: Iterable[Any], regulation: str = "GDPR"
) -> CheckResult:
    """Check dataset column headers for person name + geolocation linkage."""
    try:
        res = detect_name_geolocation_columns(column_names)
        status = "failed" if res["match"] else "passed"
        return CheckResult(
            check_name=f"{regulation.lower()}_full_name_geolocation",
            status=status,
            column=None,
            issues_found=1 if res["match"] else 0,
            dimension="",
            details=sanitize_details(
                {
                    "rule": "full_name_geolocation",
                    "regulation": regulation,
                    "confidence": "medium",
                    "method": "column_keyword",
                    "name_columns": res.get("name_columns", []),
                    "geo_columns": res.get("geo_columns", []),
                    "description": res.get("description"),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_full_name_geolocation",
            status="error",
            column=None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_email(
    df: pd.DataFrame, column: str, regulation: str = "GDPR"
) -> CheckResult:
    """Scan a column for email addresses."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0:
            return CheckResult(
                check_name=f"{regulation.lower()}_email",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "email", "regulation": regulation, "confidence": "high", "reason": "no_data"}
                ),
            )

        matches = sum(1 for v in series if detect_email(v)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total

        return CheckResult(
            check_name=f"{regulation.lower()}_email",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "email",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "regex",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_email",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_national_id(
    df: pd.DataFrame, column: str, regulation: str = "GDPR"
) -> CheckResult:
    """Scan a column for national identity numbers."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0:
            return CheckResult(
                check_name=f"{regulation.lower()}_national_id",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "national_id", "regulation": regulation, "confidence": "high", "reason": "no_data"}
                ),
            )

        matches = sum(1 for v in series if detect_national_id(v)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total
        return CheckResult(
            check_name=f"{regulation.lower()}_national_id",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "national_id",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "regex",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_national_id",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_phone(
    df: pd.DataFrame, column: str, regulation: str = "CCPA"
) -> CheckResult:
    """Scan a phone-named column for telephone numbers (CCPA identifier)."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")
        if column not in df.columns:
            raise KeyError(f"column {column!r} not found")

        series = df[column].dropna()
        total = int(len(series))
        if total == 0 or not _is_phone_column_name(column):
            return CheckResult(
                check_name=f"{regulation.lower()}_phone",
                status="passed",
                column=column,
                issues_found=0,
                dimension="",
                details=sanitize_details(
                    {"rule": "phone", "regulation": regulation, "confidence": "high", "reason": "no_match"}
                ),
            )

        matches = sum(1 for v in series if detect_phone(v, column)["match"])
        status = "failed" if matches > 0 else "passed"
        quality_ratio = (total - matches) / total
        return CheckResult(
            check_name=f"{regulation.lower()}_phone",
            status=status,
            column=column,
            issues_found=matches,
            dimension="",
            quality_ratio=quality_ratio,
            details=sanitize_details(
                {
                    "rule": "phone",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "regex_keyword",
                    "rows_scanned": total,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_phone",
            status="error",
            column=column if isinstance(column, str) else None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_precise_geolocation(
    column_names: Iterable[Any], regulation: str = "CCPA"
) -> CheckResult:
    """Check headers for CPRA precise geolocation columns."""
    try:
        res = detect_precise_geolocation_columns(column_names)
        status = "failed" if res["match"] else "passed"
        return CheckResult(
            check_name=f"{regulation.lower()}_precise_geolocation",
            status=status,
            column=None,
            issues_found=1 if res["match"] else 0,
            dimension="",
            details=sanitize_details(
                {
                    "rule": "precise_geolocation",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "column_keyword",
                    "geo_columns": res.get("geo_columns", []),
                    "description": res.get("description"),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_precise_geolocation",
            status="error",
            column=None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_unique_identifier(
    column_names: Iterable[Any], regulation: str = "CCPA"
) -> CheckResult:
    """Check headers for CCPA unique personal identifier columns."""
    try:
        res = detect_unique_identifier_columns(column_names)
        status = "failed" if res["match"] else "passed"
        return CheckResult(
            check_name=f"{regulation.lower()}_unique_personal_identifier",
            status=status,
            column=None,
            issues_found=1 if res["match"] else 0,
            dimension="",
            details=sanitize_details(
                {
                    "rule": "unique_personal_identifier",
                    "regulation": regulation,
                    "confidence": "high",
                    "method": "column_keyword",
                    "identifier_columns": res.get("identifier_columns", []),
                    "description": res.get("description"),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_name=f"{regulation.lower()}_unique_personal_identifier",
            status="error",
            column=None,
            issues_found=0,
            dimension="",
            details=sanitize_details({"error": str(exc)}),
        )


def check_privacy_compliance(
    df: pd.DataFrame, regulation: str = "GDPR"
) -> list[CheckResult]:
    """Run regulation-specific privacy CheckResults against a DataFrame."""
    try:
        if df is None or not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame")

        reg_norm = str(regulation).upper().replace("-", "_")
        results: list[CheckResult] = []
        cols = [str(c) for c in df.columns]

        if reg_norm == "CCPA":
            for col in cols:
                results.append(check_privacy_ssn(df, col, regulation=reg_norm))
                results.append(check_privacy_email(df, col, regulation=reg_norm))
                results.append(check_privacy_ip_address(df, col, regulation=reg_norm))
                results.append(check_privacy_phone(df, col, regulation=reg_norm))
            results.append(check_privacy_precise_geolocation(cols, regulation=reg_norm))
            results.append(check_privacy_unique_identifier(cols, regulation=reg_norm))
        else:
            for col in cols:
                results.append(check_privacy_ssn(df, col, regulation=reg_norm))
                results.append(check_privacy_national_id(df, col, regulation=reg_norm))
                results.append(check_privacy_email(df, col, regulation=reg_norm))
                results.append(check_privacy_ip_address(df, col, regulation=reg_norm))
                results.append(check_privacy_dob(df, col, regulation=reg_norm))
            results.append(check_privacy_name_geolocation(cols, regulation=reg_norm))
        return results
    except Exception as exc:  # noqa: BLE001
        return [
            CheckResult(
                check_name=f"{regulation.lower()}_compliance",
                status="error",
                column=None,
                issues_found=0,
                dimension="",
                details=sanitize_details({"error": str(exc)}),
            )
        ]


def check_gdpr_compliance(df: pd.DataFrame) -> list[CheckResult]:
    """Run GDPR compliance check suite."""
    return check_privacy_compliance(df, regulation="GDPR")


def check_ccpa_compliance(df: pd.DataFrame) -> list[CheckResult]:
    """Run CCPA compliance check suite."""
    return check_privacy_compliance(df, regulation="CCPA")
