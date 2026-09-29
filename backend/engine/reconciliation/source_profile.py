"""Additive source-understanding profile; does not alter normalized values or validation."""
from __future__ import annotations

from ..shared.schema import header_key

_ACCOUNTING = {"debit": "DEBIT_AMOUNT", "debit_amount": "DEBIT_AMOUNT", "dr": "DEBIT_AMOUNT", "credit": "CREDIT_AMOUNT", "credit_amount": "CREDIT_AMOUNT", "cr": "CREDIT_AMOUNT"}
_TDS_WORDS = ("tds", "withholding", "tax_withheld", "tax_deducted", "tds_payable", "tds_receivable", "tds_liability")


def source_profile(headers: list[object], mapped: dict[str, str], unmapped: list[str]) -> dict:
    """Describe source structure and conservative semantic evidence.

    A debit/credit column is deliberately only an accounting direction. It is
    never mapped to a TDS amount by this profiler.
    """
    fields, warnings = [], []
    for header in headers:
        label = str(header)
        key = header_key(label)
        canonical = mapped.get(label)
        semantic = _ACCOUNTING.get(key)
        if canonical:
            fields.append({"source_column": label, "canonical_field": canonical, "mapping_method": "CANONICAL_HEADER" if key == canonical else "CANONICAL_ALIAS", "semantic_field": semantic or canonical.upper(), "confidence": "HIGH", "status": "DETECTED"})
        elif key:
            fields.append({"source_column": label, "canonical_field": None, "semantic_field": semantic, "confidence": "NOT_DETERMINABLE" if not semantic else "HIGH", "status": "IGNORED" if not semantic else "DETECTED"})
    tds_headers = [str(h) for h in headers if any(word in header_key(h) for word in _TDS_WORDS)]
    if tds_headers:
        warnings.append({"code": "TDS_ROLE_REVIEW_REQUIRED", "message": "TDS-labelled source fields need accounting-context confirmation before they are treated as a books TDS amount.", "source_columns": tds_headers})
    elif any(_ACCOUNTING.get(header_key(h)) for h in headers):
        warnings.append({"code": "TDS_ROLE_NOT_DETERMINABLE", "message": "Debit and credit amounts were detected, but no TDS accounting amount was inferred.", "source_columns": []})
    return {"columns": fields, "ignored_columns": unmapped, "accounting_context": "REVIEW_REQUIRED" if warnings else "NOT_APPLICABLE", "warnings": warnings}
