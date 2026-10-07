import logging
import os
import re
import threading
import uuid
from time import perf_counter
from hashlib import sha256
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from bson.decimal128 import Decimal128
from dotenv import load_dotenv
from fastapi import Depends, Request, BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from starlette.middleware.cors import CORSMiddleware

from engine.reconciliation.classifier import CATEGORY
from engine.shared.ai_assistant import AIConfigurationError, RESULT_FIELDS, build_run_context, focused_context, get_provider, safe_filters, validate_question
from engine.reconciliation.matcher import MATCH_METHODS, RESULT_TYPES
from engine.reconciliation.normalizer import core_name
from engine.reconciliation.validation_jobs import ValidationJobs
from engine.reconciliation.source_processing_jobs import SourceProcessingJobs
from engine.shared.parser import SUPPORTED_EXTENSIONS
from engine.reconciliation.pipeline import DEFAULT_SETTINGS, STEPS, PipelineError, inspect_upload_source, load_and_validate, now_iso, run_26as_analysis, run_pipeline, run_sales_tds_26as_analysis
from engine.reconciliation.reports import FORMATS, REPORTS, build_report, render
from engine.shared.sample_data import SAMPLE_ASSESSEE, sample_files
from engine.shared.schema import FILE_KINDS, FILE_LABELS, PAYMENT_LEDGER, TDS_DEPOSIT_EVIDENCE, SCHEMAS, TEMPLATE_ROWS, schema_description
from engine.reconciliation.tds_rules import RuleError, validate_rule
from engine.reconciliation.sales_tds_workflow import _transaction_reconciliation
from engine.tds_compliance import ASSIGNMENT_STATUSES, DEDUCTEE_MASTER_SCHEMA, PAYMENT_LEDGER_SCHEMA, PAYER_CATEGORIES, RECIPIENT_CATEGORIES, RECIPIENT_RESIDENCIES, WORKFLOW as TDS_COMPLIANCE_WORKFLOW, calculate_deposit_compliance, calculate_interest_compliance, calculate_interest_from_deposit_results, calculate_ledger_transactions, calculate_tds_calculator, freeze_calculation_results, validate_deposit_evidence, validate_payment_ledger
from engine.tds_compliance.government_evidence import SOURCE_TYPE as GOVERNMENT_SUMMARY_SOURCE_TYPE, validate_government_tax_credit_summary
from engine.tds_compliance.government_verification import verify_government_summary
from engine.tds_compliance.return_audit import ARTIFACT_TYPES as RETURN_AUDIT_ARTIFACT_TYPES, parse_return_artifact, reconcile_return_rows, RETURN_AUDIT_SCHEMA_VERSION
from engine.tds_compliance.return_reconciliation import reconcile as reconcile_return_evidence, SCHEMA_VERSION as RETURN_RECONCILIATION_SCHEMA_VERSION
from engine.tds_compliance.source_traceability import governed_activation_errors, source_traceability_errors, source_traceability_snapshot
from auth_boundary import AuthenticationProviderConfigurationRequired, NonProductionAdapterDisabled, OIDCConfiguration, Principal, authorize, build_development_principal
from cors_config import CORS_METHODS, allowed_frontend_origins

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

mongo_url = os.environ.get("MONGO_URL")
database_name = os.environ.get("DB_NAME")
if not mongo_url or not database_name:
    raise RuntimeError(
        "Backend configuration is incomplete. Set MONGO_URL and DB_NAME in backend/.env before starting the server."
    )
client = MongoClient(mongo_url)
db = client[database_name]
for coll, keys in (("results", [("run_id", ASCENDING), ("seq", ASCENDING)]), ("uploads", [("upload_id", ASCENDING)]), ("runs", [("run_id", ASCENDING)]), ("identity_mappings", [("assessee_pan", ASCENDING), ("tan", ASCENDING)]), ("ai_contexts", [("run_id", ASCENDING), ("version", ASCENDING)]), ("ai_conversations", [("conversation_id", ASCENDING), ("run_id", ASCENDING)])):
    db[coll].create_index(keys)
db.reconciliation_relationship_commentaries.create_index([("commentary_id", ASCENDING)], unique=True)
db.reconciliation_relationship_commentaries.create_index([("run_id", ASCENDING), ("relationship_id", ASCENDING), ("version", ASCENDING)], unique=True)
db.reconciliation_relationship_commentaries.create_index([("run_id", ASCENDING), ("relationship_id", ASCENDING), ("is_current", ASCENDING)])
db.reconciliation_relationship_commentary_events.create_index([("run_id", ASCENDING), ("relationship_id", ASCENDING), ("created_at", ASCENDING)])
db.tds_rules.create_index([("rule_id", ASCENDING)], unique=True)
db.tds_compliance_assignments.create_index([("assignment_id", ASCENDING)], unique=True)
db.tds_compliance_assignments.create_index([("organization_id", ASCENDING), ("client_id", ASCENDING), ("financial_year", ASCENDING)])
db.tds_compliance_rules.create_index([("rule_id", ASCENDING)], unique=True)
db.tds_compliance_rule_history.create_index([("rule_id", ASCENDING), ("revision", ASCENDING)], unique=True)
db.tds_compliance_classification_mappings.create_index([("mapping_id", ASCENDING)], unique=True)
db.tds_compliance_classification_mappings.create_index([("organization_id", ASCENDING), ("client_id", ASCENDING), ("active", ASCENDING)])
db.tds_compliance_transaction_classifications.create_index([("assignment_id", ASCENDING), ("ledger_version_id", ASCENDING), ("transaction_id", ASCENDING)], unique=True)
db.tds_compliance_transaction_reviews.create_index([("assignment_id", ASCENDING), ("ledger_version_id", ASCENDING), ("transaction_id", ASCENDING)], unique=True)
db.tds_compliance_audit_events.create_index([("assignment_id", ASCENDING), ("timestamp", ASCENDING)])
db.tds_compliance_ledger_versions.create_index([("ledger_version_id", ASCENDING)], unique=True)
db.tds_compliance_ledger_versions.create_index([("assignment_id", ASCENDING), ("version", DESCENDING)])
db.tds_compliance_ledger_rows.create_index([("assignment_id", ASCENDING), ("ledger_version_id", ASCENDING), ("source_row_number", ASCENDING)])
db.tds_compliance_calculation_runs.create_index([("calculation_id", ASCENDING)], unique=True)
db.tds_compliance_calculation_runs.create_index([("assignment_id", ASCENDING), ("created_at", DESCENDING)])
db.tds_compliance_calculation_results.create_index([("assignment_id", ASCENDING), ("calculation_id", ASCENDING), ("transaction_id", ASCENDING)])
db.tds_compliance_interest_runs.create_index([("interest_run_id", ASCENDING)], unique=True)
db.tds_compliance_interest_runs.create_index([("assignment_id", ASCENDING), ("created_at", DESCENDING)])
db.tds_compliance_interest_results.create_index([("assignment_id", ASCENDING), ("interest_run_id", ASCENDING), ("transaction_id", ASCENDING)])
db.tds_compliance_interest_reviews.create_index([("assignment_id", ASCENDING), ("interest_run_id", ASCENDING), ("transaction_id", ASCENDING)], unique=True)
db.tds_compliance_deposit_evidence_versions.create_index([("evidence_version_id", ASCENDING)], unique=True)
db.tds_compliance_deposit_evidence_rows.create_index([("assignment_id", ASCENDING), ("evidence_version_id", ASCENDING), ("source_row_number", ASCENDING)])
db.tds_compliance_government_evidence_versions.create_index([("evidence_version_id", ASCENDING)], unique=True)
db.tds_compliance_government_evidence_versions.create_index([("assignment_id", ASCENDING), ("version_number", ASCENDING)], unique=True)
db.tds_compliance_government_evidence_versions.create_index([("assignment_id", ASCENDING), ("financial_year", ASCENDING)])
db.tds_compliance_government_evidence_rows.create_index([("assignment_id", ASCENDING), ("evidence_version_id", ASCENDING), ("source_row_number", ASCENDING)])
db.tds_compliance_government_evidence_verifications.create_index([("verification_id", ASCENDING)], unique=True)
db.tds_compliance_government_evidence_verifications.create_index([("assignment_id", ASCENDING), ("version_number", ASCENDING)], unique=True)
db.tds_compliance_government_evidence_verifications.create_index([("assignment_id", ASCENDING), ("government_evidence_version_id", ASCENDING)])
db.tds_compliance_government_evidence_reviews.create_index([("assignment_id", ASCENDING), ("verification_id", ASCENDING)], unique=True)
db.tds_compliance_deposit_summaries.create_index([("deposit_run_id", ASCENDING)], unique=True)
db.tds_compliance_deposit_runs.create_index([("deposit_run_id", ASCENDING)], unique=True)
db.tds_compliance_deposit_results.create_index([("assignment_id", ASCENDING), ("deposit_run_id", ASCENDING), ("transaction_id", ASCENDING)])
db.tds_compliance_deposit_reviews.create_index([("assignment_id", ASCENDING), ("deposit_run_id", ASCENDING), ("transaction_id", ASCENDING)], unique=True)
db.tds_compliance_phase5_policies.create_index([("policy_id", ASCENDING), ("policy_version", ASCENDING)], unique=True)
db.tds_compliance_deposit_relationships.create_index([("assignment_id", ASCENDING), ("deposit_run_id", ASCENDING), ("relationship_id", ASCENDING)])
db.tds_compliance_return_artifact_versions.create_index([("artifact_id", ASCENDING)], unique=True)
db.tds_compliance_return_artifact_versions.create_index([("assignment_id", ASCENDING), ("uploaded_at", DESCENDING)])
db.tds_compliance_return_rows.create_index([("assignment_id", ASCENDING), ("artifact_id", ASCENDING), ("source_row_number", ASCENDING)])
db.tds_compliance_return_records.create_index([("assignment_id", ASCENDING), ("artifact_id", ASCENDING), ("source_line_number", ASCENDING)])
db.tds_compliance_return_audit_results.create_index([("assignment_id", ASCENDING), ("audit_run_id", ASCENDING), ("result_id", ASCENDING)], unique=True)
db.tds_compliance_return_audit_runs.create_index([("audit_run_id", ASCENDING)], unique=True)
db.tds_compliance_return_exceptions.create_index([("assignment_id", ASCENDING), ("audit_run_id", ASCENDING)])
db.tds_compliance_return_audit_review_decisions.create_index([("decision_id", ASCENDING)], unique=True)
db.tds_compliance_return_audit_review_decisions.create_index([("assignment_id", ASCENDING), ("audit_result_id", ASCENDING), ("created_at", ASCENDING)])
db.tds_calculator_runs.create_index([("calculator_run_id", ASCENDING)], unique=True)
db.tds_calculator_runs.create_index([("assignment_id", ASCENDING), ("created_at", DESCENDING)])

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("26as")
app = FastAPI(title="26AS Reconciliation API")
NO_ID = {"_id": 0}


def _tds_mongo_snapshot(value):
    """Copy TDS evidence into MongoDB-safe exact numeric values.

    TDS engines retain ``Decimal`` during calculation. Persisted snapshots use
    BSON Decimal128, preserving financial precision without mutating the
    in-memory API result.
    """
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, dict):
        return {key: _tds_mongo_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_tds_mongo_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_tds_mongo_snapshot(item) for item in value)
    return value


def _tds_api_value(value):
    """Expose BSON Decimal128 snapshots through the existing Decimal API form."""
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, dict):
        return {key: _tds_api_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_tds_api_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_tds_api_value(item) for item in value)
    return value
CLIENT_ERROR = "Reconciliation service could not process the request."


def strip(doc):
    if doc is not None:
        doc.pop("_id", None)
    return doc


class Store:
    def __init__(self, assessee_pan: str):
        self.pan = assessee_pan or ""

    def saved_mappings(self):
        return {m["tan"]: m for m in db.identity_mappings.find({"assessee_pan": self.pan}, NO_ID)}

    def saved_aliases(self):
        return list(db.identity_aliases.find({"assessee_pan": self.pan}, NO_ID))

    def decisions(self):
        return {d["tan"]: d for d in db.identity_decisions.find({"assessee_pan": self.pan}, NO_ID)}


def get_settings():
    doc = db.settings.find_one({"key": "reconciliation"}, NO_ID) or {}
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in doc.items() if k in DEFAULT_SETTINGS}}
    settings["tds_rules"] = list(db.tds_rules.find({"is_active": True}, NO_ID))
    return settings


def _tds_compliance_security_boundary():
    """Fail closed until a real OIDC verifier is configured and wired.

    The existing app has no production token verifier. The new workflow must
    not replace one with browser names, sessionStorage, or caller headers.
    """
    try:
        return build_development_principal()
    except NonProductionAdapterDisabled:
        pass
    try:
        OIDCConfiguration.from_environment()
    except AuthenticationProviderConfigurationRequired as exc:
        raise HTTPException(503, str(exc)) from exc
    raise HTTPException(501, "TDS Compliance API is awaiting the configured OIDC token-verification adapter; no unverified request identity is accepted.")


