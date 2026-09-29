from copy import deepcopy

from engine.tds_compliance.core import calculate_interest_from_deposit_results


PHASE2 = {"transaction_id": "PAY-1", "calculation_status": "CALCULATED", "expected_tds": 100.0, "tds_deducted": 100.0, "effective_event_date": "2025-06-01", "governing_act": "ACT", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL"}
DEPOSIT = {"transaction_id": "PAY-1", "calculation_status": "CALCULATED", "expected_tds": 100.0, "actual_tds_deducted": 100.0, "deposited_tds": 100.0, "deposit_difference": 0.0, "governing_law": "ACT", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL", "deductible_date": "2025-06-01", "actual_deduction_date": "2025-06-01", "evidence_version_id": "EV-1", "evidence_entries": [{"deposit_date": "2025-06-03", "tds_amount": 100.0}]}


def rule(kind, **overrides):
    base = {"rule_id": f"{kind}-1", "rule_version": "v1", "interest_type": kind, "active": True, "lifecycle": "ACTIVE", "governing_law": "ACT", "effective_from": "2025-04-01", "effective_to": "2026-03-31", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL", "priority": 1, "rate": "1", "interest_base": "expected_tds", "period_counting_method": "CALENDAR_MONTH_OR_PART", "rounding_method": "HALF_UP", "rounding_precision": 2}
    if kind == "DEPOSIT_DELAY_INTEREST":
        base.update({"deposit_due_date_mode": "FIXED_OFFSET_DAYS", "deposit_due_offset_days": 3})
    base.update(overrides)
    return base


RULES = [rule("DEDUCTION_DELAY_INTEREST"), rule("DEPOSIT_DELAY_INTEREST")]


def calculate(deposit=DEPOSIT, phase2=PHASE2, rules=RULES):
    return calculate_interest_from_deposit_results([phase2], [deposit], rules, assignment_id="A-1", calculation_id="CALC-1", deposit_run_id="DEP-1", ledger_version_id="LEDGER-1")[0]


def test_no_delay_uses_frozen_phase2_and_persisted_phase4_sources():
    result = calculate()
    assert result["expected_tds"] == 100.0
    assert result["deposited_tds"] == 100.0
    assert result["overall_status"] == "NO_INTEREST_INDICATED"
    assert result["deduction_interest"] == 0.0
    assert result["deposit_interest"] == 0.0


def test_late_deduction_and_late_deposit_are_separate_components():
    deposit = {**DEPOSIT, "actual_deduction_date": "2025-06-02", "evidence_entries": [{"deposit_date": "2025-06-08", "tds_amount": 100.0}]}
    result = calculate(deposit=deposit)
    assert result["deduction_status"] == "LATE_DEDUCTION"
    assert result["deposit_status"] == "LATE_DEPOSIT"
    assert result["deduction_interest"] == 1.0
    assert result["deposit_interest"] == 1.0
    assert result["overall_status"] == "INTEREST_DUE"


def test_missing_dates_and_invalid_date_order_are_controlled():
    missing = calculate(deposit={**DEPOSIT, "actual_deduction_date": None})
    invalid = calculate(deposit={**DEPOSIT, "actual_deduction_date": "2025-05-30"})
    no_deposit = calculate(deposit={**DEPOSIT, "evidence_entries": [{"tds_amount": 100.0}]})
    assert missing["overall_status"] == "DATE_NOT_DETERMINABLE"
    assert invalid["overall_status"] == "INVALID_DATE_ORDER"
    assert no_deposit["overall_status"] == "DATE_NOT_DETERMINABLE"


def test_missing_or_ambiguous_policy_never_guesses_interest():
    missing = calculate(rules=[])
    ambiguous = calculate(rules=[rule("DEDUCTION_DELAY_INTEREST"), rule("DEDUCTION_DELAY_INTEREST", rule_id="D2"), rule("DEPOSIT_DELAY_INTEREST")])
    assert missing["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert ambiguous["overall_status"] == "INTEREST_REVIEW_REQUIRED"


def test_historical_snapshots_remain_unchanged_after_source_changes():
    phase2, deposit = deepcopy(PHASE2), deepcopy(DEPOSIT)
    result = calculate(phase2=phase2, deposit=deposit)
    phase2["expected_tds"] = 999.0
    deposit["evidence_entries"][0]["deposit_date"] = "2025-12-31"
    assert result["expected_tds"] == 100.0
    assert result["actual_deposit_date"] == "2025-06-03"
    assert result["calculation_id"] == "CALC-1"
    assert result["deposit_run_id"] == "DEP-1"
