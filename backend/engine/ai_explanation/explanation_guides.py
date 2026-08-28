"""
Plain-language educational guides for each report section.

Used by ai_explainer.py for both AI prompts and rule-based fallbacks so a
novice reader always learns what a check means, how this project runs it,
what was found, and how to improve — even when Gemini is unavailable.
"""

from __future__ import annotations

from typing import Any

# Labels rendered by enhanced_report.py — order matters for display.
EXPLANATION_SECTIONS: tuple[str, ...] = (
    "WHAT THIS MEANS",
    "HOW WE CHECKED IT",
    "WHAT WE FOUND",
    "WHY IT MATTERS",
    "HOW TO FIX",
)

CHECK_GUIDE: dict[str, dict[str, str]] = {
    "missing_values": {
        "what_it_means": (
            "This check measures completeness — whether important cells in your spreadsheet "
            "are empty when they should contain a value. Blank rows in a customer name or "
            "amount column mean reports and dashboards may skip records or show wrong totals."
        ),
        "how_we_check": (
            "We scan every column and count how many cells are empty (null, blank, or whitespace-only). "
            "Each column gets a pass or fail based on how much data is missing compared to the full sheet."
        ),
        "fix_hints": (
            "Go back to the source system (ERP, CRM, export job) and make critical fields required. "
            "Fill gaps from master data where possible, or flag incomplete rows for manual review before reuse."
        ),
    },
    "duplicates": {
        "what_it_means": (
            "Duplicates mean the same record appears more than once — either an entire row copied "
            "verbatim or the same business key (order ID, customer code) repeated. This inflates "
            "counts and can double-count revenue or inventory."
        ),
        "how_we_check": (
            "We look for exact duplicate rows and, when possible, duplicate values in columns that "
            "look like unique identifiers (names ending in ID, Code, No., etc.)."
        ),
        "fix_hints": (
            "Remove or merge duplicate rows at the source. Add a unique constraint on the business key "
            "in your database or export filter so the same transaction cannot be loaded twice."
        ),
    },
    "type_mismatch": {
        "what_it_means": (
            "Type reliability checks whether each column stores data in a consistent, usable form — "
            "for example numbers stored as numbers (not text), dates as dates, and categories as text. "
            "Mixed types break sorting, sums, and charts silently."
        ),
        "how_we_check": (
            "For each column we sample values and detect the dominant type (number, date, text). "
            "Values that do not match that dominant pattern are counted as type issues."
        ),
        "fix_hints": (
            "Fix formatting at export (avoid storing numbers with currency symbols in the cell). "
            "Standardize date formats in the source system and re-export, or clean mixed columns in Excel "
            "before uploading again."
        ),
    },
    "outliers": {
        "what_it_means": (
            "Outliers are values that sit far outside the normal range for a numeric column — for example "
            "a sales amount ten times higher than every other row, or a negative quantity where only "
            "positive values are expected. They may be data entry errors or rare but valid events."
        ),
        "how_we_check": (
            "On measurement columns only (amounts, quantities, scores), we use the IQR (interquartile range) "
            "method: values beyond 1.5× the typical spread from the middle of the distribution are flagged. "
            "Identifier, date, and personal-data columns are skipped on purpose."
        ),
        "fix_hints": (
            "Review each flagged value with the business owner — confirm typos (extra zero), unit mix-ups, "
            "or legitimate exceptions. Correct errors at source; document valid outliers so analysts know to keep them."
        ),
    },
    "consistency": {
        "what_it_means": (
            "Consistency checks whether the same real-world thing is written the same way everywhere — "
            "for example 'Paid', 'paid', and 'PAID' treated as one category. Inconsistent labels split "
            "reports and make totals look wrong even when the underlying data exists."
        ),
        "how_we_check": (
            "We compare text and categorical columns for variants that likely mean the same thing — "
            "different spellings, casing, or extra spaces — and flag columns where inconsistent labels appear."
        ),
        "fix_hints": (
            "Use dropdown lists or lookup tables at data entry instead of free typing. "
            "Run a one-time cleanup to pick one canonical spelling per value, then enforce it in the source system."
        ),
    },
    "schema_quality": {
        "what_it_means": (
            "Schema quality is about the structure of your file — column names that are blank, duplicated, "
            "or unclear ('Column1', 'Unnamed'), and headers that are hard for people or tools to understand. "
            "Poor schema slows every downstream step."
        ),
        "how_we_check": (
            "After detecting the header row, we inspect column names for emptiness, duplicates, "
            "overly generic labels, and patterns that suggest merged or broken headers."
        ),
        "fix_hints": (
            "Rename columns to clear business names before sharing the file. "
            "Remove blank spacer columns and fix merged header rows in the original export template."
        ),
    },
    "validity": {
        "what_it_means": (
            "Validity checks whether values follow expected rules — correct email shape, phone patterns, "
            "allowed status codes, dates in range, and business rules from your configuration. "
            "A cell can be filled in but still unusable if the value is logically wrong."
        ),
        "how_we_check": (
            "We apply format rules (regex, allowed lists, min/max) and your configured business rules "
            "to each relevant column based on its role (email-like, date, categorical, etc.)."
        ),
        "fix_hints": (
            "Fix invalid entries at the source or in a staging sheet before re-upload. "
            "Tighten validation on forms and imports so bad values cannot enter the system in the first place."
        ),
    },
    "freshness": {
        "what_it_means": (
            "Freshness tells you how up to date your data is. We look at date columns and measure how "
            "recent the latest records are compared to today. Stale data leads to decisions based on "
            "old facts — for example forecasting or KPIs that no longer reflect reality."
        ),
        "how_we_check": (
            "We find columns classified as dates, parse the values, and compute how many days have passed "
            "since the most recent date in the file. Thresholds flag data that has not been refreshed "
            "within an expected window."
        ),
        "fix_hints": (
            "Refresh the export on your normal schedule (daily, weekly) and verify the latest transaction date "
            "matches expectations. Automate the extract so manual delays do not leave you working with outdated files."
        ),
    },
    "pii": {
        "what_it_means": (
            "This section scans for personally identifiable information (PII) — phone numbers, emails, "
            "national IDs, and similar fields that can identify a person. Finding PII is not always an "
            "error, but you must handle it carefully before sharing or analyzing data."
        ),
        "how_we_check": (
            "We pattern-match each column for common PII types (mobile numbers, emails, etc.) using "
            "rules tuned for this engine. Counts and column names are reported; raw sensitive values "
            "are never copied into the explanation."
        ),
        "fix_hints": (
            "Mask or remove PII before sharing files externally. Restrict access to raw columns, "
            "use aggregation where possible, and follow your organization's privacy and compliance policy."
        ),
    },
    "referential_integrity": {
        "what_it_means": (
            "Referential integrity checks whether values in one column point to records that should exist "
            "elsewhere — for example a customer ID on an order that does not appear in a customer master file. "
            "Broken references cause joins and reports to drop rows silently."
        ),
        "how_we_check": (
            "When you supply a reference directory of master files, we compare key columns in this dataset "
            "against those masters and count values with no matching parent record."
        ),
        "fix_hints": (
            "Investigate orphaned IDs — they may be typos, timing lag (order before master sync), or deleted "
            "master records. Correct keys at source or update reference files before reporting."
        ),
    },
    "hipaa_phi": {
        "what_it_means": (
            "This scan evaluates your dataset for Protected Health Information (PHI) under the Health Insurance "
            "Portability and Accountability Act (HIPAA). PHI includes 18 specific categories of personal identifiers "
            "(such as patient names, phone numbers, email addresses, Social Security numbers, medical record numbers, dates, "
            "and geographic details) when associated with healthcare services or payment data. Under US federal law (45 CFR Part 160 "
            "and Part 164), dataset handlers must safeguard PHI to protect individual patient privacy."
        ),
        "how_we_check": (
            "We scan column headers, data formats, and structural patterns across all rows to detect HIPAA identifier categories. "
            "We calculate a HIPAA Exposure Score (0 to 100) based on identifier severity, count of affected columns, and sensitivity "
            "of detected fields. Raw patient values are NEVER copied or stored in this report — only identifier categories and "
            "column-level summary metrics are recorded."
        ),
        "fix_hints": (
            "1. De-identify or mask sensitive columns (e.g. redact phone numbers or convert birth dates to age ranges) "
            "following HIPAA Safe Harbor guidelines (45 CFR § 164.514(b)). "
            "2. Restrict dataset access to authorized personnel with a signed Business Associate Agreement (BAA) in place. "
            "3. Ensure the file is encrypted at rest (AES-256) and in transit (TLS 1.3) before transferring outside secure networks."
        ),
    },
    "fuzzy_match": {
        "what_it_means": (
            "Fuzzy standardization suggests when two text values likely mean the same thing but are spelled "
            "differently — useful for cleaning categories before analysis. It does not change your data automatically."
        ),
        "how_we_check": (
            "On eligible text columns, we compare similar strings using fuzzy matching and group variants "
            "that exceed a similarity threshold, recommending a canonical spelling."
        ),
        "fix_hints": (
            "Review suggested groups with domain experts, pick one standard label per group, "
            "and apply bulk replacements in the source system or a controlled cleanup step."
        ),
    },
    "entity_resolution": {
        "what_it_means": (
            "Entity resolution helps standardize names for real-world things — cities, countries, product codes — "
            "when the same entity appears under multiple spellings. It suggests a canonical form without "
            "overwriting your original column."
        ),
        "how_we_check": (
            "We match values against known canonical lists, then similar strings (fuzzy match), then "
            "meaning-based similarity for harder cases. Each value gets auto-match, needs-review, or no-match."
        ),
        "fix_hints": (
            "Approve high-confidence mappings, manually review the review queue, and extend canonical lookup "
            "tables for recurring variants your organization uses."
        ),
    },
    "pci_pan": {
        "what_it_means": (
            "A Primary Account Number (PAN) is a credit or debit card number. Finding these in a "
            "spreadsheet means cardholder data may be stored in the clear."
        ),
        "how_we_check": (
            "We look for 13–19 digit sequences and confirm they pass the standard Luhn checksum "
            "used by payment cards. Full numbers are masked in this report."
        ),
        "fix_hints": (
            "Remove or tokenize card numbers at the source. Store only a processor token or last four "
            "digits, and restrict who can export this file."
        ),
    },
    "pci_card_expiry": {
        "what_it_means": (
            "Card expiration dates (month/year) are cardholder data. They often sit next to PANs "
            "and should not live in everyday operational files."
        ),
        "how_we_check": (
            "We first match column names that look like expiry/expiration, then check cell values "
            "against MM/YY or MM/YYYY patterns."
        ),
        "fix_hints": (
            "Drop expiry columns from analytics extracts, or store them only in the payment vault "
            "that already holds the card token."
        ),
    },
    "pci_cvv": {
        "what_it_means": (
            "CVV/CVC is the security code on a card. PCI DSS forbids storing it after authorization."
        ),
        "how_we_check": (
            "We flag columns whose names look like CVV, CVC, or security code. We do not read the cell values."
        ),
        "fix_hints": (
            "Delete CVV columns from stored files immediately. Never keep this field in backups or exports."
        ),
    },
    "glba_routing_number": {
        "what_it_means": (
            "An ABA routing number identifies a US bank for ACH and wires. It is nonpublic financial "
            "information when tied to a customer account."
        ),
        "how_we_check": (
            "We look for 9-digit values and confirm they pass the ABA weighted checksum."
        ),
        "fix_hints": (
            "Limit who can export routing numbers, mask them in reports, and keep them only in systems "
            "that already protect customer banking data."
        ),
    },
    "routing_number": {
        "what_it_means": (
            "An ABA routing number identifies a US bank for ACH and wires. It is nonpublic financial "
            "information when tied to a customer account."
        ),
        "how_we_check": (
            "We look for 9-digit values and confirm they pass the ABA weighted checksum."
        ),
        "fix_hints": (
            "Limit who can export routing numbers, mask them in reports, and keep them only in systems "
            "that already protect customer banking data."
        ),
    },
    "glba_bank_account_number": {
        "what_it_means": (
            "Bank account numbers and IBANs represent nonpublic personal financial information (NPI) "
            "under the Gramm-Leach-Bliley Act (GLBA). Unmasked account numbers pose severe fraud and unauthorized transfer risks."
        ),
        "how_we_check": (
            "We scan column headers for bank account, checking, savings, deposit, and IBAN naming patterns."
        ),
        "fix_hints": (
            "Mask account numbers to the last 4 digits (e.g., ****1234), restrict exports, and enforce role-based access control."
        ),
    },
    "bank_account_number": {
        "what_it_means": (
            "Bank account numbers and IBANs represent nonpublic personal financial information (NPI) "
            "under the Gramm-Leach-Bliley Act (GLBA). Unmasked account numbers pose severe fraud and unauthorized transfer risks."
        ),
        "how_we_check": (
            "We scan column headers for bank account, checking, savings, deposit, and IBAN naming patterns."
        ),
        "fix_hints": (
            "Mask account numbers to the last 4 digits (e.g., ****1234), restrict exports, and enforce role-based access control."
        ),
    },
    "glba_loan_application_data": {
        "what_it_means": (
            "Loan application records, principal amounts, and mortgage figures are protected customer financial data under GLBA."
        ),
        "how_we_check": (
            "We inspect column names for loan application, principal amount, mortgage balance, and borrowing details."
        ),
        "fix_hints": (
            "Restrict access to authorized lending officers and ensure compliance with GLBA financial privacy rules."
        ),
    },
    "loan_application_data": {
        "what_it_means": (
            "Loan application records, principal amounts, and mortgage figures are protected customer financial data under GLBA."
        ),
        "how_we_check": (
            "We inspect column names for loan application, principal amount, mortgage balance, and borrowing details."
        ),
        "fix_hints": (
            "Restrict access to authorized lending officers and ensure compliance with GLBA financial privacy rules."
        ),
    },
    "glba_credit_history_data": {
        "what_it_means": (
            "Credit scores, FICO ratings, and credit report summaries are regulated nonpublic financial records under GLBA."
        ),
        "how_we_check": (
            "We check column names for credit score, FICO rating, credit history, and bureau report keywords."
        ),
        "fix_hints": (
            "Isolate credit history columns to authorized underwriting environments and redact before broader analytical sharing."
        ),
    },
    "credit_history_data": {
        "what_it_means": (
            "Credit scores, FICO ratings, and credit report summaries are regulated nonpublic financial records under GLBA."
        ),
        "how_we_check": (
            "We check column names for credit score, FICO rating, credit history, and bureau report keywords."
        ),
        "fix_hints": (
            "Isolate credit history columns to authorized underwriting environments and redact before broader analytical sharing."
        ),
    },
    "glba_tax_return_data": {
        "what_it_means": (
            "Tax return information, adjusted gross income (AGI), and W-2/1099 data are strictly protected nonpublic records under GLBA."
        ),
        "how_we_check": (
            "We scan column headers for tax return, filing status, AGI, and income verification terms."
        ),
        "fix_hints": (
            "Enforce strict confidentiality and data masking for tax records in accordance with the GLBA Safeguards Rule."
        ),
    },
    "tax_return_data": {
        "what_it_means": (
            "Tax return information, adjusted gross income (AGI), and W-2/1099 data are strictly protected nonpublic records under GLBA."
        ),
        "how_we_check": (
            "We scan column headers for tax return, filing status, AGI, and income verification terms."
        ),
        "fix_hints": (
            "Enforce strict confidentiality and data masking for tax records in accordance with the GLBA Safeguards Rule."
        ),
    },
    "PAN": {
        "what_it_means": (
            "A Primary Account Number (PAN) is a credit or debit card number. Storing unmasked PANs violates PCI DSS Requirement 3."
        ),
        "how_we_check": (
            "We detect 13-19 digit card sequences passing the Luhn mod-10 algorithm. Full numbers are masked to the last 4 digits."
        ),
        "fix_hints": (
            "Tokenize card numbers, truncate to the last 4 digits, and never store raw PANs in plaintext operational spreadsheets."
        ),
    },
    "card_expiry": {
        "what_it_means": (
            "Card expiration dates represent sensitive cardholder data that should not be stored unencrypted alongside account numbers."
        ),
        "how_we_check": (
            "We match column headers indicating expiration and validate MM/YY date formats."
        ),
        "fix_hints": (
            "Drop expiration date columns from operational exports and store only tokenized payment credentials in secure vaults."
        ),
    },
    "cvv": {
        "what_it_means": (
            "Card Verification Values (CVV/CVC) are sensitive authentication data (SAD). PCI DSS strictly prohibits storing CVV post-authorization."
        ),
        "how_we_check": (
            "We flag column headers matching CVV/CVC security code terms. Row values are never read or stored."
        ),
        "fix_hints": (
            "Delete CVV columns immediately. Sensitive authentication data must never be written to persistent storage."
        ),
    },
    "audit_trail_headers": {
        "what_it_means": (
            "SOX-relevant files should show who created or approved a record and when it last changed, "
            "so auditors can follow the trail."
        ),
        "how_we_check": (
            "We look at column names for creator, approver, and last-modified style fields and report coverage."
        ),
        "fix_hints": (
            "Add created-by, approved-by, and last-modified timestamp columns at the source system before export."
        ),
    },
    "audit_trail_completeness": {
        "what_it_means": (
            "SOX-relevant files should show who created or approved a record and when it last changed, "
            "so auditors can follow the trail."
        ),
        "how_we_check": (
            "We look at column names for creator, approver, and last-modified style fields and report coverage."
        ),
        "fix_hints": (
            "Add created-by, approved-by, and last-modified timestamp columns at the source system before export."
        ),
    },
    "transaction_timestamp": {
        "what_it_means": (
            "Transaction timestamps prove when a financial event happened. Missing or invalid dates "
            "weaken an audit trail."
        ),
        "how_we_check": (
            "We identify columns that parse as dates or look like transaction timestamps."
        ),
        "fix_hints": (
            "Ensure every financial event has a reliable timestamp from the source system, not a blank or placeholder."
        ),
    },
    "sox_audit_trail_headers": {
        "what_it_means": (
            "SOX-relevant files should show who created or approved a record and when it last changed, "
            "so auditors can follow the trail."
        ),
        "how_we_check": (
            "We look at column names for creator, approver, and last-modified style fields and report coverage."
        ),
        "fix_hints": (
            "Add created-by, approved-by, and last-modified timestamp columns at the source system before export."
        ),
    },
    "sox_transaction_timestamp": {
        "what_it_means": (
            "Transaction timestamps prove when a financial event happened. Missing or invalid dates "
            "weaken an audit trail."
        ),
        "how_we_check": (
            "We identify columns that parse as dates or look like transaction timestamps."
        ),
        "fix_hints": (
            "Ensure every financial event has a reliable timestamp from the source system, not a blank or placeholder."
        ),
    },
    "ml_readiness": {
        "what_it_means": (
            "Forecast readiness assesses whether your time-series data is suitable for prediction models "
            "(such as demand or revenue forecasting). It is separate from the general data quality score — "
            "you can have clean data that is still too short or irregular for forecasting."
        ),
        "how_we_check": (
            "When you provide a target column (what to predict) and a date column, we evaluate history length, "
            "date spacing regularity, target completeness, and signs of data leakage that would inflate model accuracy."
        ),
        "fix_hints": (
            "Collect more history if the date range is short, fill or explain missing periods, fix irregular "
            "timestamps, and remove columns that would not be available at prediction time (leakage)."
        ),
    },
    "__overall__": {
        "what_it_means": (
            "This report is an automated health check of your spreadsheet. It scores how trustworthy the "
            "data is for reporting and analysis across completeness, accuracy, consistency, privacy, and related areas."
        ),
        "how_we_check": (
            "The engine runs a fixed pipeline of checks on every column, combines results into dimension scores, "
            "and produces one overall Data Quality Score from weighted averages — all rule-based, with optional "
            "AI explanations to help you interpret the numbers."
        ),
        "fix_hints": (
            "Start with Critical and High severity items in the check list below. "
            "Re-upload after fixes to track improvement over time."
        ),
    },
}


