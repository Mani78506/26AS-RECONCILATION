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
from .source_traceability import governed_activation_errors


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


PAYMENT_LEDGER_SCHEMA = ["transaction_id", "source_reference", "deductee_name", "deductee_pan", "deductee_type", "deductee_gstin", "vendor_code", "invoice_number", "invoice_date", "transaction_date", "credit_date", "payment_date", "amount", "taxable_amount", "payment_nature", "recipient_residency", "recipient_category", "payer_category", "section_input", "description", "tds_expected", "tds_deducted", "tds_deposited", "deduction_date", "deposit_date", "challan_number", "challan_date", "certificate_number", "certificate_rate", "certificate_valid_from", "certificate_valid_to", "financial_year", "tax_year", "quarter"]
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
    "salary": "salary", "provident_fund": "provident_fund", "insurance_commission": "insurance_commission",
    "commission_brokerage": "commission_brokerage", "rent": "rent", "immovable_property": "immovable_property",
    "jda": "jda", "compulsory_acquisition": "compulsory_acquisition", "mutual_fund_units": "mutual_fund_units",
    "business_trust": "business_trust", "investment_fund": "investment_fund", "securitisation_trust": "securitisation_trust",
    "interest_securities": "interest_securities", "interest_other": "interest_other", "professional_service": "professional_service",
    "technical_service": "technical_service", "director_remuneration": "director_remuneration", "royalty": "royalty",
    "dividend": "dividend", "life_insurance": "life_insurance", "senior_citizen": "senior_citizen",
    "benefit_perquisite": "benefit_perquisite", "ecommerce": "ecommerce", "virtual_digital_asset": "virtual_digital_asset",
    "lottery": "lottery", "online_game": "online_game", "horse_race": "horse_race",
    "lottery_commission": "lottery_commission", "cash_withdrawal": "cash_withdrawal",
    "national_savings_scheme": "national_savings_scheme", "partner_remuneration_interest": "partner_remuneration_interest",
    "non_resident_interest": "non_resident_interest", "non_resident_capital_gain": "non_resident_capital_gain",
    "non_resident_other_income": "non_resident_other_income", "foreign_sports_person": "foreign_sports_person",
}

RECIPIENT_RESIDENCIES = {"RESIDENT", "NON_RESIDENT", "FOREIGN_COMPANY"}
RECIPIENT_CATEGORIES = {"PERSON", "INDIVIDUAL", "HUF", "INDIVIDUAL_HUF", "DOMESTIC_COMPANY", "COMPANY", "SENIOR_CITIZEN", "COOPERATIVE_SOCIETY", "NON_RESIDENT_COOPERATIVE_SOCIETY", "PARTNER", "UNITHOLDER", "SPECIFIED_PERSON", "ANY_NON_RESIDENT", "FOREIGN_COMPANY"}
PAYER_CATEGORIES = {"ANY_PAYER", "DESIGNATED_PERSON", "QUALIFYING_INDIVIDUAL_HUF", "BANK_COOPERATIVE_BANK_POST_OFFICE", "COMPANY", "FIRM", "ECOMMERCE_OPERATOR", "BUSINESS_TRUST", "INVESTMENT_FUND", "SECURITISATION_TRUST", "GOVERNMENT_OR_INDIAN_CONCERN"}


