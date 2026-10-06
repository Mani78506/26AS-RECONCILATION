from copy import deepcopy

from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, load_catalog


def _draft():
    _, rules = load_catalog()
    return deepcopy(next(rule for rule in rules if rule["rule_id"] == "STAT-393-3-HORSE-RACE-2026-V1"))


def _row(**changes):
    value = {
        "transaction_id": "HR-1", "source_reference": "HR-1", "financial_year": "2026-27",
        "payment_nature": "horse_race", "recipient_residency": "RESIDENT",
        "recipient_category": "PERSON", "amount": "10000", "deductee_pan": "ABCDE1234F",
        "credit_date": "2026-05-01", "payment_date": "2026-05-01", "tds_deducted": "3000",
        "section_input": "393(3) [Table: Sl. No. 3]",
    }
    value.update(changes)
    return value


def _test_only_active_copy():
    rule = document(_draft(), "test")
    rule.update({
        "lifecycle": "ACTIVE", "active": True, "source_url": "https://example.test/source",
        "source_verified_at": "2026-10-05",
    })
    return rule


def test_horse_race_catalog_rule_is_source_verified_and_active():
    draft = _draft()
    assert draft["status"] == "ACTIVE"
    assert draft["source_status"] == "SOURCE_VERIFIED"
    assert draft["source_traceability_status"] == "VERIFIED"
    assert draft["provision_reference"] == "393(3) [Table: Sl. No. 3]"
    assert draft["old_act_reference"] == "194BB"
    assert draft["rate_value"] == "30"
    assert draft["threshold"] == "10000"
    assert draft["threshold_mode"] == "PER_TRANSACTION"
    stored = document(draft, "test")
    assert stored["lifecycle"] == "ACTIVE" and stored["active"] is True
    assert source_traceability_snapshot(stored)["status"] == "VERIFIED"


def test_draft_cannot_calculate_and_requires_governed_activation():
    result = calculate_ledger_transactions([_row()], [_draft()])[0]
    assert result["calculation_status"] == "RULE_NOT_FOUND"
    assert result["reason_code"] == "RULE_NOT_FOUND"


def test_test_only_active_copy_applies_explicit_horse_race_threshold_and_freezes_context():
    rule = _test_only_active_copy()
    below = calculate_ledger_transactions([_row(amount="9999", tds_deducted="0")], [rule])[0]
    boundary = calculate_ledger_transactions([_row(amount="10000")], [rule])[0]
    above = calculate_ledger_transactions([_row(amount="10001", tds_deducted="3000.30")], [rule])[0]
    assert below["calculation_status"] == "CALCULATED"
    assert below["threshold_status"] == "BELOW_THRESHOLD" and below["expected_tds"] == 0.0
    assert boundary["rule_id"] == "STAT-393-3-HORSE-RACE-2026-V1"
    assert boundary["expected_tds"] == 3000.0
    assert above["expected_tds"] == 3000.3
    frozen = freeze_calculation_results([boundary])[0]
    assert frozen["rule_snapshot"]["table_reference"] == "Table: Sl. No. 3"
    assert frozen["rule_snapshot"]["rule_version"] == "2026-27.taxmann-source.v1"


def test_test_only_active_copy_fails_closed_for_wrong_or_missing_party_context():
    rule = _test_only_active_copy()
    wrong_residency = calculate_ledger_transactions([_row(recipient_residency="NON_RESIDENT")], [rule])[0]
    missing_category = calculate_ledger_transactions([_row(recipient_category=None)], [rule])[0]
    wrong_nature = calculate_ledger_transactions([_row(payment_nature="lottery")], [rule])[0]
    assert wrong_residency["calculation_status"] == "RULE_NOT_FOUND"
    assert missing_category["calculation_status"] == "RULE_NOT_FOUND"
    assert wrong_nature["calculation_status"] == "RULE_NOT_FOUND"
