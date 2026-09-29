from engine.reconciliation.pipeline import load_and_validate
from engine.shared.schema import BOOKS, FORM26AS, SALES_REGISTRY


def _validate(kind, text, expected_fy=None, workflow=None):
    return load_and_validate(kind, "renamed-source.csv", text.encode(), expected_fy, workflow=workflow)


def test_books_uat_headers_map_to_canonical_fields_and_inherit_assignment_fy():
    report, rows = _validate(BOOKS, "Vouch No.,TDS Receivable in Books (₹),Customer / Party Name,Posting Date\nV-1,1000,Acme Limited,15-04-2025\n", "2025-26")
    assert report["status"] == "VALID"
    assert {item["canonical_field"] for item in report["header_mappings"]} >= {"transaction_id", "tds_expected", "customer_name", "document_date"}
    assert rows[0]["financial_year"] == "2025-26"
    assert rows[0]["financial_year_source"] == "DERIVED_FROM_DATE"


def test_books_assignment_fy_is_recorded_when_no_source_date_exists():
    report, rows = _validate(BOOKS, "Vouch No.,TDS Receivable in Books (₹),Customer / Party Name,Posting Date\nV-1,1000,Acme Limited,\n", "2025-26")
    assert any(issue["field"] == "document_date" for issue in report["issues"])
    assert report["status"] == "BLOCKED"
    assert not rows


def test_books_cross_financial_years_remain_a_review_signal():
    report, _ = _validate(BOOKS, "Vouch No.,TDS Receivable in Books (₹),Customer / Party Name,Posting Date\nV-1,1000,Acme,31-03-2025\nV-2,1000,Acme,01-04-2025\n")
    assert any(issue["problem"] == "Multiple financial years detected" for issue in report["issues"])


def test_26as_uat_headers_map_amount_paid_and_booking_date_is_not_transaction_date():
    report, rows = _validate(FORM26AS, "SI No.,TAN of Deductor,Name of Deductor,Section,Transaction Date,Date of Booking,Total Amount Paid / Credited (₹),Total TDS Deducted (₹),TDS Deposited (₹)\n1,ABCD12345E,Acme Limited,194J,15-04-2025,20-04-2025,100000,1000,1000\n", "2025-26")
    mapped = {item["canonical_field"] for item in report["header_mappings"]}
    assert {"tan", "deductor_name", "transaction_date", "amount_paid", "tax_deducted", "tds_deposited"} <= mapped
    assert "Date of Booking" in report["columns_unmapped"]
    assert rows[0]["amount_paid"] == 100000.0
    assert rows[0]["financial_year_source"] == "DERIVED_FROM_DATE"


def test_sales_customer_party_name_maps_but_posting_date_does_not_become_invoice_date():
    report, _ = _validate(SALES_REGISTRY, "Invoice No.,Customer / Party Name,Posting Date,Sales Amount\nINV-1,Acme Limited,15-04-2025,100000\n", "2025-26", "SALES_TDS_26AS")
    assert "customer_name" in report["columns_mapped"]
    assert "invoice_date" not in report["columns_mapped"]
    assert any(issue["field"] == "invoice_date" for issue in report["issues"])


def test_mapping_is_filename_independent():
    report, _ = load_and_validate(BOOKS, "unrelated-name.xlsx.csv", b"Vouch No.,TDS Receivable in Books (Rs),Customer / Party Name,Posting Date\nV-1,1000,Acme,15-04-2025\n", "2025-26")
    assert {item["canonical_field"] for item in report["header_mappings"]} >= {"transaction_id", "tds_expected", "customer_name", "document_date"}