def controlled_recipient_residency(transaction: dict) -> str | None:
    """Return a supplied controlled residency value, never infer it from PAN.

    The legacy contractor control is retained as a narrow evidence-backed
    compatibility bridge.  Generic rows require the new controlled input.
    """
    value = str(transaction.get("recipient_residency") or "").strip().upper()
    if value in RECIPIENT_RESIDENCIES:
        return value
    contractor = str(transaction.get("contractor_residency_status") or "").upper()
    if contractor == "CONFIRMED_RESIDENT":
        return "RESIDENT"
    if contractor == "CONFIRMED_NON_RESIDENT":
        return "NON_RESIDENT"
    return None


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
    candidates, section_conflict, residency_scope_candidates = [], False, []
    classification_fact_required = False
    residency = controlled_recipient_residency(transaction)
    for rule in rules:
        # Interest and due-date policies share the governed rules collection,
        # but are never statutory withholding rules.  Letting either policy
        # type enter this selector can make an otherwise single statutory
        # match ambiguous.
        if rule.get("interest_type") or rule.get("policy_kind"):
            continue
        if not _rule_scope_matches(transaction, rule):
            continue
        if not rule.get("active", True) or rule.get("lifecycle") != "ACTIVE":
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
        residency_scope_candidates.append(rule)
        # New generalized catalog rows must have controlled residency and
        # party-category evidence.  Legacy contractor records intentionally
        # remain under their dedicated contractor evidence contract.
        if rule.get("generalized_selection_required") and rule.get("recipient_residency"):
            if not residency or rule.get("recipient_residency") != residency:
                continue
        if rule.get("generalized_selection_required") and rule.get("recipient_category") and rule.get("recipient_category") != transaction.get("recipient_category"):
            continue
        if rule.get("generalized_selection_required") and rule.get("payer_category") and rule.get("payer_category") != transaction.get("payer_category"):
            continue
        requirements = rule.get("required_classification_facts") or []
        if requirements:
            facts = transaction.get("classification_facts") or []
            def fact_is_verified(requirement):
                return any(
                    fact.get("fact_type") == requirement.get("fact_type")
                    and fact.get("value_code") == requirement.get("value_code")
                    and fact.get("source_condition_reference") == requirement.get("source_condition_reference")
                    and fact.get("evidence_status") == "VERIFIED"
                    and fact.get("evidence_reference")
                    for fact in facts
                )
            if not all(fact_is_verified(requirement) for requirement in requirements):
                classification_fact_required = True
                continue
        if section and rule.get("section_reference") and section != str(rule["section_reference"]).upper() and section != str(rule.get("historical_section_reference") or "").upper():
            section_conflict = True
            continue
        candidates.append(rule)
    if not candidates:
        if not residency and any(item.get("generalized_selection_required") and item.get("recipient_residency") for item in residency_scope_candidates):
            return None, "RECIPIENT_RESIDENCY_REQUIRED"
        if classification_fact_required:
            return None, "CLASSIFICATION_FACT_REQUIRED"
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


# Contractor controls are intentionally opt-in until a CA records a bounded
# product scope.  They turn supplied evidence into an auditable decision; they
# never derive a payer class, exception, PAN status, or deadline from labels.
CONTRACTOR_CONTROL_CONTRACT = "CONTRACTOR_WITHHOLDING_V1"
PAYER_ELIGIBILITY_STATUSES = {"CONFIRMED_ELIGIBLE", "CONFIRMED_INELIGIBLE", "INSUFFICIENT_EVIDENCE"}
CONTRACTOR_EXCEPTION_STATUSES = {"NO_EXCEPTION_CONFIRMED", "EXCEPTION_CONFIRMED", "NOT_ASSESSED", "INSUFFICIENT_EVIDENCE"}
CONTRACTOR_EXCEPTION_TYPES = {"PERSONAL_PURPOSE_INDIVIDUAL_HUF", "GOODS_CARRIAGE"}
CONTRACTOR_RESIDENCY_STATUSES = {"CONFIRMED_RESIDENT", "CONFIRMED_NON_RESIDENT", "INSUFFICIENT_EVIDENCE"}
CONTRACTOR_INVOICE_MATERIAL_STATUSES = {
    "NO_CUSTOMER_SUPPLIED_MATERIAL_CONFIRMED",
    "CUSTOMER_SUPPLIED_MATERIAL_NOT_SEPARATELY_STATED",
    "CUSTOMER_SUPPLIED_MATERIAL_SEPARATELY_STATED",
    "INSUFFICIENT_EVIDENCE",
}
PAN_OPERATIONAL_STATUSES = {"VERIFIED", "NOT_VERIFIED", "UNKNOWN", "UNAVAILABLE"}
DEDUCTOR_TYPES = {"GOVERNMENT_OFFICE", "OTHER_DEDUCTOR"}
CHALLAN_ROUTES = {"WITH_CHALLAN", "WITHOUT_CHALLAN"}


def _is_contractor_rule(rule: dict) -> bool:
    reference = str(rule.get("section_reference") or rule.get("provision_reference") or "").upper()
    return str(rule.get("payment_nature") or "").lower() == "contractor" and ("194C" in reference or "393" in reference or not reference)


