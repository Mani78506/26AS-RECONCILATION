import csv
from pathlib import Path

MATRIX = Path(__file__).resolve().parents[2] / "docs" / "tds_compliance" / "statutory_product_input_matrix.csv"


def test_product_input_matrix_covers_each_current_missing_input_row_once():
    reconciliation = list(csv.DictReader((MATRIX.parent / "statutory_source_reconciliation.csv").open(encoding="utf-8", newline="")))
    matrix = list(csv.DictReader(MATRIX.open(encoding="utf-8", newline="")))
    missing = {row["source_row_reference"] for row in reconciliation if row["reconciliation_result"] == "MISSING_PRODUCT_INPUT"}
    # The executable catalogue now covers five of the formerly deferred rows;
    # the matrix remains aligned with the 63 rows still needing product input.
    assert len(missing) == 63
    assert {row["source_row_id"] for row in matrix} == missing
    assert len(matrix) == len(missing)
    assert {row["fact_category"] for row in matrix} == {
        "PARTY_CAPACITY", "ASSET_INSTRUMENT_SCHEME", "SERVICE_ACTIVITY_CHANNEL", "AGREEMENT_HISTORIC_PROVISION",
    }
    note_six = next(row for row in matrix if row["source_row_id"] == "CA-FY2026-27-NR-38")
    assert note_six["implementation_status"] == "SOURCE_NOTE_6_VERIFICATION_REQUIRED"
