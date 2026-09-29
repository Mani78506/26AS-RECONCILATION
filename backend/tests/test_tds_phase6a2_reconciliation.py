from copy import deepcopy
from pathlib import Path

import pytest

from engine.tds_compliance.return_audit import parse_return_artifact
from engine.tds_compliance.return_reconciliation import reconcile

ROOT = Path(__file__).parent / "fixtures" / "official_return_samples"


def fixture(filename):
    parsed = parse_return_artifact((ROOT / filename).read_bytes(), filename)
    rows = deepcopy(parsed["return_rows"])
    books, calculations, deposits, interests = [], [], [], []
    for i, r in enumerate(rows):
        # Synthetic authoritative source-reference enrichment; source files stay untouched.
        tx = f"PAY-{i}"
        r["source_reference"] = tx
        books.append(
            {
                "transaction_id": tx,
                "ledger_version_id": "L1",
                "deductee_pan": r["deductee_pan"],
                "amount": r["amount_paid_or_credited"],
                "source_reference": tx,
            }
        )
        calculations.append(
            {
                "transaction_id": tx,
                "calculation_id": "C1",
                "calculation_status": "CALCULATED",
                "expected_tds": r["total_tax_deducted"],
                "tds_deducted": r["total_tax_deducted"],
                "section_reference": r["section"],
                "effective_event_date": r["payment_or_credit_date"],
            }
        )
        c = parsed["challans"][0]
        deposits.append(
            {
                "transaction_id": tx,
                "deposit_run_id": "D1",
                "relationship_status": "ESTABLISHED",
                "liability_ids": [tx],
                "deposited_tds": r["total_tax_deposited"],
                "evidence_entries": [
                    {
                        "evidence_id": f"E{i}",
                        "challan_number": c["challan_serial_number"],
                        "bsr_code": c["bsr_code"],
                        "deposit_date": c["challan_deposit_date"],
                        "total_amount": c["challan_amount"],
                        "tds_amount": r["total_tax_deposited"],
                    }
                ],
            }
        )
        interests.append(
            {
                "transaction_id": tx,
                "interest_run_id": "I1",
                "overall_status": "NO_INTEREST_INDICATED",
            }
        )
    return dict(
        return_rows=rows,
        calculation_rows=calculations,
        ledger_rows=books,
        challans=parsed["challans"],
        deposit_rows=deposits,
        interest_rows=interests,
        context={"return_artifact_id": filename, "statement_type": "REGULAR"},
    )


@pytest.mark.parametrize(
    "filename", ["26QQ1.zip", "27QQ3.zip", "140RQ1.txt", "144RQ1.txt"]
)
@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "tax",
        "amount",
        "section",
        "date",
        "missing_return",
        "missing_books",
        "ambiguous",
        "missing_deposit",
        "correction",
    ],
)
def test_official_fixture_reconciliation(filename, case):
    args = fixture(filename)
    if case == "tax":
        args["calculation_rows"][0]["expected_tds"] += 1
    if case == "amount":
        args["ledger_rows"][0]["amount"] += 1
    if case == "section":
        args["calculation_rows"][0]["section_reference"] = "OTHER"
    if case == "date":
        args["calculation_rows"][0]["effective_event_date"] = "2000-01-01"
    if case == "missing_return":
        args["return_rows"].pop(0)
    if case == "missing_books":
        args["return_rows"][0].update(
            source_reference="UNKNOWN", deductee_pan="ZZZZZ9999Z"
        )
    if case == "ambiguous":
        args["ledger_rows"].append(deepcopy(args["ledger_rows"][0]))
    if case == "missing_deposit":
        args["deposit_rows"] = []
    if case == "correction":
        args["context"].update(
            statement_type="CORRECTION", return_artifact_id="CORRECTION-1"
        )
        args["return_rows"].pop(0)
    before = deepcopy(args)
    result = reconcile(**args)
    assert args == before
    if case == "exact":
        assert all(
            r["tax_status"] == "MATCHED" and r["payment_status"] == "MATCHED"
            for r in result["items"]
        )
        if filename in {"140RQ1.txt", "144RQ1.txt"}:
            assert (
                result["items"][0]["challan_status"] == "REVIEW_REQUIRED"
            )  # Official source omits serial number.
        else:
            assert result["summary"]["matched"] == len(args["return_rows"])
    if case == "tax":
        assert result["items"][0]["tax_status"] == "TAX_DIFFERENCE"
    if case in {"amount", "section", "date"}:
        assert result["summary"][f"{case}_differences"] == 1
    if case == "missing_return":
        assert result["summary"]["missing_in_return"] == 1
    if case == "missing_books":
        assert result["summary"]["missing_in_books"] == 1
    if case == "ambiguous":
        assert result["items"][0]["identity_status"] == "REVIEW_REQUIRED"
    if case == "missing_deposit":
        assert result["summary"]["missing_deposit_evidence"] == len(args["return_rows"])
    if case == "correction":
        assert result["summary"]["missing_in_return"] == 0
        assert all(r["return_artifact_id"] == "CORRECTION-1" for r in result["items"])


