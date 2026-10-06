"""Run complete isolated TDS E2E only with supplied CA policy values."""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
BACKEND = Path(__file__).resolve().parents[2]
E2E = BACKEND / "tests" / "e2e"
REPORT_JSON = E2E / "tds_phase5_execution_report.json"
REPORT_MD = E2E / "tds_phase5_execution_report.md"
EXPECTED_DB = "26as_reconciliation_tds_e2e"
REQUIRED_TYPES = {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}
DUE_DATE_POLICY_KIND = "CONTRACTOR_DEPOSIT_DUE_DATE"
E2E_ASSIGNMENTS = {"E2E_GOLDEN_PATH", "E2E_EXCEPTION_PATH"}

def _json(value: Any) -> Any:
    if isinstance(value, list): return [_json(item) for item in value]
    if isinstance(value, dict): return {key: _json(item) for key, item in value.items() if key != "_id"}
    if hasattr(value, "isoformat"): return value.isoformat()
    return str(value) if type(value).__name__ == "Decimal128" else value

def _configure() -> None:
    # Keep the runner's principal and database context identical to the
    # deterministic fixture builder before any lifecycle service is called.
    from build_tds_e2e_fixtures import _configure_environment
    _configure_environment()

def _write_report(result: dict) -> None:
    REPORT_JSON.write_text(json.dumps(_json(result), indent=2), encoding="utf-8")
    lines = ["# Phase 5 isolated E2E execution report", "", "Final: " + result["final_status"], "", "Database: " + result["database"], "Policy state: " + result["policy_state"]]
    if result.get("blocker"): lines.extend(["", "Blocker: " + result["blocker"]])
    for name in ("policy_verification", "due_date_policy_verification", "deposit_matching_policy_verification", "golden", "exception", "review_and_lock"):
        if result.get(name): lines.extend(["", "## " + name.replace("_", " ").title(), "", json.dumps(result[name], indent=2)])
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _policy_payload_path() -> Path | None:
    raw = os.environ.get("TDS_E2E_PHASE5_POLICY_FILE")
    return Path(raw) if raw else None

def _verify_active_policies(server, provisioned: list[dict], supplied: dict) -> list[dict]:
    supplied_by_type = {item["interest_type"]: item for item in supplied["policies"]}
    if set(supplied_by_type) != REQUIRED_TYPES: raise RuntimeError("E2E EXECUTION BLOCKED: supplied policies do not contain both required interest types.")
    verified = []
    for created in provisioned:
        record = server.db.tds_compliance_rules.find_one({"rule_id": created["rule_id"]}, {"_id": 0})
        if not record or record.get("lifecycle") != "ACTIVE" or not record.get("active"): raise RuntimeError("E2E EXECUTION BLOCKED: policy is not active.")
        source = supplied_by_type[record.get("interest_type")]
        fields = ("financial_year", "payment_nature", "deductee_type", "governing_act", "effective_from", "effective_to", "priority")
        mismatch = [field for field in fields if record.get(field) != source.get(field)]
        if mismatch or record.get("rule_version") != created.get("rule_version"): raise RuntimeError("E2E EXECUTION BLOCKED: activated policy verification failed: " + ", ".join(mismatch or ["rule_version"]))
        metadata = record.get("approval_metadata") or {}
        if not metadata.get("approved_source") or metadata.get("approved_source") != source.get("approval_metadata", {}).get("approved_source"): raise RuntimeError("E2E EXECUTION BLOCKED: activated policy approval metadata was not persisted.")
        verified.append({key: record.get(key) for key in ("rule_id", "rule_version", "interest_type", "lifecycle", "active", "financial_year", "payment_nature", "deductee_type", "governing_act", "effective_from", "effective_to", "priority", "approval_metadata", "approved_at", "approved_by", "activated_at", "activated_by")})
    if {item["interest_type"] for item in verified} != REQUIRED_TYPES: raise RuntimeError("E2E EXECUTION BLOCKED: activated policy types are incomplete.")
    return verified


