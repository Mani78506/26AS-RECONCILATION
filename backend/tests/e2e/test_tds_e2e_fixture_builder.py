from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128
from fastapi import HTTPException

from build_tds_e2e_fixtures import trace_deductee_type_contract


def test_deductee_type_uses_real_ca_classification_contract():
    trace = trace_deductee_type_contract()
    assert trace["transaction_id"] == "E2E-G-001"
    assert trace["fixture_csv"] == "INDIVIDUAL_HUF"
    assert "deductee_type" in trace["schema_fields"]
    # It is intentionally not a payment-ledger upload field. The reviewed
    # classification is the authoritative supported source for this fixture.
    assert trace["validator_value"] is None
    assert trace["committed_ledger_value"] is None
    assert trace["before_classification_status"] == "RULE_NOT_FOUND"
    assert trace["classification_record_value"] == "INDIVIDUAL_HUF"
    assert trace["classification_rule_dimensions"] == {
        "recipient_residency": "RESIDENT",
        "recipient_category": "INDIVIDUAL_HUF_CONTRACTOR",
        "payer_category": "DESIGNATED_PERSON",
    }
    assert trace["classification_review_dimensions"] == trace["classification_rule_dimensions"]
    assert trace["calculation_rule_dimensions"] == trace["classification_rule_dimensions"]
    assert trace["calculation_status"] == "CALCULATED"
    assert trace["rule_id"] == "STAT-393-6I-INDHUF-2026-V1"
    assert trace["persisted_decision_context"] == trace["retrieved_decision_context"]
    assert trace["persisted_decision_context"] == {
        "payment_nature": "contractor",
        "section_reference": "393(1) [Table: Sl. No. 6(i)]",
        "table_reference": "Table: Sl. No. 6(i)",
        "deductee_type": "INDIVIDUAL_HUF",
        "recipient_residency": "RESIDENT",
        "recipient_category": "INDIVIDUAL_HUF_CONTRACTOR",
        "payer_category": "DESIGNATED_PERSON",
        "rule_id": "STAT-393-6I-INDHUF-2026-V1",
        "rule_version": "2026-27.official.v1",
        "calculation_status": "CALCULATED",
    }
    assert trace["expected_tds"] is not None
    assert trace["persisted_rate_type"] == "Decimal128"
    assert trace["persisted_rate"] == "1.00"
    assert str(trace["persisted_expected_tds"]) == "500.0"
    assert str(trace["api_rate"]) == "1.00"
    assert str(trace["api_expected_tds"]) == "500.0"
    assert trace["rule_snapshot"]["rule_id"] == "STAT-393-6I-INDHUF-2026-V1"


def test_phase2_snapshot_serializes_nested_decimals_exactly():
    from build_tds_e2e_fixtures import _configure_environment
    _configure_environment()
    import server

    original = {"rate": Decimal("1.00"), "nested": {"threshold": Decimal("100000.00")}, "items": [Decimal("500.00")]}
    persisted = server._tds_mongo_snapshot(original)
    assert original["rate"] == Decimal("1.00")  # no in-memory mutation
    assert isinstance(persisted["rate"], Decimal128)
    assert isinstance(persisted["nested"]["threshold"], Decimal128)
    assert persisted["rate"].to_decimal() == Decimal("1.00")
    assert persisted["nested"]["threshold"].to_decimal() == Decimal("100000.00")
    assert persisted["items"][0].to_decimal() == Decimal("500.00")


def test_phase5_persisted_interest_function_is_available_to_server():
    from build_tds_e2e_fixtures import _configure_environment
    _configure_environment()
    from engine.tds_compliance.core import calculate_interest_from_deposit_results
    import server

    assert server.calculate_interest_from_deposit_results is calculate_interest_from_deposit_results


