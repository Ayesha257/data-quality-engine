"""Normalize scan-time compliance module opt-in flags.

Default is nothing selected: HIPAA and financial detectors do not run
unless the caller lists them. Detection implementations are unchanged;
this module only decides which ones are invoked.
"""

from __future__ import annotations

from typing import Iterable

ALLOWED_COMPLIANCE_MODULES = ("HIPAA", "PCI_DSS", "GLBA", "SOX", "GDPR", "CCPA")

_ALIASES = {
    "PCI": "PCI_DSS",
    "PCIDSS": "PCI_DSS",
    "PCI-DSS": "PCI_DSS",
    "CPRA": "CCPA",
    "CCPA_CPRA": "CCPA",
    "CCPA-CPRA": "CCPA",
    "CCPA/CPRA": "CCPA",
}


def normalize_compliance_modules(
    raw: Iterable[str] | str | None,
    *,
    include_hipaa: bool = False,
) -> list[str]:
    """Return a de-duplicated list of allowed module ids, preserving order."""
    if raw is None:
        items: list[str] = []
    elif isinstance(raw, str):
        items = [part for part in raw.replace(";", ",").split(",") if part.strip()]
    else:
        items = []
        for x in raw:
            if x is not None:
                for part in str(x).replace(";", ",").split(","):
                    if part.strip():
                        items.append(part.strip())

    out: list[str] = []
    for item in items:
        token = item.strip().upper().replace("-", "_").replace(" ", "_")
        token = _ALIASES.get(token, token)
        token = _ALIASES.get(item.strip().upper(), token)
        if token in ALLOWED_COMPLIANCE_MODULES and token not in out:
            out.append(token)

    # Legacy include_hipaa=True still means "run HIPAA detectors" so the
    # main report's HIPAA section has data. It does not enable financial
    # modules and does not by itself write a standalone HIPAA file unless
    # HIPAA is also listed (GET still requires HIPAA in this list).
    if include_hipaa and "HIPAA" not in out:
        out.append("HIPAA")
    return out
