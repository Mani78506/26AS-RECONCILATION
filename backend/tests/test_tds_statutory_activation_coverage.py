import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COVERAGE = ROOT / "docs" / "tds_compliance" / "statutory_activation_coverage.csv"
MANIFEST = ROOT / "backend" / "data" / "tds_statutory_rules" / "fy2026_27_ca_source_manifest.json"


def test_activation_coverage_accounts_for_every_source_row_and_exposes_nonactive_blockers():
    rows = list(csv.DictReader(COVERAGE.open(encoding="utf-8", newline="")))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(rows) == manifest["row_count"] == 92
    assert {row["source_row_id"] for row in rows} == {row["source_id"] for row in manifest["rows"]}
    assert {status: sum(row["current_status"] == status for row in rows) for status in (
        "ACTIVE_EXECUTABLE", "MISSING_PRODUCT_INPUT", "NOT_EXECUTABLE_YET", "SOURCE_AMBIGUOUS",
    )} == {
        "ACTIVE_EXECUTABLE": 14, "MISSING_PRODUCT_INPUT": 63,
        "NOT_EXECUTABLE_YET": 12, "SOURCE_AMBIGUOUS": 3,
    }
    for row in rows:
        if row["current_status"] != "ACTIVE_EXECUTABLE":
            assert row["blocker_category"]
            assert row["exact_blocker"]
            assert row["needed_to_unblock"]
    assert next(row for row in rows if row["source_row_id"] == "CA-FY2026-27-NR-38")["blocker_category"] == "SOURCE"
    for source_id in range(39, 45):
        assert next(row for row in rows if row["source_row_id"] == f"CA-FY2026-27-NR-{source_id:02d}")["blocker_category"] == "AGREEMENT_HISTORIC"
