from io import BytesIO
import pandas as pd

from engine.tds_compliance.government_evidence import SOURCE_TYPE, validate_government_tax_credit_summary


HEADERS = ["Sr. No.", "Name of Deductor", "TAN of Deductor", "Total Amount Paid/Credited", "Total Tax Deducted", "Total TDS Deposited", "Office Note"]
ROW = [1, "Example Deductor", "ABCD12345E", 100000, 1000, 1000, "retained"]


def workbook(headers=HEADERS, rows=(ROW,)):
    stream = BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        pd.DataFrame([list(headers), *rows]).to_excel(writer, index=False, header=False, sheet_name="Summary")
    return stream.getvalue()


def test_valid_summary_maps_only_approved_fields_and_preserves_unknown_columns():
    report = validate_government_tax_credit_summary("any-name.xlsx", workbook())
    assert report["can_commit"] is True
    assert report["file"]["source_type"] == SOURCE_TYPE
    assert set(report["canonical_mappings"].values()) == {"sr_no", "deductor_name", "deductor_tan", "amount_paid_credited", "tax_deducted", "tds_deposited"}
    assert "Office Note" in report["unmapped_columns"]
    row = report["rows"][0]
    assert row["canonical_values"]["tax_deducted"] == 1000.0
    assert row["source_values"]["Office Note"] == "retained"


def test_safe_header_normalisation_and_filename_independence():
    headers = [" SR NO ", "DEDUCTOR NAME", "DEDUCTOR TAN", "Total Amount Paid / Credited", "Total Tax Deducted", "Total TDS Deposited"]
    one = validate_government_tax_credit_summary("a.xlsx", workbook(headers, [ROW[:6]]))
    two = validate_government_tax_credit_summary("renamed-client-file.xlsx", workbook(headers, [ROW[:6]]))
    assert one["canonical_mappings"] == two["canonical_mappings"]
    assert one["rows"][0]["canonical_values"] == two["rows"][0]["canonical_values"]


def test_missing_required_header_is_not_guessed():
    headers = ["Sr. No.", "Name of Deductor", "TAN of Deductor", "Payment Amount", "Total Tax Deducted", "Total TDS Deposited"]
    report = validate_government_tax_credit_summary("summary.xlsx", workbook(headers, [ROW[:3] + [100000, 1000, 1000]]))
    assert report["can_commit"] is False
    assert any("amount_paid_credited" in message for message in report["validation_messages"])


def test_invalid_tan_and_amount_are_invalid_not_zero():
    report = validate_government_tax_credit_summary("summary.xlsx", workbook(rows=([1, "Example", "bad", "not-an-amount", 10, 10, None],)))
    row = report["rows"][0]
    assert row["row_status"] == "INVALID"
    assert row["deductor_tan"] == "BAD"
    assert row["amount_paid_credited"] is None


def test_missing_values_remain_none_and_duplicate_is_reviewed():
    data = [ROW, ROW]
    report = validate_government_tax_credit_summary("summary.xlsx", workbook(rows=data))
    assert report["summary"]["duplicate_rows"] == 2
    assert all(row["row_status"] == "WARNING" for row in report["rows"])
    missing = validate_government_tax_credit_summary("summary.xlsx", workbook(rows=([1, "Example", None, None, None, None, None],)))
    item = missing["rows"][0]
    assert item["deductor_tan"] is None and item["tax_deducted"] is None and item["tds_deposited"] is None
