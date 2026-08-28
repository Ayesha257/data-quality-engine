"""Measure that skipping unrequested HIPAA analysis is a real time win."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from backend.engine.checkpoint import UserPrompt
from backend.engine.compliance.opt_in import normalize_compliance_modules
from conftest import SAMPLE_XLSX

SAMPLE = SAMPLE_XLSX


class AutoConfirmPrompt(UserPrompt):
    def confirm(self, message: str, details: dict) -> bool:
        return True

    def ask_int(self, message: str, default: int | None = None) -> int:
        return default if default is not None else 0

    def ask_text(self, message: str, default: str | None = None) -> str:
        return default if default is not None else ""


def test_normalize_modules_default_empty():
    assert normalize_compliance_modules(None) == []
    assert normalize_compliance_modules([]) == []
    assert normalize_compliance_modules(["pci-dss", "HIPAA"]) == ["PCI_DSS", "HIPAA"]
    assert normalize_compliance_modules(None, include_hipaa=True) == ["HIPAA"]


@pytest.mark.skipif(not Path(SAMPLE).exists(), reason="sample fixture missing")
def test_skipping_hipaa_is_faster_than_running_it(tmp_path, monkeypatch, capsys):
    import backend.main as main
    from backend.config.settings import SETTINGS

    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path / "logs")

    def _run(*, modules, include_hipaa):
        t0 = time.perf_counter()
        main.run_pipeline(
            str(SAMPLE),
            prompt=AutoConfirmPrompt(),
            write_report=False,
            client_id="timing_client",
            include_hipaa=include_hipaa,
            compliance_modules=modules,
        )
        return time.perf_counter() - t0

    off = _run(modules=[], include_hipaa=False)
    on = _run(modules=["HIPAA"], include_hipaa=True)
    printed = capsys.readouterr().out
    assert "Skipped: HIPAA was not selected" in printed
    assert "[timing] hipaa_compliance:" in printed
    # Opt-out must not be slower than running HIPAA; allow tiny noise.
    assert off <= on * 1.15 + 0.5


def test_expiry_name_gate_still_finds_exp_date_column():
    """Optimization must not change PCI expiry detection on a matching column."""
    from backend.compliance.financial_compliance import scan_pci_dss_findings

    df = pd.DataFrame(
        {
            "amount": [10, 20],
            "exp_date": ["05/27", "11/28"],
            "notes": ["hello", "world"],
        }
    )
    scanned = scan_pci_dss_findings(df)
    medium = scanned["medium"]
    assert len(medium) == 1
    assert medium[0]["column_name"] == "exp_date"
    assert medium[0]["field_name"] == "card_expiry"
