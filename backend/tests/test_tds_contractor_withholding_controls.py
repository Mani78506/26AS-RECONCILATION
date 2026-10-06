from copy import deepcopy
from types import SimpleNamespace

from engine.tds_compliance.core import (
    CONTRACTOR_CONTROL_CONTRACT,
    calculate_deposit_compliance,
    calculate_ledger_transactions,
    determine_configured_contractor_deposit_deadline,
    freeze_calculation_results,
)
from pydantic import ValidationError
import server
from server import TdsTransactionClassificationBody


class _Cursor(list):
    def sort(self, key, direction):
        return _Cursor(sorted(self, key=lambda item: str(item.get(key) or ""), reverse=direction < 0))


class _Collection:
    """Minimal isolated Mongo collection contract used by calculation routes."""

    def __init__(self):
        self.rows = []

    def insert_one(self, row):
        self.rows.append(deepcopy(row))

    def insert_many(self, rows):
        self.rows.extend(deepcopy(rows))

    def replace_one(self, query, row, upsert=False):
        for index, existing in enumerate(self.rows):
            if all(existing.get(key) == value for key, value in query.items()):
                self.rows[index] = deepcopy(row)
                return
        if upsert:
            self.rows.append(deepcopy(row))

    def find_one(self, query, projection=None, sort=None):
        rows = self.find(query, projection)
        for key, direction in sort or []:
            rows = rows.sort(key, direction)
        return rows[0] if rows else None

    def find(self, query, projection=None):
        return _Cursor(deepcopy([row for row in self.rows if all(row.get(key) == value for key, value in query.items())]))

RULE_194C = {
    "rule_id": "TEST-194C", "rule_version": "test-v1", "active": True, "lifecycle": "ACTIVE",
    "financial_year": "2025-26", "governing_act": "INCOME_TAX_ACT_1961",
    "effective_from": "2025-04-01", "effective_to": "2026-03-31", "payment_nature": "contractor",
    "deductee_type": "INDIVIDUAL_HUF", "rate": "1", "threshold_type": "PER_TRANSACTION_AND_AGGREGATE",
    "per_transaction_threshold": "30000", "aggregate_financial_year_threshold": "100000",
    "calculation_basis": "full_amount", "rounding_method": "HALF_UP", "rounding_precision": 2,
}


def row(**changes):
    value = {
        "transaction_id": "C-1", "source_reference": "C-1", "financial_year": "2025-26",
        "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "deductee_pan": "ABCDE1234F",
        "amount": "50000", "credit_date": "2025-06-01", "payment_date": "2025-06-01",
        "tds_deducted": "500", "contractor_control_contract": CONTRACTOR_CONTROL_CONTRACT,
        "payer_eligibility_status": "CONFIRMED_ELIGIBLE", "payer_eligibility_evidence_reference": "PAYER-EVIDENCE-1",
        "contractor_residency_status": "CONFIRMED_RESIDENT", "contractor_residency_evidence_reference": "RESIDENCY-EVIDENCE-1",
        "contractor_exception_status": "NO_EXCEPTION_CONFIRMED", "contractor_exception_evidence_reference": "EXCEPTION-REVIEW-1",
        "contractor_invoice_material_status": "NO_CUSTOMER_SUPPLIED_MATERIAL_CONFIRMED", "contractor_invoice_material_evidence_reference": "INVOICE-EVIDENCE-1",
    }
    value.update(changes)
    return value


def result(**changes):
    return calculate_ledger_transactions([row(**changes)], [RULE_194C])[0]


def test_eligible_contractor_evidence_calculates_and_freezes_control_evidence():
    item = result(pan_operational_status="VERIFIED", pan_evidence_reference="PAN-EVIDENCE-1")
    assert item["calculation_status"] == "CALCULATED"
    assert item["expected_tds"] == 500.0
    assert item["contractor_applicability"]["status"] == "APPLICABLE"
    assert item["pan_evidence"] == {"pan_syntax_status": "FORMAT_VALID_UNVERIFIED", "pan_operational_status": "VERIFIED", "pan_evidence_reference": "PAN-EVIDENCE-1", "status": "EVIDENCE_BACKED"}


