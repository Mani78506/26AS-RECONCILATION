"""Pure summary-level Phase 3B government-evidence controls.

No transaction matching, statutory calculation, deposit compliance or 26AS
data is used here.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01")) if value is not None and value != "" else None
    except Exception:
        return None


def _sum(rows: list[dict], field: str) -> Decimal | None:
    values = [_amount(row.get(field)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(usable, Decimal("0.00")) if usable else None


def _complete_total(rows: list[dict], field: str) -> Decimal | None:
    """Return a control total only when every scoped record supplies it.

    A partial actual-TDS total looks precise but is unsafe evidence.  Expected
    TDS remains visible separately and is never substituted for actual TDS.
    """
    if not rows or any(_amount(row.get(field)) is None for row in rows):
        return None
    return _sum(rows, field)


def verify_government_summary(*, assignment: dict, evidence_version: dict, evidence_rows: list[dict], calculation_run: dict | None, calculation_rows: list[dict]) -> dict:
    assignment_tan = str(assignment.get("tan") or "").strip().upper() or None
    government_tans = sorted({str(row.get("deductor_tan") or "").strip().upper() for row in evidence_rows if row.get("deductor_tan")})
    if not assignment_tan or not government_tans:
        identity_status = "IDENTITY_NOT_DETERMINABLE"
    elif government_tans == [assignment_tan]:
        identity_status = "IDENTITY_SUPPORTED"
    else:
        identity_status = "IDENTITY_REVIEW_REQUIRED"
    government_fy, government_quarter = evidence_version.get("financial_year"), evidence_version.get("quarter")
    if not government_fy and not government_quarter:
        period_status = "PERIOD_NOT_DETERMINABLE"
    elif government_fy != assignment.get("financial_year") or (government_quarter and assignment.get("quarter") and government_quarter != assignment.get("quarter")):
        period_status = "PERIOD_REVIEW_REQUIRED"
    else:
        period_status = "PERIOD_SUPPORTED"
    scoped = [row for row in calculation_rows if row.get("assignment_id") == assignment.get("assignment_id") and (not row.get("organization_id") or row.get("organization_id") == assignment.get("organization_id")) and (not row.get("client_id") or row.get("client_id") == assignment.get("client_id")) and (not row.get("financial_year") or row.get("financial_year") == assignment.get("financial_year")) and (not assignment.get("quarter") or row.get("quarter") == assignment.get("quarter"))]
    internal_actual = _complete_total(scoped, "tds_deducted")
    internal_expected = _sum(scoped, "expected_tds")
    government_tax = _sum(evidence_rows, "tax_deducted")
    difference = government_tax - internal_actual if government_tax is not None and internal_actual is not None else None
    if not calculation_run or government_tax is None:
        tds_status = "NOT_DETERMINABLE"
    elif internal_actual is None:
        tds_status = "TDS_CONTROL_REVIEW"
    elif difference == 0:
        tds_status = "TDS_CONTROL_MATCH"
    else:
        tds_status = "TDS_CONTROL_DIFFERENCE"
    deposited = _sum(evidence_rows, "tds_deposited")
    if identity_status == "IDENTITY_REVIEW_REQUIRED": overall = "IDENTITY_REVIEW_REQUIRED"
    elif period_status == "PERIOD_REVIEW_REQUIRED": overall = "PERIOD_REVIEW_REQUIRED"
    elif tds_status == "TDS_CONTROL_DIFFERENCE": overall = "TDS_CONTROL_DIFFERENCE"
    elif identity_status == "IDENTITY_SUPPORTED" and period_status == "PERIOD_SUPPORTED" and tds_status == "TDS_CONTROL_MATCH": overall = "SUPPORTED"
    elif tds_status == "NOT_DETERMINABLE" and identity_status == "IDENTITY_NOT_DETERMINABLE" and period_status == "PERIOD_NOT_DETERMINABLE": overall = "NOT_DETERMINABLE"
    else: overall = "PARTIALLY_SUPPORTED"
    as_float = lambda value: float(value) if value is not None else None
    return {"assignment_tan": assignment_tan, "government_tan": government_tans[0] if len(government_tans) == 1 else None, "government_tans": government_tans, "identity_status": identity_status, "assignment_financial_year": assignment.get("financial_year"), "government_financial_year": government_fy, "assignment_quarter": assignment.get("quarter"), "government_quarter": government_quarter, "period_status": period_status, "transaction_count": len(scoped), "internal_actual_tds": as_float(internal_actual), "internal_expected_tds": as_float(internal_expected), "government_tax_deducted": as_float(government_tax), "government_tds_deposited": as_float(deposited), "government_amount_paid_credited": as_float(_sum(evidence_rows, "amount_paid_credited")), "tds_difference": as_float(difference), "tds_control_status": tds_status, "amount_paid_credited_status": "INFORMATIONAL_ONLY", "tds_deposited_status": "EVIDENCE_AVAILABLE" if deposited is not None else "EVIDENCE_NOT_AVAILABLE", "verification_status": overall, "control_results": {"identity": identity_status, "period": period_status, "tds_deducted": tds_status, "amount_paid_credited": "INFORMATIONAL_ONLY", "tds_deposited": "EVIDENCE_AVAILABLE" if deposited is not None else "EVIDENCE_NOT_AVAILABLE"}, "provenance": {"government_evidence_version_id": evidence_version.get("evidence_version_id"), "calculation_id": calculation_run.get("calculation_id") if calculation_run else None, "government_source_file": evidence_version.get("original_filename"), "government_source_rows": [row.get("source_row_number") for row in evidence_rows]}}
