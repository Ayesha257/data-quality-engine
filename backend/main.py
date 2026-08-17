from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime
from pathlib import Path

from backend.engine.checkpoint import (
    CLIPrompt,
    UserPrompt,
    confirm_header_row,
    confirm_processing_scope,
    apply_scope,
)
from backend.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    header_preview,
    load_with_confirmed_header,
    get_sheet_visibility,
)
from backend.engine.checks.missing_values import check_missing_values
from backend.engine.checks.duplicates import check_duplicates_frame
from backend.engine.checks.type_mismatch import check_type_consistency_frame
from backend.engine.checks.outliers import detect_outliers_frame
from backend.engine.checks.schema_quality import check_schema_quality
from backend.engine.checks.consistency import check_consistency_frame
from backend.engine.checks.validity import check_validity_frame
from backend.engine.checks.freshness import check_freshness_frame
from backend.engine.checks.encoding import check_encoding
from backend.engine.checks.referential_integrity import (
    check_referential_integrity,
)
from backend.config import domain_rules
from backend.engine.column_classifier import classify_columns
from backend.config.settings import SETTINGS
from backend.engine.pii.detect_pii import detect_pii_in_series
from backend.engine.standardization.fuzzy_match import (
    check_fuzzy_standardization_frame,
    standardize_frame,
)
from backend.engine.scoring import compute_data_quality_score
from backend.logging import get_logger, log_event
from backend.engine.readiness.scorer import score_readiness
from backend.engine.entity_resolution import (
    load_entity_resolution_config,
    resolve_dataframe,
)
from backend.engine.readiness.temporal import analyze_temporal_sufficiency
from backend.engine.readiness.intervals import analyze_interval_regularity
from backend.engine.readiness.target import analyze_target_integrity
from backend.engine.readiness.leakage import analyze_leakage_and_cardinality
from backend.engine.compliance import (
    assess_hipaa_compliance,
    assess_hipaa_compliance_as_check_results,
    score_hipaa_compliance,
    build_compliance_report_data,
    generate_compliance_html_report,
)
from backend.engine.reports.report_generator import build_report_data
from backend.engine.reports.pdf_report import generate_pdf_report
from backend.engine.ai_explanation.enhanced_report import generate_ai_enhanced_html_report
from backend.database import history
import logging

# NOTE: the legacy (non-AI) HTML report generator --
#   from backend.engine.report import generate_html_report, write_html_report
# has been intentionally removed from this file. The Phase 2 AI-enhanced
# report (built via build_report_data() + generate_ai_enhanced_html_report())
# is now the ONLY report --report writes. See _write_ai_enhanced_report()
# and the "if write_report:" block inside run_pipeline() below.


def _print_encoding_check(filepath: str):
    """
    Encoding detection on CSV raw bytes only (plan.md Section 10 item 6).

    Not labeled as a numbered Task — avoids colliding with rubric Task 5/6.
    Excel paths are skipped explicitly (already decoded by openpyxl/xlrd).
    """
    print("\n=== Encoding Check (CSV bytes) ===")
    path = Path(filepath)
    if path.suffix.lower() != ".csv":
        print(
            "Skipped: not a CSV file. Excel workbooks are already decoded by "
            "openpyxl/xlrd/calamine — there are no raw text-encoding bytes to sniff."
        )
        return {"encoding_result": None, "skipped": True}

    sample_size = int(SETTINGS.get("encoding_sample_size", 100_000))
    sample = path.read_bytes()[:sample_size]
    result = check_encoding(sample)
    print(f"Status     : {result.status}")
    print(f"Encoding   : {result.details.get('encoding')}")
    print(f"Confidence : {result.details.get('confidence')}")
    print(f"Threshold  : {result.details.get('confidence_threshold')}")
    print(f"Sample len : {result.details.get('sample_length')} / file bytes read for sniff")
    if result.details.get("low_confidence"):
        print(f"Flag       : {result.details.get('flag')}")
        print(f"Recommended: {result.details.get('recommended_encoding')}")
        print(f"Fallback   : {result.details.get('fallback_tried')}")
    if result.status == "error":
        print(f"Error      : {result.details.get('error')}")
    return {"encoding_result": result, "skipped": False}


def _print_classification_results(df):
    """
    Column classification, run right after Task 1 (header detection) and
    before any check that depends on knowing a column's role -- most
    importantly outliers, which must not run mean/std/IQR on identifier or
    PII columns.

    This is a *visible* pipeline step (previously classify_columns() was
    only called implicitly, inside detect_outliers_frame()). Task 3 still
    calls classify_columns() itself internally too -- that keeps
    detect_outliers_frame() correct and testable in isolation even if
    someone calls it directly outside this pipeline. The small re-computation
    cost here buys an explicit, inspectable step in the printed report.
    """
    print("\n=== Column Classification (runs after Task 1, before Task 2-4) ===")
    roles = classify_columns(df)

    print("-" * 70)
    print(f"{'Column':40} {'Role':>20}")
    print("-" * 70)
    role_counts: dict[str, int] = {}
    for col, role in roles.items():
        role_counts[role] = role_counts.get(role, 0) + 1
        print(f"{str(col):40} {role:>20}")
    print("-" * 70)
    print("Role counts:", ", ".join(f"{r}={c}" for r, c in sorted(role_counts.items())))
    print(
        "Why this matters: measurement columns get real IQR/KNN outlier stats; "
        "identifier/pii/date/categorical/free_text columns are skipped in Task 3 "
        "(see 'skipped_non_measurement_column' below) since mean/std on an invoice "
        "number or phone number is meaningless."
    )
    return roles


def _business_key_columns(df) -> list[str] | None:
    """
    Pick compound uniqueness key when an ID + site/address distinguisher exist.

    Customer List-style ERP sheets repeat Customer No. across real distinct
    sites (Add. Code). Using ID alone false-flags those as duplicates; ID +
    site code is the business key. Returns None to let check_duplicates_frame
    fall back to infer_uniqueness_keys().
    """
    lower_map = {str(c).strip().lower(): c for c in df.columns}

    id_col = None
    for name, col in lower_map.items():
        if (
            "customer no" in name
            or "supplier code" in name
            or "supplier no" in name
            or name in {"customer no.", "customer no"}
        ):
            id_col = col
            break
    if id_col is None:
        return None

    site_keys = {
        "add. code",
        "add code",
        "site",
        "site code",
        "location code",
        "warehouse",
    }
    site_col = None
    for name, col in lower_map.items():
        if name in site_keys or name.endswith("add. code"):
            site_col = col
            break

    if site_col is not None:
        return [id_col, site_col]
    return [id_col]


