"""
Phase 2 — M3: ML Readiness Assessment (PHASE2_PLAN.md, "M3: ML Readiness
Assessment").

Answers "can this dataset support Prophet forecasting" independently of
the Section 5 general data-quality composite score. Every module here
follows the same rules Phase 1 checks and phase2/ai_explainer.py /
phase2/rules.py already follow:

    - Deterministic. No AI, no randomness.
    - Never raises on bad input (missing column, empty df, wrong dtype,
      etc.) -- always returns the module's result dataclass with
      `blockers` (or, for leakage, `concern_level`) populated instead of
      an exception.
    - Fully generic. No dataset-specific logic; every function works on
      any DataFrame with a plausible date/target column.
    - phase2/ only ever calls into engine/, never the other way around
      (see PHASE2_PLAN.md section 2.14). Nothing in this package imports
      from backend.engine.

Modules:
    temporal.py   analyze_temporal_sufficiency()  -> TemporalAnalysis
    intervals.py  analyze_interval_regularity()   -> IntervalAnalysis
    target.py     analyze_target_integrity()      -> TargetAnalysis
    leakage.py    analyze_leakage_and_cardinality()-> LeakageAnalysis
    scorer.py     score_readiness()               -> ReadinessScore
"""

from __future__ import annotations

from backend.engine.readiness.intervals import (
    IntervalAnalysis,
    analyze_interval_regularity,
)
from backend.engine.readiness.leakage import (
    LeakageAnalysis,
    analyze_leakage_and_cardinality,
)
from backend.engine.readiness.scorer import ReadinessScore, score_readiness
from backend.engine.readiness.target import TargetAnalysis, analyze_target_integrity
from backend.engine.readiness.temporal import (
    TemporalAnalysis,
    analyze_temporal_sufficiency,
)

__all__ = [
    "TemporalAnalysis",
    "analyze_temporal_sufficiency",
    "IntervalAnalysis",
    "analyze_interval_regularity",
    "TargetAnalysis",
    "analyze_target_integrity",
    "LeakageAnalysis",
    "analyze_leakage_and_cardinality",
    "ReadinessScore",
    "score_readiness",
]