def _audit_tds_compliance(action: str, assignment: dict, *, entity_id: str | None = None, old_value=None, new_value=None, actor_id: str | None = None):
    """Append-only audit-event persistence; no update/delete path exists."""
    db.tds_compliance_audit_events.insert_one({"event_id": uuid.uuid4().hex, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment.get("assignment_id"), "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "user_id": actor_id, "action": action, "entity_type": "TDS_AUDIT_ASSIGNMENT", "entity_id": entity_id or assignment.get("assignment_id"), "old_value": old_value, "new_value": new_value, "timestamp": now_iso(), "source": "API", "reason": None})


class ReturnAuditRunBody(BaseModel):
    artifact_id: str
    calculation_id: str | None = None


class ReturnAuditReviewDecisionBody(BaseModel):
    """Append-only CA review decision; it never changes parsed or audit evidence."""

    model_config = ConfigDict(extra="forbid")
    decision: str
    reason: str


def _return_audit_access(assignment_id: str, principal: Principal, permission: str = "view_results") -> dict:
    assignment = _tds_assignment_or_404(assignment_id)
    _tds_workspace_access(principal, assignment)
    try:
        authorize(principal, permission, organization_id=assignment["organization_id"], client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return assignment


def _return_audit_artifact_or_404(assignment_id: str, artifact_id: str) -> dict:
    artifact = db.tds_compliance_return_artifact_versions.find_one({"assignment_id": assignment_id, "artifact_id": artifact_id}, NO_ID)
    if not artifact:
        raise HTTPException(404, "Return Audit artifact not found for this assignment.")
    return artifact


def _return_audit_result_or_404(assignment_id: str, result_id: str) -> dict:
    result = db.tds_compliance_return_audit_results.find_one({"assignment_id": assignment_id, "result_id": result_id}, NO_ID)
    if not result:
        raise HTTPException(404, "Return Audit result not found for this assignment.")
    return result


@app.post("/api/tds-compliance/assignments/{assignment_id}/return-audit/upload", status_code=201)
async def upload_return_audit_artifact(assignment_id: str, file: UploadFile = File(...), artifact_type: str | None = Form(None), principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _return_audit_access(assignment_id, principal, "upload_source")
    if assignment.get("status") == "LOCKED": raise HTTPException(409, "This assignment is locked; return evidence cannot be added.")
    declared = (artifact_type or "").upper() or None
    if declared and declared not in RETURN_AUDIT_ARTIFACT_TYPES: raise HTTPException(422, "Unsupported return artifact type.")
    artifact_id = f"TDRA-{uuid.uuid4().hex[:14].upper()}"
    suffix = Path(file.filename or "source.bin").suffix.lower() or ".bin"
    path = UPLOAD_DIR / f"{artifact_id}{suffix}"
    size = 0; digest = sha256()
    with path.open("wb") as destination:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                destination.close(); path.unlink(missing_ok=True); raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
            digest.update(chunk); destination.write(chunk)
    if not size: path.unlink(missing_ok=True); raise HTTPException(400, "The uploaded file is empty.")
    report = parse_return_artifact(path.read_bytes(), file.filename or path.name, declared)
    source_metadata = report.get("metadata") or {}
    source_fy = source_metadata.get("normalized_financial_year")
    source_quarter = source_metadata.get("quarter")
    conflicts = []
    if source_fy and assignment.get("financial_year") and source_fy != assignment.get("financial_year"): conflicts.append("Source financial year differs from the selected assignment.")
    if source_quarter and assignment.get("quarter") and source_quarter != assignment.get("quarter"): conflicts.append("Source quarter differs from the selected assignment.")
    if conflicts and report["parser_status"] == "VALID":
        report["parser_status"] = "REVIEW_REQUIRED"; report["parser_message"] = " ".join(conflicts)
    doc = {"artifact_id": artifact_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "schema_version": RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "financial_year": assignment.get("financial_year"), "quarter": assignment.get("quarter"), "return_form": report.get("source_form"), "statement_type": report.get("statement_type"), "source_metadata": source_metadata, "source_fy_or_tax_year": source_metadata.get("source_fy_or_tax_year"), "normalized_financial_year": source_fy, "source_quarter": source_quarter, "artifact_type": report["artifact_type"], "filename": file.filename, "mime_type": file.content_type, "extension": suffix, "size": size, "sha256": digest.hexdigest(), "uploaded_at": now_iso(), "uploaded_by": principal.user_id, "parser_status": report["parser_status"], "parser_message": report["parser_message"], "source_version": report.get("source_version"), "archive_entries": report.get("archive_entries", [])}
    db.tds_compliance_return_artifact_versions.insert_one(dict(doc))
    if report.get("records"): db.tds_compliance_return_records.insert_many([{**record, "artifact_id": artifact_id, "source_artifact_id": artifact_id, "source_hash": doc["sha256"], "source_filename": doc["filename"], "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "workflow": TDS_COMPLIANCE_WORKFLOW} for record in report["records"]])
    if report["return_rows"]: db.tds_compliance_return_rows.insert_many([{**row, "artifact_id": artifact_id, "source_artifact_id": artifact_id, "source_hash": doc["sha256"], "source_filename": doc["filename"], "workflow": TDS_COMPLIANCE_WORKFLOW, "schema_version": RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "source_artifact_sha256": doc["sha256"], "parsed_at": now_iso()} for row in report["return_rows"]])
    if report["challans"]: db.tds_compliance_return_challans.insert_many([{**row, "artifact_id": artifact_id, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "source_artifact_sha256": doc["sha256"]} for row in report["challans"]])
    if report["filing_evidence"]: db.tds_compliance_return_filing_evidence.insert_many([{**row, "artifact_id": artifact_id, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "source_artifact_sha256": doc["sha256"]} for row in report["filing_evidence"]])
    _audit_tds_compliance("RETURN_AUDIT_ARTIFACT_UPLOADED", assignment, entity_id=artifact_id, new_value={"artifact_type": doc["artifact_type"], "sha256": doc["sha256"], "parser_status": doc["parser_status"]}, actor_id=principal.user_id)
    return {**doc, "return_row_count": len(report["return_rows"])}


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/artifacts")
def list_return_audit_artifacts(assignment_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal)
    return {"items": list(db.tds_compliance_return_artifact_versions.find({"assignment_id": assignment_id}, NO_ID).sort("uploaded_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/artifacts/{artifact_id}")
def get_return_audit_artifact(assignment_id: str, artifact_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal); artifact = _return_audit_artifact_or_404(assignment_id, artifact_id)
    return {"artifact": artifact, "rows": list(db.tds_compliance_return_rows.find({"assignment_id": assignment_id, "artifact_id": artifact_id}, NO_ID).sort("source_row_number", ASCENDING)), "challans": list(db.tds_compliance_return_challans.find({"assignment_id": assignment_id, "artifact_id": artifact_id}, NO_ID)), "filing_evidence": list(db.tds_compliance_return_filing_evidence.find({"assignment_id": assignment_id, "artifact_id": artifact_id}, NO_ID)), "records": list(db.tds_compliance_return_records.find({"assignment_id": assignment_id, "artifact_id": artifact_id}, NO_ID).sort("source_line_number", ASCENDING))}


@app.post("/api/tds-compliance/assignments/{assignment_id}/return-audit/artifacts/{artifact_id}/validate")
def validate_return_audit_artifact(assignment_id: str, artifact_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _return_audit_access(assignment_id, principal); artifact = _return_audit_artifact_or_404(assignment_id, artifact_id)
    try:
        authorize(principal, "validate", organization_id=assignment["organization_id"], client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    _audit_tds_compliance("RETURN_AUDIT_ARTIFACT_VALIDATED", assignment, entity_id=artifact_id, new_value={"parser_status": artifact["parser_status"]}, actor_id=principal.user_id)
    return {"artifact_id": artifact_id, "status": artifact["parser_status"], "message": artifact["parser_message"], "return_form": artifact.get("return_form"), "source_version": artifact.get("source_version")}


@app.post("/api/tds-compliance/assignments/{assignment_id}/return-audit/run", status_code=201)
def run_return_audit(assignment_id: str, body: ReturnAuditRunBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _return_audit_access(assignment_id, principal); artifact = _return_audit_artifact_or_404(assignment_id, body.artifact_id)
    try:
        authorize(principal, "reconcile", organization_id=assignment["organization_id"], client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if assignment.get("status") == "LOCKED": raise HTTPException(409, "This assignment is locked; new audit runs cannot be added.")
    if artifact["parser_status"] != "VALID": raise HTTPException(422, "This artifact needs CA review before a deterministic return-row audit can run.")
    calculation = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "calculation_id": body.calculation_id}, NO_ID) if body.calculation_id else db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("created_at", DESCENDING)])
    if body.calculation_id and not calculation: raise HTTPException(404, "Calculation snapshot not found for this assignment.")
    calculation = calculation or {}
    calculation_rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation.get("calculation_id")}, NO_ID)) if calculation else []
    ledger_version = calculation.get("ledger_version_id") or (db.tds_compliance_ledger_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)]) or {}).get("ledger_version_id")
    ledger_rows = list(db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": ledger_version}, NO_ID)) if ledger_version else []
    deposit_run = db.tds_compliance_deposit_runs.find_one({"assignment_id": assignment_id, "calculation_id": calculation.get("calculation_id"), "ledger_version_id": ledger_version}, NO_ID, sort=[("created_at", DESCENDING)]) if calculation else None
    deposit_rows = list(db.tds_compliance_deposit_results.find({"assignment_id": assignment_id, "deposit_run_id": deposit_run["deposit_run_id"]}, NO_ID)) if deposit_run else []
    interest_run = db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "calculation_id": calculation.get("calculation_id"), "deposit_run_id": deposit_run["deposit_run_id"], "ledger_version_id": ledger_version}, NO_ID, sort=[("created_at", DESCENDING)]) if deposit_run else None
    interest_rows = list(db.tds_compliance_interest_results.find({"assignment_id": assignment_id, "interest_run_id": interest_run["interest_run_id"]}, NO_ID)) if interest_run else []
    challans = list(db.tds_compliance_return_challans.find({"assignment_id": assignment_id, "artifact_id": body.artifact_id}, NO_ID))
    return_rows = list(db.tds_compliance_return_rows.find({"assignment_id": assignment_id, "artifact_id": body.artifact_id}, NO_ID))
    audit_run_id = f"TDRAUD-{uuid.uuid4().hex[:14].upper()}"; created_at = now_iso()
    context = {"financial_year": artifact.get("normalized_financial_year") or assignment.get("financial_year"), "quarter": artifact.get("source_quarter") or assignment.get("quarter"), "return_artifact_id": body.artifact_id, "return_form": artifact.get("return_form"), "statement_type": artifact.get("statement_type"), "original_return_reference": (artifact.get("source_metadata") or {}).get("original_return_reference"), "correction_reference": (artifact.get("source_metadata") or {}).get("correction_reference")}
    comparison = reconcile_return_evidence(return_rows, calculation_rows, ledger_rows, challans=challans, deposit_rows=deposit_rows, interest_rows=interest_rows, context=context)
    results = comparison["items"]
    run = {"audit_run_id": audit_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "schema_version": RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "artifact_id": body.artifact_id, "source_artifact_sha256": artifact["sha256"], "calculation_id": calculation.get("calculation_id"), "ledger_version_id": calculation.get("ledger_version_id"), "created_at": created_at, "created_by": principal.user_id, "summary": {"total": len(results), "matched": sum(r["status"] == "MATCHED" for r in results), "exceptions": sum(r["status"] != "MATCHED" for r in results)}}
    run.update({**context, "schema_version": RETURN_RECONCILIATION_SCHEMA_VERSION, "summary": comparison["summary"], "overall_status": comparison["overall_status"], "deposit_run_id": (deposit_run or {}).get("deposit_run_id"), "interest_run_id": (interest_run or {}).get("interest_run_id")})
    db.tds_compliance_return_audit_runs.insert_one(dict(run))
    docs = [{**row, "result_id": f"TDRARES-{uuid.uuid4().hex[:14].upper()}", "audit_run_id": audit_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "schema_version": RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "artifact_id": body.artifact_id, "source_artifact_sha256": artifact["sha256"], "created_at": created_at} for row in results]
    for doc in docs: doc["schema_version"] = RETURN_RECONCILIATION_SCHEMA_VERSION
    if docs: db.tds_compliance_return_audit_results.insert_many(docs)
    exceptions = [d for d in docs if d["status"] != "MATCHED"]
    if exceptions: db.tds_compliance_return_exceptions.insert_many([{**d, "exception_id": f"TDRAEX-{uuid.uuid4().hex[:14].upper()}"} for d in exceptions])
    _audit_tds_compliance("RETURN_AUDIT_RUN", assignment, entity_id=audit_run_id, new_value={"artifact_id": body.artifact_id, "calculation_id": calculation.get("calculation_id"), **run["summary"]}, actor_id=principal.user_id)
    return {"audit_run": run, "items": docs}


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/summary")
def return_audit_summary(assignment_id: str, audit_run_id: str | None = None, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal); run = db.tds_compliance_return_audit_runs.find_one({"assignment_id": assignment_id, **({"audit_run_id": audit_run_id} if audit_run_id else {})}, NO_ID, sort=[("created_at", DESCENDING)])
    return {"latest_run": run, "summary": run.get("summary") if run else None}


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/results")
def list_return_audit_results(assignment_id: str, audit_run_id: str | None = None, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal); query = {"assignment_id": assignment_id};
    if audit_run_id: query["audit_run_id"] = audit_run_id
    return _tds_api_value({"items": list(db.tds_compliance_return_audit_results.find(query, NO_ID).sort("created_at", DESCENDING))})


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/results/{result_id}")
def get_return_audit_result(assignment_id: str, result_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal)
    return _tds_api_value(_return_audit_result_or_404(assignment_id, result_id))


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/review-decisions")
def list_return_audit_review_decisions(assignment_id: str, audit_run_id: str | None = None, audit_result_id: str | None = None, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal)
    query = {"assignment_id": assignment_id}
    if audit_run_id: query["audit_run_id"] = audit_run_id
    if audit_result_id: query["audit_result_id"] = audit_result_id
    return {"items": list(db.tds_compliance_return_audit_review_decisions.find(query, NO_ID).sort("created_at", DESCENDING))}


@app.post("/api/tds-compliance/assignments/{assignment_id}/return-audit/results/{result_id}/review-decisions", status_code=201)
def create_return_audit_review_decision(assignment_id: str, result_id: str, body: ReturnAuditReviewDecisionBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _return_audit_access(assignment_id, principal, "review_identity")
    if assignment.get("status") == "LOCKED": raise HTTPException(409, "This assignment is locked; review decisions cannot be added.")
    decision = body.decision.strip().upper()
    if decision not in {"CONFIRM", "REJECT", "KEEP_REVIEW", "MARK_UNSUPPORTED"}:
        raise HTTPException(422, "Decision must be CONFIRM, REJECT, KEEP_REVIEW, or MARK_UNSUPPORTED.")
    if not body.reason.strip(): raise HTTPException(422, "A review reason is required.")
    result = _return_audit_result_or_404(assignment_id, result_id)
    record = {"decision_id": f"TDRAD-{uuid.uuid4().hex[:14].upper()}", "workflow": TDS_COMPLIANCE_WORKFLOW, "schema_version": RETURN_RECONCILIATION_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "artifact_id": result.get("artifact_id"), "audit_run_id": result.get("audit_run_id"), "audit_result_id": result_id, "user": principal.user_id, "created_at": now_iso(), "decision": decision, "reason": body.reason.strip(), "source_references": result.get("source_references") or {}, "result_status": result.get("status")}
    db.tds_compliance_return_audit_review_decisions.insert_one(dict(record))
    _audit_tds_compliance("RETURN_AUDIT_REVIEW_DECISION", assignment, entity_id=record["decision_id"], new_value={"audit_result_id": result_id, "decision": decision, "reason": record["reason"]}, actor_id=principal.user_id)
    return record


@app.get("/api/tds-compliance/assignments/{assignment_id}/return-audit/exceptions")
def list_return_audit_exceptions(assignment_id: str, audit_run_id: str | None = None, principal: Principal = Depends(_tds_compliance_security_boundary)):
    _return_audit_access(assignment_id, principal); query = {"assignment_id": assignment_id};
    if audit_run_id: query["audit_run_id"] = audit_run_id
    return {"items": list(db.tds_compliance_return_exceptions.find(query, NO_ID).sort("created_at", DESCENDING))}


# ---------- health / meta ----------
@app.get("/api/health")
def health():
    try:
        db.command("ping")
        mongo = "ok"
    except Exception:
        mongo = "unavailable"
    return {"status": "ok", "service": "26AS Reconciliation Engine", "version": "1.0.0", "database": mongo, "time": now_iso()}


@app.get("/api/meta")
def meta():
    return {"result_types": RESULT_TYPES, "match_methods": MATCH_METHODS, "claimability": ["CLAIMABLE", "NOT_CLAIMABLE", "NOT_APPLICABLE"], "identity_statuses": ["EXACT", "HIGH_CONFIDENCE", "REVIEW_REQUIRED", "UNMAPPED"], "exception_categories": sorted(set(CATEGORY.values())), "steps": [{"key": k, "label": l} for k, l in STEPS], "reports": REPORTS, "formats": list(FORMATS), "file_kinds": {k: FILE_LABELS[k] for k in FILE_KINDS}, "supported_extensions": list(SUPPORTED_EXTENSIONS), "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}


# ---------- TDS Compliance foundation (deductor-side; isolated) ----------
class TdsComplianceAssignmentBody(BaseModel):
    organization_id: str
    client_id: str
    assessee_legal_name: str
    assessee_pan: str | None = None
    tan: str | None = None
    financial_year: str
    tax_year: str | None = None
    assessment_year: str | None = None
    quarter: str | None = None
    audit_period_start: str | None = None
    audit_period_end: str | None = None
    appointment_date: str | None = None
    target_submission_deadline: str | None = None
    status: str = "DRAFT"


class PaymentLedgerUploadBody(BaseModel):
    upload_id: str


class GovernmentEvidenceUploadBody(BaseModel):
    upload_id: str


class GovernmentEvidenceVerificationBody(BaseModel):
    evidence_version_id: str | None = None
    calculation_id: str | None = None


class GovernmentEvidenceReviewBody(BaseModel):
    action: str
    reason: str | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def review_content(self):
        action = self.action.upper()
        if action not in {"REVIEW", "COMMENT", "RESOLVE", "REOPEN"}:
            raise ValueError("Unsupported review action.")
        if action in {"REVIEW", "RESOLVE", "REOPEN"} and not (self.reason or "").strip():
            raise ValueError("A reason is required for this review action.")
        if action == "COMMENT" and not (self.comment or "").strip():
            raise ValueError("A comment is required.")
        self.action = action
        return self


class CalculationPreviewBody(BaseModel):
    ledger_version_id: str | None = None


class InterestPreviewBody(BaseModel):
    calculation_id: str


class InterestComplianceBody(BaseModel):
    calculation_id: str
    deposit_run_id: str
    model_config = ConfigDict(extra="forbid")


class DepositEvidenceBody(BaseModel):
    upload_id: str | None = None
    calculation_id: str | None = None
    interest_run_id: str | None = None
    evidence_version_id: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class SourceMappingReviewBody(BaseModel):
    """CA confirmation of a detected source column's accounting role."""
    model_config = ConfigDict(extra="forbid")
    source_column: str
    canonical_field: str
    accounting_role: str | None = None
    evidence: str | None = None


class TdsCalculatorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    financial_year: str
    payment_nature: str
    amount: str | float | int
    actual_tds_deducted: str | float | int | None = None
    credit_date: str | None = None
    payment_date: str | None = None
    deduction_date: str | None = None
    actual_deposit_date: str | None = None
    deductee_name: str | None = None
    deductee_pan: str | None = None
    pan_status: str | None = None
    deductee_type: str | None = None
    payer_type: str | None = None
    resident_status: str | None = None
    recipient_residency: str | None = None
    recipient_category: str | None = None
    payer_category: str | None = None
    section_input: str | None = None
    previous_aggregate_amount: str | float | int | None = None
    taxable_amount: str | float | int | None = None
    payment_mode: str = "GROSS"
    certificate_number: str | None = None
    certificate_rate: str | float | int | None = None
    certificate_valid_from: str | None = None
    certificate_valid_to: str | None = None
    certificate_deductee_pan: str | None = None
    certificate_payment_nature: str | None = None

    @model_validator(mode="after")
    def calculator_inputs(self):
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", self.financial_year):
            raise ValueError("financial_year must use YYYY-YY.")
        if self.payment_mode.upper() not in {"GROSS", "NET_OF_TDS"}:
            raise ValueError("payment_mode must be GROSS or NET_OF_TDS.")
        if self.recipient_residency is not None:
            self.recipient_residency = self.recipient_residency.upper()
            if self.recipient_residency not in RECIPIENT_RESIDENCIES:
                raise ValueError("recipient_residency must be RESIDENT, NON_RESIDENT, or FOREIGN_COMPANY.")
        for field, allowed in (("recipient_category", RECIPIENT_CATEGORIES), ("payer_category", PAYER_CATEGORIES)):
            value = getattr(self, field)
            if value is not None:
                value = value.upper()
                if value not in allowed:
                    raise ValueError(f"{field} is not a controlled category.")
                setattr(self, field, value)
        return self


class TdsComplianceRuleBody(BaseModel):
    """A controlled, CA-owned rule draft.

    Values that determine a statutory amount may be blank in a DRAFT, but are
    required before the draft can be approved.  This avoids silently filling
    legal values while still allowing a CA team to prepare a rule for review.
    """
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    scope: str = "ORGANIZATION"
    client_id: str | None = None
    assignment_id: str | None = None
    financial_year: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    governing_act: str | None = None
    payment_nature: str | None = None
    deductee_type: str | None = None
    # Generalized source-catalog dimensions.  They are optional for legacy
    # records and mandatory only for a governed rule that opts into generic
    # selection; no source transcription becomes executable automatically.
    recipient_residency: str | None = None
    recipient_category: str | None = None
    payer_category: str | None = None
    nature_of_payment: str | None = None
    act_2025_section: str | None = None
    act_2025_table_sl_no: str | None = None
    corresponding_1961_act_section: str | None = None
    surcharge_hec: dict | None = None
    conditions: list[str] | None = None
    exceptions: list[str] | None = None
    evidence_requirements: list[str] | None = None
    generalized_selection_required: StrictBool = False
    section_reference: str | None = None
    historical_section_reference: str | None = None
    table_reference: str | None = None
    rate: str | float | int | None = None
    no_pan_rate: str | float | int | None = None
    threshold: str | float | int | None = None
    threshold_type: str | None = None
    per_transaction_threshold: str | float | int | None = None
    aggregate_financial_year_threshold: str | float | int | None = None
    excess_only: bool = False
    calculation_basis: str | None = None
    rule_defined_base_field: str | None = None
    rounding_method: str | None = "HALF_UP"
    rounding_precision: int | None = 2
    priority: StrictInt = 0
    source: str | None = None
    source_reference: str | None = None
    source_url: str | None = None
    source_document_title: str | None = None
    source_provision_reference: str | None = None
    source_retrieved_at: str | None = None
    source_verified_at: str | None = None
    source_verification_evidence: str | None = None
    approval_metadata: dict[str, str] | None = None
    policy_status: str | None = None
    ca_approved: StrictBool | None = None
    environment: str | None = None
    assumption_status: str | None = None
    source_gap: StrictBool | None = None
    ca_review_required: StrictBool | None = None
    # Interest rules use the same governed document and lifecycle as statutory
    # rules.  They are deliberately optional in a DRAFT, then required at
    # submission so a CA can prepare an incomplete draft without the system
    # supplying legal values.
    interest_type: str | None = None
    rate_unit: str | None = None
    interest_base: str | None = None
    period_counting_method: str | None = None
    period_day_block: StrictInt | None = None
    deposit_due_date_mode: str | None = None
    deposit_due_offset_days: StrictInt | None = None
    deposit_due_day: StrictInt | None = None
    # A contractor deposit deadline is independently governed from an
    # interest-rate policy.  Its values remain blank in a draft; approval
    # rejects an incomplete configuration instead of supplying a default.
    policy_kind: str | None = None
    deductor_type: str | None = None
    challan_route: str | None = None
    deadline_mode: str | None = None
    days_after_month_end: StrictInt | None = None
    deadline_month: StrictInt | None = None
    deadline_day: StrictInt | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        self.scope = self.scope.upper()
        if self.scope not in {"ORGANIZATION", "CLIENT", "ASSIGNMENT"}:
            raise ValueError("scope must be ORGANIZATION, CLIENT, or ASSIGNMENT.")
        if not self.organization_id.strip():
            raise ValueError("organization_id is required.")
        if self.scope in {"CLIENT", "ASSIGNMENT"} and not (self.client_id or "").strip():
            raise ValueError("client_id is required for this scope.")
        if self.scope == "ASSIGNMENT" and not (self.assignment_id or "").strip():
            raise ValueError("assignment_id is required for assignment scope.")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("Effective dates are reversed.")
        if self.recipient_residency is not None:
            self.recipient_residency = self.recipient_residency.upper()
            if self.recipient_residency not in RECIPIENT_RESIDENCIES:
                raise ValueError("recipient_residency must be RESIDENT, NON_RESIDENT, or FOREIGN_COMPANY.")
        for field, allowed in (("recipient_category", RECIPIENT_CATEGORIES), ("payer_category", PAYER_CATEGORIES)):
            value = getattr(self, field)
            if value is not None:
                value = value.upper()
                if value not in allowed:
                    raise ValueError(f"{field} is not a controlled category.")
                setattr(self, field, value)
        if self.generalized_selection_required and not self.recipient_residency:
            raise ValueError("generalized_selection_required requires recipient_residency.")
        if self.interest_type and self.interest_type not in {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}:
            raise ValueError("interest_type must be DEDUCTION_DELAY_INTEREST or DEPOSIT_DELAY_INTEREST.")
        if self.policy_status and self.policy_status != "PROVISIONAL_UAT":
            raise ValueError("policy_status supports only PROVISIONAL_UAT for guarded E2E records.")
        if self.environment and self.environment != "isolated_e2e_only":
            raise ValueError("environment supports only isolated_e2e_only for provisional records.")
        if self.policy_kind and self.policy_kind != "CONTRACTOR_DEPOSIT_DUE_DATE":
            raise ValueError("Unsupported policy_kind.")
        if self.policy_kind and self.interest_type:
            raise ValueError("A contractor due-date policy cannot also be an interest policy.")
        if self.deductor_type and self.deductor_type not in {"GOVERNMENT_OFFICE", "OTHER_DEDUCTOR"}:
            raise ValueError("Unsupported deductor_type.")
        if self.challan_route and self.challan_route not in {"WITH_CHALLAN", "WITHOUT_CHALLAN"}:
            raise ValueError("Unsupported challan_route.")
        if self.deadline_mode and self.deadline_mode not in {"DEDUCTION_DATE", "MONTH_END_PLUS_DAYS", "FIXED_MONTH_DAY"}:
            raise ValueError("Unsupported deadline_mode.")
        if self.days_after_month_end is not None and self.days_after_month_end < 0:
            raise ValueError("days_after_month_end cannot be negative.")
        if self.deadline_month is not None and not 1 <= self.deadline_month <= 12:
            raise ValueError("deadline_month must be between 1 and 12.")
        if self.deadline_day is not None and not 1 <= self.deadline_day <= 31:
            raise ValueError("deadline_day must be between 1 and 31.")
        if self.interest_base and self.interest_base not in {"expected_tds", "actual_tds", "tax_not_deducted", "tax_short_deducted"}:
            raise ValueError("Unsupported interest_base.")
        if self.rate_unit and self.rate_unit != "PERCENT_PER_PERIOD":
            raise ValueError("interest-policy rate_unit must be PERCENT_PER_PERIOD.")
        if self.period_counting_method and self.period_counting_method not in {"CALENDAR_MONTH_OR_PART", "CONFIGURED_FIXED_DAY_BLOCK"}:
            raise ValueError("Unsupported period_counting_method.")
        if self.deposit_due_date_mode and self.deposit_due_date_mode not in {"FIXED_OFFSET_DAYS", "NEXT_MONTH_CONFIGURED_DAY", "CHALLAN_CUM_STATEMENT"}:
            raise ValueError("Unsupported deposit_due_date_mode.")
        if self.period_day_block is not None and self.period_day_block <= 0:
            raise ValueError("period_day_block must be positive.")
        if self.deposit_due_offset_days is not None and self.deposit_due_offset_days < 0:
            raise ValueError("deposit_due_offset_days cannot be negative.")
        if self.deposit_due_day is not None and not 1 <= self.deposit_due_day <= 31:
            raise ValueError("deposit_due_day must be between 1 and 31.")
        traceability_errors = source_traceability_errors(self.model_dump())
        if traceability_errors:
            raise ValueError("; ".join(traceability_errors))
        return self


class TdsClassificationMappingBody(BaseModel):
    """CA-controlled accounting evidence to payment-nature mapping."""
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    client_id: str | None = None
    assignment_id: str | None = None
    keyword: str | None = None
    account_description: str | None = None
    gl_account_code: str | None = None
    vendor_code: str | None = None
    payment_nature: str
    section_reference: str | None = None
    priority: StrictInt = 0
    active: StrictBool = True

    @model_validator(mode="after")
    def mapping_evidence(self):
        if not self.organization_id.strip():
            raise ValueError("organization_id is required.")
        if not any(str(value or "").strip() for value in (self.keyword, self.account_description, self.gl_account_code, self.vendor_code)):
            raise ValueError("At least one mapping evidence field is required.")
        return self


CLASSIFICATION_FACT_TYPES = {
    "PARTY_CAPACITY",
    "ASSET_INSTRUMENT_SCHEME",
    "SERVICE_ACTIVITY_CHANNEL",
    "AGREEMENT_HISTORIC_PROVISION",
}
CLASSIFICATION_FACT_EVIDENCE_STATUSES = {
    "VERIFIED", "UNVERIFIED", "UNKNOWN", "CONTRADICTED", "NOT_APPLICABLE",
}
SOURCE_CONDITION_REFERENCE_PATTERN = re.compile(r"CA-FY2026-27-(?:RES|NR)-[0-9]{2}$")
VALUE_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,79}$")


class TdsClassificationFact(BaseModel):
    """Source-anchored fact retained for future governed catalog predicates.

    Facts are evidence records, not statutory-rule selections.  No rule may
    consume a fact unless its configured predicate explicitly names the same
    type, value and source condition in a future governed catalog version.
    """
    model_config = ConfigDict(extra="forbid")
    fact_type: str
    value_code: str | None = None
    evidence_status: str
    evidence_reference: str | None = None
    source_condition_reference: str
    review_reason: str | None = None

    @model_validator(mode="after")
    def validate_source_anchored_fact(self):
        self.fact_type = self.fact_type.upper()
        self.evidence_status = self.evidence_status.upper()
        if self.fact_type not in CLASSIFICATION_FACT_TYPES:
            raise ValueError("Unsupported classification fact_type.")
        if self.evidence_status not in CLASSIFICATION_FACT_EVIDENCE_STATUSES:
            raise ValueError("Unsupported classification evidence_status.")
        if not SOURCE_CONDITION_REFERENCE_PATTERN.fullmatch(self.source_condition_reference):
            raise ValueError("source_condition_reference must identify a FY 2026-27 CA source row.")
        if self.value_code is not None:
            self.value_code = self.value_code.upper()
            if not VALUE_CODE_PATTERN.fullmatch(self.value_code):
                raise ValueError("value_code must use a controlled uppercase code.")
        if self.evidence_status == "VERIFIED" and (not self.value_code or not str(self.evidence_reference or "").strip()):
            raise ValueError("VERIFIED classification facts require value_code and evidence_reference.")
        if self.evidence_status in {"UNVERIFIED", "UNKNOWN", "CONTRADICTED"} and not str(self.review_reason or "").strip():
            raise ValueError("Unresolved classification facts require review_reason.")
        return self


class TdsTransactionClassificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_nature: str
    section_reference: str | None = None
    deductee_type: str | None = None
    recipient_residency: str | None = None
    recipient_category: str | None = None
    payer_category: str | None = None
    classification_facts: list[TdsClassificationFact] = Field(default_factory=list)
    # Optional controlled contractor evidence. Supplying these fields never
    # approves a statutory rule; it permits the domain layer to retain a
    # review-only applicability decision rather than infer it from a label.
    contractor_control_contract: str | None = None
    payer_eligibility_status: str | None = None
    payer_eligibility_evidence_reference: str | None = None
    contractor_residency_status: str | None = None
    contractor_residency_evidence_reference: str | None = None
    contractor_exception_status: str | None = None
    contractor_exception_type: str | None = None
    contractor_exception_evidence_reference: str | None = None
    pan_operational_status: str | None = None
    pan_evidence_reference: str | None = None
    deductor_type: str | None = None
    challan_route: str | None = None
    contractor_personal_purpose_attestation: StrictBool | None = None
    contractor_personal_purpose_payer_type: str | None = None
    contractor_personal_purpose_payer_type_evidence_reference: str | None = None
    goods_carriage_count: StrictInt | None = None
    goods_carriage_business_evidence_reference: str | None = None
    goods_carriage_declaration_reference: str | None = None
    goods_carriage_pan_reference: str | None = None
    goods_carriage_particulars_reference: str | None = None
    contractor_invoice_material_status: str | None = None
    contractor_invoice_material_evidence_reference: str | None = None
    reason: str = "CA review classification"

    @model_validator(mode="after")
    def validate_contractor_control(self):
        if self.recipient_residency is not None:
            self.recipient_residency = self.recipient_residency.upper()
            if self.recipient_residency not in RECIPIENT_RESIDENCIES:
                raise ValueError("recipient_residency must be RESIDENT, NON_RESIDENT, or FOREIGN_COMPANY.")
        fields = (self.payer_eligibility_status, self.payer_eligibility_evidence_reference, self.contractor_residency_status, self.contractor_residency_evidence_reference, self.contractor_exception_status, self.contractor_exception_type, self.contractor_exception_evidence_reference, self.pan_operational_status, self.pan_evidence_reference, self.deductor_type, self.challan_route, self.contractor_personal_purpose_attestation, self.contractor_personal_purpose_payer_type, self.contractor_personal_purpose_payer_type_evidence_reference, self.goods_carriage_count, self.goods_carriage_business_evidence_reference, self.goods_carriage_declaration_reference, self.goods_carriage_pan_reference, self.goods_carriage_particulars_reference, self.contractor_invoice_material_status, self.contractor_invoice_material_evidence_reference)
        if self.contractor_control_contract is not None and self.contractor_control_contract != "CONTRACTOR_WITHHOLDING_V1":
            raise ValueError("Unsupported contractor_control_contract.")
        if any(value is not None for value in fields) and self.contractor_control_contract != "CONTRACTOR_WITHHOLDING_V1":
            raise ValueError("Controlled contractor evidence requires contractor_control_contract=CONTRACTOR_WITHHOLDING_V1.")
        keys = [(fact.fact_type, fact.source_condition_reference) for fact in self.classification_facts]
        if len(keys) != len(set(keys)):
            raise ValueError("Only one classification fact is permitted for each fact_type and source condition.")
        return self


class TdsTransactionReviewBody(BaseModel):
    """Append-only CA review action for a source transaction, never a result edit."""
    model_config = ConfigDict(extra="forbid")
    action: str
    reason: str | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def valid_action(self):
        self.action = self.action.upper()
        if self.action not in {"REVIEW", "COMMENT", "RESOLVE", "REOPEN"}:
            raise ValueError("action must be REVIEW, COMMENT, RESOLVE, or REOPEN.")
        if self.action in {"REVIEW", "RESOLVE", "REOPEN"} and not str(self.reason or "").strip():
            raise ValueError("reason is required for this review action.")
        if self.action == "COMMENT" and not str(self.comment or "").strip():
            raise ValueError("comment is required for a comment action.")
        return self


def _assignment_document(body: TdsComplianceAssignmentBody):
    item = body.model_dump()
    item["status"] = item["status"].upper()
    if item["status"] not in ASSIGNMENT_STATUSES:
        raise HTTPException(400, "Unsupported TDS Compliance assignment status.")
    for key in ("organization_id", "client_id", "assessee_legal_name", "financial_year"):
        if not str(item.get(key) or "").strip():
            raise HTTPException(400, f"{key} is required.")
    return {**item, "assignment_id": f"TDCA-{uuid.uuid4().hex[:12].upper()}", "workflow": TDS_COMPLIANCE_WORKFLOW, "created_at": now_iso(), "updated_at": now_iso(), "created_by": None, "locked_at": None, "locked_by": None, "version": 1}


@app.get("/api/tds-compliance/schema/payment-ledger")
def tds_compliance_payment_ledger_schema():
    _tds_compliance_security_boundary()
    return {"workflow": TDS_COMPLIANCE_WORKFLOW, "kind": "payment_ledger", "fields": PAYMENT_LEDGER_SCHEMA, "note": "Absent source values remain null/unknown. TDS deducted is never treated as TDS deposited."}


@app.get("/api/tds-compliance/schema/deductee-master")
def tds_compliance_deductee_master_schema():
    _tds_compliance_security_boundary()
    return {"workflow": TDS_COMPLIANCE_WORKFLOW, "kind": "deductee_master", "fields": DEDUCTEE_MASTER_SCHEMA, "note": "PAN format validity is not PAN verification."}


@app.get("/api/tds-compliance/rules")
def list_tds_compliance_rules(principal: Principal = Depends(_tds_compliance_security_boundary)):
    """Return only rules in the caller's organisation and permitted clients."""
    query = {"workflow": TDS_COMPLIANCE_WORKFLOW, "$or": [{"organization_id": principal.organization_id}, {"scope": "GLOBAL"}]}
    rules = list(db.tds_compliance_rules.find(query, NO_ID).sort([("financial_year", DESCENDING), ("updated_at", DESCENDING)]))
    rules = [rule for rule in rules if not rule.get("client_id") or principal.can_access_client(rule["client_id"])]
    return {"rules": rules}


def _rule_access(principal: Principal, rule: dict, permission: str = "manage_rules") -> None:
    try:
        authorize(principal, permission, organization_id=rule.get("organization_id"), client_id=rule.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


def _mapping_access(principal: Principal, mapping: dict, permission: str = "manage_rules") -> None:
    try:
        authorize(principal, permission, organization_id=mapping.get("organization_id"), client_id=mapping.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.get("/api/tds-compliance/classification-mappings")
def list_tds_classification_mappings(principal: Principal = Depends(_tds_compliance_security_boundary)):
    mappings = list(db.tds_compliance_classification_mappings.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": principal.organization_id}, NO_ID).sort([("priority", DESCENDING), ("updated_at", DESCENDING)]))
    return {"items": [mapping for mapping in mappings if not mapping.get("client_id") or principal.can_access_client(mapping["client_id"])]}


@app.post("/api/tds-compliance/classification-mappings", status_code=201)
def create_tds_classification_mapping(body: TdsClassificationMappingBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    data = body.model_dump()
    if data["organization_id"] != principal.organization_id or (data.get("client_id") and not principal.can_access_client(data["client_id"])):
        raise HTTPException(403, "Classification mapping scope is not available.")
    mapping = {**data, "mapping_id": f"TDCM-{uuid.uuid4().hex[:12].upper()}", "workflow": TDS_COMPLIANCE_WORKFLOW, "version": 1, "created_at": now_iso(), "updated_at": now_iso(), "created_by": principal.user_id, "updated_by": principal.user_id}
    _mapping_access(principal, mapping)
    db.tds_compliance_classification_mappings.insert_one(dict(mapping))
    _audit_tds_rule("CLASSIFICATION_MAPPING_CREATED", {**mapping, "rule_id": mapping["mapping_id"]}, principal, new_value={"payment_nature": mapping["payment_nature"], "priority": mapping["priority"]})
    return mapping


def _mapping_matches_row(mapping: dict, row: dict) -> bool:
    description = " ".join(str(row.get(field) or "") for field in ("description", "deductee_name", "invoice_number")).lower()
    keyword = str(mapping.get("keyword") or "").strip().lower()
    account = str(mapping.get("account_description") or "").strip().lower()
    gl_code = str(mapping.get("gl_account_code") or "").strip().lower()
    vendor = str(mapping.get("vendor_code") or "").strip().lower()
    checks = []
    if keyword: checks.append(keyword in description)
    if account: checks.append(account in description)
    if gl_code: checks.append(gl_code == str(row.get("gl_account_code") or "").strip().lower())
    if vendor: checks.append(vendor == str(row.get("vendor_code") or "").strip().lower())
    return bool(checks) and all(checks)


def _apply_classification_mappings(assignment: dict, rows: list[dict], ledger_version_id: str | None = None) -> list[dict]:
    """Derive only from explicit, scoped CA mappings; never infer free text."""
    mappings = list(db.tds_compliance_classification_mappings.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": assignment["organization_id"], "active": True}, NO_ID))
    classification_query = {"assignment_id": assignment["assignment_id"]}
    if ledger_version_id:
        classification_query["ledger_version_id"] = ledger_version_id
    overrides = {item["transaction_id"]: item for item in db.tds_compliance_transaction_classifications.find(classification_query, NO_ID)}
    output = []
    for source in rows:
        row = dict(source)
        override = overrides.get(row.get("transaction_id"))
        if override:
            controlled = {key: override.get(key) for key in ("recipient_residency", "recipient_category", "payer_category", "classification_facts", "contractor_control_contract", "payer_eligibility_status", "payer_eligibility_evidence_reference", "contractor_residency_status", "contractor_residency_evidence_reference", "contractor_exception_status", "contractor_exception_type", "contractor_exception_evidence_reference", "pan_operational_status", "pan_evidence_reference", "deductor_type", "challan_route", "contractor_personal_purpose_attestation", "contractor_personal_purpose_payer_type", "contractor_personal_purpose_payer_type_evidence_reference", "goods_carriage_count", "goods_carriage_business_evidence_reference", "goods_carriage_declaration_reference", "goods_carriage_pan_reference", "goods_carriage_particulars_reference", "contractor_invoice_material_status", "contractor_invoice_material_evidence_reference") if override.get(key) is not None}
            row.update({"payment_nature": override["payment_nature"], "section_input": override.get("section_reference") or row.get("section_input"), "deductee_type": override.get("deductee_type") or row.get("deductee_type"), "payment_nature_source": "CA_REVIEW", "payment_nature_confidence": "HIGH", "payment_nature_status": "CLASSIFIED", "classification_reason": override.get("reason"), "classification_reviewed_at": override.get("reviewed_at"), "classification_reviewer": override.get("reviewed_by"), **controlled})
            output.append(row); continue
        if str(row.get("payment_nature") or "").strip():
            row.update({"payment_nature_source": "SOURCE_PROVIDED", "payment_nature_confidence": "HIGH", "classification_reason": "Payment nature was provided by the source ledger."})
            output.append(row); continue
        matches = [mapping for mapping in mappings if (not mapping.get("client_id") or mapping["client_id"] == assignment.get("client_id")) and (not mapping.get("assignment_id") or mapping["assignment_id"] == assignment.get("assignment_id")) and _mapping_matches_row(mapping, row)]
        if not matches:
            row.update({"payment_nature": None, "payment_nature_source": "NOT_PROVIDED", "payment_nature_confidence": "NONE", "payment_nature_status": "NOT_DETERMINABLE", "classification_reason": "Payment nature/statutory classification was not available from the source ledger."})
            output.append(row); continue
        priority = max(int(mapping.get("priority", 0)) for mapping in matches)
        winners = [mapping for mapping in matches if int(mapping.get("priority", 0)) == priority]
        natures = {mapping.get("payment_nature") for mapping in winners}
        if len(natures) != 1:
            row.update({"payment_nature": None, "payment_nature_source": "CA_MAPPING", "payment_nature_confidence": "CONFLICT", "payment_nature_status": "CONFLICT", "classification_reason": "Multiple equally-prioritised CA classification mappings conflict."})
        else:
            mapping = winners[0]
            row.update({"payment_nature": mapping["payment_nature"], "section_input": row.get("section_input") or mapping.get("section_reference"), "payment_nature_source": "CA_MAPPING", "payment_nature_confidence": "HIGH", "payment_nature_status": "CLASSIFIED", "classification_mapping_id": mapping["mapping_id"], "classification_reason": "Payment nature was derived from an active CA classification mapping."})
        output.append(row)
    return output


@app.get("/api/tds-compliance/assignments/{assignment_id}/classification-review")
def list_tds_classification_review(assignment_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _tds_assignment_or_404(assignment_id)
    _mapping_access(principal, assignment, "view_results")
    version, rows = _ledger_for_calculation(assignment_id, None)
    classified = _apply_classification_mappings(assignment, rows, version.get("ledger_version_id"))
    fields = (
        "transaction_id", "payment_date", "credit_date", "deductee_name", "description", "amount",
        "payment_nature", "payment_nature_source", "payment_nature_confidence", "payment_nature_status",
        "recipient_residency", "recipient_category", "payer_category", "section_input", "deductee_type",
        "classification_reason", "classification_facts", "contractor_control_contract", "payer_eligibility_status",
        "payer_eligibility_evidence_reference", "contractor_residency_status",
        "contractor_residency_evidence_reference", "contractor_exception_status",
        "contractor_exception_type", "contractor_exception_evidence_reference", "pan_operational_status",
        "pan_evidence_reference", "deductor_type", "challan_route",
        "contractor_personal_purpose_attestation", "contractor_personal_purpose_payer_type",
        "contractor_personal_purpose_payer_type_evidence_reference", "goods_carriage_count",
        "goods_carriage_business_evidence_reference", "goods_carriage_declaration_reference",
        "goods_carriage_pan_reference", "goods_carriage_particulars_reference",
        "contractor_invoice_material_status", "contractor_invoice_material_evidence_reference",
    )
    return {"items": [{key: row.get(key) for key in fields} for row in classified]}


@app.put("/api/tds-compliance/assignments/{assignment_id}/classification-review/{transaction_id}")
def classify_tds_transaction(assignment_id: str, transaction_id: str, body: TdsTransactionClassificationBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _tds_assignment_or_404(assignment_id)
    _mapping_access(principal, assignment)
    version, rows = _ledger_for_calculation(assignment_id, None)
    if not any(row.get("transaction_id") == transaction_id for row in rows):
        raise HTTPException(404, "Payment ledger transaction not found.")
    record = {"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"], "transaction_id": transaction_id, "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], **body.model_dump(), "reviewed_at": now_iso(), "reviewed_by": principal.user_id}
    db.tds_compliance_transaction_classifications.replace_one({"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"], "transaction_id": transaction_id}, record, upsert=True)
    _audit_tds_compliance("TRANSACTION_CLASSIFIED", assignment, entity_id=transaction_id, new_value={"payment_nature": record["payment_nature"], "section_reference": record.get("section_reference"), "reason": record["reason"]})
    return record


def _rule_or_404(rule_id: str) -> dict:
    rule = db.tds_compliance_rules.find_one({"workflow": TDS_COMPLIANCE_WORKFLOW, "rule_id": rule_id}, NO_ID)
    if not rule:
        raise HTTPException(404, "TDS Compliance rule not found.")
    return rule


def _audit_tds_rule(action: str, rule: dict, principal: Principal, *, old_value=None, new_value=None) -> None:
    db.tds_compliance_audit_events.insert_one({
        "event_id": uuid.uuid4().hex, "workflow": TDS_COMPLIANCE_WORKFLOW,
        "assignment_id": rule.get("assignment_id"), "organization_id": rule.get("organization_id"),
        "client_id": rule.get("client_id"), "user_id": principal.user_id,
        "action": action, "entity_type": "TDS_COMPLIANCE_RULE", "entity_id": rule["rule_id"],
        "old_value": old_value, "new_value": new_value, "timestamp": now_iso(),
        "source": "RULE_MANAGEMENT", "reason": None,
    })


def _record_rule_revision(rule: dict, action: str, principal: Principal) -> None:
    revision = db.tds_compliance_rule_history.count_documents({"rule_id": rule["rule_id"]}) + 1
    snapshot = {key: value for key, value in rule.items() if key != "_id"}
    db.tds_compliance_rule_history.insert_one({"rule_id": rule["rule_id"], "revision": revision, "action": action, "recorded_at": now_iso(), "recorded_by": principal.user_id, "snapshot": snapshot})


def _approval_errors(rule: dict) -> list[str]:
    if rule.get("policy_kind") == "CONTRACTOR_DEPOSIT_DUE_DATE":
        return _contractor_due_date_policy_approval_errors(rule)
    if rule.get("interest_type"):
        return _interest_rule_approval_errors(rule)
    required = ("financial_year", "effective_from", "effective_to", "governing_act", "payment_nature", "deductee_type", "section_reference", "rate", "threshold_type", "calculation_basis", "rounding_method", "rounding_precision", "source", "source_reference")
    errors = [field for field in required if rule.get(field) in {None, ""}]
    if rule.get("threshold_type") != "NO_THRESHOLD" and rule.get("threshold") in {None, ""}:
        if rule.get("per_transaction_threshold") in {None, ""} and rule.get("aggregate_financial_year_threshold") in {None, ""}:
            errors.append("threshold")
    if rule.get("financial_year") and not re.fullmatch(r"[0-9]{4}-[0-9]{2}", str(rule["financial_year"])):
        errors.append("financial_year_format")
    if rule.get("effective_from") and rule.get("effective_to") and rule["effective_from"] > rule["effective_to"]:
        errors.append("effective_date_range")
    return errors


def _contractor_due_date_policy_approval_errors(rule: dict) -> list[str]:
    """Validate the independent governed deadline configuration at approval."""
    required = (
        "financial_year", "effective_from", "effective_to", "governing_act",
        "payment_nature", "deductee_type", "section_reference", "deductor_type",
        "deadline_mode", "source", "source_reference", "source_url",
        "source_document_title", "source_provision_reference", "source_retrieved_at",
        "source_verification_evidence",
    )
    errors = [field for field in required if rule.get(field) in {None, ""}]
    if rule.get("payment_nature") != "contractor":
        errors.append("payment_nature")
    if rule.get("deductor_type") not in {"GOVERNMENT_OFFICE", "OTHER_DEDUCTOR"}:
        errors.append("deductor_type")
    if rule.get("deductor_type") == "GOVERNMENT_OFFICE" and rule.get("challan_route") not in {"WITH_CHALLAN", "WITHOUT_CHALLAN"}:
        errors.append("challan_route")
    mode = rule.get("deadline_mode")
    if mode not in {"DEDUCTION_DATE", "MONTH_END_PLUS_DAYS", "FIXED_MONTH_DAY"}:
        errors.append("deadline_mode")
    elif mode == "MONTH_END_PLUS_DAYS" and (not isinstance(rule.get("days_after_month_end"), int) or rule["days_after_month_end"] < 0):
        errors.append("days_after_month_end")
    elif mode == "FIXED_MONTH_DAY" and (
        not isinstance(rule.get("deadline_month"), int)
        or not 1 <= rule["deadline_month"] <= 12
        or not isinstance(rule.get("deadline_day"), int)
        or not 1 <= rule["deadline_day"] <= 31
    ):
        errors.extend(("deadline_month", "deadline_day"))
    try:
        start, end = date.fromisoformat(str(rule.get("effective_from"))), date.fromisoformat(str(rule.get("effective_to")))
        if start > end:
            errors.append("effective_date_range")
        fy = str(rule.get("financial_year"))
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", fy) or int(fy[-2:]) != (int(fy[:4]) + 1) % 100:
            errors.append("financial_year_format")
        elif end < date(int(fy[:4]), 4, 1) or start > date(int(fy[:4]) + 1, 3, 31):
            errors.append("financial_year_scope")
    except (TypeError, ValueError):
        errors.append("effective_dates")
    return sorted(set(errors))


def _interest_rule_approval_errors(rule: dict) -> list[str]:
    """Validate every value consumed by the interest engine before approval.

    This is a policy admission check, not a calculation fallback.  It rejects
    missing or malformed legal configuration rather than choosing defaults.
    """
    required = (
        "financial_year", "effective_from", "effective_to", "governing_act",
        "payment_nature", "deductee_type", "interest_type", "rate", "rate_unit",
        "interest_base", "period_counting_method", "rounding_method",
        "rounding_precision", "source", "source_reference",
    )
    errors = [field for field in required if rule.get(field) in {None, ""}]
    if rule.get("interest_type") not in {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}:
        errors.append("interest_type")
    if rule.get("interest_base") not in {"expected_tds", "actual_tds", "tax_not_deducted", "tax_short_deducted"}:
        errors.append("interest_base")
    if rule.get("rate_unit") != "PERCENT_PER_PERIOD":
        errors.append("rate_unit")
    if rule.get("period_counting_method") not in {"CALENDAR_MONTH_OR_PART", "CONFIGURED_FIXED_DAY_BLOCK"}:
        errors.append("period_counting_method")
    if rule.get("period_counting_method") == "CONFIGURED_FIXED_DAY_BLOCK" and not isinstance(rule.get("period_day_block"), int):
        errors.append("period_day_block")
    if rule.get("rounding_method") != "HALF_UP":
        errors.append("rounding_method")
    if not isinstance(rule.get("rounding_precision"), int) or rule.get("rounding_precision") < 0:
        errors.append("rounding_precision")
    try:
        if Decimal(str(rule.get("rate"))) <= 0:
            errors.append("rate")
    except Exception:
        errors.append("rate")
    try:
        start, end = date.fromisoformat(str(rule.get("effective_from"))), date.fromisoformat(str(rule.get("effective_to")))
        if start > end:
            errors.append("effective_date_range")
        fy = str(rule.get("financial_year"))
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", fy) or int(fy[-2:]) != (int(fy[:4]) + 1) % 100:
            errors.append("financial_year_format")
        else:
            fy_start = date(int(fy[:4]), 4, 1)
            fy_end = date(int(fy[:4]) + 1, 3, 31)
            if end < fy_start or start > fy_end:
                errors.append("financial_year_scope")
    except (TypeError, ValueError):
        errors.append("effective_dates")
    # Rule 218 timing belongs to the separately governed Phase 4 due-date
    # policy. Phase 5 receives its immutable result snapshot, so requiring a
    # second deadline configuration here would create two sources of truth.
    return sorted(set(errors))


def _conflicting_active_interest_rule(rule: dict) -> dict | None:
    """Return an equally-prioritised active rule that would make selection unsafe."""
    if not rule.get("interest_type"):
        return None
    candidates = db.tds_compliance_rules.find({
        "workflow": TDS_COMPLIANCE_WORKFLOW,
        "lifecycle": "ACTIVE",
        "active": True,
        "interest_type": rule["interest_type"],
        "organization_id": rule.get("organization_id"),
        "client_id": rule.get("client_id"),
        "assignment_id": rule.get("assignment_id"),
        "financial_year": rule.get("financial_year"),
        "governing_act": rule.get("governing_act"),
        "payment_nature": rule.get("payment_nature"),
        "deductee_type": rule.get("deductee_type"),
        "priority": rule.get("priority"),
        "rule_id": {"$ne": rule["rule_id"]},
    }, NO_ID)
    for candidate in candidates:
        if str(candidate.get("effective_from")) <= str(rule.get("effective_to")) and str(rule.get("effective_from")) <= str(candidate.get("effective_to")):
            return candidate
    return None


def _conflicting_active_due_date_policy(rule: dict) -> dict | None:
    if rule.get("policy_kind") != "CONTRACTOR_DEPOSIT_DUE_DATE":
        return None
    candidates = db.tds_compliance_rules.find({
        "workflow": TDS_COMPLIANCE_WORKFLOW, "lifecycle": "ACTIVE", "active": True,
        "policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE",
        "organization_id": rule.get("organization_id"), "client_id": rule.get("client_id"),
        "assignment_id": rule.get("assignment_id"), "financial_year": rule.get("financial_year"),
        "governing_act": rule.get("governing_act"), "payment_nature": rule.get("payment_nature"),
        "deductee_type": rule.get("deductee_type"), "section_reference": rule.get("section_reference"),
        "deductor_type": rule.get("deductor_type"), "challan_route": rule.get("challan_route"),
        "priority": rule.get("priority"), "rule_id": {"$ne": rule["rule_id"]},
    }, NO_ID)
    for candidate in candidates:
        if str(candidate.get("effective_from")) <= str(rule.get("effective_to")) and str(rule.get("effective_from")) <= str(candidate.get("effective_to")):
            return candidate
    return None


def _rule_document(body: TdsComplianceRuleBody, principal: Principal, *, rule_id: str | None = None, version: int = 1, family_id: str | None = None) -> dict:
    data = body.model_dump()
    if data["organization_id"] != principal.organization_id:
        raise HTTPException(403, "Rules may only be configured in the caller's organisation.")
    if data.get("client_id") and not principal.can_access_client(data["client_id"]):
        raise HTTPException(403, "Rule client is not available.")
    if data["scope"] == "ASSIGNMENT":
        assignment = _tds_assignment_or_404(data["assignment_id"])
        if assignment.get("organization_id") != data["organization_id"] or assignment.get("client_id") != data["client_id"]:
            raise HTTPException(422, "Assignment scope must match the rule organisation and client.")
    now = now_iso()
    identifier = rule_id or f"TDCR-{uuid.uuid4().hex[:12].upper()}"
    rule = {**data, "rule_id": identifier, "rule_family_id": family_id or identifier, "rule_version": f"v{version}", "version": version,
            "workflow": TDS_COMPLIANCE_WORKFLOW, "lifecycle": "DRAFT", "active": False,
            "created_at": now, "updated_at": now, "created_by": principal.user_id, "updated_by": principal.user_id,
            "approved_at": None, "approved_by": None, "activated_at": None, "activated_by": None}
    traceability = source_traceability_snapshot(rule)
    return {**rule, "source_traceability": traceability, "source_traceability_status": traceability["status"]}


@app.post("/api/tds-compliance/rules", status_code=201)
def create_tds_compliance_rule(body: TdsComplianceRuleBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_document(body, principal)
    _rule_access(principal, rule)
    db.tds_compliance_rules.insert_one(dict(rule))
    _record_rule_revision(rule, "CREATED_DRAFT", principal)
    _audit_tds_rule("RULE_CREATED_DRAFT", rule, principal, new_value={"lifecycle": "DRAFT", "rule_version": rule["rule_version"]})
    return rule


@app.get("/api/tds-compliance/rules/{rule_id}")
def get_tds_compliance_rule(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule, "view_results")
    return rule


@app.get("/api/tds-compliance/rules/{rule_id}/history")
def tds_compliance_rule_history(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule, "view_results")
    return {"items": list(db.tds_compliance_rule_history.find({"rule_id": rule_id}, NO_ID).sort("revision", DESCENDING))}


@app.get("/api/tds-compliance/rules/{rule_id}/audit")
def tds_compliance_rule_audit(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule, "view_results")
    return {"items": list(db.tds_compliance_audit_events.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "entity_type": "TDS_COMPLIANCE_RULE", "entity_id": rule_id}, NO_ID).sort("timestamp", DESCENDING))}


@app.put("/api/tds-compliance/rules/{rule_id}")
def update_tds_compliance_rule(rule_id: str, body: TdsComplianceRuleBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    existing = _rule_or_404(rule_id)
    _rule_access(principal, existing)
    replacement = _rule_document(body, principal, rule_id=rule_id, version=int(existing.get("version", 1)), family_id=existing.get("rule_family_id"))
    if existing.get("lifecycle") == "DRAFT":
        replacement.update({"created_at": existing.get("created_at"), "created_by": existing.get("created_by")})
        db.tds_compliance_rules.replace_one({"rule_id": rule_id}, replacement)
        _record_rule_revision(replacement, "DRAFT_UPDATED", principal)
        _audit_tds_rule("RULE_DRAFT_UPDATED", replacement, principal, old_value=existing, new_value=replacement)
        return replacement
    # An approved/active version is immutable. Editing forks a new inactive draft.
    next_version = int(existing.get("version", 1)) + 1
    fork = _rule_document(body, principal, version=next_version, family_id=existing.get("rule_family_id") or rule_id)
    db.tds_compliance_rules.insert_one(dict(fork))
    _record_rule_revision(fork, "FORKED_DRAFT", principal)
    _audit_tds_rule("RULE_FORKED_DRAFT", fork, principal, old_value={"based_on": rule_id}, new_value={"lifecycle": "DRAFT"})
    return fork


@app.post("/api/tds-compliance/rules/{rule_id}/submit")
def submit_tds_compliance_rule(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule)
    if rule.get("lifecycle") != "DRAFT":
        raise HTTPException(409, "Only a draft rule can be submitted for approval.")
    missing = _approval_errors(rule)
    if missing:
        raise HTTPException(422, "Rule cannot be submitted until configured: " + ", ".join(missing))
    now = now_iso()
    db.tds_compliance_rules.update_one({"rule_id": rule_id, "lifecycle": "DRAFT"}, {"$set": {"lifecycle": "PENDING_APPROVAL", "active": False, "submitted_at": now, "submitted_by": principal.user_id, "updated_at": now, "updated_by": principal.user_id}})
    submitted = _rule_or_404(rule_id)
    _record_rule_revision(submitted, "SUBMITTED_FOR_APPROVAL", principal)
    _audit_tds_rule("RULE_SUBMITTED_FOR_APPROVAL", submitted, principal, old_value={"lifecycle": "DRAFT"}, new_value={"lifecycle": "PENDING_APPROVAL"})
    return submitted


@app.post("/api/tds-compliance/rules/{rule_id}/approve")
def approve_tds_compliance_rule(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule)
    if rule.get("lifecycle") != "PENDING_APPROVAL":
        raise HTTPException(409, "Only a rule pending approval can be approved.")
    now = now_iso()
    approval = {"lifecycle": "APPROVED", "active": False, "approved_at": now, "approved_by": principal.user_id, "updated_at": now, "updated_by": principal.user_id}
    traceability = source_traceability_snapshot({**rule, **approval})
    db.tds_compliance_rules.update_one({"rule_id": rule_id, "lifecycle": "PENDING_APPROVAL"}, {"$set": {**approval, "source_traceability": traceability, "source_traceability_status": traceability["status"]}})
    approved = _rule_or_404(rule_id)
    _record_rule_revision(approved, "APPROVED", principal)
    _audit_tds_rule("RULE_APPROVED", approved, principal, old_value={"lifecycle": "PENDING_APPROVAL"}, new_value={"lifecycle": "APPROVED"})
    return approved


@app.post("/api/tds-compliance/rules/{rule_id}/activate")
def activate_tds_compliance_rule(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule)
    if rule.get("lifecycle") != "APPROVED":
        raise HTTPException(409, "Only an approved rule can be activated.")
    now = now_iso()
    activation_errors = governed_activation_errors(rule)
    if activation_errors:
        raise HTTPException(422, "Rule cannot be activated until governed evidence is complete: " + ", ".join(activation_errors))
    conflict = _conflicting_active_due_date_policy(rule) or _conflicting_active_interest_rule(rule)
    if conflict:
        raise HTTPException(409, f"Activation would create an ambiguous active policy scope with {conflict['rule_id']}.")
    # The new approved version becomes effective atomically in governance
    # terms: prior active versions in the same family are retained but no
    # longer eligible to calculate.
    predecessors = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "rule_family_id": rule.get("rule_family_id"), "lifecycle": "ACTIVE", "rule_id": {"$ne": rule_id}}, NO_ID))
    for predecessor in predecessors:
        db.tds_compliance_rules.update_one({"rule_id": predecessor["rule_id"], "lifecycle": "ACTIVE"}, {"$set": {"lifecycle": "SUPERSEDED", "active": False, "superseded_by": rule_id, "superseded_at": now, "updated_at": now, "updated_by": principal.user_id}})
        superseded = _rule_or_404(predecessor["rule_id"])
        _record_rule_revision(superseded, "SUPERSEDED", principal)
        _audit_tds_rule("RULE_SUPERSEDED", superseded, principal, old_value={"lifecycle": "ACTIVE"}, new_value={"lifecycle": "SUPERSEDED", "superseded_by": rule_id})
    db.tds_compliance_rules.update_one({"rule_id": rule_id, "lifecycle": "APPROVED"}, {"$set": {"lifecycle": "ACTIVE", "active": True, "activated_at": now, "activated_by": principal.user_id, "updated_at": now, "updated_by": principal.user_id}})
    active = _rule_or_404(rule_id)
    _record_rule_revision(active, "ACTIVATED", principal)
    _audit_tds_rule("RULE_ACTIVATED", active, principal, old_value={"lifecycle": "APPROVED"}, new_value={"lifecycle": "ACTIVE"})
    return active


@app.post("/api/tds-compliance/rules/{rule_id}/deactivate")
def deactivate_tds_compliance_rule(rule_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    rule = _rule_or_404(rule_id)
    _rule_access(principal, rule)
    if rule.get("lifecycle") not in {"APPROVED", "ACTIVE"}:
        raise HTTPException(409, "Only an approved or active rule can be deactivated.")
    now = now_iso()
    db.tds_compliance_rules.update_one({"rule_id": rule_id}, {"$set": {"lifecycle": "DEACTIVATED", "active": False, "deactivated_at": now, "deactivated_by": principal.user_id, "updated_at": now, "updated_by": principal.user_id}})
    deactivated = _rule_or_404(rule_id)
    _record_rule_revision(deactivated, "DEACTIVATED", principal)
    _audit_tds_rule("RULE_DEACTIVATED", deactivated, principal, old_value={"lifecycle": rule.get("lifecycle")}, new_value={"lifecycle": "DEACTIVATED"})
    return deactivated


@app.get("/api/tds-compliance/assignments")
def list_tds_compliance_assignments():
    _tds_compliance_security_boundary()
    return {"items": list(db.tds_compliance_assignments.find({"workflow": TDS_COMPLIANCE_WORKFLOW}, NO_ID).sort("created_at", DESCENDING))}


@app.post("/api/tds-compliance/assignments")
def create_tds_compliance_assignment(body: TdsComplianceAssignmentBody):
    _tds_compliance_security_boundary()
    assignment = _assignment_document(body)
    db.tds_compliance_assignments.insert_one(dict(assignment))
    _audit_tds_compliance("ASSIGNMENT_CREATED", assignment, new_value={"status": assignment["status"]})
    return assignment


@app.get("/api/tds-compliance/assignments/{assignment_id}")
def get_tds_compliance_assignment(assignment_id: str):
    _tds_compliance_security_boundary()
    assignment = db.tds_compliance_assignments.find_one({"assignment_id": assignment_id, "workflow": TDS_COMPLIANCE_WORKFLOW}, NO_ID)
    if not assignment:
        raise HTTPException(404, "TDS Compliance assignment not found.")
    return assignment


@app.post("/api/tds-compliance/assignments/{assignment_id}/lock")
def lock_tds_compliance_assignment(assignment_id: str):
    _tds_compliance_security_boundary()
    assignment = db.tds_compliance_assignments.find_one({"assignment_id": assignment_id, "workflow": TDS_COMPLIANCE_WORKFLOW}, NO_ID)
    if not assignment:
        raise HTTPException(404, "TDS Compliance assignment not found.")
    if assignment.get("status") == "LOCKED":
        return assignment
    now = now_iso()
    db.tds_compliance_assignments.update_one({"assignment_id": assignment_id, "status": {"$ne": "LOCKED"}}, {"$set": {"status": "LOCKED", "locked_at": now, "updated_at": now}, "$inc": {"version": 1}})
    locked = db.tds_compliance_assignments.find_one({"assignment_id": assignment_id}, NO_ID)
    _audit_tds_compliance("ASSIGNMENT_LOCKED", locked, old_value={"status": assignment.get("status")}, new_value={"status": "LOCKED"})
    return locked


def _tds_assignment_or_404(assignment_id: str) -> dict:
    assignment = db.tds_compliance_assignments.find_one({"assignment_id": assignment_id, "workflow": TDS_COMPLIANCE_WORKFLOW}, NO_ID)
    if not assignment:
        raise HTTPException(404, "TDS Compliance assignment not found.")
    return assignment


def _tds_workspace_access(principal: Principal, assignment: dict) -> None:
    """Apply the existing TDS boundary before exposing assignment evidence."""
    try:
        authorize(principal, "view_results", organization_id=assignment["organization_id"], client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


def _tds_workspace_stage(name: str, *, available: bool, complete: bool = False, blocked: bool = False) -> str:
    if blocked:
        return "BLOCKED"
    if complete:
        return "COMPLETED"
    return "IN_PROGRESS" if available else "NOT_STARTED"


@app.get("/api/tds-compliance/assignments/{assignment_id}/workspace")
def tds_compliance_assignment_workspace(assignment_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    """CA-facing, read-only summary of persisted TDS Compliance evidence.

    This endpoint deliberately derives status and counts from immutable versions
    and runs. It does not create workflow state or infer unavailable evidence.
    """
    assignment = _tds_assignment_or_404(assignment_id)
    _tds_workspace_access(principal, assignment)
    latest_ledger = db.tds_compliance_ledger_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    ledger_rows = list(db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": latest_ledger["ledger_version_id"]}, NO_ID)) if latest_ledger else []
    latest_calculation = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("created_at", DESCENDING)])
    calculation_rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": latest_calculation["calculation_id"]}, NO_ID)) if latest_calculation else []
    latest_interest = db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "deposit_run_id": {"$exists": True}}, NO_ID, sort=[("created_at", DESCENDING)])
    interest_rows = list(db.tds_compliance_interest_results.find({"assignment_id": assignment_id, "interest_run_id": latest_interest["interest_run_id"]}, NO_ID)) if latest_interest else []
    latest_evidence = db.tds_compliance_deposit_evidence_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    latest_government_evidence = db.tds_compliance_government_evidence_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version_number", DESCENDING)])
    latest_government_verification = db.tds_compliance_government_evidence_verifications.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version_number", DESCENDING)])
    latest_deposit = db.tds_compliance_deposit_runs.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("created_at", DESCENDING)])
    deposit_rows = list(db.tds_compliance_deposit_results.find({"assignment_id": assignment_id, "deposit_run_id": latest_deposit["deposit_run_id"]}, NO_ID)) if latest_deposit else []

    classified = sum(str(row.get("payment_nature_status") or "").upper() == "CLASSIFIED" for row in ledger_rows)
    calculation_review = [row for row in calculation_rows if row.get("calculation_status") != "CALCULATED" or row.get("compliance_status") not in {"COMPLIANT", "NOT_DETERMINABLE"}]
    interest_review = [row for row in interest_rows if row.get("overall_status") not in {"NO_INTEREST_INDICATED"}]
    deposit_review = [row for row in deposit_rows if row.get("overall_status") not in {"FULLY_DEPOSIT_COMPLIANT", "COMPLIANT", "NOT_APPLICABLE"}]
    exceptions = {
        "classification_review": sum(str(row.get("payment_nature_status") or "").upper() != "CLASSIFIED" for row in ledger_rows),
        "rule_not_found": sum(row.get("reason_code") in {"RULE_NOT_FOUND", "RULE_AMBIGUOUS", "SECTION_CONFLICT"} for row in calculation_rows),
        "insufficient_data": sum(row.get("calculation_status") == "INSUFFICIENT_DATA" for row in calculation_rows),
        "tds_difference": sum(row.get("deduction_status") in {"SHORT_DEDUCTION", "EXCESS_DEDUCTION", "NOT_DEDUCTED"} for row in calculation_rows),
        "deposit_issue": len(deposit_review),
        "interest_issue": len(interest_review),
        "reconciliation_issue": None,
        "return_issue": None,
        "missing_evidence": sum(1 for item in (latest_ledger, latest_evidence) if not item),
    }
    total_exceptions = sum(value for value in exceptions.values() if isinstance(value, int))
    locked = assignment.get("status") == "LOCKED"
    stages = [
        {"key": "assignment", "label": "Assignment", "status": "COMPLETED", "detail": "Assignment created"},
        {"key": "data_collection", "label": "Data collection", "status": _tds_workspace_stage("data", available=bool(latest_ledger), complete=bool(latest_ledger)), "detail": "Payment ledger committed" if latest_ledger else "Payment ledger not committed"},
        {"key": "classification", "label": "Classification", "status": _tds_workspace_stage("classification", available=bool(latest_ledger), complete=bool(ledger_rows) and classified == len(ledger_rows), blocked=bool(latest_ledger) and classified != len(ledger_rows)), "detail": f"{classified} of {len(ledger_rows)} ledger rows classified" if latest_ledger else "Awaiting payment ledger"},
        {"key": "tds_review", "label": "TDS review", "status": _tds_workspace_stage("calculation", available=bool(latest_calculation), complete=bool(latest_calculation) and not calculation_review, blocked=bool(latest_calculation) and bool(calculation_review)), "detail": "Calculation run available" if latest_calculation else "Awaiting calculation run"},
        {"key": "deposit", "label": "Deposit", "status": _tds_workspace_stage("deposit", available=bool(latest_deposit), complete=bool(latest_deposit) and not deposit_review, blocked=bool(latest_deposit) and bool(deposit_review)), "detail": "Deposit compliance run available" if latest_deposit else "Awaiting deposit evidence and review"},
        {"key": "interest", "label": "Interest Compliance", "status": _tds_workspace_stage("interest", available=bool(latest_interest), complete=bool(latest_interest) and not interest_review, blocked=bool(latest_interest) and bool(interest_review)), "detail": "Persisted calculation and deposit run assessed" if latest_interest else "Awaiting a persisted deposit-compliance run"},
        {"key": "government_evidence", "label": "Government Evidence", "status": "COMPLETED" if latest_government_evidence else "NOT_STARTED", "detail": f"Government Tax Credit Summary v{latest_government_evidence.get('version_number')} committed" if latest_government_evidence else "Government Tax Credit Summary not committed"},
        {"key": "government_evidence_verification", "label": "Government Evidence Verification", "status": "COMPLETED" if latest_government_verification else "NOT_STARTED", "detail": f"Control verification v{latest_government_verification.get('version_number')}: {latest_government_verification.get('verification_status')}" if latest_government_verification else "Awaiting committed government summary evidence and a verification run"},
        {"key": "return_compliance", "label": "Return compliance", "status": "NOT_STARTED", "detail": "No return-evidence workflow is persisted yet"},
        {"key": "ca_review", "label": "CA review", "status": "COMPLETED" if locked else "IN_PROGRESS" if latest_calculation else "NOT_STARTED", "detail": "Assignment is locked" if locked else "CA review remains open"},
        {"key": "approval", "label": "Approval", "status": "COMPLETED" if locked else "NOT_STARTED", "detail": "Lock is the available final assignment action"},
        {"key": "lock", "label": "Lock", "status": "COMPLETED" if locked else "NOT_STARTED", "detail": "Locked" if locked else "Assignment remains editable"},
    ]
    sources = [
        {"key": "payment_ledger", "label": "Payment Ledger", "uploaded": bool(latest_ledger), "validated": bool(latest_ledger), "committed": bool(latest_ledger), "version": latest_ledger.get("version") if latest_ledger else None, "row_count": len(ledger_rows), "validation_issues": (latest_ledger or {}).get("validation_summary", {}).get("invalid_rows"), "detail": (latest_ledger or {}).get("source_file_name")},
        {"key": "deductee_master", "label": "Deductee / Vendor Master", "uploaded": False, "validated": False, "committed": False, "version": None, "row_count": None, "validation_issues": None, "detail": "No committed deductee-master source is stored"},
        {"key": "deposit_evidence", "label": "Deposit / Challan Evidence", "uploaded": bool(latest_evidence), "validated": bool(latest_evidence), "committed": bool(latest_evidence), "version": latest_evidence.get("version") if latest_evidence else None, "row_count": db.tds_compliance_deposit_evidence_rows.count_documents({"assignment_id": assignment_id, "evidence_version_id": latest_evidence["evidence_version_id"]}) if latest_evidence else 0, "validation_issues": (latest_evidence or {}).get("validation_summary", {}).get("invalid_rows"), "detail": (latest_evidence or {}).get("source_file_name")},
        {"key": "government_evidence", "label": "Government Tax Credit Summary", "uploaded": bool(latest_government_evidence), "validated": bool(latest_government_evidence), "committed": bool(latest_government_evidence), "version": latest_government_evidence.get("version_number") if latest_government_evidence else None, "row_count": latest_government_evidence.get("row_count") if latest_government_evidence else 0, "validation_issues": (latest_government_evidence or {}).get("invalid_row_count"), "detail": (latest_government_evidence or {}).get("original_filename") or "No government summary source is stored"},
        {"key": "return_evidence", "label": "Return-related Evidence", "uploaded": False, "validated": False, "committed": False, "version": None, "row_count": None, "validation_issues": None, "detail": "No return-evidence source is stored"},
    ]
    return {"assignment": assignment, "overview": {"workflow_stage": next((stage["key"] for stage in stages if stage["status"] != "COMPLETED"), "lock"), "overall_review_state": "LOCKED" if locked else "REVIEW_REQUIRED" if total_exceptions else "IN_PROGRESS", "last_updated": max([value for value in [assignment.get("updated_at"), (latest_ledger or {}).get("committed_at"), (latest_calculation or {}).get("created_at"), (latest_interest or {}).get("created_at"), (latest_deposit or {}).get("created_at"), (latest_government_verification or {}).get("created_at")] if value], default=assignment.get("created_at")), "lock_status": "LOCKED" if locked else "EDITABLE", "transactions_uploaded": len(ledger_rows), "transactions_classified": classified, "transactions_calculated": len(calculation_rows), "exceptions": total_exceptions, "deposit_issues": len(deposit_review), "interest_issues": len(interest_review), "reconciliation_issues": None, "return_issues": None}, "stages": stages, "sources": sources, "exceptions": exceptions, "runs": {"ledger_version_id": (latest_ledger or {}).get("ledger_version_id"), "calculation_id": (latest_calculation or {}).get("calculation_id"), "interest_run_id": (latest_interest or {}).get("interest_run_id"), "deposit_run_id": (latest_deposit or {}).get("deposit_run_id"), "government_evidence_verification_id": (latest_government_verification or {}).get("verification_id")}}


def _payment_ledger_upload_or_404(upload_id: str) -> tuple[dict, bytes]:
    upload = db.uploads.find_one({"upload_id": upload_id, "kind": PAYMENT_LEDGER}, NO_ID)
    if not upload:
        raise HTTPException(404, "Payment ledger upload not found.")
    path = UPLOAD_DIR / f"{upload_id}{upload['ext']}"
    if not path.exists():
        raise HTTPException(410, "Payment ledger source file is no longer available.")
    return upload, path.read_bytes()


def _payment_ledger_preview(assignment_id: str, upload_id: str) -> tuple[dict, dict]:
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; a payment ledger cannot be added.")
    upload, content = _payment_ledger_upload_or_404(upload_id)
    report = validate_payment_ledger(upload["filename"], content, assignment)
    for row in report["rows"]:
        row["source_file_id"] = upload_id
    report["file"].update({"upload_id": upload_id, "size": upload.get("size"), "uploaded_at": upload.get("uploaded_at")})
    return assignment, report


@app.post("/api/tds-compliance/assignments/{assignment_id}/payment-ledger/validate")
def validate_tds_payment_ledger(assignment_id: str, body: PaymentLedgerUploadBody):
    _tds_compliance_security_boundary()
    _, report = _payment_ledger_preview(assignment_id, body.upload_id)
    # Preview is not persisted: a corrected source always produces a fresh result.
    return {**report, "preview_rows": report["rows"][:100]}


@app.post("/api/tds-compliance/assignments/{assignment_id}/payment-ledger/commit")
def commit_tds_payment_ledger(assignment_id: str, body: PaymentLedgerUploadBody):
    _tds_compliance_security_boundary()
    assignment, report = _payment_ledger_preview(assignment_id, body.upload_id)
    if not report["can_commit"]:
        raise HTTPException(422, "Payment ledger cannot be committed until structural validation and column mapping issues are resolved.")
    latest = db.tds_compliance_ledger_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    version = int(latest.get("version", 0)) + 1 if latest else 1
    ledger_version_id = f"TDCL-{uuid.uuid4().hex[:14].upper()}"
    snapshot = {"ledger_version_id": ledger_version_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "version": version, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "source_file_id": body.upload_id, "source_file_name": report["file"]["filename"], "source_file_metadata": report["file"], "mapped_headers": report["mapped_headers"], "unmapped_headers": report["unmapped_headers"], "mapping_warnings": report["mapping_warnings"], "validation_summary": report["summary"], "committed_at": now_iso(), "committed_by": None}
    db.tds_compliance_ledger_versions.insert_one(dict(snapshot))
    documents = [{**row, "ledger_version_id": ledger_version_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "committed_at": snapshot["committed_at"]} for row in report["rows"]]
    if documents:
        db.tds_compliance_ledger_rows.insert_many(documents)
    _audit_tds_compliance("PAYMENT_LEDGER_COMMITTED", assignment, entity_id=ledger_version_id, new_value={"version": version, "source_file_id": body.upload_id, "row_count": len(documents)})
    return {"ledger_version": snapshot, "summary": report["summary"]}


@app.get("/api/tds-compliance/assignments/{assignment_id}/payment-ledger")
def get_tds_payment_ledger(assignment_id: str):
    _tds_compliance_security_boundary()
    _tds_assignment_or_404(assignment_id)
    version = db.tds_compliance_ledger_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    if not version:
        return {"ledger_version": None, "items": []}
    rows = list(db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"]}, NO_ID).sort("source_row_number", ASCENDING))
    return {"ledger_version": version, "items": rows}


@app.get("/api/tds-compliance/assignments/{assignment_id}/payment-ledger/summary")
def get_tds_payment_ledger_summary(assignment_id: str):
    _tds_compliance_security_boundary()
    _tds_assignment_or_404(assignment_id)
    version = db.tds_compliance_ledger_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    return {"ledger_version": version, "summary": version.get("validation_summary") if version else None}


def _ledger_for_calculation(assignment_id: str, ledger_version_id: str | None) -> tuple[dict, list[dict]]:
    query = {"assignment_id": assignment_id}
    if ledger_version_id:
        query["ledger_version_id"] = ledger_version_id
    version = db.tds_compliance_ledger_versions.find_one(query, NO_ID, sort=[("version", DESCENDING)])
    if not version:
        raise HTTPException(409, "Commit a payment ledger before running a statutory calculation.")
    rows = list(db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"]}, NO_ID).sort("source_row_number", ASCENDING))
    return version, rows


def _calculation_summary(results: list[dict]) -> dict:
    statuses = ("COMPLIANT", "SHORT_DEDUCTION", "EXCESS_DEDUCTION", "REVIEW_REQUIRED", "NOT_DETERMINABLE")
    return {"total_transactions": len(results), "applicable_transactions": sum(item.get("calculation_status") == "CALCULATED" for item in results), "usable_transactions": sum(item.get("calculation_status") not in {"INSUFFICIENT_DATA", "LAW_NOT_DETERMINABLE"} for item in results), "transactions_requiring_review": sum(item.get("compliance_status") == "REVIEW_REQUIRED" for item in results), "missing_payment_nature": sum(item.get("payment_nature_status") == "NOT_DETERMINABLE" for item in results), "missing_dates": sum(item.get("calculation_status") == "LAW_NOT_DETERMINABLE" for item in results), "missing_pan": sum(item.get("pan_status") == "NOT_PROVIDED" for item in results), "missing_actual_tds": sum(item.get("deduction_status") == "ACTUAL_TDS_NOT_PROVIDED" for item in results), "status_counts": {status: sum(item.get("compliance_status") == status for item in results) for status in statuses}}


def _tds_transaction_totals(rows: list[dict]) -> dict:
    def total(field: str):
        values = [item.get(field) for item in rows if item.get(field) is not None]
        return round(sum(float(value) for value in values), 2) if values else None
    return {"total_payment_amount": total("current_amount"), "expected_tds": total("expected_tds"), "actual_tds": total("tds_deducted"), "difference": total("tds_deduction_difference")}


def _tds_transaction_status(row: dict) -> str:
    if row.get("calculation_status") == "RULE_NOT_FOUND": return "RULE_NOT_FOUND"
    if row.get("calculation_status") == "INSUFFICIENT_DATA": return "INSUFFICIENT_DATA"
    if row.get("calculation_status") in {"REVIEW_REQUIRED", "LAW_NOT_DETERMINABLE"}: return "REVIEW_REQUIRED"
    if row.get("deduction_status") == "ACTUAL_TDS_NOT_PROVIDED": return "ACTUAL_TDS_NOT_PROVIDED"
    if row.get("deduction_status") in {"SHORT_DEDUCTION", "EXCESS_DEDUCTION"}: return row["deduction_status"]
    if row.get("deduction_status") == "COMPLIANT": return "MATCHED"
    return row.get("compliance_status") or "NOT_DETERMINABLE"


@app.get("/api/tds-compliance/assignments/{assignment_id}/transactions")
def list_tds_compliance_transactions(assignment_id: str, calculation_id: str | None = None, principal: Principal = Depends(_tds_compliance_security_boundary)):
    """Return immutable calculation evidence enriched by separate CA review state."""
    assignment = _tds_assignment_or_404(assignment_id)
    _tds_workspace_access(principal, assignment)
    calculation = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, **({"calculation_id": calculation_id} if calculation_id else {})}, NO_ID, sort=[("created_at", DESCENDING)])
    if not calculation:
        return {"assignment_id": assignment_id, "calculation": None, "summary": {"total_transactions": 0, "classified": 0, "classification_review": 0, "calculated": 0, "matched": 0, "short_deduction": 0, "excess_deduction": 0, "rule_not_found": 0, "insufficient_data": 0, "actual_tds_missing": 0, "other_review": 0, **_tds_transaction_totals([])}, "items": []}
    rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation["calculation_id"]}, NO_ID).sort("transaction_id", ASCENDING))
    ledger = {item.get("transaction_id"): item for item in db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": calculation["ledger_version_id"]}, NO_ID)}
    reviews = {item.get("transaction_id"): item for item in db.tds_compliance_transaction_reviews.find({"assignment_id": assignment_id, "ledger_version_id": calculation["ledger_version_id"]}, NO_ID)}
    items = []
    for result in rows:
        source = ledger.get(result.get("transaction_id"), {})
        item = {**result, "transaction_status": _tds_transaction_status(result), "review": reviews.get(result.get("transaction_id")), "payment_date": source.get("payment_date"), "credit_date": source.get("credit_date"), "amount": source.get("amount"), "invoice_number": source.get("invoice_number"), "source_file_name": source.get("source_file_name"), "source_row_number": source.get("source_row_number"), "ledger_version_id": calculation["ledger_version_id"]}
        items.append(item)
    summary = {"total_transactions": len(items), "classified": sum(item.get("payment_nature_status") == "CLASSIFIED" for item in items), "classification_review": sum(item.get("payment_nature_status") != "CLASSIFIED" for item in items), "calculated": sum(item.get("calculation_status") == "CALCULATED" for item in items), "matched": sum(item["transaction_status"] == "MATCHED" for item in items), "short_deduction": sum(item["transaction_status"] == "SHORT_DEDUCTION" for item in items), "excess_deduction": sum(item["transaction_status"] == "EXCESS_DEDUCTION" for item in items), "rule_not_found": sum(item["transaction_status"] == "RULE_NOT_FOUND" for item in items), "insufficient_data": sum(item["transaction_status"] == "INSUFFICIENT_DATA" for item in items), "actual_tds_missing": sum(item["transaction_status"] == "ACTUAL_TDS_NOT_PROVIDED" for item in items), "other_review": sum(item["transaction_status"] == "REVIEW_REQUIRED" for item in items), **_tds_transaction_totals(items)}
    return _tds_api_value({"assignment_id": assignment_id, "calculation": calculation, "summary": summary, "items": items})


@app.post("/api/tds-compliance/assignments/{assignment_id}/transactions/{transaction_id}/review")
def review_tds_compliance_transaction(assignment_id: str, transaction_id: str, body: TdsTransactionReviewBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _tds_assignment_or_404(assignment_id)
    try:
        authorize(principal, "reconcile", organization_id=assignment["organization_id"], client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    version, rows = _ledger_for_calculation(assignment_id, None)
    if not any(row.get("transaction_id") == transaction_id for row in rows):
        raise HTTPException(404, "Payment ledger transaction not found.")
    existing = db.tds_compliance_transaction_reviews.find_one({"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"], "transaction_id": transaction_id}, NO_ID)
    old_state = (existing or {}).get("review_status")
    action = body.action
    status = "IN_REVIEW" if action in {"REVIEW", "COMMENT", "REOPEN"} else "RESOLVED"
    now = now_iso()
    event = {"action": action, "reason": body.reason, "comment": body.comment, "previous_status": old_state, "new_status": status, "actor": principal.user_id, "timestamp": now}
    record = {**(existing or {}), "review_id": (existing or {}).get("review_id", f"TDCR-{uuid.uuid4().hex[:12].upper()}"), "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment.get("client_id"), "ledger_version_id": version["ledger_version_id"], "transaction_id": transaction_id, "review_status": status, "reason": body.reason or (existing or {}).get("reason"), "updated_at": now, "updated_by": principal.user_id, "history": [*((existing or {}).get("history") or []), event]}
    if not existing: record["created_at"] = now; record["created_by"] = principal.user_id
    db.tds_compliance_transaction_reviews.replace_one({"assignment_id": assignment_id, "ledger_version_id": version["ledger_version_id"], "transaction_id": transaction_id}, record, upsert=True)
    _audit_tds_compliance(f"TRANSACTION_REVIEW_{action}", assignment, entity_id=transaction_id, old_value={"review_status": old_state}, new_value=event)
    return record


def _calculation_preview(assignment_id: str, ledger_version_id: str | None) -> tuple[dict, dict, list[dict]]:
    assignment = _tds_assignment_or_404(assignment_id)
    version, rows = _ledger_for_calculation(assignment_id, ledger_version_id)
    rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW}, NO_ID))
    classified_rows = _apply_classification_mappings(assignment, rows, version.get("ledger_version_id"))
    results = freeze_calculation_results(calculate_ledger_transactions(classified_rows, rules, assignment_id=assignment_id))
    return assignment, version, results


@app.post("/api/tds-compliance/assignments/{assignment_id}/calculations/preview")
def preview_tds_calculation(assignment_id: str, body: CalculationPreviewBody):
    _tds_compliance_security_boundary()
    _, version, results = _calculation_preview(assignment_id, body.ledger_version_id)
    return {"ledger_version": version, "summary": _calculation_summary(results), "items": results}


@app.post("/api/tds-compliance/assignments/{assignment_id}/calculations/run")
def run_tds_calculation(assignment_id: str, body: CalculationPreviewBody):
    _tds_compliance_security_boundary()
    assignment, version, results = _calculation_preview(assignment_id, body.ledger_version_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; a new calculation run cannot be created.")
    calculation_id = f"TDCC-{uuid.uuid4().hex[:14].upper()}"
    created_at = now_iso()
    rule_versions = sorted({f"{item.get('rule_id')}:{item.get('rule_version')}" for item in results if item.get("rule_id")})
    run = {"calculation_id": calculation_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "ledger_version_id": version["ledger_version_id"], "rule_versions": rule_versions, "created_at": created_at, "created_by": None, "summary": _calculation_summary(results)}
    db.tds_compliance_calculation_runs.insert_one(dict(run))
    if results:
        db.tds_compliance_calculation_results.insert_many([
            _tds_mongo_snapshot({**item, "calculation_id": calculation_id, "ledger_version_id": version["ledger_version_id"], "workflow": TDS_COMPLIANCE_WORKFLOW, "calculated_at": created_at})
            for item in results
        ])
    _audit_tds_compliance("CALCULATION_RUN", assignment, entity_id=calculation_id, new_value={"ledger_version_id": version["ledger_version_id"], "rule_versions": rule_versions, "transaction_count": len(results)})
    return {"calculation": run, "items": results}


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculations")
def list_tds_calculations(assignment_id: str):
    _tds_compliance_security_boundary()
    _tds_assignment_or_404(assignment_id)
    return {"items": list(db.tds_compliance_calculation_runs.find({"assignment_id": assignment_id}, NO_ID).sort("created_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculations/{calculation_id}")
def get_tds_calculation(assignment_id: str, calculation_id: str):
    _tds_compliance_security_boundary()
    run = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "calculation_id": calculation_id}, NO_ID)
    if not run:
        raise HTTPException(404, "TDS Compliance calculation run not found.")
    return _tds_api_value({"calculation": run, "items": list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation_id}, NO_ID).sort("transaction_id", ASCENDING))})


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculations/{calculation_id}/summary")
def get_tds_calculation_summary(assignment_id: str, calculation_id: str):
    _tds_compliance_security_boundary()
    run = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "calculation_id": calculation_id}, NO_ID)
    if not run:
        raise HTTPException(404, "TDS Compliance calculation run not found.")
    return {"calculation": run, "summary": run.get("summary")}


