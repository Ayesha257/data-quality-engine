"""Enterprise Business Rule Engine for Data Quality Validation.

Loads, validates, and evaluates business rules specified in JSON.
Supports single-column rules (required, unique, datatype, min_max, regex, allowed_values)
and cross-column comparison rules (date comparisons, amount vs discount, quantity > 0, invoice <= today).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from data_quality_engine.config.settings import REPO_ROOT, SETTINGS
from data_quality_engine.engine.models import CheckResult

_SUPPORTED_RULE_TYPES = frozenset(
    {
        "required",
        "unique",
        "datatype",
        "min_max",
        "regex",
        "allowed_values",
        "cross_column",
    }
)

_DEFAULT_RULES_PATH = (
    REPO_ROOT / "data_quality_engine" / "config" / "business_rules.json"
)
_SAMPLE_CAP = 100


def default_rules_path() -> Path:
    configured = SETTINGS.get("business_rules_path")
    if configured:
        return Path(configured)
    return _DEFAULT_RULES_PATH


def _validate_rule(rule: dict[str, Any], index: int) -> None:
    rtype = rule.get("type")
    if rtype not in _SUPPORTED_RULE_TYPES:
        raise ValueError(
            f"Rule #{index}: unsupported type {rtype!r}; "
            f"expected one of {sorted(_SUPPORTED_RULE_TYPES)}"
        )
    if rtype == "unique":
        cols = rule.get("columns") or rule.get("column")
        if not cols:
            raise ValueError(f"Rule #{index}: unique rule needs 'columns' or 'column'")
    elif rtype == "cross_column":
        if not rule.get("left") or not rule.get("right"):
            raise ValueError(f"Rule #{index}: cross_column rule requires 'left' and 'right'")
    elif rtype in {"required", "datatype", "min_max", "regex", "allowed_values"}:
        if not rule.get("column") and not rule.get("column_pattern"):
            raise ValueError(
                f"Rule #{index}: {rtype} rule needs 'column' or 'column_pattern'"
            )


def load_business_rules(path: Path | str | None = None) -> dict[str, Any]:
    """Load business rules JSON. Returns empty scaffold when file missing/disabled."""
    if SETTINGS.get("business_rules_enabled") is False:
        return {"version": 0, "rules": [], "duplicate_detection": {}}

    rules_path = Path(path) if path else default_rules_path()
    if not rules_path.exists():
        return {"version": 0, "rules": [], "duplicate_detection": {}}

    with rules_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"Business rules file must be a JSON object: {rules_path}")

    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("'rules' must be a list")

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule #{i} must be an object")
        _validate_rule(rule, i)

    dup_cfg = data.get("duplicate_detection") or {}
    if not isinstance(dup_cfg, dict):
        raise ValueError("'duplicate_detection' must be an object")

    return {
        "version": data.get("version", 1),
        "description": data.get("description", ""),
        "rules": rules,
        "duplicate_detection": dup_cfg,
    }


@lru_cache(maxsize=4)
def load_business_rules_cached(path_str: str | None = None) -> dict[str, Any]:
    """Cached loader for repeated pipeline calls within one process."""
    return load_business_rules(path_str)


def resolve_column(df_columns: list[Any], spec: str) -> str | None:
    """Exact match first, then case-insensitive stripped match."""
    if spec in df_columns:
        return str(spec)
    target = str(spec).strip().lower()
    for col in df_columns:
        if str(col).strip().lower() == target:
            return str(col)
    return None


def resolve_columns(df_columns: list[Any], specs: list[str]) -> list[str]:
    resolved: list[str] = []
    for spec in specs:
        col = resolve_column(df_columns, spec)
        if col is not None:
            resolved.append(col)
    return resolved


def columns_matching_pattern(df_columns: list[Any], pattern: str) -> list[str]:
    """Return columns whose name contains pattern (case-insensitive)."""
    pat = re.compile(re.escape(str(pattern).strip()), re.I)
    return [str(c) for c in df_columns if pat.search(str(c).strip())]


def rule_applies(rule: dict[str, Any], df_columns: list[Any]) -> bool:
    """Check if target columns exist in df."""
    if rule.get("enabled") is False:
        return False

    rtype = rule.get("type")
    if rtype == "unique":
        specs = rule.get("columns") or ([rule["column"]] if rule.get("column") else [])
        resolved = resolve_columns(df_columns, [str(s) for s in specs])
        if rule.get("compound") or rule.get("when_columns_present"):
            return len(resolved) == len(specs)
        return bool(resolved)

    if rtype == "cross_column":
        left = rule.get("left")
        right = rule.get("right")
        left_res = resolve_column(df_columns, str(left)) if left else None
        if left_res is None:
            return False
        if right and str(right).upper() in {"$TODAY", "TODAY", "NOW"}:
            return True
        right_res = resolve_column(df_columns, str(right)) if right else None
        return right_res is not None

    if rule.get("column_pattern"):
        return bool(columns_matching_pattern(df_columns, rule["column_pattern"]))

    col = rule.get("column")
    if col is None:
        return False
    resolved = resolve_column(df_columns, str(col))
    return resolved is not None


def configured_compound_keys(
    df_columns: list[Any], dup_cfg: dict[str, Any] | None = None
) -> list[list[str]] | None:
    cfg = dup_cfg or {}
    specs = cfg.get("compound_keys") or []
    for spec in specs:
        cols = spec.get("columns") or []
        resolved = resolve_columns(df_columns, [str(c) for c in cols])
        if spec.get("require_all_present") and len(resolved) != len(cols):
            continue
        if resolved:
            return [resolved]
    return None


def configured_unique_rules(
    rules: list[dict[str, Any]], df_columns: list[Any]
) -> list[list[str]]:
    keys: list[list[str]] = []
    for rule in rules:
        if rule.get("type") != "unique":
            continue
        if not rule_applies(rule, df_columns):
            continue
        specs = rule.get("columns") or ([rule["column"]] if rule.get("column") else [])
        resolved = resolve_columns(df_columns, [str(s) for s in specs])
        if resolved:
            keys.append(resolved)
    return keys


class BusinessRuleEngine:
    """Enterprise Business Rule Engine evaluating JSON rules against Pandas DataFrames."""

    def __init__(self, rules_spec: dict[str, Any] | None = None) -> None:
        self.rules_spec = rules_spec or load_business_rules()
        self.rules: list[dict[str, Any]] = [
            r for r in self.rules_spec.get("rules", []) if r.get("enabled", True) is not False
        ]
        self.duplicate_config: dict[str, Any] = self.rules_spec.get("duplicate_detection", {})

    def evaluate_rules(
        self, df: pd.DataFrame, roles: dict[str, str] | None = None
    ) -> list[CheckResult]:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []

        results: list[CheckResult] = []
        df_cols = list(df.columns)

        for rule in self.rules:
            if not rule_applies(rule, df_cols):
                continue

            try:
                res = self._evaluate_single_rule(df, rule, df_cols)
                if res:
                    if isinstance(res, list):
                        results.extend(res)
                    else:
                        results.append(res)
            except Exception as exc:  # noqa: BLE001
                rule_id = rule.get("id") or rule.get("name") or "unknown_rule"
                results.append(
                    CheckResult(
                        check_name="business_rule_validation",
                        status="error",
                        column=rule.get("column"),
                        issues_found=0,
                        details={"rule": rule_id, "error": str(exc)},
                        dimension="validity",
                    )
                )
        return results

    def _evaluate_single_rule(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> CheckResult | list[CheckResult] | None:
        rtype = rule.get("type")
        if rtype == "cross_column":
            return self._eval_cross_column(df, rule, df_cols)
        if rtype == "required":
            return self._eval_required(df, rule, df_cols)
        if rtype == "unique":
            return self._eval_unique(df, rule, df_cols)
        if rtype == "datatype":
            return self._eval_datatype(df, rule, df_cols)
        if rtype == "min_max":
            return self._eval_min_max(df, rule, df_cols)
        if rtype == "regex":
            return self._eval_regex(df, rule, df_cols)
        if rtype == "allowed_values":
            return self._eval_allowed_values(df, rule, df_cols)
        return None

    def _target_columns(self, df_cols: list[Any], rule: dict[str, Any]) -> list[str]:
        if rule.get("column_pattern"):
            return columns_matching_pattern(df_cols, rule["column_pattern"])
        if rule.get("column"):
            resolved = resolve_column(df_cols, str(rule["column"]))
            return [resolved] if resolved else []
        return []

    def _build_check_result(
        self,
        col_name: str | None,
        rule: dict[str, Any],
        issue_idx: list[Any],
        total_rows: int,
        extra: dict[str, Any] | None = None,
    ) -> CheckResult:
        issues = len(issue_idx)
        status = "passed" if issues == 0 else "failed"
        failed_pct = (issues / float(total_rows)) * 100.0 if total_rows > 0 else 0.0

        details = {
            "rule_id": rule.get("id", "business_rule"),
            "rule_name": rule.get("name", rule.get("id", "Business Rule Validation")),
            "rule_type": rule.get("type"),
            "status": status,
            "failed_records": issues,
            "failed_pct": round(failed_pct, 4),
            "severity": rule.get("severity", "MEDIUM").upper(),
            "business_impact": rule.get(
                "business_impact", "Validation rule failure affects data quality."
            ),
            "recommendation": rule.get(
                "recommendation", "Review and fix failed records at ingestion."
            ),
            "row_indices": issue_idx[:_SAMPLE_CAP],
            "row_indices_truncated": issues > _SAMPLE_CAP,
        }
        if extra:
            details.update(extra)

        dimension = "uniqueness" if rule.get("type") == "unique" else "validity"
        return CheckResult(
            check_name="business_rule_validation",
            status=status,
            column=col_name,
            issues_found=issues,
            details=details,
            dimension=dimension,
        )

    def _eval_required(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> list[CheckResult]:
        cols = self._target_columns(df_cols, rule)
        res = []
        for col in cols:
            series = df[col]
            mask = series.isna()
            if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
                mask = mask | (series.astype(str).str.strip() == "")
            issue_idx = df.index[mask].tolist()
            res.append(self._build_check_result(col, rule, issue_idx, len(df)))
        return res

    def _eval_unique(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> CheckResult | None:
        specs = rule.get("columns") or ([rule["column"]] if rule.get("column") else [])
        resolved = resolve_columns(df_cols, [str(s) for s in specs])
        if not resolved:
            return None
        mask = df.duplicated(subset=resolved, keep="first")
        issue_idx = df.index[mask].tolist()
        col_label = ",".join(resolved)
        return self._build_check_result(
            col_label, rule, issue_idx, len(df), extra={"subset": resolved}
        )

    def _eval_datatype(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> list[CheckResult]:
        cols = self._target_columns(df_cols, rule)
        expected = str(rule.get("expected_type", "numeric")).lower()
        res = []
        for col in cols:
            non_null = df[col].dropna()
            if non_null.empty:
                res.append(self._build_check_result(col, rule, [], len(df)))
                continue

            invalid_idx: list[Any] = []
            if expected in {"numeric", "float", "integer", "number", "int"}:
                numeric = pd.to_numeric(non_null, errors="coerce")
                invalid_idx = non_null.index[numeric.isna()].tolist()
                if expected in {"integer", "int"}:
                    valid_num = numeric.dropna()
                    float_idx = valid_num.index[
                        (valid_num % 1 != 0)
                    ].tolist()
                    invalid_idx = sorted(set(invalid_idx) | set(float_idx))
            elif expected == "date":
                parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
                invalid_idx = non_null.index[parsed.isna()].tolist()
            elif expected == "boolean":
                bool_vals = {"true", "false", "1", "0", "yes", "no", "y", "n", "t", "f"}
                invalid_idx = [
                    idx for idx, v in non_null.items() if str(v).strip().lower() not in bool_vals
                ]

            res.append(self._build_check_result(col, rule, invalid_idx, len(df)))
        return res

    def _eval_min_max(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> list[CheckResult]:
        cols = self._target_columns(df_cols, rule)
        min_val = rule.get("min")
        max_val = rule.get("max")
        res = []
        for col in cols:
            numeric = pd.to_numeric(df[col], errors="coerce")
            valid_mask = numeric.notna()
            mask = pd.Series(False, index=df.index)
            if min_val is not None:
                mask = mask | (valid_mask & (numeric < float(min_val)))
            if max_val is not None:
                mask = mask | (valid_mask & (numeric > float(max_val)))
            issue_idx = df.index[mask].tolist()
            res.append(
                self._build_check_result(
                    col, rule, issue_idx, len(df), extra={"min": min_val, "max": max_val}
                )
            )
        return res

    def _eval_regex(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> list[CheckResult]:
        cols = self._target_columns(df_cols, rule)
        pat_str = rule.get("pattern", "")
        if not pat_str:
            return []
        regex = re.compile(pat_str, re.I)
        res = []
        for col in cols:
            non_null = df[col].dropna()
            invalid_idx = [
                idx for idx, v in non_null.items() if not regex.search(str(v))
            ]
            res.append(self._build_check_result(col, rule, invalid_idx, len(df)))
        return res

    def _eval_allowed_values(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> list[CheckResult]:
        cols = self._target_columns(df_cols, rule)
        allowed = set(str(v).strip().lower() for v in (rule.get("allowed_values") or []))
        if not allowed:
            return []
        res = []
        for col in cols:
            non_null = df[col].dropna()
            invalid_idx = [
                idx
                for idx, v in non_null.items()
                if str(v).strip().lower() not in allowed
            ]
            res.append(self._build_check_result(col, rule, invalid_idx, len(df)))
        return res

    def _eval_cross_column(
        self, df: pd.DataFrame, rule: dict[str, Any], df_cols: list[Any]
    ) -> CheckResult | None:
        left_spec = str(rule.get("left"))
        right_spec = str(rule.get("right"))
        op = str(rule.get("op", "<="))

        left_col = resolve_column(df_cols, left_spec)
        if left_col is None:
            return None

        is_today = right_spec.upper() in {"$TODAY", "TODAY", "NOW"}
        right_col = resolve_column(df_cols, right_spec) if not is_today else None

        col_label = f"{left_col} vs {right_spec}"
        if not is_today and right_col is None:
            return None

        try:
            left_series = df[left_col]
            if is_today:
                right_val = pd.Timestamp(datetime.now().date())
                right_series = pd.Series(right_val, index=df.index)
            else:
                right_series = df[right_col]  # type: ignore[arg-type]

            # Try numeric comparison first, fallback to date comparison
            left_num = pd.to_numeric(left_series, errors="coerce")
            right_num = pd.to_numeric(right_series, errors="coerce")

            if left_num.notna().sum() > 0 and right_num.notna().sum() > 0:
                valid = left_num.notna() & right_num.notna()
                l_vals, r_vals = left_num, right_num
            else:
                l_dates = pd.to_datetime(left_series, errors="coerce", format="mixed")
                r_dates = pd.to_datetime(right_series, errors="coerce", format="mixed")
                valid = l_dates.notna() & r_dates.notna()
                l_vals, r_vals = l_dates, r_dates

            if op in {"<=", "le"}:
                violation = valid & (l_vals > r_vals)
            elif op in {">=", "ge"}:
                violation = valid & (l_vals < r_vals)
            elif op in {"<", "lt"}:
                violation = valid & (l_vals >= r_vals)
            elif op in {">", "gt"}:
                violation = valid & (l_vals <= r_vals)
            elif op in {"==", "eq"}:
                violation = valid & (l_vals != r_vals)
            elif op in {"!=", "ne"}:
                violation = valid & (l_vals == r_vals)
            else:
                return None

            issue_idx = df.index[violation].tolist()
            return self._build_check_result(
                col_label,
                rule,
                issue_idx,
                len(df),
                extra={"left": left_col, "right": right_spec, "op": op},
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                check_name="business_rule_validation",
                status="error",
                column=col_label,
                issues_found=0,
                details={"rule_id": rule.get("id"), "error": str(exc)},
                dimension="validity",
            )
