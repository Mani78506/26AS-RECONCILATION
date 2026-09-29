"""Deterministic expected-TDS calculation.

This module deliberately contains no default statutory rates.  A rate can only
be used when it is supplied by the application's configured rule list.  It is
therefore safe to use for reconciliation and 26AS-only analysis without
turning a reported 26AS deduction into an assumed Books expectation.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from .tds_rules import resolve_rule


TWOPLACES = Decimal("0.01")


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _in_range(value, start, end):
    if not value:
        return True
    try:
        current = date.fromisoformat(str(value)[:10])
        return (not start or current >= date.fromisoformat(start)) and (not end or current <= date.fromisoformat(end))
    except ValueError:
        return False


def find_rule(transaction: dict, rules: list[dict]) -> dict | None:
    return resolve_rule(transaction, rules)


def calculate_expected_tds(transaction: dict, rules: list[dict], *, base_field: str) -> dict:
    """Calculate only with an explicit base, section and configured rule.

    Returned decimals are converted to floats at the persistence/API boundary;
    all arithmetic here stays Decimal.
    """
    explicit = _decimal(transaction.get("tds_expected"))
    if explicit is not None:
        return {"tds_expected": explicit, "tds_rate": None, "tds_calculation_method": "BOOKS_REPORTED", "tds_calculation_status": "AVAILABLE", "tds_calculation_basis": "Books TDS column", "tds_calculation_reason": "Books supplied an explicit TDS amount.", "tds_rule_id": None}
    base = _decimal(transaction.get(base_field))
    if base is None:
        return _unavailable("INSUFFICIENT_DATA", f"A reliable TDS calculation base ({base_field}) is unavailable.")
    if not transaction.get("section"):
        return _unavailable("INSUFFICIENT_DATA", "TDS section is unavailable.")
    if not transaction.get("financial_year") or not (transaction.get("document_date") or transaction.get("transaction_date")):
        return _unavailable("INSUFFICIENT_DATA", "Transaction date or financial year is unavailable.")
    rule = find_rule(transaction, rules)
    if not rule:
        return _unavailable("RULE_UNAVAILABLE", f"No configured TDS rule is available for section {transaction['section']} in FY {transaction['financial_year']}.")
    rate = _decimal(rule.get("rate"))
    if rate is None:
        return _unavailable("RULE_UNAVAILABLE", f"Configured rule {rule.get('rule_id') or rule.get('section')} has no rate.")
    if rule.get("calculation_base") and rule["calculation_base"] not in {"BOOKS_TDS_BASE" if base_field == "tds_base_amount" else "AMOUNT_PAID_CREDITED", "CUSTOM_BASE"}:
        return _unavailable("INSUFFICIENT_DATA", f"Configured rule requires {rule['calculation_base']}, but {base_field} was supplied.")
    threshold = _decimal(rule.get("threshold"))
    if rule.get("threshold_type", "NO_THRESHOLD") not in ("NO_THRESHOLD", "PER_TRANSACTION"):
        return _unavailable("INSUFFICIENT_DATA", f"Threshold type {rule.get('threshold_type')} requires aggregate data that is not available per transaction.")
    if threshold is not None and base < threshold:
        expected = Decimal("0.00")
    else:
        expected = (base * rate / Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return {"tds_expected": expected, "tds_rate": rate, "tds_calculation_method": "RULE_CALCULATED", "tds_calculation_status": "AVAILABLE", "tds_calculation_basis": f"{base_field}: {base}", "tds_calculation_reason": f"{base} × {rate}% using configured rule {rule.get('rule_id') or rule.get('section')}.", "tds_rule_id": rule.get("rule_id"), "tds_rule_version": rule.get("version"), "tds_rule_source": rule.get("source"), "tds_rule_effective_from": rule.get("effective_from"), "tds_rule_effective_to": rule.get("effective_to")}


def _unavailable(status, reason):
    return {"tds_expected": None, "tds_rate": None, "tds_calculation_method": "NOT_CALCULATED", "tds_calculation_status": status, "tds_calculation_basis": None, "tds_calculation_reason": reason, "tds_rule_id": None}


def public_calculation(result: dict) -> dict:
    """Convert Decimal metadata into BSON/JSON-safe values."""
    return {key: (float(value) if isinstance(value, Decimal) else value) for key, value in result.items()}