def _interest_summary(items: list[dict]) -> dict:
    review_states = {"REVIEW_REQUIRED", "RULE_NOT_FOUND", "RULE_AMBIGUOUS", "INTEREST_NOT_DETERMINABLE", "DEDUCTION_DATE_NOT_PROVIDED", "DEPOSIT_DATE_NOT_PROVIDED", "INVALID_DATE_SEQUENCE", "ACTUAL_TDS_NOT_PROVIDED"}
    return {"transactions_reviewed": len(items), "deduction_delays": sum(item.get("deduction_status") == "DEDUCTION_DELAY" for item in items), "deposit_delays": sum(item.get("deposit_status") == "DEPOSIT_DELAY" for item in items), "review_required": sum(item.get("overall_status") in review_states for item in items), "total_interest": round(sum(float(item.get("total_interest") or 0) for item in items if item.get("total_interest") is not None), 2)}


def _interest_preview(assignment_id: str, calculation_id: str) -> tuple[dict, dict, list[dict]]:
    assignment = _tds_assignment_or_404(assignment_id)
    calculation = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "calculation_id": calculation_id}, NO_ID)
    if not calculation:
        raise HTTPException(404, "Phase 3 calculation run not found for this assignment.")
    phase3_rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation_id}, NO_ID))
    ledger_rows = list(db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": calculation["ledger_version_id"]}, NO_ID))
    interest_rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}}, NO_ID))
    return assignment, calculation, calculate_interest_compliance(phase3_rows, ledger_rows, interest_rules, assignment_id=assignment_id, calculation_id=calculation_id, ledger_version_id=calculation["ledger_version_id"])


