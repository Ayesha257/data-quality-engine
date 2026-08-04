from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime
from pathlib import Path

from data_quality_engine.engine.checkpoint import (
    CLIPrompt,
    UserPrompt,
    confirm_header_row,
    confirm_processing_scope,
    apply_scope,
)
from data_quality_engine.engine.ingestion import (
    read_excel_file,
    detect_header_row,
    header_preview,
    load_with_confirmed_header,
    get_sheet_visibility,
)
from data_quality_engine.engine.checks.missing_values import check_missing_values
from data_quality_engine.engine.checks.duplicates import check_duplicates_frame
from data_quality_engine.engine.checks.type_mismatch import check_type_consistency_frame
from data_quality_engine.engine.checks.outliers import detect_outliers_frame
from data_quality_engine.engine.checks.schema_quality import check_schema_quality
from data_quality_engine.engine.checks.consistency import check_consistency_frame
from data_quality_engine.engine.checks.validity import check_validity_frame
from data_quality_engine.engine.checks.freshness import check_freshness_frame
from data_quality_engine.engine.checks.encoding import check_encoding
from data_quality_engine.engine.checks.referential_integrity import (
    check_referential_integrity,
)
from data_quality_engine.config import domain_rules
from data_quality_engine.engine.report import generate_html_report, write_html_report
from data_quality_engine.engine.column_classifier import classify_columns
from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.engine.pii.detect_pii import detect_pii_in_series
from data_quality_engine.engine.standardization.fuzzy_match import (
    check_fuzzy_standardization_frame,
    standardize_frame,
)
from data_quality_engine.engine.scoring import compute_data_quality_score
from data_quality_engine.engine.logging_utils import get_logger, log_event
import logging

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
):
    """
    Task 6: composite Data Quality Score against the teacher's 8-dimension
    rubric, plus the separate Privacy Risk report. See scoring.py for why
    dimension_results is keyed explicitly by rubric name rather than by
    each check's own CheckResult.dimension field.

    Fuzzy standardization and CSV encoding CheckResults are merged into the
    consistency dimension alongside case/whitespace consistency results.
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
    )

    print("\n=== Task 6 Results (Data Quality Score) ===")
    print("\n[10] Composite Data Quality Score")
    print("-" * 70)
    if score.get("error"):
        print(f"Scoring error: {score['error']}")
    else:
        dqs = score["data_quality_score"]
        print(f"Data Quality Score: {dqs if dqs is not None else 'N/A'} / 100")
        print(f"Scorable weight fraction: {score['scorable_weight_fraction'] * 100:.1f}% of rubric")
        if score["dimensions_excluded"]:
            print(f"Dimensions excluded (no results supplied): {score['dimensions_excluded']}")
        print("-" * 70)
        print(f"{'Dimension':20} {'Score':>8} {'Weight':>8} {'Assessed':>10} {'Skipped':>8}")
        print("-" * 70)
        for dim, info in score["dimension_scores"].items():
            s = info["score"] if info["score"] is not None else "-"
            assessed = info.get("total", 0)
            skipped = info.get("skipped", 0)
            print(f"{dim:20} {str(s):>8} {info['weight']:>8.2f} {assessed:>10} {skipped:>8}")

    print("\n[11] Privacy Risk (separate -- never part of the score above)")
    print("-" * 70)
    risk = score.get("privacy_risk")
    if not risk:
        print("No PII summary available for this run.")
    else:
        print(f"Risk level: {risk['risk_level']}")
        print(f"Columns with PII: {risk['columns_with_pii']} / {risk['total_columns']}")
        print(f"PII types found: {risk['pii_types_found']}")

    return score


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

    Not folded into the Task 6 composite score: scoring.py's 8-dimension
    rubric (RUBRIC_DIMENSIONS in scoring.py) doesn't have "integrity" or
    "accuracy" slots yet -- adding those is a scoring.py/plan.md change,
    out of scope here. This prints as its own section, the same way
    Privacy Risk is reported separately from the composite score.
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


def run_pipeline(
    filepath: str,
    sheet_name: str | None = None,
    prompt: UserPrompt | None = None,
    *,
    reference_dir: str | None = None,
    include_products: bool = False,
    write_report: bool = False,
    report_dir: str | None = None,
):
    """
    Full Phase 1 pipeline for one file (all sheets, or a single --sheet).

    Order: header confirm → scope confirm → column classification →
    missing/duplicates/types → outliers → PII → schema/consistency/
    validity/freshness → referential integrity (opt-in) → composite score
    + privacy risk → HTML report (opt-in).

    reference_dir: opt-in only. When set, points to the folder holding
    Customer List.xls / Supplier List.xls / Product Data by Product
    Site.xlsx, and referential-integrity checks run against whatever
    columns in this file match those masters. When None (default), the
    step is skipped and that is printed explicitly.
    include_products: also check product-code columns against Product
    Data by Product Site.xlsx (large file — off by default).
    write_report: opt-in. When True, writes an HTML report per sheet to
    report_dir (default SETTINGS["reports_dir"]) built from the exact same
    CheckResult objects the console output above already printed -- see
    engine/report.py's module docstring for why that matters.
    """
    prompt = prompt or CLIPrompt()
    run_id = uuid.uuid4().hex[:12]
    logger = get_logger(run_id)

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
        detected = detect_header_row(raw_df)
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
            # plan.md Section 4.9 step (g): fuzzy standardization after PII
            fuzzy_summary = _print_fuzzy_standardization_results(df, classification)
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
            )

            if write_report:
                report_html = generate_html_report(
                    file_label=Path(filepath).name,
                    sheet_name=sname,
                    rows=len(df),
                    columns=df.shape[1],
                    header_row=header_row,
                    classification=classification,
                    task2_summary=task2_summary,
                    task3_summary=task3_summary,
                    task4_summary=task4_summary,
                    fuzzy_summary=fuzzy_summary,
                    task5_summary=task5_summary,
                    score=score,
                    processing_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    execution_time_s=time.perf_counter() - sheet_start,
                )
                out_dir = Path(report_dir) if report_dir else SETTINGS["reports_dir"]
                stem = Path(filepath).stem
                safe_sheet = "".join(c if c.isalnum() else "_" for c in sname)
                out_path = write_html_report(
                    report_html,
                    reports_dir=out_dir,
                    filename=f"{stem}_{safe_sheet}_data_quality_report.html",
                )
                print(f"\nReport written: {out_path}")
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
            "schema_quality",
            "consistency",
            "validity",
            "freshness",
            "referential_integrity" if reference_dir else "referential_integrity_skipped",
            "scoring",
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
                "referential_columns_scanned": referential_summary["referential_columns_scanned"],
                "referential_columns_with_issues": referential_summary["referential_columns_with_issues"],
                "encoding_skipped": bool((encoding_summary or {}).get("skipped")),
                "encoding_status": (
                    None
                    if not encoding_summary or encoding_summary.get("encoding_result") is None
                    else encoding_summary["encoding_result"].status
                ),
            },
        )

    print("\nDone: Task 1-6 completed.")


# Backward-compatible alias (older scripts/tests may still call this name).
run_task1_task2 = run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the full Phase 1 pipeline: ingestion + header detection, "
            "core profiling, outliers, PII, RapidFuzz fuzzy standardization, "
            "schema/consistency/validity/freshness, and a composite Data Quality Score."
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
        help="Write an HTML data-quality report per sheet, built from the "
        "exact same check results as the console output above (never a "
        "separate calculation). Written to --report-dir or "
        "SETTINGS['reports_dir'] (./reports by default).",
    )
    p.add_argument(
        "--report-dir",
        default=None,
        help="Directory to write --report output to. Defaults to "
        "SETTINGS['reports_dir'].",
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
    )