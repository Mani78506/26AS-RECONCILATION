"""Versioned configured-rule validation and deterministic selection.

No statutory rules live here.  The repository stores only rules supplied by an
authorised administrator together with their source evidence.
"""
from datetime import date
from decimal import Decimal
import re

BASES = {"AMOUNT_PAID_CREDITED", "BOOKS_TDS_BASE", "CUSTOM_BASE"}
THRESHOLD_TYPES = {"NO_THRESHOLD", "PER_TRANSACTION", "AGGREGATE", "ANNUAL"}
FY_RE = re.compile(r"^\d{4}-\d{2}$")


class RuleError(ValueError):
    pass


def _date(value, field):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise RuleError(f"{field} must be YYYY-MM-DD.") from exc


def validate_rule(rule: dict, existing: list[dict] | None = None, *, exclude_id: str | None = None) -> dict:
    item = dict(rule or {})
    item["section"] = str(item.get("section") or "").strip().upper()
    if not item["section"]:
        raise RuleError("Section is required.")
    if not FY_RE.fullmatch(str(item.get("financial_year") or "")):
        raise RuleError("Financial year must use YYYY-YY format.")
    try:
        item["rate"] = str(Decimal(str(item["rate"])))
    except Exception as exc:
        raise RuleError("Rate must be numeric.") from exc
    if Decimal(item["rate"]) < 0:
        raise RuleError("Rate cannot be negative.")
    threshold = item.get("threshold")
    if threshold not in (None, ""):
        try:
            item["threshold"] = str(Decimal(str(threshold)))
        except Exception as exc:
            raise RuleError("Threshold must be numeric.") from exc
        if Decimal(item["threshold"]) < 0:
            raise RuleError("Threshold cannot be negative.")
    else:
        item["threshold"] = None
    item["threshold_type"] = str(item.get("threshold_type") or "NO_THRESHOLD").upper()
    if item["threshold_type"] not in THRESHOLD_TYPES:
        raise RuleError("Unsupported threshold type.")
    item["calculation_base"] = str(item.get("calculation_base") or "").upper()
    if item["calculation_base"] not in BASES:
        raise RuleError("Unsupported calculation base.")
    start, end = _date(item.get("effective_from"), "effective_from"), _date(item.get("effective_to"), "effective_to")
    if not start or not end or start > end:
        raise RuleError("effective_from and effective_to are required and must form a valid range.")
    item["effective_from"], item["effective_to"] = start.isoformat(), end.isoformat()
    item["version"] = int(item.get("version") or 1)
    if item["version"] < 1:
        raise RuleError("Version must be at least 1.")
    if not str(item.get("source") or "").strip() or not str(item.get("source_reference") or "").strip():
        raise RuleError("Source and source_reference are required for configured rules.")
    item["is_active"] = bool(item.get("is_active", True))
    item["conditions"] = item.get("conditions") or {}
    if item["is_active"]:
        for other in existing or []:
            if other.get("rule_id") == exclude_id or not other.get("is_active", True):
                continue
            if other.get("section") == item["section"] and other.get("financial_year") == item["financial_year"]:
                if item["effective_from"] <= other.get("effective_to", "") and other.get("effective_from", "") <= item["effective_to"]:
                    raise RuleError("Active rules for the same section and FY cannot have overlapping effective dates.")
    return item


def resolve_rule(transaction: dict, rules: list[dict]) -> dict | None:
    fy, section = str(transaction.get("financial_year") or ""), str(transaction.get("section") or "").upper()
    when = transaction.get("document_date") or transaction.get("transaction_date")
    try:
        day = date.fromisoformat(str(when)[:10])
    except ValueError:
        return None
    candidates = [r for r in rules if r.get("is_active", True) and r.get("section") == section and r.get("financial_year") == fy and r.get("effective_from") <= day.isoformat() <= r.get("effective_to")]
    return max(candidates, key=lambda r: int(r.get("version") or 1), default=None)