def assess_contractor_applicability(transaction: dict, rule: dict) -> dict:
    """Assess only supplied contractor evidence under the review contract.

    This does not approve a legal scope.  It distinguishes ineligibility,
    established source-supported exceptions, and unavailable evidence so a
    caller can freeze the decision with the calculation snapshot.
    """
    if not _is_contractor_rule(rule):
        return {"status": "NOT_APPLICABLE", "reason_code": None, "evidence": {}}
    payer_status = str(transaction.get("payer_eligibility_status") or "INSUFFICIENT_EVIDENCE").upper()
    payer_evidence = str(transaction.get("payer_eligibility_evidence_reference") or "").strip()
    residency_status = str(transaction.get("contractor_residency_status") or "INSUFFICIENT_EVIDENCE").upper()
    residency_evidence = str(transaction.get("contractor_residency_evidence_reference") or "").strip()
    exception_status = str(transaction.get("contractor_exception_status") or "NOT_ASSESSED").upper()
    exception_type = str(transaction.get("contractor_exception_type") or "").upper()
    exception_evidence = str(transaction.get("contractor_exception_evidence_reference") or "").strip()
    evidence = {
        "payer_eligibility_status": payer_status, "payer_eligibility_evidence_reference": payer_evidence or None,
        "contractor_residency_status": residency_status, "contractor_residency_evidence_reference": residency_evidence or None,
        "contractor_exception_status": exception_status, "contractor_exception_type": exception_type or None,
        "contractor_exception_evidence_reference": exception_evidence or None,
        "contractor_personal_purpose_attestation": transaction.get("contractor_personal_purpose_attestation"),
        "contractor_personal_purpose_payer_type": transaction.get("contractor_personal_purpose_payer_type"),
        "contractor_personal_purpose_payer_type_evidence_reference": transaction.get("contractor_personal_purpose_payer_type_evidence_reference"),
        "goods_carriage_count": transaction.get("goods_carriage_count"),
        "goods_carriage_business_evidence_reference": transaction.get("goods_carriage_business_evidence_reference"),
        "goods_carriage_declaration_reference": transaction.get("goods_carriage_declaration_reference"),
        "goods_carriage_pan_reference": transaction.get("goods_carriage_pan_reference"),
        "goods_carriage_particulars_reference": transaction.get("goods_carriage_particulars_reference"),
        "contractor_invoice_material_status": str(transaction.get("contractor_invoice_material_status") or "INSUFFICIENT_EVIDENCE").upper(),
        "contractor_invoice_material_evidence_reference": transaction.get("contractor_invoice_material_evidence_reference"),
    }
    if residency_status not in CONTRACTOR_RESIDENCY_STATUSES or not residency_evidence:
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_RESIDENCY_EVIDENCE_REQUIRED", "reason": "Resident-recipient status is not evidenced for this resident-contractor rule.", "evidence": evidence}
    if residency_status == "CONFIRMED_NON_RESIDENT":
        return {"status": "NOT_APPLICABLE", "reason_code": "CONTRACTOR_RECIPIENT_CONFIRMED_NON_RESIDENT", "reason": "This resident-contractor rule does not apply where controlled evidence confirms a non-resident recipient.", "evidence": evidence}
    if residency_status != "CONFIRMED_RESIDENT":
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_RESIDENCY_UNRESOLVED", "reason": "Resident-recipient status is not confirmed; this contractor rule cannot be selected by inference.", "evidence": evidence}
    if payer_status not in PAYER_ELIGIBILITY_STATUSES or not payer_evidence:
        return {"status": "REVIEW_REQUIRED", "reason_code": "PAYER_ELIGIBILITY_EVIDENCE_REQUIRED", "reason": "Contractor payer eligibility is not evidenced by the controlled review contract.", "evidence": evidence}
    if payer_status == "CONFIRMED_INELIGIBLE":
        if exception_status == "EXCEPTION_CONFIRMED" or exception_type:
            return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_EVIDENCE_CONTRADICTORY", "reason": "Payer ineligibility conflicts with an asserted contractor exception.", "evidence": evidence}
        return {"status": "NOT_APPLICABLE", "reason_code": "PAYER_CONFIRMED_INELIGIBLE", "reason": "Controlled payer evidence states this contractor rule does not apply.", "evidence": evidence}
    if payer_status != "CONFIRMED_ELIGIBLE":
        return {"status": "REVIEW_REQUIRED", "reason_code": "PAYER_ELIGIBILITY_UNRESOLVED", "reason": "Payer eligibility is not confirmed; contractor classification alone is insufficient.", "evidence": evidence}
    if exception_status not in CONTRACTOR_EXCEPTION_STATUSES:
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_EXCEPTION_STATUS_INVALID", "reason": "Contractor exception status is not recognised by the controlled review contract.", "evidence": evidence}
    if exception_status == "EXCEPTION_CONFIRMED":
        if exception_type not in CONTRACTOR_EXCEPTION_TYPES or not exception_evidence:
            return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_EXCEPTION_EVIDENCE_REQUIRED", "reason": "An asserted contractor exception lacks a supported type or evidence reference.", "evidence": evidence}
        if exception_type == "PERSONAL_PURPOSE_INDIVIDUAL_HUF" and (
            evidence["contractor_personal_purpose_attestation"] is not True
            or evidence["contractor_personal_purpose_payer_type"] != "INDIVIDUAL_HUF"
            or not evidence["contractor_personal_purpose_payer_type_evidence_reference"]
        ):
            return {"status": "REVIEW_REQUIRED", "reason_code": "PERSONAL_PURPOSE_EVIDENCE_REQUIRED", "reason": "Personal-purpose exception requires an individual/HUF payer, an explicit attestation, and evidence references.", "evidence": evidence}
        if exception_type == "GOODS_CARRIAGE":
            count = evidence["goods_carriage_count"]
            if not isinstance(count, int) or count < 0 or count > 10 or not all(evidence[key] for key in ("goods_carriage_business_evidence_reference", "goods_carriage_declaration_reference", "goods_carriage_pan_reference", "goods_carriage_particulars_reference")):
                return {"status": "REVIEW_REQUIRED", "reason_code": "GOODS_CARRIAGE_EVIDENCE_REQUIRED", "reason": "Goods-carriage exception requires business, count, declaration, PAN and prescribed-particulars evidence.", "evidence": evidence}
        return {"status": "NOT_APPLICABLE", "reason_code": f"CONTRACTOR_EXCEPTION_{exception_type}", "reason": "A controlled review established a source-supported contractor exception.", "evidence": evidence}
    if exception_status == "NO_EXCEPTION_CONFIRMED" and (exception_type or any(evidence[key] not in (None, "") for key in ("contractor_personal_purpose_attestation", "contractor_personal_purpose_payer_type", "contractor_personal_purpose_payer_type_evidence_reference", "goods_carriage_count", "goods_carriage_business_evidence_reference", "goods_carriage_declaration_reference", "goods_carriage_pan_reference", "goods_carriage_particulars_reference"))):
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_EVIDENCE_CONTRADICTORY", "reason": "No-exception assessment conflicts with asserted exception evidence.", "evidence": evidence}
    if exception_status != "NO_EXCEPTION_CONFIRMED" or not exception_evidence:
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_EXCEPTION_NOT_ASSESSED", "reason": "No-exception treatment requires an explicit assessment and evidence reference.", "evidence": evidence}
    material_status = evidence["contractor_invoice_material_status"]
    material_evidence = evidence["contractor_invoice_material_evidence_reference"]
    if material_status not in CONTRACTOR_INVOICE_MATERIAL_STATUSES or not material_evidence:
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_INVOICE_MATERIAL_EVIDENCE_REQUIRED", "reason": "Invoice-material treatment is not evidenced for the contractor calculation base.", "evidence": evidence}
    if material_status == "CUSTOMER_SUPPLIED_MATERIAL_SEPARATELY_STATED":
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_MATERIAL_BASE_REVIEW_REQUIRED", "reason": "The invoice separately states customer-supplied material, but the current governed rule has no approved material-allocation input.", "evidence": evidence}
    if material_status == "INSUFFICIENT_EVIDENCE":
        return {"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_INVOICE_MATERIAL_UNRESOLVED", "reason": "Invoice-material treatment is not confirmed; the full invoice amount cannot be assumed safely.", "evidence": evidence}
    return {"status": "APPLICABLE", "reason_code": "CONTRACTOR_APPLICABILITY_CONFIRMED", "reason": "Payer eligibility and no-exception assessment are explicitly evidenced.", "evidence": evidence}


