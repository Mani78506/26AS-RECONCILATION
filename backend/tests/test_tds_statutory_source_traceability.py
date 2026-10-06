from copy import deepcopy

import server
from auth_boundary import Principal
from engine.tds_compliance.source_traceability import source_traceability_snapshot
from seeders.seed_tds_statutory_catalog import document, is_traceability_only_difference, load_catalog, validate


def test_catalog_rule_without_official_evidence_is_explicitly_verification_required():
    catalog, rules = load_catalog()
    assert catalog["catalog_version"]
    assert all(validate(rule) == [] for rule in rules)

    # Keep this guard independent of the live catalog: its entries may gain
    # verified evidence as the statutory research is completed.
    without_evidence = deepcopy(rules[0])
    for field in (
        "source_url",
        "source_document_title",
        "source_retrieved_at",
        "source_verified_at",
        "source_verification_evidence",
    ):
        without_evidence[field] = None
    without_evidence["status"] = "DRAFT"

    stored = document(without_evidence, catalog["catalog_version"])

    assert stored["source_traceability_status"] == "VERIFICATION_REQUIRED"
    assert stored["source_traceability"]["official_url"] is None
    assert "source_locator" in stored["source_traceability"]["missing_fields"]
    assert stored["source_traceability"]["ca_approval_status"] == "CA_APPROVAL_NOT_RECORDED"
    legacy = {key: value for key, value in stored.items() if key not in {
        "source_provision_reference", "source_url", "source_document_title",
        "source_retrieved_at", "source_verification_evidence",
        "source_traceability", "source_traceability_status",
    }}
    assert is_traceability_only_difference(legacy, stored)


def test_complete_source_metadata_is_verified_and_malformed_url_is_rejected():
    record = {
        "source": "Test authority",
        "source_url": "https://www.indiacode.nic.in/",
        "source_document_title": "Test source document",
        "source_provision_reference": "Test provision",
        "effective_from": "2026-04-01",
        "effective_to": "2027-03-31",
        "source_retrieved_at": "2026-10-01",
        "source_verified_at": "2026-10-01",
        "source_verification_evidence": "Test evidence reference",
        "lifecycle": "APPROVED",
        "approved_at": "2026-10-01T00:00:00Z",
        "approved_by": "test-ca",
        "approval_metadata": {"approval_authority": "test-ca", "approval_reference": "TEST-APPROVAL-1"},
    }

    traceability = source_traceability_snapshot(record)
    assert traceability["status"] == "VERIFIED"
    assert traceability["ca_approval_status"] == "CA_APPROVAL_RECORDED"

    _, rules = load_catalog()
    malformed = {**deepcopy(rules[0]), "source_url": "not-a-url"}
    assert "source_url must be an absolute https URL" in validate(malformed)


def test_2026_contractor_catalog_row_records_table_6_scope_distinction():
    _, rules = load_catalog()
    rule = next(item for item in rules if item["rule_id"] == "STAT-393-6I-INDHUF-2026-V1")

    assert rule["table_reference"] == "Table: Sl. No. 6(i)"
    assert rule["payer_category"] == "DESIGNATED_PERSON"
    assert rule["deductee_type"] == "INDIVIDUAL_HUF"
    assert rule["recipient_category"] == "INDIVIDUAL_HUF_CONTRACTOR"
    assert rule["form_141_applicability"] == "NOT_APPLICABLE_TABLE_6_I"
    assert rule["rate_value"] == "1"


def test_rule_draft_retains_traceability_and_existing_source_approval_guard():
    principal = Principal("test-ca", "ORG-1", frozenset({"ADMIN"}))
    body = server.TdsComplianceRuleBody(
        organization_id="ORG-1",
        financial_year="2026-27",
        effective_from="2026-04-01",
        effective_to="2027-03-31",
        governing_act="INCOME_TAX_ACT_2025",
        payment_nature="contractor",
        deductee_type="INDIVIDUAL_HUF",
        section_reference="393(1) [Table: Sl. No. 6(i)]",
        rate="1",
        threshold_type="NO_THRESHOLD",
        calculation_basis="full_amount",
        source="Test authority",
        source_reference="Test reference",
    )

    draft = server._rule_document(body, principal)

    assert draft["source_traceability_status"] == "VERIFICATION_REQUIRED"
    assert draft["source_traceability"]["legal_provision_reference"] == "393(1) [Table: Sl. No. 6(i)]"
    assert "source" in server._approval_errors({**draft, "source": None})
