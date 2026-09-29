from engine.tds_compliance.return_audit import parse_return_artifact, reconcile_return_rows

def test_structured_return_parser_preserves_source_rows():
    data = b"FORM 26Q,REGULAR\ntransaction_id,deductee_pan,tds_amount,amount_paid\nPAY-1,ABCDE1234F,100,1000\n"
    report = parse_return_artifact(data, "return.csv")
    assert report["parser_status"] == "VALID"
    assert report["source_form"] == "26Q"
    assert report["return_rows"][0]["return_transaction_id"] == "PAY-1"


def test_structured_return_parser_exposes_documentary_matching_references():
    data = b"transaction_id,deductee_pan,tds_amount,payment_reference,invoice_number,document_identifier\nPAY-1,ABCDE1234F,100,PMT-1,INV-1,DOC-1\n"
    row = parse_return_artifact(data, "return.csv")["return_rows"][0]
    assert (row["payment_reference"], row["invoice_number"], row["document_identifier"]) == ("PMT-1", "INV-1", "DOC-1")

def test_fvu_is_review_required_and_not_fabricated():
    report = parse_return_artifact(b"\\x00\\x01binary", "return.fvu")
    assert report["parser_status"] == "REVIEW_REQUIRED"
    assert not report["return_rows"]

def test_return_audit_requires_exact_reference_and_pan():
    rows = [{"return_transaction_id":"PAY-1","deductee_pan":"ABCDE1234F","tds_amount":100,"source_row_number":2}, {"return_transaction_id":"RET-2","deductee_pan":"ABCDE1234F","tds_amount":25,"source_row_number":3}]
    calculations = [{"transaction_id":"PAY-1","deductee_pan":"ABCDE1234F","expected_tds":100,"calculation_id":"C1","ledger_version_id":"L1"}, {"transaction_id":"PAY-3","deductee_pan":"ABCDE1234F","expected_tds":30,"calculation_id":"C1","ledger_version_id":"L1"}]
    results = reconcile_return_rows(rows, calculations, [])
    assert {row["status"] for row in results} == {"MATCHED", "MISSING_IN_BOOKS", "MISSING_IN_RETURN"}
from pathlib import Path
from engine.tds_compliance.return_audit import parse_return_artifact

FIXTURES = {
    "26QQ1.zip": {"form":"26Q", "fy":"2020-21", "quarter":"Q1", "rows":4, "tax":1000.0},
    "27QQ3.zip": {"form":"27Q", "fy":"2019-20", "quarter":"Q3", "rows":2, "tax":11000.0},
    "140RQ1.txt": {"form":"FORM_140", "fy":"2026-27", "quarter":"Q1", "rows":1, "tax":900.0},
    "144RQ1.txt": {"form":"FORM_144", "fy":"2026-27", "quarter":"Q1", "rows":1, "tax":2000.0},
}

def test_official_protean_samples_parse_with_raw_records_and_confirmed_metadata():
    root=Path(__file__).parent/"fixtures"/"official_return_samples"
    for filename, expected in FIXTURES.items():
        report=parse_return_artifact((root/filename).read_bytes(), filename)
        assert report["parser_status"] == "VALID"
        assert report["source_form"] == expected["form"]
        assert report["statement_type"] == "REGULAR"
        assert report["metadata"]["normalized_financial_year"] == expected["fy"]
        assert report["metadata"]["quarter"] == expected["quarter"]
        assert len(report["return_rows"]) == expected["rows"]
        assert len(report["challans"]) == 1
        assert {record["record_category"] for record in report["records"]} == {"HEADER","DEDUCTOR","CHALLAN","DEDUCTEE"}
        assert all(row["raw_record"] and row["source_line_number"] for row in report["return_rows"])
        assert report["return_rows"][0]["total_tax_deducted"] == expected["tax"]

def test_malformed_and_ambiguous_official_inputs_fail_safely():
    source=b"1^FH^NS1^R^01012020\n2^BH^1^1^26Q\n"
    report=parse_return_artifact(source,"bad.txt")
    assert report["parser_status"] == "PARSE_ERROR"
    import io, zipfile
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,"w") as archive:
        archive.writestr("one.txt",b"1^FH^")
        archive.writestr("two.txt",b"1^FH^")
    assert parse_return_artifact(payload.getvalue(),"ambiguous.zip")["parser_status"] == "REVIEW_REQUIRED"


def test_correction_marker_is_kept_separate_from_regular_statement():
    source=(Path(__file__).parent / "fixtures" / "official_return_samples" / "140RQ1.txt").read_text(encoding="ascii")
    correction=source.replace("^R^", "^C^", 1)
    report=parse_return_artifact(correction.encode("ascii"), "140-correction.txt")
    assert report["parser_status"] == "VALID"
    assert report["statement_type"] == "CORRECTION"
