"""Deterministic CA UAT matrix for the Phase 6A Return Audit evidence chain."""

from copy import deepcopy
from time import perf_counter

import pytest

from engine.tds_compliance.return_audit import parse_return_artifact
from engine.tds_compliance.return_reconciliation import reconcile
from tests.test_tds_phase6a2_reconciliation import ROOT, fixture


@pytest.mark.parametrize("filename", ["26QQ1.zip", "27QQ3.zip", "140RQ1.txt", "144RQ1.txt"])
def test_official_artifact_uat_chain_is_traceable(filename):
    parsed = parse_return_artifact((ROOT / filename).read_bytes(), filename)
    assert parsed["parser_status"] == "VALID"
    args = fixture(filename)
    result = reconcile(**args)
    assert result["summary"]["total_return_rows"] == len(parsed["return_rows"])
    assert result["summary"]["status_count_total"] == result["summary"]["total_audit_results"]
    for item in result["items"]:
        assert item["return_artifact_id"] == filename
        assert item["source_references"]["return"]["source_row_number"]
        assert item["payment_transaction_id"]
        assert item["calculation_result_id"]
        assert item["deposit_evidence_id"]
        assert item["interest_result_id"]


@pytest.mark.parametrize(
    ("scenario", "mutate", "expected"),
    [
        ("MATCHED", lambda a: None, "MATCHED"),
        ("TAX_DIFFERENCE", lambda a: a["calculation_rows"][0].update(expected_tds=99), "DIFFERENCE"),
        ("AMOUNT_DIFFERENCE", lambda a: a["ledger_rows"][0].update(amount=99), "DIFFERENCE"),
        ("IDENTITY_DIFFERENCE", lambda a: a["ledger_rows"][0].update(deductee_pan="ZZZZZ9999Z"), "REVIEW_REQUIRED"),
        ("SECTION_DIFFERENCE", lambda a: a["calculation_rows"][0].update(section_reference="OTHER"), "DIFFERENCE"),
        ("DATE_DIFFERENCE", lambda a: a["calculation_rows"][0].update(effective_event_date="2000-01-01"), "DIFFERENCE"),
        ("MISSING_IN_RETURN", lambda a: a["return_rows"].pop(0), "MISSING_IN_RETURN"),
        ("MISSING_IN_BOOKS", lambda a: a["return_rows"][0].update(source_reference="UNKNOWN", deductee_pan="ZZZZZ9999Z"), "MISSING_IN_BOOKS"),
        ("MISSING_IN_DEPOSIT_EVIDENCE", lambda a: a.update(deposit_rows=[]), "MISSING_IN_DEPOSIT_EVIDENCE"),
        ("REVIEW_REQUIRED", lambda a: a.update(calculation_rows=[]), "REVIEW_REQUIRED"),
        ("CONFLICT", lambda a: a["return_rows"][0].update(transaction_id="PAY-1"), "CONFLICT"),
        ("CORRECTION_REVIEW_REQUIRED", lambda a: a["context"].update(statement_type="CORRECTION"), "CORRECTION_REVIEW_REQUIRED"),
    ],
)
def test_complete_uat_status_matrix(scenario, mutate, expected):
    args = fixture("27QQ3.zip")
    mutate(args)
    result = reconcile(**args)
    assert expected in {item["overall_status"] for item in result["items"]}, scenario
    for item in result["items"]:
        assert "source_references" in item
        if item["overall_status"] in {"REVIEW_REQUIRED", "CONFLICT", "CORRECTION_REVIEW_REQUIRED", "MISSING_IN_DEPOSIT_EVIDENCE"}:
            assert item["review_reasons"], scenario


def _large_fixture(size):
    args = fixture("27QQ3.zip")
    source_return, source_book, source_calc, source_deposit, source_interest = (
        args["return_rows"][0], args["ledger_rows"][0], args["calculation_rows"][0], args["deposit_rows"][0], args["interest_rows"][0]
    )
    args.update(return_rows=[], ledger_rows=[], calculation_rows=[], deposit_rows=[], interest_rows=[])
    for index in range(size):
        transaction_id = f"PERF-{index:05d}"
        row = {**source_return, "source_reference": transaction_id, "source_row_number": index + 1}
        book = {**source_book, "transaction_id": transaction_id, "source_reference": transaction_id}
        calculation = {**source_calc, "transaction_id": transaction_id}
        deposit = deepcopy(source_deposit); deposit["transaction_id"] = transaction_id; deposit["liability_ids"] = [transaction_id]
        interest = {**source_interest, "transaction_id": transaction_id}
        args["return_rows"].append(row); args["ledger_rows"].append(book); args["calculation_rows"].append(calculation); args["deposit_rows"].append(deposit); args["interest_rows"].append(interest)
    return args


@pytest.mark.parametrize("size,limit_seconds", [(1_000, 4.0), (10_000, 20.0)])
def test_large_return_uses_indexed_reconciliation(size, limit_seconds):
    args = _large_fixture(size)
    started = perf_counter()
    result = reconcile(**args)
    elapsed = perf_counter() - started
    assert len(result["items"]) == size
    assert result["summary"]["matched"] == size
    assert elapsed < limit_seconds, f"{size} rows took {elapsed:.2f}s"
