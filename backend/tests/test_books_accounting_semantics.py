from engine.reconciliation.pipeline import load_and_validate
from engine.shared.schema import BOOKS, FORM26AS, SALES_REGISTRY


LEDGER = b"Ledger,Voucher Date,Voucher No,Voucher Type,Debit,Credit,Narration,Month\nTDS Receivable,15-04-2025,V-1,Receipt,10000,,Customer receipt,Apr-25\nTDS Receivable,16-04-2025,V-2,Journal,,2000,Reversal,Apr-25\nProfessional Income,17-04-2025,V-3,Receipt,50000,,Income receipt,Apr-25\nTDS Receivable,18-04-2025,V-4,Journal,,,Opening Balance,Apr-25\n"


def test_full_reconciliation_derives_only_explicit_tds_receivable_debits():
    report, rows = load_and_validate(BOOKS, "any-ledger-name.csv", LEDGER, "2025-26", workflow="FULL_RECONCILIATION")

    assert report["status"] == "READY_WITH_WARNINGS"
    assert report["row_summary"] == {
        "transaction_rows": 4, "valid_rows": 1, "warning_rows": 0,
        "exception_rows": 0, "excluded_non_transaction_rows": 2,
        "tds_receivable_rows": 2, "tds_receivable_debit_rows": 1,
        "tds_receivable_credit_rows": 1, "accounting_role_review_rows": 1,
    }
    assert report["columns_mapped"] == ["source_ledger", "document_date", "transaction_id", "voucher_type", "debit_amount", "credit_amount", "narration", "source_period"]
    assert len(rows) == 1
    debit = rows[0]
    assert debit["accounting_role"] == "TDS_RECEIVABLE"
    assert debit["tds_expected"] == 10000.0
    assert debit["tds_receivable_debit"] == 10000.0
    assert debit["tds_receivable_credit"] is None
    assert debit["source_ledger"] == "TDS Receivable"
    assert debit["customer_name"] == ""
    assert any(issue["problem"].endswith("require identity review") for issue in report["issues"])


def test_full_reconciliation_never_infers_tds_from_generic_debit_credit():
    content = b"Ledger,Voucher Date,Voucher No,Debit,Credit\nProfessional Income,15-04-2025,V-1,10000,\n"
    report, rows = load_and_validate(BOOKS, "independent.csv", content, "2025-26", workflow="FULL_RECONCILIATION")

    assert report["status"] == "BLOCKED"
    assert not rows
    assert any(issue["problem"] == "SOURCE SEMANTIC REVIEW REQUIRED" for issue in report["issues"])


def test_accounting_semantics_do_not_run_outside_full_books_workflow():
    report, rows = load_and_validate(BOOKS, "same-file.csv", LEDGER, "2025-26", workflow="SALES_TDS_26AS")

    assert not rows
    assert "source_ledger" in report["columns_mapped"]
    assert report["row_summary"]["tds_receivable_rows"] is None
    assert all(issue["problem"] != "SOURCE SEMANTIC REVIEW REQUIRED" for issue in report["issues"])


def test_26as_and_sales_normalization_remain_unchanged_by_books_semantics():
    as_content = b"TAN of Deductor,Name of Deductor,Transaction Date,Total TDS Deducted (Rs)\nABCD12345E,Acme,15-04-2025,1000\n"
    sales_content = b"Invoice No.,Customer / Party Name,Invoice Date,Sales Amount\nI-1,Acme,15-04-2025,100000\n"
    as_default = load_and_validate(FORM26AS, "statement.csv", as_content, "2025-26")
    as_full = load_and_validate(FORM26AS, "statement.csv", as_content, "2025-26", workflow="FULL_RECONCILIATION")
    sales_default = load_and_validate(SALES_REGISTRY, "sales.csv", sales_content, "2025-26", workflow="SALES_TDS_26AS")
    sales_full = load_and_validate(SALES_REGISTRY, "sales.csv", sales_content, "2025-26", workflow="FULL_RECONCILIATION")

    assert as_default == as_full
    assert sales_default == sales_full
