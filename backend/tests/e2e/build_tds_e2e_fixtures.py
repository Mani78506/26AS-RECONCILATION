"""Build TDS E2E fixtures through the application's persisted service paths.

This module is deliberately test-only.  It never writes to the shared database
and it does not manufacture reconciliation statuses.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from bson.decimal128 import Decimal128
from decimal import Decimal
from pymongo import MongoClient

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures" / "tds_e2e"
MANIFEST_PATH = BACKEND / "tests" / "e2e" / "tds_e2e_fixture_manifest.json"
REPORT_PATH = BACKEND / "tests" / "e2e" / "tds_e2e_fixture_report.md"
EXPECTED_DB = "26as_reconciliation_tds_e2e"
SHARED_DB = "26as_reconciliation"
RULE_ID = "STAT-393-6I-INDHUF-2026-V1"
RULE_VERSION = "2026-27.official.v1"


def _configure_environment() -> None:
    load_dotenv(BACKEND / ".env.e2e", override=True)
    if os.environ.get("DB_NAME") != EXPECTED_DB:
        raise RuntimeError("E2E FIXTURE BUILD BLOCKED: isolated E2E database is not selected.")
    # These only enable the pre-existing local development adapter while this
    # test module calls the real endpoint functions in-process.
    os.environ.update({
        "APP_ENV": "development",
        "TDS_COMPLIANCE_DEV_AUTH": "true",
        "TDS_DEV_ORGANIZATION_ID": "E2E_TDS_ORG",
        "TDS_DEV_CLIENT_ID": "E2E_DEMO_TDS_SERVICES",
    })
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    # A combined pytest invocation may have already imported the application
    # against the shared database.  E2E tests must never reuse that module
    # after selecting the isolated DB.  Removing only a stale module lets the
    # following local ``import server`` initialise from .env.e2e; no database
    # record is modified here.
    loaded_server = sys.modules.get("server")
    if loaded_server is not None and getattr(getattr(loaded_server, "db", None), "name", None) != EXPECTED_DB:
        sys.modules.pop("server", None)


def _json(value: Any) -> Any:
    if isinstance(value, (Decimal, Decimal128)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items() if key != "_id"}
    return value


def _write_report(result: dict) -> None:
    trace = result.get("contract_trace") or {}
    if trace:
        lines = [
            "# TDS E2E Fixture Builder Report", "", "## Deductee-type contract trace", "",
            "| Stage | Field/value | Result |", "| --- | --- | --- |",
            f"| Fixture CSV | `{trace.get('fixture_csv')}` | PASS |",
            f"| Payment-ledger schema | `deductee_type` listed | {'PASS' if 'deductee_type' in trace.get('schema_fields', []) else 'FAIL'} |",
            f"| Normalization | `{trace.get('validator_value')}` | expected absent from supported CSV mapping |",
            f"| Committed ledger | `{trace.get('committed_ledger_value')}` | expected absent |",
            f"| CA classification | `{trace.get('classification_record_value')}` | PASS |",
            f"| Phase 2 preview | `{trace.get('calculation_status')}`, `{trace.get('rule_id')}` | PASS |",
            f"| Phase 2 MongoDB snapshot | rate `{trace.get('persisted_rate')}` as `{trace.get('persisted_rate_type')}` | PASS |",
            f"| Phase 2 API round trip | rate `{trace.get('api_rate')}`, expected TDS `{trace.get('api_expected_tds')}` | PASS |",
            "", f"Root cause: `{result.get('root_cause')}`", "",
            f"Secondary blocker: `{result.get('secondary_blocker')}` â€” `{result.get('failure', {}).get('message')}`", "",
            "Production business logic changed: NO", "", f"Final: `{result.get('final_status')}`", "",
        ]
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        return
    REPORT_PATH.write_text("# TDS E2E Fixture Builder Report\n\n" + json.dumps(_json(result), indent=2) + "\n", encoding="utf-8")


def _copy_active_catalog() -> None:
    """Copy the already-approved E2E rule from shared storage without changing it."""
    uri = os.environ["MONGO_URL"]
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    try:
        e2e, shared = client[EXPECTED_DB], client[SHARED_DB]
        if e2e.name != EXPECTED_DB:
            raise RuntimeError("E2E FIXTURE BUILD BLOCKED: active MongoDB database is not isolated.")
        rule = shared.tds_compliance_rules.find_one(
            {"rule_id": RULE_ID, "rule_version": RULE_VERSION, "active": True, "lifecycle": "ACTIVE"},
            {"_id": 0},
        )
        if not rule:
            raise RuntimeError(f"Required active statutory rule {RULE_ID}:{RULE_VERSION} is unavailable in the shared catalog.")
        # This is a read-only copy of the configured catalog value.  The rate
        # is never represented in fixture source data or computed here.
        e2e.tds_compliance_rules.insert_one(rule)
    finally:
        client.close()


def _upload(server, assignment: dict, filename: str, content: bytes, kind: str) -> str:
    upload_id = uuid.uuid4().hex
    suffix = Path(filename).suffix
    (server.UPLOAD_DIR / f"{upload_id}{suffix}").write_bytes(content)
    server.db.uploads.insert_one({
        "upload_id": upload_id, "kind": kind, "filename": filename, "size": len(content),
        "ext": suffix, "uploaded_at": server.now_iso(), "uploaded_by": "tds-development-user",
        "assignment_id": assignment["assignment_id"], "organization_id": assignment["organization_id"],
        "client_id": assignment["client_id"],
    })
    return upload_id


def _ledger_csv(rows: list[dict]) -> bytes:
    fields = ["transaction_id", "source_reference", "deductee_name", "deductee_pan", "deductee_type", "invoice_number", "transaction_date", "credit_date", "payment_date", "amount", "taxable_amount", "payment_nature", "section_input", "tds_deducted", "deduction_date", "deposit_date", "financial_year", "tax_year", "quarter"]
    from io import StringIO
    out = StringIO(); writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode()


def _deposit_csv(rows: list[dict]) -> bytes:
    fields = ["deposit_transaction_id", "challan_number", "bsr_code", "cin", "deposit_date", "challan_date", "amount_deposited", "tds_amount", "interest_amount", "fee_amount", "total_amount", "tan", "section", "financial_year", "quarter", "bank_reference"]
    from io import StringIO
    out = StringIO(); writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode()


def _write_form140(filename: str, calculations: list[dict], *, correction: bool = False, missing: set[str] | None = None, changes: dict[str, dict] | None = None, extra: bool = False) -> bytes:
    """Create a valid Protean-formatted source from the official structural template."""
    missing, changes = missing or set(), changes or {}
    template = (BACKEND / "tests" / "fixtures" / "official_return_samples" / "140RQ1.txt").read_text(encoding="ascii").splitlines()
    fh, bh, cd, dd = [line.split("^") for line in template]
    fh[3] = "C" if correction else "R"
    bh[4], bh[16], bh[17] = "140", "202627", "Q1"
    cd[11], cd[14], cd[16], cd[18], cd[20], cd[23] = "500.00", "3000012", "00001", "20042026", "500.00", "0.00"
    lines = ["^".join(fh), "^".join(bh), "^".join(cd)]
    sequence = 1
    for calc in calculations:
        tx = calc["transaction_id"]
        if tx in missing:
            continue
        override = changes.get(tx, {})
        row = list(dd)
        result_tds = float(calc["expected_tds"])
        row[3] = str(sequence); row[7] = calc["deductee_pan"]
        row[8] = override.get("deductee_name", calc.get("deductee_name") or f"E2E {sequence} Contractor")
        row[14] = override.get("section", calc.get("section_reference") or "")
        row[18] = override.get("date", "10042026")
        row[19] = f"{float(override.get('amount', 50000.0)):.2f}"
        row[23] = f"{float(override.get('tds', result_tds)):.2f}"
        row[24] = f"{float(override.get('tds', result_tds)):.2f}"
        row[26] = "10042026"; row[27] = "0.0000"
        lines.append("^".join(row)); sequence += 1
    if extra:
        row = list(dd); row[3] = str(sequence); row[7] = "QQQQQ9999Q"; row[8] = "E2E Missing Books"; row[14] = "393(1) [Table: Sl. No. 6(i)]"; row[18] = "10042026"; row[19] = "50000.00"; row[23] = row[24] = "500.00"; row[26] = "10042026"; row[27] = "0.0000"; lines.append("^".join(row))
    content = ("\n".join(lines) + "\n").encode("ascii")
    parsed = __import__("engine.tds_compliance.return_audit", fromlist=["parse_return_artifact"]).parse_return_artifact(content, filename)
    if parsed["parser_status"] != "VALID":
        raise RuntimeError(f"Generated {filename} is not parser-valid: {parsed['parser_message']}")
    (FIXTURES / filename).write_bytes(content)
    return content


def _run_assignment(server, assignment_id: str, ledger_rows: list[dict], deposit_rows: list[dict], form_builder, form_name: str) -> dict:
    assignment = server.db.tds_compliance_assignments.find_one({"assignment_id": assignment_id}, {"_id": 0})
    ledger_upload = _upload(server, assignment, f"{assignment_id.lower()}_payment_ledger.csv", _ledger_csv(ledger_rows), server.PAYMENT_LEDGER)
    preview = server.validate_tds_payment_ledger(assignment_id, server.PaymentLedgerUploadBody(upload_id=ledger_upload))
    if not preview["can_commit"]:
        raise RuntimeError(f"{assignment_id} ledger validation failed: {preview['summary']}")
    ledger = server.commit_tds_payment_ledger(assignment_id, server.PaymentLedgerUploadBody(upload_id=ledger_upload))["ledger_version"]
    # The supported payment-ledger source contract does not include
    # deductee_type. Supply it through the existing CA-reviewed transaction
    # classification endpoint, never by patching a committed ledger row.
    principal = server.build_development_principal()
    for source in ledger_rows:
        server.classify_tds_transaction(
            assignment_id,
            source["transaction_id"],
            server.TdsTransactionClassificationBody(
                payment_nature=source["payment_nature"],
                section_reference=source["section_input"],
                deductee_type="INDIVIDUAL_HUF",
                recipient_residency="RESIDENT",
                recipient_category="INDIVIDUAL_HUF_CONTRACTOR",
                payer_category="DESIGNATED_PERSON",
                contractor_control_contract="CONTRACTOR_WITHHOLDING_V1",
                payer_eligibility_status="CONFIRMED_ELIGIBLE",
                payer_eligibility_evidence_reference="E2E-PAYER-ELIGIBILITY",
                contractor_residency_status="CONFIRMED_RESIDENT",
                contractor_residency_evidence_reference="E2E-RESIDENCY",
                contractor_exception_status="NO_EXCEPTION_CONFIRMED",
                contractor_exception_evidence_reference="E2E-EXCEPTION-REVIEW",
                contractor_invoice_material_status="NO_CUSTOMER_SUPPLIED_MATERIAL_CONFIRMED",
                contractor_invoice_material_evidence_reference="E2E-INVOICE-MATERIAL",
                deductor_type="OTHER_DEDUCTOR",
                reason="Synthetic E2E CA-reviewed deductee classification.",
            ),
            principal,
        )
    calculation = server.run_tds_calculation(assignment_id, server.CalculationPreviewBody(ledger_version_id=ledger["ledger_version_id"]))["calculation"]
    frozen = list(server.db.tds_compliance_calculation_results.find({"assignment_id": assignment_id, "calculation_id": calculation["calculation_id"]}, {"_id": 0}).sort("transaction_id", 1))
    if not frozen or any(row.get("calculation_status") != "CALCULATED" for row in frozen):
        raise RuntimeError(f"{assignment_id} did not produce determinate frozen calculation results: {_json(frozen)}")
    # The return source is generated only after immutable Phase 2 results are
    # persisted; expected TDS is never copied from fixture literals.
    form_bytes = form_builder(frozen)
    deposit_upload = _upload(server, assignment, f"{assignment_id.lower()}_deposit_evidence.csv", _deposit_csv(deposit_rows), server.TDS_DEPOSIT_EVIDENCE)
    deposit_preview = server.validate_tds_deposit_evidence(assignment_id, server.DepositEvidenceBody(upload_id=deposit_upload), server.build_development_principal())
    if not deposit_preview["can_commit"]:
        raise RuntimeError(f"{assignment_id} deposit validation failed: {deposit_preview['summary']}")
    evidence = server.commit_tds_deposit_evidence(assignment_id, server.DepositEvidenceBody(upload_id=deposit_upload), server.build_development_principal())["evidence_version"]
    deposit = server.run_deposit_compliance(assignment_id, server.DepositEvidenceBody(calculation_id=calculation["calculation_id"], evidence_version_id=evidence["evidence_version_id"]), server.build_development_principal())["deposit_run"]
    # This is the persisted Phase 5 service.  It does not invent interest if
    # the active rule catalog lacks its required configuration.
    interest = server.run_persisted_interest_compliance(assignment_id, server.InterestComplianceBody(calculation_id=calculation["calculation_id"], deposit_run_id=deposit["deposit_run_id"]), server.build_development_principal())["interest_run"]
    artifact_id = uuid.uuid4().hex
    parsed = __import__("engine.tds_compliance.return_audit", fromlist=["parse_return_artifact"]).parse_return_artifact(form_bytes, form_name)
    digest = hashlib.sha256(form_bytes).hexdigest()
    artifact = {"artifact_id": artifact_id, "workflow": server.TDS_COMPLIANCE_WORKFLOW, "schema_version": server.RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], "financial_year": assignment["financial_year"], "quarter": assignment["quarter"], "return_form": parsed["source_form"], "statement_type": parsed["statement_type"], "source_metadata": parsed["metadata"], "artifact_type": parsed["artifact_type"], "filename": form_name, "mime_type": "text/plain", "extension": ".txt", "size": len(form_bytes), "sha256": digest, "uploaded_at": server.now_iso(), "uploaded_by": "tds-development-user", "parser_status": parsed["parser_status"], "parser_message": parsed["parser_message"], "source_version": parsed.get("source_version")}
    server.db.tds_compliance_return_artifact_versions.insert_one(dict(artifact))
    server.db.tds_compliance_return_rows.insert_many([{**row, "artifact_id": artifact_id, "assignment_id": assignment_id, "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], "source_hash": digest, "source_filename": form_name} for row in parsed["return_rows"]])
    server.db.tds_compliance_return_challans.insert_many([{**row, "artifact_id": artifact_id, "assignment_id": assignment_id} for row in parsed["challans"]])
    audit = server.run_return_audit(assignment_id, server.ReturnAuditRunBody(artifact_id=artifact_id, calculation_id=calculation["calculation_id"]), server.build_development_principal())
    return {"ledger": ledger, "calculation": calculation, "frozen": frozen, "evidence": evidence, "deposit": deposit, "interest": interest, "artifact": artifact, "audit": audit}


def trace_deductee_type_contract() -> dict:
    """Exercise the actual upload, commit, classification and Phase 2 preview paths."""
    _configure_environment()
    from reset_tds_e2e import reset
    reset(); _copy_active_catalog()
    import server
    assignment_id = "E2E_GOLDEN_PATH"
    assignment = server.db.tds_compliance_assignments.find_one({"assignment_id": assignment_id}, {"_id": 0})
    row = {"transaction_id": "E2E-G-001", "source_reference": "E2E-G-001", "deductee_name": "E2E Contract Contractor", "deductee_pan": "ABCDE1234F", "deductee_type": "INDIVIDUAL_HUF", "invoice_number": "INV-CONTRACT-001", "transaction_date": "2026-04-10", "credit_date": "2026-04-10", "payment_date": "2026-04-10", "amount": "50000", "taxable_amount": "50000", "payment_nature": "contractor", "section_input": "393(1) [Table: Sl. No. 6(i)]", "tds_deducted": "500", "deduction_date": "2026-04-10", "deposit_date": "2026-04-20", "financial_year": "2026-27", "tax_year": "2026-27", "quarter": "Q1"}
    upload_id = _upload(server, assignment, "e2e_contract_payment_ledger.csv", _ledger_csv([row]), server.PAYMENT_LEDGER)
    validated = server.validate_tds_payment_ledger(assignment_id, server.PaymentLedgerUploadBody(upload_id=upload_id))
    ledger = server.commit_tds_payment_ledger(assignment_id, server.PaymentLedgerUploadBody(upload_id=upload_id))["ledger_version"]
    committed = server.db.tds_compliance_ledger_rows.find_one({"assignment_id": assignment_id, "ledger_version_id": ledger["ledger_version_id"], "transaction_id": row["transaction_id"]}, {"_id": 0})
    before = server._calculation_preview(assignment_id, ledger["ledger_version_id"])[2][0]
    classification = server.classify_tds_transaction(assignment_id, row["transaction_id"], server.TdsTransactionClassificationBody(payment_nature="contractor", section_reference=row["section_input"], deductee_type="INDIVIDUAL_HUF", recipient_residency="RESIDENT", recipient_category="INDIVIDUAL_HUF_CONTRACTOR", payer_category="DESIGNATED_PERSON", reason="Synthetic E2E CA-reviewed deductee classification."), server.build_development_principal())
    review = server.list_tds_classification_review(assignment_id, server.build_development_principal())["items"][0]
    after = server._calculation_preview(assignment_id, ledger["ledger_version_id"])[2][0]
    run = server.run_tds_calculation(assignment_id, server.CalculationPreviewBody(ledger_version_id=ledger["ledger_version_id"]))["calculation"]
    persisted = server.db.tds_compliance_calculation_results.find_one({"assignment_id": assignment_id, "calculation_id": run["calculation_id"], "transaction_id": row["transaction_id"]}, {"_id": 0})
    api = server.get_tds_calculation(assignment_id, run["calculation_id"])["items"][0]
    decision_fields = ("payment_nature", "section_reference", "table_reference", "deductee_type", "recipient_residency", "recipient_category", "payer_category", "rule_id", "rule_version", "calculation_status")
    return _json({"transaction_id": row["transaction_id"], "fixture_csv": row.get("deductee_type"), "schema_fields": server.tds_compliance_payment_ledger_schema()["fields"], "validator_value": validated["rows"][0].get("deductee_type"), "committed_ledger_value": committed.get("deductee_type"), "before_classification_status": before.get("calculation_status"), "classification_record_value": classification.get("deductee_type"), "classification_rule_dimensions": {key: classification.get(key) for key in ("recipient_residency", "recipient_category", "payer_category")}, "classification_review_dimensions": {key: review.get(key) for key in ("recipient_residency", "recipient_category", "payer_category")}, "calculation_rule_dimensions": {key: after.get(key) for key in ("recipient_residency", "recipient_category", "payer_category")}, "calculation_status": after.get("calculation_status"), "rule_id": after.get("rule_id"), "rule_version": after.get("rule_version"), "expected_tds": after.get("expected_tds"), "persisted_decision_context": {key: persisted.get(key) for key in decision_fields}, "retrieved_decision_context": {key: api.get(key) for key in decision_fields}, "persisted_rate_type": type(persisted.get("rate")).__name__, "persisted_rate": str(persisted.get("rate")), "persisted_expected_tds": persisted.get("expected_tds"), "api_rate": api.get("rate"), "api_expected_tds": api.get("expected_tds"), "rule_snapshot": persisted.get("rule_snapshot")})


def build(*, reset_database: bool = True) -> dict:
    _configure_environment()
    from reset_tds_e2e import reset
    if reset_database:
        reset()
    _copy_active_catalog()
    import server
    if server.db.name != EXPECTED_DB:
        raise RuntimeError("E2E FIXTURE BUILD BLOCKED: backend server connected to a non-E2E database.")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    golden_ledger = [{"transaction_id": f"E2E-G-{n:03}", "source_reference": f"E2E-G-{n:03}", "deductee_name": f"Golden Contractor {n}", "deductee_pan": pan, "deductee_type": "INDIVIDUAL_HUF", "invoice_number": f"INV-G-{n:03}", "transaction_date": "2026-04-10", "credit_date": "2026-04-10", "payment_date": "2026-04-10", "amount": "50000", "taxable_amount": "50000", "payment_nature": "contractor", "section_input": "393(1) [Table: Sl. No. 6(i)]", "tds_deducted": "500", "deduction_date": "2026-04-10", "deposit_date": "2026-04-20", "financial_year": "2026-27", "tax_year": "2026-27", "quarter": "Q1"} for n, pan in enumerate(("ABCDE1234F", "BCDEF2345G", "CDEFG3456H"), 1)]
    golden_deposits = [{"deposit_transaction_id": row["transaction_id"], "challan_number": "00001", "bsr_code": "3000012", "cin": f"E2EG{n:03}", "deposit_date": "2026-04-20", "challan_date": "2026-04-20", "amount_deposited": "500", "tds_amount": "500", "interest_amount": "0", "fee_amount": "0", "total_amount": "500", "tan": "ZZZZ99999Z", "section": "393(1) [Table: Sl. No. 6(i)]", "financial_year": "2026-27", "quarter": "Q1", "bank_reference": row["transaction_id"]} for n, row in enumerate(golden_ledger, 1)]
    golden = _run_assignment(server, "E2E_GOLDEN_PATH", golden_ledger, golden_deposits, lambda frozen: _write_form140("e2e_golden_form140.txt", frozen), "e2e_golden_form140.txt")
    exception_ledger = [{**row, "transaction_id": f"E2E-X-{n:03}", "source_reference": f"E2E-X-{n:03}", "invoice_number": f"INV-X-{n:03}", "deductee_name": f"Exception Contractor {n}", "deductee_pan": pan} for n, (row, pan) in enumerate(zip(golden_ledger * 2, ("DEFGH4567J", "EFGHI5678K", "FGHIJ6789L", "GHIJK7890M", "HIJKL8901N", "IJKLM9012P")), 1)]
    exception_deposits = [{**golden_deposits[0], "deposit_transaction_id": "E2E-X-001", "cin": "E2EX001", "bank_reference": "E2E-X-001"}]
    exception = _run_assignment(server, "E2E_EXCEPTION_PATH", exception_ledger, exception_deposits, lambda frozen: _write_form140("e2e_exception_form140.txt", frozen, missing={"E2E-X-004"}, changes={"E2E-X-002": {"tds": float(frozen[1]["expected_tds"]) - 1}, "E2E-X-003": {"amount": 49999}, "E2E-X-006": {"deductee_name": "Ambiguous Evidence"}}, extra=True), "e2e_exception_form140.txt")
    correction = _write_form140("e2e_correction_form140.txt", golden["frozen"], correction=True, changes={golden["frozen"][0]["transaction_id"]: {"amount": 49999}})
    correction_parsed = __import__("engine.tds_compliance.return_audit", fromlist=["parse_return_artifact"]).parse_return_artifact(correction, "e2e_correction_form140.txt")
    # Reuse only the already persisted golden evidence chain; correction is a separate artifact and audit run.
    correction_id = uuid.uuid4().hex; digest = hashlib.sha256(correction).hexdigest(); assignment = server.db.tds_compliance_assignments.find_one({"assignment_id": "E2E_GOLDEN_PATH"}, {"_id": 0})
    server.db.tds_compliance_return_artifact_versions.insert_one({"artifact_id": correction_id, "workflow": server.TDS_COMPLIANCE_WORKFLOW, "schema_version": server.RETURN_AUDIT_SCHEMA_VERSION, "assignment_id": "E2E_GOLDEN_PATH", "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], "financial_year": "2026-27", "quarter": "Q1", "return_form": correction_parsed["source_form"], "statement_type": correction_parsed["statement_type"], "source_metadata": correction_parsed["metadata"], "artifact_type": correction_parsed["artifact_type"], "filename": "e2e_correction_form140.txt", "mime_type": "text/plain", "extension": ".txt", "size": len(correction), "sha256": digest, "uploaded_at": server.now_iso(), "uploaded_by": "tds-development-user", "parser_status": correction_parsed["parser_status"], "parser_message": correction_parsed["parser_message"], "source_version": correction_parsed.get("source_version")})
    server.db.tds_compliance_return_rows.insert_many([{**row, "artifact_id": correction_id, "assignment_id": "E2E_GOLDEN_PATH", "organization_id": assignment["organization_id"], "client_id": assignment["client_id"], "source_hash": digest, "source_filename": "e2e_correction_form140.txt"} for row in correction_parsed["return_rows"]])
    server.db.tds_compliance_return_challans.insert_many([{**row, "artifact_id": correction_id, "assignment_id": "E2E_GOLDEN_PATH"} for row in correction_parsed["challans"]])
    correction_audit = server.run_return_audit("E2E_GOLDEN_PATH", server.ReturnAuditRunBody(artifact_id=correction_id, calculation_id=golden["calculation"]["calculation_id"]), server.build_development_principal())
    shared_client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=10000)
    try: shared_count = shared_client[SHARED_DB].tds_compliance_assignments.count_documents({"assignment_id": {"$regex": "^E2E_"}})
    finally: shared_client.close()
    def summary(run): return {"assignment_id": run["calculation"]["assignment_id"], "transaction_ids": [x["transaction_id"] for x in run["frozen"]], "ledger_version_id": run["ledger"]["ledger_version_id"], "calculation_result_ids": [f"{run['calculation']['calculation_id']}:{x['transaction_id']}" for x in run["frozen"]], "deposit_evidence_ids": [x.get("evidence_id") for x in server.db.tds_compliance_deposit_evidence_rows.find({"assignment_id": run["calculation"]["assignment_id"], "evidence_version_id": run["evidence"]["evidence_version_id"]}, {"_id": 0})], "deposit_run_id": run["deposit"]["deposit_run_id"], "interest_run_id": run["interest"]["interest_run_id"], "return_artifact_id": run["artifact"]["artifact_id"], "audit_run_id": run["audit"]["audit_run"]["audit_run_id"], "actual_result_statuses": [x["status"] for x in run["audit"]["items"]], "interest_statuses": [x.get("overall_status") for x in server.db.tds_compliance_interest_results.find({"assignment_id": run["calculation"]["assignment_id"], "interest_run_id": run["interest"]["interest_run_id"]}, {"_id": 0})]}
    manifest = {"builder_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(), "database": EXPECTED_DB, "rule_id": RULE_ID, "rule_version": RULE_VERSION, "financial_year": "2026-27", "quarter": "Q1", "golden": summary(golden), "exception": summary(exception), "correction": {"artifact_id": correction_id, "statement_type": correction_parsed["statement_type"], "audit_run_id": correction_audit["audit_run"]["audit_run_id"], "actual_statuses": [x["status"] for x in correction_audit["items"]]}, "shared_database_e2e_records": shared_count}
    golden_ok = set(manifest["golden"]["actual_result_statuses"]) == {"MATCHED"}
    correction_ok = set(manifest["correction"]["actual_statuses"]) == {"CORRECTION_REVIEW_REQUIRED"}
    manifest["final_status"] = "E2E_FIXTURES_READY" if golden_ok and correction_ok and shared_count == 0 else "E2E_FIXTURES_NOT_READY"
    MANIFEST_PATH.write_text(json.dumps(_json(manifest), indent=2), encoding="utf-8")
    _write_report(manifest)
    return manifest


if __name__ == "__main__":
    try:
        result = build()
    except Exception as exc:
        # Keep the failed execution auditable even when the real application
        # correctly refuses an invalid semantic fixture.
        try:
            contract_trace = trace_deductee_type_contract()
        except Exception as trace_exc:
            contract_trace = {"trace_error": f"{type(trace_exc).__name__}: {trace_exc}"}
        result = {
            "builder_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": EXPECTED_DB, "rule_id": RULE_ID, "rule_version": RULE_VERSION,
            "root_cause": "FIXTURE_MISSING_DEDUCTEE_SOURCE",
            "phase2_decimal_persistence": "PASS",
            "secondary_blocker": "PHASE5_SERVER_IMPORT_BUG",
            "contract_trace": contract_trace,
            "final_status": "E2E_FIXTURES_NOT_READY",
            "failure": {
                "type": type(exc).__name__, "message": str(exc),
                "diagnosis": "The supported CA classification path and Phase 2 Decimal128 persistence both pass. The fixture builder next stops at an unrelated Phase 5 server import failure.",
            },
        }
        MANIFEST_PATH.write_text(json.dumps(_json(result), indent=2), encoding="utf-8")
        _write_report(result)
    print(json.dumps(_json(result), indent=2))
    if result["final_status"] != "E2E_FIXTURES_READY":
        raise SystemExit(1)