def _print_top_results(df):
    print("\n=== Task 2 Results (Detailed) ===")
    print(f"Rows: {len(df)} | Columns: {df.shape[1]}")

    # ---- Missing ----
    missing = check_missing_values(df)
    print("\n[1] Missing Values  (Completeness)")
    print("-" * 70)
    print(f"{'Column':40} {'Missing':>10} {'%':>10} {'Status':>10}")
    print("-" * 70)
    total_missing = 0
    for r in missing:
        total_missing += r.issues_found
        pct = r.details.get("missing_pct", 0)
        print(f"{str(r.column):40} {r.issues_found:10d} {pct:10.4f} {r.status:>10}")
    print("-" * 70)
    print(f"TOTAL missing cells: {total_missing}")
    print(f"Columns with missing: {sum(1 for r in missing if r.issues_found > 0)} / {len(missing)}")

    # ---- Duplicates (full-row + business-key uniqueness) ----
    business_key = _business_key_columns(df)
    dup_results = check_duplicates_frame(df, key_columns=business_key)
    full_row_dup = next(
        (r for r in dup_results if r.check_name == "duplicates"),
        dup_results[0],
    )
    key_dups = [r for r in dup_results if r.check_name == "duplicate_keys"]

    print("\n[2] Duplicates  (Uniqueness)")
    print("-" * 70)
    if business_key:
        print(f"Business key used   : {business_key}")
    else:
        print("Business key used   : (auto-inferred single-column keys)")
    print("Full-row exact duplicates")
    print(f"  Status              : {full_row_dup.status}")
    print(f"  Extra duplicate rows: {full_row_dup.issues_found}  (keep='first')")
    print(
        f"  Rows in dup sets    : {full_row_dup.details.get('duplicate_set_rows', full_row_dup.issues_found)}"
    )
    print(f"  Total rows          : {full_row_dup.details.get('total_rows')}")
    sample_idx = full_row_dup.details.get("row_indices", [])
    set_idx = full_row_dup.details.get("duplicate_set_indices", sample_idx)
    if sample_idx:
        print(f"  Extra row idx       : {sample_idx[:30]}")
        print(f"  All rows in sets    : {set_idx[:30]}")
        for g in (full_row_dup.details.get("duplicate_groups") or [])[:5]:
            print(
                f"  group size={g.get('count')} rows={g.get('row_indices')} "
                f"preview={g.get('key_preview')}"
            )
    else:
        print("  Extra row idx       : none (no full-row duplicates)")
    print(
        "  Note: indices are 0-based in the loaded dataframe "
        "(after header), not Excel sheet row numbers."
    )

    if key_dups:
        print("\nBusiness-key duplicates (same ID, rows may otherwise differ)")
        for r in key_dups:
            print(f"  Key column          : {r.column}")
            print(f"  Status              : {r.status}")
            print(f"  Extra key dups      : {r.issues_found}")
            print(
                f"  Rows sharing a key  : {r.details.get('duplicate_set_rows', r.issues_found)}"
            )
            print(
                f"  Distinct keys reused: {r.details.get('unique_keys_repeated', 0)}"
            )
            sample_idx = r.details.get("row_indices", [])
            if sample_idx:
                print(f"  Sample extra idx    : {sample_idx[:30]}")
            for g in (r.details.get("duplicate_groups") or [])[:8]:
                print(
                    f"  key={g.get('key')!r} count={g.get('count')} "
                    f"rows={g.get('row_indices')}"
                )
    else:
        print("\nBusiness-key duplicates: no ID-like columns inferred")

    # Aggregate for pass/fail summary: any uniqueness failure counts
    dup_issues_total = sum(r.issues_found for r in dup_results if r.status != "error")
    dup_failed = any(r.status == "failed" for r in dup_results)

    # ---- Type mismatch ----
    type_results = check_type_consistency_frame(df)
    print("\n[3] Type Mismatch  (Type Reliability)")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Dominant':>12} {'Status':>10}")
    print("-" * 70)
    bad_cols = 0
    for r in type_results:
        if r.issues_found > 0:
            bad_cols += 1
        dominant = r.details.get("dominant_type", "-")
        print(f"{str(r.column):40} {r.issues_found:10d} {str(dominant):>12} {r.status:>10}")
        sample = r.details.get("sample_values", [])
        if sample:
            print(f"  sample bad values: {sample[:5]}")
    print("-" * 70)
    print(f"Columns with type issues: {bad_cols} / {len(type_results)}")

    print("\n=== Summary ===")
    print(f"Missing-values check: {len(missing)} columns scanned")
    print(
        f"Duplicates check    : "
        f"{'failed' if dup_failed else full_row_dup.status} "
        f"({dup_issues_total} duplicate issues across full-row + keys)"
    )
    print(f"Type-mismatch check : {len(type_results)} columns scanned, {bad_cols} with issues")

    return {
        "missing_columns_scanned": len(missing),
        "missing_columns_with_issues": sum(1 for r in missing if r.issues_found > 0),
        "duplicate_rows": dup_issues_total,
        "type_mismatch_columns_scanned": len(type_results),
        "type_mismatch_columns_with_issues": bad_cols,
        # Raw CheckResults, kept alongside the summary counts above so
        # scoring.py can consume them directly -- see compute_data_quality_score
        # call in run_pipeline(). Not printed, not part of the JSONL log.
        "missing_results": missing,
        "duplicate_result": full_row_dup,
        "duplicate_results": dup_results,
        "type_results": type_results,
    }


def _print_task3_results(df):
    """Task 3: outlier detection, printed in the same report style as Task 2."""
    print("\n=== Task 3 Results (Outlier Detection) ===")
    results = detect_outliers_frame(df)

    print("\n[4] Outliers  (Outlier Risk)")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Method':>10} {'Status':>10}")
    print("-" * 70)
    flagged_cols = 0
    skipped_cols = 0
    for r in results:
        if r.issues_found > 0:
            flagged_cols += 1
        if r.details.get("reason") == "skipped_non_measurement_column":
            skipped_cols += 1
        method = r.details.get("method", "-")
        print(f"{str(r.column):40} {r.issues_found:10d} {str(method):>10} {r.status:>10}")
        if r.details.get("reason"):
            print(f"  reason: {r.details['reason']} (role={r.details.get('classified_role', '-')})")
        sample = r.details.get("sample_values", [])
        if sample:
            print(f"  sample outlier values: {sample[:5]}")
        if "note" in r.details and "dominant_value" in r.details:
            print(
                f"  note: {r.details['note']} "
                f"(dominant_value={r.details['dominant_value']}, "
                f"dominant_value_ratio={r.details['dominant_value_ratio']})"
            )
    print("-" * 70)
    print(f"Columns with outliers   : {flagged_cols} / {len(results)}")
    print(f"Columns skipped (non-measurement): {skipped_cols} / {len(results)}")

    return {
        "outlier_columns_scanned": len(results),
        "outlier_columns_with_issues": flagged_cols,
        "outlier_columns_skipped": skipped_cols,
        "outlier_results": results,
    }


def _print_task4_results(df):
    """
    Task 4: PII detection + masking, printed as a summary only.
    Never prints raw PII values -- only counts/types and the already-masked
    sample values produced by detect_pii_in_series()/mask_pii().
    """
    print("\n=== Task 4 Results (PII Detection & Masking) ===")

    print("\n[5] PII  (Sensitivity)")
    print("-" * 70)
    print(f"{'Column':40} {'Rows w/ PII':>12} {'Types found':>20}")
    print("-" * 70)
    total_rows_with_pii = 0
    flagged_cols = 0
    per_column_summaries = {}
    for col in df.columns:
        summary = detect_pii_in_series(df[col])
        per_column_summaries[str(col)] = summary
        rows_with_pii = summary.get("rows_with_pii", 0)
        if rows_with_pii > 0:
            flagged_cols += 1
            total_rows_with_pii += rows_with_pii
            type_counts = summary.get("type_counts", {})
            types_str = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))
            print(f"{str(col):40} {rows_with_pii:12d} {types_str:>20}")
            masked_rows = summary.get("masked_rows", {})
            preview = list(masked_rows.values())[:2]
            if preview:
                print(f"  masked sample: {preview}")
    print("-" * 70)
    print(f"Columns with PII: {flagged_cols} / {df.shape[1]}")
    print(f"Total rows with PII across flagged columns: {total_rows_with_pii}")

    return {
        "pii_columns_scanned": df.shape[1],
        "pii_columns_with_hits": flagged_cols,
        "pii_rows_with_hits": total_rows_with_pii,
        # column_name -> detect_pii_in_series() summary, for scoring.py's
        # separate (never-part-of-composite) privacy risk report.
        "pii_summary_by_column": per_column_summaries,
    }


