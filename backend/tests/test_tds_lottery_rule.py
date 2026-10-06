from copy import deepcopy

from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, load_catalog

RULE_ID = "STAT-393-3-LOTTERY-2026-V1"


def _catalog_rule():
    _, rules = load_catalog()
    return deepcopy(next(rule for rule in rules if rule["rule_id"] == RULE_ID))


def _active_runtime_rule():
    return document(_catalog_rule(), "test")


def _row(**changes):
    value = {
        "transaction_id": "LOT-1",
        "source_reference": "LOT-1",
        "financial_year": "2026-27",
        "payment_nature": "lottery",
        "recipient_residency": "RESIDENT",
        "recipient_category": "PERSON",
        "amount": "10000",
        "deductee_pan": "ABCDE1234F",
        "credit_date": "2026-05-01",
        "payment_date": "2026-05-01",
        "tds_deducted": "3000",
        "section_input": "393(3) [Table: Sl. No. 1]",
    }
    value.update(changes)
    return value


def test_lottery_catalog_row_preserves_verified_source_and_act_identities():
    rule = _catalog_rule()
    assert rule["status"] == "ACTIVE"
    assert rule["source_row_id"] == "CA-FY2026-27-RES-39"
    assert rule["source_status"] == "SOURCE_VERIFIED"
    assert rule["source_traceability_status"] == "VERIFIED"
    assert rule["source_document_filename"] == "Scan_20261005_121033.pdf"
    assert rule["source_document_sha256"] == "95DC35D476A29CE2F2D5559203460CBA288F1394CC233E1A48BAD9493A1C0F7F"
    assert rule["source_page_reference"] == "Printed page 30, Para R1.6"
    assert rule["source_verified_at"] == "2026-10-05"
    assert rule["governing_law"] == "INCOME_TAX_ACT_2025"
    assert rule["new_act_reference"] == "393(3)"
    assert rule["table_reference"] == "Table: Sl. No. 1"
    assert rule["old_act_reference"] == "194B"
    assert rule["rate_value"] == "30"
    assert rule["threshold"] == "10000"
    assert rule["threshold_mode"] == "PER_TRANSACTION"
    assert source_traceability_snapshot(_active_runtime_rule())["status"] == "VERIFIED"


def test_lottery_uses_the_explicit_per_transaction_threshold_and_freezes_rule_context():
    rule = _active_runtime_rule()
    below = calculate_ledger_transactions([_row(amount="9999", tds_deducted="0")], [rule])[0]
    boundary = calculate_ledger_transactions([_row(amount="10000")], [rule])[0]
    above = calculate_ledger_transactions([_row(amount="10001", tds_deducted="3000.30")], [rule])[0]

    assert below["calculation_status"] == "CALCULATED"
    assert below["threshold_status"] == "BELOW_THRESHOLD"
    assert below["expected_tds"] == 0.0
    assert boundary["calculation_status"] == "CALCULATED"
    assert boundary["expected_tds"] == 3000.0
    assert above["expected_tds"] == 3000.3
    assert boundary["rule_id"] == RULE_ID
    assert boundary["applicable_rate"] == 30.0

    frozen = freeze_calculation_results([boundary])[0]
    snapshot = frozen["rule_snapshot"]
    assert snapshot["rule_id"] == RULE_ID
    assert snapshot["rule_version"] == "2026-27.taxmann-source.v1"
    assert snapshot["governing_law"] == "INCOME_TAX_ACT_2025"
    assert snapshot["new_act_reference"] == "393(3)"
    assert snapshot["table_reference"] == "Table: Sl. No. 1"
    assert snapshot["old_act_reference"] == "194B"
    assert snapshot["threshold_mode"] == "PER_TRANSACTION"


def test_lottery_fails_closed_for_wrong_or_missing_classification_and_nonactive_rule():
    rule = _active_runtime_rule()
    wrong_nature = calculate_ledger_transactions([_row(payment_nature="horse_race")], [rule])[0]
    wrong_residency = calculate_ledger_transactions([_row(recipient_residency="NON_RESIDENT")], [rule])[0]
    wrong_category = calculate_ledger_transactions([_row(recipient_category="COMPANY")], [rule])[0]
    missing_category = calculate_ledger_transactions([_row(recipient_category=None)], [rule])[0]
    draft = deepcopy(rule)
    draft.update({"active": False, "lifecycle": "DRAFT", "status": "DRAFT"})
    inactive = calculate_ledger_transactions([_row()], [draft])[0]

    for result in (wrong_nature, wrong_residency, wrong_category, missing_category, inactive):
        assert result["calculation_status"] == "RULE_NOT_FOUND"
        assert result["reason_code"] == "RULE_NOT_FOUND"
