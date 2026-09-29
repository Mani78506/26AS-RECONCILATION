"""Source-aware readers for deductor-side CA payment-ledger exports.

These profiles are deliberately local to TDS Compliance.  They do not change
the generic upload parser or any assessee-side reconciliation source.
"""
from __future__ import annotations

from typing import Any
from io import BytesIO
import re

import pandas as pd

from ..shared.parser import ParseError, parse_amount
from ..shared.schema import header_key


TDS_PAYABLE_GL = "TDS_PAYABLE_GL"
VENDOR_LEDGER = "VENDOR_LEDGER"
CA_TDS_WORKING_REFERENCE = "CA_TDS_WORKING_REFERENCE"

_GL_HEADERS = {
    "posting_date": "transaction_date", "document_no": "transaction_id",
    "cheque_no": "cheque_number", "offsetting_account": "offsetting_account",
    "offsetting_description": "source_counterparty_description", "narration": "description",
    "debit_amount": "source_debit_amount", "credit_amount": "source_credit_amount",
    "closing_balance": "closing_balance", "project_name": "project_name", "cr_dr": "cr_dr",
    "gstin": "deductee_gstin", "invoice_number": "invoice_number", "invoice_date": "invoice_date",
    "document_type": "document_type", "payee_name": "source_payee_name",
    "purchase_order_number": "purchase_order_number",
}

_VENDOR_HEADERS = {
    "vendor_code": "vendor_code", "vendor_name": "deductee_name", "party_name": "deductee_name",
    "pan": "deductee_pan", "gstin": "deductee_gstin", "msme": "msme",
    "invoice_number": "invoice_number", "invoice_no": "invoice_number", "invoice_date": "invoice_date",
    "posting_date": "transaction_date", "document_no": "transaction_id", "document_number": "transaction_id",
    "amount": "amount", "transaction_amount": "amount", "gross_amount": "amount",
    "narration": "description", "description": "description", "tds_deducted": "tds_deducted",
    "tds_amount": "tds_deducted", "offsetting_account": "offsetting_account",
    "offsetting_description": "source_counterparty_description", "document_type": "document_type",
}


def _value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        value = value.strip()
    return value if value != "" else None