def _print_fuzzy_standardization_results(df, roles):
    """
    plan.md Task 5 (Section 4.4): RapidFuzz text standardization.

    Runs after PII (pipeline step g). Reports near-duplicate clusters and
    the {original: canonical} mappings without mutating the working frame
    in the CLI (callers that want cleaned data use apply_standardization /
    standardize_frame explicitly).
    """
    print("\n=== Plan Task 5 Results (Fuzzy Text Standardization) ===")
    print("\n[5b] Fuzzy Standardization  (Consistency - RapidFuzz)")
    print("-" * 70)
    print(f"{'Column':40} {'Remap rows':>12} {'Clusters':>10} {'Status':>10}")
    print("-" * 70)

    results = check_fuzzy_standardization_frame(df, roles=roles)
    mappings = standardize_frame(df, roles=roles)
    flagged = 0
    for r in results:
        if r.details.get("reason", "").startswith("skipped_"):
            continue
        if r.issues_found > 0:
            flagged += 1
        clusters = r.details.get("clusters_collapsed", 0)
        print(
            f"{str(r.column):40} {r.issues_found:12d} {clusters:10d} {r.status:>10}"
        )
        sample = r.details.get("mapping_sample") or {}
        if sample:
            preview = list(sample.items())[:3]
            print(f"  mapping sample: {preview}")
        for cluster in (r.details.get("clusters") or [])[:2]:
            print(
                f"  cluster -> {cluster.get('canonical')!r} "
                f"variants={cluster.get('variants')}"
            )
    print("-" * 70)
    print(f"Columns with fuzzy remaps: {flagged}")
    print(f"Columns with non-empty mappings built: {len(mappings)}")
    print(
        "Note: mapping is reported here; apply via "
        "apply_standardization(series, mapping) when cleaning data."
    )

    return {
        "fuzzy_columns_scanned": len(results),
        "fuzzy_columns_with_issues": flagged,
        "fuzzy_results": results,
        "fuzzy_mappings": {
            col: {src: dst for src, dst in mapping.items() if src != dst}
            for col, mapping in mappings.items()
            if any(src != dst for src, dst in mapping.items())
        },
    }