def test_missing_approved_interest_policy_keeps_e2e_paths_reviewable_without_guessing():
    """The real E2E service must retain its policy gate when no approved rules exist."""
    from build_tds_e2e_fixtures import _configure_environment, build

    manifest = build()
    _configure_environment()
    import server

    assert manifest["final_status"] == "E2E_FIXTURES_NOT_READY"
    assert set(manifest["golden"]["interest_statuses"]) == {"POLICY_NOT_CONFIGURED"}
    assert set(manifest["exception"]["interest_statuses"]) == {"POLICY_NOT_CONFIGURED"}
    assert set(manifest["golden"]["actual_result_statuses"]) == {"REVIEW_REQUIRED"}
    assert {"REVIEW_REQUIRED", "MISSING_IN_BOOKS", "MISSING_IN_RETURN"} <= set(manifest["exception"]["actual_result_statuses"])
    assert set(manifest["correction"]["actual_statuses"]) == {"CORRECTION_REVIEW_REQUIRED"}
    assert manifest["shared_database_e2e_records"] == 0

    interest_run_ids = [manifest["golden"]["interest_run_id"], manifest["exception"]["interest_run_id"]]
    results = list(server.db.tds_compliance_interest_results.find({"interest_run_id": {"$in": interest_run_ids}}, {"_id": 0}))
    assert len(results) == 9
    assert {result["overall_status"] for result in results} == {"POLICY_NOT_CONFIGURED"}
    assert all(result.get("total_interest") is None for result in results)
    assert all(not result.get("deduction_rule_snapshot") and not result.get("deposit_rule_snapshot") for result in results)


def test_isolated_golden_workflow_persists_and_restores_phase4_results():
    """Exercise classification → calculation → Phase 4 run → persisted detail."""
    from build_tds_e2e_fixtures import _configure_environment, build

    manifest = build()
    _configure_environment()
    import server

    golden = manifest["golden"]
    detail = server.deposit_compliance_detail(
        golden["assignment_id"],
        golden["deposit_run_id"],
        server.build_development_principal(),
    )

    assert detail["deposit_run"]["calculation_id"]
    assert detail["deposit_run"]["ledger_version_id"] == golden["ledger_version_id"]
    assert detail["deposit_run"]["phase5_rule_snapshot"] is None
    assert {item["transaction_id"] for item in detail["items"]} == set(golden["transaction_ids"])
    # The immutable ledger-version identity belongs to the persisted run, not
    # each individual deposit-result item.
    assert detail["deposit_run"]["calculation_id"] == golden["calculation_result_ids"][0].split(":", 1)[0]
    assert all(item["phase5_rule_snapshot"] is None for item in detail["items"])
    calculations = list(server.db.tds_compliance_calculation_results.find({
        "assignment_id": golden["assignment_id"],
        "calculation_id": detail["deposit_run"]["calculation_id"],
    }, {"_id": 0}))
    assert calculations
    assert {item["calculation_status"] for item in calculations} == {"CALCULATED"}
    assert {item["rule_id"] for item in calculations} == {"STAT-393-6I-INDHUF-2026-V1"}
    assert {
        (item["recipient_residency"], item["recipient_category"], item["payer_category"])
        for item in calculations
    } == {("RESIDENT", "INDIVIDUAL_HUF_CONTRACTOR", "DESIGNATED_PERSON")}


def test_isolated_return_audit_review_keeps_exception_visible_after_assignment_lock():
    """Keep every current isolated unresolved status immutable through a lock."""
    from build_tds_e2e_fixtures import _configure_environment, build

    manifest = build()
    _configure_environment()
    import server

    principal = server.build_development_principal()
    exception = manifest["exception"]
    assignment_id = exception["assignment_id"]
    audit_run_id = exception["audit_run_id"]
    results = server.list_return_audit_results(assignment_id, audit_run_id, principal)["items"]
    unresolved = [item for item in results if item["status"] != "MATCHED"]
    before = {
        item["result_id"]: server.get_return_audit_result(assignment_id, item["result_id"], principal)
        for item in unresolved
    }
    audit_before = server.return_audit_summary(assignment_id, audit_run_id, principal)["latest_run"]

    assert {item["status"] for item in unresolved} == {
        "REVIEW_REQUIRED",
        "MISSING_IN_BOOKS",
        "MISSING_IN_RETURN",
    }
    for result in before.values():
        assert result["assignment_id"] == assignment_id
        assert result["audit_run_id"] == audit_run_id
        assert result["artifact_id"] == exception["return_artifact_id"]

    decisions = []
    for result in before.values():
        decision = server.create_return_audit_review_decision(
            assignment_id,
            result["result_id"],
            server.ReturnAuditReviewDecisionBody(
                decision="KEEP_REVIEW",
                reason="E2E exception remains unresolved pending CA evidence.",
            ),
            principal,
        )
        decisions.append(decision)
        assert decision["assignment_id"] == assignment_id
        assert decision["artifact_id"] == result["artifact_id"]
        assert decision["audit_run_id"] == result["audit_run_id"]
        assert decision["audit_result_id"] == result["result_id"]
        assert decision["result_status"] == result["status"]
        assert decision["source_references"] == result.get("source_references", {})

    locked = server.lock_tds_compliance_assignment(assignment_id)
    after = {
        result_id: server.get_return_audit_result(assignment_id, result_id, principal)
        for result_id in before
    }
    exceptions = server.list_return_audit_exceptions(assignment_id, audit_run_id, principal)["items"]
    persisted_decisions = server.list_return_audit_review_decisions(assignment_id, audit_run_id, None, principal)["items"]
    audit_after = server.return_audit_summary(assignment_id, audit_run_id, principal)["latest_run"]

    assert locked["status"] == "LOCKED"
    assert audit_after == audit_before
    assert after == before
    assert {item["result_id"] for item in exceptions} == set(before)
    assert {(item["result_id"], item["status"]) for item in exceptions} == {
        (result_id, result["status"]) for result_id, result in before.items()
    }
    assert {item["decision_id"] for item in persisted_decisions} == {
        item["decision_id"] for item in decisions
    }
    with pytest.raises(HTTPException, match="locked; review decisions cannot be added"):
        server.create_return_audit_review_decision(
            assignment_id,
            next(iter(before)),
            server.ReturnAuditReviewDecisionBody(decision="CONFIRM", reason="Must remain blocked."),
            principal,
        )


