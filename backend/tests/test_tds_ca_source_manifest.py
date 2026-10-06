import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import server
from engine.tds_compliance.core import calculate_ledger_transactions
from seeders.seed_tds_statutory_catalog import load_catalog, source_manifest_errors


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "tds_statutory_rules" / "fy2026_27_ca_source_manifest.json"


def _commission_rule():
    return {
        "rule_id": "TEST-393-1II-RESIDENT", "rule_version": "test-v1",
        "active": True, "lifecycle": "ACTIVE", "governing_act": "INCOME_TAX_ACT_2025",
        "financial_year": "2026-27", "effective_from": "2026-04-01", "effective_to": "2027-03-31",
        "payment_nature": "commission_brokerage", "section_reference": "393(1) [Table: Sl. No. 1(ii)]",
        "recipient_residency": "RESIDENT", "recipient_category": "PERSON", "payer_category": "PAYER",
        "generalized_selection_required": True, "rate": "2", "threshold_type": "NO_THRESHOLD",
        "calculation_basis": "full_amount", "rounding_method": "HALF_UP", "rounding_precision": 2,
    }


def _transaction(**overrides):
    return {
        "transaction_id": "TX-1", "source_reference": "TX-1", "financial_year": "2026-27",
        "credit_date": "2026-05-01", "payment_date": "2026-05-01", "amount": "100000",
        "deductee_pan": "ABCDE1234F",
        "payment_nature": "commission_brokerage", "recipient_residency": "RESIDENT",
        "recipient_category": "PERSON", "payer_category": "PAYER", **overrides,
    }


def test_ca_source_manifest_has_all_transcribed_rows_and_required_dimensions():
    catalog, _ = load_catalog()
    assert source_manifest_errors(catalog) == []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["row_count"] == 92
    assert manifest["resident_row_count"] == 46
    assert manifest["non_resident_or_foreign_company_row_count"] == 46
    assert len(manifest["rows"]) == 92
    assert sum(row["source_status"] == "SOURCE_VERIFIED" for row in manifest["rows"]) == 13
    assert sum(row["source_status"] == "TRANSCRIBED_UNVERIFIED" for row in manifest["rows"]) == 79
    assert any(row["nature_of_payment"] == "CONTRACTOR" and row["rate"] == "1" for row in manifest["rows"])
    assert any(row["recipient_residency"] == "FOREIGN_COMPANY" for row in manifest["rows"])


def test_category_b_count_includes_the_distinct_section_197_row_and_no_placeholder():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    category_b = [row for row in manifest["rows"] if row["source_id"].startswith("CA-FY2026-27-NR-")]

    assert len(category_b) == 46
    assert not any(row["nature_of_payment"] == "SOURCE_ROW_UNRESOLVED" for row in category_b)
    assert any(
        row["source_page"] == 32
        and row["2025_act_section"] == "393(2)"
        and row["2025_act_table_sl_no"] == "17"
        and row["1961_act_section"] == "195(1)"
        and row["nature_of_payment"] == "NON_RESIDENT_CAPITAL_GAIN"
        and row["rate"] == "12.5"
        and row["threshold"] is None
        and row["conditions"] == "Section 197 long-term gain; SC cap 15%"
        for row in category_b
    )


def test_generic_selection_requires_controlled_residency_and_never_uses_resident_rule_for_nonresident():
    rule = _commission_rule()
    resident = calculate_ledger_transactions([_transaction()], [rule])[0]
    non_resident = calculate_ledger_transactions([_transaction(recipient_residency="NON_RESIDENT")], [rule])[0]
    unknown = calculate_ledger_transactions([_transaction(recipient_residency=None)], [rule])[0]

    assert resident["calculation_status"] == "CALCULATED"
    assert resident["rule_id"] == rule["rule_id"]
    assert non_resident["calculation_status"] == "RULE_NOT_FOUND"
    assert unknown["calculation_status"] == "REVIEW_REQUIRED"
    assert unknown["reason_code"] == "RECIPIENT_RESIDENCY_REQUIRED"


def test_generic_calculator_dimensions_reject_free_form_categories():
    with pytest.raises(ValidationError):
        server.TdsCalculatorBody(financial_year="2026-27", payment_nature="commission_brokerage", amount="100", recipient_residency="RESIDENT", recipient_category="uncontrolled")