def _print_entity_resolution_results(df, classification, *, client_id: str | None = None):
    """
    Phase 2 M6: multi-tier entity resolution (lookup -> RapidFuzz -> semantic).

    Non-destructive — reports canonical suggestions and review queue only;
    never overwrites the working dataframe.
    """
    print("\n=== Phase 2 M6 Results (Entity Resolution) ===")
    print("\n[6] Entity Resolution  (Tier 1 lookup -> Tier 2 RapidFuzz -> Tier 3 semantic)")
    print("-" * 70)

    config = load_entity_resolution_config()
    if not config.enabled:
        print("Entity resolution disabled in configuration.")
        return {"enabled": False, "columns": {}, "summary": {}, "review_queue": []}

    try:
        if client_id:
            from backend.database import get_session

            with get_session() as session:
                result = resolve_dataframe(
                    df,
                    classification,
                    config=config,
                    client_id=client_id,
                    session=session,
                )
        else:
            result = resolve_dataframe(
                df,
                classification,
                config=config,
                client_id=client_id,
                session=None,
            )
    except RuntimeError:
        result = resolve_dataframe(
            df,
            classification,
            config=config,
            client_id=client_id,
            session=None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Entity resolution error (non-fatal): {exc}")
        return {"enabled": False, "error": str(exc), "columns": {}, "summary": {}, "review_queue": []}

    summary = result.get("summary") or {}
    print(
        f"Values resolved: {summary.get('total_values', 0)} | "
        f"Auto: {summary.get('auto_match', 0)} | "
        f"Review: {summary.get('review', 0)} | "
        f"No match: {summary.get('no_match', 0)}"
    )
    for col, block in (result.get("columns") or {}).items():
        resolutions = block.get("resolutions") or {}
        auto = sum(1 for r in resolutions.values() if r.get("decision") == "auto_match")
        print(f"  {col} ({block.get('entity_type')}): {len(resolutions)} unique values, {auto} auto-matched")
    print("-" * 70)
    print("Note: original column values are preserved; apply canonical mappings manually or via M7.")
    return result


def _print_task5_results(df, roles, pii_summary_by_column=None):
    """
    Task 5: the 4 rubric dimensions not covered by Task 1-4 --
    Schema Quality, Consistency, Validity, Freshness. Printed in the same
    report style as Task 2/3. roles comes from _print_classification_results
    (computed once per sheet, reused here rather than reclassifying).
    pii_summary_by_column: Task 4 output, reused so email-format validity
    covers PII EMAIL columns whose names do not contain \"email\".
    """
    print("\n=== Task 5 Results (Schema, Consistency, Validity, Freshness) ===")

    # ---- Schema Quality ----
    schema_results = check_schema_quality(df)
    print("\n[6] Schema Quality")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Status':>10}")
    print("-" * 70)
    schema_bad = 0
    for r in schema_results:
        if r.issues_found > 0:
            schema_bad += 1
        print(f"{str(r.column):40} {r.issues_found:10d} {r.status:>10}")
        if r.details.get("issues"):
            print(f"  issues: {r.details['issues']}")
    print("-" * 70)
    print(f"Columns with schema issues: {schema_bad} / {len(schema_results)}")

    # ---- Consistency ----
    consistency_results = check_consistency_frame(df, roles=roles)
    print("\n[7] Consistency")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Status':>10}")
    print("-" * 70)
    consistency_bad = 0
    for r in consistency_results:
        if r.issues_found > 0:
            consistency_bad += 1
        print(f"{str(r.column):40} {r.issues_found:10d} {r.status:>10}")
        if r.details.get("examples"):
            print(f"  example variants: {r.details['examples'][:2]}")
    print("-" * 70)
    print(f"Columns with consistency issues: {consistency_bad} / {len(consistency_results)}")

    # ---- Validity ----
    validity_results = check_validity_frame(
        df, roles=roles, pii_summary_by_column=pii_summary_by_column
    )
    print("\n[8] Validity")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Rule':>28} {'Status':>10}")
    print("-" * 70)
    validity_bad = 0
    for r in validity_results:
        if r.issues_found > 0:
            validity_bad += 1
        rule = r.details.get("rule", r.details.get("reason", "-"))
        print(f"{str(r.column):40} {r.issues_found:10d} {str(rule):>28} {r.status:>10}")
    print("-" * 70)
    print(f"Checks with validity issues: {validity_bad} / {len(validity_results)}")

    # ---- Freshness ----
    freshness_results = check_freshness_frame(df, roles=roles)
    print("\n[9] Freshness")
    print("-" * 70)
    print(f"{'Column':40} {'Issues':>10} {'Status':>10}")
    print("-" * 70)
    freshness_bad = 0
    for r in freshness_results:
        if r.issues_found > 0:
            freshness_bad += 1
        print(f"{str(r.column):40} {r.issues_found:10d} {r.status:>10}")
        if r.details.get("max_date"):
            print(f"  max_date: {r.details['max_date']}  days_since_max: {r.details.get('days_since_max')}")
    print("-" * 70)
    print(f"Columns flagged stale: {freshness_bad} / {len(freshness_results)}")

    return {
        "schema_columns_scanned": len(schema_results),
        "schema_columns_with_issues": schema_bad,
        "consistency_columns_scanned": len(consistency_results),
        "consistency_columns_with_issues": consistency_bad,
        "validity_checks_scanned": len(validity_results),
        "validity_checks_with_issues": validity_bad,
        "freshness_columns_scanned": len(freshness_results),
        "freshness_columns_with_issues": freshness_bad,
        "schema_results": schema_results,
        "consistency_results": consistency_results,
        "validity_results": validity_results,
        "freshness_results": freshness_results,
    }


def _print_scoring_results(
    task2_summary,
    task3_summary,
    task4_summary,
    task5_summary,
    fuzzy_summary=None,
    encoding_summary=None,
    hipaa_summary=None,
):
    """
    Task 6: composite Data Quality Score against the 9-dimension rubric
    (including privacy_sensitivity / PII), plus standalone privacy risk and
    HIPAA exposure reports. HIPAA applies a composite ceiling but is not its
    own rubric dimension (PHASE2_HIPAA_PHI_PLAN.md §5.2).
    """
    consistency_results = list(task5_summary["consistency_results"])
    if fuzzy_summary and fuzzy_summary.get("fuzzy_results"):
        consistency_results.extend(fuzzy_summary["fuzzy_results"])
    if encoding_summary and encoding_summary.get("encoding_result") is not None:
        consistency_results.append(encoding_summary["encoding_result"])

    dimension_results = {
        "completeness": task2_summary["missing_results"],
        "type_reliability": task2_summary["type_results"],
        "uniqueness": task2_summary.get("duplicate_results")
        or [task2_summary["duplicate_result"]],
        "outlier_risk": task3_summary["outlier_results"],
        "schema_quality": task5_summary["schema_results"],
        "consistency": consistency_results,
        "validity": task5_summary["validity_results"],
        "freshness": task5_summary["freshness_results"],
    }
    score = compute_data_quality_score(
        dimension_results,
        pii_summary_by_column=task4_summary["pii_summary_by_column"],
        hipaa_exposure=(hipaa_summary or {}).get("hipaa_exposure"),
    )

    print("\n=== Task 6 Results (Data Quality Score) ===")
    print("\n[10] Composite Data Quality Score")
    print("-" * 70)
    if score.get("error"):
        print(f"Scoring error: {score['error']}")
    else:
        dqs = score["data_quality_score"]
        raw = score.get("data_quality_score_raw")
        print(f"Data Quality Score: {dqs if dqs is not None else 'N/A'} / 100")
        if raw is not None and raw != dqs:
            print(f"  (weighted average before caps: {raw})")
        adjustments = score.get("composite_adjustments") or {}
        for cap in adjustments.get("caps_applied") or []:
            if cap.get("reason") == "hipaa_exposure":
                print(
                    f"  HIPAA proportional cap applied ({cap.get('severity')}, "
                    f"exposure {cap.get('exposure_score', 0):.1f}): max {cap['cap']}"
                )
        print(f"Scorable weight fraction: {score['scorable_weight_fraction'] * 100:.1f}% of rubric")
        if score["dimensions_excluded"]:
            print(f"Dimensions excluded (no results supplied): {score['dimensions_excluded']}")
        print("-" * 70)
        print(f"{'Dimension':20} {'Score':>8} {'Weight':>8} {'Severity':>10} {'Assessed':>10} {'Skipped':>8}")
        print("-" * 70)
        for dim, info in score["dimension_scores"].items():
            s = info["score"] if info["score"] is not None else "-"
            assessed = info.get("total", 0)
            skipped = info.get("skipped", 0)
            severity = info.get("severity", "None")
            print(
                f"{dim:20} {str(s):>8} {info['weight']:>8.2f} {severity:>10} "
                f"{assessed:>10} {skipped:>8}"
            )

    print("\n[11] Privacy Risk (PII detail — scored in composite via privacy_sensitivity)")
    print("-" * 70)
    risk = score.get("privacy_risk")
    if not risk:
        print("No PII summary available for this run.")
    else:
        print(f"Risk level: {risk['risk_level']}")
        print(f"Columns with PII: {risk['columns_with_pii']} / {risk['total_columns']}")
        print(f"PII types found: {risk['pii_types_found']}")

    hipaa_exp = score.get("hipaa_exposure")
    if hipaa_exp:
        print("\n[12] HIPAA Exposure (separate score — applies composite ceiling)")
        print("-" * 70)
        print(f"Exposure score: {hipaa_exp['exposure_score']:.1f} / 100 ({hipaa_exp['severity']})")
        print(f"Identifiers detected: {hipaa_exp['identifiers_detected']}")
        print(f"Columns affected: {hipaa_exp['columns_affected']}")

    return score


def _print_hipaa_compliance_results(
    task4_summary: dict,
    row_count: int,
    *,
    run_id: str | None = None,
) -> dict:
    """
    Phase 2 M9: HIPAA PHI compliance scan (PHASE2_HIPAA_PHI_PLAN.md §5.1).

    Runs immediately after Task 4 PII detection and before Task 6 scoring.
    Never prints raw PHI — counts and identifier labels only.
    """
    pii_summary = task4_summary.get("pii_summary_by_column") or {}
    result = assess_hipaa_compliance(pii_summary, row_count, run_id=run_id)
    exposure = score_hipaa_compliance(result)
    check_results = assess_hipaa_compliance_as_check_results(
        pii_summary, row_count, run_id=run_id
    )

    print("\n=== HIPAA PHI Compliance Scan (Phase 2 M9) ===")
    print("-" * 70)
    print(f"Scope: {result.scope}")
    print(f"Posture: {result.status}")
    print(f"Exposure score: {exposure.exposure_score:.1f} / 100 ({exposure.severity})")
    print(f"Columns with PHI: {len(result.columns_with_phi)}")
    if result.identifier_counts:
        print("Identifier counts (assessable only):")
        for hipaa_id, count in sorted(result.identifier_counts.items()):
            if count > 0:
                print(f"  {hipaa_id}: {count}")
    for not_assessed in result.identifiers_not_assessed:
        print(f"  {not_assessed}: NOT ASSESSED")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings[:5]:
            print(f"  - {warning}")
    print(f"\nDisclaimer: {result.disclaimer}")
    print("-" * 70)

    return {
        "hipaa_result": result,
        "hipaa_exposure": exposure,
        "hipaa_check_results": check_results,
    }


def _print_referential_integrity_results(
    df,
    reference_dir: str | None,
    include_products: bool = False,
):
    """
    Opt-in only: reference master files (Customer List.xls, Supplier
    List.xls, Product Data by Product Site.xlsx) are not guaranteed to sit
    alongside every file being checked, so this step only runs when the
    caller explicitly passes --reference-dir. When skipped, that is printed
    so it is visible in the report instead of silently absent.

    Not folded into the Task 6 composite score: scoring.py's rubric doesn't
    have "integrity" or "accuracy" slots yet -- adding those is a scoring.py/
    plan.md change, out of scope here. This prints as its own section (PII
    is now scored via privacy_sensitivity in the composite).
    """
    print("\n=== Referential Integrity (opt-in, requires --reference-dir) ===")
    if not reference_dir:
        print("Skipped: no --reference-dir supplied.")
        return {"referential_results": [], "referential_columns_scanned": 0,
                "referential_columns_with_issues": 0}

    ref_map = domain_rules.reference_lists_for_frame(
        df, Path(reference_dir), include_products=include_products
    )
    if not ref_map:
        print(
            "No columns in this sheet matched a known reference list "
            "(Customer No., Supplier Code, Product, ...)."
        )
        return {"referential_results": [], "referential_columns_scanned": 0,
                "referential_columns_with_issues": 0}

    results = []
    bad_cols = 0
    print("-" * 70)
    print(f"{'Column':30} {'Ref size':>10} {'Issues':>10} {'Status':>10}")
    print("-" * 70)
    for column, values in ref_map.items():
        ref_set = set(values)
        result = check_referential_integrity(df, column, ref_set)
        results.append(result)
        if result.status != "passed":
            bad_cols += 1
        print(
            f"{str(column):30} {len(ref_set):10d} {result.issues_found:10d} "
            f"{result.status:>10}"
        )
        if result.status == "failed":
            print(f"  bad values: {result.details.get('sample_invalid_values', [])[:5]}")
        elif result.status == "error":
            print(f"  error: {result.details.get('error')}")
    print("-" * 70)

    return {
        "referential_results": results,
        "referential_columns_scanned": len(results),
        "referential_columns_with_issues": bad_cols,
    }


def _print_ml_readiness_results(df, target_column: str | None, date_column: str | None):
    """
    Phase 2 -- M3: ML Readiness Assessment. Opt-in only (requires both
    --target-column and --date-column); when either is missing this is
    skipped and that is printed explicitly, the same way Referential
    Integrity is skipped without --reference-dir.

    Never raises -- score_readiness() itself is designed to never raise
    (see phase2/readiness/scorer.py), and every sub-analysis here follows
    the same contract, so a bad/missing column comes back as blockers in
    the printed output, not a crashed run.

    Returns a dict shaped for build's HTML report card (_ml_readiness_html
    in engine/report.py) -- an asdict()'d ReadinessScore plus the four
    sub-analyses under "temporal"/"interval"/"target"/"leakage", or None
    if this step was skipped.
    """
    from dataclasses import asdict

    print("\n=== ML Model Readiness (opt-in, requires --target-column and --date-column) ===")
    if not target_column or not date_column:
        print("Skipped: --target-column and --date-column were not both supplied.")
        return None

    if target_column not in df.columns:
        print(f"Skipped: target column '{target_column}' not found in this sheet.")
        return None
    if date_column not in df.columns:
        print(f"Skipped: date column '{date_column}' not found in this sheet.")
        return None

    result = score_readiness(df, target_column=target_column, date_column=date_column)
    temporal = analyze_temporal_sufficiency(df, date_column)
    interval = analyze_interval_regularity(df, date_column)
    target = analyze_target_integrity(df, target_column)
    leakage = analyze_leakage_and_cardinality(df, target_column)

    print("-" * 70)
    print(f"Verdict: {result.verdict.upper()}   Overall score: {result.overall_score:.1f} / 100")
    print("-" * 70)
    print(f"{'Sub-score':20} {'Value':>10}")
    print(f"{'Temporal':20} {result.temporal_score:>10.1f}")
    print(f"{'Interval':20} {result.interval_score:>10.1f}")
    print(f"{'Target':20} {result.target_score:>10.1f}")
    print(f"{'Leakage':20} {result.leakage_score:>10.1f}")

    print(f"\n[Temporal] observations={temporal.total_observations} "
          f"range_days={temporal.date_range_days} "
          f"frequency={temporal.implied_frequency} "
          f"seasonal_cycles={temporal.seasonal_cycles_detected}")
    print(f"[Interval] frequency={interval.inferred_frequency} "
          f"missing_intervals={interval.missing_intervals} "
          f"duplicate_timestamps={interval.duplicate_timestamps} "
          f"regularity_score={interval.regularity_score}")
    print(f"[Target:{target.column_name}] null%={target.null_pct} "
          f"zero%={target.zero_pct} outlier%={target.outlier_pct} "
          f"variance={target.variance}")
    print(f"[Leakage] perfect_correlation={leakage.perfect_correlation_features} "
          f"high_cardinality={leakage.high_cardinality_features} "
          f"identifiers={leakage.identifier_features}")

    if result.blockers:
        print("\nBLOCKERS:")
        for b in result.blockers:
            print(f"  - {b}")
    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  - {w}")
    if result.recommendations:
        print("\nRecommendations:")
        for r in result.recommendations:
            print(f"  - {r}")
    print("-" * 70)

    readiness_dict = asdict(result)
    readiness_dict["temporal"] = asdict(temporal)
    readiness_dict["interval"] = asdict(interval)
    readiness_dict["target"] = asdict(target)
    readiness_dict["leakage"] = asdict(leakage)
    return readiness_dict


def _write_ai_enhanced_report(
    *,
    filepath: str,
    sname: str,
    df,
    header_row: int,
    classification: dict[str, str],
    task2_summary: dict,
    task3_summary: dict,
    task4_summary: dict,
    task5_summary: dict,
    fuzzy_summary: dict,
    entity_resolution_summary: dict | None,
    referential_summary: dict,
    hipaa_summary: dict | None,
    score: dict,
    readiness_summary: dict | None,
    processing_time_seconds: float,
    out_dir,
    client_id: str,
    gemini_api_key: str | None,
    include_hipaa: bool = False,
):
    """
    Bridges this pipeline's already-computed CheckResult lists (Task 2-5,
    exactly what the console output already used) into build_report_data()'s
    expected shape, then renders the Phase 2 "Inspect button" AI-enhanced
    HTML report from that -- no checks are re-run, no second pass over the
    dataframe.

    This is now the ONLY report --report writes (see run_pipeline's
    "if write_report:" block below). The old engine.report legacy HTML
    report generator has been removed from this pipeline.

    include_hipaa: opt-out only. When True (default -- unchanged prior
    behavior), the HIPAA PHI section is included in this report exactly as
    before. When False, the HIPAA section is simply omitted from
    check_results_by_name -- build_report_data() / the HTML/PDF renderers
    already loop generically over whatever check names are present, so
    omitting the key here is enough to gracefully drop the section with no
    broken/empty placeholder. The underlying HIPAA analysis itself
    (hipaa_summary) is never skipped or recomputed by this flag -- it is
    always already computed upstream (see _print_hipaa_compliance_results)
    so the standalone compliance report can reuse the exact same
    CheckResult objects regardless of this flag.

    Raises on failure -- run_pipeline's caller wraps this in its own
    try/except so a report failure is surfaced as an [ERROR] line instead
    of silently producing nothing. This "raises" contract covers the HTML
    report only; the PDF is best-effort (see below) and never raises.

    Returns (html_path, pdf_path). pdf_path is None if PDF rendering
    failed -- that failure is logged but never blocks the HTML report
    which already succeeded by that point.
    """
    check_results_by_name: dict[str, list] = {
        "missing_values": task2_summary["missing_results"],
        "duplicates": task2_summary["duplicate_results"],
        "type_mismatch": task2_summary["type_results"],
        "outliers": task3_summary["outlier_results"],
        "schema_quality": task5_summary["schema_results"],
        "consistency": task5_summary["consistency_results"],
        "validity": task5_summary["validity_results"],
        "freshness": task5_summary["freshness_results"],
    }
    non_skipped_fuzzy = [
        r
        for r in fuzzy_summary.get("fuzzy_results", [])
        if not str(r.details.get("reason", "")).startswith("skipped_")
    ]
    if non_skipped_fuzzy:
        check_results_by_name["fuzzy_match"] = non_skipped_fuzzy
    if referential_summary.get("referential_results"):
        check_results_by_name["referential_integrity"] = referential_summary["referential_results"]
    if include_hipaa and hipaa_summary and hipaa_summary.get("hipaa_check_results"):
        check_results_by_name["hipaa_phi"] = hipaa_summary["hipaa_check_results"]

    fuzzy_results_param = {
        str(r.column): {
            "remap_rows": r.issues_found,
            "clusters": r.details.get("clusters_collapsed", 0),
            "mapping_sample": list((r.details.get("mapping_sample") or {}).items())[:5],
        }
        for r in non_skipped_fuzzy
    }

    report_data = build_report_data(
        filepath=filepath,
        sheet_name=sname,
        df_shape=df.shape,
        header_row=header_row,
        processing_time_seconds=processing_time_seconds,
        classification=classification,
        check_results_by_name=check_results_by_name,
        pii_summary_by_column=task4_summary.get("pii_summary_by_column") or {},
        fuzzy_results=fuzzy_results_param,
        entity_resolution=entity_resolution_summary,
        score=score,
        readiness=readiness_summary,
    )

    trend = None
    try:
        trend = history.record_run_and_get_trend(
            client_id=client_id, file_name=Path(filepath).name, report_data=report_data
        )
        if trend is not None:
            print(f"Score trend: {trend.to_display_text()}")
    except Exception as exc:  # noqa: BLE001 - trend history is best-effort only
        print(f"Score trend unavailable (history DB error): {exc}")

    stem = Path(filepath).stem
    safe_sheet = "".join(c if c.isalnum() else "_" for c in sname)
    ai_path = Path(out_dir) / f"{stem}_{safe_sheet}_data_quality_report.html"
    ai_path.parent.mkdir(parents=True, exist_ok=True)
    generate_ai_enhanced_html_report(
        report_data, str(ai_path), api_key=gemini_api_key, trend=trend
    )

    # A real, downloadable PDF alongside the HTML report -- same
    # report_data, no re-computation. Best-effort: a PDF render failure
    # (e.g. an fpdf2 layout edge case) must never take down the HTML
    # report that already succeeded above.
    pdf_path: str | None = None
    try:
        pdf_file = Path(out_dir) / f"{stem}_{safe_sheet}_data_quality_report.pdf"
        generate_pdf_report(report_data, str(pdf_file))
        pdf_path = str(pdf_file)
    except Exception as exc:  # noqa: BLE001 - PDF is a bonus output, not required
        print(f"\n[WARN] PDF report generation failed for sheet '{sname}': {exc}")

    return str(ai_path), pdf_path


def _write_compliance_report(
    *,
    filepath: str,
    sname: str,
    row_count: int,
    column_count: int,
    hipaa_summary: dict | None,
    out_dir,
    gemini_api_key: str | None = None,
) -> str | None:
    """
    Writes the standalone Compliance Report (currently HIPAA-only; see
    engine/compliance/report.py for how a second regulation module would
    be added). Reuses the exact same hipaa_check_results CheckResult
    objects the main report's HIPAA section (when included) renders --
    no second call into the HIPAA scanner/scorer, no duplicated analysis.

    Independent of include_hipaa: the standalone compliance report always
    reflects the compliance analysis for this run, regardless of whether
    the main report was asked to include or omit its own HIPAA section --
    those are two different presentation surfaces for the same underlying
    result.

    Best-effort, like the PDF report: returns None (never raises) if there
    is nothing to report or if rendering fails, so a compliance-report
    problem never blocks the main report or the rest of the pipeline.
    """
    if not hipaa_summary or not hipaa_summary.get("hipaa_check_results"):
        return None
    try:
        modules = {"hipaa_phi": hipaa_summary["hipaa_check_results"]}
        report_data = build_compliance_report_data(
            filepath=filepath,
            sheet_name=sname,
            row_count=row_count,
            column_count=column_count,
            modules=modules,
            gemini_api_key=gemini_api_key,
        )
        stem = Path(filepath).stem
        safe_sheet = "".join(c if c.isalnum() else "_" for c in sname)
        out_path = Path(out_dir) / f"{stem}_{safe_sheet}_compliance_report.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        generate_compliance_html_report(report_data, str(out_path))
        return str(out_path)
    except Exception as exc:  # noqa: BLE001 - best-effort, mirrors PDF report handling
        print(f"\n[WARN] Compliance report generation failed for sheet '{sname}': {exc}")
        return None


def run_pipeline(
    filepath: str,
    sheet_name: str | None = None,
    prompt: UserPrompt | None = None,
    *,
    reference_dir: str | None = None,
    include_products: bool = False,
    write_report: bool = False,
    report_dir: str | None = None,
    target_column: str | None = None,
    date_column: str | None = None,
    client_id: str = "default_client",
    gemini_api_key: str | None = None,
    include_hipaa: bool = False,
) -> list[dict]:
    """
    Full Phase 1 + Phase 2 pipeline for one file (all sheets, or a single --sheet).

    Order: header confirm → scope confirm → column classification →
    missing/duplicates/types → outliers → PII → schema/consistency/
    validity/freshness → referential integrity (opt-in) → composite score
    + privacy risk → ML readiness (opt-in) → Phase 2 AI-enhanced HTML report.

    reference_dir: opt-in only. When set, points to the folder holding
    Customer List.xls / Supplier List.xls / Product Data by Product
    Site.xlsx, and referential-integrity checks run against whatever
    columns in this file match those masters. When None (default), the
    step is skipped and that is printed explicitly.
    include_products: also check product-code columns against Product
    Data by Product Site.xlsx (large file — off by default).
    write_report: opt-in. When True, writes ONE HTML report per sheet --
    the Phase 2 AI-enhanced report (built via build_report_data() +
    generate_ai_enhanced_html_report()) -- to report_dir (default
    SETTINGS["reports_dir"]). Built from the exact same CheckResult
    objects the console output above already printed. The legacy
    (non-AI) report generator has been removed; this is now the only
    report the pipeline writes.
    target_column / date_column: opt-in, both required together. When set,
    runs the Phase 2 M3 ML Readiness Assessment (temporal sufficiency,
    interval regularity, target integrity, leakage/cardinality) against
    this sheet and folds it into the console output and the report as its
    own section -- never into the Task 6 composite score, the same
    pattern as Referential Integrity above.
    client_id / gemini_api_key: only used when write_report=True. Every
    --report run writes "<stem>_<sheet>_data_quality_report.html" -- built
    from the exact same CheckResult objects as the console output (no
    second pipeline pass), with an "Inspect" button on every finding that
    shows a plain-language explanation (AI via Gemini if gemini_api_key /
    GEMINI_API_KEY is set, otherwise a rule-based fallback -- see
    phase2/enhanced_report.py). client_id scopes the score-trend history
    shown on that report; use a real per-client id once you process files
    for more than one client so their trends don't mix.

    Returns a list with one dict per sheet actually processed (skipped/
    hidden sheets are omitted), each shaped:
        {
            "sheet_name": str,
            "rows": int, "columns": int,
            "data_quality_score": float | None,
            "privacy_risk_level": str | None,
            "ml_readiness_verdict": str | None,
            "ml_readiness_score": float | None,
            "report_path": str | None,   # only when write_report=True and it succeeded
            "compliance_report_path": str | None,  # only when write_report=True and it succeeded
            "error": str | None,         # set instead of the above if this sheet raised
        }
    This is purely additive: every existing CLI/script caller already
    ignored run_pipeline's (previously implicit `None`) return value, so
    nothing about current behavior changes. It exists so a non-interactive
    caller (Phase 2 M4's REST API -- see phase2/api/jobs.py) can report a
    real outcome back to whoever asked for the run, for any file/dataset,
    without scraping stdout or re-deriving what the pipeline already knows.
    """
    prompt = prompt or CLIPrompt()
    run_id = uuid.uuid4().hex[:12]
    logger = get_logger(run_id)
    sheet_outcomes: list[dict] = []

    sheets = read_excel_file(filepath)
    if sheet_name:
        if sheet_name not in sheets:
            raise KeyError(f"Sheet '{sheet_name}' not found. Available: {list(sheets.keys())}")
        items = [(sheet_name, sheets[sheet_name])]
    else:
        # FIX (ISS-01, validation audit): don't silently include hidden
        # sheets (e.g. Sage X3's hidden "Sage.X3.ReservedSheet" config
        # sheet) in the default all-sheets run -- they're not data the
        # user asked to be assessed. An explicit --sheet still overrides
        # this and can target a hidden sheet on purpose.
        visibility = get_sheet_visibility(filepath)
        hidden = [name for name in sheets if visibility.get(name, True) is False]
        if hidden:
            print(f"Skipping hidden sheet(s) (not requested explicitly): {hidden}")
        items = [(name, df) for name, df in sheets.items() if name not in hidden]
        if not items:
            # Every sheet was hidden -- fall back to processing them all
            # rather than silently doing nothing.
            items = list(sheets.items())

    for sname, raw_df in items:
        sheet_start = time.perf_counter()
        print("\n" + "=" * 80)
        print(f"Sheet: {sname}")

        # Task 1: detect + confirm header
        try:
            detected = detect_header_row(raw_df)
        except ValueError:
            # Empty / headerless sheet (e.g. a truly blank tab left in the
            # workbook) -- skip it and keep processing the remaining
            # sheets rather than aborting the whole file.
            print("Skipped: empty or no header found")
            continue
        preview = header_preview(raw_df, detected)
        header_row = confirm_header_row(prompt, detected, preview)

        df = load_with_confirmed_header(raw_df, header_row)

        # Scope checkpoint before heavy checks
        est = max(0.1, (len(df) * max(1, df.shape[1])) / 50000.0)
        scope = confirm_processing_scope(prompt, len(df), df.shape[1], est)
        df = apply_scope(df, scope)

        print(f"\nFinal shape after header+scope: {df.shape}")
        try:
            encoding_summary = _print_encoding_check(filepath)
            classification = _print_classification_results(df)
            task2_summary = _print_top_results(df)
            task3_summary = _print_task3_results(df)
            task4_summary = _print_task4_results(df)
            # Phase 2 M9: HIPAA compliance after PII, before scoring (plan §5.1)
            hipaa_summary = _print_hipaa_compliance_results(
                task4_summary, len(df), run_id=run_id
            )
            # plan.md Section 4.9 step (g): fuzzy standardization after PII
            fuzzy_summary = _print_fuzzy_standardization_results(df, classification)
            entity_resolution_summary = _print_entity_resolution_results(
                df, classification, client_id=client_id
            )
            task5_summary = _print_task5_results(
                df, classification, task4_summary.get("pii_summary_by_column")
            )
            referential_summary = _print_referential_integrity_results(
                df, reference_dir, include_products
            )
            score = _print_scoring_results(
                task2_summary,
                task3_summary,
                task4_summary,
                task5_summary,
                fuzzy_summary,
                encoding_summary,
                hipaa_summary=hipaa_summary,
            )
            readiness_summary = _print_ml_readiness_results(df, target_column, date_column)

            report_path: str | None = None
            pdf_report_path: str | None = None
            compliance_report_path: str | None = None
            report_error: str | None = None
            if write_report:
                out_dir = Path(report_dir) if report_dir else SETTINGS["reports_dir"]

                try:
                    report_path, pdf_report_path = _write_ai_enhanced_report(
                        filepath=filepath,
                        sname=sname,
                        df=df,
                        header_row=header_row,
                        classification=classification,
                        task2_summary=task2_summary,
                        task3_summary=task3_summary,
                        task4_summary=task4_summary,
                        task5_summary=task5_summary,
                        fuzzy_summary=fuzzy_summary,
                        entity_resolution_summary=entity_resolution_summary,
                        referential_summary=referential_summary,
                        hipaa_summary=hipaa_summary,
                        score=score,
                        readiness_summary=readiness_summary,
                        processing_time_seconds=time.perf_counter() - sheet_start,
                        out_dir=out_dir,
                        client_id=client_id,
                        gemini_api_key=gemini_api_key,
                        include_hipaa=include_hipaa,
                    )
                    print(f"\nReport written: {report_path}")
                except Exception as exc:  # noqa: BLE001 - report generation is
                    # best-effort and must never abort the remaining sheets.
                    report_error = str(exc)
                    print(f"\n[ERROR] Report generation failed for sheet '{sname}': {exc}")

                # Standalone Compliance Report -- always attempted whenever
                # write_report=True, independent of include_hipaa above
                # (that flag only controls the main report's own section).
                # Best-effort: never raises, never blocks the main report.
                compliance_report_path = _write_compliance_report(
                    filepath=filepath,
                    sname=sname,
                    row_count=len(df),
                    column_count=df.shape[1],
                    hipaa_summary=hipaa_summary,
                    out_dir=out_dir,
                    gemini_api_key=gemini_api_key,
                )
                if compliance_report_path:
                    print(f"Compliance report written: {compliance_report_path}")
        except Exception as exc:  # noqa: BLE001 - never abort remaining sheets
            log_event(
                logger,
                logging.ERROR,
                "sheet_failed",
                run_id=run_id,
                step="pipeline",
                details={"file": str(Path(filepath).name), "sheet": sname, "error": str(exc)},
            )
            print(f"\n[ERROR] Sheet '{sname}' failed: {exc}")
            sheet_outcomes.append({
                "sheet_name": sname,
                "rows": None,
                "columns": None,
                "data_quality_score": None,
                "dimension_scores": {},
                "privacy_risk_level": None,
                "ml_readiness_verdict": None,
                "ml_readiness_score": None,
                "report_path": None,
                "pdf_report_path": None,
                "compliance_report_path": None,
                "error": str(exc),
            })
            continue

        checks_run = [
            "encoding",
            "column_classification",
            "missing_values",
            "duplicates",
            "type_mismatch",
            "outliers",
            "pii",
            "fuzzy_standardization",
            "entity_resolution",
            "schema_quality",
            "consistency",
            "validity",
            "freshness",
            "referential_integrity" if reference_dir else "referential_integrity_skipped",
            "scoring",
            "ml_readiness" if (target_column and date_column) else "ml_readiness_skipped",
        ]
        pass_count = (
            (task2_summary["missing_columns_scanned"] - task2_summary["missing_columns_with_issues"])
            + (1 if task2_summary["duplicate_rows"] == 0 else 0)
            + (task2_summary["type_mismatch_columns_scanned"] - task2_summary["type_mismatch_columns_with_issues"])
            + (task3_summary["outlier_columns_scanned"] - task3_summary["outlier_columns_with_issues"])
            + (task4_summary["pii_columns_scanned"] - task4_summary["pii_columns_with_hits"])
            + (fuzzy_summary["fuzzy_columns_scanned"] - fuzzy_summary["fuzzy_columns_with_issues"])
            + (task5_summary["schema_columns_scanned"] - task5_summary["schema_columns_with_issues"])
            + (task5_summary["consistency_columns_scanned"] - task5_summary["consistency_columns_with_issues"])
            + (task5_summary["validity_checks_scanned"] - task5_summary["validity_checks_with_issues"])
            + (task5_summary["freshness_columns_scanned"] - task5_summary["freshness_columns_with_issues"])
        )
        fail_count = (
            task2_summary["missing_columns_with_issues"]
            + (0 if task2_summary["duplicate_rows"] == 0 else 1)
            + task2_summary["type_mismatch_columns_with_issues"]
            + task3_summary["outlier_columns_with_issues"]
            + task4_summary["pii_columns_with_hits"]
            + fuzzy_summary["fuzzy_columns_with_issues"]
            + task5_summary["schema_columns_with_issues"]
            + task5_summary["consistency_columns_with_issues"]
            + task5_summary["validity_checks_with_issues"]
            + task5_summary["freshness_columns_with_issues"]
        )
        log_event(
            logger,
            logging.INFO,
            "sheet_processed",
            run_id=run_id,
            step="pipeline",
            details={
                "file": str(Path(filepath).name),
                "sheet": sname,
                "checks_run": checks_run,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "classification_roles": {
                    role: sum(1 for r in classification.values() if r == role)
                    for role in sorted(set(classification.values()))
                },
                "data_quality_score": score.get("data_quality_score"),
                "scorable_weight_fraction": score.get("scorable_weight_fraction"),
                "privacy_risk_level": (score.get("privacy_risk") or {}).get("risk_level"),
                "fuzzy_columns_with_issues": fuzzy_summary["fuzzy_columns_with_issues"],
                "entity_resolution_auto": (entity_resolution_summary or {}).get("summary", {}).get(
                    "auto_match"
                ),
                "entity_resolution_review": (entity_resolution_summary or {}).get("summary", {}).get(
                    "review"
                ),
                "entity_resolution_no_match": (entity_resolution_summary or {}).get("summary", {}).get(
                    "no_match"
                ),
                "referential_columns_scanned": referential_summary["referential_columns_scanned"],
                "referential_columns_with_issues": referential_summary["referential_columns_with_issues"],
                "encoding_skipped": bool((encoding_summary or {}).get("skipped")),
                "encoding_status": (
                    None
                    if not encoding_summary or encoding_summary.get("encoding_result") is None
                    else encoding_summary["encoding_result"].status
                ),
                "ml_readiness_verdict": (
                    readiness_summary.get("verdict") if readiness_summary else None
                ),
                "ml_readiness_score": (
                    readiness_summary.get("overall_score") if readiness_summary else None
                ),
            },
        )

        sheet_outcomes.append({
            "sheet_name": sname,
            "rows": int(df.shape[0]),
            "columns": int(df.shape[1]),
            "data_quality_score": score.get("data_quality_score"),
            "dimension_scores": score.get("dimension_scores", {}),
            "privacy_risk_level": (score.get("privacy_risk") or {}).get("risk_level"),
            "ml_readiness_verdict": (
                readiness_summary.get("verdict") if readiness_summary else None
            ),
            "ml_readiness_score": (
                readiness_summary.get("overall_score") if readiness_summary else None
            ),
            "report_path": report_path,
            "pdf_report_path": pdf_report_path,
            "compliance_report_path": compliance_report_path,
            "entity_resolution": entity_resolution_summary,
            "entity_resolution_auto": (entity_resolution_summary or {}).get("summary", {}).get(
                "auto_match"
            ),
            "entity_resolution_review": (entity_resolution_summary or {}).get("summary", {}).get(
                "review"
            ),
            "entity_resolution_no_match": (entity_resolution_summary or {}).get("summary", {}).get(
                "no_match"
            ),
            "error": report_error,
        })

    print("\nDone: Task 1-6 completed.")
    return sheet_outcomes


# Backward-compatible alias (older scripts/tests may still call this name).
run_task1_task2 = run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the full Phase 1 + Phase 2 pipeline: ingestion + header detection, "
            "core profiling, outliers, PII, RapidFuzz fuzzy standardization, "
            "schema/consistency/validity/freshness, a composite Data Quality Score, "
            "and the Phase 2 AI-enhanced HTML report (the only report format written)."
        )
    )
    p.add_argument("filepath", help="Path to .xlsx/.xls/.csv file")
    p.add_argument("--sheet", default=None, help="Optional sheet name")
    p.add_argument(
        "--reference-dir",
        default=None,
        help=(
            "Opt-in: folder containing Customer List.xls / Supplier "
            "List.xls / Product Data by Product Site.xlsx. When set, runs "
            "referential-integrity checks against whatever columns in "
            "this file match those masters. Skipped by default."
        ),
    )
    p.add_argument(
        "--include-products",
        action="store_true",
        help="Also check product-code columns against Product Data by "
        "Product Site.xlsx (large file, off by default).",
    )
    p.add_argument(
        "--report",
        action="store_true",
        help="Write the Phase 2 AI-enhanced HTML data-quality report per "
        "sheet (the only report format this pipeline writes), built from "
        "the exact same check results as the console output above (never "
        "a separate calculation). Written to --report-dir or "
        "SETTINGS['reports_dir'] (./reports by default).",
    )
    p.add_argument(
        "--report-dir",
        default=None,
        help="Directory to write --report output to. Defaults to "
        "SETTINGS['reports_dir'].",
    )
    p.add_argument(
        "--target-column",
        default=None,
        help="Opt-in: numeric column to forecast. Must be paired with "
        "--date-column. When both are set, runs the Phase 2 M3 ML "
        "Readiness Assessment (temporal sufficiency, interval regularity, "
        "target integrity, leakage) and adds it to the console output and "
        "the report. Skipped by default.",
    )
    p.add_argument(
        "--date-column",
        default=None,
        help="Opt-in: date/time column for the ML Readiness Assessment. "
        "Must be paired with --target-column.",
    )
    p.add_argument(
        "--client-id",
        default="default_client",
        help="Only used with --report. Identifies which client this run "
        "belongs to, for the report's score-trend history "
        "(default: 'default_client'). Use a real client id once you're "
        "processing files for more than one client so their trends don't mix.",
    )
    p.add_argument(
        "--gemini-api-key",
        default=None,
        help="Only used with --report. Gemini API key for the report's "
        "Inspect-button explanations. If omitted, falls back to "
        "the GEMINI_API_KEY env var, and if that's also unset the Inspect "
        "button uses rule-based explanations instead of AI.",
    )
    p.add_argument(
        "--no-hipaa-in-report",
        action="store_true",
        help="Only used with --report. Omit the HIPAA PHI compliance "
        "section from the main data quality report (default: included). "
        "The HIPAA analysis itself always still runs and is always "
        "available in the separate standalone Compliance Report, "
        "regardless of this flag.",
    )
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    path = Path(args.filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    run_pipeline(
        str(path),
        args.sheet,
        reference_dir=args.reference_dir,
        include_products=args.include_products,
        write_report=args.report,
        report_dir=args.report_dir,
        target_column=args.target_column,
        date_column=args.date_column,
        client_id=args.client_id,
        gemini_api_key=args.gemini_api_key,
        include_hipaa=not args.no_hipaa_in_report,
    )