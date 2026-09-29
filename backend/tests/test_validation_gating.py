from engine.reconciliation.pipeline import load_and_validate
from engine.shared.schema import BOOKS, FORM26AS


BOOKS_HEADER = "Vouch No.,TDS Receivable in Books (₹),Customer / Party Name,Posting Date\n"
AS_HEADER = "TAN of Deductor,Name of Deductor,Transaction Date,Total TDS Deducted (₹)\n"


def test_books_bad_transaction_date_is_a_row_exception_and_valid_rows_continue():
    report, rows = load_and_validate(BOOKS, "books.csv", (BOOKS_HEADER + "V-1,100,Acme,15-04-2025\nV-2,200,Acme,\n").encode(), "2025-26")
    assert report["status"] == "READY_WITH_EXCEPTIONS"
    assert report["row_summary"]["valid_rows"] == 1
    assert report["row_summary"]["exception_rows"] == 1
    assert len(rows) == 1 and rows[0]["transaction_id"] == "V-1"
    assert report["row_exceptions"][0]["field"] == "document_date"


def test_26as_bad_tan_and_date_are_row_exceptions_and_valid_rows_continue():
    report, rows = load_and_validate(FORM26AS, "statement.csv", (AS_HEADER + "ABCD12345E,Acme,15-04-2025,100\nINVALID,Acme,,200\n").encode(), "2025-26")
    assert report["status"] == "READY_WITH_EXCEPTIONS"
    assert report["row_summary"]["valid_rows"] == 1
    assert report["row_summary"]["exception_rows"] == 1
    assert len(rows) == 1 and rows[0]["tan"] == "ABCD12345E"
    assert {exception["field"] for exception in report["row_exceptions"]} == {"transaction_date", "tan"}


def test_missing_required_header_remains_blocked():
    report, rows = load_and_validate(BOOKS, "books.csv", b"Vouch No.,Customer / Party Name,Posting Date\nV-1,Acme,15-04-2025\n", "2025-26")
    assert report["status"] == "BLOCKED"
    assert rows


def test_total_row_is_excluded_before_transaction_validation():
    report, rows = load_and_validate(BOOKS, "books.csv", (BOOKS_HEADER + "V-1,100,Acme,15-04-2025\nTotal,100,Total,\n").encode(), "2025-26")
    assert report["status"] == "VALID"
    assert len(rows) == 1
    assert not report["row_exceptions"]