def guide_for(check_name: str) -> dict[str, str]:
    """Return the educational guide for a check, with safe defaults."""
    base = CHECK_GUIDE.get(check_name) or {
        "what_it_means": (
            "This section summarizes one automated quality check on your uploaded file."
        ),
        "how_we_check": (
            "The engine applies deterministic rules defined for this project and records pass/fail per column."
        ),
        "fix_hints": "Review flagged columns in the report tables and correct issues at the data source.",
    }
    return dict(base)


def format_section_lines(sections: dict[str, str]) -> str:
    """Join labeled sections into the text format the HTML modal parser expects."""
    lines: list[str] = []
    for label in EXPLANATION_SECTIONS:
        text = sections.get(label, "").strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _format_findings(summary: dict[str, Any]) -> str:
    """Turn summary stats into a novice-friendly findings paragraph."""
    severity = summary.get("severity", "Unknown")
    issues = int(summary.get("total_issues_found") or 0)
    cols_checked = summary.get("columns_checked", 0)
    cols_with_issues = summary.get("columns_with_issues", 0)
    affected = summary.get("affected_columns") or []
    affected_text = ", ".join(str(c) for c in affected[:8])
    if len(affected) > 8:
        affected_text += f", and {len(affected) - 8} more"

    if severity in ("None",) or (issues == 0 and cols_with_issues == 0):
        return (
            f"Good news — no significant issues were detected. We checked {cols_checked} column(s) "
            f"and none failed this check. Your data looks healthy for this dimension."
        )

    parts = [
        f"Severity: {severity}.",
        f"We found {issues} issue(s) across {cols_with_issues} of {cols_checked} column(s) checked.",
    ]
    if affected_text:
        parts.append(f"Columns most affected: {affected_text}.")
    samples = summary.get("sample_findings") or []
    if samples:
        sample_bits = []
        for s in samples[:3]:
            col = s.get("column", "?")
            count = s.get("issues_found")
            if count is not None:
                sample_bits.append(f"{col} ({count} issue(s))")
            else:
                sample_bits.append(str(col))
        if sample_bits:
            parts.append(f"Examples: {'; '.join(sample_bits)}.")
    er = summary.get("entity_resolution_summary")
    if er:
        parts.append(
            f"Resolution summary: {er.get('auto_match', 0)} auto-matched, "
            f"{er.get('review', 0)} need review, {er.get('no_match', 0)} unresolved "
            f"(of {er.get('total_values', 0)} values scanned)."
        )
    return " ".join(parts)
