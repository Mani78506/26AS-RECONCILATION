"""Lifecycle and isolation tests for explicitly supplied Phase 5 interest policies."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from build_tds_e2e_fixtures import _configure_environment
from reset_tds_e2e import reset


@pytest.fixture(autouse=True)
def isolated_e2e_database():
    _configure_environment()
    reset()
    yield
    reset()


def server_module():
    _configure_environment()
    import server
    assert server.db.name == "26as_reconciliation_tds_e2e"
    return server


def explicit_policy(kind: str, **changes):
    rule = {
        "organization_id": "E2E_TDS_ORG",
        "scope": "ASSIGNMENT",
        "client_id": "E2E_DEMO_TDS_SERVICES",
        "assignment_id": "E2E_GOLDEN_PATH",
        "financial_year": "2026-27",
        "effective_from": "2026-04-01",
        "effective_to": "2027-03-31",
        "governing_act": "INCOME_TAX_ACT_2025",
        "payment_nature": "contractor",
        "deductee_type": "INDIVIDUAL_HUF",
        # These are explicitly synthetic test-shape values. They are never
        # copied into the shared catalogue or used as statutory fixture data.
        "rate": "1",
        "rate_unit": "PERCENT_PER_PERIOD",
        "interest_base": "expected_tds",
        "period_counting_method": "CALENDAR_MONTH_OR_PART",
        "rounding_method": "HALF_UP",
        "rounding_precision": 2,
        "priority": 3,
        "source": "E2E_EXPLICIT_TEST_POLICY",
        "source_reference": "E2E-CA-APPROVAL-REFERENCE",
        "source_url": "https://example.test/e2e-interest-policy",
        "source_document_title": "E2E isolated policy test fixture",
        "source_provision_reference": "E2E-TEST-PROVISION",
        "source_retrieved_at": "2026-10-06",
        "source_verified_at": "2026-10-06",
        "source_verification_evidence": "Isolated test fixture only; never statutory source evidence.",
        "approval_metadata": {
            "approved_source": "E2E isolated test configuration",
            "approval_authority": "E2E_TEST_AUTHORITY",
            "approval_reference": "E2E-TEST-APPROVAL",
        },
        "interest_type": kind,
    }
    if kind == "DEPOSIT_DELAY_INTEREST":
        rule.update({"deposit_due_date_mode": "FIXED_OFFSET_DAYS", "deposit_due_offset_days": 3})
    rule.update(changes)
    return rule


def lifecycle(server, payload):
    principal = server.build_development_principal()
    draft = server.create_tds_compliance_rule(server.TdsComplianceRuleBody(**payload), principal)
    pending = server.submit_tds_compliance_rule(draft["rule_id"], principal)
    approved = server.approve_tds_compliance_rule(draft["rule_id"], principal)
    active = server.activate_tds_compliance_rule(draft["rule_id"], principal)
    return draft, pending, approved, active


def test_interest_policy_create_submit_approve_activate_persists_audit_and_version():
    server = server_module()
    draft, pending, approved, active = lifecycle(server, explicit_policy("DEDUCTION_DELAY_INTEREST"))

    assert [draft["lifecycle"], pending["lifecycle"], approved["lifecycle"], active["lifecycle"]] == [
        "DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE",
    ]
    assert active["active"] is True
    assert active["rule_version"] == "v1"
    assert active["approved_at"] and active["approved_by"]
    assert active["activated_at"] and active["activated_by"]
    history = list(server.db.tds_compliance_rule_history.find({"rule_id": active["rule_id"]}, {"_id": 0}))
    audit = list(server.db.tds_compliance_audit_events.find({"entity_id": active["rule_id"]}, {"_id": 0}))
    assert {item["action"] for item in history} >= {"CREATED_DRAFT", "SUBMITTED_FOR_APPROVAL", "APPROVED", "ACTIVATED"}
    assert {item["action"] for item in audit} >= {"RULE_CREATED_DRAFT", "RULE_SUBMITTED_FOR_APPROVAL", "RULE_APPROVED", "RULE_ACTIVATED"}


@pytest.mark.parametrize("change, expected", [
    ({"effective_from": None, "effective_to": None}, "effective_from"),
    ({"interest_base": None}, "interest_base"),
    ({"period_counting_method": None}, "period_counting_method"),
    ({"rate": None}, "rate"),
])
def test_incomplete_interest_policy_draft_cannot_be_submitted(change, expected):
    server = server_module()
    principal = server.build_development_principal()
    payload = explicit_policy("DEDUCTION_DELAY_INTEREST", **change)
    draft = server.create_tds_compliance_rule(server.TdsComplianceRuleBody(**payload), principal)
    with pytest.raises(HTTPException) as error:
        server.submit_tds_compliance_rule(draft["rule_id"], principal)
    assert error.value.status_code == 422
    assert expected in str(error.value.detail)


def test_deposit_interest_policy_does_not_duplicate_phase4_due_date_configuration():
    server = server_module()
    policy = {
        key: value for key, value in explicit_policy("DEPOSIT_DELAY_INTEREST").items()
        if key not in {"deposit_due_date_mode", "deposit_due_offset_days", "deposit_due_day"}
    }

    _, _, _, active = lifecycle(server, policy)

    assert active["interest_type"] == "DEPOSIT_DELAY_INTEREST"
    assert active["active"] is True


def test_wrong_fy_is_rejected_and_wrong_payment_or_deductee_scope_is_not_selected():
    server = server_module()
    principal = server.build_development_principal()
    wrong_fy = server.create_tds_compliance_rule(
        server.TdsComplianceRuleBody(**explicit_policy("DEDUCTION_DELAY_INTEREST", financial_year="2025-26")),
        principal,
    )
    with pytest.raises(HTTPException) as error:
        server.submit_tds_compliance_rule(wrong_fy["rule_id"], principal)
    assert error.value.status_code == 422
    assert "financial_year_scope" in str(error.value.detail)

    from engine.tds_compliance.core import calculate_interest_from_deposit_results
    deduction = explicit_policy("DEDUCTION_DELAY_INTEREST", payment_nature="professional")
    deposit = explicit_policy("DEPOSIT_DELAY_INTEREST", deductee_type="COMPANY")
    phase2 = {"transaction_id": "TX", "calculation_status": "CALCULATED", "expected_tds": 100, "tds_deducted": 100, "effective_event_date": "2026-04-10", "governing_act": "INCOME_TAX_ACT_2025", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF"}
    phase5 = {"transaction_id": "TX", "calculation_status": "CALCULATED", "expected_tds": 100, "actual_tds_deducted": 100, "governing_law": "INCOME_TAX_ACT_2025", "payment_nature": "contractor", "deductee_type": "INDIVIDUAL_HUF", "deductible_date": "2026-04-10", "actual_deduction_date": "2026-04-10", "evidence_entries": [{"deposit_date": "2026-04-12", "tds_amount": 100}]}
    result = calculate_interest_from_deposit_results([phase2], [phase5], [deduction, deposit], assignment_id="A", calculation_id="C", deposit_run_id="D")[0]
    assert result["overall_status"] == "POLICY_NOT_CONFIGURED"
    assert result.get("total_interest") is None


def test_conflicting_active_interest_scope_is_rejected():
    server = server_module()
    lifecycle(server, explicit_policy("DEDUCTION_DELAY_INTEREST"))
    principal = server.build_development_principal()
    conflicting = server.create_tds_compliance_rule(
        server.TdsComplianceRuleBody(**explicit_policy("DEDUCTION_DELAY_INTEREST", source_reference="E2E-SECOND-APPROVAL")),
        principal,
    )
    server.submit_tds_compliance_rule(conflicting["rule_id"], principal)
    server.approve_tds_compliance_rule(conflicting["rule_id"], principal)
    with pytest.raises(HTTPException) as error:
        server.activate_tds_compliance_rule(conflicting["rule_id"], principal)
    assert error.value.status_code == 409
    assert "ambiguous active policy scope" in str(error.value.detail)


def test_e2e_provisioning_requires_explicit_values_and_uses_normal_lifecycle(tmp_path, monkeypatch):
    from provision_tds_phase5_interest_policy import provision_from_explicit_file

    monkeypatch.delenv("TDS_E2E_PHASE5_POLICY_FILE", raising=False)
    assert provision_from_explicit_file()["policy_state"] == "POLICY_VALUES_NOT_SUPPLIED"

    payload = {
        "policies": [
            explicit_policy("DEDUCTION_DELAY_INTEREST"),
            explicit_policy("DEPOSIT_DELAY_INTEREST"),
        ]
    }
    config = tmp_path / "explicit_ca_policy.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    result = provision_from_explicit_file(config)
    assert result["final_status"] == "E2E_PHASE5_POLICY_READY"
    assert {item["interest_type"] for item in result["created"]} == {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}
    assert all(item["lifecycle"] == "ACTIVE" and item["approved_at"] and item["activated_at"] for item in result["created"])


def test_example_template_is_rejected_before_any_e2e_policy_is_created():
    from provision_tds_phase5_interest_policy import provision_from_explicit_file

    template = Path(__file__).with_name("tds_phase5_interest_policy.example.json")
    with pytest.raises(RuntimeError, match="approval_metadata.approved_source"):
        provision_from_explicit_file(template)
    server = server_module()
    assert server.db.tds_compliance_rules.count_documents({"interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}}) == 0


def test_provisioner_rejects_missing_approval_metadata_and_conflicting_scope_before_write(tmp_path):
    from provision_tds_phase5_interest_policy import provision_from_explicit_file

    missing_metadata = tmp_path / "missing_metadata.json"
    missing_metadata.write_text(json.dumps({"policies": [
        {**explicit_policy("DEDUCTION_DELAY_INTEREST"), "approval_metadata": {}},
        explicit_policy("DEPOSIT_DELAY_INTEREST"),
    ]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="approval_metadata.approved_source"):
        provision_from_explicit_file(missing_metadata)

    missing_traceability = tmp_path / "missing_traceability.json"
    missing_traceability.write_text(json.dumps({"policies": [
        {key: value for key, value in explicit_policy("DEDUCTION_DELAY_INTEREST").items() if key != "source_verified_at"},
        explicit_policy("DEPOSIT_DELAY_INTEREST"),
    ]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="source traceability is incomplete"):
        provision_from_explicit_file(missing_traceability)

    server = server_module()
    lifecycle(server, explicit_policy("DEDUCTION_DELAY_INTEREST"))
    conflicting = tmp_path / "conflicting_scope.json"
    conflicting.write_text(json.dumps({"policies": [
        explicit_policy("DEDUCTION_DELAY_INTEREST", source_reference="E2E-CONFLICT"),
        explicit_policy("DEPOSIT_DELAY_INTEREST"),
    ]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="scope conflicts"):
        provision_from_explicit_file(conflicting)
    assert server.db.tds_compliance_rules.count_documents({"interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}}) == 1


def test_complete_e2e_runner_stops_cleanly_without_an_explicit_policy_file(monkeypatch):
    from run_tds_phase5_e2e import execute

    monkeypatch.delenv("TDS_E2E_PHASE5_POLICY_FILE", raising=False)
    result = execute()
    assert result["final_status"] == "E2E_PHASE5_POLICY_NOT_READY"
    assert result["policy_state"] == "POLICY_VALUES_NOT_SUPPLIED"
    server = server_module()
    assert server.db.tds_compliance_rules.count_documents({"interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}, "active": True}) == 0