def test_isolated_assignment_lock_requires_security_boundary_and_existing_assignment(monkeypatch):
    """Locking preserves its existing boundary and rejects an unknown assignment."""
    from build_tds_e2e_fixtures import _configure_environment, build

    manifest = build()
    _configure_environment()
    import server

    assignment_id = manifest["exception"]["assignment_id"]
    with pytest.raises(HTTPException, match="TDS Compliance assignment not found"):
        server.lock_tds_compliance_assignment("E2E_UNKNOWN_ASSIGNMENT")

    before = server.db.tds_compliance_assignments.find_one({"assignment_id": assignment_id}, {"_id": 0})

    def forbidden_boundary():
        raise HTTPException(403, "Unauthorized test principal.")

    monkeypatch.setattr(server, "_tds_compliance_security_boundary", forbidden_boundary)
    with pytest.raises(HTTPException, match="Unauthorized test principal"):
        server.lock_tds_compliance_assignment(assignment_id)

    after = server.db.tds_compliance_assignments.find_one({"assignment_id": assignment_id}, {"_id": 0})
    assert after == before


def test_isolated_correction_review_remains_visible_after_assignment_lock():
    """The correction-specific unresolved status also survives the lock unchanged."""
    from build_tds_e2e_fixtures import _configure_environment, build

    manifest = build()
    _configure_environment()
    import server

    principal = server.build_development_principal()
    assignment_id = manifest["golden"]["assignment_id"]
    correction = manifest["correction"]
    results = server.list_return_audit_results(assignment_id, correction["audit_run_id"], principal)["items"]
    assert {item["status"] for item in results} == {"CORRECTION_REVIEW_REQUIRED"}
    before = {item["result_id"]: server.get_return_audit_result(assignment_id, item["result_id"], principal) for item in results}

    decisions = [
        server.create_return_audit_review_decision(
            assignment_id,
            result_id,
            server.ReturnAuditReviewDecisionBody(
                decision="KEEP_REVIEW",
                reason="Correction chain requires CA review in the isolated E2E fixture.",
            ),
            principal,
        )
        for result_id in before
    ]
    locked = server.lock_tds_compliance_assignment(assignment_id)
    after = {
        result_id: server.get_return_audit_result(assignment_id, result_id, principal)
        for result_id in before
    }
    persisted_decisions = server.list_return_audit_review_decisions(
        assignment_id,
        correction["audit_run_id"],
        None,
        principal,
    )["items"]

    assert locked["status"] == "LOCKED"
    assert after == before
    assert {item["artifact_id"] for item in after.values()} == {correction["artifact_id"]}
    assert {item["audit_run_id"] for item in after.values()} == {correction["audit_run_id"]}
    assert {item["decision_id"] for item in persisted_decisions} == {
        item["decision_id"] for item in decisions
    }
    assert all(item["decision"] == "KEEP_REVIEW" for item in persisted_decisions)