def _provision_explicit_due_date_policy(server, supplied: dict) -> list[dict]:
    """Provision only supplied E2E deadline policy data through its lifecycle."""
    policies = supplied.get("due_date_policies")
    if not isinstance(policies, list) or len(policies) != 1:
        raise RuntimeError(
            "E2E EXECUTION BLOCKED: exactly one explicit Phase 4 due-date policy configuration is required."
        )
    policy = policies[0]
    if not isinstance(policy, dict) or policy.get("policy_kind") != DUE_DATE_POLICY_KIND:
        raise RuntimeError("E2E EXECUTION BLOCKED: supplied due-date configuration has an unsupported policy kind.")
    approval = policy.get("approval_metadata")
    if not isinstance(approval, dict) or not all(approval.get(key) for key in ("approved_source", "approval_authority", "approval_reference")):
        raise RuntimeError("E2E EXECUTION BLOCKED: due-date policy approval metadata is incomplete.")
    allowed = set(server.TdsComplianceRuleBody.model_fields)
    try:
        body = server.TdsComplianceRuleBody(**{key: value for key, value in policy.items() if key in allowed})
    except Exception as exc:
        raise RuntimeError(f"E2E EXECUTION BLOCKED: due-date policy shape is invalid: {exc}") from exc
    errors = server._approval_errors(body.model_dump())
    if errors:
        raise RuntimeError("E2E EXECUTION BLOCKED: due-date policy cannot be submitted: " + ", ".join(errors))
    traceability = server.source_traceability_snapshot(body.model_dump())
    if traceability["status"] != "VERIFIED":
        raise RuntimeError("E2E EXECUTION BLOCKED: due-date policy source traceability is incomplete: " + ", ".join(traceability["missing_fields"] + traceability["validation_errors"]))
    conflict = server._conflicting_active_due_date_policy({**body.model_dump(), "rule_id": "E2E-DUE-PREFLIGHT"})
    if conflict:
        raise RuntimeError(f"E2E EXECUTION BLOCKED: due-date policy scope conflicts with {conflict['rule_id']}.")
    principal = server.build_development_principal()
    created = server.create_tds_compliance_rule(body, principal)
    created = server.submit_tds_compliance_rule(created["rule_id"], principal)
    created = server.approve_tds_compliance_rule(created["rule_id"], principal)
    created = server.activate_tds_compliance_rule(created["rule_id"], principal)
    if not created.get("active") or created.get("lifecycle") != "ACTIVE":
        raise RuntimeError("E2E EXECUTION BLOCKED: due-date policy did not become active.")
    return [{key: created.get(key) for key in (
        "rule_id", "rule_version", "policy_kind", "lifecycle", "active", "financial_year",
        "governing_act", "payment_nature", "deductee_type", "section_reference", "deductor_type",
        "challan_route", "deadline_mode", "days_after_month_end", "deadline_month", "deadline_day",
        "effective_from", "effective_to", "approved_at", "approved_by", "activated_at", "activated_by",
    )}]


def _provision_explicit_deposit_matching_policies(server, supplied: dict) -> list[dict]:
    """Create only supplied, assignment-scoped Phase 4 matching policies."""
    policies = supplied.get("deposit_matching_policies")
    if not isinstance(policies, list) or len(policies) != len(E2E_ASSIGNMENTS):
        raise RuntimeError("E2E EXECUTION BLOCKED: explicit Phase 4 deposit-matching policies are required for both E2E assignments.")
    assignments = {policy.get("assignment_id") for policy in policies if isinstance(policy, dict)}
    if assignments != E2E_ASSIGNMENTS:
        raise RuntimeError("E2E EXECUTION BLOCKED: deposit-matching policies must cover exactly the Golden and Exception E2E assignments.")
    principal = server.build_development_principal()
    created = []
    for policy in policies:
        try:
            body = server.Phase5PolicyBody(**policy)
        except Exception as exc:
            raise RuntimeError(f"E2E EXECUTION BLOCKED: deposit-matching policy shape is invalid: {exc}") from exc
        if not body.active or body.status not in {"APPROVED", "ACTIVE"}:
            raise RuntimeError("E2E EXECUTION BLOCKED: deposit-matching policy must be explicitly active and approved.")
        record = server.create_phase5_policy(body.assignment_id, body, principal)
        created.append({key: record.get(key) for key in (
            "policy_id", "policy_version", "assignment_id", "financial_years", "effective_from",
            "effective_to", "priority", "active", "status", "authoritative_identifier_types",
            "permitted_relationship_types", "allocation_policy", "ambiguity_policy",
        )})
    return created

def _run_summary(server, manifest: dict, name: str) -> dict:
    run = manifest[name]
    interest = list(server.db.tds_compliance_interest_results.find({"assignment_id": run["assignment_id"], "interest_run_id": run["interest_run_id"]}, {"_id": 0}).sort("transaction_id", 1))
    audits = list(server.db.tds_compliance_return_audit_results.find({"assignment_id": run["assignment_id"], "audit_run_id": run["audit_run_id"]}, {"_id": 0}).sort("transaction_id", 1))
    if any(item.get("overall_status") == "POLICY_NOT_CONFIGURED" for item in interest): raise RuntimeError("E2E EXECUTION BLOCKED: " + name + " still contains POLICY_NOT_CONFIGURED.")
    if not interest or any(not item.get("deduction_rule_snapshot") or not item.get("deposit_rule_snapshot") for item in interest): raise RuntimeError("E2E EXECUTION BLOCKED: " + name + " interest results lack persisted policy snapshots.")
    return {"assignment_id": run["assignment_id"], "ledger_version_id": run["ledger_version_id"], "calculation_result_ids": run["calculation_result_ids"], "deposit_run_id": run["deposit_run_id"], "interest_run_id": run["interest_run_id"], "interest_result_ids": [run["interest_run_id"] + ":" + str(item.get("transaction_id")) for item in interest], "interest_results": _json(interest), "return_artifact_id": run["return_artifact_id"], "audit_run_id": run["audit_run_id"], "return_audit_result_ids": [item.get("result_id") or item.get("audit_result_id") or run["audit_run_id"] + ":" + str(item.get("transaction_id")) for item in audits], "return_audit_statuses": [item.get("status") for item in audits], "review_or_exception_count": sum(item.get("status") != "MATCHED" for item in audits)}