def _metadata(raw: pd.DataFrame, before_row: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    labels = {"company_name": "company", "gl_code": "tds_payable_account", "gl_description": "gl_description", "period": "source_period"}
    for row in raw.iloc[:before_row].itertuples(index=False, name=None):
        if len(row) < 2:
            continue
        label = header_key(row[0])
        if label in labels and _value(row[1]) is not None:
            values[labels[label]] = _value(row[1])
    return values


def _header_candidate(values: list[Any], mappings: dict[str, str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for index, value in enumerate(values):
        canonical = mappings.get(header_key(value))
        if canonical:
            result[index] = canonical
    return result


def _is_ignored(values: list[Any]) -> bool:
    text = " ".join(str(v).strip().lower() for v in values if _value(v) is not None)
    if not text:
        return True
    return bool(re.search(r"\b(opening balance|closing balance|grand total|subtotal|total)\b", text))


def _is_tds_payment(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("document_type", "description", "source_counterparty_description")).lower()
    return "tds payment" in text or bool(re.search(r"\btds\s+paid\b", text))


def _is_bank_counterparty(value: Any) -> bool:
    text = str(value or "").lower()
    return "bank" in text or "cash" in text


def _read_workbook(filename: str, content: bytes) -> pd.ExcelFile:
    try:
        return pd.ExcelFile(BytesIO(content))
    except Exception as exc:
        raise ParseError("The file could not be read as an Excel workbook.") from exc


def _profile_gl(workbook: pd.ExcelFile, filename: str) -> dict | None:
    candidates = []
    for sheet in workbook.sheet_names:
        raw = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
        for row_index in range(len(raw.index)):
            mapping = _header_candidate(raw.iloc[row_index].tolist(), _GL_HEADERS)
            required = {"transaction_date", "source_debit_amount", "source_credit_amount"}
            if required <= set(mapping.values()):
                score = len(mapping) + (5 if "document_type" in mapping.values() else 0)
                candidates.append((score, len(raw.index) - row_index, sheet, raw, row_index, mapping))
    if not candidates:
        return None
    _, _, sheet, raw, header_row, mapping = max(candidates, key=lambda item: (item[0], item[1]))
    meta = _metadata(raw, header_row)
    # A generic bank/cash workbook can have these headers.  A GL profile is
    # accepted only when its metadata makes the TDS payable account explicit.
    if "tds payable" not in str(meta.get("gl_description") or "").lower() and not re.search(r"(?:^|_)2041", str(meta.get("tds_payable_account") or "")):
        return None
    rows, ignored = [], 0
    for source_index in range(header_row + 1, len(raw.index)):
        values = raw.iloc[source_index].tolist()
        if _is_ignored(values):
            ignored += 1
            continue
        record = {field: _value(values[index]) if index < len(values) else None for index, field in mapping.items()}
        if not any(record.values()):
            ignored += 1
            continue
        record.update(meta)
        record["source_row_number"] = source_index + 1
        record["source_sheet"] = sheet
        record["source_file_name"] = filename
        record["source_category"] = TDS_PAYABLE_GL
        record["source_profile"] = TDS_PAYABLE_GL
        record["source_transaction_kind"] = "TDS_PAYMENT" if _is_tds_payment(record) else "TDS_PAYABLE_GL_ENTRY"
        debit, credit = parse_amount(record.get("source_debit_amount")), parse_amount(record.get("source_credit_amount"))
        # A debit/credit here is an entry in a TDS-payable account. It is kept
        # as source evidence only; it never becomes the gross vendor payment.
        record["source_debit_amount"], record["source_credit_amount"] = debit, credit
        if record["source_transaction_kind"] == "TDS_PAYMENT" and debit not in (None, 0):
            record["tds_deposited"] = debit
        elif credit not in (None, 0):
            record["tds_deducted"] = credit
        counterparty = record.get("source_payee_name") or record.get("source_counterparty_description")
        if not _is_bank_counterparty(counterparty):
            record["deductee_name"] = counterparty
        rows.append(record)
    return {"source_profile": TDS_PAYABLE_GL, "source_category": TDS_PAYABLE_GL, "sheet_name": sheet,
            "header_row": header_row + 1, "workbook_sheets": list(workbook.sheet_names), "metadata": meta,
            "rows": rows, "ignored_rows": ignored, "mapped_headers": sorted(set(mapping.values())),
            "unmapped_headers": [str(v) for v in raw.iloc[header_row].tolist() if _value(v) and header_key(v) not in _GL_HEADERS]}


def _profile_vendor(workbook: pd.ExcelFile, filename: str) -> dict | None:
    candidates = []
    for sheet in workbook.sheet_names:
        raw = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
        for row_index in range(min(25, len(raw.index))):
            mapping = _header_candidate(raw.iloc[row_index].tolist(), _VENDOR_HEADERS)
            if {"deductee_name", "amount"} <= set(mapping.values()) or {"vendor_code", "amount"} <= set(mapping.values()):
                candidates.append((len(mapping), len(raw.index) - row_index, sheet, raw, row_index, mapping))
    if not candidates:
        return None
    _, _, sheet, raw, header_row, mapping = max(candidates, key=lambda item: (item[0], item[1]))
    rows, ignored = [], 0
    for source_index in range(header_row + 1, len(raw.index)):
        values = raw.iloc[source_index].tolist()
        if _is_ignored(values):
            ignored += 1
            continue
        record = {field: _value(values[index]) if index < len(values) else None for index, field in mapping.items()}
        if not any(record.values()):
            ignored += 1
            continue
        record.update({"source_row_number": source_index + 1, "source_sheet": sheet, "source_file_name": filename,
                       "source_category": VENDOR_LEDGER, "source_profile": VENDOR_LEDGER,
                       "source_transaction_kind": "VENDOR_LEDGER_ENTRY"})
        rows.append(record)
    return {"source_profile": VENDOR_LEDGER, "source_category": VENDOR_LEDGER, "sheet_name": sheet,
            "header_row": header_row + 1, "workbook_sheets": list(workbook.sheet_names), "metadata": {},
            "rows": rows, "ignored_rows": ignored, "mapped_headers": sorted(set(mapping.values())),
            "unmapped_headers": [str(v) for v in raw.iloc[header_row].tolist() if _value(v) and header_key(v) not in _VENDOR_HEADERS]}


def inspect_tds_source(filename: str, content: bytes) -> dict | None:
    """Return an isolated source profile, or None for the ordinary template."""
    if not filename.lower().endswith((".xlsx", ".xls")):
        return None
    workbook = _read_workbook(filename, content)
    if re.search(r"acpl.*tds.*working|tds.*working", filename, flags=re.I):
        return {"source_profile": CA_TDS_WORKING_REFERENCE, "source_category": CA_TDS_WORKING_REFERENCE,
                "sheet_name": None, "header_row": None, "workbook_sheets": list(workbook.sheet_names), "metadata": {},
                "rows": [], "ignored_rows": 0, "mapped_headers": [], "unmapped_headers": [],
                "reference_only": True}
    return _profile_gl(workbook, filename) or _profile_vendor(workbook, filename)