@app.post("/api/tds-compliance/assignments/{assignment_id}/interest/preview")
def preview_interest_compliance(assignment_id: str, body: InterestPreviewBody):
    _tds_compliance_security_boundary()
    assignment, calculation, items = _interest_preview(assignment_id, body.calculation_id)
    _audit_tds_compliance("INTEREST_PREVIEWED", assignment, entity_id=body.calculation_id, new_value={"transaction_count": len(items)})
    return {"calculation": calculation, "summary": _interest_summary(items), "items": items}


@app.post("/api/tds-compliance/assignments/{assignment_id}/interest/run")
def run_interest_compliance(assignment_id: str, body: InterestPreviewBody):
    _tds_compliance_security_boundary()
    assignment, calculation, items = _interest_preview(assignment_id, body.calculation_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; an interest run cannot be created.")
    interest_run_id, created_at = f"TDCI-{uuid.uuid4().hex[:14].upper()}", now_iso()
    _audit_tds_compliance("INTEREST_RUN_STARTED", assignment, entity_id=interest_run_id, new_value={"calculation_id": body.calculation_id})
    run = {"interest_run_id": interest_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "calculation_id": body.calculation_id, "ledger_version_id": calculation["ledger_version_id"], "created_at": created_at, "created_by": None, "summary": _interest_summary(items)}
    try:
        db.tds_compliance_interest_runs.insert_one(dict(run))
        if items:
            db.tds_compliance_interest_results.insert_many([{**item, "interest_run_id": interest_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "calculated_at": created_at} for item in freeze_calculation_results(items)])
    except Exception:
        _audit_tds_compliance("INTEREST_RUN_FAILED", assignment, entity_id=interest_run_id, new_value={"calculation_id": body.calculation_id})
        raise
    _audit_tds_compliance("INTEREST_RUN_COMPLETED", assignment, entity_id=interest_run_id, new_value={"calculation_id": body.calculation_id, "transaction_count": len(items)})
    return {"interest_run": run, "items": items}


@app.get("/api/tds-compliance/assignments/{assignment_id}/interest/history")
def interest_history(assignment_id: str):
    _tds_compliance_security_boundary()
    _tds_assignment_or_404(assignment_id)
    return {"items": list(db.tds_compliance_interest_runs.find({"assignment_id": assignment_id}, NO_ID).sort("created_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/interest/{interest_run_id}")
def interest_run_detail(assignment_id: str, interest_run_id: str):
    _tds_compliance_security_boundary()
    run = db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id}, NO_ID)
    if not run: raise HTTPException(404, "Interest compliance run not found.")
    return {"interest_run": run, "items": list(db.tds_compliance_interest_results.find({"assignment_id": assignment_id, "interest_run_id": interest_run_id}, NO_ID).sort("transaction_id", ASCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/interest/{interest_run_id}/summary")
def interest_run_summary(assignment_id: str, interest_run_id: str):
    _tds_compliance_security_boundary()
    run = db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id}, NO_ID)
    if not run: raise HTTPException(404, "Interest compliance run not found.")
    return {"interest_run": run, "summary": run.get("summary")}


# ---------- Phase 5: interest from persisted Phase 2 + Phase 4 snapshots ----------
def _interest_compliance_summary(items: list[dict]) -> dict:
    review_states = {"INTEREST_REVIEW_REQUIRED", "DATE_NOT_DETERMINABLE", "POLICY_NOT_CONFIGURED", "INSUFFICIENT_DATA", "INVALID_DATE_ORDER"}
    return {"transactions_reviewed": len(items), "interest_due_count": sum(item.get("overall_status") == "INTEREST_DUE" for item in items), "no_interest_indicated_count": sum(item.get("overall_status") == "NO_INTEREST_INDICATED" for item in items), "review_required_count": sum(item.get("overall_status") in review_states for item in items), "late_deduction_count": sum(item.get("deduction_status") == "LATE_DEDUCTION" for item in items), "late_deposit_count": sum(item.get("deposit_status") == "LATE_DEPOSIT" for item in items), "total_interest": round(sum(float(item.get("total_interest") or 0) for item in items if item.get("total_interest") is not None), 2)}


def _interest_compliance_preview(assignment_id: str, body: InterestComplianceBody) -> tuple[dict, dict, dict, list[dict]]:
    assignment = _tds_assignment_or_404(assignment_id)
    calculation = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "calculation_id": body.calculation_id}, NO_ID)
    if not calculation:
        raise HTTPException(404, "Persisted calculation snapshot not found for this assignment.")
    deposit_run = db.tds_compliance_deposit_runs.find_one({"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "deposit_run_id": body.deposit_run_id, "calculation_id": body.calculation_id}, NO_ID)
    if not deposit_run:
        raise HTTPException(404, "Persisted deposit-compliance run not found for this calculation and assignment.")
    calculation_rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": body.calculation_id}, NO_ID))
    deposit_rows = list(db.tds_compliance_deposit_results.find({"assignment_id": assignment_id, "deposit_run_id": body.deposit_run_id}, NO_ID))
    rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}, "$and": [{"$or": [{"organization_id": assignment.get("organization_id")}, {"organization_id": None}, {"scope": "GLOBAL"}]}, {"$or": [{"client_id": assignment.get("client_id")}, {"client_id": None}, {"scope": "GLOBAL"}]}]}, NO_ID))
    if not (getattr(db, "name", None) == "26as_reconciliation_tds_e2e" and os.environ.get("TDS_PROVISIONAL_UAT_E2E") == "true"):
        rules = [rule for rule in rules if rule.get("policy_status") != "PROVISIONAL_UAT"]
    items = calculate_interest_from_deposit_results(calculation_rows, deposit_rows, rules, assignment_id=assignment_id, calculation_id=body.calculation_id, deposit_run_id=body.deposit_run_id, ledger_version_id=calculation.get("ledger_version_id"))
    return assignment, calculation, deposit_run, items


