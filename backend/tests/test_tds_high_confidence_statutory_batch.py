from copy import deepcopy

import pytest

from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, load_catalog

CASES = (
    ("STAT-393-1-8IV-BENEFIT-PERQUISITE-2026-V1", "CA-FY2026-27-RES-34", "benefit_perquisite", "10", "20000", "PARTY_CAPACITY", "SPECIFIED_PERSON", {"recipient_residency": "RESIDENT", "recipient_category": "PERSON"}),
    ("STAT-393-3-4-LOTTERY-COMMISSION-2026-V1", "CA-FY2026-27-RES-42", "lottery_commission", "2", "20000", "SERVICE_ACTIVITY_CHANNEL", "LOTTERY_TICKET_INTERMEDIARY", {}),
    ("STAT-393-3-6-NATIONAL-SAVINGS-SCHEME-2026-V1", "CA-FY2026-27-RES-45", "national_savings_scheme", "10", "2500", "ASSET_INSTRUMENT_SCHEME", "SECTION_80CCA_2_A_AMOUNT", {}),
    ("STAT-393-3-7-PARTNER-REMUNERATION-INTEREST-2026-V1", "CA-FY2026-27-RES-46", "partner_remuneration_interest", "10", "20000", "PARTY_CAPACITY", "PARTNER_OF_PAYER_FIRM", {"recipient_category": "PARTNER", "payer_category": "FIRM"}),
)


def _rule(rule_id):
    _, rules = load_catalog()
    return document(deepcopy(next(item for item in rules if item["rule_id"] == rule_id)), "test")


def _fact(source_row, fact_type, value_code):
    return [{"fact_type": fact_type, "value_code": value_code, "evidence_status": "VERIFIED", "evidence_reference": f"EVIDENCE-{source_row}", "source_condition_reference": source_row}]


def _row(payment_nature, facts, **scope):
    row = {
        "transaction_id": "HIGH-CONFIDENCE-1", "source_reference": "HIGH-CONFIDENCE-1", "financial_year": "2026-27",
        "payment_nature": payment_nature, "amount": "1", "deductee_pan": "ABCDE1234F",
        "credit_date": "2026-05-01", "payment_date": "2026-05-01", "tds_deducted": "0",
        "classification_facts": facts,
    }
    row.update(scope)
    return row


@pytest.mark.parametrize("rule_id,source_row,payment_nature,rate,threshold,fact_type,value_code,scope", CASES)
def test_high_confidence_catalog_rules_are_traceable_active_and_thresholded(rule_id, source_row, payment_nature, rate, threshold, fact_type, value_code, scope):
    rule = _rule(rule_id)
    assert rule["lifecycle"] == "ACTIVE" and rule["active"] is True
    assert rule["source_row_id"] == source_row and rule["rate"] == rate
    assert rule["aggregate_financial_year_threshold"] == threshold
    assert source_traceability_snapshot(rule)["status"] == "VERIFIED"

    below = calculate_ledger_transactions([_row(payment_nature, _fact(source_row, fact_type, value_code), amount=str(int(threshold) - 1), **scope)], [rule])[0]
    at = calculate_ledger_transactions([_row(payment_nature, _fact(source_row, fact_type, value_code), amount=threshold, **scope)], [rule])[0]
    above = calculate_ledger_transactions([_row(payment_nature, _fact(source_row, fact_type, value_code), amount=str(int(threshold) + 1), **scope)], [rule])[0]
    assert below["expected_tds"] == 0.0 and at["expected_tds"] == 0.0
    assert above["rule_id"] == rule_id and above["applicable_rate"] == float(rate)
    assert above["expected_tds"] == pytest.approx((int(threshold) + 1) * float(rate) / 100)
    frozen = freeze_calculation_results([above])[0]
    assert frozen["rule_snapshot"]["rule_id"] == rule_id
    assert frozen["classification_facts"] == _fact(source_row, fact_type, value_code)


@pytest.mark.parametrize("rule_id,source_row,payment_nature,rate,threshold,fact_type,value_code,scope", CASES)
def test_high_confidence_catalog_rules_fail_closed_for_missing_evidence_wrong_act_or_inactive_rule(rule_id, source_row, payment_nature, rate, threshold, fact_type, value_code, scope):
    rule = _rule(rule_id)
    missing = calculate_ledger_transactions([_row(payment_nature, [], amount=str(int(threshold) + 1), **scope)], [rule])[0]
    assert missing["calculation_status"] == "REVIEW_REQUIRED"
    assert missing["reason_code"] == "CLASSIFICATION_FACT_REQUIRED"

    wrong_act = calculate_ledger_transactions([_row(payment_nature, _fact(source_row, fact_type, value_code), amount=str(int(threshold) + 1), credit_date="2026-03-31", payment_date="2026-03-31", **scope)], [rule])[0]
    assert wrong_act["calculation_status"] == "RULE_NOT_FOUND"

    inactive = deepcopy(rule)
    inactive.update({"active": False, "lifecycle": "DRAFT"})
    blocked = calculate_ledger_transactions([_row(payment_nature, _fact(source_row, fact_type, value_code), amount=str(int(threshold) + 1), **scope)], [inactive])[0]
    assert blocked["calculation_status"] == "RULE_NOT_FOUND"


def test_partner_rule_requires_firm_partner_scope_as_well_as_evidence():
    rule = _rule("STAT-393-3-7-PARTNER-REMUNERATION-INTEREST-2026-V1")
    facts = _fact("CA-FY2026-27-RES-46", "PARTY_CAPACITY", "PARTNER_OF_PAYER_FIRM")
    result = calculate_ledger_transactions([_row("partner_remuneration_interest", facts, amount="20001", recipient_category="PERSON", payer_category="FIRM")], [rule])[0]
    assert result["calculation_status"] == "RULE_NOT_FOUND"