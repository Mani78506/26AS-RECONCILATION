"""Phase 3A parser for government tax-credit *summary* evidence only."""
from __future__ import annotations

from io import BytesIO
import re
from typing import Any

import pandas as pd

from ..shared.parser import ParseError, parse_amount
from ..shared.schema import header_key

SOURCE_TYPE = "GOVERNMENT_TAX_CREDIT_SUMMARY"
FIELDS = ("sr_no", "deductor_name", "deductor_tan", "amount_paid_credited", "tax_deducted", "tds_deposited")
_HEADERS = {
    "sr_no": {"sr_no", "sr_no_", "serial_no", "serial_number", "s_no", "sno"},
    "deductor_name": {"name_of_deductor", "deductor_name"},
    "deductor_tan": {"tan_of_deductor", "deductor_tan"},
    "amount_paid_credited": {"total_amount_paid_credited"},
    "tax_deducted": {"total_tax_deducted"},
    "tds_deposited": {"total_tds_deposited"},
}


def _cell(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    value = value.strip() if isinstance(value, str) else value
    return value if value != "" else None


def _mapping(values: list[Any]) -> dict[int, str]:
    lookup = {alias: field for field, aliases in _HEADERS.items() for alias in aliases}
    return {index: lookup[key] for index, value in enumerate(values) if (key := header_key(value)) in lookup}


def _tables(filename: str, content: bytes):
    lower = filename.lower()
    try:
        if lower.endswith(".csv"):
            yield "CSV", pd.read_csv(BytesIO(content), header=None, dtype=object, keep_default_na=False)
        elif lower.endswith((".xlsx", ".xls")):
            workbook = pd.ExcelFile(BytesIO(content))
            for sheet in workbook.sheet_names:
                yield sheet, pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
        else:
            raise ParseError("Government Tax Credit Summary supports CSV, XLSX and XLS tables.")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("The government summary file could not be read as a spreadsheet.") from exc


def validate_government_tax_credit_summary(filename: str, content: bytes, assignment: dict | None = None) -> dict:
    try:
        candidates = []
        all_sheets = []
        for sheet, raw in _tables(filename, content):
            all_sheets.append(sheet)
            for index in range(min(len(raw.index), 30)):
                mapping = _mapping(raw.iloc[index].tolist())
                candidates.append((len(mapping), len(raw.index) - index, sheet, raw, index, mapping))
        if not candidates:
            raise ParseError("No readable table was found.")
        score, _, sheet, raw, header_row, mapping = max(candidates, key=lambda item: (item[0], item[1]))
    except ParseError as exc:
        return {"file": {"original_filename": filename, "source_type": SOURCE_TYPE}, "headers": [], "canonical_mappings": {}, "unmapped_columns": [], "rows": [], "summary": {"total_rows": 0, "valid_rows": 0, "warning_rows": 0, "invalid_rows": 0, "duplicate_rows": 0}, "validation_messages": [str(exc)], "can_commit": False}
    headers = [str(v) for v in raw.iloc[header_row].tolist() if _cell(v) is not None]
    canonical_mappings = {str(raw.iloc[header_row, index]): field for index, field in mapping.items()}
    missing = [field for field in FIELDS if field not in mapping.values()]
    unmapped = [str(v) for index, v in enumerate(raw.iloc[header_row].tolist()) if _cell(v) is not None and index not in mapping]
    rows, duplicate_keys = [], {}
    for row_index in range(header_row + 1, len(raw.index)):
        values = raw.iloc[row_index].tolist()
        source_values = {str(raw.iloc[header_row, index]): _cell(values[index]) if index < len(values) else None for index in range(len(raw.iloc[header_row])) if _cell(raw.iloc[header_row, index]) is not None}
        if not any(value is not None for value in source_values.values()):
            continue
        canonical = {field: _cell(values[index]) if index < len(values) else None for index, field in mapping.items()}
        issues = []
        if canonical.get("sr_no") is not None and not re.fullmatch(r"\d+(?:\.0+)?", str(canonical["sr_no"]).strip()): issues.append("Sr. No. is invalid.")
        if not canonical.get("deductor_name"): issues.append("Deductor name is not provided.")
        tan = str(canonical.get("deductor_tan") or "").strip().upper() or None
        canonical["deductor_tan"] = tan
        if tan and not re.fullmatch(r"[A-Z]{4}[0-9]{5}[A-Z]", tan): issues.append("TAN format is invalid.")
        for field in ("amount_paid_credited", "tax_deducted", "tds_deposited"):
            supplied = canonical.get(field)
            value = parse_amount(supplied) if supplied is not None else None
            canonical[field] = value
            if supplied is not None and value is None: issues.append(f"{field} is not numeric.")
        row = {"source_row_number": row_index + 1, **canonical, "source_values": source_values, "canonical_values": dict(canonical), "validation_messages": issues, "provenance": {"original_filename": filename, "source_sheet": sheet, "source_row_number": row_index + 1, "source_type": SOURCE_TYPE}}
        if assignment: row.update({key: assignment.get(key) for key in ("assignment_id", "organization_id", "client_id")})
        key = tuple(str(canonical.get(field) or "") for field in FIELDS)
        if any(key): duplicate_keys.setdefault(key, []).append(row)
        rows.append(row)
    for repeated in duplicate_keys.values():
        if len(repeated) > 1:
            for row in repeated: row["validation_messages"].append("Duplicate source summary row retained for CA review.")
    for row in rows:
        msgs = row["validation_messages"]
        row["row_status"] = "INVALID" if any("invalid" in msg.lower() or "not numeric" in msg.lower() or "not provided" in msg.lower() for msg in msgs) else "WARNING" if msgs else "VALID"
    counts = {state: sum(row["row_status"] == state for row in rows) for state in ("VALID", "WARNING", "INVALID")}
    return {"file": {"original_filename": filename, "source_type": SOURCE_TYPE, "source_sheet": sheet, "header_row": header_row + 1, "workbook_sheets": all_sheets, "financial_year": None, "quarter": None}, "headers": headers, "canonical_mappings": canonical_mappings, "unmapped_columns": unmapped, "rows": rows, "summary": {"total_rows": len(rows), "valid_rows": counts["VALID"], "warning_rows": counts["WARNING"], "invalid_rows": counts["INVALID"], "duplicate_rows": sum("Duplicate source summary row retained for CA review." in row["validation_messages"] for row in rows)}, "validation_messages": [f"Missing required header: {field}." for field in missing], "can_commit": bool(rows) and not missing and not counts["INVALID"]}
