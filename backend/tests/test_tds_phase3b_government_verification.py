from copy import deepcopy

from engine.tds_compliance.government_verification import verify_government_summary


ASSIGNMENT = {
    "assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1",
    "tan": "ABCD12345E", "financial_year": "2025-26", "quarter": "Q2",
}
EVIDENCE = {"evidence_version_id": "GE-1", "financial_year": "2025-26", "quarter": "Q2", "original_filename": "government-summary.csv"}
ROWS = [{"source_row_number": 5, "deductor_tan": "ABCD12345E", "tax_deducted": 100, "tds_deposited": 90, "amount_paid_credited": 5000}]
CALCULATION = {"calculation_id": "CALC-1"}
CALC_ROWS = [{"assignment_id": "A-1", "organization_id": "ORG-1", "client_id": "CLIENT-1", "financial_year": "2025-26", "quarter": "Q2", "transaction_id": "T-1", "tds_deducted": 100, "expected_tds": 125}]


def result(*, assignment=ASSIGNMENT, evidence=EVIDENCE, rows=ROWS, calculation=CALCULATION, calculations=CALC_ROWS):
    return verify_government_summary(assignment=assignment, evidence_version=evidence, evidence_rows=rows, calculation_run=calculation, calculation_rows=calculations)


def test_matching_tan_period_and_actual_tds_supports_summary_control():
    output = result()
    assert output["identity_status"] == "IDENTITY_SUPPORTED"
    assert output["period_status"] == "PERIOD_SUPPORTED"
    assert output["tds_control_status"] == "TDS_CONTROL_MATCH"
    assert output["verification_status"] == "SUPPORTED"


def test_tan_mismatch_requires_identity_review_without_amount_identity_inference():
    rows = [{**ROWS[0], "deductor_tan": "ZZZZ99999Z"}]
    output = result(rows=rows)
    assert output["identity_status"] == "IDENTITY_REVIEW_REQUIRED"
    assert output["verification_status"] == "IDENTITY_REVIEW_REQUIRED"


def test_missing_tan_is_not_determinable():
    output = result(rows=[{key: value for key, value in ROWS[0].items() if key != "deductor_tan"}])
    assert output["identity_status"] == "IDENTITY_NOT_DETERMINABLE"


def test_period_mismatch_requires_review_and_missing_period_is_not_derived():
    mismatch = result(evidence={**EVIDENCE, "quarter": "Q3"})
    missing = result(evidence={**EVIDENCE, "financial_year": None, "quarter": None})
    assert mismatch["period_status"] == "PERIOD_REVIEW_REQUIRED"
    assert missing["period_status"] == "PERIOD_NOT_DETERMINABLE"


def test_actual_tds_is_used_not_expected_tds():
    output = result(calculations=[{**CALC_ROWS[0], "tds_deducted": 80, "expected_tds": 100}])
    assert output["internal_actual_tds"] == 80.0
    assert output["internal_expected_tds"] == 100.0
    assert output["tds_difference"] == 20.0
    assert output["tds_control_status"] == "TDS_CONTROL_DIFFERENCE"


def test_missing_actual_tds_is_review_not_a_zero_or_expected_substitute():
    output = result(calculations=[{**CALC_ROWS[0], "tds_deducted": None}])
    assert output["internal_actual_tds"] is None
    assert output["tds_control_status"] == "TDS_CONTROL_REVIEW"


def test_government_paid_and_deposited_are_evidence_only():
    output = result()
    assert output["government_amount_paid_credited"] == 5000.0
    assert output["amount_paid_credited_status"] == "INFORMATIONAL_ONLY"
    assert output["government_tds_deposited"] == 90.0
    assert output["tds_deposited_status"] == "EVIDENCE_AVAILABLE"


def test_scope_filters_other_assignment_organization_client_and_financial_year():
    foreign = [
        {**CALC_ROWS[0], "assignment_id": "A-2", "tds_deducted": 1000},
        {**CALC_ROWS[0], "organization_id": "ORG-2", "tds_deducted": 1000},
        {**CALC_ROWS[0], "client_id": "CLIENT-2", "tds_deducted": 1000},
        {**CALC_ROWS[0], "financial_year": "2026-27", "tds_deducted": 1000},
    ]
    output = result(calculations=CALC_ROWS + foreign)
    assert output["transaction_count"] == 1
    assert output["internal_actual_tds"] == 100.0


def test_result_is_summary_only_and_does_not_mutate_immutable_sources():
    evidence_rows, calculation_rows = deepcopy(ROWS), deepcopy(CALC_ROWS)
    output = result(rows=evidence_rows, calculations=calculation_rows)
    evidence_rows[0]["tax_deducted"] = 999
    calculation_rows[0]["tds_deducted"] = 999
    assert output["government_tax_deducted"] == 100.0
    assert output["internal_actual_tds"] == 100.0
    assert "transaction_id" not in output
    assert output["provenance"]["government_source_rows"] == [5]
