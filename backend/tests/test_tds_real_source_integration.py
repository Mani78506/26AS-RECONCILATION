from __future__ import annotations

from io import BytesIO

import pandas as pd

from engine.tds_compliance.core import validate_payment_ledger


def _workbook(rows, *, name="TDS", metadata=True, extra_sheet=True):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        values = []
        if metadata:
            values = [
                ["COMPANY NAME:", "ACNMPL"],
                ["GL Code:", "0002041002"],
                ["GL Description:", "TDS Payable 94C-Contractor Company Deductees"],
                ["Period:", "01.04.2025 To 31.03.2026"],
                [None, None, None, None, "Opening Balance", None, 0, 100],
                [],
            ]
        header = ["Posting date", "Document no", "Cheque No", "Offsetting Account", "Offsetting Description", "Narration", "Debit Amount", "Credit Amount", "Closing Balance", "Project Name", "Cr/Dr", "GSTIN", "Invoice Number", "Invoice Date", "Document Type", "Payee Name", "Purchase Order Number"]
        pd.DataFrame(values + [header] + rows).to_excel(writer, sheet_name=name, index=False, header=False)
        if extra_sheet:
            pd.DataFrame([["Row Labels", "Sum of Debit Amount"], ["Grand Total", 99]]).to_excel(writer, sheet_name="Pivot", index=False, header=False)
    return output.getvalue()


def test_tds_payable_gl_profile_detects_metadata_and_excludes_non_transactions():
    report = validate_payment_ledger("2041002_94C - Contractor company deducteed.xlsx", _workbook([
        ["2025-04-19", "2800000022", None, "0024006148", "SHAW HOTELS", "Inv.No:BN48", 0, 112, 212, "Project", "CR", "27AABCS4495D1ZA", "BN48", "2025-04-11", "Vendor Invoice", None, None],
        ["2025-04-20", "4000000001", "CH-1", "0001072062", "ICICI Bank", "TDS paid for Apr", 112, 0, 100, "Project", "CR", None, "CONT-CO", "2025-04-20", "TDS Payment", None, None],
        [None, None, None, None, "Grand Total", None, 112, 112],
    ]))

    assert report["file"]["source_profile"] == "TDS_PAYABLE_GL"
    assert report["file"]["metadata"]["tds_payable_account"] == "0002041002"
    assert report["file"]["header_row"] == 7
    assert report["summary"]["transaction_rows"] == 2
    assert report["summary"]["ignored_rows"] >= 1
    invoice, payment = report["rows"]
    assert invoice["deductee_name"] == "SHAW HOTELS"
    assert invoice["deductee_gstin"] == "27AABCS4495D1ZA"
    assert invoice["invoice_number"] == "BN48"
    assert invoice["tds_deducted"] == 112.0
    assert invoice["amount"] is None
    assert payment["source_transaction_kind"] == "TDS_PAYMENT"
    assert payment["tds_deposited"] == 112.0
    assert payment["deductee_name"] is None
    assert report["can_commit"] is True


def test_gl_debit_is_preserved_as_directional_evidence_not_gross_payment():
    report = validate_payment_ledger("2041018_194Q- Purchase of Material .1.xlsx", _workbook([
        ["2025-04-03", "3400000005", None, "0022001342", "Aparna Enterprises", "invoice reversal", 123, 0, 100, "Project", "CR", "24AABCA9108R1ZD", "1108000737", "2025-03-31", "MM-Invoice", None, "PO-1"],
    ]))
    row = report["rows"][0]
    assert row["source_debit_amount"] == 123.0
    assert row["source_credit_amount"] == 0.0
    assert row["amount"] is None
    assert row["tds_deducted"] is None
    assert row["validation_status"] == "WARNING"
    assert report["can_commit"] is True


def test_vendor_ledger_preserves_vendor_pan_gstin_invoice_and_actual_tds():
    output = BytesIO()
    frame = pd.DataFrame([
        ["Vendor Code", "Vendor Name", "PAN", "GSTIN", "Invoice Number", "Invoice Date", "Posting Date", "Amount", "TDS Deducted", "Narration"],
        ["V-1", "Sharma Enterprises", "ABCDE1234F", "36ABCDE1234F1Z5", "INV-1", "2025-04-10", "2025-04-12", 100000, 2000, "Contract service"],
        [None, None, None, None, None, None, None, None, None, "Grand Total"],
    ])
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, header=False)
    report = validate_payment_ledger("22002451_Sharma Enterprises.xlsx", output.getvalue())
    row = report["rows"][0]
    assert report["file"]["source_profile"] == "VENDOR_LEDGER"
    assert row["vendor_code"] == "V-1"
    assert row["deductee_pan"] == "ABCDE1234F"
    assert row["deductee_gstin"] == "36ABCDE1234F1Z5"
    assert row["invoice_number"] == "INV-1"
    assert row["tds_deducted"] == 2000.0
    assert report["can_commit"] is True


def test_ca_working_reference_is_never_committable_payment_ledger_data():
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([["Month", "TDS as per Act"], ["April", "=100*0.02"]]).to_excel(writer, index=False, header=False)
    report = validate_payment_ledger("ACPL_TDS Workings_Q4_Final.xlsx", output.getvalue())
    assert report["file"]["source_profile"] == "CA_TDS_WORKING_REFERENCE"
    assert report["can_commit"] is False
    assert report["rows"] == []


def test_duplicate_gl_entries_are_retained_for_review():
    row = ["2025-04-19", "2800000022", None, "0024006148", "SHAW HOTELS", "Inv.No:BN48", 0, 112, 212, "Project", "CR", "27AABCS4495D1ZA", "BN48", "2025-04-11", "Vendor Invoice", None, None]
    report = validate_payment_ledger("2041002_94C - Contractor company deducteed.xlsx", _workbook([row, row]))
    assert report["summary"]["duplicate_rows"] == 2
    assert {item["duplicate_status"] for item in report["rows"]} == {"DUPLICATE_REVIEW"}