def test_ineligible_and_unknown_payer_are_distinct_safe_outcomes():
    ineligible = result(payer_eligibility_status="CONFIRMED_INELIGIBLE")
    unknown = result(payer_eligibility_status="INSUFFICIENT_EVIDENCE")
    assert ineligible["calculation_status"] == "NOT_APPLICABLE"
    assert ineligible["reason_code"] == "PAYER_CONFIRMED_INELIGIBLE"
    assert unknown["calculation_status"] == "REVIEW_REQUIRED"
    assert unknown["reason_code"] == "PAYER_ELIGIBILITY_UNRESOLVED"


def test_supported_exceptions_require_type_and_evidence_and_do_not_calculate():
    goods = result(contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="GOODS_CARRIAGE", contractor_exception_evidence_reference="DECL-1")
    asserted_without_evidence = result(contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="GOODS_CARRIAGE", contractor_exception_evidence_reference="")
    assert goods["calculation_status"] == "REVIEW_REQUIRED"
    assert goods["reason_code"] == "GOODS_CARRIAGE_EVIDENCE_REQUIRED"
    assert asserted_without_evidence["calculation_status"] == "REVIEW_REQUIRED"
    assert asserted_without_evidence["reason_code"] == "CONTRACTOR_EXCEPTION_EVIDENCE_REQUIRED"


def test_pan_syntax_unknown_never_selects_a_higher_rate_automatically():
    unknown = result(pan_operational_status="UNKNOWN")
    valid = result(pan_operational_status="VERIFIED", pan_evidence_reference="PAN-1")
    assert unknown["calculation_status"] == "CALCULATED"
    assert unknown["applicable_rate"] == valid["applicable_rate"] == 1.0
    assert unknown["pan_evidence"]["status"] == "NOT_VERIFIED"


def policies(act):
    common = {"active": True, "lifecycle": "ACTIVE", "approved_configuration": True, "source_verified": True,
              "configuration_id": f"CFG-{act}", "governing_act": act, "financial_year": "2025-26" if act.endswith("1961") else "2026-27",
              "section_reference": "194C" if act.endswith("1961") else "393(1) [Table: Sl. No. 6(i)]", "payment_nature": "contractor", "effective_from": "2025-04-01", "effective_to": "2027-03-31"}
    return [
        {**common, "policy_id": "GOV-BOOK", "deductor_type": "GOVERNMENT_OFFICE", "challan_route": "WITHOUT_CHALLAN", "deadline_mode": "DEDUCTION_DATE"},
        {**common, "policy_id": "GOV-CHALLAN", "deductor_type": "GOVERNMENT_OFFICE", "challan_route": "WITH_CHALLAN", "deadline_mode": "MONTH_END_PLUS_DAYS", "days_after_month_end": 7},
        {**common, "policy_id": "OTHER", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "deadline_mode": "MONTH_END_PLUS_DAYS", "days_after_month_end": 7},
        {**common, "policy_id": "OTHER-MARCH", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "deadline_mode": "FIXED_MONTH_DAY", "deadline_month": 4, "deadline_day": 30,
         "effective_from": "2025-03-01", "effective_to": "2025-03-31"},
    ]


def test_deposit_deadline_uses_only_explicit_approved_policy_branches():
    policies_1961 = policies("INCOME_TAX_ACT_1961")
    gov_challan = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_1961", "financial_year": "2025-26", "section_reference": "194C", "deduction_date": "2025-06-10", "deductor_type": "GOVERNMENT_OFFICE", "challan_route": "WITH_CHALLAN"}, policies_1961)
    gov_book = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_1961", "financial_year": "2025-26", "section_reference": "194C", "deduction_date": "2025-06-10", "deductor_type": "GOVERNMENT_OFFICE", "challan_route": "WITHOUT_CHALLAN"}, policies_1961)
    other_march = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_1961", "financial_year": "2025-26", "section_reference": "194C", "deduction_date": "2025-03-10", "deductor_type": "OTHER_DEDUCTOR"}, policies_1961)
    other_month = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_1961", "financial_year": "2025-26", "section_reference": "194C", "deduction_date": "2025-06-10", "deductor_type": "OTHER_DEDUCTOR"}, policies_1961)
    assert gov_challan["deposit_due_date"] == "2025-07-07"
    assert gov_book["deposit_due_date"] == "2025-06-10"
    assert other_march["deposit_due_date"] == "2025-04-30"
    assert other_month["deposit_due_date"] == "2025-07-07"
    assert gov_challan["policy_snapshot"]["policy_id"] == "GOV-CHALLAN"


