from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import server
from engine.tds_compliance.core import calculate_ledger_transactions, freeze_calculation_results
from server import TdsTransactionClassificationBody


class _Cursor(list):
    def sort(self, key, direction):
        return self


class _Collection:
    def __init__(self): self.rows = []
    def replace_one(self, query, value, upsert=False):
        for index, row in enumerate(self.rows):
            if all(row.get(key) == expected for key, expected in query.items()):
                self.rows[index] = deepcopy(value); return
        if upsert: self.rows.append(deepcopy(value))
    def find(self, query, projection=None):
        return _Cursor(deepcopy([row for row in self.rows if all(row.get(key) == expected for key, expected in query.items())]))


def fact(**changes):
    value = {
        "fact_type": "PARTY_CAPACITY", "value_code": "DOMESTIC_COMPANY",
        "evidence_status": "VERIFIED", "evidence_reference": "CORP-REG-1",
        "source_condition_reference": "CA-FY2026-27-RES-04",
    }
    value.update(changes)
    return value


def row(**changes):
    value = {
        "transaction_id": "F-1", "source_reference": "F-1", "financial_year": "2026-27",
        "credit_date": "2026-05-01", "payment_date": "2026-05-01", "amount": "10000",
        "deductee_pan": "ABCDE1234F", "payment_nature": "lottery",
        "recipient_residency": "RESIDENT", "recipient_category": "PERSON",
        "section_input": "393(3) [Table: Sl. No. 1]",
    }
    value.update(changes)
    return value


def rule(**changes):
    value = {
        "rule_id": "TEST-FACT", "rule_version": "v1", "active": True, "lifecycle": "ACTIVE",
        "financial_year": "2026-27", "governing_act": "INCOME_TAX_ACT_2025",
        "effective_from": "2026-04-01", "effective_to": "2027-03-31",
        "payment_nature": "lottery", "section_reference": "393(3) [Table: Sl. No. 1]",
        "recipient_residency": "RESIDENT", "recipient_category": "PERSON",
        "generalized_selection_required": True, "rate": "30", "threshold_type": "PER_TRANSACTION",
        "threshold": "10000", "calculation_basis": "full_amount", "rounding_method": "HALF_UP",
        "rounding_precision": 2, "required_classification_facts": [{
            "fact_type": "PARTY_CAPACITY", "value_code": "DOMESTIC_COMPANY",
            "source_condition_reference": "CA-FY2026-27-RES-04",
        }],
    }
    value.update(changes)
    return value


def test_classification_fact_contract_accepts_verified_evidence_and_rejects_uncontrolled_or_unresolved_forms():
    body = TdsTransactionClassificationBody(payment_nature="lottery", classification_facts=[fact()])
    assert body.classification_facts[0].evidence_status == "VERIFIED"
    for invalid in (
        fact(evidence_reference=None),
        fact(fact_type="PAYER_TYPE_194D"),
        fact(evidence_status="UNKNOWN", review_reason=None),
        fact(value_code="free text"),
        fact(source_condition_reference="194D"),
    ):
        with pytest.raises(ValidationError):
            TdsTransactionClassificationBody(payment_nature="lottery", classification_facts=[invalid])


def test_classification_facts_persist_for_the_selected_ledger_version_and_freeze_with_calculation(monkeypatch):
    classifications = _Collection()
    database = SimpleNamespace(
        tds_compliance_transaction_classifications=classifications,
        tds_compliance_classification_mappings=_Collection(),
    )
    assignment = {"assignment_id": "A", "organization_id": "O", "client_id": "C"}
    ledger = {"ledger_version_id": "L-CURRENT"}
    source = row(assignment_id="A", ledger_version_id="L-CURRENT")
    monkeypatch.setattr(server, "db", database)
    monkeypatch.setattr(server, "_tds_assignment_or_404", lambda *_: assignment)
    monkeypatch.setattr(server, "_ledger_for_calculation", lambda *_: (ledger, [source]))
    monkeypatch.setattr(server, "_mapping_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

    body = TdsTransactionClassificationBody(payment_nature="lottery", section_reference="393(3) [Table: Sl. No. 1]", recipient_residency="RESIDENT", recipient_category="PERSON", classification_facts=[fact()])
    expected_facts = body.model_dump()["classification_facts"]
    persisted = server.classify_tds_transaction("A", "F-1", body, SimpleNamespace(user_id="CA"))
    classified = server._apply_classification_mappings(assignment, [source], "L-CURRENT")[0]
    result = calculate_ledger_transactions([classified], [rule()])[0]
    frozen = freeze_calculation_results([result])[0]

    assert persisted["classification_facts"] == expected_facts
    assert classified["classification_facts"] == expected_facts
    assert result["calculation_status"] == "CALCULATED"
    assert frozen["classification_facts"] == expected_facts


def test_required_facts_fail_closed_when_missing_unknown_or_not_verified():
    missing = calculate_ledger_transactions([row()], [rule()])[0]
    unknown = calculate_ledger_transactions([row(classification_facts=[fact(evidence_status="UNKNOWN", evidence_reference=None, review_reason="Evidence not available")])], [rule()])[0]
    contradicted = calculate_ledger_transactions([row(classification_facts=[fact(evidence_status="CONTRADICTED", review_reason="Evidence conflicts")])], [rule()])[0]

    for result in (missing, unknown, contradicted):
        assert result["calculation_status"] == "REVIEW_REQUIRED"
        assert result["reason_code"] == "CLASSIFICATION_FACT_REQUIRED"
