from engine.reconciliation.pipeline import inspect_upload_source
from engine.shared.schema import BOOKS
import io

import pandas as pd


def test_source_inspection_maps_headers_without_running_validation():
    inspected = inspect_upload_source(
        BOOKS,
        "books.csv",
        b"Vouch No.,TDS Receivable in Books (Rs),Customer / Party Name,Posting Date\nV-1,1000,Acme,15-04-2025\n",
    )

    assert inspected["readable"] is True
    assert inspected["row_count"] == 1
    assert {"transaction_id", "tds_expected", "customer_name", "document_date"} <= set(inspected["columns_mapped"])
    assert inspected["semantic_profile"] is not None


def test_source_inspection_reports_an_unreadable_upload_without_raising():
    inspected = inspect_upload_source(BOOKS, "books.xlsx", b"not an xlsx")

    assert inspected["readable"] is False
    assert inspected["diagnostic"]


def test_source_inspection_reports_progress_and_does_not_create_row_records():
    stages = []
    inspected = inspect_upload_source(
        BOOKS,
        "ledger.csv",
        b"Ledger,Voucher Date,Voucher No,Debit,Credit\nTDS Receivable,15-04-2025,V-1,100,\n",
        lambda stage, processed, total: stages.append((stage, processed, total)),
    )

    assert stages == [("OPENING_WORKBOOK", 0, None), ("DETECTING_COLUMNS", 0, 1)]
    assert inspected["row_count"] == 1
    assert inspected["source_processing_stage"] == "SOURCE_PROFILE_READY"


def test_xlsx_source_inspection_selects_the_best_header_sheet_once():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([["cover sheet"]]).to_excel(writer, sheet_name="Cover", header=False, index=False)
        pd.DataFrame([["Ledger", "Voucher Date", "Voucher No", "Debit"], ["TDS Receivable", "15-04-2025", "V-1", 100]]).to_excel(writer, sheet_name="Ledger", header=False, index=False)

    inspected = inspect_upload_source(BOOKS, "any-name.xlsx", buffer.getvalue())

    assert inspected["source"]["sheet_name"] == "Ledger"
    assert inspected["row_count"] == 1
    assert {"source_ledger", "document_date", "transaction_id", "debit_amount"} <= set(inspected["columns_mapped"])