def test_missing_snapshots_and_duplicate_return_rows_fail_closed():
    args = fixture("140RQ1.txt")
    args["calculation_rows"] = []
    assert reconcile(**args)["overall_status"] == "REVIEW_REQUIRED"
    args = fixture("140RQ1.txt")
    args["return_rows"].append(deepcopy(args["return_rows"][0]))
    assert all(
        r["identity_status"] == "REVIEW_REQUIRED" for r in reconcile(**args)["items"]
    )


def test_official_sequence_never_matches_payment_id_and_pan_ambiguity_is_retained():
    args = fixture("26QQ1.zip")
    for row in args["return_rows"]:
        row.pop("source_reference")
    result = reconcile(**args)
    assert result["summary"]["matched"] == 0
    assert result["summary"]["review_rows"] == 4
    assert result["summary"]["missing_in_return"] == 0


def test_missing_values_are_not_zero_and_interest_is_not_recalculated():
    args = fixture("140RQ1.txt")
    args["return_rows"][0]["total_tax_deducted"] = None
    args["return_rows"][0]["tds_amount"] = None
    args["interest_rows"][0]["interest_difference"] = 12.25
    result = reconcile(**args)["items"][0]
    assert result["difference"] is None
    assert result["tax_status"] == "REVIEW_REQUIRED"
    assert result["interest_difference"] == 12.25


def test_challan_ambiguity_and_shared_relationships_are_not_allocated():
    args = fixture("140RQ1.txt")
    args["challans"].append(deepcopy(args["challans"][0]))
    assert reconcile(**args)["items"][0]["challan_status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "field,new_value",
    [("bsr_code", "WRONG"), ("deposit_date", "2000-01-01"), ("total_amount", 1)],
)
def test_challan_field_differences_are_visible(field, new_value):
    args = fixture("27QQ3.zip")
    args["deposit_rows"][0]["evidence_entries"][0][field] = new_value
    assert reconcile(**args)["items"][0]["challan_status"] == "CHALLAN_DIFFERENCE"


def test_unconfirmed_name_and_amount_never_match():
    args = fixture("140RQ1.txt")
    args["return_rows"][0].update(source_reference=None, deductee_pan=None)
    args["ledger_rows"][0]["deductee_name"] = args["return_rows"][0]["deductee_name"]
    assert reconcile(**args)["items"][0]["identity_status"] == "REVIEW_REQUIRED"


def test_document_identity_and_existing_mapping_tiers():
    for method, fields in [
        ("EXACT_DOCUMENT_REFERENCE", {"invoice_number": "INV-1"}),
        ("AUTHORITATIVE_IDENTITY", {"deductee_identity_id": "D-1"}),
        ("EXPLICIT_ASSIGNMENT_MAPPING", {"assigned_payment_transaction_id": "PAY-0"}),
    ]:
        args = fixture("140RQ1.txt")
        args["return_rows"][0].update(
            source_reference=None, deductee_pan=None, **fields
        )
        args["ledger_rows"][0].update(fields)
        result = reconcile(**args)["items"][0]
        assert result["identity_status"] == "CONFIRMED"
        assert result["match_method"] == method


