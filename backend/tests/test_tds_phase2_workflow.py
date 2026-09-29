from copy import deepcopy

from engine.tds_compliance.core import calculate_ledger_transactions


RULE_194C = {
    "rule_id": "STAT-194C-INDHUF-2025-V1", "rule_version": "2025-26.official.v1",
    "workflow": "TDS_COMPLIANCE", "scope": "GLOBAL", "active": True, "lifecycle": "ACTIVE",
    "financial_year": "2025-26", "governing_act": "INCOME_TAX_ACT_1961",
    "effective_from": "2025-04-01", "effective_to": "2026-03-31", "payment_nature": "contractor",
    "deductee_type": "INDIVIDUAL_HUF", "rate": "1", "threshold_type": "PER_TRANSACTION_AND_AGGREGATE",
    "per_transaction_threshold": "30000", "aggregate_financial_year_threshold": "100000",
    "calculation_basis": "full_amount", "rounding_method": "HALF_UP", "rounding_precision": 2, "priority": 0,
}


def row(**overrides):
    value = {"transaction_id": "PAY-1", "source_reference": "PAY-1", "financial_year": "2025-26", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "deductee_pan": "ABCDE1234F", "amount": "50000", "credit_date": "2025-06-01", "payment_date": "2025-06-01"}
    value.update(overrides)
    return value


def test_verified_194c_threshold_and_actual_tds_statuses():
    below = calculate_ledger_transactions([row(amount="20000", tds_deducted="0")], [RULE_194C])[0]
    matched = calculate_ledger_transactions([row(tds_deducted="500")], [RULE_194C])[0]
    short = calculate_ledger_transactions([row(tds_deducted="400")], [RULE_194C])[0]
    excess = calculate_ledger_transactions([row(tds_deducted="600")], [RULE_194C])[0]
    missing = calculate_ledger_transactions([row()], [RULE_194C])[0]
    assert below["expected_tds"] == 0.0
    assert matched["deduction_status"] == "COMPLIANT"
    assert short["deduction_status"] == "SHORT_DEDUCTION"
    assert excess["deduction_status"] == "EXCESS_DEDUCTION"
    assert missing["deduction_status"] == "ACTUAL_TDS_NOT_PROVIDED"


def test_missing_pan_rule_not_found_and_ambiguous_are_controlled():
    missing_pan = calculate_ledger_transactions([row(deductee_pan="")], [RULE_194C])[0]
    no_rule = calculate_ledger_transactions([row(payment_nature="professional_fee")], [RULE_194C])[0]
    ambiguous = calculate_ledger_transactions([row()], [RULE_194C, {**RULE_194C, "rule_id": "DUPLICATE"}])[0]
    assert missing_pan["calculation_status"] == "REVIEW_REQUIRED"
    assert missing_pan["expected_tds"] is None
    assert no_rule["reason_code"] == "RULE_NOT_FOUND"
    assert ambiguous["reason_code"] == "RULE_AMBIGUOUS"


def test_calculation_rule_snapshot_is_immutable_after_rule_change():
    rule = deepcopy(RULE_194C)
    result = calculate_ledger_transactions([row(tds_deducted="500")], [rule])[0]
    rule["rate"] = "99"
    assert result["rule_snapshot"]["rate"] == "1"
    assert result["expected_tds"] == 500.0


def test_aggregate_threshold_and_assignment_financial_year_isolation():
    rows = [row(transaction_id=f"PAY-{index}", source_reference=f"PAY-{index}", amount="25000", credit_date=f"2025-0{index}-01", payment_date=f"2025-0{index}-01", tds_deducted="0") for index in range(4, 9)]
    results = calculate_ledger_transactions(rows, [RULE_194C], assignment_id="ASSIGNMENT-A")
    assert [item["expected_tds"] for item in results[:-1]] == [0.0, 0.0, 0.0, 0.0]
    assert results[-1]["expected_tds"] == 250.0
    different_assignment = calculate_ledger_transactions([row(amount="25000", tds_deducted="0")], [RULE_194C], assignment_id="ASSIGNMENT-B")[0]
    wrong_year = calculate_ledger_transactions([row(financial_year="2026-27")], [RULE_194C])[0]
    assert different_assignment["expected_tds"] == 0.0
    assert wrong_year["reason_code"] == "RULE_NOT_FOUND"
