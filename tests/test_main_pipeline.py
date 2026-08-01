"""End-to-end test: main.run_pipeline covers Tasks 1-6 on sample data."""

from __future__ import annotations

import json
from pathlib import Path

from data_quality_engine.engine.checkpoint import UserPrompt

import main


class AutoConfirmPrompt(UserPrompt):
    """Always accepts the detected header row / full processing scope."""

    def confirm(self, message: str, details: dict) -> bool:
        return True

    def ask_int(self, message: str, default: int | None = None) -> int:
        return default if default is not None else 0

    def ask_text(self, message: str, default: str | None = None) -> str:
        return default if default is not None else ""


SAMPLE = Path(__file__).resolve().parents[1] / "src" / "sample_data" / "sample_data.xlsx"


def test_run_pipeline_completes_all_tasks(capsys):
    assert SAMPLE.exists(), f"sample data missing: {SAMPLE}"

    main.run_pipeline(str(SAMPLE), prompt=AutoConfirmPrompt())

    out = capsys.readouterr().out
    assert "Encoding Check (CSV bytes)" in out
    assert "Skipped: not a CSV file" in out
    assert "Task 2 Results" in out
    assert "Task 3 Results (Outlier Detection)" in out
    assert "Task 4 Results (PII Detection & Masking)" in out
    assert "Plan Task 5 Results (Fuzzy Text Standardization)" in out
    assert "Task 5 Results (Schema, Consistency, Validity, Freshness)" in out
    assert "Task 6 Results (Data Quality Score)" in out
    assert "Data Quality Score:" in out
    assert "Privacy Risk (separate -- never part of the score above)" in out
    assert "Done: Task 1-6 completed." in out


def test_run_pipeline_writes_jsonl_log(tmp_path, monkeypatch):
    # Redirect logs to a temp dir so the test doesn't depend on / pollute
    # the repo's real logs/ directory.
    from data_quality_engine.config.settings import SETTINGS

    monkeypatch.setitem(SETTINGS, "logs_dir", tmp_path)

    main.run_pipeline(str(SAMPLE), prompt=AutoConfirmPrompt())

    log_files = list(tmp_path.glob("run_*.jsonl"))
    assert len(log_files) == 1

    content = log_files[0].read_text(encoding="utf-8").strip()
    assert content, "expected at least one JSONL log line"

    line = json.loads(content.splitlines()[0])
    assert line["details"]["file"] == "sample_data.xlsx"
    assert "checks_run" in line["details"]
    assert set(line["details"]["checks_run"]) == {
        "encoding",
        "column_classification",
        "missing_values",
        "duplicates",
        "type_mismatch",
        "outliers",
        "pii",
        "fuzzy_standardization",
        "schema_quality",
        "consistency",
        "validity",
        "freshness",
        "scoring",
    }
    assert "data_quality_score" in line["details"]
    assert "privacy_risk_level" in line["details"]