def _review_and_lock(server, manifest: dict) -> dict:
    """Exercise the real review/lock services without altering any audit result."""
    principal = server.build_development_principal()
    result = {}
    for name in ("golden", "exception"):
        run = manifest[name]
        assignment_id, audit_run_id = run["assignment_id"], run["audit_run_id"]
        before = server.list_return_audit_results(assignment_id, audit_run_id, principal)["items"]
        unresolved = [item for item in before if item.get("status") != "MATCHED"]
        decisions = []
        for item in unresolved:
            decisions.append(server.create_return_audit_review_decision(
                assignment_id,
                item["result_id"],
                server.ReturnAuditReviewDecisionBody(
                    decision="KEEP_REVIEW",
                    reason="Isolated E2E evidence remains unresolved pending CA review.",
                ),
                principal,
            ))
        locked = server.lock_tds_compliance_assignment(assignment_id)
        after = server.list_return_audit_results(assignment_id, audit_run_id, principal)["items"]
        if after != before:
            raise RuntimeError("E2E EXECUTION BLOCKED: assignment lock altered persisted Return Audit results.")
        persisted = server.list_return_audit_review_decisions(assignment_id, audit_run_id, None, principal)["items"]
        if {item["decision_id"] for item in persisted} != {item["decision_id"] for item in decisions}:
            raise RuntimeError("E2E EXECUTION BLOCKED: persisted review decision identity mismatch.")
        result[name] = {
            "assignment_id": assignment_id,
            "locked_status": locked.get("status"),
            "unresolved_before_lock": len(unresolved),
            "review_decision_ids": [item["decision_id"] for item in decisions],
            "return_audit_results_preserved": True,
        }
    return result

def execute() -> dict:
    _configure()
    from reset_tds_e2e import reset
    from provision_tds_phase5_interest_policy import provision_from_explicit_file
    reset_result = reset()
    base = {"executed_at": datetime.now(timezone.utc).isoformat(), "database": EXPECTED_DB, "reset": reset_result, "policy_state": "POLICY_VALUES_NOT_SUPPLIED", "final_status": "E2E_PHASE5_POLICY_NOT_READY"}
    path = _policy_payload_path()
    if not path:
        base["blocker"] = "TDS_E2E_PHASE5_POLICY_FILE is required; no policy was provisioned."
        _write_report(base)
        return base
    provisioned = provision_from_explicit_file(path)
    if provisioned.get("final_status") != "E2E_PHASE5_POLICY_READY":
        base.update({"policy_state": provisioned.get("policy_state", "POLICY_VALUES_NOT_SUPPLIED"), "blocker": "Policy provisioner did not activate both required policies."})
        _write_report(base)
        return base
    supplied = json.loads(path.read_text(encoding="utf-8-sig"))
    import server
    policy_verification = _verify_active_policies(server, provisioned["created"], supplied)
    due_date_policy_verification = _provision_explicit_due_date_policy(server, supplied)
    deposit_matching_policy_verification = _provision_explicit_deposit_matching_policies(server, supplied)
    from build_tds_e2e_fixtures import build
    manifest = build(reset_database=False)
    if manifest["shared_database_e2e_records"] != 0:
        raise RuntimeError("E2E EXECUTION BLOCKED: shared database contains E2E records.")
    golden, exception = _run_summary(server, manifest, "golden"), _run_summary(server, manifest, "exception")
    review_and_lock = _review_and_lock(server, manifest)
    result = {**base, "policy_state": "POLICY_READY_FOR_E2E", "policy_verification": policy_verification, "due_date_policy_verification": due_date_policy_verification, "deposit_matching_policy_verification": deposit_matching_policy_verification, "golden": golden, "exception": exception, "review_and_lock": review_and_lock, "shared_database_e2e_records": manifest["shared_database_e2e_records"], "final_status": "E2E_PHASE5_EXECUTION_COMPLETED"}
    _write_report(result)
    return result

if __name__ == "__main__":
    try: result = execute()
    except Exception as exc:
        result = {"executed_at": datetime.now(timezone.utc).isoformat(), "database": os.environ.get("DB_NAME", EXPECTED_DB), "policy_state": "POLICY_VALUES_NOT_SUPPLIED", "final_status": "E2E_PHASE5_POLICY_NOT_READY", "blocker": type(exc).__name__ + ": " + str(exc)}
        _write_report(result); print(json.dumps(result, indent=2)); raise SystemExit(1)
    print(json.dumps(_json(result), indent=2))
    if result["final_status"] != "E2E_PHASE5_EXECUTION_COMPLETED": raise SystemExit(1)
