from copy import deepcopy
from types import SimpleNamespace

from engine.tds_compliance.core import calculate_deposit_compliance, validate_deposit_evidence
from fastapi import FastAPI
from fastapi.testclient import TestClient
import server


class _Collection:
    def __init__(self):
        self.rows = []

    def insert_one(self, row):
        self.rows.append(deepcopy(row))

    def insert_many(self, rows):
        self.rows.extend(deepcopy(rows))

    def find_one(self, query, projection=None, sort=None):
        rows = self.find(query, projection)
        return rows[0] if rows else None

    def find(self, query, projection=None):
        return [deepcopy(row) for row in self.rows if all(row.get(key) == value for key, value in query.items())]

    def count_documents(self, query):
        return len(self.find(query))


class _PolicyCollection(_Collection):
    """The preview's policy query contains Mongo's $or, irrelevant to this test."""

    def find(self, query, projection=None):
        return deepcopy(self.rows)


ASSIGNMENT = {"assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1", "financial_year": "2025-26", "quarter": "Q2", "tan": "ABCD12345E"}
POLICY = {"policy_id": "DEP-POLICY", "policy_version": "1", "active": True, "status": "APPROVED", "financial_years": ["2025-26"], "effective_from": "2025-04-01", "effective_to": "2026-03-31", "priority": 1}
LIABILITY = {"transaction_id": "PAY-1", "financial_year": "2025-26", "quarter": "Q2", "effective_event_date": "2025-06-01", "calculation_status": "CALCULATED", "expected_tds": 100.0, "tds_deducted": 95.0}
EVIDENCE = {"evidence_id": "E-1", "deposit_transaction_id": "PAY-1", "tds_amount": 100.0, "deposit_date": "2025-06-05", "validation_status": "VALID"}


def control(liabilities=None, evidence=None, interest=None, policies=None):
    return calculate_deposit_compliance(liabilities or [LIABILITY], evidence if evidence is not None else [EVIDENCE], interest or [], assignment_id="A-1", calculation_id="CALC-1", evidence_version_id="EV-1", policies=[POLICY] if policies is None else policies)[0]


def test_expected_tds_is_frozen_snapshot_not_actual_or_live_rule_value():
    result = control()
    assert result["expected_tds"] == 100.0
    assert result["actual_tds_deducted"] == 95.0
    assert result["deposited_tds"] == 100.0
    assert result["deposit_difference"] == 0.0
    assert result["deposit_status"] == "DEPOSIT_MATCHED"


def test_exact_reference_only_amount_difference_and_missing_evidence():
    difference = control(evidence=[{**EVIDENCE, "tds_amount": 90.0}])
    missing = control(evidence=[])
    unrelated = control(evidence=[{**EVIDENCE, "deposit_transaction_id": "PAY-OTHER"}])
    assert difference["deposit_status"] == "DEPOSIT_SHORT"
    assert missing["deposit_status"] == "MISSING_DEPOSIT_EVIDENCE"
    assert unrelated["deposit_status"] == "MISSING_DEPOSIT_EVIDENCE"


def test_timing_requires_configured_due_date_and_hands_off_interest():
    result = control(interest=[{"transaction_id": "PAY-1", "overall_status": "INTEREST_REVIEW_REQUIRED"}])
    assert result["timeliness_status"] == "DUE_DATE_NOT_DETERMINABLE"
    assert result["interest_status"] == "INTEREST_REVIEW_REQUIRED"
    assert result["overall_status"] == "NOT_DETERMINABLE"


def test_invalid_date_sequence_is_reviewed_without_guessing_timing():
    result = control(interest=[{"transaction_id": "PAY-1", "actual_deduction_date": "2025-06-10"}])
    assert result["timeliness_status"] == "INVALID_DATE_SEQUENCE"
    assert result["overall_status"] == "REVIEW_REQUIRED"


def test_missing_policy_is_controlled_not_a_legacy_timing_default():
    result = control(policies=[])
    assert result["reason_code"] == "PHASE5_POLICY_NOT_FOUND"
    assert result["overall_status"] == "REVIEW_REQUIRED"


def test_policy_without_explicit_approval_and_activation_is_not_selected():
    incomplete = {key: value for key, value in POLICY.items() if key not in {"active", "status"}}
    result = control(policies=[incomplete])
    assert result["reason_code"] == "PHASE5_POLICY_NOT_FOUND"
    assert result["overall_status"] == "REVIEW_REQUIRED"


