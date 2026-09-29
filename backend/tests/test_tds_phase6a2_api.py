"""Exercise the actual Return Audit routes with an isolated in-memory store.

Extract route definitions to avoid server.py's import-time production Mongo setup.
No live database or authentication configuration is changed by these tests.
"""

import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from auth_boundary import Principal, authorize
from engine.tds_compliance.return_reconciliation import reconcile
from tests.test_tds_phase6a2_reconciliation import fixture
from engine.tds_compliance.return_audit import parse_return_artifact


class Cursor(list):
    def sort(self, key, direction):
        return Cursor(
            sorted(self, key=lambda r: str(r.get(key) or ""), reverse=direction < 0)
        )


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))

    def find(self, query, projection=None):
        return Cursor(
            deepcopy(
                [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]
            )
        )

    def find_one(self, query, projection=None, sort=None):
        rows = self.find(query)
        for key, direction in sort or []:
            rows = rows.sort(key, direction)
        return rows[0] if rows else None

    def insert_one(self, row):
        self.rows.append(deepcopy(row))

    def insert_many(self, rows):
        self.rows.extend(deepcopy(rows))


@pytest.fixture
def api(tmp_path, request):
    filename = getattr(request, "param", "27QQ3.zip")
    args = fixture(filename)
    parsed = parse_return_artifact(
        (Path(__file__).parent / "fixtures" / "official_return_samples" / filename).read_bytes(),
        filename,
    )
    for row in args["ledger_rows"]:
        row.update(
            financial_year=parsed["metadata"]["normalized_financial_year"],
            quarter=parsed["metadata"]["quarter"],
        )
    db = SimpleNamespace()
    data = {
        "assignments": [
            {
                "assignment_id": "A",
                "workflow": "TDS_COMPLIANCE",
                "organization_id": "O",
                "client_id": "CLIENT",
                "financial_year": parsed["metadata"]["normalized_financial_year"],
                "quarter": parsed["metadata"]["quarter"],
            }
        ],
        "return_artifact_versions": [
            {
                "artifact_id": "R",
                "parser_status": "VALID",
                "sha256": "hash",
                "return_form": parsed["source_form"],
                "statement_type": "REGULAR",
            }
        ],
        "calculation_runs": [
            {"calculation_id": "C1", "ledger_version_id": "L1", "created_at": "1"}
        ],
        "ledger_versions": [{"ledger_version_id": "L1", "version": 1}],
        "ledger_rows": args["ledger_rows"],
        "calculation_results": args["calculation_rows"],
        "deposit_runs": [
            {
                "deposit_run_id": "D1",
                "calculation_id": "C1",
                "ledger_version_id": "L1",
                "created_at": "1",
            }
        ],
        "deposit_results": args["deposit_rows"],
        "interest_runs": [
            {
                "interest_run_id": "I1",
                "deposit_run_id": "D1",
                "calculation_id": "C1",
                "ledger_version_id": "L1",
                "created_at": "1",
            }
        ],
        "interest_results": args["interest_rows"],
        "return_rows": [{**r, "artifact_id": "R"} for r in args["return_rows"]],
        "return_challans": [{**r, "artifact_id": "R"} for r in args["challans"]],
        "return_records": [],
        "return_filing_evidence": [],
        "return_audit_runs": [],
        "return_audit_results": [],
        "return_exceptions": [],
        "return_audit_review_decisions": [],
    }
    for name, rows in data.items():
        setattr(
            db,
            "tds_compliance_" + name,
            Collection([{**r, "assignment_id": "A"} for r in rows]),
        )
    principal = [Principal("U", "O", frozenset({"ADMIN"}), frozenset({"CLIENT"}))]
    names = {
        "ReturnAuditRunBody",
        "ReturnAuditReviewDecisionBody",
        "_tds_assignment_or_404",
        "_tds_workspace_access",
        "_return_audit_access",
        "_return_audit_artifact_or_404",
        "_return_audit_result_or_404",
        "upload_return_audit_artifact",
        "list_return_audit_artifacts",
        "run_return_audit",
        "return_audit_summary",
        "list_return_audit_results",
        "get_return_audit_result",
        "list_return_audit_exceptions",
        "list_return_audit_review_decisions",
        "create_return_audit_review_decision",
    }
    tree = ast.parse(
        (Path(__file__).parents[1] / "server.py").read_text(encoding="utf-8")
    )
    module = ast.Module(
        body=[
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names
        ],
        type_ignores=[],
    )
    app = FastAPI()
    scope = dict(
        app=app,
        db=db,
        Principal=Principal,
        authorize=authorize,
        HTTPException=HTTPException,
        Depends=Depends,
        File=File,
        Form=Form,
        UploadFile=UploadFile,
        BaseModel=BaseModel,
        ConfigDict=ConfigDict,
        NO_ID={"_id": 0},
        DESCENDING=-1,
        uuid=uuid,
        TDS_COMPLIANCE_WORKFLOW="TDS_COMPLIANCE",
        RETURN_AUDIT_SCHEMA_VERSION="6A.1",
        RETURN_AUDIT_ARTIFACT_TYPES={"RETURN_SOURCE", "FVU", "RETURN_PACKAGE", "FILING_EVIDENCE", "FORM_27A"},
        RETURN_RECONCILIATION_SCHEMA_VERSION="6A.2",
        MAX_UPLOAD_BYTES=25 * 1024 * 1024,
        UPLOAD_DIR=tmp_path,
        Path=Path,
        sha256=__import__("hashlib").sha256,
        parse_return_artifact=__import__("engine.tds_compliance.return_audit", fromlist=["parse_return_artifact"]).parse_return_artifact,
        reconcile_return_evidence=reconcile,
        _tds_compliance_security_boundary=lambda: principal[0],
        now_iso=lambda: datetime.now(timezone.utc).isoformat(),
        _audit_tds_compliance=lambda *a, **kw: None,
    )
    exec(compile(module, "return_audit_routes", "exec"), scope)
    return TestClient(app), db, principal


