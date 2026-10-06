from copy import deepcopy

import pytest

from engine.tds_compliance.core import select_statutory_rule
from engine.tds_compliance.source_traceability import catalog_activation_errors, governed_activation_errors
from seeders.seed_tds_statutory_catalog import document, load_catalog, validate


def _rule(**changes):
    value = {
        "rule_id": "TEST-GOVERNED", "rule_version": "v1", "active": True,
        "lifecycle": "ACTIVE", "financial_year": "2026-27",
        "governing_act": "INCOME_TAX_ACT_2025", "effective_from": "2026-04-01",
        "effective_to": "2027-03-31", "payment_nature": "commission",
        "rate": "2", "threshold_type": "NO_THRESHOLD",
    }
    value.update(changes)
    return value


def _transaction():
    return {
        "financial_year": "2026-27", "payment_nature": "commission",
        "credit_date": "2026-06-01", "payment_date": "2026-06-01",
    }


def _law():
    return {"status": "LAW_DETERMINED", "act": "INCOME_TAX_ACT_2025", "effective_event_date": "2026-06-01"}


def _traceable_approved_rule(**changes):
    value = _rule(
        lifecycle="APPROVED", active=False, source="Test authority",
        source_url="https://www.indiacode.nic.in/",
        source_document_title="Test primary text",
        source_provision_reference="Test provision",
        source_retrieved_at="2026-10-05", source_verified_at="2026-10-05",
        source_verification_evidence="TEST-SOURCE-EVIDENCE",
        approved_at="2026-10-05T00:00:00Z", approved_by="test-ca",
        approval_metadata={"approval_authority": "test-ca", "approval_reference": "TEST-APPROVAL-1"},
    )
    value.update(changes)
    return value


def test_only_active_rule_is_selected_for_calculation():
    nature = {"payment_nature": "commission"}
    for lifecycle in ("DRAFT", "PENDING_APPROVAL", "APPROVED"):
        selected, reason = select_statutory_rule(_transaction(), [_rule(lifecycle=lifecycle, active=False)], _law(), nature)
        assert selected is None
        assert reason == "RULE_NOT_FOUND"
    selected, reason = select_statutory_rule(_transaction(), [_rule()], _law(), nature)
    assert selected["rule_id"] == "TEST-GOVERNED"
    assert reason == "SECTION_MISSING"


def test_manual_rule_activation_requires_verified_source_and_recorded_approval_evidence():
    assert governed_activation_errors(_traceable_approved_rule()) == []
    assert governed_activation_errors(_traceable_approved_rule(source_verified_at=None)) == ["source_verification_required"]
    assert governed_activation_errors(_traceable_approved_rule(approval_metadata=None)) == ["approval_evidence_required"]
    assert governed_activation_errors(_traceable_approved_rule(approved_at=None, approved_by=None)) == ["approval_evidence_required"]


def test_verified_global_catalog_rule_is_active_without_manual_approval_metadata():
    catalog, rules = load_catalog()
    catalog_rule = document(rules[0], catalog["catalog_version"])
    assert catalog_rule["lifecycle"] == "ACTIVE"
    assert catalog_rule["active"] is True
    assert catalog_rule["source_traceability"]["status"] == "VERIFIED"
    assert catalog_rule["source_traceability"]["ca_approval_status"] == "CA_APPROVAL_NOT_RECORDED"
    assert not catalog_rule.get("approved_at")
    assert not catalog_rule.get("approved_by")
    assert catalog_rule["created_by"] == "VERIFIED_STATUTORY_CATALOG_IMPORT"

    contractor = document(next(item for item in rules if item["rule_id"] == "STAT-393-6I-INDHUF-2026-V1"), catalog["catalog_version"])
    selected, reason = select_statutory_rule(
        {
            "financial_year": "2026-27", "payment_nature": "contractor",
            "deductee_type": "INDIVIDUAL_HUF", "credit_date": "2026-06-01",
            "payment_date": "2026-06-01", "section_input": "393(1) [Table: Sl. No. 6(i)]",
        },
        [contractor],
        _law(),
        {"payment_nature": "contractor"},
    )
    assert selected and selected["rule_id"] == "STAT-393-6I-INDHUF-2026-V1"
    assert reason == "SECTION_CONFIRMED"

    candidate = deepcopy(rules[0])
    candidate.update({"rule_id": "TEST-CATALOG-DRAFT", "rule_version": "test.v1", "status": "DRAFT"})
    imported = document(candidate, catalog["catalog_version"])
    assert imported["lifecycle"] == "DRAFT"
    assert imported["active"] is False


def test_global_catalog_cannot_be_active_without_complete_source_traceability():
    _, rules = load_catalog()
    missing_verification = deepcopy(rules[0])
    missing_verification["source_verified_at"] = None
    assert catalog_activation_errors(missing_verification) == ["source_verification_required"]
    assert "source_verification_required" in validate(missing_verification)
    with pytest.raises(ValueError, match="source_verification_required"):
        document(missing_verification, "test")

    missing_traceability = deepcopy(rules[0])
    missing_traceability["source_url"] = None
    missing_traceability["source_document_title"] = None
    missing_traceability["source_verification_evidence"] = None
    assert catalog_activation_errors(missing_traceability) == ["source_verification_required"]
    assert "source_verification_required" in validate(missing_traceability)
    with pytest.raises(ValueError, match="source_verification_required"):
        document(missing_traceability, "test")