def test_rule_218_2026_branches_require_separate_governed_scopes():
    """Rule 218 dates are exercised only with isolated governed test policies."""
    common = {
        "policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE", "active": True,
        "lifecycle": "ACTIVE", "source_traceability_status": "VERIFIED",
        "approved_at": "2026-01-01T00:00:00Z", "approved_by": "E2E-TEST-CA",
        "governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27",
        "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF",
        "effective_from": "2026-04-01", "effective_to": "2027-03-31",
    }
    general = {**common, "rule_id": "TEST-218-GENERAL", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "deadline_mode": "MONTH_END_PLUS_DAYS", "days_after_month_end": 7}
    march = {**common, "rule_id": "TEST-218-MARCH", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "deadline_mode": "FIXED_MONTH_DAY", "deadline_month": 4, "deadline_day": 30, "effective_from": "2027-03-01"}
    special = {**common, "rule_id": "TEST-218-FORM141", "section_reference": "393(1) [Table: Sl. No. 6(ii)]", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "deadline_mode": "MONTH_END_PLUS_DAYS", "days_after_month_end": 30}

    general_result = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "section_reference": general["section_reference"], "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [general])
    march_result = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "section_reference": march["section_reference"], "deduction_date": "2027-03-10", "deductor_type": "OTHER_DEDUCTOR"}, [march])
    special_result = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "section_reference": special["section_reference"], "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [special])

    assert general_result["deposit_due_date"] == "2026-07-07"
    assert march_result["deposit_due_date"] == "2027-04-30"
    assert special_result["deposit_due_date"] == "2026-07-30"


