"""Regression: one unconfigured transaction cannot poison a mixed ledger."""
from copy import deepcopy

from engine.tds_compliance.core import CONTRACTOR_CONTROL_CONTRACT, calculate_ledger_transactions
from seeders.seed_tds_statutory_catalog import document, load_catalog


def _rules():
    catalog, rules = load_catalog()
    return [document(deepcopy(rule), catalog["catalog_version"]) for rule in rules]


def _fact(kind, value, source):
    return {
        "fact_type": kind, "value_code": value, "evidence_status": "VERIFIED",
        "evidence_reference": f"EVIDENCE-{source}-{value}", "source_condition_reference": source,
    }


def _row(transaction_id, nature, amount, **extra):
    row = {
        "transaction_id": transaction_id, "source_reference": transaction_id,
        "financial_year": "2026-27", "payment_nature": nature, "amount": amount,
        "deductee_pan": "ABCDE1234F", "credit_date": "2026-05-01",
        "payment_date": "2026-05-01", "tds_deducted": "0",
    }
    row.update(extra)
    return row


def test_mixed_payment_ledger_calculates_supported_rows_and_fails_closed_per_row():
    rows = [
        _row("CONTRACTOR", "contractor", "30001", deductee_type="INDIVIDUAL_HUF",
             contractor_control_contract=CONTRACTOR_CONTROL_CONTRACT,
             payer_eligibility_status="CONFIRMED_ELIGIBLE", payer_eligibility_evidence_reference="PAYER-1",
             contractor_residency_status="CONFIRMED_RESIDENT", contractor_residency_evidence_reference="RES-1",
             contractor_exception_status="NO_EXCEPTION_CONFIRMED", contractor_exception_evidence_reference="EX-1",
             contractor_invoice_material_status="NO_CUSTOMER_SUPPLIED_MATERIAL_CONFIRMED", contractor_invoice_material_evidence_reference="INV-1"),
        _row("COMMISSION", "commission_brokerage", "20001", recipient_residency="RESIDENT", recipient_category="PERSON",
             classification_facts=[_fact("PARTY_CAPACITY", "SPECIFIED_PERSON", "CA-FY2026-27-RES-05"), _fact("SERVICE_ACTIVITY_CHANNEL", "NOT_BSNL_MTNL_PCO_FRANCHISEE", "CA-FY2026-27-RES-05")]),
        _row("GOODS", "purchase_of_goods", "5000001", recipient_residency="RESIDENT", recipient_category="PERSON",
             classification_facts=[_fact("PARTY_CAPACITY", "PURCHASE_GOODS_ELIGIBLE_BUYER_PRECEDING_TY_TURNOVER_OVER_10_CRORE", "CA-FY2026-27-RES-32"), _fact("SERVICE_ACTIVITY_CHANNEL", "NO_OTHER_TDS_OR_TCS_APPLIES", "CA-FY2026-27-RES-32")]),
        _row("ECOMMERCE", "ecommerce", "1000", payer_category="ECOMMERCE_OPERATOR", recipient_residency="RESIDENT", recipient_category="PERSON",
             classification_facts=[_fact("PARTY_CAPACITY", "ECOMMERCE_PARTICIPANT_OTHER_THAN_INDIVIDUAL_HUF", "CA-FY2026-27-RES-36"), _fact("SERVICE_ACTIVITY_CHANNEL", "ECOMMERCE_PLATFORM_FACILITATED", "CA-FY2026-27-RES-36")]),
        _row("VDA", "virtual_digital_asset", "10001", recipient_residency="RESIDENT", recipient_category="PERSON",
             classification_facts=[_fact("ASSET_INSTRUMENT_SCHEME", "VIRTUAL_DIGITAL_ASSET_TRANSFER", "CA-FY2026-27-RES-38"), _fact("PARTY_CAPACITY", "VDA_OTHER_THAN_SPECIFIED_PERSON", "CA-FY2026-27-RES-38"), _fact("SERVICE_ACTIVITY_CHANNEL", "VDA_RELEASE_CONDITION_SATISFIED", "CA-FY2026-27-RES-38")]),
        _row("UNSUPPORTED", "rent_plant_machinery", "60000", recipient_residency="RESIDENT", recipient_category="PERSON"),
        _row("AMBIGUOUS", "commission_brokerage", "20001", recipient_residency="RESIDENT", recipient_category="PERSON", classification_facts=[]),
    ]

    result = {item["transaction_id"]: item for item in calculate_ledger_transactions(rows, _rules())}
    supported = ("CONTRACTOR", "COMMISSION", "GOODS", "ECOMMERCE", "VDA")
    assert all(result[key]["calculation_status"] == "CALCULATED" for key in supported)
    # Purchase-of-goods has only Re.1 above its aggregate threshold and rounds
    # to zero.  A configured rule/snapshot, rather than a zero amount alone,
    # proves this is not the unavailable-rule fallback.
    assert all(result[key]["rule_id"] and result[key]["expected_tds"] is not None for key in supported)
    assert result["GOODS"]["expected_tds"] == 0.0
    assert result["UNSUPPORTED"]["calculation_status"] in {"RULE_NOT_FOUND", "REVIEW_REQUIRED"}
    assert result["UNSUPPORTED"]["compliance_status"] == "REVIEW_REQUIRED"
    assert result["AMBIGUOUS"]["calculation_status"] == "REVIEW_REQUIRED"
    assert result["AMBIGUOUS"]["reason_code"] == "CLASSIFICATION_FACT_REQUIRED"
    assert all(result[key]["expected_tds"] is None for key in ("UNSUPPORTED", "AMBIGUOUS"))