def test_pan_conflict_duplicate_calculation_and_phase4_review_are_retained():
    args = fixture("27QQ3.zip")
    args["ledger_rows"][0]["deductee_pan"] = "ZZZZZ9999Z"
    assert reconcile(**args)["items"][0]["identity_status"] == "REVIEW_REQUIRED"
    args = fixture("27QQ3.zip")
    args["calculation_rows"].append(deepcopy(args["calculation_rows"][0]))
    assert reconcile(**args)["items"][0]["tax_status"] == "REVIEW_REQUIRED"
    args = fixture("27QQ3.zip")
    args["deposit_rows"][0]["overall_status"] = "REVIEW_REQUIRED"
    assert reconcile(**args)["items"][0]["overall_status"] == "REVIEW_REQUIRED"


def test_period_scope_and_deposit_total_do_not_include_another_quarter():
    args = fixture("27QQ3.zip")
    args["context"].update(financial_year="2019-20", quarter="Q3")
    args["ledger_rows"][0].update(
        financial_year="2019-20", quarter="Q2", payment_date="2019-07-01"
    )
    result = reconcile(**args)
    assert result["summary"]["books_payment_amount"] == args["ledger_rows"][1]["amount"]
    assert (
        result["summary"]["approved_deposit_evidence"]
        == args["deposit_rows"][1]["evidence_entries"][0]["tds_amount"]
    )


def test_actual_tds_is_compared_without_replacing_expected_tds():
    args = fixture("27QQ3.zip")
    args["calculation_rows"][0]["tds_deducted"] -= 1
    row = reconcile(**args)["items"][0]
    assert row["tax_status"] == "MATCHED"
    assert row["actual_tds_difference"] == 1
    assert row["overall_status"] == "DIFFERENCE"


def test_summary_deduplicates_shared_evidence_and_preserves_unknown_amounts():
    args = fixture("27QQ3.zip")
    args["deposit_rows"][1]["evidence_entries"] = deepcopy(
        args["deposit_rows"][0]["evidence_entries"]
    )
    args["return_rows"][0]["amount_paid_or_credited"] = None
    result = reconcile(**args)
    assert result["summary"]["return_amount_paid"] is None
    assert (
        result["summary"]["approved_deposit_evidence"]
        == args["deposit_rows"][0]["evidence_entries"][0]["tds_amount"]
    )
    args = fixture("140RQ1.txt")
    args["deposit_rows"][0]["liability_ids"].append("OTHER")
    assert reconcile(**args)["items"][0]["challan_status"] == "REVIEW_REQUIRED"


def test_conflicting_references_and_challan_serial_difference():
    args = fixture("27QQ3.zip")
    args["return_rows"][0]["transaction_id"] = "PAY-1"
    assert reconcile(**args)["items"][0]["identity_status"] == "CONFLICT"
    args = fixture("27QQ3.zip")
    args["deposit_rows"][0]["evidence_entries"][0]["challan_number"] = "DIFFERENT"
    assert reconcile(**args)["items"][0]["challan_status"] == "CHALLAN_DIFFERENCE"


def test_frozen_interest_difference_is_not_reported_as_matched():
    args = fixture("27QQ3.zip")
    args["interest_rows"][0]["interest_difference"] = 1
    result = reconcile(**args)["items"][0]
    assert result["interest_status"] == "INTEREST_DIFFERENCE"
    assert result["overall_status"] == "DIFFERENCE"


def test_partial_calculation_summary_is_unknown():
    args = fixture("27QQ3.zip")
    args["calculation_rows"].pop()
    result = reconcile(**args)
    assert result["summary"]["expected_tds"] is None
    assert result["summary"]["tds_difference"] is None
