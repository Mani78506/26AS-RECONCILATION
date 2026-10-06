import csv
import json
from pathlib import Path

from seeders.seed_tds_statutory_catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "tds_statutory_rules" / "fy2026_27_ca_source_manifest.json"
RECONCILIATION = ROOT.parent / "docs" / "tds_compliance" / "statutory_source_reconciliation.csv"
ALLOWED_RESULTS = {"SUPPORTED_FOR_CONFIGURATION", "SOURCE_AMBIGUOUS", "MISSING_PRODUCT_INPUT", "SOURCE_ROW_UNRESOLVED", "NOT_EXECUTABLE_YET"}


def _rows():
    with RECONCILIATION.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_reconciliation_has_one_record_for_every_transcribed_source_row():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = _rows()
    assert len(rows) == manifest["row_count"] == 92
    assert {row["source_row_reference"] for row in rows} == {row["source_id"] for row in manifest["rows"]}
    assert {row["reconciliation_result"] for row in rows} <= ALLOWED_RESULTS
    assert sum(row["reconciliation_result"] == "SOURCE_ROW_UNRESOLVED" for row in rows) == 0


def test_category_b_discrepancy_is_resolved_by_the_distinct_section_197_source_row():
    category_b = [row for row in _rows() if row["source_row_reference"].startswith("CA-FY2026-27-NR-")]
    resolved = next(row for row in category_b if row["source_row_reference"] == "CA-FY2026-27-NR-33")
    assert len(category_b) == 46
    assert {key: resolved[key] for key in ("section_table", "old_section", "payment_nature", "rate_percent", "reconciliation_result")} == {
        "section_table": "393(2) / 17", "old_section": "195(1)", "payment_nature": "NON_RESIDENT_CAPITAL_GAIN", "rate_percent": "12.5", "reconciliation_result": "MISSING_PRODUCT_INPUT",
    }


def test_verified_source_rows_are_executable_once_in_catalog():
    executable = [row for row in _rows() if row["current_executable_rule_status"] == "EXECUTABLE_ACTIVE"]
    assert {row["source_row_reference"] for row in executable} == {"CA-FY2026-27-RES-05", "CA-FY2026-27-RES-20", "CA-FY2026-27-RES-32", "CA-FY2026-27-RES-34", "CA-FY2026-27-RES-35", "CA-FY2026-27-RES-36", "CA-FY2026-27-RES-37", "CA-FY2026-27-RES-38", "CA-FY2026-27-RES-39", "CA-FY2026-27-RES-40", "CA-FY2026-27-RES-41", "CA-FY2026-27-RES-42", "CA-FY2026-27-RES-45", "CA-FY2026-27-RES-46"}

def test_catalog_contractors_remain_traceable_and_unmapped_source_rows_remain_non_executable():
    catalog, _ = load_catalog()
    rules = {rule["rule_id"]: rule for rule in catalog["rules"]}
    for rule_id in ("STAT-194C-INDHUF-2025-V1", "STAT-393-6I-INDHUF-2026-V1"):
        assert rules[rule_id]["status"] == "ACTIVE"
        assert rules[rule_id]["source_traceability_status"] == "VERIFIED"
        assert rules[rule_id]["source_url"]
        assert rules[rule_id]["source_verification_evidence"]
    lottery = next(row for row in _rows() if row["source_row_reference"] == "CA-FY2026-27-RES-39")
    assert lottery["current_rule_id"] == "STAT-393-3-LOTTERY-2026-V1"
    assert lottery["current_executable_rule_status"] == "EXECUTABLE_ACTIVE"
    assert lottery["section_table"] == "393(3) / 1"
    assert lottery["old_section"] == "194B"
    assert lottery["threshold"] == "10000_PER_TRANSACTION"
    commission = next(row for row in _rows() if row["source_row_reference"] == "CA-FY2026-27-RES-05")
    assert commission["current_rule_id"] == "STAT-393-1II-COMMISSION-BROKERAGE-2026-V1"
    assert commission["manifest_source_status"] == "SOURCE_VERIFIED"
    assert commission["current_executable_rule_status"] == "EXECUTABLE_ACTIVE"
    horse = next(row for row in _rows() if row["source_row_reference"] == "CA-FY2026-27-RES-41")
    assert horse["current_rule_id"] == "STAT-393-3-HORSE-RACE-2026-V1"
    assert horse["current_executable_rule_status"] == "EXECUTABLE_ACTIVE"
    deferred = {row["source_row_reference"] for row in _rows() if row["current_executable_rule_status"] != "EXECUTABLE_ACTIVE"}
    assert {"CA-FY2026-27-NR-04", "CA-FY2026-27-NR-05"} <= deferred
