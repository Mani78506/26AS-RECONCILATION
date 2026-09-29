from copy import deepcopy

from engine.tds_compliance.core import calculate_deposit_compliance, validate_deposit_evidence


ASSIGNMENT = {"assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1", "financial_year": "2025-26", "quarter": "Q2", "tan": "ABCD12345E"}
POLICY = {"policy_id": "DEP-POLICY", "policy_version": "1", "active": True, "status": "APPROVED", "financial_years": ["2025-26"], "effective_from": "2025-04-01", "effective_to": "2026-03-31", "priority": 1}
LIABILITY = {"transaction_id": "PAY-1", "financial_year": "2025-26", "quarter": "Q2", "effective_event_date": "2025-06-01", "calculation_status": "CALCULATED", "expected_tds": 100.0, "tds_deducted": 95.0}
EVIDENCE = {"evidence_id": "E-1", "deposit_transaction_id": "PAY-1", "tds_amount": 100.0, "deposit_date": "2025-06-05", "validation_status": "VALID"}


def control(liabilities=None, evidence=None, interest=None, policies=None):
    return calculate_deposit_compliance(liabilities or [LIABILITY], evidence if evidence is not None else [EVIDENCE], interest or [], assignment_id="A-1", calculation_id="CALC-1", evidence_version_id="EV-1", policies=[POLICY] if policies is None else policies)[0]


def test_expected_tds_is_frozen_snapshot_not_actual_or_live_rule_value():
    result = control()
    assert result["expected_tds"] == 100.0
    assert result["actual_tds_deducted"] == 95.0
    assert result["deposited_tds"] == 100.0
    assert result["deposit_difference"] == 0.0
    assert result["deposit_status"] == "DEPOSIT_MATCHED"


def test_exact_reference_only_amount_difference_and_missing_evidence():
    difference = control(evidence=[{**EVIDENCE, "tds_amount": 90.0}])
    missing = control(evidence=[])
    unrelated = control(evidence=[{**EVIDENCE, "deposit_transaction_id": "PAY-OTHER"}])
    assert difference["deposit_status"] == "DEPOSIT_SHORT"
    assert missing["deposit_status"] == "MISSING_DEPOSIT_EVIDENCE"
    assert unrelated["deposit_status"] == "MISSING_DEPOSIT_EVIDENCE"


def test_timing_requires_configured_due_date_and_hands_off_interest():
    result = control(interest=[{"transaction_id": "PAY-1", "overall_status": "INTEREST_REVIEW_REQUIRED"}])
    assert result["timeliness_status"] == "DUE_DATE_NOT_DETERMINABLE"
    assert result["interest_status"] == "INTEREST_REVIEW_REQUIRED"
    assert result["overall_status"] == "NOT_DETERMINABLE"


def test_invalid_date_sequence_is_reviewed_without_guessing_timing():
    result = control(interest=[{"transaction_id": "PAY-1", "actual_deduction_date": "2025-06-10"}])
    assert result["timeliness_status"] == "INVALID_DATE_SEQUENCE"
    assert result["overall_status"] == "REVIEW_REQUIRED"


def test_missing_policy_is_controlled_not_a_legacy_timing_default():
    result = control(policies=[])
    assert result["reason_code"] == "PHASE5_POLICY_NOT_FOUND"
    assert result["overall_status"] == "REVIEW_REQUIRED"


def test_evidence_validation_checks_scope_and_duplicates_without_zero_fill():
    csv = b"transaction_id,challan_number,tds_amount,deposit_date,financial_year,quarter,tan\nPAY-1,CH-1,100,05-06-2025,2025-26,Q2,ABCD12345E\nPAY-1,CH-1,100,05-06-2025,2025-26,Q2,ABCD12345E\n"
    report = validate_deposit_evidence("challan.csv", csv, ASSIGNMENT)
    assert report["summary"]["duplicate_rows"] == 1
    assert report["rows"][1]["validation_status"] == "REVIEW_REQUIRED"
    mismatch = validate_deposit_evidence("challan.csv", b"transaction_id,tds_amount,financial_year,tan\nPAY-1,100,2026-27,ZZZZ99999Z\n", ASSIGNMENT)
    assert mismatch["rows"][0]["validation_status"] == "REVIEW_REQUIRED"
    assert mismatch["rows"][0]["tds_amount"] == 100.0


def test_immutable_input_and_assignment_isolation_are_preserved():
    foreign = {**LIABILITY, "transaction_id": "PAY-2", "expected_tds": 999.0}
    snapshot, source = deepcopy(LIABILITY), deepcopy(EVIDENCE)
    result = control(liabilities=[snapshot], evidence=[source])
    snapshot["expected_tds"] = 999.0
    source["tds_amount"] = 999.0
    assert result["expected_tds"] == 100.0
    assert result["deposited_tds"] == 100.0
    assert foreign["transaction_id"] not in str(result)
