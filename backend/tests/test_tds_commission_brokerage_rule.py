from copy import deepcopy

from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, load_catalog


RULE_ID = "STAT-393-1II-COMMISSION-BROKERAGE-2026-V1"
SOURCE_ROW = "CA-FY2026-27-RES-05"


def _rule():
    _, rules = load_catalog()
    return document(deepcopy(next(item for item in rules if item["rule_id"] == RULE_ID)), "test")


def _facts(**changes):
    facts = [
        {"fact_type": "PARTY_CAPACITY", "value_code": "SPECIFIED_PERSON", "evidence_status": "VERIFIED", "evidence_reference": "PAYER-REG-1", "source_condition_reference": SOURCE_ROW},
        {"fact_type": "SERVICE_ACTIVITY_CHANNEL", "value_code": "NOT_BSNL_MTNL_PCO_FRANCHISEE", "evidence_status": "VERIFIED", "evidence_reference": "CHANNEL-REVIEW-1", "source_condition_reference": SOURCE_ROW},
    ]
    for fact in facts:
        fact.update(changes)
    return facts


def _row(**changes):
    row = {
        "transaction_id": "CB-1", "source_reference": "CB-1", "financial_year": "2026-27",
        "payment_nature": "commission_brokerage", "recipient_residency": "RESIDENT",
        "recipient_category": "PERSON", "amount": "20001", "deductee_pan": "ABCDE1234F",
        "credit_date": "2026-05-01", "payment_date": "2026-05-01", "tds_deducted": "400.02",
        "section_input": "393(1) [Table: Sl. No. 1(ii)]", "classification_facts": _facts(),
    }
    row.update(changes)
    return row


def test_commission_brokerage_catalog_rule_has_primary_source_traceability_and_active_scope():
    rule = _rule()
    assert rule["lifecycle"] == "ACTIVE" and rule["active"] is True
    assert rule["source_row_id"] == SOURCE_ROW
    assert rule["rate"] == "2"
    assert rule["aggregate_financial_year_threshold"] == "20000"
    assert rule["section_reference"] == "393(1) [Table: Sl. No. 1(ii)]"
    assert source_traceability_snapshot(rule)["status"] == "VERIFIED"


def test_commission_brokerage_applies_only_after_aggregate_exceeds_threshold_and_freezes_facts():
    rule = _rule()
    below, boundary, above = calculate_ledger_transactions([
        _row(transaction_id="CB-1", amount="10000", tds_deducted="0"),
        _row(transaction_id="CB-2", amount="10000", tds_deducted="0"),
        _row(transaction_id="CB-3", amount="1", tds_deducted="0.02"),
    ], [rule])
    assert below["threshold_status"] == "BELOW_CONFIGURED_THRESHOLDS" and below["expected_tds"] == 0.0
    assert boundary["threshold_status"] == "BELOW_CONFIGURED_THRESHOLDS" and boundary["expected_tds"] == 0.0
    assert above["threshold_status"] == "AGGREGATE_THRESHOLD_MET" and above["expected_tds"] == 0.02
    assert above["rule_id"] == RULE_ID and above["applicable_rate"] == 2.0
    frozen = freeze_calculation_results([above])[0]
    assert frozen["rule_snapshot"]["rule_id"] == RULE_ID
    assert frozen["classification_facts"] == _facts()


def test_commission_brokerage_fails_closed_for_missing_or_invalid_scope_evidence_and_inactive_rule():
    rule = _rule()
    cases = [
        _row(classification_facts=[]),
        _row(classification_facts=_facts(evidence_status="UNVERIFIED", evidence_reference=None)),
        _row(recipient_residency="NON_RESIDENT"),
        _row(recipient_category=None),
        _row(section_input="393(1) [Table: Sl. No. 2(ii)]"),
    ]
    for result in calculate_ledger_transactions(cases, [rule]):
        assert result["calculation_status"] == "REVIEW_REQUIRED" or result["calculation_status"] == "RULE_NOT_FOUND"
        assert result["expected_tds"] is None
    draft = deepcopy(rule)
    draft.update({"active": False, "lifecycle": "DRAFT"})
    inactive = calculate_ledger_transactions([_row()], [draft])[0]
    assert inactive["calculation_status"] == "RULE_NOT_FOUND"


def test_blocked_controlled_payment_nature_is_retained_for_review_not_rejected_as_an_invalid_ledger_payment():
    deferred = calculate_ledger_transactions([
        _row(payment_nature="non_resident_interest", recipient_residency="NON_RESIDENT", recipient_category="ANY_NON_RESIDENT")
    ], [_rule()])[0]
    assert deferred["payment_nature"] == "non_resident_interest"
    assert deferred["calculation_status"] == "RULE_NOT_FOUND"
    assert deferred["compliance_status"] == "REVIEW_REQUIRED"