def _phase5_access(request: Request, assignment_id: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    assignment = _tds_assignment_or_404(assignment_id)
    permission = "view_results" if request.method == "GET" else "manage_rules" if "/policies" in request.url.path else "reconcile"
    try:
        authorize(principal, permission, organization_id=assignment.get("organization_id"), client_id=assignment.get("client_id"))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return principal


@app.post("/api/tds-compliance/assignments/{assignment_id}/interest-compliance/preview")
def preview_persisted_interest_compliance(assignment_id: str, body: InterestComplianceBody, principal: Principal = Depends(_phase5_access)):
    assignment, calculation, deposit_run, items = _interest_compliance_preview(assignment_id, body)
    _audit_tds_compliance("INTEREST_COMPLIANCE_PREVIEWED", assignment, entity_id=body.deposit_run_id, new_value={"calculation_id": body.calculation_id, "deposit_run_id": body.deposit_run_id, "transactions": len(items)}, actor_id=principal.user_id)
    return {"calculation": calculation, "deposit_run": deposit_run, "summary": _interest_compliance_summary(items), "items": items}


@app.post("/api/tds-compliance/assignments/{assignment_id}/interest-compliance/run")
def run_persisted_interest_compliance(assignment_id: str, body: InterestComplianceBody, principal: Principal = Depends(_phase5_access)):
    assignment, calculation, deposit_run, items = _interest_compliance_preview(assignment_id, body)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; an interest-compliance run cannot be created.")
    interest_run_id, created_at = f"TDCI-{uuid.uuid4().hex[:14].upper()}", now_iso()
    rule_versions = sorted({f"{item.get('deduction_rule_snapshot', {}).get('rule_id')}:{item.get('deduction_rule_snapshot', {}).get('rule_version')}" for item in items if item.get("deduction_rule_snapshot")} | {f"{item.get('deposit_rule_snapshot', {}).get('rule_id')}:{item.get('deposit_rule_snapshot', {}).get('rule_version')}" for item in items if item.get("deposit_rule_snapshot")})
    due_date_policy_versions = sorted({f"{item.get('due_date_policy_snapshot', {}).get('rule_id') or item.get('due_date_policy_snapshot', {}).get('policy_id')}:{item.get('due_date_policy_snapshot', {}).get('rule_version') or item.get('due_date_policy_snapshot', {}).get('policy_version')}" for item in items if item.get("due_date_policy_snapshot")})
    run = {"interest_run_id": interest_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "financial_year": assignment.get("financial_year"), "quarter": assignment.get("quarter"), "calculation_id": body.calculation_id, "deposit_run_id": body.deposit_run_id, "deposit_evidence_version_id": deposit_run.get("evidence_version_id"), "ledger_version_id": calculation.get("ledger_version_id"), "policy_versions": rule_versions, "due_date_policy_versions": due_date_policy_versions, "created_at": created_at, "created_by": principal.user_id, "summary": _interest_compliance_summary(items)}
    db.tds_compliance_interest_runs.insert_one(dict(run))
    if items:
        db.tds_compliance_interest_results.insert_many([{**item, "interest_run_id": interest_run_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "calculated_at": created_at} for item in freeze_calculation_results(items)])
    _audit_tds_compliance("INTEREST_COMPLIANCE_RUN_CREATED", assignment, entity_id=interest_run_id, new_value={"calculation_id": body.calculation_id, "deposit_run_id": body.deposit_run_id, "policy_versions": rule_versions, "transactions": len(items)}, actor_id=principal.user_id)
    return {"interest_run": run, "items": items, "summary": run["summary"]}


@app.get("/api/tds-compliance/assignments/{assignment_id}/interest-compliance")
def list_persisted_interest_compliance(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    return {"items": list(db.tds_compliance_interest_runs.find({"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "deposit_run_id": {"$exists": True}}, NO_ID).sort("created_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/interest-compliance/{interest_run_id}")
def persisted_interest_compliance_detail(assignment_id: str, interest_run_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    run = db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "interest_run_id": interest_run_id, "deposit_run_id": {"$exists": True}}, NO_ID)
    if not run:
        raise HTTPException(404, "Interest-compliance run not found.")
    rows = list(db.tds_compliance_interest_results.find({"assignment_id": assignment_id, "interest_run_id": interest_run_id}, NO_ID).sort("transaction_id", ASCENDING))
    reviews = {row.get("transaction_id"): row for row in db.tds_compliance_interest_reviews.find({"assignment_id": assignment_id, "interest_run_id": interest_run_id}, NO_ID)}
    return {"interest_run": run, "items": [{**row, "review": reviews.get(row.get("transaction_id"))} for row in rows]}


@app.post("/api/tds-compliance/assignments/{assignment_id}/interest-compliance/{interest_run_id}/transactions/{transaction_id}/review")
def review_persisted_interest_compliance(assignment_id: str, interest_run_id: str, transaction_id: str, body: GovernmentEvidenceReviewBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if not db.tds_compliance_interest_results.find_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id, "transaction_id": transaction_id}, NO_ID):
        raise HTTPException(404, "Interest-compliance result not found.")
    previous = db.tds_compliance_interest_reviews.find_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id, "transaction_id": transaction_id}, NO_ID)
    now, next_state = now_iso(), "RESOLVED" if body.action == "RESOLVE" else "OPEN"
    event = {"action": body.action, "reason": body.reason.strip() if body.reason else None, "comment": body.comment.strip() if body.comment else None, "actor_id": principal.user_id, "timestamp": now, "previous_state": (previous or {}).get("state")}
    db.tds_compliance_interest_reviews.update_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id, "transaction_id": transaction_id}, {"$set": {"workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "state": next_state, "updated_at": now, "updated_by": principal.user_id}, "$push": {"history": event}, "$setOnInsert": {"created_at": now, "created_by": principal.user_id}}, upsert=True)
    review = db.tds_compliance_interest_reviews.find_one({"assignment_id": assignment_id, "interest_run_id": interest_run_id, "transaction_id": transaction_id}, NO_ID)
    _audit_tds_compliance(f"INTEREST_COMPLIANCE_{body.action}", assignment, entity_id=f"{interest_run_id}:{transaction_id}", old_value={"state": event["previous_state"]}, new_value={"state": next_state, "reason": event["reason"], "comment": event["comment"]}, actor_id=principal.user_id)
    return {"review": review}


class Phase5PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str
    assignment_id: str
    financial_years: list[str] = Field(min_length=1)
    effective_from: date
    effective_to: date
    priority: StrictInt
    active: StrictBool
    status: str
    authoritative_identifier_types: list[str] = Field(min_length=1)
    permitted_relationship_types: list[str] = Field(min_length=1)
    allocation_policy: str
    ambiguity_policy: str
    policy_status: str | None = None
    ca_approved: StrictBool | None = None
    environment: str | None = None
    assumption_status: str | None = None
    source_gap: StrictBool | None = None
    ca_review_required: StrictBool | None = None

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.status not in {"DRAFT", "APPROVED", "ACTIVE", "RETIRED", "PROVISIONAL_UAT"}:
            raise ValueError("Unsupported approval state.")
        if self.active and self.status not in {"APPROVED", "ACTIVE", "PROVISIONAL_UAT"}:
            raise ValueError("An active policy must be approved.")
        if self.status == "PROVISIONAL_UAT" and not (self.policy_status == "PROVISIONAL_UAT" and self.ca_approved is False and self.environment == "isolated_e2e_only" and self.assumption_status == "PROVISIONAL" and self.source_gap is True and self.ca_review_required is True):
            raise ValueError("Provisional policy metadata is incomplete.")
        if self.effective_from > self.effective_to:
            raise ValueError("Effective dates are reversed.")
        for fy in self.financial_years:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", fy) or int(fy[-2:]) != (int(fy[:4]) + 1) % 100:
                raise ValueError("Invalid financial year.")
            start = int(fy[:4])
            if self.effective_to < date(start, 4, 1) or self.effective_from > date(start + 1, 3, 31):
                raise ValueError("Effective dates do not overlap the financial year.")
        if set(self.authoritative_identifier_types) != {"EXACT_TRANSACTION_REFERENCE"} or not set(self.permitted_relationship_types) <= {"ONE_TO_ONE", "ONE_TO_MANY"}:
            raise ValueError("Unsupported Phase 5 matching configuration.")
        if self.allocation_policy != "EXACT_REFERENCE_ONLY" or self.ambiguity_policy != "REVIEW_REQUIRED":
            raise ValueError("Unsupported Phase 5 allocation or ambiguity configuration.")
        return self


@app.post("/api/tds-compliance/assignments/{assignment_id}/policies")
def create_phase5_policy(assignment_id: str, body: Phase5PolicyBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if body.assignment_id != assignment_id or body.client_id != assignment.get("client_id") or assignment.get("financial_year") not in body.financial_years:
        raise HTTPException(422, "Policy scope and FY must match the assignment.")
    policy = {**body.model_dump(mode="json"), "policy_id": f"TDCP-{uuid.uuid4().hex}", "policy_version": "1", "workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": assignment.get("organization_id"), "created_at": now_iso(), "created_by": principal.user_id}
    db.tds_compliance_phase5_policies.insert_one(dict(policy))
    return policy


@app.get("/api/tds-compliance/assignments/{assignment_id}/policies")
def list_phase5_policies(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    return {"items": list(db.tds_compliance_phase5_policies.find({"assignment_id": assignment_id, "client_id": assignment["client_id"], "organization_id": assignment["organization_id"]}, NO_ID))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/policies/{policy_id}")
def get_phase5_policy(assignment_id: str, policy_id: str, principal: Principal = Depends(_phase5_access)):
    policy = next((p for p in list_phase5_policies(assignment_id, principal)["items"] if p["policy_id"] == policy_id), None)
    if not policy:
        raise HTTPException(404, "Phase 5 policy not found.")
    return policy


def _phase5_totals(items, assignment_id, version_id):
    from decimal import Decimal

    def total(field):
        values = [row.get(field) for row in items]
        return float(sum((Decimal(str(v)) for v in values), Decimal(0))) if values and all(v is not None for v in values) else None

    return {
        "liability_count": len(items),
        "evidence_count": db.tds_compliance_deposit_evidence_rows.count_documents({"assignment_id": assignment_id, "evidence_version_id": version_id}) if version_id else 0,
        "matched_count": sum(r.get("deposit_status") == "DEPOSIT_MATCHED" for r in items),
        "reconciled_count": sum(r.get("relationship_status") == "ESTABLISHED" for r in items),
        "review_required_count": sum(r.get("overall_status") == "REVIEW_REQUIRED" for r in items),
        "ambiguous_count": sum(r.get("reason_code") in {"PHASE5_POLICY_AMBIGUOUS", "AMBIGUOUS_DEPOSIT_MAPPING"} for r in items),
        "unmatched_count": sum(r.get("deposit_status") == "MISSING_DEPOSIT_EVIDENCE" for r in items),
        "invalid_date_sequence_count": sum(r.get("timeliness_status") == "INVALID_DATE_SEQUENCE" for r in items),
        "compliant_count": sum(r.get("overall_status") == "FULLY_DEPOSIT_COMPLIANT" for r in items),
        "exception_count": sum("EXCEPTION" in r.get("overall_status", "") for r in items),
        "not_determinable_count": sum(r.get("overall_status") == "NOT_DETERMINABLE" or r.get("deposit_status") == "DEPOSIT_NOT_DETERMINABLE" for r in items),
        "expected_tds_total": total("expected_tds"), "actual_tds_deducted_total": total("actual_tds_deducted"), "deposited_tds_total": total("deposited_tds"),
    }


# ---------- TDS Calculator: adapter over the Phase 3/4 configured engines ----------
def _calculator_serializable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _calculator_serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_calculator_serializable(item) for item in value]
    return value


def _calculator_result(assignment: dict, body: TdsCalculatorBody) -> dict:
    payload = body.model_dump()
    payload.update({"assignment_id": assignment["assignment_id"], "client_id": assignment["client_id"], "organization_id": assignment["organization_id"], "transaction_id": f"TDC-{uuid.uuid4().hex[:12].upper()}"})
    statutory_rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$exists": False}}, NO_ID))
    interest_rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}}, NO_ID))
    result = _calculator_serializable(calculate_tds_calculator(payload, statutory_rules, interest_rules, assignment_id=assignment["assignment_id"]))
    result["assignment_id"] = assignment["assignment_id"]
    result["client_id"] = assignment["client_id"]
    result["financial_year"] = body.financial_year
    return result


def _rule_eligibility(transaction: dict, rule: dict) -> list[str]:
    """Explain configuration eligibility; this never selects or calculates a rate."""
    law = determine_governing_tds_law(credit_date=transaction.get("credit_date"), payment_date=transaction.get("payment_date"), financial_year=transaction.get("financial_year"))
    nature = classify_payment_nature(transaction)
    reasons = []
    scope = str(rule.get("scope") or "GLOBAL").upper()
    if scope != "GLOBAL" and rule.get("organization_id") != transaction.get("organization_id"): reasons.append("ORGANIZATION_SCOPE_MISMATCH")
    if scope in {"CLIENT", "ASSIGNMENT"} and rule.get("client_id") != transaction.get("client_id"): reasons.append("CLIENT_SCOPE_MISMATCH")
    if scope == "ASSIGNMENT" and rule.get("assignment_id") != transaction.get("assignment_id"): reasons.append("ASSIGNMENT_SCOPE_MISMATCH")
    if not rule.get("active"): reasons.append("RULE_NOT_ACTIVE")
    if rule.get("lifecycle") not in {"APPROVED", "ACTIVE"}: reasons.append("RULE_NOT_APPROVED")
    if law.get("status") != "LAW_DETERMINED": reasons.append("LAW_NOT_DETERMINABLE")
    elif (rule.get("governing_act") or rule.get("act")) != law.get("act"): reasons.append("GOVERNING_ACT_MISMATCH")
    event = law.get("effective_event_date")
    if event and not (str(rule.get("effective_from") or "0000-01-01") <= event <= str(rule.get("effective_to") or "9999-12-31")): reasons.append("EFFECTIVE_DATE_MISMATCH")
    if rule.get("financial_year") not in {None, "", transaction.get("financial_year"), transaction.get("tax_year")}: reasons.append("FINANCIAL_YEAR_MISMATCH")
    if rule.get("payment_nature") and rule.get("payment_nature") != nature.get("payment_nature"): reasons.append("PAYMENT_NATURE_MISMATCH")
    if rule.get("deductee_type") and rule.get("deductee_type") != transaction.get("deductee_type"): reasons.append("DEDUCTEE_TYPE_MISMATCH")
    return reasons


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculator/rules")
def calculator_rules(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "financial_year": assignment["financial_year"]}, NO_ID))
    return {"items": rules}


