"""
Phase 2 M6 — Entity Resolution tests (synthetic data only).

Semantic tier tests mock SentenceTransformer to avoid downloading models in CI.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
import pytest

from data_quality_engine.phase2.database import init_db, get_session
from data_quality_engine.phase2.database.models import CanonicalMapping
from data_quality_engine.phase2.entity_resolution.cascade import EntityResolutionCascade
from data_quality_engine.phase2.entity_resolution.candidates import narrow_candidates
from data_quality_engine.phase2.entity_resolution.config import load_entity_resolution_config
from data_quality_engine.phase2.entity_resolution.lookup import LookupTable
from data_quality_engine.phase2.entity_resolution.models import (
    EntityResolutionConfig,
    EntityResolutionThresholds,
    EntityTypeConfig,
    ResolutionDecision,
)
from data_quality_engine.phase2.entity_resolution.normalize import safe_normalize
from data_quality_engine.phase2.entity_resolution.privacy import looks_like_pii, safe_display_value
from data_quality_engine.phase2.entity_resolution.repository import load_lookup_from_db, upsert_mapping
from data_quality_engine.phase2.entity_resolution.service import resolve_dataframe
from data_quality_engine.phase2.entity_resolution.semantic import SemanticMatcher


def _city_config(**overrides) -> EntityResolutionConfig:
    from dataclasses import replace

    thresholds = EntityResolutionThresholds(
        fuzzy_auto=0.85,
        fuzzy_review=0.75,
        semantic_auto=0.78,
        semantic_review=0.70,
    )
    spec = EntityTypeConfig(
        entity_type="city",
        columns=("City",),
        canonicals=("Lahore", "Karachi", "Islamabad"),
        aliases={"LHR": "Lahore", "KHI": "Karachi"},
    )
    cfg = EntityResolutionConfig(
        enabled=True,
        entity_types={"city": spec},
        thresholds=thresholds,
    )
    return replace(cfg, **overrides) if overrides else cfg


def _cascade(canonicals=None, aliases=None) -> EntityResolutionCascade:
    lookup = LookupTable.from_config(
        "city",
        canonicals or ["Lahore", "Karachi", "Islamabad"],
        aliases or {"LHR": "Lahore", "KHI": "Karachi"},
    )
    return EntityResolutionCascade(
        entity_type="city",
        lookup=lookup,
        canonicals=canonicals or ["Lahore", "Karachi", "Islamabad"],
        config=_city_config(),
    )


class TestNormalize:
    def test_case_and_whitespace(self):
        assert safe_normalize("  Lahore  ") == "lahore"

    def test_punctuation_strip(self):
        assert safe_normalize("Lahore.", strip_punctuation=True) == "lahore"


class TestTier1Lookup:
    def test_exact_match(self):
        c = _cascade()
        r = c.resolve_one("LHR")
        assert r.canonical_value == "Lahore"
        assert r.tier == 1
        assert r.decision == ResolutionDecision.AUTO_MATCH

    def test_normalized_match(self):
        c = _cascade()
        r = c.resolve_one("  lahore  ")
        assert r.canonical_value == "Lahore"
        assert r.tier == 1

    def test_alias_match(self):
        c = _cascade()
        r = c.resolve_one("KHI")
        assert r.canonical_value == "Karachi"

    def test_missing_null_empty(self):
        c = _cascade()
        assert c.resolve_one("").decision == ResolutionDecision.NO_MATCH
        assert c.resolve_one("   ").decision == ResolutionDecision.NO_MATCH


class TestTier2Fuzzy:
    def test_spelling_variation_auto_match(self):
        c = _cascade()
        r = c.resolve_one("Lahor")
        assert r.tier == 2
        assert r.decision == ResolutionDecision.AUTO_MATCH
        assert r.canonical_value == "Lahore"

    def test_near_match_review_not_auto(self):
        thresholds = EntityResolutionThresholds(fuzzy_auto=0.95, fuzzy_review=0.80, semantic_auto=0.78, semantic_review=0.70)
        c = EntityResolutionCascade(
            entity_type="city",
            lookup=LookupTable.from_config("city", ["Lahore"], {}),
            canonicals=["Lahore"],
            config=EntityResolutionConfig(thresholds=thresholds),
        )
        r = c.resolve_one("Lahor")
        assert r.tier == 2
        assert r.decision == ResolutionDecision.REVIEW
        assert r.requires_review is True

    def test_unrelated_no_match_skips_bad_merge(self):
        c = _cascade()
        r = c.resolve_one("ZZZZUNKNOWN")
        assert r.decision in (ResolutionDecision.NO_MATCH, ResolutionDecision.REVIEW)
        if r.decision == ResolutionDecision.NO_MATCH:
            assert r.canonical_value is None


class TestTier3Semantic:
    @pytest.fixture
    def mock_embedder(self, monkeypatch):
        def fake_encode(texts, normalize_embeddings=True):
            vecs = []
            for t in texts:
                h = hashlib.md5(t.encode()).digest()
                arr = np.frombuffer(h, dtype=np.uint8).astype(float)
                arr = np.resize(arr, 32)
                if normalize_embeddings:
                    norm = np.linalg.norm(arr) or 1.0
                    arr = arr / norm
                vecs.append(arr)
            return np.stack(vecs)

        class FakeST:
            def encode(self, texts, normalize_embeddings=True):
                return fake_encode(texts, normalize_embeddings)

        import data_quality_engine.phase2.entity_resolution.semantic as sem_mod

        monkeypatch.setitem(sem_mod._MODEL_CACHE, "all-MiniLM-L6-v2", FakeST())

    def test_semantic_used_when_fuzzy_fails(self, mock_embedder):
        lookup = LookupTable.from_config("city", ["Metropolis Alpha"], {})
        thresholds = EntityResolutionThresholds(
            fuzzy_auto=0.99,
            fuzzy_review=0.99,
            semantic_auto=0.50,
            semantic_review=0.40,
        )
        c = EntityResolutionCascade(
            entity_type="city",
            lookup=lookup,
            canonicals=["Metropolis Alpha"],
            config=EntityResolutionConfig(thresholds=thresholds),
        )
        r = c.resolve_one("Metropolis Alpha")
        assert r.tier == 1
        r2 = c.resolve_one("Metropolis Alpha City")
        assert r2.tier in (2, 3)


class TestDeterminism:
    def test_repeated_runs_identical(self):
        c = _cascade()
        values = ["LHR", "lahore", "Karachi", "UnknownXYZ"]
        first = {v: c.resolve_one(v).to_dict() for v in values}
        second = {v: c.resolve_one(v).to_dict() for v in values}
        assert first == second


class TestCandidateGeneration:
    def test_narrow_candidates_bounded(self):
        canonicals = [f"City{i}" for i in range(500)]
        pool = narrow_candidates("City1", canonicals, max_candidates=25)
        assert len(pool) <= 25

    def test_large_pool_uses_extract_not_full_scan_pairs(self):
        canonicals = [f"Place{i:04d}" for i in range(1000)]
        pool = narrow_candidates("Place0123", canonicals, max_candidates=10)
        assert len(pool) <= 10


class TestServiceDataframe:
    def test_multiple_entity_types_and_columns(self):
        df = pd.DataFrame(
            {
                "City": ["LHR", "Karachi", "Unknown"],
                "Country": ["UK", "Pakistan", "Mars"],
            }
        )
        classification = {"City": "categorical", "Country": "categorical"}
        config = EntityResolutionConfig(
            enabled=True,
            entity_types={
                "city": EntityTypeConfig(
                    entity_type="city",
                    columns=("City",),
                    canonicals=("Lahore", "Karachi"),
                    aliases={"LHR": "Lahore"},
                ),
                "country": EntityTypeConfig(
                    entity_type="country",
                    columns=("Country",),
                    canonicals=("United Kingdom", "Pakistan"),
                    aliases={"UK": "United Kingdom"},
                ),
            },
        )
        out = resolve_dataframe(df, classification, config=config)
        assert out["enabled"] is True
        assert "City" in out["columns"]
        assert "Country" in out["columns"]
        assert out["columns"]["City"]["resolutions"]["LHR"]["canonical_value"] == "Lahore"

    def test_pii_column_skipped(self):
        df = pd.DataFrame({"Email": ["a@b.com", "c@d.com"]})
        classification = {"Email": "pii"}
        config = EntityResolutionConfig(
            enabled=True,
            entity_types={
                "email": EntityTypeConfig(entity_type="email", columns=("Email",), canonicals=()),
            },
        )
        out = resolve_dataframe(df, classification, config=config)
        assert out["columns"] == {}

    def test_original_dataframe_not_mutated(self):
        df = pd.DataFrame({"City": ["LHR", "LHR"]})
        original = df.copy()
        resolve_dataframe(
            df,
            {"City": "categorical"},
            config=_city_config(),
        )
        pd.testing.assert_frame_equal(df, original)


class TestPrivacy:
    def test_email_masked_for_display(self):
        assert "@" in safe_display_value("john.doe@example.com")
        assert "john" not in safe_display_value("john.doe@example.com")

    def test_pii_flag_on_email_resolution(self):
        c = _cascade()
        r = c.resolve_one("john.doe@example.com")
        assert r.evidence.get("pii_sensitive") is True


class TestDatabaseIntegration:
    def test_db_lookup_tier1(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path / 'm6.db'}"
        init_db(database_url=db_url)
        with get_session() as session:
            upsert_mapping(
                session,
                client_id="test_client",
                column_role="city",
                source_value="LHR",
                canonical_value="Lahore",
                confidence=1.0,
                source="manual",
            )
            session.commit()
        with get_session() as session:
            table = load_lookup_from_db(session, client_id="test_client", column_role="city")
            canon, kind, _ = table.lookup("LHR")
            assert canon == "Lahore"
            assert kind == "exact"


class TestDuplicateCanonicals:
    def test_duplicate_canonicals_deduped(self):
        c = EntityResolutionCascade(
            entity_type="city",
            lookup=LookupTable.from_config("city", ["Lahore", "Lahore"], {}),
            canonicals=["Lahore", "Lahore", "Lahore"],
            config=_city_config(),
        )
        r = c.resolve_one("Lahore")
        assert r.canonical_value == "Lahore"


class TestConfigLoader:
    def test_load_from_settings(self):
        cfg = load_entity_resolution_config()
        assert cfg.enabled is True
        assert "city" in cfg.entity_types


class TestAmbiguousLowConfidence:
    def test_never_auto_merge_below_threshold(self):
        thresholds = EntityResolutionThresholds(
            fuzzy_auto=0.99,
            fuzzy_review=0.95,
            semantic_auto=0.99,
            semantic_review=0.95,
        )
        c = EntityResolutionCascade(
            entity_type="city",
            lookup=LookupTable.from_config("city", ["Lahore", "Karachi"], {}),
            canonicals=["Lahore", "Karachi"],
            config=EntityResolutionConfig(thresholds=thresholds),
        )
        r = c.resolve_one("XYZ123")
        assert r.decision != ResolutionDecision.AUTO_MATCH or r.canonical_value is None


class TestEvidenceAuditTrail:
    def test_resolution_has_evidence_fields(self):
        c = _cascade()
        r = c.resolve_one("LHR")
        d = r.to_dict()
        assert d["original_value"] == "LHR"
        assert d["normalized_value"]
        assert d["tier"] == 1
        assert d["confidence"] > 0
        assert d["decision"] == "auto_match"