def test_due_date_missing_inputs_unapproved_and_ambiguous_policies_require_review():
    configured = policies("INCOME_TAX_ACT_2025")
    missing_type = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10"}, configured)
    missing_challan = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10", "deductor_type": "GOVERNMENT_OFFICE"}, configured)
    unapproved = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [{**configured[2], "approved_configuration": False}])
    ambiguous = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [configured[2], {**configured[2], "policy_id": "DUP"}])
    assert missing_type["reason_code"] == "DEDUCTOR_TYPE_REQUIRED"
    assert missing_challan["reason_code"] == "CHALLAN_ROUTE_REQUIRED"
    assert unapproved["reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert ambiguous["reason_code"] == "DUE_DATE_POLICY_AMBIGUOUS"


def test_exception_evidence_is_specific_and_contradictions_fail_closed():
    goods_missing_details = result(contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="GOODS_CARRIAGE", contractor_exception_evidence_reference="DECL-1")
    goods_complete = result(contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="GOODS_CARRIAGE", contractor_exception_evidence_reference="DECL-1", goods_carriage_count=10, goods_carriage_business_evidence_reference="BUSINESS-1", goods_carriage_declaration_reference="DECL-1", goods_carriage_pan_reference="PAN-1", goods_carriage_particulars_reference="PART-1")
    personal_missing_attestation = result(contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="PERSONAL_PURPOSE_INDIVIDUAL_HUF", contractor_exception_evidence_reference="PERSONAL-1")
    contradictory = result(payer_eligibility_status="CONFIRMED_INELIGIBLE", contractor_exception_status="EXCEPTION_CONFIRMED", contractor_exception_type="GOODS_CARRIAGE", contractor_exception_evidence_reference="DECL-1")
    assert goods_missing_details["reason_code"] == "GOODS_CARRIAGE_EVIDENCE_REQUIRED"
    assert goods_complete["calculation_status"] == "NOT_APPLICABLE"
    assert personal_missing_attestation["reason_code"] == "PERSONAL_PURPOSE_EVIDENCE_REQUIRED"
    assert contradictory["reason_code"] == "CONTRACTOR_EVIDENCE_CONTRADICTORY"


def test_control_is_opt_in_and_snapshot_round_trip_preserves_evidence():
    legacy = calculate_ledger_transactions([row(
        contractor_control_contract=None,
        payer_eligibility_status=None,
        payer_eligibility_evidence_reference=None,
        contractor_residency_status=None,
        contractor_residency_evidence_reference=None,
        contractor_exception_status=None,
        contractor_exception_evidence_reference=None,
        contractor_invoice_material_status=None,
        contractor_invoice_material_evidence_reference=None,
    )], [RULE_194C])[0]
    controlled = result(pan_operational_status="VERIFIED", pan_evidence_reference="PAN-E1")
    frozen = freeze_calculation_results([controlled])[0]
    assert legacy["calculation_status"] == "CALCULATED"
    assert "contractor_applicability" not in legacy
    assert frozen["contractor_applicability"] == controlled["contractor_applicability"]
    assert frozen["pan_evidence"] == controlled["pan_evidence"]


def test_core_fails_closed_for_malformed_or_uncontracted_control_evidence():
    malformed = calculate_ledger_transactions([row(contractor_control_contract="OTHER")], [RULE_194C])[0]
    uncontracted = calculate_ledger_transactions([row(contractor_control_contract=None, payer_eligibility_status="CONFIRMED_ELIGIBLE")], [RULE_194C])[0]
    assert malformed["reason_code"] == "CONTRACTOR_CONTROL_CONTRACT_INVALID"
    assert uncontracted["reason_code"] == "CONTRACTOR_CONTROL_CONTRACT_INVALID"


def test_residency_and_invoice_material_conditions_are_explicit_for_both_acts():
    rule_2026 = {
        **RULE_194C,
        "rule_id": "TEST-393-6I",
        "rule_version": "test-2026-v1",
        "financial_year": "2026-27",
        "governing_act": "INCOME_TAX_ACT_2025",
        "effective_from": "2026-04-01",
        "effective_to": "2027-03-31",
        "section_reference": "393(1) [Table: Sl. No. 6(i)]",
    }
    cases = (
        (RULE_194C, row(), "CALCULATED", None),
        (rule_2026, row(financial_year="2026-27", credit_date="2026-04-10", payment_date="2026-04-10", section_input="393(1) [Table: Sl. No. 6(i)]"), "CALCULATED", None),
        (RULE_194C, row(contractor_residency_status="CONFIRMED_NON_RESIDENT"), "NOT_APPLICABLE", "CONTRACTOR_RECIPIENT_CONFIRMED_NON_RESIDENT"),
        (rule_2026, row(financial_year="2026-27", credit_date="2026-04-10", payment_date="2026-04-10", section_input="393(1) [Table: Sl. No. 6(i)]", contractor_residency_status="INSUFFICIENT_EVIDENCE"), "REVIEW_REQUIRED", "CONTRACTOR_RESIDENCY_UNRESOLVED"),
        (RULE_194C, row(contractor_invoice_material_status="CUSTOMER_SUPPLIED_MATERIAL_SEPARATELY_STATED"), "REVIEW_REQUIRED", "CONTRACTOR_MATERIAL_BASE_REVIEW_REQUIRED"),
        (rule_2026, row(financial_year="2026-27", credit_date="2026-04-10", payment_date="2026-04-10", section_input="393(1) [Table: Sl. No. 6(i)]", contractor_invoice_material_status="INSUFFICIENT_EVIDENCE"), "REVIEW_REQUIRED", "CONTRACTOR_INVOICE_MATERIAL_UNRESOLVED"),
    )
    for rule, transaction, status, reason in cases:
        item = calculate_ledger_transactions([transaction], [rule])[0]
        assert item["calculation_status"] == status
        if reason:
            assert item["reason_code"] == reason


def test_table_6_i_scope_metadata_is_frozen_and_raw_payer_labels_do_not_bypass_review():
    rule_2026 = {
        **RULE_194C,
        "rule_id": "TEST-393-6I-SCOPE",
        "financial_year": "2026-27",
        "governing_act": "INCOME_TAX_ACT_2025",
        "effective_from": "2026-04-01",
        "effective_to": "2027-03-31",
        "section_reference": "393(1) [Table: Sl. No. 6(i)]",
        "payer_category": "DESIGNATED_PERSON",
        "recipient_category": "INDIVIDUAL_HUF_CONTRACTOR",
        "form_141_applicability": "NOT_APPLICABLE_TABLE_6_I",
    }
    scope = {
        "financial_year": "2026-27", "credit_date": "2026-04-10",
        "payment_date": "2026-04-10", "section_input": "393(1) [Table: Sl. No. 6(i)]",
    }

    eligible = calculate_ledger_transactions([row(**scope)], [rule_2026])[0]
    raw_label_only = calculate_ledger_transactions([
        row(**scope, payer_type="INDIVIDUAL_HUF", payer_eligibility_status="INSUFFICIENT_EVIDENCE")
    ], [rule_2026])[0]

    assert eligible["calculation_status"] == "CALCULATED"
    assert eligible["rule_snapshot"]["payer_category"] == "DESIGNATED_PERSON"
    assert eligible["rule_snapshot"]["recipient_category"] == "INDIVIDUAL_HUF_CONTRACTOR"
    assert eligible["rule_snapshot"]["form_141_applicability"] == "NOT_APPLICABLE_TABLE_6_I"
    assert raw_label_only["calculation_status"] == "REVIEW_REQUIRED"
    assert raw_label_only["reason_code"] == "PAYER_ELIGIBILITY_UNRESOLVED"


def test_personal_and_goods_carriage_exceptions_require_every_evidenced_condition():
    personal_missing_payer_type = result(
        contractor_exception_status="EXCEPTION_CONFIRMED",
        contractor_exception_type="PERSONAL_PURPOSE_INDIVIDUAL_HUF",
        contractor_exception_evidence_reference="PERSONAL-1",
        contractor_personal_purpose_attestation=True,
    )
    personal_complete = result(
        contractor_exception_status="EXCEPTION_CONFIRMED",
        contractor_exception_type="PERSONAL_PURPOSE_INDIVIDUAL_HUF",
        contractor_exception_evidence_reference="PERSONAL-1",
        contractor_personal_purpose_attestation=True,
        contractor_personal_purpose_payer_type="INDIVIDUAL_HUF",
        contractor_personal_purpose_payer_type_evidence_reference="PAYER-TYPE-1",
    )
    goods_missing_business = result(
        contractor_exception_status="EXCEPTION_CONFIRMED",
        contractor_exception_type="GOODS_CARRIAGE",
        contractor_exception_evidence_reference="GOODS-1",
        goods_carriage_count=10,
        goods_carriage_declaration_reference="DECL-1",
        goods_carriage_pan_reference="PAN-1",
        goods_carriage_particulars_reference="PART-1",
    )
    assert personal_missing_payer_type["reason_code"] == "PERSONAL_PURPOSE_EVIDENCE_REQUIRED"
    assert personal_complete["calculation_status"] == "NOT_APPLICABLE"
    assert goods_missing_business["reason_code"] == "GOODS_CARRIAGE_EVIDENCE_REQUIRED"


def test_both_verified_contractor_rules_enforce_event_dates_and_threshold_boundaries():
    rule_2026 = {
        **RULE_194C,
        "rule_id": "TEST-393-THRESHOLD",
        "financial_year": "2026-27",
        "governing_act": "INCOME_TAX_ACT_2025",
        "effective_from": "2026-04-01",
        "effective_to": "2027-03-31",
        "section_reference": "393(1) [Table: Sl. No. 6(i)]",
    }
    scoped = (
        (RULE_194C, {"financial_year": "2025-26", "credit_date": "2025-06-01", "payment_date": "2025-06-01", "section_input": "194C"}),
        (rule_2026, {"financial_year": "2026-27", "credit_date": "2026-04-01", "payment_date": "2026-04-01", "section_input": "393(1) [Table: Sl. No. 6(i)]"}),
    )
    for rule, scope in scoped:
        at_limit = calculate_ledger_transactions([row(**scope, amount="30000", tds_deducted="0")], [rule])[0]
        above_limit = calculate_ledger_transactions([row(**scope, amount="30001", tds_deducted="300.01")], [rule])[0]
        aggregate = calculate_ledger_transactions([
            row(**{
                **scope,
                "transaction_id": f"A-{index}",
                "source_reference": f"A-{index}",
                "amount": "25000",
                "tds_deducted": "0",
                "credit_date": f"{scope['financial_year'][:4]}-0{index}-01",
                "payment_date": f"{scope['financial_year'][:4]}-0{index}-01",
            })
            for index in range(4, 9)
        ], [rule], assignment_id=f"THRESHOLD-{rule['rule_id']}")
        assert at_limit["expected_tds"] == 0.0
        assert above_limit["expected_tds"] == 300.01
        assert [item["expected_tds"] for item in aggregate[:-1]] == [0.0, 0.0, 0.0, 0.0]
        assert aggregate[-1]["expected_tds"] == 250.0

    pre_commencement = calculate_ledger_transactions([
        row(financial_year="2026-27", credit_date="2026-03-31", payment_date="2026-04-02", section_input="393(1) [Table: Sl. No. 6(i)]")
    ], [rule_2026])[0]
    assert pre_commencement["reason_code"] == "RULE_NOT_FOUND"


def test_classification_persistence_uses_only_the_selected_ledger_version(monkeypatch):
    assignment = {"assignment_id": "A", "organization_id": "O", "client_id": "C", "status": "DRAFT"}
    version = {"ledger_version_id": "L-CURRENT"}
    source = {**row(), "assignment_id": "A", "ledger_version_id": "L-CURRENT"}
    stale = {"assignment_id": "A", "ledger_version_id": "L-STALE", "transaction_id": "C-1", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "contractor_control_contract": CONTRACTOR_CONTROL_CONTRACT, "payer_eligibility_status": "CONFIRMED_INELIGIBLE", "payer_eligibility_evidence_reference": "STALE-PAYER", "contractor_exception_status": "NO_EXCEPTION_CONFIRMED", "contractor_exception_evidence_reference": "STALE-EXCEPTION"}
    db = SimpleNamespace(
        tds_compliance_transaction_classifications=_Collection(),
        tds_compliance_classification_mappings=_Collection(),
        tds_compliance_rules=_Collection(),
        tds_compliance_calculation_runs=_Collection(),
        tds_compliance_calculation_results=_Collection(),
    )
    db.tds_compliance_transaction_classifications.insert_one(stale)
    db.tds_compliance_rules.insert_one({"workflow": "TDS_COMPLIANCE", **RULE_194C})
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_tds_assignment_or_404", lambda *_: assignment)
    monkeypatch.setattr(server, "_ledger_for_calculation", lambda *_: (version, [source]))
    monkeypatch.setattr(server, "_mapping_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_tds_compliance_security_boundary", lambda: None)
    monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

    server.classify_tds_transaction("A", "C-1", TdsTransactionClassificationBody(payment_nature="contractor", section_reference="194C", deductee_type="INDIVIDUAL_HUF", contractor_control_contract=CONTRACTOR_CONTROL_CONTRACT, payer_eligibility_status="CONFIRMED_ELIGIBLE", payer_eligibility_evidence_reference="CURRENT-PAYER", contractor_residency_status="CONFIRMED_RESIDENT", contractor_residency_evidence_reference="CURRENT-RESIDENCY", contractor_exception_status="NO_EXCEPTION_CONFIRMED", contractor_exception_evidence_reference="CURRENT-EXCEPTION", contractor_invoice_material_status="NO_CUSTOMER_SUPPLIED_MATERIAL_CONFIRMED", contractor_invoice_material_evidence_reference="CURRENT-INVOICE"), SimpleNamespace(user_id="CA"))
    created = server.run_tds_calculation("A", server.CalculationPreviewBody(ledger_version_id="L-CURRENT"))
    restored = server.get_tds_calculation("A", created["calculation"]["calculation_id"])["items"][0]

    assert len(db.tds_compliance_transaction_classifications.rows) == 2
    assert restored["calculation_status"] == "CALCULATED"
    assert restored["contractor_applicability"]["evidence"]["payer_eligibility_evidence_reference"] == "CURRENT-PAYER"


def test_api_rejects_malformed_or_uncontracted_control_evidence():
    for payload in (
        {"payment_nature": "contractor", "contractor_control_contract": "OTHER"},
        {"payment_nature": "contractor", "payer_eligibility_status": "CONFIRMED_ELIGIBLE"},
        {"payment_nature": "contractor", "contractor_control_contract": CONTRACTOR_CONTROL_CONTRACT, "goods_carriage_count": "10"},
    ):
        try:
            TdsTransactionClassificationBody(**payload)
        except ValidationError:
            pass
        else:
            raise AssertionError("malformed contractor-control request was accepted")


def test_due_policy_scope_identity_and_phase4_guard_cannot_be_bypassed():
    policy = policies("INCOME_TAX_ACT_2025")[2]
    wrong_fy = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2025-26", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [policy])
    missing_identity = determine_configured_contractor_deposit_deadline({"governing_act": "INCOME_TAX_ACT_2025", "financial_year": "2026-27", "section_reference": "393(1) [Table: Sl. No. 6(i)]", "deduction_date": "2026-06-10", "deductor_type": "OTHER_DEDUCTOR"}, [{key: value for key, value in policy.items() if key != "configuration_id"}])
    liability = {"transaction_id": "C-1", "calculation_status": "REVIEW_REQUIRED", "expected_tds": None, "governing_act": "INCOME_TAX_ACT_2025", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "contractor_applicability": {"status": "REVIEW_REQUIRED", "reason_code": "PAYER_ELIGIBILITY_UNRESOLVED"}}
    phase4 = calculate_deposit_compliance([liability], [], assignment_id="A", calculation_id="C", policies=[])[0]
    assert wrong_fy["reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert missing_identity["reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert phase4["reason_code"] == "CONTRACTOR_APPLICABILITY_NOT_ESTABLISHED"
    assert phase4["contractor_applicability"]["reason_code"] == "PAYER_ELIGIBILITY_UNRESOLVED"


def test_calculation_persistence_round_trip_preserves_controlled_review_evidence(monkeypatch):
    """Exercise the real calculation write/read route against an isolated store.

    This proves the Mongo snapshot/API restoration boundary does not promote a
    controlled review outcome or an unknown operational PAN into an eligible
    or verified state.  It never connects to MongoDB.
    """
    persisted = SimpleNamespace(
        tds_compliance_calculation_runs=_Collection(),
        tds_compliance_calculation_results=_Collection(),
    )
    assignment = {"assignment_id": "A", "organization_id": "O", "client_id": "C", "status": "DRAFT"}
    version = {"ledger_version_id": "L1"}
    original = calculate_ledger_transactions(
        [row(
            payer_eligibility_status="INSUFFICIENT_EVIDENCE",
            pan_operational_status="UNKNOWN",
            pan_evidence_reference=None,
        )],
        [RULE_194C],
        assignment_id="A",
    )[0]
    assert original["calculation_status"] == "REVIEW_REQUIRED"

    monkeypatch.setattr(server, "db", persisted)
    monkeypatch.setattr(server, "_tds_compliance_security_boundary", lambda: None)
    monkeypatch.setattr(server, "_calculation_preview", lambda *_: (assignment, version, [original]))
    monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

    created = server.run_tds_calculation("A", server.CalculationPreviewBody(ledger_version_id="L1"))
    original["contractor_applicability"]["status"] = "APPLICABLE"
    original["pan_evidence"]["pan_operational_status"] = "VERIFIED"
    restored = server.get_tds_calculation("A", created["calculation"]["calculation_id"])["items"][0]

    assert restored["calculation_status"] == "REVIEW_REQUIRED"
    assert restored["reason_code"] == "PAYER_ELIGIBILITY_UNRESOLVED"
    assert restored["contractor_applicability"]["status"] == "REVIEW_REQUIRED"
    assert restored["pan_evidence"]["pan_syntax_status"] == "FORMAT_VALID_UNVERIFIED"
    assert restored["pan_evidence"]["pan_operational_status"] == "UNKNOWN"
    assert restored["pan_evidence"]["status"] == "NOT_VERIFIED"
    assert restored["governing_act"] == "INCOME_TAX_ACT_1961"
    assert restored["rule_id"] == "TEST-194C"
    assert restored["rule_version"] == "test-v1"
    assert restored["rule_snapshot"]["rule_id"] == "TEST-194C"
    assert restored["rule_snapshot"]["financial_year"] == "2025-26"
    assert restored["rule_snapshot"]["governing_act"] == "INCOME_TAX_ACT_1961"
    assert restored["section_reference"] == restored["rule_snapshot"].get("section_reference")