def test_controlled_contractor_due_date_policy_is_selected_only_when_governed():
    """Synthetic configuration values exercise plumbing only, never statutory timing."""
    liability = {
        **LIABILITY,
        "governing_act": "TEST_ACT",
        "payment_nature": "contractor",
        "deductee_type": "TEST_DEDUCTEE",
        "section_reference": "TEST_SECTION",
        "actual_deduction_date": "2025-06-01",
        "contractor_applicability": {"status": "APPLICABLE"},
        "contractor_due_date_context": {
            "deduction_date": "2025-06-01", "deductor_type": "OTHER_DEDUCTOR",
            "challan_route": None, "governing_act": "TEST_ACT",
            "financial_year": "2025-26", "section_reference": "TEST_SECTION",
            "payment_nature": "contractor", "deductee_type": "TEST_DEDUCTEE",
        },
    }
    approved = {
        "policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE", "rule_id": "TEST-DUE-1",
        "rule_version": "v1", "active": True, "lifecycle": "ACTIVE",
        "approved_at": "2025-01-01T00:00:00Z", "approved_by": "TEST-CA",
        "source_traceability_status": "VERIFIED", "governing_act": "TEST_ACT",
        "financial_year": "2025-26", "payment_nature": "contractor",
        "deductee_type": "TEST_DEDUCTEE", "section_reference": "TEST_SECTION",
        "deductor_type": "OTHER_DEDUCTOR", "challan_route": None,
        "effective_from": "2025-04-01", "effective_to": "2026-03-31",
        "deadline_mode": "DEDUCTION_DATE",
    }
    missing = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", policies=[POLICY], due_date_policies=[])[0]
    unapproved = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", policies=[POLICY], due_date_policies=[{**approved, "approved_at": None}])[0]
    selected = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", policies=[POLICY], due_date_policies=[approved])[0]
    expired = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", policies=[POLICY], due_date_policies=[{**approved, "effective_to": "2025-05-31"}])[0]
    ambiguous = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", policies=[POLICY], due_date_policies=[approved, {**approved, "rule_id": "TEST-DUE-2"}])[0]

    assert missing["contractor_due_date_reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert unapproved["contractor_due_date_reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert expired["contractor_due_date_reason_code"] == "DUE_DATE_POLICY_NOT_CONFIGURED"
    assert ambiguous["contractor_due_date_reason_code"] == "DUE_DATE_POLICY_AMBIGUOUS"
    assert selected["deposit_due_date"] == "2025-06-01"
    assert selected["due_date_policy_snapshot"]["rule_id"] == "TEST-DUE-1"
    assert selected["due_date_policy_branch_inputs"]["deductor_type"] == "OTHER_DEDUCTOR"


def test_due_date_policy_snapshot_is_immutable_in_persisted_deposit_detail(monkeypatch):
    """The result retains the approved configuration identity after live data changes."""
    liability = {
        **LIABILITY, "governing_act": "TEST_ACT", "payment_nature": "contractor",
        "deductee_type": "TEST_DEDUCTEE", "section_reference": "TEST_SECTION",
        "actual_deduction_date": "2025-06-01",
        "contractor_applicability": {"status": "APPLICABLE"},
        "contractor_due_date_context": {"deduction_date": "2025-06-01", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "governing_act": "TEST_ACT", "financial_year": "2025-26", "section_reference": "TEST_SECTION", "payment_nature": "contractor", "deductee_type": "TEST_DEDUCTEE"},
    }
    due_policy = {"policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE", "rule_id": "TEST-DUE-1", "rule_version": "v1", "active": True, "lifecycle": "ACTIVE", "approved_at": "2025-01-01T00:00:00Z", "approved_by": "TEST-CA", "source_traceability_status": "VERIFIED", "governing_act": "TEST_ACT", "financial_year": "2025-26", "payment_nature": "contractor", "deductee_type": "TEST_DEDUCTEE", "section_reference": "TEST_SECTION", "deductor_type": "OTHER_DEDUCTOR", "challan_route": None, "effective_from": "2025-04-01", "effective_to": "2026-03-31", "deadline_mode": "DEDUCTION_DATE"}
    items = calculate_deposit_compliance([liability], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", evidence_version_id="EV-1", policies=[POLICY], due_date_policies=[due_policy])
    due_policy["rule_version"] = "v2"
    db = SimpleNamespace(tds_compliance_deposit_results=_Collection(), tds_compliance_deposit_relationships=_Collection(), tds_compliance_deposit_summaries=_Collection(), tds_compliance_deposit_runs=_Collection(), tds_compliance_deposit_reviews=_Collection(), tds_compliance_deposit_evidence_rows=_Collection())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_deposit_compliance_preview", lambda *_: ({**ASSIGNMENT, "status": "DRAFT"}, {"ledger_version_id": "LEDGER-1"}, "EV-1", items))
    monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

    created = server.run_deposit_compliance("A-1", server.DepositEvidenceBody(calculation_id="CALC-1", evidence_version_id="EV-1"), None)
    restored = server.deposit_compliance_detail("A-1", created["deposit_run"]["deposit_run_id"], None)["items"][0]

    assert restored["due_date_policy_snapshot"]["rule_id"] == "TEST-DUE-1"
    assert restored["due_date_policy_snapshot"]["rule_version"] == "v1"
    assert restored["due_date_policy_branch_inputs"]["deductor_type"] == "OTHER_DEDUCTOR"


def test_deposit_policy_decision_persists_and_is_returned_by_detail_route(monkeypatch):
    assignment = {"assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1", "status": "DRAFT"}
    calculation = {"ledger_version_id": "LEDGER-1"}

    for policy, expected_reason, expected_snapshot in (
        (POLICY, None, "DEP-POLICY"),
        ({key: value for key, value in POLICY.items() if key not in {"active", "status"}}, "PHASE5_POLICY_NOT_FOUND", None),
    ):
        items = calculate_deposit_compliance([LIABILITY], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", evidence_version_id="EV-1", policies=[policy])
        db = SimpleNamespace(
            tds_compliance_deposit_results=_Collection(),
            tds_compliance_deposit_relationships=_Collection(),
            tds_compliance_deposit_summaries=_Collection(),
            tds_compliance_deposit_runs=_Collection(),
            tds_compliance_deposit_reviews=_Collection(),
            tds_compliance_deposit_evidence_rows=_Collection(),
        )
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "_deposit_compliance_preview", lambda *_: (assignment, calculation, "EV-1", items))
        monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

        created = server.run_deposit_compliance("A-1", server.DepositEvidenceBody(calculation_id="CALC-1", evidence_version_id="EV-1"), None)
        restored = server.deposit_compliance_detail("A-1", created["deposit_run"]["deposit_run_id"], None)["items"][0]

        assert restored.get("reason_code") == expected_reason
        assert restored["overall_status"] == items[0]["overall_status"]
        assert (restored["phase5_rule_snapshot"] or {}).get("policy_id") == expected_snapshot
        assert (created["deposit_run"]["phase5_rule_snapshot"] or {}).get("policy_id") == expected_snapshot


def test_deposit_run_api_persists_rejected_policy_without_snapshot(monkeypatch):
    assignment = {"assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1", "status": "DRAFT"}
    calculation = {"ledger_version_id": "LEDGER-1"}
    incomplete = {key: value for key, value in POLICY.items() if key not in {"active", "status"}}
    items = calculate_deposit_compliance([LIABILITY], [EVIDENCE], assignment_id="A-1", calculation_id="CALC-1", evidence_version_id="EV-1", policies=[incomplete])
    db = SimpleNamespace(
        tds_compliance_deposit_results=_Collection(),
        tds_compliance_deposit_relationships=_Collection(),
        tds_compliance_deposit_summaries=_Collection(),
        tds_compliance_deposit_runs=_Collection(),
        tds_compliance_deposit_reviews=_Collection(),
        tds_compliance_deposit_evidence_rows=_Collection(),
    )
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_deposit_compliance_preview", lambda *_: (assignment, calculation, "EV-1", items))
    monkeypatch.setattr(server, "_audit_tds_compliance", lambda *args, **kwargs: None)

    app = FastAPI()
    app.add_api_route("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/run", server.run_deposit_compliance, methods=["POST"])
    app.add_api_route("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/{deposit_run_id}", server.deposit_compliance_detail, methods=["GET"])
    app.dependency_overrides[server._phase5_access] = lambda: SimpleNamespace(user_id="TEST")

    with TestClient(app) as client:
        created = client.post("/api/tds-compliance/assignments/A-1/deposit-compliance/run", json={"calculation_id": "CALC-1", "evidence_version_id": "EV-1"})
        assert created.status_code == 200, created.text
        run_id = created.json()["deposit_run"]["deposit_run_id"]
        detail = client.get(f"/api/tds-compliance/assignments/A-1/deposit-compliance/{run_id}")

    assert detail.status_code == 200, detail.text
    assert detail.json()["items"][0]["reason_code"] == "PHASE5_POLICY_NOT_FOUND"
    assert detail.json()["items"][0]["phase5_rule_snapshot"] is None
    assert detail.json()["deposit_run"]["phase5_rule_snapshot"] is None


def test_deposit_preview_excludes_calculation_results_from_another_ledger_version(monkeypatch):
    assignment = {**ASSIGNMENT, "status": "DRAFT"}
    calculation = {
        "assignment_id": "A-1",
        "calculation_id": "CALC-CURRENT",
        "ledger_version_id": "LEDGER-CURRENT",
    }
    current = {**LIABILITY, "assignment_id": "A-1", "calculation_id": "CALC-CURRENT", "ledger_version_id": "LEDGER-CURRENT"}
    stale = {
        **LIABILITY,
        "assignment_id": "A-1",
        "transaction_id": "PAY-STALE",
        "calculation_id": "CALC-CURRENT",
        "ledger_version_id": "LEDGER-STALE",
    }
    db = SimpleNamespace(
        tds_compliance_calculation_runs=_Collection(),
        tds_compliance_deposit_evidence_versions=_Collection(),
        tds_compliance_interest_runs=_Collection(),
        tds_compliance_deposit_evidence_rows=_Collection(),
        tds_compliance_calculation_results=_Collection(),
        tds_compliance_ledger_rows=_Collection(),
        tds_compliance_interest_results=_Collection(),
        tds_compliance_phase5_policies=_PolicyCollection(),
        tds_compliance_rules=_PolicyCollection(),
        tds_compliance_deposit_relationships=_Collection(),
    )
    db.tds_compliance_calculation_runs.insert_one(calculation)
    db.tds_compliance_calculation_results.insert_many([current, stale])
    db.tds_compliance_ledger_rows.insert_many([
        {"assignment_id": "A-1", "ledger_version_id": "LEDGER-CURRENT", "transaction_id": "PAY-1", "deduction_date": "2025-06-01", "deductee_type": "INDIVIDUAL_HUF"},
        {"assignment_id": "A-1", "ledger_version_id": "LEDGER-STALE", "transaction_id": "PAY-STALE", "deduction_date": "2025-05-01", "deductee_type": "INDIVIDUAL_HUF"},
    ])
    db.tds_compliance_phase5_policies.insert_one(POLICY)
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_tds_assignment_or_404", lambda _: assignment)

    _, restored_calculation, _, items = server._deposit_compliance_preview(
        "A-1", server.DepositEvidenceBody(calculation_id="CALC-CURRENT")
    )

    assert restored_calculation["ledger_version_id"] == "LEDGER-CURRENT"
    assert [item["transaction_id"] for item in items] == ["PAY-1"]


def test_evidence_validation_checks_scope_and_duplicates_without_zero_fill():
    csv = b"transaction_id,challan_number,tds_amount,deposit_date,financial_year,quarter,tan\nPAY-1,CH-1,100,05-06-2025,2025-26,Q2,ABCD12345E\nPAY-1,CH-1,100,05-06-2025,2025-26,Q2,ABCD12345E\n"
    report = validate_deposit_evidence("challan.csv", csv, ASSIGNMENT)
    assert report["summary"]["duplicate_rows"] == 1
    assert report["rows"][1]["validation_status"] == "REVIEW_REQUIRED"
    mismatch = validate_deposit_evidence("challan.csv", b"transaction_id,tds_amount,financial_year,tan\nPAY-1,100,2026-27,ZZZZ99999Z\n", ASSIGNMENT)
    assert mismatch["rows"][0]["validation_status"] == "REVIEW_REQUIRED"
    assert mismatch["rows"][0]["tds_amount"] == 100.0


def test_immutable_input_and_assignment_isolation_are_preserved():
    foreign = {**LIABILITY, "transaction_id": "PAY-2", "expected_tds": 999.0}
    snapshot, source = deepcopy(LIABILITY), deepcopy(EVIDENCE)
    result = control(liabilities=[snapshot], evidence=[source])
    snapshot["expected_tds"] = 999.0
    source["tds_amount"] = 999.0
    assert result["expected_tds"] == 100.0
    assert result["deposited_tds"] == 100.0
    assert foreign["transaction_id"] not in str(result)