BASE = "/api/tds-compliance/assignments/A/return-audit"


def test_run_persists_full_evidence_and_never_updates_snapshots(api):
    client, db, _ = api
    before = deepcopy(db.tds_compliance_calculation_results.rows)
    response = client.post(
        BASE + "/run", json={"artifact_id": "R", "calculation_id": "C1"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["audit_run"]["summary"]["matched"] == 2
    for row in body["items"]:
        for key in (
            "assignment_id",
            "organization_id",
            "client_id",
            "financial_year",
            "quarter",
            "return_artifact_id",
            "return_form",
            "statement_type",
            "return_row_id",
            "payment_transaction_id",
            "payment_ledger_version_id",
            "calculation_result_id",
            "deposit_evidence_id",
            "interest_result_id",
            "identity_status",
            "payment_status",
            "tax_status",
            "challan_status",
            "interest_status",
            "overall_status",
            "source_references",
            "created_at",
            "audit_run_id",
            "schema_version",
        ):
            assert key in row
    assert db.tds_compliance_calculation_results.rows == before
    assert (
        client.get(
            BASE + "/summary",
            params={"audit_run_id": body["audit_run"]["audit_run_id"]},
        ).json()["latest_run"]["audit_run_id"]
        == body["audit_run"]["audit_run_id"]
    )


def test_missing_phase2_persists_review_and_explicit_unknown_snapshot_is_rejected(api):
    client, db, _ = api
    db.tds_compliance_calculation_runs.rows.clear()
    response = client.post(BASE + "/run", json={"artifact_id": "R"})
    assert response.status_code == 201, response.text
    assert response.json()["audit_run"]["overall_status"] == "REVIEW_REQUIRED"
    assert (
        client.post(
            BASE + "/run", json={"artifact_id": "R", "calculation_id": "OTHER"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "principal",
    [
        Principal("U", "OTHER", frozenset({"ADMIN"})),
        Principal("U", "O", frozenset({"ADMIN"}), frozenset({"OTHER"})),
    ],
)
def test_cross_tenant_and_client_access_denied(api, principal):
    client, db, selected = api
    selected[0] = principal
    for path in ("/summary", "/results", "/exceptions"):
        assert client.get(BASE + path).status_code == 403
    assert client.post(BASE + "/run", json={"artifact_id": "R"}).status_code == 403
    assert not db.tds_compliance_return_audit_runs.rows


def test_viewer_cannot_run_and_locked_assignment_cannot_be_changed(api):
    client, db, selected = api
    selected[0] = Principal("U", "O", frozenset({"VIEWER"}))
    assert client.get(BASE + "/results").status_code == 200
    assert client.post(BASE + "/run", json={"artifact_id": "R"}).status_code == 403
    selected[0] = Principal("U", "O", frozenset({"ADMIN"}))
    db.tds_compliance_assignments.rows[0]["status"] = "LOCKED"
    assert client.post(BASE + "/run", json={"artifact_id": "R"}).status_code == 409


def test_artifact_and_calculation_from_other_assignment_are_not_selected(api):
    client, db, _ = api
    db.tds_compliance_return_artifact_versions.rows[0]["assignment_id"] = "OTHER"
    assert client.post(BASE + "/run", json={"artifact_id": "R"}).status_code == 404
    db.tds_compliance_return_artifact_versions.rows[0]["assignment_id"] = "A"
    db.tds_compliance_calculation_runs.rows[0]["assignment_id"] = "OTHER"
    assert (
        client.post(
            BASE + "/run", json={"artifact_id": "R", "calculation_id": "C1"}
        ).status_code
        == 404
    )


def test_corrections_append_and_prior_run_is_still_readable(api):
    client, db, _ = api
    first = client.post(BASE + "/run", json={"artifact_id": "R"}).json()
    original = deepcopy(db.tds_compliance_return_audit_results.rows)
    db.tds_compliance_return_artifact_versions.insert_one(
        {
            **db.tds_compliance_return_artifact_versions.rows[0],
            "artifact_id": "COR",
            "statement_type": "CORRECTION",
        }
    )
    db.tds_compliance_return_rows.insert_one(
        {**db.tds_compliance_return_rows.rows[0], "artifact_id": "COR"}
    )
    second = client.post(BASE + "/run", json={"artifact_id": "COR"})
    assert second.status_code == 201
    assert second.json()["audit_run"]["summary"]["missing_in_return"] == 0
    assert db.tds_compliance_return_audit_results.rows[: len(original)] == original
    history = client.get(
        BASE + "/results", params={"audit_run_id": first["audit_run"]["audit_run_id"]}
    ).json()
    assert len(history["items"]) == 2


def test_unrelated_deposit_and_interest_runs_are_never_used(api):
    client, db, _ = api
    db.tds_compliance_deposit_runs.rows[0]["calculation_id"] = "OTHER"
    response = client.post(BASE + "/run", json={"artifact_id": "R"}).json()
    assert all(
        r["challan_status"] == "MISSING_IN_DEPOSIT_EVIDENCE"
        and not r["interest_evidence_available"]
        for r in response["items"]
    )


def test_review_decision_is_append_only_and_keeps_original_result(api):
    client, db, _ = api
    audit = client.post(BASE + "/run", json={"artifact_id": "R"}).json()
    result = audit["items"][0]
    before = deepcopy(result)
    response = client.post(
        BASE + f"/results/{result['result_id']}/review-decisions",
        json={"decision": "KEEP_REVIEW", "reason": "CA needs the original challan copy."},
    )
    assert response.status_code == 201, response.text
    decision = response.json()
    assert decision["audit_result_id"] == result["result_id"]
    assert decision["source_references"] == result["source_references"]
    assert db.tds_compliance_return_audit_results.rows[0] == before
    listed = client.get(
        BASE + "/review-decisions",
        params={"audit_run_id": audit["audit_run"]["audit_run_id"]},
    ).json()["items"]
    assert listed[0]["decision"] == "KEEP_REVIEW"


def test_review_decision_is_scoped_validated_and_locked(api):
    client, db, selected = api
    audit = client.post(BASE + "/run", json={"artifact_id": "R"}).json()
    result_id = audit["items"][0]["result_id"]
    assert client.post(BASE + f"/results/{result_id}/review-decisions", json={"decision": "INVALID", "reason": "x"}).status_code == 422
    assert client.post(BASE + f"/results/{result_id}/review-decisions", json={"decision": "CONFIRM", "reason": ""}).status_code == 422
    selected[0] = Principal("U", "O", frozenset({"VIEWER"}))
    assert client.post(BASE + f"/results/{result_id}/review-decisions", json={"decision": "CONFIRM", "reason": "x"}).status_code == 403
    selected[0] = Principal("U", "O", frozenset({"ADMIN"}))
    db.tds_compliance_assignments.rows[0]["status"] = "LOCKED"
    assert client.post(BASE + f"/results/{result_id}/review-decisions", json={"decision": "CONFIRM", "reason": "x"}).status_code == 409


@pytest.mark.parametrize(
    ("api", "filename"),
    [("26QQ1.zip", "26QQ1.zip"), ("27QQ3.zip", "27QQ3.zip"), ("140RQ1.txt", "140RQ1.txt"), ("144RQ1.txt", "144RQ1.txt")],
    indirect=["api"],
)
def test_official_return_upload_then_audit_retrieval_chain(api, filename):
    client, _, _ = api
    source = (Path(__file__).parent / "fixtures" / "official_return_samples" / filename).read_bytes()
    uploaded = client.post(BASE + "/upload", files={"file": (filename, source, "application/zip" if filename.endswith(".zip") else "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    assert artifact["parser_status"] == "VALID"
    assert artifact["return_row_count"] > 0
    listed = client.get(BASE + "/artifacts").json()["items"]
    assert any(item["artifact_id"] == artifact["artifact_id"] for item in listed)
    audit = client.post(BASE + "/run", json={"artifact_id": artifact["artifact_id"]}).json()
    assert audit["audit_run"]["artifact_id"] == artifact["artifact_id"]
    assert client.get(BASE + "/summary", params={"audit_run_id": audit["audit_run"]["audit_run_id"]}).status_code == 200
    assert client.get(BASE + "/results", params={"audit_run_id": audit["audit_run"]["audit_run_id"]}).status_code == 200
    assert client.get(BASE + "/exceptions", params={"audit_run_id": audit["audit_run"]["audit_run_id"]}).status_code == 200
