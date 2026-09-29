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
CATALOG = ROOT / "data" / "tds_statutory_rules" / "official_tds_catalog.json"
REQUIRED = {"rule_id", "rule_version", "financial_year", "governing_law", "effective_from", "effective_to", "payment_nature", "provision_reference", "rate_type", "rate_value", "threshold_mode", "calculation_base", "source_authority", "source_reference", "source_verified_at", "status"}
SUPPORTED_THRESHOLD_MODES = {"NO_THRESHOLD", "PER_TRANSACTION", "AGGREGATE", "AGGREGATE_EXCESS", "PER_TRANSACTION_AND_AGGREGATE"}
KNOWN_CATEGORIES = {"contractor", "professional_fee", "technical_service", "commission", "brokerage", "rent_machinery", "rent_building", "interest", "purchase_of_goods", "director_remuneration", "insurance_commission", "ecommerce", "benefits_perquisites", "salary", "non_resident_payment"}


def load_catalog() -> tuple[dict, list[dict]]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Catalog rules must be a list.")
    return data, rules


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
    return errors


def document(rule: dict, catalog_version: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {**rule, "workflow": "TDS_COMPLIANCE", "scope": "GLOBAL", "lifecycle": "ACTIVE", "active": True, "version": 1, "catalog_version": catalog_version, "governing_act": rule["governing_law"], "section_reference": rule["provision_reference"], "rate": rule["rate_value"], "threshold_type": rule["threshold_mode"], "aggregate_financial_year_threshold": rule.get("aggregate_threshold"), "calculation_basis": rule["calculation_base"], "rounding_method": "HALF_UP", "rounding_precision": 2, "priority": 0, "source": rule["source_authority"], "created_at": now, "updated_at": now, "created_by": "VERIFIED_STATUTORY_CATALOG_IMPORT", "updated_by": "VERIFIED_STATUTORY_CATALOG_IMPORT"}


def coverage(rules: list[dict]) -> dict:
    configured = sorted({rule["payment_nature"] for rule in rules})
    return {"configured_categories": configured, "source_verification_required": sorted(KNOWN_CATEGORIES - set(configured)), "verified_rule_count": len(rules)}


def seed(db, rules: list[dict], catalog_version: str, dry_run: bool) -> dict:
    result = {"inserted": [], "skipped": [], "conflicts": []}
    for rule in rules:
        existing = db.tds_compliance_rules.find_one({"workflow": "TDS_COMPLIANCE", "rule_id": rule["rule_id"], "rule_version": rule["rule_version"]}, {"_id": 0})
        candidate = document(rule, catalog_version)
        if not existing:
            if not dry_run: db.tds_compliance_rules.insert_one(candidate)
            result["inserted"].append(rule["rule_id"]); continue
        comparable = {key: value for key, value in candidate.items() if key not in {"created_at", "updated_at"}}
        prior = {key: existing.get(key) for key in comparable}
        if prior == comparable: result["skipped"].append(rule["rule_id"])
        else: result["conflicts"].append({"rule_id": rule["rule_id"], "code": "CATALOG_VERSION_CONFLICT"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--validate", action="store_true"); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--coverage", action="store_true"); args = parser.parse_args()
    catalog, rules = load_catalog(); errors = {rule.get("rule_id", "UNKNOWN"): validate(rule) for rule in rules}; errors = {key: value for key, value in errors.items() if value}
    if errors: print(json.dumps({"valid": False, "errors": errors}, indent=2)); return 1
    if args.coverage: print(json.dumps(coverage(rules), indent=2)); return 0
    if args.validate: print(json.dumps({"valid": True, "rules": len(rules), "catalog_version": catalog.get("catalog_version")}, indent=2)); return 0
    load_dotenv(ROOT / ".env"); mongo = MongoClient(os.environ["MONGO_URL"]); db = mongo[os.environ["DB_NAME"]]
    result = seed(db, rules, catalog.get("catalog_version", "UNVERSIONED"), args.dry_run); mongo.close(); print(json.dumps(result, indent=2)); return 1 if result["conflicts"] else 0


if __name__ == "__main__": sys.exit(main())