def pan_evidence_assessment(transaction: dict) -> dict:
    """Keep PAN syntax and operational evidence separate; no external lookup."""
    syntax = transaction.get("pan_status") or pan_status(transaction.get("deductee_pan"))
    operational = str(transaction.get("pan_operational_status") or "UNKNOWN").upper()
    reference = str(transaction.get("pan_evidence_reference") or "").strip()
    if operational not in PAN_OPERATIONAL_STATUSES:
        operational = "UNKNOWN"
    if operational == "VERIFIED" and not reference:
        operational = "UNKNOWN"
    return {"pan_syntax_status": syntax, "pan_operational_status": operational, "pan_evidence_reference": reference or None,
            "status": "EVIDENCE_BACKED" if operational == "VERIFIED" else "EVIDENCE_UNAVAILABLE" if operational == "UNAVAILABLE" else "NOT_VERIFIED"}


def determine_configured_contractor_deposit_deadline(transaction: dict, policies: list[dict]) -> dict:
    """Select an explicitly approved Rule 30/218 policy and calculate no default.

    Policies provide the branch/method values.  This function deliberately has
    no embedded statutory date values and returns review-required when scope,
    evidence, configuration, or policy selection is incomplete or ambiguous.
    """
    deduction = _day(transaction.get("deduction_date"))
    law = transaction.get("governing_act")
    financial_year = transaction.get("financial_year")
    section_reference = str(transaction.get("section_reference") or "").strip()
    deductor_type = str(transaction.get("deductor_type") or "").upper()
    challan_route = str(transaction.get("challan_route") or "").upper()
    if not deduction:
        return {"status": "REVIEW_REQUIRED", "reason_code": "DEDUCTION_DATE_REQUIRED", "deposit_due_date": None}
    if deductor_type not in DEDUCTOR_TYPES:
        return {"status": "REVIEW_REQUIRED", "reason_code": "DEDUCTOR_TYPE_REQUIRED", "deposit_due_date": None}
    if deductor_type == "GOVERNMENT_OFFICE" and challan_route not in CHALLAN_ROUTES:
        return {"status": "REVIEW_REQUIRED", "reason_code": "CHALLAN_ROUTE_REQUIRED", "deposit_due_date": None}
    deductee_type = transaction.get("deductee_type")
    def governed(policy: dict) -> bool:
        if policy.get("policy_kind") == "CONTRACTOR_DEPOSIT_DUE_DATE":
            return (
                policy.get("active") is True
                and policy.get("lifecycle") == "ACTIVE"
                and policy.get("source_traceability_status") == "VERIFIED"
                and bool(policy.get("approved_at"))
                and bool(policy.get("approved_by"))
                and bool(policy.get("rule_id"))
            )
        # Compatibility for the pre-existing isolated configuration contract.
        return policy.get("active") and policy.get("lifecycle") == "ACTIVE" and policy.get("approved_configuration") is True and policy.get("source_verified") is True and bool(policy.get("configuration_id"))
    candidates = [p for p in policies if governed(p)
                  and p.get("governing_act") == law and p.get("financial_year") == financial_year and p.get("payment_nature") == "contractor"
                  and (not p.get("deductee_type") or p.get("deductee_type") == deductee_type)
                  and (not section_reference or p.get("section_reference") == section_reference)
                  and p.get("deductor_type") == deductor_type and (p.get("challan_route") or None) == (challan_route or None)
                  and str(p.get("effective_from") or "0000-01-01") <= deduction.isoformat() <= str(p.get("effective_to") or "9999-12-31")]
    if len(candidates) != 1:
        return {"status": "REVIEW_REQUIRED", "reason_code": "DUE_DATE_POLICY_NOT_CONFIGURED" if not candidates else "DUE_DATE_POLICY_AMBIGUOUS", "deposit_due_date": None}
    policy = candidates[0]
    mode = str(policy.get("deadline_mode") or "").upper()
    from datetime import timedelta
    if mode == "DEDUCTION_DATE":
        due = deduction
    elif mode == "MONTH_END_PLUS_DAYS":
        days = policy.get("days_after_month_end")
        if not isinstance(days, int) or days < 0:
            return {"status": "REVIEW_REQUIRED", "reason_code": "DUE_DATE_POLICY_INVALID", "deposit_due_date": None}
        next_month = deduction.replace(day=28) + timedelta(days=4)
        due = next_month - timedelta(days=next_month.day) + timedelta(days=days)
    elif mode == "FIXED_MONTH_DAY":
        month, day = policy.get("deadline_month"), policy.get("deadline_day")
        if not isinstance(month, int) or not isinstance(day, int):
            return {"status": "REVIEW_REQUIRED", "reason_code": "DUE_DATE_POLICY_INVALID", "deposit_due_date": None}
        try:
            year = deduction.year + (1 if month < deduction.month else 0)
            due = date(year, month, day)
        except ValueError:
            return {"status": "REVIEW_REQUIRED", "reason_code": "DUE_DATE_POLICY_INVALID", "deposit_due_date": None}
    else:
        return {"status": "REVIEW_REQUIRED", "reason_code": "DUE_DATE_POLICY_INVALID", "deposit_due_date": None}
    snapshot = deepcopy({key: value for key, value in policy.items() if key != "_id"})
    snapshot.setdefault("configuration_id", policy.get("rule_id"))
    snapshot.setdefault("policy_id", policy.get("rule_id"))
    snapshot.setdefault("policy_version", policy.get("rule_version"))
    return {"status": "DETERMINED", "reason_code": "CONFIGURED_DUE_DATE", "deposit_due_date": due.isoformat(),
            "policy_snapshot": snapshot,
            "branch_inputs": {"deduction_date": deduction.isoformat(), "deductor_type": deductor_type, "challan_route": challan_route or None, "governing_act": law, "financial_year": financial_year, "section_reference": section_reference or None}}


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
        aggregate_excess = str(rule.get("threshold_type") or "").upper() == "AGGREGATE_EXCESS"
        if aggregate_triggered and aggregate_excess:
            # Some provisions expressly apply only to the amount exceeding the
            # annual threshold.  This is source-configured, never inferred.
            subject = cumulative - max(prior, aggregate_fy)
        else:
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
        result = {"transaction_id": transaction.get("transaction_id"), "assignment_id": assignment_id or transaction.get("assignment_id"), "source_reference": transaction.get("source_reference"), "deductee_name": transaction.get("deductee_name"), "deductee_pan": transaction.get("deductee_pan"), "pan_status": transaction.get("pan_status") or pan_status(transaction.get("deductee_pan")), **nature, "financial_year": transaction.get("financial_year"), "deductee_type": transaction.get("deductee_type"), "recipient_residency": transaction.get("recipient_residency"), "recipient_category": transaction.get("recipient_category"), "payer_category": transaction.get("payer_category"), "classification_facts": deepcopy(transaction.get("classification_facts") or []), "governing_act": law.get("act"), "effective_event_date": law.get("effective_event_date"), "current_amount": float(base) if base is not None else None, "calculated_at": None}
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
            review = section_status in {"SECTION_CONFLICT", "RECIPIENT_RESIDENCY_REQUIRED", "CLASSIFICATION_FACT_REQUIRED"}
            reason = ("Section input conflicts with configured rule context." if section_status == "SECTION_CONFLICT"
                      else "Recipient residency evidence is required before selecting a statutory rule." if section_status == "RECIPIENT_RESIDENCY_REQUIRED"
                      else "Verified source-backed classification evidence is required before selecting this statutory rule." if section_status == "CLASSIFICATION_FACT_REQUIRED"
                      else "No active, approved configured rule matched this transaction.")
            result.update({"calculation_status": "REVIEW_REQUIRED" if review else "RULE_NOT_FOUND", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": section_status, "reason": reason, "recommended_action": "Review source classification and configure an authoritative rule; no rate was invented.", "expected_tds": None, "tds_deduction_difference": None})
            results.append(result); continue
        result.update({"rule_id": rule.get("rule_id"), "rule_version": rule.get("rule_version"), "rule_status": rule.get("lifecycle"), "section_reference": rule.get("section_reference"), "table_reference": rule.get("table_reference")})
        # Persist complete rule context with the result, not merely a live ID.
        result["rule_snapshot"] = deepcopy({key: value for key, value in rule.items() if key != "_id"})
        result["pan_evidence"] = pan_evidence_assessment(transaction)
        control_value = transaction.get("contractor_control_contract")
        control_fields = ("payer_eligibility_status", "payer_eligibility_evidence_reference", "contractor_residency_status", "contractor_residency_evidence_reference", "contractor_exception_status", "contractor_exception_type", "contractor_exception_evidence_reference", "contractor_personal_purpose_attestation", "contractor_personal_purpose_payer_type", "contractor_personal_purpose_payer_type_evidence_reference", "goods_carriage_count", "goods_carriage_business_evidence_reference", "goods_carriage_declaration_reference", "goods_carriage_pan_reference", "goods_carriage_particulars_reference", "contractor_invoice_material_status", "contractor_invoice_material_evidence_reference", "pan_operational_status", "pan_evidence_reference", "deductor_type", "challan_route")
        control_supplied = control_value not in (None, "") or any(transaction.get(key) is not None for key in control_fields)
        if control_supplied:
            result["contractor_due_date_context"] = {
                "deduction_date": transaction.get("deduction_date"),
                "deductor_type": transaction.get("deductor_type"),
                "challan_route": transaction.get("challan_route"),
                "governing_act": law.get("act"),
                "financial_year": transaction.get("financial_year"),
                "section_reference": rule.get("section_reference"),
                "payment_nature": nature.get("payment_nature"),
                "deductee_type": transaction.get("deductee_type"),
            }
            applicability = ({"status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_CONTROL_CONTRACT_INVALID", "reason": "Controlled contractor evidence requires the exact supported control contract.", "evidence": {}} if control_value != CONTRACTOR_CONTROL_CONTRACT else assess_contractor_applicability(transaction, rule))
            result["contractor_applicability"] = applicability
            if applicability["status"] == "NOT_APPLICABLE":
                result.update({"calculation_status": "NOT_APPLICABLE", "deduction_status": "NOT_APPLICABLE", "compliance_status": "RULE_EXCEPTION", "reason_code": applicability["reason_code"], "reason": applicability["reason"], "recommended_action": "Retain the controlled evidence in the review/audit record.", "expected_tds": 0.0, "tds_deduction_difference": None})
                results.append(result); continue
            if applicability["status"] != "APPLICABLE":
                result.update({"calculation_status": "REVIEW_REQUIRED", "deduction_status": "NOT_DETERMINABLE", "compliance_status": "REVIEW_REQUIRED", "reason_code": applicability["reason_code"], "reason": applicability["reason"], "recommended_action": "Complete controlled payer and exception evidence; no contractor liability was assumed.", "expected_tds": None, "tds_deduction_difference": None})
                results.append(result); continue
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
    candidates = [p for p in policies if p.get("active") is True and p.get("status") in {"APPROVED", "ACTIVE"} and (not p.get("financial_years") or financial_year in p.get("financial_years", [])) and (not event_date or str(p.get("effective_from", "0000-01-01")) <= event_date <= str(p.get("effective_to", "9999-12-31")))]
    if not candidates: return None, "PHASE5_POLICY_NOT_FOUND"
    priority = max(int(p.get("priority", 0)) for p in candidates); winners = [p for p in candidates if int(p.get("priority", 0)) == priority]
    return (deepcopy(winners[0]), None) if len(winners) == 1 else (None, "PHASE5_POLICY_AMBIGUOUS")


def calculate_deposit_compliance(phase3_results: list[dict], evidence_rows: list[dict], phase4_results: list[dict] | None = None, *, assignment_id: str, calculation_id: str, evidence_version_id: str | None = None, policies: list[dict] | None = None, due_date_policies: list[dict] | None = None, relationships: list[dict] | None = None) -> list[dict]:
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
        contractor_control = liability.get("contractor_applicability")
        if contractor_control and contractor_control.get("status") != "APPLICABLE":
            base.update({"overall_status": "REVIEW_REQUIRED", "reason_code": "CONTRACTOR_APPLICABILITY_NOT_ESTABLISHED", "reason": "Deposit compliance cannot bypass the controlled contractor applicability decision.", "contractor_applicability": deepcopy(contractor_control)})
            results.append(base); continue
        if contractor_control:
            base["contractor_applicability"] = deepcopy(contractor_control)
        due_date_context = liability.get("contractor_due_date_context")
        if due_date_policies is not None and due_date_context:
            determination = determine_configured_contractor_deposit_deadline(
                {**due_date_context, "deduction_date": liability.get("actual_deduction_date") or due_date_context.get("deduction_date")},
                due_date_policies,
            )
            base.update({
                "contractor_due_date_status": determination["status"],
                "contractor_due_date_reason_code": determination["reason_code"],
                "due_date_policy_snapshot": determination.get("policy_snapshot"),
                "due_date_policy_branch_inputs": determination.get("branch_inputs"),
                "deposit_due_date": determination.get("deposit_due_date"),
            })
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
        difference = deposited - expected
        due = _day(base.get("deposit_due_date")) if due_date_policies is not None and due_date_context else _day(phase4_by_tx.get(tx, {}).get("deposit_due_date"))
        dates = [_day(e.get("deposit_date")) for e in matches]
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
        due_snapshot = deposit.get("due_date_policy_snapshot")
        due_error = _frozen_due_date_snapshot_error(deposit)
        if not due_error:
            due_date = deposit.get("deposit_due_date")
            result["due_date_policy_snapshot"] = deepcopy(due_snapshot)
            result["due_date_policy_branch_inputs"] = deepcopy(deposit.get("due_date_policy_branch_inputs"))
        else:
            due_date = None
        result["deposit_due_date"] = due_date
        if due_error:
            result.update({"overall_status": "POLICY_NOT_CONFIGURED", "reason": "A valid frozen Phase 4 due-date policy decision is unavailable for this deposit result.", "explanation": "Phase 5 did not derive or replace the governed Rule 218 deadline."})
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
        # Section 398(3)(a)(ii) measures deposit-delay interest from the
        # deduction/collection date through actual payment.  The governed due
        # date remains the control that decides whether a payment is late; it
        # is not the statutory start of the interest period.
        deposit_component = _interest_component(
            deposit_rule,
            actual_deduction,
            actual_deposit if actual_deposit >= actual_deduction else actual_deduction,
            interest_phase2,
        )
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
        # The API lifecycle rejects these records before activation.  Keep the
        # domain selector equally fail-closed so a malformed imported/test
        # record cannot become calculation-eligible by bypassing that route.
        if governed_activation_errors(rule):
            continue
        # Rule API records use governing_act; imported historical records may
        # use governing_law or act.  They express the same persisted scope.
        if (rule.get("governing_law") or rule.get("governing_act") or rule.get("act")) != law or not event_date:
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


def _frozen_due_date_snapshot_error(deposit: dict) -> str | None:
    """Validate the persisted Phase 4 decision without consulting live policy."""
    if deposit.get("contractor_due_date_status") != "DETERMINED":
        return deposit.get("contractor_due_date_reason_code") or "DUE_DATE_POLICY_NOT_CONFIGURED"
    if not _day(deposit.get("deposit_due_date")):
        return "DUE_DATE_POLICY_SNAPSHOT_INVALID"
    snapshot = deposit.get("due_date_policy_snapshot")
    required = ("rule_id", "rule_version", "approved_at", "approved_by")
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("policy_kind") != "CONTRACTOR_DEPOSIT_DUE_DATE"
        or snapshot.get("active") is not True
        or snapshot.get("lifecycle") != "ACTIVE"
        or snapshot.get("source_traceability_status") != "VERIFIED"
        or any(not snapshot.get(field) for field in required)
        or not isinstance(deposit.get("due_date_policy_branch_inputs"), dict)
    ):
        return "DUE_DATE_POLICY_SNAPSHOT_INVALID"
    return None


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
        # This predecessor preview path has no persisted Phase 4 deposit
        # result, so it cannot prove which governed Rule 218 branch applied.
        # The persisted /interest-compliance workflow is the sole path that
        # consumes the immutable Phase 4 due-date-policy snapshot.
        result.update({
            "overall_status": "POLICY_NOT_CONFIGURED",
            "reason": "A frozen Phase 4 due-date policy snapshot is required for deposit-delay interest.",
            "explanation": "Use the persisted interest-compliance workflow after Phase 4 deposit compliance; no live deadline is derived from an interest policy.",
        })
        results.append(result)
    return results
