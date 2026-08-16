"""
Phase 2 — M1 (Foundations) integration tests.

Run just these with:
    pytest tests/test_phase2_m1_setup.py -v

These tests exercise, end to end, everything M1 delivers:
    - database tables get created and can store/query rows
    - JSONL logging + run manifests get written and can be read back
    - rule resolution merges base + per-client YAML correctly
    - Pydantic schemas validate good input and reject bad input

Every test uses a temporary directory (via the `tmp_path` pytest fixture)
so nothing here touches your real logs/ or config/ folders, and tests
never depend on each other's state.
"""

from __future__ import annotations

import json

import pytest
import yaml

from backend.database import get_session, init_db
from backend.database.models import (
    CanonicalMapping,
    Disposition,
    DispositionType,
    Rating,
    RunManifest,
    RunRecord,
    RunStatus,
)
from backend.logging import (
    get_run_logger,
    init_logging,
    log_event,
    new_run_id,
    query_runs_by_client,
    read_run_manifest,
    write_run_manifest,
)
from backend.services.rules import RuleResolutionError, RuleResolver
from backend.schemas.models import (
    CanonicalMappingCreate,
    DispositionCreate,
    FindingSummary,
    RatingCreate,
    RunCreateRequest,
    RunRecordSchema,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# 1. Database layer
# ---------------------------------------------------------------------------

class TestDatabaseSetup:
    def test_init_db_creates_engine_and_tables(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        engine = init_db(database_url=db_url)
        table_names = set(engine.dialect.get_table_names(engine.connect()))
        assert {
            "run_records",
            "run_manifests",
            "canonical_mappings",
            "dispositions",
            "ratings",
        }.issubset(table_names)

    def test_run_record_insert_and_query(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(database_url=db_url)

        with get_session() as session:
            run = RunRecord(
                client_id="acme",
                file_name="orders.xlsx",
                status=RunStatus.COMPLETED,
                overall_score=87.5,
                dimension_scores={"completeness": 90.0, "validity": 85.0},
            )
            session.add(run)

        with get_session() as session:
            fetched = session.query(RunRecord).filter_by(client_id="acme").one()
            assert fetched.file_name == "orders.xlsx"
            assert fetched.overall_score == 87.5
            assert fetched.dimension_scores["completeness"] == 90.0

    def test_run_manifest_relationship(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(database_url=db_url)

        with get_session() as session:
            run = RunRecord(client_id="acme", file_name="orders.xlsx")
            session.add(run)
            session.flush()  # get run.id without committing yet
            manifest = RunManifest(
                run_id=run.id,
                checks_run=["missing_values", "duplicates"],
                ruleset_snapshot={"version": "v1"},
            )
            session.add(manifest)
            run_id = run.id

        with get_session() as session:
            fetched = session.get(RunRecord, run_id)
            assert fetched.manifest is not None
            assert "missing_values" in fetched.manifest.checks_run

    def test_canonical_mapping_roundtrip(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(database_url=db_url)

        with get_session() as session:
            mapping = CanonicalMapping(
                client_id="acme",
                column_role="city",
                source_value="LHR",
                canonical_value="Lahore",
                confidence=0.95,
                source="fuzzy",
            )
            session.add(mapping)

        with get_session() as session:
            fetched = session.query(CanonicalMapping).filter_by(source_value="LHR").one()
            assert fetched.canonical_value == "Lahore"

    def test_disposition_and_rating_insert(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        init_db(database_url=db_url)

        with get_session() as session:
            run = RunRecord(client_id="acme", file_name="orders.xlsx")
            session.add(run)
            session.flush()
            session.add(
                Disposition(
                    run_id=run.id,
                    finding_id="outliers:Amount",
                    disposition=DispositionType.FALSE_POSITIVE,
                    note="Known bulk order, not an outlier.",
                )
            )
            session.add(Rating(run_id=run.id, finding_id="outliers:Amount", rating=4))
            run_id = run.id

        with get_session() as session:
            fetched = session.get(RunRecord, run_id)
            assert len(fetched.dispositions) == 1
            assert fetched.dispositions[0].disposition == DispositionType.FALSE_POSITIVE
            assert fetched.ratings[0].rating == 4


# ---------------------------------------------------------------------------
# 2. Logging + manifests
# ---------------------------------------------------------------------------

class TestLoggingSetup:
    def test_init_logging_creates_directories(self, tmp_path):
        logs_dir = tmp_path / "logs"
        cfg = init_logging(logs_dir=logs_dir)
        assert cfg.logs_dir.exists()
        assert cfg.manifests_dir.exists()

    def test_run_id_is_unique(self, tmp_path):
        init_logging(logs_dir=tmp_path / "logs")
        ids = {new_run_id() for _ in range(50)}
        assert len(ids) == 50  # all unique

    def test_run_logger_writes_jsonl(self, tmp_path):
        logs_dir = tmp_path / "logs"
        init_logging(logs_dir=logs_dir)
        run_id = new_run_id()
        logger = get_run_logger(run_id, client_id="acme")
        log_event(logger, 20, "Started ingestion", run_id=run_id, step="ingestion", client_id="acme")

        log_file = logs_dir / f"phase2_run_{run_id}.jsonl"
        assert log_file.exists()
        line = json.loads(log_file.read_text().strip().splitlines()[0])
        assert line["run_id"] == run_id
        assert line["step"] == "ingestion"
        assert line["message"] == "Started ingestion"

    def test_write_and_read_run_manifest(self, tmp_path):
        init_logging(logs_dir=tmp_path / "logs")
        run_id = new_run_id()
        write_run_manifest(
            run_id=run_id,
            client_id="acme",
            file_name="orders.xlsx",
            ruleset_version="v1",
            checks_run=["missing_values", "duplicates", "outliers"],
            status="completed",
            started_at="2026-08-06T00:00:00Z",
            completed_at="2026-08-06T00:01:00Z",
        )
        manifest = read_run_manifest(run_id)
        assert manifest["client_id"] == "acme"
        assert manifest["checks_run"] == ["missing_values", "duplicates", "outliers"]

    def test_query_runs_by_client_filters_correctly(self, tmp_path):
        init_logging(logs_dir=tmp_path / "logs")
        for client, fname in [("acme", "a.xlsx"), ("acme", "b.xlsx"), ("globex", "c.xlsx")]:
            write_run_manifest(
                run_id=new_run_id(),
                client_id=client,
                file_name=fname,
                ruleset_version="v1",
                checks_run=[],
                status="completed",
                started_at="2026-08-06T00:00:00Z",
            )
        acme_runs = query_runs_by_client("acme")
        assert len(acme_runs) == 2
        assert all(r["client_id"] == "acme" for r in acme_runs)


# ---------------------------------------------------------------------------
# 3. Rule resolution
# ---------------------------------------------------------------------------

class TestRuleResolution:
    @pytest.fixture
    def config_dir(self, tmp_path):
        cfg = tmp_path / "config"
        (cfg / "clients" / "acme").mkdir(parents=True)
        base = {
            "client_id": "__base__",
            "version": "v1.0",
            "thresholds": {"fuzzy_threshold": 90, "min_acceptable_overall_score": 60.0},
            "business_rules": [
                {"rule_id": "r1", "description": "d1", "condition": "c1", "severity": "medium"}
            ],
        }
        (cfg / "base_rules.yaml").write_text(yaml.dump(base))

        override = {
            "client_id": "acme",
            "version": "v1",
            "thresholds": {"fuzzy_threshold": 80},  # only overrides ONE key
        }
        (cfg / "clients" / "acme" / "rules_v1.yaml").write_text(yaml.dump(override))
        return cfg

    def test_resolve_merges_base_and_client(self, config_dir):
        resolver = RuleResolver(config_dir=config_dir)
        resolved = resolver.resolve("acme")
        assert resolved["thresholds"]["fuzzy_threshold"] == 80  # overridden
        assert resolved["thresholds"]["min_acceptable_overall_score"] == 60.0  # inherited
        assert resolved["business_rules"][0]["rule_id"] == "r1"  # inherited (untouched list)

    def test_resolve_falls_back_to_base_for_unknown_client(self, config_dir):
        resolver = RuleResolver(config_dir=config_dir)
        resolved = resolver.resolve("nonexistent_client")
        assert resolved["thresholds"]["fuzzy_threshold"] == 90  # base value, unmodified
        # No client override exists, so the resolved version is whatever
        # base_rules.yaml declares itself (here "v1.0") — not a magic
        # string, so a run's manifest always shows a real version number.
        assert resolved["version"] == "v1.0"

    def test_dry_run_does_not_populate_cache(self, config_dir):
        resolver = RuleResolver(config_dir=config_dir)
        resolver.resolve("acme", dry_run=True)
        assert "acme" not in resolver._cache

    def test_resolve_uses_cache_on_second_call(self, config_dir):
        resolver = RuleResolver(config_dir=config_dir)
        first = resolver.resolve("acme")
        # Mutate the file on disk; cached call should NOT pick this up.
        (config_dir / "clients" / "acme" / "rules_v1.yaml").write_text(
            yaml.dump({"client_id": "acme", "version": "v1", "thresholds": {"fuzzy_threshold": 1}})
        )
        second = resolver.resolve("acme")
        assert second["thresholds"]["fuzzy_threshold"] == first["thresholds"]["fuzzy_threshold"]

    def test_missing_base_ruleset_raises(self, tmp_path):
        empty_dir = tmp_path / "empty_config"
        empty_dir.mkdir()
        resolver = RuleResolver(config_dir=empty_dir)
        with pytest.raises(RuleResolutionError):
            resolver.resolve("acme")


# ---------------------------------------------------------------------------
# 4. Pydantic schemas
# ---------------------------------------------------------------------------

class TestPydanticSchemas:
    def test_run_create_request_accepts_valid_input(self):
        req = RunCreateRequest(client_id="acme", file_name="orders.xlsx")
        assert req.client_id == "acme"

    def test_run_create_request_rejects_bad_client_id(self):
        with pytest.raises(ValidationError):
            RunCreateRequest(client_id="a", file_name="orders.xlsx")  # too short

    def test_run_create_request_rejects_bad_file_extension(self):
        with pytest.raises(ValidationError):
            RunCreateRequest(client_id="acme", file_name="orders.txt")

    def test_finding_summary_rejects_out_of_range_ratio(self):
        with pytest.raises(ValidationError):
            FindingSummary(
                check_name="missing_values",
                column="Email",
                status="failed",
                issues_found=3,
                quality_ratio=1.5,  # invalid: must be 0..1
            )

    def test_rating_create_enforces_1_to_5(self):
        with pytest.raises(ValidationError):
            RatingCreate(run_id="r1", finding_id="f1", rating=9)
        ok = RatingCreate(run_id="r1", finding_id="f1", rating=5)
        assert ok.rating == 5

    def test_disposition_create_accepts_valid_enum(self):
        d = DispositionCreate(run_id="r1", finding_id="f1", disposition="false_positive")
        assert d.disposition.value == "false_positive"

    def test_canonical_mapping_create_confidence_bounds(self):
        with pytest.raises(ValidationError):
            CanonicalMappingCreate(
                client_id="acme",
                column_role="city",
                source_value="LHR",
                canonical_value="Lahore",
                confidence=1.2,
            )

    def test_run_record_schema_reads_from_orm_object(self):
        from backend.database.models import RunRecord as ORMRunRecord

        orm_row = ORMRunRecord(
            id="r1",
            client_id="acme",
            file_name="orders.xlsx",
            status=RunStatus.COMPLETED,
            started_at="2026-08-06T00:00:00Z",
        )
        schema = RunRecordSchema.model_validate(orm_row)
        assert schema.id == "r1"
        assert schema.status == "completed"
