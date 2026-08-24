"""Tests for engine/checks/referential_integrity.py (plan.md Section 4.3/5).

Covers:
  - valid keys pass
  - a planted invalid key fails with the correct row index
  - empty reference_values is handled without crashing (status="error",
    not a false "everything fails")
  - bad input types return status="error"
  - dimension defaults to "integrity" and can be overridden to "accuracy"
  - null/blank key values are skipped, not flagged
  - row_indices are capped at 100, same pattern as duplicates.py

Also covers config/domain_rules.py's load_customer_codes /
load_supplier_codes / load_product_codes -- the loader scaffolding that
referential_integrity.load_reference_values() wires up. These are tested
against a *mocked* master workbook (patching engine.ingestion.read_excel_file)
since no real Customer List.xls / Supplier List.xls / Product Data by
Product Site.xlsx is available in this checkout.

FLAG: the loader tests below only prove the loaders work against a
plausibly-shaped mock. They have not been validated against an actual
Customer List.xls / Supplier List.xls / Product Data by Product Site.xlsx --
someone with access to the real Easby master files should run
load_customer_codes()/load_supplier_codes()/load_product_codes() against
them and confirm the returned column really is the customer/supplier/product
code column before this is relied on in production.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.engine.checks.referential_integrity import (
    check_referential_integrity,
    load_reference_values,
)


# ---------------------------------------------------------------------------
# check_referential_integrity
# ---------------------------------------------------------------------------


def test_valid_keys_pass():
    df = pd.DataFrame({"Customer No.": ["C1", "C2", "C3"]})
    result = check_referential_integrity(df, "Customer No.", {"C1", "C2", "C3"})
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.column == "Customer No."
    assert result.dimension == "integrity"


def test_planted_invalid_key_fails_with_correct_row_index():
    df = pd.DataFrame({"Customer No.": ["C1", "C2", "GHOST", "C3"]})
    result = check_referential_integrity(df, "Customer No.", {"C1", "C2", "C3"})
    assert result.status == "failed"
    assert result.issues_found == 1
    assert result.details["row_indices"] == [2]
    assert result.details["sample_invalid_values"] == ["GHOST"]


def test_planted_invalid_key_preserves_original_row_index_after_slicing():
    # Row indices must reflect the dataframe's actual index, not a
    # freshly-reset positional count -- mirrors how duplicates.py works.
    df = pd.DataFrame({"Customer No.": ["C1", "GHOST", "C2", "C3"]})
    sliced = df.iloc[1:]  # index is now [1, 2, 3]
    result = check_referential_integrity(sliced, "Customer No.", {"C1", "C2", "C3"})
    assert result.status == "failed"
    assert result.details["row_indices"] == [1]


def test_null_and_blank_keys_are_skipped_not_flagged():
    df = pd.DataFrame({"Customer No.": ["C1", None, "  ", "C2"]})
    result = check_referential_integrity(df, "Customer No.", {"C1", "C2"})
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details["checked_rows"] == 2
    assert result.details["skipped_null_or_blank_rows"] == 2


def test_values_compared_as_stripped_strings_ignores_dtype_mismatch():
    # Reference set has ints, dataframe has strings with whitespace -- should
    # still match, since master lists and transactional exports rarely share
    # dtypes.
    df = pd.DataFrame({"Customer No.": [" 1", "2 ", "3"]})
    result = check_referential_integrity(df, "Customer No.", {1, 2, 3})
    assert result.status == "passed"
    assert result.issues_found == 0


def test_empty_reference_values_handled_without_crashing():
    df = pd.DataFrame({"Customer No.": ["C1", "C2"]})
    result = check_referential_integrity(df, "Customer No.", set())
    assert result.status == "error"
    assert result.issues_found == 0
    assert "empty" in result.details["error"].lower()


def test_reference_values_of_only_blanks_treated_as_empty():
    df = pd.DataFrame({"Customer No.": ["C1"]})
    result = check_referential_integrity(df, "Customer No.", {"", "   ", None})
    assert result.status == "error"


def test_bad_df_type_returns_error():
    result = check_referential_integrity("not a dataframe", "Customer No.", {"C1"})
    assert result.status == "error"
    assert result.issues_found == 0


def test_bad_key_column_type_returns_error():
    df = pd.DataFrame({"Customer No.": ["C1"]})
    result = check_referential_integrity(df, 123, {"C1"})  # type: ignore[arg-type]
    assert result.status == "error"


def test_missing_key_column_returns_error():
    df = pd.DataFrame({"Customer No.": ["C1"]})
    result = check_referential_integrity(df, "Nonexistent Column", {"C1"})
    assert result.status == "error"
    assert result.column == "Nonexistent Column"


def test_non_iterable_reference_values_returns_error():
    df = pd.DataFrame({"Customer No.": ["C1"]})
    result = check_referential_integrity(df, "Customer No.", 42)  # type: ignore[arg-type]
    assert result.status == "error"


def test_none_reference_values_returns_error():
    df = pd.DataFrame({"Customer No.": ["C1"]})
    result = check_referential_integrity(df, "Customer No.", None)  # type: ignore[arg-type]
    assert result.status == "error"


def test_row_indices_capped_at_100():
    values = [f"BAD{i}" for i in range(150)]
    df = pd.DataFrame({"Product Code": values})
    result = check_referential_integrity(df, "Product Code", {"OK"})
    assert result.issues_found == 150
    assert len(result.details["row_indices"]) == 100
    assert result.details["row_indices_truncated"] is True


def test_accuracy_dimension_override():
    df = pd.DataFrame({"City": ["Lahore", "Atlantis"]})
    result = check_referential_integrity(
        df, "City", {"Lahore", "Karachi"}, dimension="accuracy"
    )
    assert result.dimension == "accuracy"
    assert result.status == "failed"
    assert result.issues_found == 1


def test_supplier_and_product_style_columns_same_pattern():
    df = pd.DataFrame({"Supplier Code": ["S1", "S2", "S404"]})
    result = check_referential_integrity(df, "Supplier Code", {"S1", "S2"})
    assert result.issues_found == 1
    assert result.details["row_indices"] == [2]


# ---------------------------------------------------------------------------
# load_reference_values() + domain_rules.py loaders (mocked master files)
# ---------------------------------------------------------------------------


def test_load_reference_values_rejects_unknown_kind():
    with pytest.raises(ValueError):
        load_reference_values("widget")


def test_load_reference_values_customer_wires_to_domain_rules(monkeypatch):
    """
    Mocked master file: real Customer List.xls / Supplier List.xls /
    Product Data by Product Site.xlsx were not available to test against
    (see module docstring) -- this proves load_reference_values() reaches
    the existing domain_rules.py loader and normalizes its output to a set,
    not that the loader's column-matching survives contact with the real
    file's actual headers.
    """
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()
    monkeypatch.setattr(
        domain_rules,
        "load_customer_codes",
        lambda dataset_dir=None: ("C1", "C2", "C3"),
    )

    result = load_reference_values("customer", dataset_dir="/fake/dir")
    assert result == {"C1", "C2", "C3"}
    assert isinstance(result, set)


def test_load_reference_values_supplier_and_product(monkeypatch):
    from backend.config import domain_rules

    monkeypatch.setattr(
        domain_rules, "load_supplier_codes", lambda dataset_dir=None: ("S1", "S2")
    )
    monkeypatch.setattr(
        domain_rules, "load_product_codes", lambda dataset_dir=None: ("P1",)
    )
    assert load_reference_values("supplier") == {"S1", "S2"}
    assert load_reference_values("product") == {"P1"}


def test_domain_rules_load_customer_codes_against_mocked_master(monkeypatch, tmp_path):
    """
    Exercises the *real* load_customer_codes() body (header detection +
    column matching + code extraction) against an in-memory mock of
    read_excel_file, standing in for an actual Customer List.xls.

    FLAG: this is a mock, not the real master file. The column-matching
    logic (_find_column) has not been confirmed against the real workbook's
    header row/text -- do that before relying on this in production.
    """
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()

    mock_raw = pd.DataFrame(
        [
            ["Customer No.", "Name"],
            ["C1", "Acme"],
            ["C2", "Globex"],
            [None, "Blank row"],
        ]
    )

    def fake_read_excel_file(path):
        return {"Sheet1": mock_raw}

    monkeypatch.setattr(domain_rules, "read_excel_file", fake_read_excel_file)
    # Make the master file "exist" for the .exists() check.
    fake_path = tmp_path / "Customer List.xls"
    fake_path.write_text("stub")

    codes = domain_rules.load_customer_codes(dataset_dir=str(tmp_path))
    assert set(codes) == {"C1", "C2"}


def test_domain_rules_load_customer_codes_missing_file_returns_empty(tmp_path):
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()
    codes = domain_rules.load_customer_codes(dataset_dir=str(tmp_path))
    assert codes == tuple()


def test_domain_rules_loader_swallows_read_errors(monkeypatch, tmp_path):
    """A corrupt/unreadable master file must not crash the loader -- degrade
    to an empty reference set (which check_referential_integrity then
    reports as status="error", not a false pass/fail)."""
    from backend.config import domain_rules

    domain_rules.load_supplier_codes.cache_clear()

    def broken_read_excel_file(path):
        raise ValueError("corrupt workbook")

    monkeypatch.setattr(domain_rules, "read_excel_file", broken_read_excel_file)
    fake_path = tmp_path / "Supplier List.xls"
    fake_path.write_text("stub")

    codes = domain_rules.load_supplier_codes(dataset_dir=str(tmp_path))
    assert codes == tuple()


def test_end_to_end_check_using_mocked_loader(monkeypatch):
    """Full flow: load_reference_values("customer") feeds directly into
    check_referential_integrity, matching the real usage this module
    documents (Booked Orders / Invoice List Customer No. vs Customer List)."""
    from backend.config import domain_rules

    monkeypatch.setattr(
        domain_rules,
        "load_customer_codes",
        lambda dataset_dir=None: ("C1", "C2"),
    )

    orders = pd.DataFrame({"Customer No.": ["C1", "C2", "C999"]})
    ref = load_reference_values("customer")
    result = check_referential_integrity(orders, "Customer No.", ref)
    assert result.status == "failed"
    assert result.issues_found == 1
    assert result.details["row_indices"] == [2]


def test_check_referential_name_vs_code_reference_type_mismatch():
    """Direct API: name-shaped column vs code reference must not 100% fail."""
    df = pd.DataFrame(
        {
            "Main Customer": [
                "Guru Systems Ltd",
                "Circatron Ltd",
                "Easby Electronics Ltd",
            ]
        }
    )
    result = check_referential_integrity(
        df, "Main Customer", {"C00001", "C00002", "C00095"}
    )
    assert result.status == "passed"
    assert result.issues_found == 0
    assert result.details.get("reason") == "reference_type_mismatch"


def test_run_pipeline_skips_empty_sheet_gracefully(tmp_path, capsys):
    """Empty sheet must not crash the multi-sheet pipeline."""
    import backend.main as main
    from backend.engine.checkpoint import UserPrompt

    class Auto(UserPrompt):
        def confirm(self, message, details):
            return True

        def ask_int(self, message, default=None):
            return default if default is not None else 0

        def ask_text(self, message, default=None):
            return default if default is not None else ""

    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([["Name"], ["Ali"]]).to_excel(
            writer, sheet_name="Sheet1", index=False, header=False
        )
        pd.DataFrame().to_excel(writer, sheet_name="Sheet2", index=False, header=False)
        pd.DataFrame([["City"], ["Lahore"]]).to_excel(
            writer, sheet_name="Sheet3", index=False, header=False
        )

    main.run_pipeline(str(path), prompt=Auto())
    out = capsys.readouterr().out
    assert "Sheet: Sheet2" in out
    assert "Skipped: empty or no header found" in out
    assert "Sheet: Sheet3" in out
    assert "Done: Task 1-6 completed." in out


def test_name_shaped_column_not_compared_to_code_only_reference(monkeypatch):
    """
    Regression: \"Main Customer\" holding company names must not be checked
    against a customer-*code* reference set (that produced a false 100% fail).
    With no name reference available, the column is skipped -- not flagged.
    """
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()
    domain_rules.load_customer_names.cache_clear()
    monkeypatch.setattr(
        domain_rules,
        "load_customer_codes",
        lambda dataset_dir=None: ("C00001", "C00002", "C00095"),
    )
    monkeypatch.setattr(
        domain_rules,
        "load_customer_names",
        lambda dataset_dir=None: tuple(),
    )

    df = pd.DataFrame(
        {
            "Main Customer": [
                "Guru Systems Ltd",
                "Circatron Ltd",
                "Easby Electronics Ltd",
            ]
        }
    )
    matched, skipped = domain_rules.reference_lists_for_frame(df, dataset_dir=None)
    assert "Main Customer" not in matched
    assert "Main Customer" in skipped
    assert "reference type mismatch" in skipped["Main Customer"]


def test_name_shaped_column_uses_company_name_reference_when_available(monkeypatch):
    """Option (b): name-shaped Main Customer matches against Company Name list."""
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()
    domain_rules.load_customer_names.cache_clear()
    monkeypatch.setattr(
        domain_rules,
        "load_customer_codes",
        lambda dataset_dir=None: ("C00001", "C00002"),
    )
    monkeypatch.setattr(
        domain_rules,
        "load_customer_names",
        lambda dataset_dir=None: (
            "Guru Systems Ltd",
            "Circatron Ltd",
            "Easby Electronics Ltd",
        ),
    )

    df = pd.DataFrame(
        {
            "Main Customer": [
                "Guru Systems Ltd",
                "Circatron Ltd",
                "Ghost Customer Ltd",
            ]
        }
    )
    matched, skipped = domain_rules.reference_lists_for_frame(df, dataset_dir=None)
    assert "Main Customer" not in skipped
    assert "Main Customer" in matched
    assert set(matched["Main Customer"]) == {
        "Guru Systems Ltd",
        "Circatron Ltd",
        "Easby Electronics Ltd",
    }

    result = check_referential_integrity(
        df, "Main Customer", set(matched["Main Customer"])
    )
    assert result.status == "failed"
    assert result.issues_found == 1
    assert result.details["sample_invalid_values"] == ["Ghost Customer Ltd"]


def test_code_shaped_main_customer_still_uses_code_reference(monkeypatch):
    """Same header can hold codes (Stock Report) -- still match codes."""
    from backend.config import domain_rules

    domain_rules.load_customer_codes.cache_clear()
    domain_rules.load_customer_names.cache_clear()
    monkeypatch.setattr(
        domain_rules,
        "load_customer_codes",
        lambda dataset_dir=None: ("C00095", "C00016", "C00015"),
    )
    monkeypatch.setattr(
        domain_rules,
        "load_customer_names",
        lambda dataset_dir=None: ("Texecom Ltd", "Guru Systems Ltd"),
    )

    df = pd.DataFrame({"Main Customer": ["C00095", "C00016", "DEAD", "C00015"]})
    matched, skipped = domain_rules.reference_lists_for_frame(df, dataset_dir=None)
    assert "Main Customer" not in skipped
    assert set(matched["Main Customer"]) == {"C00095", "C00016", "C00015"}
    result = check_referential_integrity(
        df, "Main Customer", set(matched["Main Customer"])
    )
    assert result.status == "failed"
    assert result.issues_found == 1
    assert "DEAD" in result.details["sample_invalid_values"]
