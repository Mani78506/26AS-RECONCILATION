"""Provision explicitly supplied Phase 5 interest policies into the isolated E2E DB.

This script never supplies statutory values.  A CA-approved JSON document must
be provided through TDS_E2E_PHASE5_POLICY_FILE.  It uses the normal rule
lifecycle so history, approval, activation and audit metadata are retained.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[2]
EXPECTED_DB = "26as_reconciliation_tds_e2e"
EXPECTED_TYPES = {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}


def _configure() -> None:
    # Reuse the fixture builder's isolated database and development-principal
    # setup.  The provisioner invokes real lifecycle services, which must use
    # the same seeded E2E organisation as the assignments it provisions for.
    from build_tds_e2e_fixtures import _configure_environment
    _configure_environment()


def _is_provisional_uat(policy: dict) -> bool:
    return (policy.get("policy_status") == "PROVISIONAL_UAT" and policy.get("environment") == "isolated_e2e_only"
            and policy.get("ca_approved") is False and (policy.get("approval_metadata") is None or policy.get("approval_metadata") == {})
            and policy.get("assumption_status") == "PROVISIONAL" and policy.get("source_gap") is True
            and policy.get("ca_review_required") is True)


def _preflight(server, policies: list[dict]) -> list[tuple[object, dict | None, bool]]:
    """Validate all supplied policies before writing a single E2E record."""
    allowed = set(server.TdsComplianceRuleBody.model_fields)
    prepared = []
    for supplied_policy in policies:
        provisional = _is_provisional_uat(supplied_policy)
        approval_metadata = supplied_policy.get("approval_metadata")
        if provisional and not (server.db.name == EXPECTED_DB and os.environ.get("TDS_PROVISIONAL_UAT_E2E") == "true"):
            raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: provisional UAT policies require the isolated E2E environment flag.")
        if not provisional and (not isinstance(approval_metadata, dict) or not all(
            approval_metadata.get(field)
            for field in ("approved_source", "approval_authority", "approval_reference")
        )):
            raise RuntimeError(
                "E2E POLICY PROVISIONING BLOCKED: explicit approval_metadata.approved_source, "
                "approval_authority, and approval_reference are required."
            )
        try:
            body = server.TdsComplianceRuleBody(**{key: value for key, value in supplied_policy.items() if key in allowed})
        except Exception as exc:
            raise RuntimeError(f"E2E POLICY PROVISIONING BLOCKED: policy shape is incomplete or invalid: {exc}") from exc
        missing = server._approval_errors(body.model_dump())
        if missing:
            raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: policy cannot be submitted until configured: " + ", ".join(missing))
        traceability = server.source_traceability_snapshot(body.model_dump())
        if traceability["status"] != "VERIFIED":
            details = ", ".join(traceability["missing_fields"] + traceability["validation_errors"])
            raise RuntimeError(
                "E2E POLICY PROVISIONING BLOCKED: source traceability is incomplete: " + details
            )
        # Query against the draft's eventual identity fields. The helper only
        # excludes a rule ID when supplied by an existing persisted document.
        conflict = server._conflicting_active_interest_rule({**body.model_dump(), "rule_id": "E2E-PREFLIGHT"})
        if conflict:
            raise RuntimeError(f"E2E POLICY PROVISIONING BLOCKED: active policy scope conflicts with {conflict['rule_id']}.")
        prepared.append((body, approval_metadata if isinstance(approval_metadata, dict) else None, provisional))
    return prepared


def provision_from_explicit_file(path: str | os.PathLike[str] | None = None) -> dict:
    _configure()
    raw_path = str(path) if path is not None else os.environ.get("TDS_E2E_PHASE5_POLICY_FILE")
    if not raw_path:
        return {
            "database": EXPECTED_DB,
            "policy_state": "POLICY_VALUES_NOT_SUPPLIED",
            "final_status": "E2E_PHASE5_POLICY_NOT_READY",
        }
    supplied = Path(raw_path)
    if not supplied.is_file():
        raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: explicit policy configuration file was not found.")

    # Accept the UTF-8 BOM emitted by some Windows editors while keeping the
    # configuration a normal JSON file.
    payload = json.loads(supplied.read_text(encoding="utf-8-sig"))
    policies = payload.get("policies")
    if not isinstance(policies, list) or len(policies) != 2:
        raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: configuration must contain exactly two policies.")
    types = {policy.get("interest_type") for policy in policies if isinstance(policy, dict)}
    if types != EXPECTED_TYPES:
        raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: exactly one deduction-delay and one deposit-delay policy are required.")

    import server

    if server.db.name != EXPECTED_DB:
        raise RuntimeError("E2E POLICY PROVISIONING BLOCKED: backend is connected to a non-E2E database.")
    principal = server.build_development_principal()
    prepared = _preflight(server, policies)
    created = []
    for body, approval_metadata, provisional in prepared:
        rule = server.create_tds_compliance_rule(body, principal)
        if provisional:
            now = server.now_iso()
            server.db.tds_compliance_rules.update_one({"rule_id": rule["rule_id"], "lifecycle": "DRAFT"}, {"$set": {"lifecycle": "ACTIVE", "active": True, "activated_at": now, "activated_by": "PROVISIONAL_UAT_E2E_RUNNER", "updated_at": now, "updated_by": "PROVISIONAL_UAT_E2E_RUNNER"}})
            rule = server._rule_or_404(rule["rule_id"])
            server._record_rule_revision(rule, "PROVISIONAL_UAT_ACTIVATED", principal)
            server._audit_tds_rule("RULE_PROVISIONAL_UAT_ACTIVATED", rule, principal, old_value={"lifecycle": "DRAFT"}, new_value={"lifecycle": "ACTIVE", "policy_status": "PROVISIONAL_UAT"})
        else:
            rule = server.submit_tds_compliance_rule(rule["rule_id"], principal)
            rule = server.approve_tds_compliance_rule(rule["rule_id"], principal)
            rule = server.activate_tds_compliance_rule(rule["rule_id"], principal)
        created.append({
            "rule_id": rule["rule_id"],
            "rule_version": rule["rule_version"],
            "interest_type": rule["interest_type"],
            "lifecycle": rule["lifecycle"],
            "active": rule["active"],
            "approved_at": rule["approved_at"],
            "approved_by": rule["approved_by"],
            "activated_at": rule["activated_at"],
            "activated_by": rule["activated_by"],
            "policy_status": rule.get("policy_status"), "ca_approved": rule.get("ca_approved"),
            "approved_source": approval_metadata.get("approved_source") if approval_metadata else None,
        })
    return {
        "database": EXPECTED_DB,
        "policy_state": "PROVISIONAL_UAT_POLICY_READY" if any(item[2] for item in prepared) else "POLICY_READY_FOR_E2E",
        "created": created,
        "final_status": "E2E_PHASE5_POLICY_READY",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(provision_from_explicit_file(), indent=2))
    except Exception as exc:
        print(json.dumps({
            "database": EXPECTED_DB,
            "policy_state": "POLICY_VALUES_NOT_SUPPLIED",
            "final_status": "E2E_PHASE5_POLICY_NOT_READY",
            "error": f"{type(exc).__name__}: {exc}",
        }, indent=2))
        raise SystemExit(1)
