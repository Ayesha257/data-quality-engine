"""Unit tests for BusinessRuleEngine in config/rule_engine.py."""

from __future__ import annotations

import pandas as pd
import pytest

from data_quality_engine.config.rule_engine import BusinessRuleEngine, load_business_rules


def test_business_rule_engine_required():
    rules_spec = {
        "rules": [
            {
                "id": "req_cust",
                "name": "Required Customer No.",
                "type": "required",
                "column": "Customer No.",
                "enabled": True,
                "severity": "HIGH",
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame({"Customer No.": ["C001", None, "  ", "C002"]})
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 2
    assert res.details["row_indices"] == [1, 2]


def test_business_rule_engine_unique():
    rules_spec = {
        "rules": [
            {
                "id": "uniq_cust",
                "name": "Unique Customer Site",
                "type": "unique",
                "columns": ["Customer No.", "Add. Code"],
                "enabled": True,
                "severity": "CRITICAL",
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame(
        {
            "Customer No.": ["C01", "C01", "C02"],
            "Add. Code": ["HO", "HO", "HO"],
        }
    )
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_datatype():
    rules_spec = {
        "rules": [
            {
                "id": "qty_num",
                "name": "Quantity Numeric",
                "type": "datatype",
                "column": "Qty",
                "expected_type": "numeric",
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame({"Qty": ["10", "abc", "25"]})
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_min_max():
    rules_spec = {
        "rules": [
            {
                "id": "non_neg",
                "name": "Qty Non-Negative",
                "type": "min_max",
                "column": "Qty",
                "min": 0,
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame({"Qty": [10, -5, 0, 15]})
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_regex():
    rules_spec = {
        "rules": [
            {
                "id": "email_pat",
                "name": "Valid Email Regex",
                "type": "regex",
                "column": "Email",
                "pattern": r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame({"Email": ["valid@test.com", "invalid-email", "user@domain.org"]})
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_allowed_values():
    rules_spec = {
        "rules": [
            {
                "id": "status_allowed",
                "name": "Allowed Status",
                "type": "allowed_values",
                "column": "Status",
                "allowed_values": ["Open", "Closed"],
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame({"Status": ["Open", "INVALID", "Closed"]})
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_cross_column_dates():
    rules_spec = {
        "rules": [
            {
                "id": "del_after_order",
                "name": "Delivery After Order",
                "type": "cross_column",
                "left": "Delivery Date",
                "op": ">=",
                "right": "Order Date",
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame(
        {
            "Order Date": ["2026-01-10", "2026-01-10"],
            "Delivery Date": ["2026-01-15", "2026-01-05"],  # row 1 is invalid
        }
    )
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]


def test_business_rule_engine_cross_column_discount_total():
    rules_spec = {
        "rules": [
            {
                "id": "discount_le_total",
                "name": "Discount <= Total",
                "type": "cross_column",
                "left": "Discount",
                "op": "<=",
                "right": "Total",
                "enabled": True,
            }
        ]
    }
    engine = BusinessRuleEngine(rules_spec)
    df = pd.DataFrame(
        {
            "Discount": [10.0, 150.0],
            "Total": [100.0, 100.0],  # row 1: 150 <= 100 fails
        }
    )
    results = engine.evaluate_rules(df)
    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.issues_found == 1
    assert res.details["row_indices"] == [1]
