"""Idempotent importer for the verified statutory TDS catalog.

Run from the backend directory:
  python seeders/seed_tds_statutory_catalog.py --validate
  python seeders/seed_tds_statutory_catalog.py --dry-run
  python seeders/seed_tds_statutory_catalog.py --coverage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.tds_compliance.source_traceability import (
    catalog_activation_errors,
    source_traceability_errors,
    source_traceability_snapshot,
)

CATALOG = ROOT / "data" / "tds_statutory_rules" / "official_tds_catalog.json"
CATALOG_DIR = CATALOG.parent
REQUIRED = {"rule_id", "rule_version", "financial_year", "governing_law", "effective_from", "effective_to", "payment_nature", "provision_reference", "rate_type", "rate_value", "threshold_mode", "calculation_base", "source_authority", "source_reference", "status"}
SUPPORTED_THRESHOLD_MODES = {"NO_THRESHOLD", "PER_TRANSACTION", "AGGREGATE", "AGGREGATE_EXCESS", "PER_TRANSACTION_AND_AGGREGATE"}
KNOWN_CATEGORIES = {"contractor", "professional_fee", "technical_service", "commission", "brokerage", "rent_machinery", "rent_building", "interest", "purchase_of_goods", "director_remuneration", "insurance_commission", "ecommerce", "benefits_perquisites", "salary", "non_resident_payment"}
TRACEABILITY_DOCUMENT_FIELDS = {
    "source_provision_reference", "source_url", "source_document_title",
    "source_retrieved_at", "source_verification_evidence",
    "source_traceability", "source_traceability_status",
}


def load_catalog() -> tuple[dict, list[dict]]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Catalog rules must be a list.")
    return data, rules


def source_manifest_errors(catalog: dict) -> list[str]:
    """Validate the transcribed source inventory without making it executable.

    A manifest is intentionally distinct from `rules`: it preserves all source
    rows, including rows with no rate or unresolved qualification, while rule
    lifecycle controls decide what may calculate.
    """
    errors: list[str] = []
    for manifest_ref in catalog.get("source_manifests", []):
        filename = manifest_ref.get("manifest_file")
        path = CATALOG_DIR / str(filename or "")
        if not filename or not path.is_file():
            errors.append(f"missing source manifest {filename}")
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"invalid source manifest {filename}")
            continue
        rows = manifest.get("rows")
        required = {"source_id", "source_page", "recipient_residency", "recipient_class", "2025_act_section", "2025_act_table_sl_no", "1961_act_section", "nature_of_payment", "rate", "threshold", "surcharge_or_hec", "conditions", "source_paragraph", "source_status"}
        if not isinstance(rows, list) or len(rows) != manifest_ref.get("row_count"):
            errors.append(f"source manifest row count mismatch {filename}")
            continue
        if len({row.get("source_id") for row in rows}) != len(rows):
            errors.append(f"source manifest has duplicate source IDs {filename}")
        for index, row in enumerate(rows, 1):
            missing = required - set(row)
            if missing:
                errors.append(f"source manifest row {index} missing {', '.join(sorted(missing))}")
    return errors


def validate(rule: dict) -> list[str]:
    missing = sorted(field for field in REQUIRED if rule.get(field) in (None, ""))
    errors = [f"missing {field}" for field in missing]
    if rule.get("effective_from", "") > rule.get("effective_to", ""):
        errors.append("effective dates are reversed")
    try:
        if float(rule.get("rate_value", "-1")) < 0: errors.append("negative rate")
    except (TypeError, ValueError): errors.append("invalid rate")
    for field in ("per_transaction_threshold", "aggregate_threshold"):
        if rule.get(field) not in (None, ""):
            try:
                if float(rule[field]) < 0: errors.append(f"negative {field}")
            except (TypeError, ValueError): errors.append(f"invalid {field}")
    if rule.get("threshold_mode") not in SUPPORTED_THRESHOLD_MODES: errors.append("unsupported threshold mode")
    if rule.get("threshold_mode") == "PER_TRANSACTION_AND_AGGREGATE" and (rule.get("per_transaction_threshold") in (None, "") or rule.get("aggregate_threshold") in (None, "")):
        errors.append("dual threshold requires both thresholds")
    status = rule.get("status", "DRAFT")
    if status not in {"DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE"}:
        errors.append("unsupported catalog status")
    errors.extend(source_traceability_errors(rule))
    if status == "ACTIVE":
        errors.extend(catalog_activation_errors(rule))
    return errors


def document(rule: dict, catalog_version: str) -> dict:
    """Translate a validated global statutory catalog row for runtime use.

    A verified ACTIVE catalog row is eligible through this source-controlled
    import path. Manual and client-specific rules never use this function and
    remain subject to the API's governed approval lifecycle.
    """
    now = datetime.now(timezone.utc).isoformat()
    active = rule.get("status") == "ACTIVE"
    if active:
        errors = catalog_activation_errors(rule)
        if errors:
            raise ValueError("ACTIVE catalog rule requires " + ", ".join(errors))
    lifecycle = "ACTIVE" if active else "DRAFT"
    actor = "VERIFIED_STATUTORY_CATALOG_IMPORT" if active else "CATALOG_DRAFT_IMPORT"
    candidate = {**rule, "workflow": "TDS_COMPLIANCE", "scope": "GLOBAL", "lifecycle": lifecycle, "active": active, "version": 1, "catalog_version": catalog_version, "governing_act": rule["governing_law"], "section_reference": rule["provision_reference"], "rate": rule["rate_value"], "threshold_type": rule["threshold_mode"], "aggregate_financial_year_threshold": rule.get("aggregate_threshold"), "excess_only": rule.get("threshold_mode") == "AGGREGATE_EXCESS", "calculation_basis": rule["calculation_base"], "rounding_method": "HALF_UP", "rounding_precision": 2, "priority": 0, "source": rule["source_authority"], "created_at": now, "updated_at": now, "created_by": actor, "updated_by": actor}
    traceability = source_traceability_snapshot(candidate)
    return {**candidate, "source_traceability": traceability, "source_traceability_status": traceability["status"]}


def is_traceability_only_difference(existing: dict, candidate: dict) -> bool:
    """Allow safe enrichment of legacy catalog records without rule changes."""
    changed = {
        key for key, value in candidate.items()
        if key not in {"created_at", "updated_at"} and existing.get(key) != value
    }
    return bool(changed) and changed <= TRACEABILITY_DOCUMENT_FIELDS


def coverage(rules: list[dict]) -> dict:
    configured = sorted({rule["payment_nature"] for rule in rules})
    return {"configured_categories": configured, "source_verification_required": sorted(KNOWN_CATEGORIES - set(configured)), "verified_rule_count": len(rules)}


def seed(db, rules: list[dict], catalog_version: str, dry_run: bool) -> dict:
    result = {"inserted": [], "traceability_updated": [], "skipped": [], "conflicts": []}
    for rule in rules:
        existing = db.tds_compliance_rules.find_one({"workflow": "TDS_COMPLIANCE", "rule_id": rule["rule_id"], "rule_version": rule["rule_version"]}, {"_id": 0})
        candidate = document(rule, catalog_version)
        if not existing:
            if not dry_run: db.tds_compliance_rules.insert_one(candidate)
            result["inserted"].append(rule["rule_id"]); continue
        comparable = {key: value for key, value in candidate.items() if key not in {"created_at", "updated_at"}}
        prior = {key: existing.get(key) for key in comparable}
        if prior == comparable: result["skipped"].append(rule["rule_id"])
        elif is_traceability_only_difference(existing, candidate):
            if not dry_run:
                db.tds_compliance_rules.update_one(
                    {"workflow": "TDS_COMPLIANCE", "rule_id": rule["rule_id"], "rule_version": rule["rule_version"]},
                    {"$set": {key: candidate.get(key) for key in TRACEABILITY_DOCUMENT_FIELDS}},
                )
            result["traceability_updated"].append(rule["rule_id"])
        else: result["conflicts"].append({"rule_id": rule["rule_id"], "code": "CATALOG_VERSION_CONFLICT"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--validate", action="store_true"); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--coverage", action="store_true"); args = parser.parse_args()
    catalog, rules = load_catalog(); errors = {rule.get("rule_id", "UNKNOWN"): validate(rule) for rule in rules}; errors = {key: value for key, value in errors.items() if value}; manifest_errors = source_manifest_errors(catalog)
    if manifest_errors: errors["SOURCE_MANIFEST"] = manifest_errors
    if errors: print(json.dumps({"valid": False, "errors": errors}, indent=2)); return 1
    if args.coverage: print(json.dumps(coverage(rules), indent=2)); return 0
    if args.validate: print(json.dumps({"valid": True, "rules": len(rules), "catalog_version": catalog.get("catalog_version")}, indent=2)); return 0
    load_dotenv(ROOT / ".env"); mongo = MongoClient(os.environ["MONGO_URL"]); db = mongo[os.environ["DB_NAME"]]
    result = seed(db, rules, catalog.get("catalog_version", "UNVERSIONED"), args.dry_run); mongo.close(); print(json.dumps(result, indent=2)); return 1 if result["conflicts"] else 0


if __name__ == "__main__": sys.exit(main())
