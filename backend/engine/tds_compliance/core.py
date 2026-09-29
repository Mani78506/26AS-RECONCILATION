"""Isolated domain foundation for deductor-side TDS compliance.

This module intentionally does not call the assessee-side reconciliation
matcher, claimability logic, or configured 26AS-only calculator.  It contains
only deterministic domain contracts that later compliance phases can build on.
No statutory rate is supplied by this module.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
import re
from copy import deepcopy

from ..shared.parser import ParseError, parse_amount, parse_date, parse_rows, read_table
from ..shared.schema import PAYMENT_LEDGER, TDS_DEPOSIT_EVIDENCE
from .source_profiles import CA_TDS_WORKING_REFERENCE, TDS_PAYABLE_GL, inspect_tds_source


WORKFLOW = "TDS_COMPLIANCE"
ASSIGNMENT_STATUSES = {"DRAFT", "ACTIVE", "UNDER_REVIEW", "APPROVED", "LOCKED", "CLOSED"}
PAN_STATUSES = {"NOT_PROVIDED", "FORMAT_INVALID", "FORMAT_VALID_UNVERIFIED", "VERIFIED", "INVALID", "UNKNOWN"}
RULE_LIFECYCLES = {"DRAFT", "REVIEW", "APPROVED", "ACTIVE", "RETIRED", "RULE_REQUIRES_OFFICIAL_VERIFICATION"}
TDS_DEDUCTION_STATUSES = {"NOT_APPLICABLE", "COMPLIANT", "SHORT_DEDUCTION", "EXCESS_DEDUCTION", "NOT_DEDUCTED", "NOT_DETERMINABLE"}
DEPOSIT_STATUSES = {"DEPOSITED_ON_TIME", "LATE_DEPOSIT", "NOT_DEPOSITED", "DEPOSIT_EVIDENCE_MISSING", "NOT_DETERMINABLE"}
OVERALL_STATUSES = {"COMPLIANT", "SHORT_DEDUCTION", "LATE_DEDUCTION", "LATE_DEPOSIT", "SHORT_AND_LATE", "PAN_EXCEPTION", "RULE_EXCEPTION", "DATA_EXCEPTION", "REVIEW_REQUIRED", "NOT_DETERMINABLE"}
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def determine_governing_tds_law(*, credit_date: Any = None, payment_date: Any = None, financial_year: str | None = None) -> dict:
    """Determine the non-salary governing Act from the earlier real event.

    FY is retained as context but deliberately never substitutes for an event
    date, so a transition-period record cannot be classified from label alone.
    """
    credit, payment = _day(credit_date), _day(payment_date)
    events = [item for item in (credit, payment) if item]
    if not events:
        return {"act": None, "effective_event_date": None, "determination_method": "NO_CREDIT_OR_PAYMENT_DATE", "source_rule": "LAW_NOT_DETERMINABLE", "status": "LAW_NOT_DETERMINABLE", "reason": "Credit date and payment date are both unavailable; governing law cannot be inferred from financial year."}
    event = min(events)
    act = "INCOME_TAX_ACT_1961" if event <= date(2026, 3, 31) else "INCOME_TAX_ACT_2025"
    return {"act": act, "effective_event_date": event.isoformat(), "determination_method": "EARLIER_OF_CREDIT_OR_PAYMENT", "source_rule": "TRANSITION_2026_04_01", "status": "LAW_DETERMINED", "financial_year": financial_year}


def pan_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return "NOT_PROVIDED"
    return "FORMAT_VALID_UNVERIFIED" if PAN_RE.fullmatch(text) else "FORMAT_INVALID"


def source_provenance(source_reference: Any, *, generated_prefix: str, row_number: int | None = None) -> dict:
    value = str(source_reference or "").strip()
    if value:
        return {"source_reference": value, "source_reference_status": "SOURCE_PROVIDED"}
    suffix = str(row_number or "UNKNOWN")
    return {"source_reference": f"SYS-{generated_prefix}-{suffix}", "source_reference_status": "SYSTEM_GENERATED"}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def select_compliance_rule(transaction: dict, rules: list[dict]) -> dict | None:
    law = determine_governing_tds_law(credit_date=transaction.get("credit_date"), payment_date=transaction.get("payment_date"), financial_year=transaction.get("financial_year"))
    if law["status"] != "LAW_DETERMINED":
        return None
    event = law["effective_event_date"]
    candidates = [r for r in rules if r.get("lifecycle") in {"APPROVED", "ACTIVE"} and r.get("active", True)
                  and r.get("act") == law["act"] and r.get("financial_year") == transaction.get("financial_year")
                  and r.get("payment_nature") == transaction.get("payment_nature")
                  and (not r.get("deductee_type") or r.get("deductee_type") == transaction.get("deductee_type"))
                  and r.get("effective_from", "") <= event <= r.get("effective_to", "9999-12-31")]
    return max(candidates, key=lambda item: str(item.get("rule_version") or ""), default=None)


def calculate_compliance_tds(transaction: dict, rules: list[dict], *, cumulative_amount: Any = None) -> dict:
    """Domain calculation contract. Missing law/rule/data remains explicit."""
    law = determine_governing_tds_law(credit_date=transaction.get("credit_date"), payment_date=transaction.get("payment_date"), financial_year=transaction.get("financial_year"))
    status = pan_status(transaction.get("deductee_pan"))
    if law["status"] != "LAW_DETERMINED":
        return {"calculation_status": "LAW_NOT_DETERMINABLE", "expected_tds": None, "governing_act": None, "pan_status": status, "warnings": [law["reason"]], "explanation": law["reason"]}
    rule = select_compliance_rule(transaction, rules)
    if not rule:
        return {"calculation_status": "RULE_NOT_FOUND", "expected_tds": None, "governing_act": law["act"], "effective_event_date": law["effective_event_date"], "pan_status": status, "warnings": ["No approved/active statutory rule matched this source record."], "explanation": "A statutory rate was not invented."}
    amount = _decimal(transaction.get("taxable_amount")) or _decimal(transaction.get("amount"))
    if amount is None:
        return {"calculation_status": "INSUFFICIENT_DATA", "expected_tds": None, "governing_act": law["act"], "pan_status": status, "rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "warnings": ["Transaction amount is absent."], "explanation": "Expected TDS cannot be calculated without a source amount."}
    rate = _decimal(rule.get("no_pan_rate" if status == "NOT_PROVIDED" and rule.get("no_pan_rate") is not None else "rate"))
    if rate is None:
        return {"calculation_status": "REQUIRES_REVIEW", "expected_tds": None, "governing_act": law["act"], "pan_status": status, "rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "warnings": ["Rule has no usable rate for this PAN status."], "explanation": "Rate selection requires review."}
    threshold = _decimal(rule.get("single_transaction_threshold"))
    base = amount
    if threshold is not None and amount < threshold:
        expected, threshold_status = Decimal("0.00"), "BELOW_SINGLE_TRANSACTION_THRESHOLD"
    else:
        expected, threshold_status = (base * rate / Decimal("100")).quantize(Decimal("0.01")), "APPLICABLE"
    return {"calculation_status": "CALCULATED", "applicability": "APPLICABLE", "applicable_rate": float(rate), "threshold_status": threshold_status, "expected_tds": float(expected), "calculation_base": float(base), "excess_amount": None, "pan_adjustment": "NO_PAN_RATE" if status == "NOT_PROVIDED" else None, "certificate_adjustment": None, "rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "governing_act": law["act"], "section_reference": rule.get("section_reference"), "table_reference": rule.get("table_reference"), "effective_event_date": law["effective_event_date"], "explanation": f"Calculated only from approved rule {rule.get('rule_id')} version {rule.get('rule_version')}.", "warnings": [], "pan_status": status}


PAYMENT_LEDGER_SCHEMA = ["transaction_id", "source_reference", "deductee_name", "deductee_pan", "deductee_type", "deductee_gstin", "vendor_code", "invoice_number", "invoice_date", "transaction_date", "credit_date", "payment_date", "amount", "taxable_amount", "payment_nature", "section_input", "description", "tds_expected", "tds_deducted", "tds_deposited", "deduction_date", "deposit_date", "challan_number", "challan_date", "certificate_number", "certificate_rate", "certificate_valid_from", "certificate_valid_to", "financial_year", "tax_year", "quarter"]
DEDUCTEE_MASTER_SCHEMA = ["deductee_id", "vendor_code", "legal_name", "pan", "gstin", "deductee_type", "address", "pan_status", "pan_verified_at", "lower_deduction_certificate", "certificate_number", "certificate_rate", "certificate_valid_from", "certificate_valid_to"]


# Phase 2 ingestion only.  These helpers intentionally do not select a rule or
# calculate a statutory liability: source TDS values remain source evidence.
LEDGER_DATE_FIELDS = ("invoice_date", "transaction_date", "credit_date", "payment_date", "deduction_date", "deposit_date")
LEDGER_AMOUNT_FIELDS = ("amount", "taxable_amount", "tds_expected", "tds_deducted", "tds_deposited")


def normalise_financial_year(value: Any) -> str | None:
    text = str(value or "").strip().upper().replace("FY", "").strip()
    match = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{2}|\d{4})", text)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end < 100:
        end += (start // 100) * 100
    if end != start + 1:
        return None
    return f"{start:04d}-{end % 100:02d}"


def _derived_financial_year(day: date) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _quarter(day: date) -> str:
    return ("Q1", "Q2", "Q3", "Q4")[(day.month - 4) % 12 // 3]


def _normalise_date(value: Any) -> tuple[str | None, str | None]:
    """Return ISO date and an explicit issue without guessing slash dates."""
    if value in (None, ""):
        return None, None
    text = str(value).strip()
    ambiguous = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if ambiguous and int(ambiguous.group(1)) <= 12 and int(ambiguous.group(2)) <= 12:
        return None, f"Ambiguous date '{text}' requires source-format review."
    parsed = parse_date(value)
    return (parsed.isoformat(), None) if parsed else (None, f"Invalid date '{text}'.")


def _missing_counts(rows: list[dict]) -> dict[str, int]:
    fields = ("deductee_name", "vendor_code", "amount", "financial_year", *LEDGER_DATE_FIELDS)
    return {field: sum(1 for row in rows if row.get(field) in (None, "")) for field in fields}


def validate_payment_ledger(filename: str, content: bytes, assignment: dict | None = None) -> dict:
    """Parse and validate a payment ledger without persisting or calculating tax.

    A result is deliberately preview-safe: all original rows are represented,
    invalid rows are retained, and a caller must explicitly commit a clean
    structural validation to create an immutable version.
    """
    source_profile = None
    try:
        source_profile = inspect_tds_source(filename, content)
        if source_profile and source_profile.get("reference_only"):
            return {
                "file": {"filename": filename, "sheet_name": None, "header_row": None, "workbook_sheets": source_profile["workbook_sheets"], "source_profile": CA_TDS_WORKING_REFERENCE},
                "headers": [], "mapped_headers": [], "unmapped_headers": [],
                "mapping_warnings": ["This CA TDS working workbook is reference-only. Its formulas and manually entered rates are not ingested as statutory or payment-ledger data."],
                "summary": {"total_rows": 0, "transaction_rows": 0, "ignored_rows": 0, "valid_rows": 0, "warning_rows": 0, "review_rows": 0, "invalid_rows": 0, "duplicate_rows": 0, "missing_field_counts": {}, "pan_format_counts": {}, "date_errors": 0, "fy_errors": 0},
                "rows": [], "can_commit": False,
            }
        if source_profile:
            source_rows = source_profile["rows"]
            mapped_headers = source_profile["mapped_headers"]
            unmapped_headers = source_profile["unmapped_headers"]
            raw_headers = [*mapped_headers, *unmapped_headers]
            table = None
        else:
            table = read_table(filename, content, PAYMENT_LEDGER)
            source_rows, mapped_headers, unmapped_headers = parse_rows(table, PAYMENT_LEDGER)
            raw_headers = [str(value) for value in table.columns]
    except ParseError as exc:
        return {"file": {"filename": filename}, "headers": [], "mapped_headers": [], "unmapped_headers": [], "mapping_warnings": [str(exc)], "summary": {"total_rows": 0, "valid_rows": 0, "warning_rows": 0, "review_rows": 0, "invalid_rows": 0, "duplicate_rows": 0, "missing_field_counts": {}, "pan_format_counts": {}, "date_errors": 0, "fy_errors": 0}, "rows": [], "can_commit": False}

    mapping_warnings: list[str] = []
    if any(re.sub(r"[^a-z0-9]", "", header.lower()) in {"total", "grandtotal"} for header in raw_headers):
        mapping_warnings.append("Column mapping requires review: a Total-style column was not inferred as an amount.")
    if not mapped_headers:
        mapping_warnings.append("Column mapping requires review: no recognised payment-ledger headers were detected.")

    rows, pan_counts = [], {status: 0 for status in ("NOT_PROVIDED", "FORMAT_INVALID", "FORMAT_VALID_UNVERIFIED")}
    date_errors = fy_errors = 0
    for raw in source_rows:
        issues: list[str] = []
        normalized: dict[str, Any] = {field: None for field in PAYMENT_LEDGER_SCHEMA}
        normalized.update({key: raw.get(key) or None for key in PAYMENT_LEDGER_SCHEMA if key in raw})
        for key in ("source_category", "source_profile", "source_transaction_kind", "company", "tds_payable_account", "gl_description", "source_period", "source_sheet", "document_type", "cheque_number", "offsetting_account", "source_counterparty_description", "source_payee_name", "source_debit_amount", "source_credit_amount", "closing_balance", "project_name", "cr_dr", "purchase_order_number", "msme"):
            if key in raw:
                normalized[key] = raw.get(key)
        # Preserve source semantics from bank-ledger exports.  A payee is
        # preferred; the offsetting description is used only when it is the
        # only source-provided counterparty label. Debit/credit are never
        # summed: the populated side is the transaction amount.
        normalized["deductee_name"] = normalized.get("deductee_name") or raw.get("source_payee_name") or raw.get("source_counterparty_description") or None
        normalized["source_row_number"] = raw.get("source_row_number") or raw.get("row_no")
        normalized["source_file_name"] = filename
        normalized["source_file_id"] = None

        for field in LEDGER_AMOUNT_FIELDS:
            supplied = raw.get(field)
            normalized[field] = parse_amount(supplied) if supplied not in (None, "") else None
            if supplied not in (None, "") and normalized[field] is None:
                issues.append(f"{field.replace('_', ' ').title()} is not a usable amount.")
        if normalized.get("amount") is None and not source_profile:
            debit, credit = _decimal(raw.get("source_debit_amount")), _decimal(raw.get("source_credit_amount"))
            nonzero = [value for value in (debit, credit) if value is not None and value != 0]
            if len(nonzero) == 1:
                normalized["amount"] = float(nonzero[0])
            elif debit == 0 and credit == 0:
                normalized["amount"] = 0.0
            elif len(nonzero) > 1:
                issues.append("Debit and credit amounts are both populated; transaction amount requires review.")

        usable_dates: list[date] = []
        for field in LEDGER_DATE_FIELDS:
            normalized[field], date_issue = _normalise_date(raw.get(field))
            if date_issue:
                issues.append(date_issue)
                date_errors += 1
            if normalized[field]:
                usable_dates.append(date.fromisoformat(normalized[field]))
        for field in ("certificate_valid_from", "certificate_valid_to"):
            normalized[field], certificate_date_issue = _normalise_date(raw.get(field))
            if certificate_date_issue:
                issues.append(certificate_date_issue)

        provided_fy = raw.get("financial_year")
        normalized["financial_year"] = normalise_financial_year(provided_fy)
        if provided_fy not in (None, "") and not normalized["financial_year"]:
            issues.append(f"Financial year '{provided_fy}' is invalid.")
            fy_errors += 1
        if not normalized["financial_year"] and normalized.get("source_category") == TDS_PAYABLE_GL and normalized.get("transaction_date"):
            normalized["financial_year"] = _derived_financial_year(date.fromisoformat(normalized["transaction_date"]))
        elif not normalized["financial_year"] and usable_dates:
            normalized["financial_year"] = _derived_financial_year(usable_dates[0])
        if not normalized["financial_year"]:
            issues.append("Financial year cannot be determined without a valid FY or usable date.")
        normalized["quarter"] = _quarter(usable_dates[0]) if usable_dates else None
        normalized["tax_year"] = normalized["financial_year"]

        provided_reference = raw.get("transaction_id") or raw.get("invoice_number")
        normalized.update(source_provenance(provided_reference, generated_prefix="TDS", row_number=normalized["source_row_number"]))
        normalized["transaction_id"] = normalized["source_reference"]
        normalized["pan_status"] = pan_status(normalized.get("deductee_pan"))
        pan_counts[normalized["pan_status"]] += 1

        is_gl_payment = normalized.get("source_transaction_kind") == "TDS_PAYMENT"
        is_gl_entry = normalized.get("source_category") == TDS_PAYABLE_GL
        has_tds_evidence = any(normalized.get(field) not in (None, 0, 0.0) for field in ("tds_deducted", "tds_deposited", "source_debit_amount", "source_credit_amount"))
        if is_gl_payment:
            # The bank is the clearing counterparty, not the deductee.
            normalized["deductee_name"] = None
        has_identity = bool(normalized.get("deductee_name") or normalized.get("vendor_code") or normalized.get("deductee_pan"))
        if not has_identity:
            if not is_gl_payment:
                issues.append("Vendor/deductee identity is not provided.")
        if normalized.get("amount") is None and not is_gl_entry:
            issues.append("Transaction amount is not provided or unusable.")
        if is_gl_entry and normalized.get("amount") is None:
            issues.append("Gross vendor payment amount is not present in this TDS payable GL entry; TDS evidence is retained without calculating a statutory amount.")
        if is_gl_entry and not has_tds_evidence:
            issues.append("TDS payable GL entry has no usable debit or credit evidence.")
        if not usable_dates and not normalized.get("financial_year"):
            issues.append("No usable transaction, credit or payment date is available.")

        status = "VALID"
        if (not has_identity and not is_gl_payment) or (normalized.get("amount") is None and not is_gl_entry) or (is_gl_entry and not has_tds_evidence):
            status = "INVALID"
        elif mapping_warnings or any("Ambiguous date" in issue or "cannot be determined" in issue for issue in issues):
            status = "REVIEW_REQUIRED"
        elif normalized["pan_status"] != "FORMAT_VALID_UNVERIFIED" or issues:
            status = "WARNING"
        normalized["validation_status"] = status
        normalized["validation_issues"] = issues
        normalized["duplicate_status"] = None
        if assignment:
            normalized.update({key: assignment.get(key) for key in ("organization_id", "client_id", "assignment_id")})
        rows.append(normalized)

    signatures: dict[tuple, list[dict]] = {}
    for row in rows:
        signature = (str(row.get("deductee_name") or row.get("vendor_code") or "").strip().upper(), str(row.get("invoice_number") or row.get("transaction_id") or "").strip().upper(), row.get("transaction_date") or row.get("credit_date") or row.get("payment_date"), row.get("amount") if row.get("amount") is not None else row.get("tds_deducted") if row.get("tds_deducted") is not None else row.get("tds_deposited"), row.get("source_transaction_kind"))
        if all(signature):
            signatures.setdefault(signature, []).append(row)
    for duplicated in signatures.values():
        if len(duplicated) > 1:
            for row in duplicated:
                row["duplicate_status"] = "DUPLICATE_REVIEW"
                row["validation_status"] = "REVIEW_REQUIRED" if row["validation_status"] != "INVALID" else "INVALID"
                row["validation_issues"].append("Possible duplicate source transaction; retained for review.")

    counts = {state: sum(row["validation_status"] == state for row in rows) for state in ("VALID", "WARNING", "REVIEW_REQUIRED", "INVALID")}
    summary = {"total_rows": len(rows), "transaction_rows": len(rows), "ignored_rows": source_profile.get("ignored_rows", 0) if source_profile else 0, "valid_rows": counts["VALID"], "warning_rows": counts["WARNING"], "review_rows": counts["REVIEW_REQUIRED"], "invalid_rows": counts["INVALID"], "duplicate_rows": sum(row["duplicate_status"] == "DUPLICATE_REVIEW" for row in rows), "missing_field_counts": _missing_counts(rows), "pan_format_counts": pan_counts, "date_errors": date_errors, "fy_errors": fy_errors}
    file_info = {"filename": filename, "sheet_name": source_profile.get("sheet_name") if source_profile else table.attrs.get("sheet_name"), "header_row": source_profile.get("header_row") if source_profile else table.attrs.get("header_row"), "workbook_sheets": source_profile.get("workbook_sheets") if source_profile else table.attrs.get("workbook_sheets")}
    if source_profile:
        file_info.update({"source_profile": source_profile["source_profile"], "source_category": source_profile["source_category"], "metadata": source_profile.get("metadata", {})})
    return {"file": file_info, "headers": raw_headers, "mapped_headers": mapped_headers, "unmapped_headers": unmapped_headers, "mapping_warnings": mapping_warnings, "summary": summary, "rows": rows, "can_commit": not mapping_warnings and counts["INVALID"] == 0 and len(rows) > 0}


# ---------- Phase 3: configured-rule statutory calculation ----------
PAYMENT_NATURES = {
    "contractor": "contractor", "contract": "contractor", "professional_fee": "professional_fee",
    "professional fees": "professional_fee", "technical_service": "technical_service",
    "technical services": "technical_service", "rent_machinery": "rent_machinery",
    "rent_building": "rent_building", "commission": "commission", "brokerage": "brokerage",
    "purchase_of_goods": "purchase_of_goods", "interest": "interest", "other_specified_payment": "other_specified_payment",
}


def classify_payment_nature(transaction: dict) -> dict:
    value = str(transaction.get("payment_nature") or "").strip().lower().replace("-", "_")
    normalized = PAYMENT_NATURES.get(value)
    if normalized:
        return {"payment_nature": normalized, "payment_nature_status": transaction.get("payment_nature_status") or "CLASSIFIED", "payment_nature_source": transaction.get("payment_nature_source") or "SOURCE_PROVIDED", "payment_nature_confidence": transaction.get("payment_nature_confidence") or "HIGH", "classification_reason": transaction.get("classification_reason") or "Payment nature was supplied by the source."}
    if value:
        return {"payment_nature": None, "payment_nature_status": "REVIEW_REQUIRED", "payment_nature_source": transaction.get("payment_nature_source") or "SOURCE_PROVIDED", "payment_nature_confidence": "LOW", "reason": "Payment nature is not in the controlled classification list.", "classification_reason": transaction.get("classification_reason") or "Payment nature is not in the controlled classification taxonomy."}
    return {"payment_nature": None, "payment_nature_status": transaction.get("payment_nature_status") or "NOT_DETERMINABLE", "payment_nature_source": transaction.get("payment_nature_source") or "NOT_PROVIDED", "payment_nature_confidence": transaction.get("payment_nature_confidence") or "NONE", "reason": transaction.get("classification_reason") or "Payment nature/statutory classification was not available from the source ledger.", "classification_reason": transaction.get("classification_reason") or "Payment nature/statutory classification was not available from the source ledger."}


def _rule_value(rule: dict, key: str, default=None):
    return rule.get(key, default)


def _rule_scope_matches(transaction: dict, rule: dict) -> bool:
    """Apply explicit TDS Compliance rule scope without changing legacy fixtures.

    Older immutable calculation snapshots did not contain a scope and remain
    readable as global rules.  Managed rules always carry a scope and must
    match the transaction context before statutory matching can begin.
    """
    scope = str(rule.get("scope") or "GLOBAL").upper()
    if scope == "GLOBAL":
        return True
    if rule.get("organization_id") != transaction.get("organization_id"):
        return False
    if scope == "ORGANIZATION":
        return True
    if rule.get("client_id") != transaction.get("client_id"):
        return False
    return scope != "ASSIGNMENT" or rule.get("assignment_id") == transaction.get("assignment_id")


def select_statutory_rule(transaction: dict, rules: list[dict], law: dict, nature: dict) -> tuple[dict | None, str]:
    if law.get("status") != "LAW_DETERMINED":
        return None, "LAW_NOT_DETERMINABLE"
    event, section = law["effective_event_date"], str(transaction.get("section_input") or "").strip().upper()
    candidates, section_conflict = [], False
    for rule in rules:
        if not _rule_scope_matches(transaction, rule):
            continue
        if not rule.get("active", True) or rule.get("lifecycle") not in {"APPROVED", "ACTIVE"}:
            continue
        if (rule.get("governing_act") or rule.get("act")) != law["act"]:
            continue
        if rule.get("financial_year") not in {None, "", transaction.get("financial_year"), transaction.get("tax_year")}:
            continue
        if not (str(rule.get("effective_from") or "0000-01-01") <= event <= str(rule.get("effective_to") or "9999-12-31")):
            continue
        rule_nature = rule.get("payment_nature")
        if rule_nature and nature.get("payment_nature") and rule_nature != nature["payment_nature"]:
            continue
        if rule_nature and not nature.get("payment_nature") and not section:
            continue
        if rule.get("deductee_type") and rule.get("deductee_type") != transaction.get("deductee_type"):
            continue
        if section and rule.get("section_reference") and section != str(rule["section_reference"]).upper() and section != str(rule.get("historical_section_reference") or "").upper():
            section_conflict = True
            continue
        candidates.append(rule)
    if not candidates:
        return None, "SECTION_CONFLICT" if section_conflict else "RULE_NOT_FOUND"
    # Only an explicit configured priority can resolve overlapping rules. A
    # rule version is evidence, not an implicit precedence policy.
    highest_priority = max(int(item.get("priority", 0)) for item in candidates)
    selected_candidates = [item for item in candidates if int(item.get("priority", 0)) == highest_priority]
    if len(selected_candidates) != 1:
        return None, "RULE_AMBIGUOUS"
    selected = selected_candidates[0]
    return selected, "SECTION_CONFIRMED" if section else "SECTION_MISSING"


def determine_applicable_tds_rate(transaction: dict, rule: dict | None, event_date: str | None) -> dict:
    if not rule or not event_date:
        return {"status": "RATE_NOT_DETERMINABLE", "rate": None, "rate_source": None, "rate_reason": "No applicable configured statutory rule."}
    certificate_rate = _decimal(transaction.get("certificate_rate"))
    certificate_from, certificate_to = _day(transaction.get("certificate_valid_from")), _day(transaction.get("certificate_valid_to"))
    event = _day(event_date)
    if certificate_rate is not None:
        certificate_number = str(transaction.get("certificate_number") or "").strip()
        certificate_pan = str(transaction.get("certificate_deductee_pan") or "").strip().upper()
        source_pan = str(transaction.get("deductee_pan") or "").strip().upper()
        certificate_nature = transaction.get("certificate_payment_nature")
        if not certificate_number or not certificate_from or not certificate_to or not event or not (certificate_from <= event <= certificate_to) or (certificate_pan and certificate_pan != source_pan) or (certificate_nature and certificate_nature != transaction.get("payment_nature")):
            return {"status": "RULE_REVIEW_REQUIRED", "rate": None, "rate_source": None, "rate_reason": "Certificate is supplied but is not valid for the effective event date.", "certificate_adjustment": "CERTIFICATE_INVALID_OR_EXPIRED"}
        return {"status": "RATE_DETERMINED", "rate": certificate_rate, "rate_source": "VALID_LOWER_OR_NIL_CERTIFICATE", "rate_reason": "Configured certificate rate is valid for the effective event date.", "certificate_adjustment": "CERTIFICATE_APPLIED", "pan_adjustment": None}
    status = transaction.get("pan_status") or pan_status(transaction.get("deductee_pan"))
    if status in {"NOT_PROVIDED", "FORMAT_INVALID", "INVALID"}:
        rate = _decimal(rule.get("no_pan_rate"))
        if rate is None:
            return {"status": "RULE_REVIEW_REQUIRED", "rate": None, "rate_source": None, "rate_reason": "Configured rule does not provide a rate for missing or invalid PAN.", "pan_adjustment": "PAN_TREATMENT_UNAVAILABLE"}
        return {"status": "RATE_DETERMINED", "rate": rate, "rate_source": "CONFIGURED_NO_PAN_RATE", "rate_reason": "Configured rule provides the applicable rate for the source PAN status.", "pan_adjustment": "CONFIGURED_NO_PAN_RATE", "certificate_adjustment": None}
    rate = _decimal(rule.get("rate"))
    if rate is None:
        return {"status": "RATE_NOT_DETERMINABLE", "rate": None, "rate_source": None, "rate_reason": "Configured rule has no usable rate."}
    return {"status": "RATE_DETERMINED", "rate": rate, "rate_source": "CONFIGURED_RULE", "rate_reason": "Rate is from the configured, versioned statutory rule.", "pan_adjustment": None, "certificate_adjustment": None}


def _threshold_details(rule: dict, current: Decimal, prior: Decimal) -> dict:
    # A controlled statutory rule may require both a per-payment and an
    # aggregate-FY test.  This is additive to the legacy single-threshold
    # modes, so existing governed snapshots retain their exact behaviour.
    per_payment = _decimal(rule.get("per_transaction_threshold"))
    aggregate_fy = _decimal(rule.get("aggregate_financial_year_threshold"))
    if per_payment is not None or aggregate_fy is not None:
        cumulative = prior + current
        per_triggered = per_payment is not None and current > per_payment
        aggregate_triggered = aggregate_fy is not None and cumulative > aggregate_fy
        subject = current if per_triggered or aggregate_triggered else Decimal("0")
        if per_triggered and aggregate_triggered:
            status = "PER_TRANSACTION_AND_AGGREGATE_THRESHOLD_MET"
        elif per_triggered:
            status = "PER_TRANSACTION_THRESHOLD_MET"
        elif aggregate_triggered:
            status = "AGGREGATE_THRESHOLD_MET"
        else:
            status = "BELOW_CONFIGURED_THRESHOLDS"
        return {"threshold": aggregate_fy or per_payment, "per_transaction_threshold": per_payment,
                "aggregate_financial_year_threshold": aggregate_fy, "threshold_status": status,
                "prior_aggregate_amount": prior, "cumulative_amount": cumulative,
                "threshold_remaining": max(Decimal("0"), aggregate_fy-cumulative) if aggregate_fy is not None else None,
                "amount_subject_to_tds": subject}
    threshold = _decimal(rule.get("threshold") if rule.get("threshold") is not None else rule.get("single_transaction_threshold"))
    mode = str(rule.get("threshold_type") or "NO_THRESHOLD").upper()
    cumulative = prior + current
    if threshold is None or mode == "NO_THRESHOLD":
        return {"threshold": threshold, "threshold_status": "NOT_APPLICABLE", "prior_aggregate_amount": prior, "cumulative_amount": cumulative, "threshold_remaining": None, "amount_subject_to_tds": current}
    if mode == "PER_TRANSACTION":
        subject = current if current >= threshold else Decimal("0")
        return {"threshold": threshold, "threshold_status": "THRESHOLD_REACHED" if current == threshold else "ABOVE_THRESHOLD" if current > threshold else "BELOW_THRESHOLD", "prior_aggregate_amount": prior, "cumulative_amount": cumulative, "threshold_remaining": max(Decimal("0"), threshold-current), "amount_subject_to_tds": subject}
    if mode not in {"AGGREGATE", "BOTH", "AGGREGATE_EXCESS"}:
        return {"threshold": threshold, "threshold_status": "NOT_DETERMINABLE", "prior_aggregate_amount": prior, "cumulative_amount": cumulative, "threshold_remaining": None, "amount_subject_to_tds": None}
    excess_only = bool(rule.get("excess_only")) or mode == "AGGREGATE_EXCESS"
    if cumulative < threshold:
        subject, status = Decimal("0"), "BELOW_THRESHOLD"
    elif cumulative == threshold:
        subject, status = (Decimal("0") if excess_only else current), "THRESHOLD_REACHED"
    else:
        subject, status = (cumulative - max(prior, threshold) if excess_only else current), "ABOVE_THRESHOLD"
    return {"threshold": threshold, "threshold_status": status, "prior_aggregate_amount": prior, "cumulative_amount": cumulative, "threshold_remaining": max(Decimal("0"), threshold-cumulative), "amount_subject_to_tds": subject}


def calculate_ledger_transactions(transactions: list[dict], rules: list[dict], *, assignment_id: str | None = None) -> list[dict]:
    """Calculate only from a supplied governed rule set, preserving snapshots."""
    aggregates: dict[tuple, Decimal] = {}
    results = []
    ordered = sorted(transactions, key=lambda row: (row.get("credit_date") or row.get("payment_date") or "9999-12-31", row.get("source_row_number") or 0))
    for transaction in ordered:
        law = determine_governing_tds_law(credit_date=transaction.get("credit_date"), payment_date=transaction.get("payment_date"), financial_year=transaction.get("financial_year"))
        nature = classify_payment_nature(transaction)
        rule_base = None
        supplied_taxable, supplied_amount = _decimal(transaction.get("taxable_amount")), _decimal(transaction.get("amount"))
        # The base is selected only after the rule is known. Keep zero as an
        # explicit source value; it must never fall through to gross amount.
        base = supplied_taxable if supplied_taxable is not None else supplied_amount
        result = {"transaction_id": transaction.get("transaction_id"), "assignment_id": assignment_id or transaction.get("assignment_id"), "source_reference": transaction.get("source_reference"), "deductee_name": transaction.get("deductee_name"), "deductee_pan": transaction.get("deductee_pan"), "pan_status": transaction.get("pan_status") or pan_status(transaction.get("deductee_pan")), **nature, "governing_act": law.get("act"), "effective_event_date": law.get("effective_event_date"), "current_amount": float(base) if base is not None else None, "calculated_at": None}
        if law["status"] != "LAW_DETERMINED":
            result.update({"calculation_status": "LAW_NOT_DETERMINABLE", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "LAW_EXCEPTION", "reason_code": "LAW_NOT_DETERMINABLE", "reason": law["reason"], "recommended_action": "Provide a valid credit date or payment date.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        if not nature.get("payment_nature"):
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "PAYMENT_NATURE_NOT_DETERMINABLE", "reason": nature["reason"], "recommended_action": "Classify payment nature through the controlled review workflow.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        if transaction.get("duplicate_status") == "DUPLICATE_REVIEW":
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "DUPLICATE_REVIEW", "reason": "This ledger row is a possible duplicate and is excluded from automatic statutory calculation.", "recommended_action": "Resolve duplicate source evidence before calculation.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        rule, section_status = select_statutory_rule(transaction, rules, law, nature)
        result["section_status"] = section_status
        if not rule:
            reason = "Section input conflicts with configured rule context." if section_status == "SECTION_CONFLICT" else "No active, approved configured rule matched this transaction."
            result.update({"calculation_status": "REVIEW_REQUIRED" if section_status == "SECTION_CONFLICT" else "RULE_NOT_FOUND", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": section_status, "reason": reason, "recommended_action": "Review source classification and configure an authoritative rule; no rate was invented.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        result.update({"rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "rule_status": rule.get("lifecycle"), "section_reference": rule.get("section_reference"), "table_reference": rule.get("table_reference")})
        # Persist complete rule context with the result, not merely a live ID.
        result["rule_snapshot"] = deepcopy({key: value for key, value in rule.items() if key != "_id"})
        if law["act"] == "INCOME_TAX_ACT_2025" and not str(rule.get("section_reference") or "").startswith("393"):
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "NEW_ACT_REFERENCE_REQUIRED", "reason": "A post-1-Apr-2026 rule must use a section 393 table/serial reference.", "recommended_action": "Configure the authoritative Income-tax Act, 2025 rule reference.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        basis = str(rule.get("calculation_basis") or "full_amount").lower()
        if basis == "taxable_amount":
            base = supplied_taxable
        elif basis == "full_amount":
            base = supplied_amount
        elif basis == "rule_defined_base":
            configured_field = str(rule.get("rule_defined_base_field") or "")
            base = _decimal(transaction.get(configured_field)) if configured_field else None
        else:
            base = None
        result["current_amount"] = float(base) if base is not None else None
        if base is None:
            result.update({"calculation_status": "INSUFFICIENT_DATA", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "DATA_EXCEPTION", "reason_code": "AMOUNT_UNAVAILABLE", "reason": "No usable rule-defined source amount is available.", "recommended_action": "Provide the applicable source amount.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        if base < 0:
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "NEGATIVE_AMOUNT_REVIEW", "reason": "Negative/credit-note treatment is not configured for this statutory rule.", "recommended_action": "Review the source transaction and configure an approved treatment if applicable.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        aggregate_key = (assignment_id or transaction.get("assignment_id"), transaction.get("financial_year"), transaction.get("deductee_pan") or transaction.get("vendor_code") or transaction.get("deductee_name"), rule.get("rule_id"), nature["payment_nature"])
        threshold = _threshold_details(rule, base, aggregates.get(aggregate_key, Decimal("0")))
        aggregates[aggregate_key] = threshold["cumulative_amount"]
        result.update({key: float(value) if isinstance(value, Decimal) else value for key, value in threshold.items()})
        if threshold["amount_subject_to_tds"] is None:
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "THRESHOLD_NOT_DETERMINABLE", "reason": "Configured threshold mode is not supported deterministically.", "recommended_action": "Review the authoritative threshold configuration.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        rate_info = determine_applicable_tds_rate(transaction, rule, law["effective_event_date"])
        result.update(rate_info)
        result["applicable_rate"] = float(rate_info["rate"]) if rate_info.get("rate") is not None else None
        if rate_info["status"] != "RATE_DETERMINED":
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": rate_info["status"], "reason": rate_info["rate_reason"], "recommended_action": "Review the rule, PAN treatment and certificate evidence.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        precision = int(rule.get("rounding_precision", 2))
        rounding = str(rule.get("rounding_method") or "HALF_UP").upper()
        if rounding != "HALF_UP":
            result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": "ROUNDING_METHOD_UNSUPPORTED", "reason": "Configured rule uses a rounding method this engine cannot apply safely.", "recommended_action": "Configure a supported, CA-approved rounding method.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        quantum = Decimal("1").scaleb(-precision)
        unrounded = threshold["amount_subject_to_tds"] * rate_info["rate"] / Decimal("100")
        expected = unrounded.quantize(quantum, rounding=ROUND_HALF_UP if rounding == "HALF_UP" else ROUND_HALF_UP)
        actual = _decimal(transaction.get("tds_deducted"))
        difference = actual - expected if actual is not None else None
        deduction_status = "ACTUAL_TDS_NOT_PROVIDED" if actual is None else "COMPLIANT" if difference == 0 else "SHORT_DEDUCTION" if difference < 0 else "EXCESS_DEDUCTION"
        compliance_status = "COMPLIANT" if deduction_status == "COMPLIANT" else "NOT_DETERMINABLE" if deduction_status == "ACTUAL_TDS_NOT_PROVIDED" else deduction_status
        result.update({"calculation_status": "CALCULATED", "calculation_base": basis, "calculation_base_amount": float(threshold["amount_subject_to_tds"]), "unrounded_expected_tds": float(unrounded), "expected_tds": float(expected), "rounding_method": rounding, "tds_deducted": float(actual) if actual is not None else None, "tds_deduction_difference": float(difference) if difference is not None else None, "deduction_status": deduction_status, "compliance_status": compliance_status, "reason_code": deduction_status, "reason": f"Governing law: {law['act']}. Payment nature: {nature['payment_nature']}. Rule {rule.get('rule_id')} version {rule.get('rule_version')}; base {float(threshold['amount_subject_to_tds'])}, rate {float(rate_info['rate'])}%, expected {float(expected)}; actual TDS is compared only when supplied.", "recommended_action": "No action required." if deduction_status == "COMPLIANT" else "Review source deduction evidence and the configured rule context."})
        results.append(result)
    return results


def calculate_tds_calculator(transaction: dict, statutory_rules: list[dict], interest_rules: list[dict] | None = None, *, assignment_id: str = "CALCULATOR") -> dict:
    """Compose the Phase 3 and Phase 4 engines for one auditable calculator request.

    This adapter deliberately owns no statutory rate, threshold, due-date or
    interest formula.  It normalises the calculator's payment mode, delegates
    liability selection to Phase 3, then delegates timing to Phase 4.
    """
    source = deepcopy(transaction)
    source["transaction_id"] = source.get("transaction_id") or "TDS-CALCULATOR"
    source["source_reference"] = source.get("source_reference") or source["transaction_id"]
    source["pan_status"] = source.get("pan_status") or pan_status(source.get("deductee_pan"))
    source["deduction_date"] = source.get("deduction_date") or source.get("credit_date") or source.get("payment_date")
    # Keep source deduction evidence separate from calculated liability so the
    # Phase 3 comparison remains available when the user supplies it.
    if source.get("actual_tds_deducted") is not None:
        source["tds_deducted"] = source["actual_tds_deducted"]
    mode = str(source.get("payment_mode") or "GROSS").upper()
    if mode not in {"GROSS", "NET_OF_TDS"}:
        return {"status": "REVIEW_REQUIRED", "reason_code": "PAYMENT_MODE_INVALID", "warnings": ["Payment mode must be Gross or Net of TDS."], "input": source}
    amount = _decimal(source.get("amount"))
    if amount is None or amount < 0:
        return {"status": "MISSING_REQUIRED_INPUT", "reason_code": "AMOUNT_UNAVAILABLE", "warnings": ["A non-negative payment amount is required."], "input": source}

    # Net-of-TDS first resolves the exact selected Phase 3 rule/rate. It is
    # intentionally unavailable for threshold regimes where a gross-up would
    # need an unconfigured allocation policy.
    if mode == "NET_OF_TDS":
        law = determine_governing_tds_law(credit_date=source.get("credit_date"), payment_date=source.get("payment_date"), financial_year=source.get("financial_year"))
        nature = classify_payment_nature(source)
        rule, rule_status = select_statutory_rule(source, statutory_rules, law, nature)
        rate_info = determine_applicable_tds_rate(source, rule, law.get("effective_event_date"))
        if not rule or rate_info.get("status") != "RATE_DETERMINED":
            return {"status": "RULE_NOT_FOUND" if not rule else "REVIEW_REQUIRED", "reason_code": rule_status if not rule else rate_info.get("status"), "warnings": ["Net-of-TDS amount cannot be grossed up until the configured rule and rate are determinable."], "input": source}
        if str(rule.get("threshold_type") or "NO_THRESHOLD").upper() != "NO_THRESHOLD":
            return {"status": "REVIEW_REQUIRED", "reason_code": "NET_MODE_THRESHOLD_REVIEW", "warnings": ["Net-of-TDS gross-up is not determinable for this configured threshold regime."], "rule_snapshot": deepcopy(rule), "input": source}
        rate = rate_info["rate"]
        if rate >= Decimal("100"):
            return {"status": "REVIEW_REQUIRED", "reason_code": "NET_MODE_RATE_INVALID", "warnings": ["Configured rate cannot be used for net-of-TDS gross-up."], "rule_snapshot": deepcopy(rule), "input": source}
        gross = (amount / (Decimal("1") - rate / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        source["amount"] = str(gross)

    prior = _decimal(source.get("previous_aggregate_amount"))
    phase3_inputs = [source]
    if prior is not None and prior > 0:
        # Feed the declared source aggregate through the same Phase 3 ledger
        # accumulator. The synthetic row is never returned or persisted.
        phase3_inputs = [{**source, "transaction_id": "TDS-CALCULATOR-PRIOR", "source_reference": "TDS-CALCULATOR-PRIOR", "amount": str(prior), "taxable_amount": None, "source_row_number": 0}, {**source, "source_row_number": 1}]
    phase3 = calculate_ledger_transactions(phase3_inputs, statutory_rules, assignment_id=assignment_id)[-1]
    result = {"input": deepcopy(transaction), "payment_mode": mode, **phase3}
    if phase3.get("calculation_status") != "CALCULATED":
        result["status"] = phase3.get("calculation_status") or "REVIEW_REQUIRED"
        result["warnings"] = [phase3.get("reason") or "Calculation requires review."]
        return result
    gross = _decimal(source.get("amount"))
    expected = _decimal(phase3.get("expected_tds"))
    result.update({
        "status": "CALCULATED", "gross_amount": float(gross) if gross is not None else None,
        "taxable_tds_base": phase3.get("calculation_base_amount"), "threshold_amount": phase3.get("threshold"),
        "threshold_consumed": phase3.get("prior_aggregate_amount"),
        "threshold_excess": phase3.get("amount_subject_to_tds"), "tds_rate": phase3.get("applicable_rate"),
        "tds_amount": phase3.get("expected_tds"), "net_payment": float(gross - expected) if gross is not None and expected is not None else None,
        "actual_tds_deducted": phase3.get("tds_deducted"),
        "deduction_date": source.get("deduction_date"), "tds_trigger": "CREDIT_OR_PAYMENT", "trigger_date": phase3.get("effective_event_date"),
        "provision": phase3.get("section_reference"), "rule_snapshot": deepcopy(phase3.get("rule_snapshot")),
        "warnings": [],
    })
    if mode == "NET_OF_TDS":
        result["entered_net_payment"] = float(amount)
    if not interest_rules:
        result.update({"deposit_due_date": None, "interest_status": "NOT_REQUESTED", "interest": None})
        return result
    timing_source = {**source, "amount": source.get("amount"), "tds_deducted": phase3.get("expected_tds"), "deposit_date": source.get("actual_deposit_date") or source.get("deposit_date")}
    timing_phase3 = {**phase3, "tds_deducted": phase3.get("expected_tds"), "tds_deduction_difference": 0}
    timing = calculate_interest_compliance([timing_phase3], [timing_source], interest_rules, assignment_id=assignment_id, calculation_id="TDS-CALCULATOR", ledger_version_id="TDS-CALCULATOR")[0]
    result.update({"deposit_due_date": timing.get("deposit_due_date"), "due_date_rule_snapshot": deepcopy(timing.get("deposit_rule_snapshot")), "interest_status": timing.get("overall_status"), "interest": {key: timing.get(key) for key in ("deduction_interest", "deposit_interest", "total_interest", "deduction_delay_periods", "deposit_delay_periods", "actual_deposit_date")}})
    if not timing_source.get("deposit_date"):
        result["warnings"].append("Actual deposit date not provided; interest cannot be determined.")
    return result


# ---------- Phase 5: source-provided deposit / challan evidence ----------
DEPOSIT_EVIDENCE_SCHEMA = ["deposit_transaction_id", "challan_number", "bsr_code", "cin", "deposit_date", "challan_date", "amount_deposited", "tds_amount", "interest_amount", "fee_amount", "total_amount", "tan", "section", "financial_year", "quarter", "month", "bank_reference"]


def validate_deposit_evidence(filename: str, content: bytes, assignment: dict | None = None) -> dict:
    """Validate evidence without treating a ledger deposit value as a challan."""
    try:
        table = read_table(filename, content, TDS_DEPOSIT_EVIDENCE)
        raw_rows, mapped, unmapped = parse_rows(table, TDS_DEPOSIT_EVIDENCE)
    except ParseError as exc:
        return {"file": {"filename": filename}, "rows": [], "mapped_headers": [], "unmapped_headers": [], "can_commit": False, "summary": {"total_rows": 0, "invalid_rows": 0, "review_rows": 0}, "mapping_warnings": [str(exc)]}
    rows, invalid, review, seen_references = [], 0, 0, set()
    for raw in raw_rows:
        issues, row = [], {field: raw.get(field) or None for field in DEPOSIT_EVIDENCE_SCHEMA}
        row["source_row_number"], row["source_file_name"] = raw["row_no"], filename
        row.update(source_provenance(row.get("deposit_transaction_id"), generated_prefix="EVID", row_number=raw["row_no"]))
        row["evidence_id"], row["evidence_id_status"] = row["source_reference"], row["source_reference_status"]
        for field in ("amount_deposited", "tds_amount", "interest_amount", "fee_amount", "total_amount"):
            supplied = raw.get(field); value = parse_amount(supplied) if supplied not in (None, "") else None
            row[field] = value
            if supplied not in (None, "") and (value is None or value < 0): issues.append(f"{field} is invalid or negative.")
        for field in ("deposit_date", "challan_date"):
            row[field], issue = _normalise_date(raw.get(field))
            if issue: issues.append(issue)
        row["financial_year"] = normalise_financial_year(raw.get("financial_year"))
        if raw.get("financial_year") and not row["financial_year"]: issues.append("Financial year is invalid.")
        if assignment and row["financial_year"] and row["financial_year"] != assignment.get("financial_year"):
            issues.append("Financial year does not match this TDS Compliance assignment.")
        if assignment and row.get("quarter") and assignment.get("quarter") and str(row["quarter"]).upper() != str(assignment["quarter"]).upper():
            issues.append("Quarter does not match this TDS Compliance assignment.")
        row["tan"] = str(row.get("tan") or "").strip().upper() or None
        if row["tan"] and not re.fullmatch(r"[A-Z]{4}[0-9]{5}[A-Z]", row["tan"]): issues.append("TAN format is invalid.")
        if assignment and row["tan"] and assignment.get("tan") and row["tan"] != str(assignment["tan"]).upper():
            issues.append("TAN does not match this TDS Compliance assignment.")
        components = [row.get(k) for k in ("tds_amount", "interest_amount", "fee_amount")]
        total = row.get("total_amount") or row.get("amount_deposited")
        if total is not None and all(x is not None for x in components) and abs(total - sum(components)) > 0.01: issues.append("Total amount does not equal supplied TDS, interest and fee components.")
        if row.get("tds_amount") is None: issues.append("TDS amount is not provided; deposit amount cannot be matched.")
        duplicate_key = (row.get("deposit_transaction_id"), row.get("challan_number") or row.get("cin") or row.get("bank_reference"))
        if all(duplicate_key) and duplicate_key in seen_references: issues.append("Duplicate deposit reference; CA review is required.")
        elif all(duplicate_key): seen_references.add(duplicate_key)
        row["validation_issues"] = issues; row["validation_status"] = "INVALID" if any("invalid or negative" in x or "format is invalid" in x for x in issues) else "REVIEW_REQUIRED" if issues else "VALID"
        invalid += row["validation_status"] == "INVALID"; review += row["validation_status"] == "REVIEW_REQUIRED"
        if assignment: row.update({k: assignment.get(k) for k in ("assignment_id", "organization_id", "client_id")})
        rows.append(row)
    return {"file": {"filename": filename}, "rows": rows, "mapped_headers": mapped, "unmapped_headers": unmapped, "mapping_warnings": [] if mapped else ["No recognised deposit-evidence headers were detected."], "summary": {"total_rows": len(rows), "invalid_rows": invalid, "review_rows": review, "duplicate_rows": sum(any("Duplicate deposit reference" in issue for issue in row["validation_issues"]) for row in rows)}, "can_commit": bool(rows) and not invalid and bool(mapped)}


def select_phase5_policy(policies: list[dict], *, financial_year: str | None, event_date: str | None = None) -> tuple[dict | None, str | None]:
    """Select only one approved policy; equal priority is intentionally unsafe."""
    candidates = [p for p in policies if p.get("active", True) and p.get("status", "APPROVED") in {"APPROVED", "ACTIVE"} and (not p.get("financial_years") or financial_year in p.get("financial_years", [])) and (not event_date or str(p.get("effective_from", "0000-01-01")) <= event_date <= str(p.get("effective_to", "9999-12-31")))]
    if not candidates: return None, "PHASE5_POLICY_NOT_FOUND"
    priority = max(int(p.get("priority", 0)) for p in candidates); winners = [p for p in candidates if int(p.get("priority", 0)) == priority]
    return (deepcopy(winners[0]), None) if len(winners) == 1 else (None, "PHASE5_POLICY_AMBIGUOUS")


def calculate_deposit_compliance(phase3_results: list[dict], evidence_rows: list[dict], phase4_results: list[dict] | None = None, *, assignment_id: str, calculation_id: str, evidence_version_id: str | None = None, policies: list[dict] | None = None, relationships: list[dict] | None = None) -> list[dict]:
    """Match only explicit references inside one assignment/FY; no name/amount-only matching."""
    phase4_by_tx = {r.get("transaction_id"): r for r in (phase4_results or [])}
    by_reference: dict[str, list[dict]] = {}
    for evidence in evidence_rows:
        key = str(evidence.get("deposit_transaction_id") or evidence.get("source_reference") or "")
        if key: by_reference.setdefault(key, []).append(evidence)
    results = []
    for liability in phase3_results:
        tx, expected = liability.get("transaction_id"), _decimal(liability.get("expected_tds"))
        policy, policy_error = select_phase5_policy(policies or [], financial_year=liability.get("financial_year"), event_date=liability.get("effective_event_date")) if policies is not None else ({"policy_id": "LEGACY_EXACT_REFERENCE", "policy_version": "1", "authoritative_identifier_types": ["EXACT_TRANSACTION_REFERENCE"], "permitted_relationship_types": ["ONE_TO_ONE", "ONE_TO_MANY"], "allocation_policy": "EXACT_REFERENCE_ONLY", "ambiguity_policy": "REVIEW_REQUIRED"}, None)
        base = {"assignment_id": assignment_id, "calculation_id": calculation_id, "evidence_version_id": evidence_version_id, "transaction_id": tx, "calculation_status": liability.get("calculation_status"), "governing_law": liability.get("governing_act"), "payment_nature": liability.get("payment_nature"), "deductee_type": liability.get("deductee_type"), "deductible_date": liability.get("effective_event_date"), "actual_deduction_date": liability.get("actual_deduction_date"), "expected_tds": float(expected) if expected is not None else None, "actual_tds_deducted": liability.get("tds_deducted"), "deposited_tds": None, "deposit_difference": None, "deposit_status": "DEPOSIT_NOT_DETERMINABLE", "timeliness_status": "DUE_DATE_NOT_DETERMINABLE", "interest_status": phase4_by_tx.get(tx, {}).get("overall_status", "INTEREST_NOT_AVAILABLE"), "evidence_entries": [], "phase5_rule_snapshot": deepcopy(policy) if policy else None}
        if policy_error:
            base.update({"relationship_status": "REVIEW_REQUIRED", "overall_status": "REVIEW_REQUIRED", "reason_code": policy_error, "reason": "Phase 5 policy selection is unresolved."}); results.append(base); continue
        if liability.get("calculation_status") != "CALCULATED" or expected is None: base.update({"overall_status": "REVIEW_REQUIRED", "reason": "Phase 3 liability is not determinable."}); results.append(base); continue
        explicit = [r for r in (relationships or []) if tx in r.get("liability_ids", [])]
        if len(explicit) > 1:
            base.update({"relationship_status": "REVIEW_REQUIRED", "overall_status": "REVIEW_REQUIRED", "reason_code": "AMBIGUOUS_DEPOSIT_MAPPING", "candidate_evidence_ids": [e for r in explicit for e in r.get("evidence_ids", [])], "reason": "Multiple explicit deposit relationships could apply."}); results.append(base); continue
        matches = by_reference.get(str(tx), []) if not explicit else [e for e in evidence_rows if e.get("evidence_id") in explicit[0].get("evidence_ids", [])]
        if not matches: base.update({"deposit_status": "MISSING_DEPOSIT_EVIDENCE", "overall_status": "MISSING_DEPOSIT_EVIDENCE", "reason": "No source-provided deposit evidence matched this transaction reference."}); results.append(base); continue
        if any(e.get("validation_status") == "INVALID" for e in matches): base.update({"deposit_status": "REVIEW_REQUIRED", "overall_status": "REVIEW_REQUIRED", "reason": "Matched deposit evidence contains invalid values."}); results.append(base); continue
        if any(_decimal(e.get("tds_amount")) is None for e in matches):
            base.update({"overall_status": "NOT_DETERMINABLE", "reason": "Matched evidence does not provide an authoritative TDS amount."}); results.append(base); continue
        deposited = sum(_decimal(e.get("tds_amount")) for e in matches)
        difference = deposited - expected; due = _day(phase4_by_tx.get(tx, {}).get("deposit_due_date")); dates = [_day(e.get("deposit_date")) for e in matches]
        relationship_id = explicit[0].get("relationship_id") if explicit else f"DEP-{assignment_id}-{tx}"
        base.update({"evidence_entries": deepcopy(matches), "deposited_tds": float(deposited), "deposit_difference": float(difference), "deposit_group_id": relationship_id, "relationship_id": relationship_id, "relationship_status": "ESTABLISHED", "liability_ids": explicit[0].get("liability_ids", [tx]) if explicit else [tx], "group_size": len(matches), "match_method": explicit[0].get("mapping_method", "EXACT_TRANSACTION_REFERENCE") if explicit else "EXACT_TRANSACTION_REFERENCE"})
        base["deposit_status"] = "DEPOSIT_MATCHED" if difference == 0 else "DEPOSIT_SHORT" if difference < 0 else "DEPOSIT_EXCESS"
        deduction_date = _day(phase4_by_tx.get(tx, {}).get("actual_deduction_date"))
        if deduction_date and any(item and item < deduction_date for item in dates):
            base.update({"timeliness_status": "INVALID_DATE_SEQUENCE", "overall_status": "REVIEW_REQUIRED", "reason": "A source-provided deposit date precedes the Phase 4 actual deduction date; no timeliness inference was made."})
            results.append(base); continue
        if not due: base["timeliness_status"] = "DUE_DATE_NOT_DETERMINABLE"
        elif any(not item for item in dates): base["timeliness_status"] = "DEPOSIT_DATE_NOT_PROVIDED"
        else: base["timeliness_status"] = "DEPOSIT_LATE" if max(dates) > due else "DEPOSIT_ON_TIME"
        base["overall_status"] = "FULLY_DEPOSIT_COMPLIANT" if base["deposit_status"] == "DEPOSIT_MATCHED" and base["timeliness_status"] == "DEPOSIT_ON_TIME" else "DEPOSIT_AND_TIMELINESS_EXCEPTION" if base["deposit_status"] != "DEPOSIT_MATCHED" and base["timeliness_status"] == "DEPOSIT_LATE" else "DEPOSIT_AMOUNT_EXCEPTION" if base["deposit_status"] != "DEPOSIT_MATCHED" else "DEPOSIT_TIMELINESS_EXCEPTION" if base["timeliness_status"] == "DEPOSIT_LATE" else "NOT_DETERMINABLE"
        base["reason"] = "Deposit result uses source-provided evidence matched by exact transaction reference; no challan or date was inferred."
        results.append(base)
    return results


def calculate_interest_from_deposit_results(calculation_rows: list[dict], deposit_rows: list[dict], interest_rules: list[dict], *, assignment_id: str, calculation_id: str, deposit_run_id: str, ledger_version_id: str | None = None) -> list[dict]:
    """Calculate delay interest from immutable Phase 2 and Phase 4 snapshots.

    This intentionally does not inspect live rules for TDS liability, ledger
    rows, government evidence, or frontend values.  Interest rules only supply
    a configured timing/interest policy after a frozen liability and persisted
    deposit result have been selected.
    """
    calculations = {row.get("transaction_id"): row for row in calculation_rows}
    results: list[dict] = []
    for deposit in deposit_rows:
        transaction_id = deposit.get("transaction_id")
        phase2 = calculations.get(transaction_id, {})
        evidence_entries = deposit.get("evidence_entries") or []
        deposit_days = [_day(item.get("deposit_date")) for item in evidence_entries]
        actual_deposit = max((item for item in deposit_days if item), default=None) if deposit_days and all(deposit_days) else None
        result = {
            "assignment_id": assignment_id, "calculation_id": calculation_id, "calculation_run_id": calculation_id,
            "deposit_run_id": deposit_run_id, "ledger_version_id": ledger_version_id,
            "deposit_evidence_version_id": deposit.get("evidence_version_id"), "transaction_id": transaction_id,
            "expected_tds": deposit.get("expected_tds"), "actual_tds": deposit.get("actual_tds_deducted"),
            "deposited_tds": deposit.get("deposited_tds"), "deposit_difference": deposit.get("deposit_difference"),
            "deductible_date": deposit.get("deductible_date"), "actual_deduction_date": deposit.get("actual_deduction_date"),
            "actual_deposit_date": actual_deposit.isoformat() if actual_deposit else None,
            "deduction_interest": None, "deposit_interest": None, "total_interest": None,
            "deduction_status": "DATE_NOT_DETERMINABLE", "deposit_status": "DATE_NOT_DETERMINABLE",
            "deposit_control_status": deposit.get("overall_status"), "evidence_entries": deepcopy(evidence_entries),
        }
        if not phase2 or phase2.get("calculation_status") != "CALCULATED" or _decimal(deposit.get("expected_tds")) is None:
            result.update({"overall_status": "INSUFFICIENT_DATA", "reason": "A persisted, calculated Phase 2 liability is not available for this deposit result.", "explanation": "Interest was not calculated without a frozen expected-TDS snapshot."})
            results.append(result); continue
        event = deposit.get("deductible_date") or phase2.get("effective_event_date")
        law = deposit.get("governing_law") or phase2.get("governing_act")
        nature = deposit.get("payment_nature") or phase2.get("payment_nature")
        deductee_type = deposit.get("deductee_type") or phase2.get("deductee_type")
        deduction_rule, deduction_error = _configured_interest_rule(interest_rules, interest_type="DEDUCTION_DELAY_INTEREST", law=law, event_date=event, payment_nature=nature, deductee_type=deductee_type)
        deposit_rule, deposit_error = _configured_interest_rule(interest_rules, interest_type="DEPOSIT_DELAY_INTEREST", law=law, event_date=event, payment_nature=nature, deductee_type=deductee_type)
        if not deduction_rule or not deposit_rule:
            ambiguous = "RULE_AMBIGUOUS" in {deduction_error, deposit_error}
            result.update({"overall_status": "INTEREST_REVIEW_REQUIRED" if ambiguous else "POLICY_NOT_CONFIGURED", "reason": "The approved configured interest policy is ambiguous or unavailable for this frozen result.", "explanation": "No statutory interest rate, timing rule, or due date was guessed."})
            results.append(result); continue
        result["deduction_rule_snapshot"] = deepcopy({key: value for key, value in deduction_rule.items() if key != "_id"})
        result["deposit_rule_snapshot"] = deepcopy({key: value for key, value in deposit_rule.items() if key != "_id"})
        deductible, actual_deduction = _day(event), _day(deposit.get("actual_deduction_date"))
        if not deductible or not actual_deduction:
            result.update({"overall_status": "DATE_NOT_DETERMINABLE", "reason": "The persisted deductible or actual deduction date is unavailable.", "explanation": "A missing date was not inferred from another source."})
            results.append(result); continue
        if actual_deduction < deductible:
            result.update({"overall_status": "INVALID_DATE_ORDER", "reason": "The persisted actual deduction date precedes the deductible event.", "explanation": "Negative delay was not calculated."})
            results.append(result); continue
        due_date, due_error = configured_deposit_due_date(actual_deduction.isoformat(), deposit_rule, deposit)
        result["deposit_due_date"] = due_date
        if due_error:
            result.update({"overall_status": "POLICY_NOT_CONFIGURED", "reason": "The approved deposit-interest policy cannot derive a due date from the persisted dates.", "explanation": "A universal deadline was not assumed."})
            results.append(result); continue
        if not actual_deposit:
            result.update({"overall_status": "DATE_NOT_DETERMINABLE", "reason": "A usable persisted deposit date is not available in the selected deposit run.", "explanation": "Missing deposit evidence was not treated as on time."})
            results.append(result); continue
        if actual_deposit < actual_deduction:
            result.update({"overall_status": "INVALID_DATE_ORDER", "reason": "The persisted deposit date precedes the actual deduction date.", "explanation": "Negative deposit delay was not calculated."})
            results.append(result); continue
        interest_phase2 = {**phase2, "expected_tds": deposit.get("expected_tds"), "tds_deducted": deposit.get("actual_tds_deducted")}
        # Validate the configured basis even where there is no delay, but do
        # not feed an end date before its start date into a period counter.
        deduction = _interest_component(deduction_rule, deductible, actual_deduction if actual_deduction >= deductible else deductible, interest_phase2)
        deposit_component = _interest_component(deposit_rule, due_date, actual_deposit if actual_deposit >= _day(due_date) else due_date, interest_phase2)
        if actual_deduction == deductible and deduction["determinable"]:
            deduction.update({"periods": 0, "interest": Decimal("0.00")})
        if actual_deposit <= _day(due_date) and deposit_component["determinable"]:
            deposit_component.update({"periods": 0, "interest": Decimal("0.00")})
        if not deduction["determinable"] or not deposit_component["determinable"]:
            result.update({"overall_status": "INTEREST_REVIEW_REQUIRED", "reason": "The configured interest policy has an unsupported base, rate, period counter, or rounding method.", "explanation": "Interest was not guessed from incomplete policy configuration."})
            results.append(result); continue
        total = deduction["interest"] + deposit_component["interest"]
        deduction_late, deposit_late = actual_deduction > deductible, actual_deposit > _day(due_date)
        result.update({"deduction_delay_periods": deduction["periods"], "deduction_interest_rate": float(deduction["rate"]), "deduction_interest_base": float(deduction["base"]), "deduction_interest": float(deduction["interest"]), "deposit_delay_periods": deposit_component["periods"], "deposit_interest_rate": float(deposit_component["rate"]), "deposit_interest_base": float(deposit_component["base"]), "deposit_interest": float(deposit_component["interest"]), "total_interest": float(total), "deduction_status": "LATE_DEDUCTION" if deduction_late else "DEDUCTION_SUPPORTED", "deposit_status": "LATE_DEPOSIT" if deposit_late else "DEPOSIT_SUPPORTED"})
        result["overall_status"] = "INTEREST_DUE" if total > 0 else "NO_INTEREST_INDICATED"
        result["reason"] = "Interest was calculated from the selected persisted calculation and deposit snapshots using approved configured interest policies."
        result["explanation"] = f"Deductible date {deductible.isoformat()}; actual deduction {actual_deduction.isoformat()}; deposit due {due_date}; actual deposit {actual_deposit.isoformat()}."
        results.append(result)
    return results


def freeze_calculation_results(results: list[dict]) -> list[dict]:
    """Create an immutable-at-write-time calculation payload.

    Mongo serialises on insert, but this explicit deep copy prevents an API
    caller or later in-process rule mutation from sharing nested rule snapshot
    objects with the historical calculation document before persistence.
    """
    return deepcopy(results)


# ---------- Phase 4: rule-configured interest and deposit compliance ----------
INTEREST_TYPES = {"DEDUCTION_DELAY_INTEREST", "DEPOSIT_DELAY_INTEREST"}


def _configured_interest_rule(rules: list[dict], *, interest_type: str, law: str | None, event_date: str | None, payment_nature: str | None, deductee_type: str | None) -> tuple[dict | None, str | None]:
    candidates = []
    for rule in rules:
        if rule.get("interest_type") != interest_type or not rule.get("active", True) or rule.get("lifecycle") not in {"APPROVED", "ACTIVE"}:
            continue
        if (rule.get("governing_law") or rule.get("act")) != law or not event_date:
            continue
        if not (str(rule.get("effective_from") or "0000-01-01") <= event_date <= str(rule.get("effective_to") or "9999-12-31")):
            continue
        if rule.get("payment_nature") and rule.get("payment_nature") != payment_nature:
            continue
        if rule.get("deductee_type") and rule.get("deductee_type") != deductee_type:
            continue
        candidates.append(rule)
    if not candidates:
        return None, "RULE_NOT_FOUND"
    priority = max(int(rule.get("priority", 0)) for rule in candidates)
    selected = [rule for rule in candidates if int(rule.get("priority", 0)) == priority]
    if len(selected) != 1:
        # Never resolve an overlap by source order. A CA must correct the
        # registry before an interest amount can be relied upon.
        return None, "RULE_AMBIGUOUS"
    return selected[0], None


def calculate_interest_periods(start_date: Any, end_date: Any, rule: dict) -> int | None:
    """Configured period counter; no implicit days/30 or ceil fallback."""
    start, end = _day(start_date), _day(end_date)
    method = str(rule.get("period_counting_method") or "").upper()
    if not start or not end or end < start:
        return None
    if method == "CALENDAR_MONTH_OR_PART":
        return (end.year - start.year) * 12 + end.month - start.month + 1
    if method == "CONFIGURED_FIXED_DAY_BLOCK":
        block = int(rule.get("period_day_block") or 0)
        if block <= 0:
            return None
        elapsed = (end - start).days + 1
        return (elapsed + block - 1) // block
    return None


def configured_deposit_due_date(deduction_date: Any, rule: dict, transaction: dict) -> tuple[str | None, str | None]:
    """Return only a rule-derived deadline; no universal next-month assumption."""
    deduction = _day(deduction_date)
    if not deduction:
        return None, "DEDUCTION_DATE_NOT_PROVIDED"
    mode = str(rule.get("deposit_due_date_mode") or "").upper()
    if mode == "FIXED_OFFSET_DAYS":
        offset = rule.get("deposit_due_offset_days")
        if offset is None:
            return None, "RULE_NOT_FOUND"
        try:
            from datetime import timedelta
            return (deduction + timedelta(days=int(offset))).isoformat(), None
        except (TypeError, ValueError):
            return None, "RULE_NOT_FOUND"
    if mode == "NEXT_MONTH_CONFIGURED_DAY":
        day = rule.get("deposit_due_day")
        if not isinstance(day, int) or not 1 <= day <= 31:
            return None, "RULE_NOT_FOUND"
        month, year = deduction.month + 1, deduction.year
        if month == 13:
            month, year = 1, year + 1
        try:
            return date(year, month, day).isoformat(), None
        except ValueError:
            return None, "RULE_NOT_FOUND"
    if mode == "CHALLAN_CUM_STATEMENT":
        supplied = _day(transaction.get("configured_deposit_due_date"))
        return (supplied.isoformat(), None) if supplied else (None, "RULE_NOT_FOUND")
    return None, "RULE_NOT_FOUND"


def _interest_base(rule: dict, phase3: dict) -> Decimal | None:
    basis = str(rule.get("interest_base") or "").lower()
    expected, actual = _decimal(phase3.get("expected_tds")), _decimal(phase3.get("tds_deducted"))
    if basis == "expected_tds": return expected
    if basis == "actual_tds": return actual
    if basis == "tax_not_deducted": return max(Decimal("0"), (expected or Decimal("0")) - (actual or Decimal("0"))) if expected is not None and actual is not None else None
    if basis == "tax_short_deducted": return max(Decimal("0"), (expected or Decimal("0")) - (actual or Decimal("0"))) if expected is not None and actual is not None else None
    return None


def _interest_component(rule: dict, start: Any, end: Any, phase3: dict) -> dict:
    periods = calculate_interest_periods(start, end, rule)
    base, rate = _interest_base(rule, phase3), _decimal(rule.get("rate"))
    rounding, precision = str(rule.get("rounding_method") or "").upper(), int(rule.get("rounding_precision", 2))
    if periods is None or base is None or rate is None or rounding != "HALF_UP":
        return {"determinable": False, "periods": periods, "base": base, "rate": rate, "interest": None}
    value = (base * rate * Decimal(periods) / Decimal("100")).quantize(Decimal("1").scaleb(-precision), rounding=ROUND_HALF_UP)
    return {"determinable": True, "periods": periods, "base": base, "rate": rate, "interest": value}


def calculate_interest_compliance(phase3_results: list[dict], ledger_rows: list[dict], interest_rules: list[dict], *, assignment_id: str, calculation_id: str, ledger_version_id: str) -> list[dict]:
    """Phase 4 consumes Phase 3 results; it never recalculates expected TDS."""
    source_by_id = {row.get("transaction_id"): row for row in ledger_rows}
    results = []
    for phase3 in phase3_results:
        source = source_by_id.get(phase3.get("transaction_id"), {})
        result = {"assignment_id": assignment_id, "ledger_version_id": ledger_version_id, "calculation_id": calculation_id, "calculation_run_id": calculation_id, "transaction_id": phase3.get("transaction_id"), "deductee_name": source.get("deductee_name"), "governing_law": phase3.get("governing_act"), "expected_tds": phase3.get("expected_tds"), "actual_tds": phase3.get("tds_deducted"), "tds_difference": phase3.get("tds_deduction_difference"), "deductible_date": phase3.get("effective_event_date"), "actual_deduction_date": source.get("deduction_date"), "actual_deposit_date": source.get("deposit_date"), "deduction_interest": None, "deposit_interest": None, "total_interest": None, "deduction_status": "NOT_DETERMINABLE", "deposit_status": "NOT_DETERMINABLE"}
        if phase3.get("calculation_status") != "CALCULATED":
            result.update({"overall_status": "REVIEW_REQUIRED", "reason": "Phase 3 expected TDS is not a completed configured-rule calculation.", "explanation": "Interest compliance cannot proceed until the upstream Phase 3 calculation is determinable."})
            results.append(result); continue
        if phase3.get("tds_deducted") is None:
            result.update({"overall_status": "ACTUAL_TDS_NOT_PROVIDED", "reason": "Actual TDS is not provided.", "explanation": "Actual TDS was not converted to zero; deduction and deposit interest are not determinable."})
            results.append(result); continue
        if _decimal(phase3.get("expected_tds")) == Decimal("0"):
            result.update({"overall_status": "REVIEW_REQUIRED", "reason": "Phase 3 expected TDS is zero.", "explanation": "Phase 4 preserves the Phase 3 zero-TDS rationale and does not infer that every zero-TDS record has identical interest treatment."})
            results.append(result); continue
        event = phase3.get("effective_event_date")
        deduction_rule, deduction_rule_error = _configured_interest_rule(interest_rules, interest_type="DEDUCTION_DELAY_INTEREST", law=phase3.get("governing_act"), event_date=event, payment_nature=phase3.get("payment_nature"), deductee_type=source.get("deductee_type"))
        deposit_rule, deposit_rule_error = _configured_interest_rule(interest_rules, interest_type="DEPOSIT_DELAY_INTEREST", law=phase3.get("governing_act"), event_date=event, payment_nature=phase3.get("payment_nature"), deductee_type=source.get("deductee_type"))
        if not deduction_rule or not deposit_rule:
            status = "RULE_AMBIGUOUS" if "RULE_AMBIGUOUS" in {deduction_rule_error, deposit_rule_error} else "RULE_NOT_FOUND"
            result.update({"overall_status": status, "reason": "A configured deduction or deposit interest rule is unavailable or ambiguous.", "explanation": "No interest rate, base or deadline was invented."})
            results.append(result); continue
        result["deduction_rule_snapshot"] = deepcopy({key: value for key, value in deduction_rule.items() if key != "_id"})
        result["deposit_rule_snapshot"] = deepcopy({key: value for key, value in deposit_rule.items() if key != "_id"})
        actual_deduction = _day(source.get("deduction_date"))
        deductible = _day(event)
        if not actual_deduction:
            result.update({"overall_status": "DEDUCTION_DATE_NOT_PROVIDED", "reason": "Actual deduction date is not provided.", "explanation": "No deduction date was inferred from payment or credit date."})
            results.append(result); continue
        if not deductible or actual_deduction < deductible:
            result.update({"overall_status": "INVALID_DATE_SEQUENCE", "reason": "Actual deduction date precedes the deductible event or event is invalid.", "explanation": "Negative delay was not calculated."})
            results.append(result); continue
        due_date, due_error = configured_deposit_due_date(actual_deduction.isoformat(), deposit_rule, source)
        result["deposit_due_date"] = due_date
        actual_deposit = _day(source.get("deposit_date"))
        if due_error:
            result.update({"overall_status": "RULE_NOT_FOUND", "reason": "The configured deposit rule cannot derive a due date.", "explanation": "A universal deposit deadline was not assumed."})
            results.append(result); continue
        if not actual_deposit:
            result.update({"overall_status": "DEPOSIT_DATE_NOT_PROVIDED", "reason": "Actual deposit date is not provided.", "explanation": "Missing deposit evidence was not treated as on time."})
            results.append(result); continue
        if actual_deposit < actual_deduction:
            result.update({"overall_status": "INVALID_DATE_SEQUENCE", "reason": "Actual deposit precedes actual deduction.", "explanation": "Negative deposit delay was not calculated."})
            results.append(result); continue
        deduction = _interest_component(deduction_rule, deductible, actual_deduction, phase3)
        deposit = _interest_component(deposit_rule, due_date, actual_deposit, phase3)
        if not deduction["determinable"] or not deposit["determinable"]:
            result.update({"overall_status": "INTEREST_NOT_DETERMINABLE", "reason": "Configured interest rate, base, period-counting or rounding cannot be safely applied.", "explanation": "Interest was not guessed from incomplete configuration."})
            results.append(result); continue
        result.update({"deduction_delay_periods": deduction["periods"], "deduction_interest_rate": float(deduction["rate"]), "deduction_interest_base": float(deduction["base"]), "deduction_interest": float(deduction["interest"]), "deposit_delay_periods": deposit["periods"], "deposit_interest_rate": float(deposit["rate"]), "deposit_interest_base": float(deposit["base"]), "deposit_interest": float(deposit["interest"]), "total_interest": float(deduction["interest"] + deposit["interest"]), "deduction_status": "DEDUCTION_DELAY" if actual_deduction > deductible else "COMPLIANT", "deposit_status": "DEPOSIT_DELAY" if actual_deposit > _day(due_date) else "COMPLIANT"})
        result["overall_status"] = "DEDUCTION_AND_DEPOSIT_DELAY" if result["deduction_status"] == "DEDUCTION_DELAY" and result["deposit_status"] == "DEPOSIT_DELAY" else result["deduction_status"] if result["deduction_status"] != "COMPLIANT" else result["deposit_status"]
        result["reason"] = "Interest calculated only from the configured deduction and deposit rules."
        result["explanation"] = f"Deductible date {event}; actual deduction {actual_deduction.isoformat()}; deposit due {due_date}; actual deposit {actual_deposit.isoformat()}; total configured interest {result['total_interest']}."
        results.append(result)
    return results