@app.post("/api/tds-compliance/assignments/{assignment_id}/calculator/rules/eligibility")
def explain_calculator_rule_eligibility(assignment_id: str, body: TdsCalculatorBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("financial_year") != body.financial_year:
        raise HTTPException(422, "Calculator financial year must match the assignment.")
    transaction = {**body.model_dump(), "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment["client_id"]}
    rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$exists": False}}, NO_ID))
    items = [{"rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "lifecycle": rule.get("lifecycle"), "active": bool(rule.get("active")), "eligible": not _rule_eligibility(transaction, rule), "reasons": _rule_eligibility(transaction, rule)} for rule in rules]
    return {"items": items}


def _verified_payment_nature_options(financial_year: str, principal: Principal) -> dict:
    """Return only active, provenance-backed statutory catalog families."""
    try:
        authorize(principal, "view_results", organization_id=principal.organization_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    rules = list(db.tds_compliance_rules.find({
        "workflow": TDS_COMPLIANCE_WORKFLOW,
        "financial_year": financial_year,
        "interest_type": {"$exists": False},
        "active": True,
        "lifecycle": "ACTIVE",
        "source_verified_at": {"$exists": True, "$ne": None},
        "$or": [
            {"scope": "GLOBAL"},
            {"organization_id": principal.organization_id, "verification_status": "VERIFIED"},
        ],
    }, NO_ID))
    options: dict[str, dict] = {}
    for rule in rules:
        nature = rule.get("payment_nature")
        if not nature:
            continue
        entry = options.setdefault(nature, {
            "code": str(nature).upper(),
            "payment_nature": nature,
            "label": str(nature).replace("_", " ").title(),
            "requires": sorted(set(rule.get("required_inputs") or [])),
            "provision": rule.get("section_reference") or rule.get("historical_section_reference"),
            "governing_act": rule.get("governing_act"),
            "provisions": [],
        })
        provision = rule.get("section_reference") or rule.get("historical_section_reference")
        if provision and provision not in entry["provisions"]:
            entry["provisions"].append(provision)
    return {"items": sorted(options.values(), key=lambda item: item["label"])}


@app.get("/api/tds-compliance/calculator/payment-natures")
def standalone_calculator_payment_natures(financial_year: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    """Dynamic payment-nature catalog for the standalone calculator."""
    return _verified_payment_nature_options(financial_year, principal)


@app.get("/api/tds-compliance/calculator/options")
def standalone_calculator_options(financial_year: str, principal: Principal = Depends(_tds_compliance_security_boundary)):
    """Compatibility alias for the dynamic verified payment-nature catalog."""
    return _verified_payment_nature_options(financial_year, principal)


@app.post("/api/tds-compliance/calculator/preview")
def preview_standalone_tds_calculator(body: TdsCalculatorBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    """Preview the configured catalogue without requiring a ledger assignment.

    This is deliberately preview-only: audited saving remains assignment-scoped.
    """
    payload = body.model_dump()
    try:
        authorize(principal, "view_results", organization_id=principal.organization_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    payload.update({"assignment_id": "STANDALONE-CALCULATOR", "organization_id": principal.organization_id, "transaction_id": f"TDC-{uuid.uuid4().hex[:12].upper()}"})
    statutory_rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$exists": False}}, NO_ID))
    interest_rules = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "interest_type": {"$in": ["DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"]}}, NO_ID))
    return _calculator_serializable(calculate_tds_calculator(payload, statutory_rules, interest_rules, assignment_id="STANDALONE-CALCULATOR"))


@app.post("/api/tds-compliance/assignments/{assignment_id}/calculator/preview")
def preview_tds_calculator(assignment_id: str, body: TdsCalculatorBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("financial_year") != body.financial_year:
        raise HTTPException(422, "Calculator financial year must match the assignment.")
    return _calculator_result(assignment, body)


@app.post("/api/tds-compliance/assignments/{assignment_id}/calculator/calculate")
def save_tds_calculator(assignment_id: str, body: TdsCalculatorBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; a calculator record cannot be saved.")
    if assignment.get("financial_year") != body.financial_year:
        raise HTTPException(422, "Calculator financial year must match the assignment.")
    result = _calculator_result(assignment, body)
    record = {"calculator_run_id": f"TDCK-{uuid.uuid4().hex[:14].upper()}", "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], "created_at": now_iso(), "created_by": principal.user_id, "input": freeze_calculation_results([body.model_dump()])[0], "result": freeze_calculation_results([result])[0], "rule_snapshot": freeze_calculation_results([result.get("rule_snapshot")])[0] if result.get("rule_snapshot") else None}
    db.tds_calculator_runs.insert_one(dict(record))
    _audit_tds_compliance("TDS_CALCULATOR_SAVED", assignment, entity_id=record["calculator_run_id"], new_value={"status": result.get("status"), "rule_id": result.get("rule_id")})
    return record


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculator/history")
def calculator_history(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    return {"items": list(db.tds_calculator_runs.find({"assignment_id": assignment_id}, NO_ID).sort("created_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/calculator/{calculator_run_id}")
def calculator_detail(assignment_id: str, calculator_run_id: str, principal: Principal = Depends(_phase5_access)):
    record = db.tds_calculator_runs.find_one({"assignment_id": assignment_id, "calculator_run_id": calculator_run_id}, NO_ID)
    if not record:
        raise HTTPException(404, "Calculator record not found.")
    return record


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence/upload")
async def upload_phase5_evidence(assignment_id: str, file: UploadFile = File(...), principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked.")
    doc = await upload(file=file, kind=TDS_DEPOSIT_EVIDENCE)
    scope = {k: assignment[k] for k in ("assignment_id", "organization_id", "client_id")}
    db.uploads.update_one({"upload_id": doc["upload_id"]}, {"$set": scope})
    return {**doc, **scope}


# ---------- Phase 3A: government tax-credit summary evidence ----------
@app.post("/api/tds-compliance/assignments/{assignment_id}/government-evidence/upload")
async def upload_government_evidence(assignment_id: str, file: UploadFile = File(...), principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(400, "Government Tax Credit Summary supports CSV, XLSX and XLS tables.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    upload_id = uuid.uuid4().hex
    (UPLOAD_DIR / f"{upload_id}{ext}").write_bytes(content)
    report = validate_government_tax_credit_summary(file.filename or "", content, assignment)
    doc = {"upload_id": upload_id, "kind": "government_tax_credit_summary", "filename": file.filename, "size": len(content), "ext": ext, "uploaded_at": now_iso(), "uploaded_by": principal.user_id, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "row_count": report["summary"]["total_rows"], "columns_mapped": report["canonical_mappings"], "columns_unmapped": report["unmapped_columns"], "readable": not bool(report["validation_messages"] and not report["rows"]), "diagnostic": "; ".join(report["validation_messages"])}
    db.uploads.insert_one(dict(doc))
    _audit_tds_compliance("GOVERNMENT_EVIDENCE_UPLOADED", assignment, entity_id=upload_id, new_value={"source_type": GOVERNMENT_SUMMARY_SOURCE_TYPE, "filename": file.filename, "rows": report["summary"]["total_rows"]}, actor_id=principal.user_id)
    return doc


def _government_evidence_preview(assignment_id: str, upload_id: str) -> tuple[dict, dict, dict]:
    assignment = _tds_assignment_or_404(assignment_id)
    upload = db.uploads.find_one({"upload_id": upload_id, "kind": "government_tax_credit_summary", "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id")}, NO_ID)
    if not upload:
        raise HTTPException(404, "Government evidence upload not found.")
    path = UPLOAD_DIR / f"{upload_id}{upload['ext']}"
    if not path.exists():
        raise HTTPException(410, "Government evidence source file is no longer available.")
    report = validate_government_tax_credit_summary(upload["filename"], path.read_bytes(), assignment)
    report["file"].update({"upload_id": upload_id, "uploaded_at": upload.get("uploaded_at"), "uploaded_by": upload.get("uploaded_by")})
    for row in report["rows"]:
        row["source_file_id"] = upload_id
    return assignment, upload, report


@app.post("/api/tds-compliance/assignments/{assignment_id}/government-evidence/validate")
def validate_government_evidence(assignment_id: str, body: GovernmentEvidenceUploadBody, principal: Principal = Depends(_phase5_access)):
    assignment, _, report = _government_evidence_preview(assignment_id, body.upload_id)
    _audit_tds_compliance("GOVERNMENT_EVIDENCE_VALIDATED", assignment, entity_id=body.upload_id, new_value={"source_type": GOVERNMENT_SUMMARY_SOURCE_TYPE, "summary": report["summary"]}, actor_id=principal.user_id)
    return {**report, "preview_rows": report["rows"][:100], "status": "VALIDATED" if report["can_commit"] else "UPLOADED"}


@app.post("/api/tds-compliance/assignments/{assignment_id}/government-evidence/commit")
def commit_government_evidence(assignment_id: str, body: GovernmentEvidenceUploadBody, principal: Principal = Depends(_phase5_access)):
    assignment, upload, report = _government_evidence_preview(assignment_id, body.upload_id)
    if not report["can_commit"]:
        raise HTTPException(422, "Government evidence cannot be committed until header and row validation issues are resolved.")
    latest = db.tds_compliance_government_evidence_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version_number", DESCENDING)])
    version_number = int(latest.get("version_number", 0)) + 1 if latest else 1
    evidence_version_id, now = f"TDCG-{uuid.uuid4().hex[:14].upper()}", now_iso()
    snapshot = {"evidence_version_id": evidence_version_id, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "financial_year": report["file"].get("financial_year"), "quarter": report["file"].get("quarter"), "source_type": GOVERNMENT_SUMMARY_SOURCE_TYPE, "original_filename": upload["filename"], "source_file_id": body.upload_id, "file_hash": None, "uploaded_by": upload.get("uploaded_by"), "uploaded_at": upload.get("uploaded_at"), "validation_status": "VALIDATED", "commit_status": "COMMITTED", "version_number": version_number, "row_count": report["summary"]["total_rows"], "valid_row_count": report["summary"]["valid_rows"], "warning_row_count": report["summary"]["warning_rows"], "invalid_row_count": report["summary"]["invalid_rows"], "validation_summary": report["summary"], "provenance": report["file"], "created_at": now, "committed_at": now, "committed_by": principal.user_id}
    rows = [{**row, "evidence_version_id": evidence_version_id, "created_at": now, "committed_at": now} for row in freeze_calculation_results(report["rows"])]
    try:
        if rows:
            db.tds_compliance_government_evidence_rows.insert_many(rows)
        db.tds_compliance_government_evidence_versions.insert_one(dict(snapshot))
    except Exception as exc:
        db.tds_compliance_government_evidence_rows.delete_many({"evidence_version_id": evidence_version_id})
        raise HTTPException(409, "Government evidence version could not be committed.") from exc
    _audit_tds_compliance("GOVERNMENT_EVIDENCE_COMMITTED", assignment, entity_id=evidence_version_id, new_value={"version_number": version_number, "source_type": GOVERNMENT_SUMMARY_SOURCE_TYPE, "rows": len(rows)}, actor_id=principal.user_id)
    return {"evidence_version": snapshot}


@app.get("/api/tds-compliance/assignments/{assignment_id}/government-evidence")
def list_government_evidence(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    return {"items": list(db.tds_compliance_government_evidence_versions.find({"assignment_id": assignment_id}, NO_ID).sort("version_number", DESCENDING))}


# ---------- Phase 3B: summary-level government evidence verification ----------
def _government_verification_context(assignment_id: str, body: GovernmentEvidenceVerificationBody) -> tuple[dict, dict, list[dict], dict | None, list[dict]]:
    """Resolve immutable sources for a control-total verification only.

    This deliberately returns evidence rows and persisted calculation rows as
    separate sets.  No source rows are matched, allocated, or altered.
    """
    assignment = _tds_assignment_or_404(assignment_id)
    evidence_query = {"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "commit_status": "COMMITTED"}
    if body.evidence_version_id:
        evidence_query["evidence_version_id"] = body.evidence_version_id
    evidence = db.tds_compliance_government_evidence_versions.find_one(evidence_query, NO_ID, sort=[("version_number", DESCENDING)])
    if not evidence:
        raise HTTPException(404, "No committed Government Tax Credit Summary evidence version was found for this assignment.")
    # Phase 3A row snapshots deliberately preserve source fields only.  Their
    # immutable version is the scoped parent, so fetch them by assignment plus
    # selected evidence version rather than requiring copied scope fields.
    evidence_rows = list(db.tds_compliance_government_evidence_rows.find({"assignment_id": assignment_id, "evidence_version_id": evidence["evidence_version_id"]}, NO_ID).sort("source_row_number", ASCENDING))

    calculation_query = {"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id")}
    if body.calculation_id:
        calculation_query["calculation_id"] = body.calculation_id
    calculation = db.tds_compliance_calculation_runs.find_one(calculation_query, NO_ID, sort=[("created_at", DESCENDING)])
    # Older immutable result rows predate scope copies on each row.  The
    # calculation run is therefore the scoped parent; results are selected by
    # its assignment and immutable calculation ID, never across assignments.
    calculation_rows = list(db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation["calculation_id"]}, NO_ID)) if calculation else []
    return assignment, evidence, evidence_rows, calculation, calculation_rows


@app.post("/api/tds-compliance/assignments/{assignment_id}/government-evidence/verify")
def verify_government_evidence(assignment_id: str, body: GovernmentEvidenceVerificationBody, principal: Principal = Depends(_phase5_access)):
    assignment, evidence, evidence_rows, calculation, calculation_rows = _government_verification_context(assignment_id, body)
    control = verify_government_summary(assignment=assignment, evidence_version=evidence, evidence_rows=evidence_rows, calculation_run=calculation, calculation_rows=calculation_rows)
    latest = db.tds_compliance_government_evidence_verifications.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version_number", DESCENDING)])
    now, verification_id = now_iso(), f"TDCV-{uuid.uuid4().hex[:14].upper()}"
    verification = {"verification_id": verification_id, "verification_run_id": verification_id, "version_number": int((latest or {}).get("version_number", 0)) + 1, "workflow": TDS_COMPLIANCE_WORKFLOW, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "financial_year": assignment.get("financial_year"), "quarter": assignment.get("quarter"), "government_evidence_version_id": evidence["evidence_version_id"], "calculation_run_id": (calculation or {}).get("calculation_id"), "created_at": now, "created_by": principal.user_id, **freeze_calculation_results([control])[0]}
    db.tds_compliance_government_evidence_verifications.insert_one(dict(verification))
    _audit_tds_compliance("GOVERNMENT_EVIDENCE_VERIFICATION_CREATED", assignment, entity_id=verification_id, new_value={"government_evidence_version_id": evidence["evidence_version_id"], "calculation_run_id": verification["calculation_run_id"], "verification_status": verification["verification_status"]}, actor_id=principal.user_id)
    return {"verification": verification}


@app.get("/api/tds-compliance/assignments/{assignment_id}/government-evidence/verification")
def list_government_evidence_verifications(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    query = {"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id")}
    return {"items": list(db.tds_compliance_government_evidence_verifications.find(query, NO_ID).sort("version_number", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/government-evidence/verification/{verification_id}")
def get_government_evidence_verification(assignment_id: str, verification_id: str, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    query = {"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "verification_id": verification_id}
    verification = db.tds_compliance_government_evidence_verifications.find_one(query, NO_ID)
    if not verification:
        raise HTTPException(404, "Government evidence verification not found.")
    review = db.tds_compliance_government_evidence_reviews.find_one({"assignment_id": assignment_id, "verification_id": verification_id}, NO_ID)
    return {"verification": verification, "review": review}


@app.post("/api/tds-compliance/assignments/{assignment_id}/government-evidence/verification/{verification_id}/review")
def review_government_evidence_verification(assignment_id: str, verification_id: str, body: GovernmentEvidenceReviewBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    verification = db.tds_compliance_government_evidence_verifications.find_one({"assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "verification_id": verification_id}, NO_ID)
    if not verification:
        raise HTTPException(404, "Government evidence verification not found.")
    previous = db.tds_compliance_government_evidence_reviews.find_one({"assignment_id": assignment_id, "verification_id": verification_id}, NO_ID)
    now = now_iso()
    next_state = "RESOLVED" if body.action == "RESOLVE" else "OPEN"
    event = {"action": body.action, "reason": body.reason.strip() if body.reason else None, "comment": body.comment.strip() if body.comment else None, "actor_id": principal.user_id, "timestamp": now, "previous_state": (previous or {}).get("state")}
    db.tds_compliance_government_evidence_reviews.update_one({"assignment_id": assignment_id, "verification_id": verification_id}, {"$set": {"workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "state": next_state, "updated_at": now, "updated_by": principal.user_id}, "$push": {"history": event}, "$setOnInsert": {"created_at": now, "created_by": principal.user_id}}, upsert=True)
    review = db.tds_compliance_government_evidence_reviews.find_one({"assignment_id": assignment_id, "verification_id": verification_id}, NO_ID)
    _audit_tds_compliance(f"GOVERNMENT_EVIDENCE_VERIFICATION_{body.action}", assignment, entity_id=verification_id, old_value={"state": event["previous_state"]}, new_value={"state": next_state, "reason": event["reason"], "comment": event["comment"]}, actor_id=principal.user_id)
    return {"review": review}


@app.get("/api/tds-compliance/assignments/{assignment_id}/government-evidence/{evidence_version_id}")
def get_government_evidence(assignment_id: str, evidence_version_id: str, principal: Principal = Depends(_phase5_access)):
    query = {"assignment_id": assignment_id, "evidence_version_id": evidence_version_id}
    version = db.tds_compliance_government_evidence_versions.find_one(query, NO_ID)
    if not version:
        raise HTTPException(404, "Government evidence version not found.")
    return {"evidence_version": version, "items": list(db.tds_compliance_government_evidence_rows.find(query, NO_ID).sort("source_row_number", ASCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence")
def list_phase5_evidence_versions(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    return {"items": list(db.tds_compliance_deposit_evidence_versions.find({"assignment_id": assignment_id}, NO_ID).sort("version", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence/{evidence_version_id}")
def get_phase5_evidence(assignment_id: str, evidence_version_id: str, principal: Principal = Depends(_phase5_access)):
    query = {"assignment_id": assignment_id, "evidence_version_id": evidence_version_id}
    version = db.tds_compliance_deposit_evidence_versions.find_one(query, NO_ID)
    if not version:
        raise HTTPException(404, "Evidence version not found.")
    return {"evidence_version": version, "items": list(db.tds_compliance_deposit_evidence_rows.find(query, NO_ID))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/{deposit_run_id}/summary")
def get_phase5_summary(assignment_id: str, deposit_run_id: str, principal: Principal = Depends(_phase5_access)):
    summary = db.tds_compliance_deposit_summaries.find_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id}, NO_ID)
    if not summary:
        raise HTTPException(404, "Deposit compliance summary not found.")
    return {"summary": summary}


# ---------- Phase 5: source-provided deposit / challan evidence ----------
@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence/upload")
async def upload_tds_deposit_evidence(assignment_id: str, file: UploadFile = File(...), principal: Principal = Depends(_phase5_access)):
    """Store a deposit/challan source under its TDS assignment before validation.

    Generic uploads have no assignment context, so they cannot safely be used
    as deposit evidence.  This endpoint does not validate, commit or alter a
    calculation snapshot.
    """
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked; deposit evidence cannot be added.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(400, "Deposit/challan evidence must be CSV, XLSX or XLS.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    upload_id = uuid.uuid4().hex
    (UPLOAD_DIR / f"{upload_id}{ext}").write_bytes(content)
    report = validate_deposit_evidence(file.filename or "deposit-evidence", content, assignment)
    upload = {"upload_id": upload_id, "kind": TDS_DEPOSIT_EVIDENCE, "filename": file.filename, "size": len(content), "ext": ext, "uploaded_at": now_iso(), "uploaded_by": principal.user_id, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "row_count": report["summary"]["total_rows"], "columns_mapped": report["mapped_headers"], "columns_unmapped": report["unmapped_headers"], "readable": bool(report["mapped_headers"]), "diagnostic": "; ".join(report["mapping_warnings"]) or None}
    db.uploads.insert_one(dict(upload))
    _audit_tds_compliance("DEPOSIT_EVIDENCE_UPLOADED", assignment, entity_id=upload_id, new_value={"rows": upload["row_count"]}, actor_id=principal.user_id)
    return upload


def _deposit_upload(upload_id: str) -> tuple[dict, bytes]:
    upload = db.uploads.find_one({"upload_id": upload_id, "kind": TDS_DEPOSIT_EVIDENCE}, NO_ID)
    if not upload: raise HTTPException(404, "TDS deposit evidence upload not found.")
    path = UPLOAD_DIR / f"{upload_id}{upload['ext']}"
    if not path.exists(): raise HTTPException(410, "Deposit evidence source file is no longer available.")
    return upload, path.read_bytes()


def _deposit_evidence_preview(assignment_id: str, upload_id: str) -> tuple[dict, dict]:
    assignment = _tds_assignment_or_404(assignment_id)
    if assignment.get("status") == "LOCKED": raise HTTPException(409, "This assignment is locked; evidence cannot be added.")
    upload, content = _deposit_upload(upload_id)
    if any(upload.get(k) != assignment.get(k) for k in ("assignment_id", "client_id", "organization_id")):
        raise HTTPException(404, "Deposit evidence upload not found.")
    report = validate_deposit_evidence(upload["filename"], content, assignment)
    for row in report["rows"]: row["source_file_id"] = upload_id
    report["file"].update({"upload_id": upload_id}); return assignment, report


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence/validate")
def validate_tds_deposit_evidence(assignment_id: str, body: DepositEvidenceBody, principal: Principal = Depends(_phase5_access)):
    if not body.upload_id: raise HTTPException(422, "upload_id is required.")
    assignment, report = _deposit_evidence_preview(assignment_id, body.upload_id)
    _audit_tds_compliance("DEPOSIT_EVIDENCE_VALIDATED", assignment, entity_id=body.upload_id, new_value={"rows": len(report["rows"])})
    return {**report, "preview_rows": report["rows"][:100]}


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-evidence/commit")
def commit_tds_deposit_evidence(assignment_id: str, body: DepositEvidenceBody, principal: Principal = Depends(_phase5_access)):
    if not body.upload_id: raise HTTPException(422, "upload_id is required.")
    assignment, report = _deposit_evidence_preview(assignment_id, body.upload_id)
    if not report["can_commit"]: raise HTTPException(422, "Deposit evidence cannot be committed until its structural validation issues are resolved.")
    latest = db.tds_compliance_deposit_evidence_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)])
    version = int((latest or {}).get("version", 0)) + 1
    evidence_version_id = f"TDCE-{uuid.uuid4().hex[:14].upper()}"; now = now_iso()
    snapshot = {"evidence_version_id": evidence_version_id, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "workflow": TDS_COMPLIANCE_WORKFLOW, "version": version, "source_file_id": body.upload_id, "source_file_name": report["file"]["filename"], "validation_summary": report["summary"], "committed_at": now}
    db.tds_compliance_deposit_evidence_versions.insert_one(dict(snapshot))
    if report["rows"]: db.tds_compliance_deposit_evidence_rows.insert_many([{**row, "evidence_version_id": evidence_version_id, "committed_at": now} for row in freeze_calculation_results(report["rows"])])
    _audit_tds_compliance("DEPOSIT_EVIDENCE_COMMITTED", assignment, entity_id=evidence_version_id, new_value={"source_file_id": body.upload_id, "rows": len(report["rows"])})
    return {"evidence_version": snapshot}


def _deposit_compliance_preview(assignment_id: str, body: DepositEvidenceBody):
    assignment = _tds_assignment_or_404(assignment_id)
    if not body.calculation_id: raise HTTPException(422, "calculation_id is required.")
    calc = db.tds_compliance_calculation_runs.find_one({"assignment_id": assignment_id, "calculation_id": body.calculation_id}, NO_ID)
    if not calc: raise HTTPException(404, "Phase 3 calculation run not found.")
    version_id = body.evidence_version_id or (db.tds_compliance_deposit_evidence_versions.find_one({"assignment_id": assignment_id}, NO_ID, sort=[("version", DESCENDING)]) or {}).get("evidence_version_id")
    if version_id and not db.tds_compliance_deposit_evidence_versions.find_one({"assignment_id": assignment_id, "evidence_version_id": version_id}):
        raise HTTPException(404, "Evidence version not found.")
    if body.interest_run_id and not db.tds_compliance_interest_runs.find_one({"assignment_id": assignment_id, "interest_run_id": body.interest_run_id, "calculation_id": body.calculation_id}):
        raise HTTPException(404, "Phase 4 run not found for this calculation.")
    evidence = list(db.tds_compliance_deposit_evidence_rows.find({"assignment_id": assignment_id, "evidence_version_id": version_id}, NO_ID)) if version_id else []
    phase3_query = {"assignment_id": assignment_id, "calculation_id": body.calculation_id}
    # A calculation ID identifies the run, whose ledger version is frozen at
    # creation.  Keep the source results in that same version so a stale or
    # malformed result record cannot enter this Phase 4 decision.
    if calc.get("ledger_version_id"):
        phase3_query["ledger_version_id"] = calc["ledger_version_id"]
    phase3_rows = list(db.tds_compliance_calculation_results.find(phase3_query, NO_ID))
    ledger_by_transaction = {row.get("transaction_id"): row for row in db.tds_compliance_ledger_rows.find({"assignment_id": assignment_id, "ledger_version_id": calc.get("ledger_version_id")}, NO_ID)}
    # New Phase 4 snapshots retain source-provided deduction-date context for
    # later Phase 5 use.  Historical records without it remain review-only.
    phase3 = [{**row, "actual_deduction_date": ledger_by_transaction.get(row.get("transaction_id"), {}).get("deduction_date") or row.get("contractor_due_date_context", {}).get("deduction_date"), "deductee_type": row.get("deductee_type") or ledger_by_transaction.get(row.get("transaction_id"), {}).get("deductee_type")} for row in phase3_rows]
    phase4 = list(db.tds_compliance_interest_results.find({"assignment_id": assignment_id, "interest_run_id": body.interest_run_id}, NO_ID)) if body.interest_run_id else []
    policies = list(db.tds_compliance_phase5_policies.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "client_id": assignment.get("client_id"), "organization_id": assignment.get("organization_id"), "$or": [{"assignment_id": assignment_id}, {"assignment_id": None}]}, NO_ID))
    if not (getattr(db, "name", None) == "26as_reconciliation_tds_e2e" and os.environ.get("TDS_PROVISIONAL_UAT_E2E") == "true"):
        policies = [policy for policy in policies if policy.get("policy_status") != "PROVISIONAL_UAT"]
    due_date_policies = list(db.tds_compliance_rules.find({"workflow": TDS_COMPLIANCE_WORKFLOW, "policy_kind": "CONTRACTOR_DEPOSIT_DUE_DATE", "$and": [{"$or": [{"organization_id": assignment.get("organization_id")}, {"organization_id": None}, {"scope": "GLOBAL"}]}, {"$or": [{"client_id": assignment.get("client_id")}, {"client_id": None}, {"scope": "GLOBAL"}]}, {"$or": [{"assignment_id": assignment_id}, {"assignment_id": None}, {"scope": {"$ne": "ASSIGNMENT"}}]}]}, NO_ID))
    if not (getattr(db, "name", None) == "26as_reconciliation_tds_e2e" and os.environ.get("TDS_PROVISIONAL_UAT_E2E") == "true"):
        due_date_policies = [policy for policy in due_date_policies if policy.get("policy_status") != "PROVISIONAL_UAT"]
    relationships = []
    for relationship_id in body.relationship_ids:
        relationship = db.tds_compliance_deposit_relationships.find_one({"assignment_id": assignment_id, "client_id": assignment.get("client_id"), "calculation_id": body.calculation_id, "evidence_version_id": version_id, "relationship_id": relationship_id}, NO_ID)
        if not relationship:
            raise HTTPException(404, "Deposit relationship not found for these inputs.")
        relationships.append(relationship)
    return assignment, calc, version_id, calculate_deposit_compliance(phase3, evidence, phase4, assignment_id=assignment_id, calculation_id=body.calculation_id, evidence_version_id=version_id, policies=policies, due_date_policies=due_date_policies, relationships=relationships)


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/preview")
def preview_deposit_compliance(assignment_id: str, body: DepositEvidenceBody, principal: Principal = Depends(_phase5_access)):
    assignment, calc, version_id, items = _deposit_compliance_preview(assignment_id, body)
    _audit_tds_compliance("DEPOSIT_COMPLIANCE_PREVIEWED", assignment, entity_id=body.calculation_id, new_value={"evidence_version_id": version_id, "rows": len(items)})
    return {"calculation": calc, "evidence_version_id": version_id, "items": items, "summary": _phase5_totals(items, assignment_id, version_id)}


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/run")
def run_deposit_compliance(assignment_id: str, body: DepositEvidenceBody, principal: Principal = Depends(_phase5_access)):
    assignment, calc, version_id, items = _deposit_compliance_preview(assignment_id, body)
    if assignment.get("status") == "LOCKED":
        raise HTTPException(409, "This assignment is locked.")
    run_id, now = f"TDCD-{uuid.uuid4().hex[:14].upper()}", now_iso()
    snapshot = next((item.get("phase5_rule_snapshot") for item in items if item.get("phase5_rule_snapshot")), None)
    run = {"deposit_run_id": run_id, "assignment_id": assignment_id, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "workflow": TDS_COMPLIANCE_WORKFLOW, "calculation_id": body.calculation_id, "interest_run_id": body.interest_run_id, "ledger_version_id": calc.get("ledger_version_id"), "evidence_version_id": version_id, "phase5_rule_snapshot": freeze_calculation_results([snapshot])[0] if snapshot else None, "created_at": now}
    if items: db.tds_compliance_deposit_results.insert_many([{**row, "deposit_run_id": run_id, "created_at": now} for row in freeze_calculation_results(items)])
    relationships = [row for row in items if row.get("relationship_id")]
    if relationships: db.tds_compliance_deposit_relationships.insert_many([{**row, "deposit_run_id": run_id, "run_id": run_id, "client_id": assignment.get("client_id"), "evidence_ids": [e.get("evidence_id") for e in row.get("evidence_entries", [])]} for row in freeze_calculation_results(relationships)])
    summary = {**_phase5_totals(items, assignment_id, version_id), "run_id": run_id, "deposit_run_id": run_id, "assignment_id": assignment_id, "client_id": assignment.get("client_id"), "organization_id": assignment.get("organization_id")}
    db.tds_compliance_deposit_summaries.insert_one(dict(summary))
    db.tds_compliance_deposit_runs.insert_one(dict(run))
    _audit_tds_compliance("DEPOSIT_COMPLIANCE_RUN_CREATED", assignment, entity_id=run_id, new_value={"evidence_version_id": version_id, "rows": len(items)})
    return {"deposit_run": run, "items": items, "summary": {k: v for k, v in summary.items() if k not in {"run_id", "deposit_run_id", "assignment_id", "client_id", "organization_id"}}}


@app.get("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/history")
def deposit_compliance_history(assignment_id: str, principal: Principal = Depends(_phase5_access)):
    _tds_assignment_or_404(assignment_id)
    return {"items": list(db.tds_compliance_deposit_runs.find({"assignment_id": assignment_id}, NO_ID).sort("created_at", DESCENDING))}


@app.get("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/{deposit_run_id}")
def deposit_compliance_detail(assignment_id: str, deposit_run_id: str, principal: Principal = Depends(_phase5_access)):
    run = db.tds_compliance_deposit_runs.find_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id}, NO_ID)
    if not run: raise HTTPException(404, "Deposit compliance run not found.")
    items = list(db.tds_compliance_deposit_results.find({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id}, NO_ID))
    reviews = {item.get("transaction_id"): item for item in db.tds_compliance_deposit_reviews.find({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id}, NO_ID)}
    return {"deposit_run": run, "items": [{**item, "review": reviews.get(item.get("transaction_id"))} for item in items]}


@app.post("/api/tds-compliance/assignments/{assignment_id}/deposit-compliance/{deposit_run_id}/transactions/{transaction_id}/review")
def review_deposit_compliance_result(assignment_id: str, deposit_run_id: str, transaction_id: str, body: GovernmentEvidenceReviewBody, principal: Principal = Depends(_phase5_access)):
    assignment = _tds_assignment_or_404(assignment_id)
    result = db.tds_compliance_deposit_results.find_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id, "transaction_id": transaction_id}, NO_ID)
    if not result:
        raise HTTPException(404, "Deposit compliance result not found.")
    previous = db.tds_compliance_deposit_reviews.find_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id, "transaction_id": transaction_id}, NO_ID)
    now, next_state = now_iso(), "RESOLVED" if body.action == "RESOLVE" else "OPEN"
    event = {"action": body.action, "reason": body.reason.strip() if body.reason else None, "comment": body.comment.strip() if body.comment else None, "actor_id": principal.user_id, "timestamp": now, "previous_state": (previous or {}).get("state")}
    db.tds_compliance_deposit_reviews.update_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id, "transaction_id": transaction_id}, {"$set": {"workflow": TDS_COMPLIANCE_WORKFLOW, "organization_id": assignment.get("organization_id"), "client_id": assignment.get("client_id"), "state": next_state, "updated_at": now, "updated_by": principal.user_id}, "$push": {"history": event}, "$setOnInsert": {"created_at": now, "created_by": principal.user_id}}, upsert=True)
    review = db.tds_compliance_deposit_reviews.find_one({"assignment_id": assignment_id, "deposit_run_id": deposit_run_id, "transaction_id": transaction_id}, NO_ID)
    _audit_tds_compliance(f"DEPOSIT_COMPLIANCE_{body.action}", assignment, entity_id=f"{deposit_run_id}:{transaction_id}", old_value={"state": event["previous_state"]}, new_value={"state": next_state, "reason": event["reason"], "comment": event["comment"]}, actor_id=principal.user_id)
    return {"review": review}


@app.get("/api/schema/{kind}")
def schema(kind: str):
    if kind not in SCHEMAS:
        raise HTTPException(404, "Unknown file kind")
    return {"kind": kind, "label": FILE_LABELS[kind], "columns": schema_description(kind)}


@app.get("/api/templates/{kind}")
def template(kind: str):
    if kind not in SCHEMAS:
        raise HTTPException(404, "Unknown file kind")
    header = ",".join(SCHEMAS[kind].keys())
    body = "\n".join(",".join(r) for r in TEMPLATE_ROWS[kind])
    return Response(f"{header}\n{body}\n", media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'})


# ---------- uploads ----------
@app.post("/api/uploads", status_code=202)
async def upload(file: UploadFile = File(...), kind: str = Form(...)):
    if kind not in FILE_KINDS:
        raise HTTPException(400, "Unknown file kind. Use books, form26as or customer_master.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext or 'unknown'}'. Upload a CSV, XLSX, XLS or 26AS/Form 16A PDF.")
    if ext == ".pdf" and kind != "form26as":
        raise HTTPException(400, "PDF uploads are supported only for 26AS / Form 16A statements. Upload Books and Customer Master as CSV, XLSX or XLS.")
    upload_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{upload_id}{ext}"
    size = 0
    with path.open("wb") as destination:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                destination.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
            destination.write(chunk)
    if not size:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is empty.")
    # Storage and source inspection are separate states.  Reading an XLSX can
    # take minutes for an ERP export, so it must never keep this HTTP request
    # open after the browser has finished transferring the bytes.
    doc = {"upload_id": upload_id, "kind": kind, "filename": file.filename, "size": size, "ext": ext, "uploaded_at": now_iso(),
           "row_count": 0, "columns_mapped": [], "recognized_headers": [], "columns_unmapped": [],
           "semantic_profile": None, "mapping_confirmations": [], "readable": None, "diagnostic": None,
           "upload_status": "STORED", "source_processing_status": "QUEUED", "source_processing_stage": "QUEUED",
           "source_processing_processed_rows": 0, "source_processing_total_rows": None, "source_processing_error": None,
           "validation_status": "NOT_STARTED"}
    db.uploads.insert_one(dict(doc))
    source_processing_jobs.enqueue()
    return doc


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    upload = db.uploads.find_one({"upload_id": upload_id}, NO_ID)
    if not upload:
        raise HTTPException(404, "Upload not found")
    return upload


@app.get("/api/uploads/{upload_id}/semantic-profile")
def get_upload_semantic_profile(upload_id: str):
    upload = db.uploads.find_one({"upload_id": upload_id}, NO_ID)
    if not upload:
        raise HTTPException(404, "Upload not found")
    return {"upload_id": upload_id, "kind": upload["kind"], "semantic_profile": upload.get("semantic_profile") or {"columns": [], "ignored_columns": upload.get("columns_unmapped", []), "accounting_context": "NOT_DETERMINABLE", "warnings": []}, "mapping_confirmations": upload.get("mapping_confirmations", [])}


@app.post("/api/uploads/{upload_id}/semantic-mappings", status_code=201)
def confirm_upload_semantic_mapping(upload_id: str, body: SourceMappingReviewBody, principal: Principal = Depends(_tds_compliance_security_boundary)):
    upload = db.uploads.find_one({"upload_id": upload_id}, NO_ID)
    if not upload:
        raise HTTPException(404, "Upload not found")
    profile = upload.get("semantic_profile") or {}
    known = {str(item.get("source_column")): item for item in profile.get("columns", [])}
    detected = known.get(body.source_column)
    if not detected:
        raise HTTPException(422, "Source column is not part of this upload profile.")
    canonical = body.canonical_field.upper()
    allowed_roles = {"TDS_BOOK_AMOUNT", "TDS_RECEIVABLE", "TDS_PAYABLE", "TDS_DEDUCTED", "WITHHOLDING_TAX", "NOT_A_TDS_FIELD"}
    if body.accounting_role and body.accounting_role.upper() not in allowed_roles:
        raise HTTPException(422, "Unsupported accounting role.")
    # A CA may explicitly confirm an accounting role, but the physical column
    # keeps its independently detected Debit/Credit canonical field.
    if canonical != str(detected.get("canonical_field") or "").upper():
        raise HTTPException(422, "The canonical column mapping is read-only; confirm an accounting role separately.")
    confirmation = {"mapping_status": "CONFIRMED", "source_column": body.source_column, "canonical_field": detected.get("canonical_field"), "accounting_role": body.accounting_role.upper() if body.accounting_role else None, "mapping_method": "CA_CONFIRMED", "confidence": detected.get("confidence"), "evidence": body.evidence, "confirmed_by": principal.user_id, "confirmed_at": now_iso()}
    db.uploads.update_one({"upload_id": upload_id}, {"$push": {"mapping_confirmations": confirmation}})
    return confirmation

@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str):
    doc = db.uploads.find_one({"upload_id": upload_id}, NO_ID)
    if not doc:
        raise HTTPException(404, "Upload not found")
    if db.tds_compliance_ledger_versions.find_one({"source_file_id": upload_id}, NO_ID):
        raise HTTPException(409, "This upload is preserved by a committed TDS Compliance payment-ledger version and cannot be deleted.")
    if db.tds_compliance_government_evidence_versions.find_one({"source_file_id": upload_id}, NO_ID):
        raise HTTPException(409, "This upload is preserved by a committed Government Tax Credit Summary evidence version and cannot be deleted.")
    path = UPLOAD_DIR / f"{upload_id}{doc['ext']}"
    if path.exists():
        path.unlink()
    db.uploads.delete_one({"upload_id": upload_id})
    return {"deleted": upload_id}


def _load_files(ids: dict):
    files = {}
    for kind, upload_id in ids.items():
        if not upload_id:
            continue
        doc = db.uploads.find_one({"upload_id": upload_id}, NO_ID)
        path = UPLOAD_DIR / f"{upload_id}{doc['ext']}" if doc else None
        if not doc or not path.exists():
            raise HTTPException(404, f"{FILE_LABELS[kind]} upload is no longer available. Upload the file again.")
        if doc.get("source_processing_status") != "COMPLETED":
            raise HTTPException(409, f"{FILE_LABELS[kind]} is still being analysed. Wait for source processing to finish before validation.")
        files[kind] = (doc["filename"], path.read_bytes())
    return files


class SourceSet(BaseModel):
    books_upload_id: str | None = None
    form26as_upload_id: str | None = None
    customer_master_upload_id: str | None = None
    tds_receivable_upload_id: str | None = None
    sales_registry_upload_id: str | None = None
    assessee_name: str | None = None
    assessee_pan: str | None = None
    # Present only when a CA starts a new reconciliation from an existing
    # client workspace. It binds the new immutable run to that client instead
    # of deriving a second client from transient form state.
    client_id: str | None = None
    financial_year: str | None = None
    workflow: str = "FULL_RECONCILIATION"


class AIChatRequest(BaseModel):
    run_id: str
    question: str
    result_id: str | None = None
    filters: dict[str, str] | None = None
    conversation_id: str | None = None


AI_CONTEXT_VERSION = 3


def _completed_ai_run(run_id: str):
    run = db.runs.find_one({"run_id": run_id}, NO_ID)
    if not run:
        raise HTTPException(404, "Reconciliation run not found.")
    if run.get("status") != "COMPLETED":
        raise HTTPException(409, "AI analysis is available only after reconciliation has completed.")
    return run


def _ai_context(run: dict):
    """Return the persisted safe context for exactly this run, creating it once."""
    cached = db.ai_contexts.find_one({"run_id": run["run_id"], "version": AI_CONTEXT_VERSION}, NO_ID)
    if cached:
        return cached["context"]
    projection = {field: 1 for field in RESULT_FIELDS}
    projection["_id"] = 0
    rows = list(db.results.find({"run_id": run["run_id"]}, projection).sort("seq", ASCENDING))
    context = build_run_context(run, rows)
    db.ai_contexts.replace_one(
        {"run_id": run["run_id"], "version": AI_CONTEXT_VERSION},
        {"run_id": run["run_id"], "version": AI_CONTEXT_VERSION, "generated_at": now_iso(), "context": context},
        upsert=True,
    )
    return context


def _conversation(run_id: str, conversation_id: str | None):
    if conversation_id:
        conversation = db.ai_conversations.find_one({"conversation_id": conversation_id}, NO_ID)
        if not conversation or conversation.get("run_id") != run_id:
            raise HTTPException(404, "AI conversation was not found for this reconciliation run.")
        return conversation
    conversation = {"conversation_id": uuid.uuid4().hex, "run_id": run_id, "messages": [], "created_at": now_iso()}
    db.ai_conversations.insert_one(conversation.copy())
    return conversation


def _ai_public_context(context: dict):
    summary = context.get("summary") or {}
    return {"client_name": (context.get("run") or {}).get("assessee_name") or "Unnamed assessee", "financial_year": (context.get("run") or {}).get("financial_year"), "result_count": context.get("result_count", summary.get("result_count", 0)), "books_count": summary.get("books_count", 0), "statement_count": summary.get("statement_count", 0)}


def _ids(src: SourceSet):
    # A workflow may be selected after another workflow's files were uploaded.
    # Return only the sources that belong to the selected workflow; hidden
    # uploads must never be inferred or validated as Customer Master inputs.
    if src.workflow == "SALES_TDS_26AS":
        return {"tds_receivable": src.tds_receivable_upload_id, "form26as": src.form26as_upload_id, "sales_registry": src.sales_registry_upload_id}
    if src.workflow == "26AS_ONLY":
        return {"form26as": src.form26as_upload_id}
    return {"books": src.books_upload_id, "form26as": src.form26as_upload_id, "customer_master": src.customer_master_upload_id}


@app.post("/api/validate")
def validate(src: SourceSet):
    return _validate_sources(src)


def _validate_sources(src: SourceSet, progress=None):
    analysis_only = src.workflow == "26AS_ONLY"
    sales_workflow = src.workflow == "SALES_TDS_26AS"
    if not src.form26as_upload_id or (sales_workflow and (not src.tds_receivable_upload_id or not src.sales_registry_upload_id)) or (not analysis_only and not sales_workflow and not src.books_upload_id):
        detail = "26AS is required for analysis." if analysis_only else "TDS Expected / Receivable, 26AS and Sales Registry are all required before validation." if sales_workflow else "Books and 26AS files are both required before validation."
        raise HTTPException(400, detail)
    files = _load_files(_ids(src))
    reports = []
    for kind, (filename, content) in files.items():
        started = perf_counter()
        report, _ = load_and_validate(kind, filename, content, src.financial_year or None, get_settings().get("tds_rules"), src.workflow)
        logger.info("Validation parsed %s '%s' in %.2fs: %s rows, %s columns, %s", kind, filename, perf_counter() - started, report["row_count"], len(report["columns_mapped"]), report["status"])
        reports.append(report)
        if progress:
            progress(sum(item["row_count"] for item in reports))
    overall = "BLOCKED" if any(r["status"] == "BLOCKED" for r in reports) else "READY_WITH_EXCEPTIONS" if any(r["status"] == "READY_WITH_EXCEPTIONS" for r in reports) else "READY_WITH_WARNINGS" if any(r["status"] == "READY_WITH_WARNINGS" for r in reports) else "VALID"
    return {"status": overall, "files": reports, "can_process": overall != "BLOCKED"}


def _inspect_uploaded_source(upload: dict, progress=None):
    path = UPLOAD_DIR / f"{upload['upload_id']}{upload['ext']}"
    if not path.exists():
        raise FileNotFoundError("The stored upload is no longer available.")
    started = perf_counter()
    inspected = inspect_upload_source(upload["kind"], upload["filename"], path.read_bytes(), progress)
    logger.info("Source inspection parsed %s '%s' in %.2fs: %s rows, %s columns", upload["kind"], upload["filename"], perf_counter() - started, inspected["row_count"], len(inspected["columns_mapped"]))
    return inspected


source_processing_jobs = SourceProcessingJobs(db.uploads, _inspect_uploaded_source)
books_validation_jobs = ValidationJobs(db.books_validation_jobs, lambda source, progress: _validate_sources(SourceSet(**source), progress))


@app.on_event("startup")
def start_background_workers():
    source_processing_jobs.start()
    books_validation_jobs.start()


@app.post("/api/books-validation/{job_id}", status_code=202)
def start_books_validation(job_id: str, src: SourceSet):
    if not re.fullmatch(r"[a-zA-Z0-9-]{12,64}", job_id):
        raise HTTPException(400, "Invalid validation ID.")
    if src.workflow != "FULL_RECONCILIATION" or not src.books_upload_id or not src.form26as_upload_id:
        raise HTTPException(400, "Books and 26AS are required for this validation workflow.")
    total = 0
    for kind, upload_id in _ids(src).items():
        if upload_id:
            upload = db.uploads.find_one({"upload_id": upload_id, "kind": kind}, NO_ID)
            if not upload or not (UPLOAD_DIR / f"{upload_id}{upload['ext']}").exists():
                raise HTTPException(404, f"{FILE_LABELS[kind]} upload is no longer available.")
            if upload.get("source_processing_status") != "COMPLETED":
                raise HTTPException(409, f"{FILE_LABELS[kind]} is still being analysed. Wait for source processing to finish before validation.")
            total += upload.get("row_count", 0)
    try:
        return books_validation_jobs.create(job_id, src.model_dump(), total)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/books-validation/{job_id}")
def books_validation_status(job_id: str):
    job = books_validation_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Validation job not found.")
    return job


# ---------- runs / jobs ----------
def _new_run(src: SourceSet, source: str, parent_run_id: str | None = None):
    bound_client_id = (src.client_id or "").strip() or None
    assessee_name, assessee_pan = src.assessee_name or "", (src.assessee_pan or "").upper()
    if bound_client_id:
        # A selected client is an explicit workspace boundary. Resolve its
        # persisted metadata before creating the run so a delayed client query
        # or blank editable input cannot fragment its reconciliation history.
        prior_runs = [item for item in db.runs.find({}, NO_ID) if _client_id(item) == bound_client_id]
        if not prior_runs:
            raise HTTPException(404, "Selected client was not found. Reopen the client directory and try again.")
        canonical = next((item for item in prior_runs if item.get("assessee_pan")), prior_runs[0])
        canonical_pan = (canonical.get("assessee_pan") or "").upper()
        if assessee_pan and canonical_pan and assessee_pan != canonical_pan:
            raise HTTPException(409, "The selected client and supplied assessee PAN do not match.")
        assessee_name = canonical.get("assessee_name") or assessee_name
        assessee_pan = canonical_pan or assessee_pan
    run_id = uuid.uuid4().hex[:12]
    run = {"run_id": run_id, "source": source, "status": "PROCESSING", "created_at": now_iso(), "started_at": now_iso(), "finished_at": None, "error": None,
           "assessee_name": assessee_name, "assessee_pan": assessee_pan, "client_id": bound_client_id, "financial_year": src.financial_year or "",
           "uploads": _ids(src), "workflow": src.workflow, "steps": [{"key": k, "label": l, "status": "PENDING", "detail": None, "started_at": None, "finished_at": None} for k, l in STEPS], "summary": None, "rerun_count": 0}
    if parent_run_id:
        run["parent_run_id"] = parent_run_id
        run["rerun_of_run_id"] = parent_run_id
        run["source"] = "rerun"
    db.runs.insert_one(dict(run))
    return run


def _execute(run_id: str):
    run = db.runs.find_one({"run_id": run_id}, NO_ID)
    db.runs.update_one({"run_id": run_id}, {"$set": {"status": "PROCESSING", "error": None, "started_at": now_iso(), "finished_at": None, "steps": [{"key": k, "label": l, "status": "PENDING", "detail": None, "started_at": None, "finished_at": None} for k, l in STEPS]}})

    def progress(step, status, detail):
        field = "started_at" if status == "RUNNING" else "finished_at"
        db.runs.update_one({"run_id": run_id, "steps.key": step}, {"$set": {"steps.$.status": status, "steps.$.detail": detail, f"steps.$.{field}": now_iso()}})

    try:
        files = _load_files(run["uploads"])
        if run.get("workflow") == "26AS_ONLY":
            out = run_26as_analysis(run_id, files, run.get("financial_year") or None, get_settings(), progress)
        elif run.get("workflow") == "SALES_TDS_26AS":
            out = run_sales_tds_26as_analysis(run_id, files, run.get("financial_year") or None, get_settings(), progress)
        else:
            out = run_pipeline(run_id, files, run.get("financial_year") or None, get_settings(), Store(run.get("assessee_pan", "")), progress)
        db.results.delete_many({"run_id": run_id})
        if out["results"]:
            # Keep MongoDB batches bounded for six-figure Books exports while
            # retaining every source-row result and its run ID.
            for offset in range(0, len(out["results"]), 1000):
                db.results.insert_many([dict(r) for r in out["results"][offset:offset + 1000]], ordered=True)
        files_meta = {k: db.uploads.find_one({"upload_id": v}, NO_ID) for k, v in run["uploads"].items() if v}
        db.runs.update_one({"run_id": run_id}, {"$set": {"status": "COMPLETED", "finished_at": now_iso(), "summary": out["summary"], "validation": out["validation"], "identities": out["identities"], "customers": out["customers"], "settings": out["settings"], "files": files_meta}})
    except PipelineError as e:
        db.runs.update_one({"run_id": run_id}, {"$set": {"status": "FAILED", "finished_at": now_iso(), "error": str(e), "validation": e.details.get("validation")}})
    except HTTPException as e:
        db.runs.update_one({"run_id": run_id}, {"$set": {"status": "FAILED", "finished_at": now_iso(), "error": e.detail}})
    except Exception:
        logger.exception("Pipeline failed for run %s", run_id)
        db.runs.update_one({"run_id": run_id}, {"$set": {"status": "FAILED", "finished_at": now_iso(), "error": "The reconciliation engine hit an unexpected error while processing. Check the source files and try again."}})


def _start(run_id: str):
    threading.Thread(target=_execute, args=(run_id,), daemon=True).start()


@app.post("/api/reconcile")
def reconcile(src: SourceSet):
    if src.workflow not in ("FULL_RECONCILIATION", "26AS_ONLY", "SALES_TDS_26AS"):
        raise HTTPException(400, "Unknown workflow.")
    if not src.form26as_upload_id or (src.workflow == "FULL_RECONCILIATION" and not src.books_upload_id) or (src.workflow == "SALES_TDS_26AS" and (not src.tds_receivable_upload_id or not src.sales_registry_upload_id)):
        raise HTTPException(400, "26AS is required for analysis; Books is also required for full reconciliation.")
    _load_files(_ids(src))
    run = _new_run(src, "upload")
    _start(run["run_id"])
    return {"job_id": run["run_id"], "run_id": run["run_id"], "status": "PROCESSING", "steps": run["steps"], "progress": 0, "error": None, "validation": None, "summary": None}


@app.post("/api/sample/load")
def load_sample():
    ids = {}
    for kind, (fn, content) in sample_files().items():
        upload_id = uuid.uuid4().hex
        (UPLOAD_DIR / f"{upload_id}.csv").write_bytes(content)
        report, _ = load_and_validate(kind, fn, content, None)
        db.uploads.insert_one({"upload_id": upload_id, "kind": kind, "filename": fn, "size": len(content), "ext": ".csv", "uploaded_at": now_iso(), "row_count": report["row_count"], "columns_mapped": report["columns_mapped"], "columns_unmapped": report["columns_unmapped"], "readable": True, "sample": True})
        ids[kind] = upload_id
    src = SourceSet(books_upload_id=ids["books"], form26as_upload_id=ids["form26as"], customer_master_upload_id=ids["customer_master"], **SAMPLE_ASSESSEE)
    run = _new_run(src, "sample")
    _start(run["run_id"])
    return {"job_id": run["run_id"], "run_id": run["run_id"], "status": "PROCESSING", "steps": run["steps"], "progress": 0, "error": None, "validation": None, "summary": None, "uploads": ids}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    run = db.runs.find_one({"run_id": job_id}, {"_id": 0, "identities": 0, "customers": 0})
    if not run:
        raise HTTPException(404, "Job not found")
    done = sum(1 for s in run["steps"] if s["status"] == "COMPLETED")
    return {"job_id": job_id, "run_id": job_id, "status": run["status"], "steps": run["steps"], "progress": round(done / len(STEPS) * 100), "error": run.get("error"), "validation": run.get("validation"), "summary": run.get("summary")}


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str):
    run = db.runs.find_one({"run_id": run_id}, NO_ID)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] == "PROCESSING":
        raise HTTPException(409, "This run is already processing.")
    src = SourceSet(
        books_upload_id=(run.get("uploads") or {}).get("books"),
        form26as_upload_id=(run.get("uploads") or {}).get("form26as"),
        customer_master_upload_id=(run.get("uploads") or {}).get("customer_master"),
        tds_receivable_upload_id=(run.get("uploads") or {}).get("tds_receivable"),
        sales_registry_upload_id=(run.get("uploads") or {}).get("sales_registry"),
        assessee_name=run.get("assessee_name"), assessee_pan=run.get("assessee_pan"),
        client_id=run.get("client_id"),
        financial_year=run.get("financial_year"), workflow=run.get("workflow") or "FULL_RECONCILIATION",
    )
    new_run = _new_run(src, "rerun", parent_run_id=run_id)
    _start(new_run["run_id"])
    return {"job_id": new_run["run_id"], "run_id": new_run["run_id"], "parent_run_id": run_id, "status": "PROCESSING"}


def _run_public(run):
    if run:
        run.pop("identities", None)
        run.pop("customers", None)
        run["client_id"] = _client_id(run)
    return run


def _client_id(run: dict) -> str:
    """Stable derived client identity; there is no separate Client Master yet.

    PAN is the preferred authoritative assessee identifier.  Older runs without
    it fall back to the normalized assessee name, so client catalogues can still
    be built without inventing a new persistent client model.
    """
    if (run.get("client_id") or "").strip():
        return run["client_id"].strip()
    identity = (run.get("assessee_pan") or "").strip().upper() or core_name(run.get("assessee_name") or "")
    return sha256(identity.encode("utf-8")).hexdigest()[:20]


def _client_public(run: dict) -> dict:
    return {"client_id": _client_id(run), "client_name": run.get("assessee_name") or "Unknown assessee", "assessee_pan": run.get("assessee_pan") or ""}


@app.get("/api/runs")
def runs(limit: int = 20):
    return [_run_public(r) for r in db.runs.find({}, {"_id": 0, "identities": 0, "customers": 0, "validation": 0}).sort("created_at", DESCENDING).limit(limit)]


@app.get("/api/clients")
def clients():
    """Safe derived client catalogue from stored reconciliation-run metadata."""
    grouped = {}
    for run in db.runs.find({}, {"_id": 0, "assessee_name": 1, "assessee_pan": 1, "financial_year": 1, "status": 1}):
        client = _client_public(run)
        item = grouped.setdefault(client["client_id"], {**client, "financial_years": set(), "run_count": 0, "completed_run_count": 0})
        if run.get("financial_year"):
            item["financial_years"].add(run["financial_year"])
        item["run_count"] += 1
        item["completed_run_count"] += int(run.get("status") == "COMPLETED")
    return {"clients": [{**item, "financial_years": sorted(item["financial_years"], reverse=True)} for item in sorted(grouped.values(), key=lambda x: x["client_name"].lower())]}


@app.get("/api/clients/{client_id}/runs")
def client_runs(client_id: str):
    rows = [run for run in db.runs.find({}, {"_id": 0, "identities": 0, "customers": 0, "validation": 0}).sort("created_at", DESCENDING) if _client_id(run) == client_id]
    if not rows:
        raise HTTPException(404, "Client not found")
    return {"client": _client_public(rows[0]), "runs": [_run_public(run) for run in rows]}


@app.get("/api/runs/latest")
def latest_run():
    run = db.runs.find_one({"status": {"$in": ["COMPLETED", "PROCESSING", "FAILED"]}}, {"_id": 0, "identities": 0, "customers": 0}, sort=[("created_at", DESCENDING)])
    return _run_public(run) or {}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, client_id: str | None = None):
    run = db.runs.find_one({"run_id": run_id}, {"_id": 0, "identities": 0, "customers": 0})
    if not run or (client_id and _client_id(run) != client_id):
        raise HTTPException(404, "Run not found")
    return _run_public(run)


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    if not db.runs.find_one({"run_id": run_id}):
        raise HTTPException(404, "Run not found")
    db.results.delete_many({"run_id": run_id})
    db.exception_states.delete_many({"run_id": run_id})
    db.runs.delete_one({"run_id": run_id})
    return {"deleted": run_id}


def _resolve_run_id(run_id):
    if run_id:
        return run_id
    run = db.runs.find_one({"status": "COMPLETED"}, {"run_id": 1}, sort=[("created_at", DESCENDING)])
    return run["run_id"] if run else None


# ---------- results ----------
def _with_transaction_reconciliation(row: dict) -> dict:
    """Read-only projection for completed Sales + TDS + 26AS results.

    A stored run is never rewritten.  The projection derives transaction-level
    display data from its persisted normalized source rows so that the UI shows
    the same view for historical and newly completed runs.
    """
    if row.get("transaction_reconciliation"):
        return row
    amount = row.get("amount_check") or {}
    tds = row.get("tds_check") or {}
    statements = list(amount.get("statement_entries") or tds.get("statement_entries") or [])
    sales_by_id = {}
    for sale in [*(amount.get("sales_entries") or []), *(amount.get("candidate_sales_entries") or [])]:
        sales_id = sale.get("sales_transaction_id")
        if sales_id:
            sales_by_id[sales_id] = sale
    if not statements:
        return row
    projected = dict(row)
    projected["transaction_reconciliation"] = _transaction_reconciliation(
        projected.get("relationship_key") or projected.get("relationship_id") or projected.get("id") or "historical-result",
        statements,
        list(sales_by_id.values()),
        get_settings(),
        (projected.get("identity") or {}).get("status") or projected.get("identity_status") or "UNMAPPED",
    )
    return projected

@app.get("/api/sales-tds-26as/summary")
def sales_tds_summary(run_id: str):
    run = db.runs.find_one({"run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID)
    if not run:
        raise HTTPException(404, "Sales + TDS + 26AS run not found.")
    return {"run": run, "summary": run.get("summary") or {}}


@app.get("/api/sales-tds-26as/results")
def sales_tds_results(run_id: str, page: int = 1, page_size: int = Query(50, le=500), status: str | None = None):
    query = {"run_id": run_id, "workflow": "SALES_TDS_26AS"}
    if status:
        query["overall_status"] = status
    total = db.results.count_documents(query)
    rows = list(db.results.find(query, NO_ID).sort("seq", ASCENDING).skip((page - 1) * page_size).limit(page_size))
    return {"items": [_with_transaction_reconciliation(row) for row in rows], "total": total, "page": page, "page_size": page_size, "run_id": run_id}


@app.get("/api/sales-tds-26as/results/{result_id}")
def sales_tds_result(result_id: str, run_id: str):
    row = db.results.find_one({"id": result_id, "run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID)
    if not row:
        raise HTTPException(404, "Sales + TDS + 26AS result not found.")
    return _with_transaction_reconciliation(row)


@app.get("/api/sales-tds-26as/exceptions")
def sales_tds_exceptions(run_id: str, page: int = 1, page_size: int = Query(50, le=500)):
    """Operational queue projection for Sales workflow without legacy Books fields."""
    run = db.runs.find_one({"run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID)
    if not run:
        raise HTTPException(404, "Sales + TDS + 26AS run not found.")
    rows = list(db.results.find({"run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID).sort("seq", ASCENDING))
    items = []
    for row in rows:
        identity, amount, tds = row.get("identity") or {}, row.get("amount_check") or {}, row.get("tds_check") or {}
        issues = []
        if identity.get("status") != "CONFIRMED": issues.append(identity.get("status") or "UNMAPPED")
        if amount.get("status") != "AMOUNT_MATCHED": issues.append(amount.get("status") or "AMOUNT_NOT_DETERMINABLE")
        if tds.get("reconciliation_status", tds.get("status")) not in {"MATCHED", "TDS_MATCHED"}:
            issues.append(tds.get("reconciliation_status", tds.get("status")) or "TDS_NOT_DETERMINABLE")
        if not issues:
            continue
        items.append({"id": row["id"], "transaction_id": row.get("transaction_id"), "assessee_name": run.get("assessee_name"), "assessee_pan": run.get("assessee_pan"), "financial_year": row.get("financial_year"), "sales_customer": identity.get("customer_name"), "customer_pan": identity.get("customer_pan"), "customer_gstin": identity.get("customer_gstin"), "deductor": identity.get("deductor_name"), "tan": identity.get("tan"), "identity_status": identity.get("status"), "identity_method": identity.get("method"), "identity_reason": identity.get("reason"), "sales_amount": amount.get("sales_amount"), "statement_amount_paid": amount.get("statement_amount_paid"), "amount_status": amount.get("status"), "amount_difference": amount.get("difference"), "tds_expected": tds.get("ledger_amount", tds.get("tds_expected")), "statement_tds_deducted": tds.get("statement_amount", tds.get("statement_tds_deducted")), "tds_status": tds.get("reconciliation_status", tds.get("status")), "tds_identity_status": tds.get("identity_status"), "tds_identity_reason": tds.get("identity_reason"), "tds_difference": tds.get("difference"), "overall_status": row.get("overall_status"), "issues": issues, "reason": row.get("reason"), "recommended_action": row.get("recommended_action")})
    total = len(items)
    return {"items": items[(page - 1) * page_size:page * page_size], "total": total, "page": page, "page_size": page_size, "run_id": run_id}

def _analysis_overview(run_id: str):
    """Aggregate only persisted 26AS-only rows for the selected run."""
    groups = {}
    for row in db.results.find({"run_id": run_id, "analysis_mode": "26AS_ONLY"}, {"_id": 0, "tan": 1, "deductor_name": 1, "section": 1, "amount_paid": 1, "tds_expected": 1, "tax_deducted": 1, "difference": 1, "analysis_status": 1, "statement_entries": 1}):
        statement = (row.get("statement_entries") or [{}])[0]
        tan = row.get("tan") or statement.get("tan") or ""
        key = (tan, row.get("deductor_name") or statement.get("deductor_name") or "Unknown deductor")
        item = groups.setdefault(key, {"tan": key[0], "deductor_name": key[1], "sections": set(), "entry_count": 0, "amount_paid": 0.0, "tds_expected": 0.0, "tds_deducted": 0.0, "difference": 0.0, "not_determinable_count": 0, "difference_count": 0})
        item["entry_count"] += 1
        item["sections"].add(row.get("section") or statement.get("section") or "")
        item["amount_paid"] += float(row.get("amount_paid") if row.get("amount_paid") is not None else statement.get("amount_paid") or 0)
        item["tds_expected"] += float(row.get("tds_expected") or 0)
        item["tds_deducted"] += float(row.get("tax_deducted") or 0)
        item["difference"] += float(row.get("difference") or 0)
        item["not_determinable_count"] += int(row.get("analysis_status") == "NOT_DETERMINABLE")
        item["difference_count"] += int(row.get("analysis_status") == "TDS_DIFFERENCE")
    rows = []
    for item in groups.values():
        item["sections"] = sorted(value for value in item["sections"] if value)
        item["amount_paid"] = round(item["amount_paid"], 2)
        item["tds_expected"] = round(item["tds_expected"], 2)
        item["tds_deducted"] = round(item["tds_deducted"], 2)
        item["difference"] = round(item["difference"], 2)
        item["analysis"] = "TDS_DIFFERENCE" if item["difference_count"] else "NOT_DETERMINABLE" if item["not_determinable_count"] else "CONSISTENT_WITH_CONFIGURED_RULE"
        rows.append(item)
    return sorted(rows, key=lambda item: (-abs(item["difference"]), item["deductor_name"].lower(), item["tan"]))


def _sales_dashboard_attention(run_id: str):
    """Expose Sales + TDS + 26AS review rows in the shared dashboard shape.

    The Sales workflow intentionally keeps its checks in separate nested
    structures.  The dashboard is a cross-workflow view, so this projection
    makes the operational queue visible without copying legacy Books fields
    into persisted Sales results.
    """
    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
    items = []
    for row in db.results.find({"run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID):
        identity = row.get("identity") or {}
        amount = row.get("amount_check") or {}
        tds = row.get("tds_check") or {}
        identity_status = identity.get("status") or "UNMAPPED"
        amount_status = amount.get("status") or "AMOUNT_NOT_DETERMINABLE"
        tds_status = tds.get("reconciliation_status", tds.get("status")) or "NOT_DETERMINABLE"
        if identity_status == "CONFIRMED" and amount_status == "AMOUNT_MATCHED" and tds_status == "MATCHED":
            continue
        difference = amount.get("difference")
        if difference is None:
            difference = tds.get("difference")
        items.append({
            "id": row.get("id"),
            "transaction_id": row.get("transaction_id"),
            "customer": identity.get("customer_name") or (row.get("sales_check") or {}).get("customer_name"),
            "deductor_name": identity.get("deductor_name"),
            "tan": identity.get("tan"),
            "result": row.get("overall_status") or tds_status,
            "severity": row.get("severity") or "MEDIUM",
            "difference": difference,
            "reason": row.get("reason") or row.get("recommended_action"),
            "books_quarter": row.get("quarter"),
            "statement_quarter": row.get("quarter"),
            "identity_status": identity_status,
            "amount_status": amount_status,
            "tds_status": tds_status,
        })
    return sorted(items, key=lambda item: (severity_rank.get(item["severity"], 4), -abs(float(item["difference"] or 0)), item.get("transaction_id") or ""))[:8]


def _historical_sales_dashboard_controls(run_id: str, stored_summary: dict):
    """Derive display controls for pre-control-model Sales runs.

    Historical runs retain source-wide totals in their saved summary and retain
    selected source evidence inside each persisted relationship. This function
    combines those immutable records only for the API response; it never writes
    a run, changes an identity decision, or re-runs matching.
    """
    rows = list(db.results.find({"run_id": run_id, "workflow": "SALES_TDS_26AS"}, NO_ID))
    selected_sales, selected_statements = {}, {}
    confirmed_tds = review_tds = missing_tds = tds_evidence = total_tds = 0.0
    for row in rows:
        amount = row.get("amount_check") or {}
        for entry in [*amount.get("sales_entries", []), *amount.get("candidate_sales_entries", [])]:
            if entry.get("sales_transaction_id"):
                selected_sales.setdefault(entry["sales_transaction_id"], entry)
        for entry in amount.get("statement_entries", []):
            if entry.get("statement_id"):
                selected_statements.setdefault(entry["statement_id"], entry)
        tds = row.get("tds_check") or {}
        statement_amount = float(tds.get("statement_amount") or 0)
        total_tds += statement_amount
        status = tds.get("reconciliation_status")
        if status == "MATCHED":
            confirmed_tds += statement_amount
        elif status == "REVIEW_REQUIRED":
            review_tds += statement_amount
        elif status == "MISSING_TDS_LEDGER_COUNTERPART":
            missing_tds += statement_amount
        if tds.get("ledger_amount") is not None:
            tds_evidence += statement_amount

    source_sales = stored_summary.get("sales_amount_total")
    source_26as = stored_summary.get("statement_amount_paid_total")
    primary_sales = round(sum(float(item.get("taxable_amount") or 0) for item in selected_sales.values()), 2)
    primary_26as = round(sum(float(item.get("amount_paid") or 0) for item in selected_statements.values()), 2)
    source_sales = float(source_sales) if source_sales is not None else None
    source_26as = float(source_26as) if source_26as is not None else None
    return {
        "sales_source_taxable_total": source_sales,
        "26as_source_amount_total": source_26as,
        "sales_source_difference": round(source_sales - source_26as, 2) if source_sales is not None and source_26as is not None else None,
        "primary_sales_taxable_total": primary_sales,
        "primary_26as_amount_total": primary_26as,
        "sales_difference_for_primary_population": round(primary_sales - primary_26as, 2),
        "sales_only_taxable_total": round(source_sales - primary_sales, 2) if source_sales is not None else None,
        "26as_only_amount_total": round(source_26as - primary_26as, 2) if source_26as is not None else None,
        "tds_amount_evidence_available": round(tds_evidence, 2),
        "total_tds_amount": round(total_tds, 2),
        "confirmed_tds_amount": round(confirmed_tds, 2),
        "review_tds_amount": round(review_tds, 2),
        "missing_tds_amount": round(missing_tds, 2),
        "controls_derived_from_historical_run": True,
    }


@app.get("/api/reconciliation/summary")
def summary(run_id: str | None = None):
    rid = _resolve_run_id(run_id)
    run = db.runs.find_one({"run_id": rid}, {"_id": 0, "identities": 0, "customers": 0, "validation": 0}) if rid else None
    if not run:
        return {"run": None, "summary": None}
    if run.get("workflow") == "SALES_TDS_26AS":
        attention = _sales_dashboard_attention(rid)
    else:
        attention = list(db.results.find({"run_id": rid, "severity": {"$in": ["HIGH", "MEDIUM"]}}, {"_id": 0, "id": 1, "transaction_id": 1, "customer": 1, "deductor_name": 1, "tan": 1, "result": 1, "severity": 1, "difference": 1, "tds_expected": 1, "tax_deducted": 1, "reason": 1, "books_quarter": 1, "statement_quarter": 1}).sort([("severity", ASCENDING), ("difference", DESCENDING)]).limit(8))
    overview = _analysis_overview(rid) if run.get("workflow") == "26AS_ONLY" else []
    summary_data = dict(run.get("summary") or {})
    if run.get("workflow") == "SALES_TDS_26AS":
        controls = dict(summary_data.get("control_totals") or {})
        if "sales_source_taxable_total" not in controls:
            controls.update(_historical_sales_dashboard_controls(rid, summary_data))
        summary_data["control_totals"] = controls
    if overview:
        summary_data.setdefault("deductor_count", len(overview))
        summary_data.setdefault("amount_paid_total", round(sum(item["amount_paid"] for item in overview), 2))
    return {"run": run, "summary": summary_data, "attention": attention, "workflow_mode": run.get("workflow") or "FULL_RECONCILIATION", "deductor_summary": overview}


FILTERS = {"financial_year": "financial_year", "quarter": None, "customer": "customer", "customer_code": "customer_code", "deductor": "deductor_name", "tan": "tan", "section": "section", "result": "result", "claimability": "claimability", "identity_status": "identity_status", "match_method": "match_method", "severity": "severity", "exception_category": "exception_category"}
SORTABLE = {"transaction_id", "customer", "customer_code", "tan", "deductor_name", "books_date", "statement_date", "books_quarter", "section", "tds_expected", "tax_deducted", "tds_deposited", "difference", "difference_pct", "result", "claimability", "match_method", "identity_status", "severity", "seq"}


def _query(run_id: str, params: dict, search: str | None):
    q = {"run_id": run_id}
    clauses = []
    for key, field in FILTERS.items():
        val = params.get(key)
        if not val:
            continue
        values = val.split(",")
        if key == "quarter":
            clauses.append({"$or": [{"books_quarter": {"$in": values}}, {"statement_quarter": {"$in": values}}]})
        elif key == "tan":
            value_filter = {"$in": values} if len(values) > 1 else values[0]
            clauses.append({"$or": [{"tan": value_filter}, {"matched_tans": value_filter}]})
        else:
            clauses.append({field: {"$in": values} if len(values) > 1 else values[0]})
    if search:
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        clauses.append({"$or": [{f: rx} for f in ("transaction_id", "customer", "customer_code", "tan", "deductor_name", "party_pan", "reason", "match_group_id", "section", "matched_tans")]})
    if clauses:
        q["$and"] = clauses
    return q


def _relationship_id(row: dict) -> str:
    """Return the stable review unit without changing any engine result fields."""
    return str(row.get("match_group_id") or row.get("relationship_id") or row["id"])


def _relationship_row(run_id: str, relationship_id: str) -> tuple[dict, str]:
    """Validate that a commentary target belongs to this completed full-reconciliation run."""
    run = db.runs.find_one({"run_id": run_id}, NO_ID)
    if not run:
        raise HTTPException(404, "Reconciliation run not found")
    if run.get("workflow") in {"26AS_ONLY", "SALES_TDS_26AS"}:
        raise HTTPException(400, "CA commentary is available only for full reconciliation relationships.")
    row = db.results.find_one({"run_id": run_id, "$or": [{"id": relationship_id}, {"match_group_id": relationship_id}, {"relationship_id": relationship_id}]}, NO_ID)
    if not row:
        raise HTTPException(404, "Relationship not found in this reconciliation run")
    return row, _relationship_id(row)


def _current_commentary(run_id: str, relationship_id: str) -> dict | None:
    return strip(db.reconciliation_relationship_commentaries.find_one(
        {"run_id": run_id, "relationship_id": relationship_id, "is_current": True}, NO_ID
    ))


class RelationshipCommentaryInput(BaseModel):
    commentary: str = Field(min_length=1, max_length=2000)
    review_status: str = "NEW"
    reviewer: str = Field(min_length=1, max_length=120)


def _validate_commentary_input(body: RelationshipCommentaryInput) -> tuple[str, str, str]:
    commentary = body.commentary.strip()
    reviewer = body.reviewer.strip()
    if not commentary:
        raise HTTPException(422, "Commentary cannot be empty.")
    if not reviewer:
        raise HTTPException(422, "Reviewer identity is required.")
    if body.review_status not in {"NEW", "REVIEWED"}:
        raise HTTPException(422, "Review status must be NEW or REVIEWED.")
    return commentary, reviewer, body.review_status


@app.get("/api/reconciliation/runs/{run_id}/relationships/{relationship_id}/commentary")
def relationship_commentary(run_id: str, relationship_id: str):
    _, canonical_id = _relationship_row(run_id, relationship_id)
    return {"run_id": run_id, "relationship_id": canonical_id, "commentary": _current_commentary(run_id, canonical_id)}


@app.get("/api/reconciliation/runs/{run_id}/relationships/{relationship_id}/commentary/history")
def relationship_commentary_history(run_id: str, relationship_id: str):
    _, canonical_id = _relationship_row(run_id, relationship_id)
    items = list(db.reconciliation_relationship_commentaries.find(
        {"run_id": run_id, "relationship_id": canonical_id}, NO_ID
    ).sort("version", DESCENDING))
    return {"run_id": run_id, "relationship_id": canonical_id, "items": items}


@app.post("/api/reconciliation/runs/{run_id}/relationships/{relationship_id}/commentary")
def create_relationship_commentary(run_id: str, relationship_id: str, body: RelationshipCommentaryInput):
    row, canonical_id = _relationship_row(run_id, relationship_id)
    commentary, reviewer, review_status = _validate_commentary_input(body)
    if _current_commentary(run_id, canonical_id):
        raise HTTPException(409, "Commentary already exists for this relationship. Update it to create a new revision.")
    ts = now_iso()
    item = {"commentary_id": uuid.uuid4().hex, "run_id": run_id, "relationship_id": canonical_id,
            "match_group_id": row.get("match_group_id"), "result_id": row["id"], "commentary": commentary,
            "review_status": review_status, "created_by": reviewer, "created_at": ts, "updated_by": reviewer,
            "updated_at": ts, "version": 1, "is_current": True}
    db.reconciliation_relationship_commentaries.insert_one(item)
    db.reconciliation_relationship_commentary_events.insert_one({"event_id": uuid.uuid4().hex, "event": "CREATED", "run_id": run_id, "relationship_id": canonical_id, "commentary_id": item["commentary_id"], "actor": reviewer, "created_at": ts})
    return strip(item)


@app.put("/api/reconciliation/runs/{run_id}/relationships/{relationship_id}/commentary")
def update_relationship_commentary(run_id: str, relationship_id: str, body: RelationshipCommentaryInput):
    row, canonical_id = _relationship_row(run_id, relationship_id)
    commentary, reviewer, review_status = _validate_commentary_input(body)
    current = _current_commentary(run_id, canonical_id)
    if not current:
        raise HTTPException(404, "No commentary exists for this relationship. Create it first.")
    ts = now_iso()
    db.reconciliation_relationship_commentaries.update_one({"commentary_id": current["commentary_id"]}, {"$set": {"is_current": False}})
    item = {"commentary_id": uuid.uuid4().hex, "run_id": run_id, "relationship_id": canonical_id,
            "match_group_id": row.get("match_group_id"), "result_id": row["id"], "commentary": commentary,
            "review_status": review_status, "created_by": current["created_by"], "created_at": current["created_at"],
            "updated_by": reviewer, "updated_at": ts, "version": current["version"] + 1, "is_current": True}
    db.reconciliation_relationship_commentaries.insert_one(item)
    db.reconciliation_relationship_commentary_events.insert_one({"event_id": uuid.uuid4().hex, "event": "UPDATED", "run_id": run_id, "relationship_id": canonical_id, "commentary_id": item["commentary_id"], "previous_commentary_id": current["commentary_id"], "actor": reviewer, "created_at": ts})
    return strip(item)


@app.get("/api/reconciliation/results")
def results(run_id: str | None = None, page: int = 1, page_size: int = Query(50, le=500), search: str | None = None, sort: str = "seq", order: str = "asc",
            financial_year: str | None = None, quarter: str | None = None, customer: str | None = None, customer_code: str | None = None, deductor: str | None = None, tan: str | None = None, section: str | None = None,
            result: str | None = None, claimability: str | None = None, identity_status: str | None = None, match_method: str | None = None, severity: str | None = None, exception_category: str | None = None, exclude_result: str | None = None):
    rid = _resolve_run_id(run_id)
    if not rid:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size, "run_id": None, "facets": {}}
    q = _query(rid, locals(), search)
    if exclude_result:
        q["result"] = {"$nin": exclude_result.split(",")}
    total = db.results.count_documents(q)
    sort_field = sort if sort in SORTABLE else "seq"
    cursor = db.results.find(q, {"_id": 0, "books": 0, "statement_entries": 0}).sort([(sort_field, DESCENDING if order == "desc" else ASCENDING), ("seq", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)
    facets = {}
    for f in ("result", "claimability", "identity_status", "match_method", "section", "books_quarter", "customer_code", "tan", "severity", "exception_category"):
        facets[f] = sorted(v for v in db.results.distinct(f, {"run_id": rid}) if v)
    customers = {}
    for r in db.results.find({"run_id": rid, "customer_code": {"$ne": ""}}, {"_id": 0, "customer_code": 1, "customer": 1}):
        customers[r["customer_code"]] = r["customer"]
    facets["customers"] = [{"code": k, "name": v} for k, v in sorted(customers.items())]
    facets["deductors"] = sorted(v for v in db.results.distinct("deductor_name", {"run_id": rid}) if v)
    items = list(cursor)
    relationship_ids = {_relationship_id(row) for row in items}
    commentaries = {
        item["relationship_id"]: item
        for item in db.reconciliation_relationship_commentaries.find(
            {"run_id": rid, "relationship_id": {"$in": list(relationship_ids)}, "is_current": True},
            {"_id": 0, "relationship_id": 1, "commentary": 1, "review_status": 1, "updated_at": 1, "updated_by": 1, "version": 1},
        )
    }
    for item in items:
        commentary = commentaries.get(_relationship_id(item))
        item["commentary_summary"] = ({"exists": True, **commentary} if commentary else {"exists": False})
    return {"items": items, "total": total, "page": page, "page_size": page_size, "run_id": rid, "facets": facets}


@app.get("/api/reconciliation/results/{result_id}")
def result_detail(result_id: str, run_id: str | None = None):
    query = {"id": result_id}
    if run_id:
        # Result IDs are not a workspace boundary; run_id is authoritative when
        # a workspace is selected and rejects cross-client/run lookups.
        query["run_id"] = run_id
    row = db.results.find_one(query, NO_ID)
    if not row:
        raise HTTPException(404, "Result not found")
    row["exception_state"] = strip(db.exception_states.find_one({"result_id": result_id})) or {"status": "OPEN", "notes": []}
    row["relationship_id"] = _relationship_id(row)
    row["commentary"] = _current_commentary(row["run_id"], row["relationship_id"])
    if row.get("match_group_id"):
        row["group_rows"] = list(db.results.find({"run_id": row["run_id"], "match_group_id": row["match_group_id"]}, {"_id": 0, "id": 1, "transaction_id": 1, "tds_expected": 1, "books_date": 1, "books_quarter": 1, "section": 1}))
    return row


# ---------- AI assistant (read-only analysis of stored engine output) ----------
@app.get("/api/ai/context/{run_id}")
def ai_context(run_id: str):
    run = _completed_ai_run(run_id)
    context = _ai_context(run)
    return {"run_id": run_id, "context": _ai_public_context(context)}


@app.post("/api/ai/chat")
def ai_chat(body: AIChatRequest):
    try:
        question = validate_question(body.question)
        filters = safe_filters(body.filters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run = _completed_ai_run(body.run_id)
    cached_context = _ai_context(run)
    conversation = _conversation(body.run_id, body.conversation_id)
    selected = None
    if body.result_id:
        selected = db.results.find_one({"id": body.result_id, "run_id": body.run_id}, NO_ID)
        if not selected:
            raise HTTPException(404, "Selected reconciliation result was not found in this run.")
    history = conversation.get("messages", [])
    context = focused_context(cached_context, question, selected, history)
    if filters:
        context["filters"] = filters
        context["results"] = [row for row in context["results"] if all(str(row.get(field, "")) in values.split(",") for field, values in filters.items())]
        context["result_rows_in_context"] = len(context["results"])
    try:
        answer = get_provider().answer(question, context)
    except AIConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("AI assistant provider request failed")
        if "quota" in str(exc).lower() or "429" in str(exc):
            raise HTTPException(503, "The AI service is temporarily unavailable because the configured AI provider has exhausted its available quota. Your reconciliation results are unaffected.") from exc
        raise HTTPException(502, "AI analysis could not be generated. Try again later. Your reconciliation results are unaffected.") from exc
    messages = (history + [{"id": uuid.uuid4().hex, "role": "user", "content": question, "timestamp": now_iso()}, {"id": uuid.uuid4().hex, "role": "assistant", "content": answer, "timestamp": now_iso()}])[-24:]
    db.ai_conversations.update_one({"conversation_id": conversation["conversation_id"], "run_id": body.run_id}, {"$set": {"messages": messages, "updated_at": now_iso()}})
    return {"run_id": body.run_id, "conversation_id": conversation["conversation_id"], "result_id": body.result_id, "answer": answer, "context": _ai_public_context(cached_context), "context_limited": context["context_limited"], "result_rows_in_context": context["result_rows_in_context"]}


# ---------- exceptions ----------
class ExceptionUpdate(BaseModel):
    status: str | None = None
    note: str | None = None
    assignee: str | None = None
    reviewer: str | None = "CA Reviewer"


@app.get("/api/exceptions")
def exceptions(run_id: str | None = None, category: str | None = None, status: str | None = None, search: str | None = None, page: int = 1, page_size: int = Query(50, le=500), sort: str = "severity", order: str = "asc"):
    rid = _resolve_run_id(run_id)
    if not rid:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size, "counts": {}, "run_id": None}
    q = {"run_id": rid, "workflow": {"$ne": "SALES_TDS_26AS"}, "result": {"$ne": "MATCHED_CLAIMABLE"}}
    if category and category != "ALL":
        q["exception_category"] = category
    if search:
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        q["$or"] = [{f: rx} for f in ("transaction_id", "customer", "tan", "deductor_name", "reason")]
    states = {s["result_id"]: strip(s) for s in db.exception_states.find({"run_id": rid})}
    rows = list(db.results.find(q, {"_id": 0, "books": 0, "statement_entries": 0}))
    for r in rows:
        st = states.get(r["id"], {})
        r["exception_status"] = st.get("status", "OPEN")
        r["notes"] = st.get("notes", [])
        r["assignee"] = st.get("assignee")
    if status and status != "ALL":
        rows = [r for r in rows if r["exception_status"] == status]
    sev = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
    keyf = {"severity": lambda r: (sev.get(r["severity"], 9), -(abs(r["difference"] or 0))), "difference": lambda r: -(abs(r["difference"] or 0)), "customer": lambda r: r["customer"] or r["deductor_name"], "transaction_id": lambda r: r["transaction_id"]}.get(sort, lambda r: r["seq"])
    rows.sort(key=keyf, reverse=(order == "desc"))
    all_rows = list(db.results.find({"run_id": rid, "result": {"$ne": "MATCHED_CLAIMABLE"}}, {"_id": 0, "id": 1, "exception_category": 1}))
    counts = {"ALL": len(all_rows)}
    for r in all_rows:
        counts[r["exception_category"]] = counts.get(r["exception_category"], 0) + 1
    status_counts = {"OPEN": 0, "IN_REVIEW": 0, "RESOLVED": 0, "IGNORED": 0}
    for r in all_rows:
        status_counts[states.get(r["id"], {}).get("status", "OPEN")] += 1
    total = len(rows)
    return {"items": rows[(page - 1) * page_size: page * page_size], "total": total, "page": page, "page_size": page_size, "counts": counts, "status_counts": status_counts, "run_id": rid}


@app.patch("/api/exceptions/{result_id}")
def update_exception(result_id: str, body: ExceptionUpdate):
    row = db.results.find_one({"id": result_id}, NO_ID)
    if not row:
        raise HTTPException(404, "Exception not found")
    if body.status and body.status not in ("OPEN", "IN_REVIEW", "RESOLVED", "IGNORED"):
        raise HTTPException(400, "Status must be OPEN, IN_REVIEW, RESOLVED or IGNORED.")
    state = db.exception_states.find_one({"result_id": result_id}, NO_ID) or {"result_id": result_id, "run_id": row["run_id"], "transaction_id": row["transaction_id"], "status": "OPEN", "notes": [], "history": []}
    ts = now_iso()
    if body.status and body.status != state["status"]:
        state["history"].append({"from": state["status"], "to": body.status, "by": body.reviewer, "at": ts})
        state["status"] = body.status
    if body.note:
        state["notes"].append({"text": body.note.strip(), "by": body.reviewer, "at": ts})
    if body.assignee is not None:
        state["assignee"] = body.assignee
    state["updated_at"] = ts
    db.exception_states.replace_one({"result_id": result_id}, state, upsert=True)
    return strip(db.exception_states.find_one({"result_id": result_id}))


# ---------- identity ----------
@app.get("/api/identity/reviews")
def identity_reviews(run_id: str | None = None, status: str | None = None):
    rid = _resolve_run_id(run_id)
    run = db.runs.find_one({"run_id": rid}, {"_id": 0, "identities": 1, "assessee_pan": 1}) if rid else None
    if not run:
        return {"items": [], "run_id": None, "counts": {}}
    items = run.get("identities", [])
    for i in items:
        i["review_id"] = f"{rid}:{i['tan']}"
    counts = {"EXACT": 0, "HIGH_CONFIDENCE": 0, "REVIEW_REQUIRED": 0, "UNMAPPED": 0}
    for i in items:
        counts[i["status"]] += 1
    if status and status != "ALL":
        items = [i for i in items if i["status"] == status]
    order = {"REVIEW_REQUIRED": 0, "UNMAPPED": 1, "HIGH_CONFIDENCE": 2, "EXACT": 3}
    items.sort(key=lambda i: (order[i["status"]], -(i["total_tax_deducted"] or 0)))
    return {"items": items, "run_id": rid, "counts": counts, "assessee_pan": run.get("assessee_pan", "")}


class IdentityAction(BaseModel):
    run_id: str
    tan: str
    customer_code: str | None = None
    reviewer: str = "CA Reviewer"
    note: str | None = None
    rerun: bool = True


def _identity_ctx(body: IdentityAction):
    run = db.runs.find_one({"run_id": body.run_id}, NO_ID)
    if not run:
        raise HTTPException(404, "Run not found")
    ident = next((i for i in run.get("identities", []) if i["tan"] == body.tan.upper()), None)
    if not ident:
        raise HTTPException(404, f"TAN {body.tan} is not part of this run.")
    return run, ident


def _maybe_rerun(run, body):
    if body.rerun and run["status"] != "PROCESSING":
        return rerun(run["run_id"])["run_id"]
    return None


@app.post("/api/identity/confirm")
def identity_confirm(body: IdentityAction):
    run, ident = _identity_ctx(body)
    code = (body.customer_code or ident.get("suggested_customer_code") or "").upper()
    customer = next((c for c in run.get("customers", []) if c["customer_code"] == code), None)
    if not customer:
        raise HTTPException(400, "Choose a customer from the books / Customer Master to confirm this mapping.")
    pan = run.get("assessee_pan", "")
    previous = db.identity_mappings.find_one({"assessee_pan": pan, "tan": ident["tan"]}, NO_ID)
    mapping = {"assessee_pan": pan, "tan": ident["tan"], "deductor_name": ident["deductor_name"], "customer_code": code, "customer_name": customer["customer_name"], "reviewer": body.reviewer, "timestamp": now_iso(), "method": "HUMAN_CONFIRMED", "source": "identity_review", "run_id": run["run_id"], "note": body.note, "similarity_at_confirmation": ident.get("score"), "previous": {k: previous[k] for k in ("customer_code", "customer_name", "reviewer", "timestamp")} if previous else None}
    db.identity_mappings.replace_one({"assessee_pan": pan, "tan": ident["tan"]}, mapping, upsert=True)
    db.identity_decisions.delete_one({"assessee_pan": pan, "tan": ident["tan"]})
    job_id = _maybe_rerun(run, body)
    return {"message": "Mapping confirmed and saved for future reconciliations.", "mapping": mapping, "job_id": job_id}


@app.post("/api/identity/reject")
def identity_reject(body: IdentityAction):
    run, ident = _identity_ctx(body)
    pan = run.get("assessee_pan", "")
    rejected = (body.customer_code or ident.get("suggested_customer_code") or "").upper()
    prev = db.identity_decisions.find_one({"assessee_pan": pan, "tan": ident["tan"]}, NO_ID) or {}
    decision = {"assessee_pan": pan, "tan": ident["tan"], "action": "REJECTED", "rejected_codes": sorted(set(prev.get("rejected_codes", [])) | ({rejected} if rejected else set())), "reviewer": body.reviewer, "timestamp": now_iso(), "note": body.note, "run_id": run["run_id"]}
    db.identity_decisions.replace_one({"assessee_pan": pan, "tan": ident["tan"]}, decision, upsert=True)
    db.identity_mappings.delete_one({"assessee_pan": pan, "tan": ident["tan"]})
    job_id = _maybe_rerun(run, body)
    return {"message": f"Suggestion {rejected or ''} rejected. It will not be proposed again for this TAN.", "decision": decision, "job_id": job_id}


@app.post("/api/identity/keep-unmapped")
def identity_keep_unmapped(body: IdentityAction):
    run, ident = _identity_ctx(body)
    pan = run.get("assessee_pan", "")
    decision = {"assessee_pan": pan, "tan": ident["tan"], "action": "KEEP_UNMAPPED", "rejected_codes": [], "reviewer": body.reviewer, "timestamp": now_iso(), "note": body.note, "run_id": run["run_id"]}
    db.identity_decisions.replace_one({"assessee_pan": pan, "tan": ident["tan"]}, decision, upsert=True)
    db.identity_mappings.delete_one({"assessee_pan": pan, "tan": ident["tan"]})
    job_id = _maybe_rerun(run, body)
    return {"message": "TAN kept unmapped. Its 26AS entries stay classified as IDENTITY_UNMAPPED.", "decision": decision, "job_id": job_id}


class AliasBody(BaseModel):
    run_id: str
    alias: str
    customer_code: str
    reviewer: str = "CA Reviewer"
    rerun: bool = True


@app.post("/api/identity/alias")
def identity_alias(body: AliasBody):
    run = db.runs.find_one({"run_id": body.run_id}, NO_ID)
    if not run:
        raise HTTPException(404, "Run not found")
    code = body.customer_code.upper()
    customer = next((c for c in run.get("customers", []) if c["customer_code"] == code), None)
    if not customer:
        raise HTTPException(400, "Unknown customer code.")
    if not body.alias.strip():
        raise HTTPException(400, "Alias cannot be blank.")
    pan = run.get("assessee_pan", "")
    alias = {"assessee_pan": pan, "alias": body.alias.strip(), "alias_norm": core_name(body.alias), "customer_code": code, "customer_name": customer["customer_name"], "created_by": body.reviewer, "timestamp": now_iso(), "run_id": run["run_id"]}
    db.identity_aliases.replace_one({"assessee_pan": pan, "alias_norm": alias["alias_norm"]}, alias, upsert=True)
    job_id = _maybe_rerun(run, body) if body.rerun else None
    return {"message": f"Alias '{alias['alias']}' saved for {customer['customer_name']}.", "alias": alias, "job_id": job_id}


@app.get("/api/identity/mappings")
def identity_mappings(assessee_pan: str | None = None):
    q = {"assessee_pan": assessee_pan.upper()} if assessee_pan else {}
    return {"mappings": list(db.identity_mappings.find(q, NO_ID).sort("timestamp", DESCENDING)), "aliases": list(db.identity_aliases.find(q, NO_ID).sort("timestamp", DESCENDING)), "decisions": list(db.identity_decisions.find(q, NO_ID).sort("timestamp", DESCENDING))}


@app.get("/api/customers")
def customers(run_id: str, q: str | None = None):
    run = db.runs.find_one({"run_id": run_id}, {"_id": 0, "customers": 1})
    if not run:
        raise HTTPException(404, "Run not found")
    items = run.get("customers", [])
    if q:
        needle = q.strip().upper()
        items = [c for c in items if needle in c["customer_code"] or needle in c["customer_name"].upper() or needle in (c.get("pan") or "") or any(needle in t for t in c.get("tans", []))]
    return {"items": [{"customer_code": c["customer_code"], "customer_name": c["customer_name"], "pan": c.get("pan", ""), "gstin": c.get("gstin", ""), "tans": c.get("tans", []), "source": c.get("source")} for c in sorted(items, key=lambda c: c["customer_code"])][:50]}


# ---------- reports ----------
@app.get("/api/reports/available")
def reports_available(run_id: str | None = None):
    rid = _resolve_run_id(run_id)
    run = db.runs.find_one({"run_id": rid}, NO_ID) if rid else None
    keys = ("ca_reconciliation", "exceptions", "control_totals") if run and run.get("workflow") == "SALES_TDS_26AS" else REPORTS.keys()
    return {"run_id": rid, "reports": [{"key": k, "label": REPORTS[k], "available": bool(rid)} for k in keys], "formats": list(FORMATS)}


@app.get("/api/reports/export")
def export(report: str = "ca_reconciliation", format: str = "xlsx", run_id: str | None = None):
    if report not in REPORTS:
        raise HTTPException(400, "Unknown report")
    if format not in FORMATS:
        raise HTTPException(400, "Format must be xlsx, csv or pdf")
    rid = _resolve_run_id(run_id)
    run = db.runs.find_one({"run_id": rid, "status": "COMPLETED"}, NO_ID) if rid else None
    if not run:
        raise HTTPException(404, "No completed reconciliation run is available to report on.")
    if run.get("workflow") == "SALES_TDS_26AS" and report not in {"ca_reconciliation", "exceptions", "control_totals"}:
        raise HTTPException(400, "This report is not available for the Sales + TDS + 26AS workflow.")
    rows = list(db.results.find({"run_id": rid}, NO_ID).sort("seq", ASCENDING))
    if report == "ca_reconciliation" and run.get("workflow") != "SALES_TDS_26AS":
        commentaries = {
            item["relationship_id"]: item
            for item in db.reconciliation_relationship_commentaries.find({"run_id": rid, "is_current": True}, NO_ID)
        }
        for row in rows:
            commentary = commentaries.get(_relationship_id(row))
            row["ca_commentary"] = commentary.get("commentary") if commentary else None
            row["ca_commentary_status"] = commentary.get("review_status") if commentary else None
            row["ca_commentary_updated_by"] = commentary.get("updated_by") if commentary else None
    states = {s["result_id"]: strip(s) for s in db.exception_states.find({"run_id": rid})}
    title, meta_rows, cols, data = build_report(report, run, rows, states)
    content = render(format, title, meta_rows, cols, data)
    filename = f"{report}_{run.get('financial_year') or 'FY'}_{rid}.{format}".replace("/", "-")
    db.report_log.insert_one({"run_id": rid, "report": report, "format": format, "rows": len(data), "generated_at": now_iso(), "filename": filename})
    return Response(content, media_type=FORMATS[format], headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/reports/history")
def report_history(run_id: str | None = None, limit: int = 20):
    q = {"run_id": run_id} if run_id else {}
    return list(db.report_log.find(q, NO_ID).sort("generated_at", DESCENDING).limit(limit))


# ---------- settings ----------
class TdsRuleBody(BaseModel):
    rule_id: str | None = None
    section: str
    financial_year: str
    effective_from: str
    effective_to: str
    rate: str | float
    threshold: str | float | None = None
    threshold_type: str = "NO_THRESHOLD"
    calculation_base: str
    conditions: dict = {}
    source: str
    source_reference: str
    version: int = 1
    is_active: bool = True


def _rule_payload(body: TdsRuleBody, current_id: str | None = None):
    raw = body.model_dump()
    raw["rule_id"] = current_id or raw.get("rule_id") or f"RULE-{uuid.uuid4().hex[:10].upper()}"
    try:
        return validate_rule(raw, list(db.tds_rules.find({}, NO_ID)), exclude_id=current_id)
    except RuleError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/tds-rules")
def list_tds_rules(active_only: bool = False):
    query = {"is_active": True} if active_only else {}
    return {"rules": list(db.tds_rules.find(query, NO_ID).sort([("financial_year", DESCENDING), ("section", ASCENDING), ("version", DESCENDING)]))}


@app.post("/api/tds-rules")
def create_tds_rule(body: TdsRuleBody):
    rule = _rule_payload(body)
    rule.update({"created_at": now_iso(), "updated_at": now_iso()})
    try:
        db.tds_rules.insert_one(rule)
    except Exception:
        raise HTTPException(409, "Rule ID already exists.")
    return strip(rule)


@app.put("/api/tds-rules/{rule_id}")
def update_tds_rule(rule_id: str, body: TdsRuleBody):
    if not db.tds_rules.find_one({"rule_id": rule_id}):
        raise HTTPException(404, "TDS rule not found.")
    rule = _rule_payload(body, rule_id)
    rule["updated_at"] = now_iso()
    db.tds_rules.replace_one({"rule_id": rule_id}, rule)
    return rule


@app.post("/api/tds-rules/{rule_id}/deactivate")
def deactivate_tds_rule(rule_id: str):
    result = db.tds_rules.update_one({"rule_id": rule_id}, {"$set": {"is_active": False, "updated_at": now_iso(), "deactivated_at": now_iso()}})
    if not result.matched_count:
        raise HTTPException(404, "TDS rule not found.")
    return {"rule_id": rule_id, "is_active": False}


class SettingsBody(BaseModel):
    amount_tolerance_abs: float | None = None
    amount_tolerance_pct: float | None = None
    fuzzy_high_threshold: int | None = None
    fuzzy_review_threshold: int | None = None
    fuzzy_min_gap: int | None = None
    max_group_size: int | None = None
    # Rules are maintained by an authorised configuration client/API, never by
    # the matching engine.  No implicit statutory default is supplied.
    tds_rules: list[dict] | None = None


@app.get("/api/settings")
def settings():
    return {"settings": get_settings(), "defaults": DEFAULT_SETTINGS, "editable": True}


@app.put("/api/settings")
def update_settings(body: SettingsBody):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "amount_tolerance_abs" in updates and updates["amount_tolerance_abs"] < 0:
        raise HTTPException(400, "Absolute tolerance cannot be negative.")
    if "amount_tolerance_pct" in updates and not 0 <= updates["amount_tolerance_pct"] <= 100:
        raise HTTPException(400, "Percentage tolerance must be between 0 and 100.")
    for k in ("fuzzy_high_threshold", "fuzzy_review_threshold"):
        if k in updates and not 50 <= updates[k] <= 100:
            raise HTTPException(400, "Fuzzy thresholds must be between 50 and 100.")
    merged = {**get_settings(), **updates}
    if merged["fuzzy_review_threshold"] > merged["fuzzy_high_threshold"]:
        raise HTTPException(400, "Review threshold must not exceed the auto-map threshold.")
    if not 2 <= merged["max_group_size"] <= 8:
        raise HTTPException(400, "Group size must be between 2 and 8.")
    for rule in merged.get("tds_rules", []):
        if not isinstance(rule, dict) or not str(rule.get("section") or "").strip() or rule.get("rate") is None:
            raise HTTPException(400, "Each TDS rule requires at least section and rate.")
        try:
            rate = float(rule["rate"])
        except (TypeError, ValueError):
            raise HTTPException(400, "TDS rule rate must be numeric.")
        if rate < 0 or rate > 100:
            raise HTTPException(400, "TDS rule rate must be between 0 and 100.")
    db.settings.replace_one({"key": "reconciliation"}, {"key": "reconciliation", **merged, "updated_at": now_iso()}, upsert=True)
    return {"settings": merged, "defaults": DEFAULT_SETTINGS, "editable": True}


# Browser origin policy is controlled only through deployment environment
# variables. Keep credentials enabled and never use a wildcard origin.
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=allowed_frontend_origins(),
    allow_methods=CORS_METHODS,
    allow_headers=["*"],
)


@app.on_event("shutdown")
def shutdown_db_client():
    # Workers own collection handles, so they must stop before PyMongo closes
    # the process-wide client.
    source_processing_jobs.stop()
    books_validation_jobs.stop()
    client.close()
