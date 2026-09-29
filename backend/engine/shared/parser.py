import csv
import io
import re
from datetime import date, datetime

import pandas as pd
import pdfplumber
from pypdf import PdfReader

from .schema import BOOKS, FORM26AS, PAYMENT_LEDGER, SALES_REGISTRY, TDS_RECEIVABLE, TAN_RE, column_lookup, header_key, required_columns

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xls", ".pdf")
# ERP exports commonly contain title, parameter and balance rows before the
# actual ledger table. This remains bounded so malformed workbooks cannot make
# header detection unbounded.
MAX_HEADER_SCAN_ROWS = 500
OLE_XLS_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_XLSX_SIGNATURE = b"PK\x03\x04"


class ParseError(Exception):
    pass


def _clean_cell(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).replace("\ufeff", "")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", text).strip()


def _meaningful(value):
    return bool(re.sub(r"[\s\x00-\x1f\x7f]", "", _clean_cell(value)))


def _read_csv_raw(content: bytes) -> pd.DataFrame:
    if not any(_meaningful(chr(byte)) for byte in content):
        raise ParseError("The CSV contains no readable header or transaction data.")
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not any(_meaningful(char) for char in text):
            continue
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        try:
            rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(_meaningful(value) for value in row)]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            return pd.DataFrame([row + [""] * (width - len(row)) for row in rows])
        except (csv.Error, ValueError):
            continue
    raise ParseError("The CSV file could not be decoded or has no readable tabular data.")


def _contextual_column(key: str, kind: str):
    """Conservative fallbacks for common accounting/report labels not covered by exact aliases."""
    words = set(key.split("_"))
    if kind == BOOKS:
        if {"tds", "receivable"}.issubset(words) or key.startswith("tds_receivable"):
            return "tds_expected"
        if key in {"particular", "particulars", "ledger_account"}:
            return "customer_name"
        if "voucher" in words and ("no" in words or "number" in words):
            return "transaction_id"
    if kind == TDS_RECEIVABLE:
        if {"tds", "receivable"}.issubset(words): return "tds_expected"
        if key in {"particular", "particulars", "ledger_account"}: return "party_name"
    if kind == PAYMENT_LEDGER:
        # Bank and ERP payment exports use these labels.  Keep debit/credit
        # separate until ledger validation chooses the populated source value.
        if key == "document_no": return "transaction_id"
        if key == "posting_date": return "payment_date"
        if key == "payee_name": return "source_payee_name"
        if key == "offsetting_description": return "source_counterparty_description"
        if key == "debit_amount": return "source_debit_amount"
        if key == "credit_amount": return "source_credit_amount"
    if kind == SALES_REGISTRY:
        if "sales" in words and "amount" in words: return "sales_amount"
        if "invoice" in words and ("date" in words or key == "invoice_date"): return "invoice_date"
    if kind == FORM26AS:
        if "financial_year" in key or key in {"fy", "financial_yr"}:
            return "financial_year"
        if "tan" in words and ("deductor" in words or len(words) <= 3):
            return "tan"
        if "deductor" in words and ("name" in words or key == "deductor"):
            return "deductor_name"
        if "date" in words and ("payment" in words or "credit" in words or "transaction" in words):
            return "transaction_date"
        if "tds" in words and "deposited" in words:
            return "tds_deposited"
        if ("tax" in words or "tds" in words) and "deducted" in words:
            return "tax_deducted"
    return None


def _header_mapping(headers, kind: str):
    lookup = column_lookup(kind)
    candidates, unmapped = [], []
    for position, header in enumerate(headers):
        key = header_key(header)
        canonical = _contextual_column(key, kind) or lookup.get(key)
        if canonical:
            candidates.append((position, key, canonical))
        elif key:
            unmapped.append(_clean_cell(header))
    # An exact canonical header (for example Invoice Number) wins over a
    # broader alias encountered earlier (for example Document no).
    mapping = {}
    chosen = set()
    for position, key, canonical in sorted(candidates, key=lambda value: (value[1] != value[2], value[0])):
        if canonical not in chosen:
            mapping[position] = canonical
            chosen.add(canonical)
    # Exact canonical headers decide conflicts, but the public mapped-column
    # list must retain the source layout. This keeps previews and 26AS block
    # reports deterministic and readable.
    return dict(sorted(mapping.items())), unmapped


def _header_score(values, kind: str):
    mapping, _ = _header_mapping(values, kind)
    required = set(required_columns(kind))
    recognised = set(mapping.values())
    return len(recognised) + 3 * len(recognised & required), mapping


def _detect_source_type(headers):
    keys = {header_key(value) for value in headers if header_key(value)}
    has_tan = any("tan" in key for key in keys)
    has_deductor = any("deductor" in key for key in keys)
    has_tds_deducted = any(("tds" in key or "tax" in key) and "deducted" in key for key in keys)
    has_tds_receivable = any("tds" in key and "receivable" in key for key in keys)
    has_ledger_context = bool({"particulars", "voucher_no", "voucher_type", "buyer_supplier"} & keys)
    has_gst_invoice = bool({"recipients_name", "recipient_name", "invoice_no", "invoice_number", "invoice_value", "taxable_value", "gstin"} & keys) and bool({"invoice_no", "invoice_number", "invoice_value", "taxable_value"} & keys)
    if has_tan and has_deductor and has_tds_deducted:
        return "Government 26AS / Form 16A"
    if has_tds_receivable or has_ledger_context:
        return "ERP TDS Receivable / Books Report"
    if has_gst_invoice:
        return "Sales Registry / GST Invoice Register"
    return "Unclassified tabular report"


def _select_table(raw: pd.DataFrame, kind: str, sheet_name: str | None = None):
    best_score, best_row, best_mapping = -1, -1, {}
    alternate_kind = FORM26AS if kind in (BOOKS, TDS_RECEIVABLE, SALES_REGISTRY) else BOOKS
    best_alternate_score, best_alternate_row = -1, -1
    for row_index in range(min(len(raw.index), MAX_HEADER_SCAN_ROWS)):
        score, mapping = _header_score(raw.iloc[row_index].tolist(), kind)
        if score > best_score:
            best_score, best_row, best_mapping = score, row_index, mapping
        alternate_score, _ = _header_score(raw.iloc[row_index].tolist(), alternate_kind)
        if alternate_score > best_alternate_score:
            best_alternate_score, best_alternate_row = alternate_score, row_index
    header_detected = best_score > 0
    if not header_detected:
        # Select a strong header for the other source type when present. This
        # makes a 26AS uploaded in the Books slot diagnosable, rather than
        # presenting a title row as a mysterious empty workbook.
        best_row = best_alternate_row if best_alternate_score > 0 else next((i for i in range(len(raw.index)) if any(_meaningful(v) for v in raw.iloc[i].tolist())), 0)
    headers = [_clean_cell(value) or f"Unnamed: {index}" for index, value in enumerate(raw.iloc[best_row].tolist())]
    table = raw.iloc[best_row + 1:].copy()
    table.columns = headers
    table = table.dropna(how="all")
    table.attrs.update({
        "sheet_name": sheet_name, "header_row": best_row + 1, "header_score": best_score,
        "header_detected": header_detected, "non_empty_rows": sum(any(_meaningful(v) for v in raw.iloc[i].tolist()) for i in range(len(raw.index))),
        "alternate_header_score": best_alternate_score,
        "detected_file_type": _detect_source_type(headers),
    })
    return table


def _financial_year_from_report_period(value) -> str | None:
    """Return FY context from an explicit report period, never a line date."""
    text = _clean_cell(value)
    match = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+to\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", text, re.I)
    if not match:
        return None
    start = parse_date(match.group(1))
    if not start:
        return None
    year = start.year if start.month >= 4 else start.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def _read_tds_receivable_group_summary(content: bytes) -> pd.DataFrame | None:
    """Recognise a multi-row accounting group summary without inventing vouchers."""
    workbook, engine_name = _open_workbook(content)
    for sheet in workbook.sheet_names:
        raw = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
        particulars_row = next((i for i in range(len(raw.index)) if any(header_key(v) in {"particulars", "particular"} for v in raw.iloc[i].tolist())), None)
        if particulars_row is None or particulars_row + 2 >= len(raw.index):
            continue
        child = raw.iloc[particulars_row + 2].tolist()
        debit_index = next((i for i, v in enumerate(child) if header_key(v) in {"debit", "dr"}), None)
        credit_index = next((i for i, v in enumerate(child) if header_key(v) in {"credit", "cr"}), None)
        if debit_index is None or credit_index is None:
            continue
        particulars_index = next(i for i, v in enumerate(raw.iloc[particulars_row].tolist()) if header_key(v) in {"particulars", "particular"})
        balance_indexes = [i for i, v in enumerate(child) if header_key(v) == "balance"]
        opening_index = next((i for i in balance_indexes if i < debit_index), None)
        closing_index = next((i for i in balance_indexes if i > credit_index), None)
        heading_values = raw.iloc[:particulars_row].to_numpy().flatten()
        period = next((_clean_cell(v) for v in heading_values if _financial_year_from_report_period(v)), "")
        financial_year = _financial_year_from_report_period(period)
        if "tds" not in " ".join(_clean_cell(v) for v in raw.iloc[:particulars_row + 3].to_numpy().flatten()).lower():
            continue
        records, semantic_rows, control_totals = [], [], {}
        for row_index in range(particulars_row + 3, len(raw.index)):
            values = raw.iloc[row_index].tolist()
            label = _clean_cell(values[particulars_index] if particulars_index < len(values) else "")
            if not label:
                continue
            source_row, label_key = row_index + 1, header_key(label)
            debit = parse_amount(values[debit_index] if debit_index < len(values) else "")
            credit = parse_amount(values[credit_index] if credit_index < len(values) else "")
            opening = parse_amount(values[opening_index] if opening_index is not None and opening_index < len(values) else "")
            closing = parse_amount(values[closing_index] if closing_index is not None and closing_index < len(values) else "")
            if label_key in {"grand_total", "total", "subtotal"}:
                control_totals = {"source_debit_total": debit, "source_credit_total": credit, "source_row": source_row, "row_type": "GRAND_TOTAL"}
                semantic_rows.append({"row_no": source_row, "accounting_label": label, "row_type": "GRAND_TOTAL", "accounting_role": "NON_TRANSACTION_ROW", "debit_amount": debit, "credit_amount": credit, "opening_balance": opening, "closing_balance": closing})
                continue
            words = label_key.replace("_", " ")
            receivable = bool(re.match(r"^tds\s+rec(?:ei|ie)vable\b", words, re.I))
            payable = bool(re.match(r"^tds\s+payable\b", words, re.I))
            role = "TDS_RECEIVABLE" if receivable else "TDS_PAYABLE" if payable else "NON_TDS_ACCOUNT"
            party = re.sub(r"^tds\s+rec(?:ei|ie)vable\s+from\s+", "", label, flags=re.I).strip() if receivable else ""
            direction = "DEBIT" if receivable and debit is not None and debit > 0 else "CREDIT" if receivable and credit is not None and credit > 0 else None
            eligible = role == "TDS_RECEIVABLE" and direction == "DEBIT"
            semantic = {"row_no": source_row, "accounting_label": label, "party_label": party or None, "row_type": "ACCOUNT_GROUP", "accounting_role": role, "accounting_direction": direction, "debit_amount": debit, "credit_amount": credit, "opening_balance": opening, "closing_balance": closing, "tds_book_amount": debit if eligible else None, "status": "VALID" if eligible else "REVIEW_REQUIRED" if role == "TDS_RECEIVABLE" else "NOT_APPLICABLE", "confidence": "HIGH" if role == "TDS_RECEIVABLE" else "NOT_DETERMINABLE", "evidence": "Explicit TDS Receivable account label + debit movement." if eligible else "Credit-only TDS Receivable movement requires accounting review." if role == "TDS_RECEIVABLE" and direction == "CREDIT" else "Source account is not a TDS Receivable debit movement.", "source_granularity": "ACCOUNT_GROUP_SUMMARY"}
            semantic_rows.append(semantic)
            if role == "TDS_RECEIVABLE":
                records.append({"party_name": party or label, "tds_expected": debit if eligible else "", "financial_year": financial_year or "", "row_no": source_row, **semantic})
        if not records and not control_totals:
            continue
        table = pd.DataFrame(records, columns=["party_name", "tds_expected", "financial_year"])
        table.attrs.update({"sheet_name": sheet, "workbook_sheets": list(workbook.sheet_names), "workbook_engine": engine_name, "header_row": particulars_row + 1, "header_score": 5, "header_detected": True, "non_empty_rows": sum(any(_meaningful(v) for v in raw.iloc[i].tolist()) for i in range(len(raw.index))), "detected_file_type": "ERP TDS Receivable Group Summary", "report_type": "TDS_RECEIVABLE_GROUP_SUMMARY", "source_granularity": "ACCOUNT_GROUP_SUMMARY", "report_period": period, "financial_year_context": financial_year, "header_structure": {"type": "MULTI_ROW_ACCOUNTING_HEADER", "raw_header_rows": [particulars_row + 1, particulars_row + 2, particulars_row + 3], "columns": [{"raw_header": "Particulars", "canonical_field": "ACCOUNT_DESCRIPTION"}, {"raw_header": "Opening", "child_header": "Balance", "canonical_field": "OPENING_BALANCE"}, {"raw_header": "Transactions", "child_header": "Debit", "canonical_field": "DEBIT_AMOUNT"}, {"raw_header": "Transactions", "child_header": "Credit", "canonical_field": "CREDIT_AMOUNT"}, {"raw_header": "Closing", "child_header": "Balance", "canonical_field": "CLOSING_BALANCE"}]}, "ledger_raw_rows": records, "ledger_semantic_rows": semantic_rows, "control_totals": control_totals})
        return table
    return None


def _open_workbook(content: bytes):
    """Open Excel bytes with an engine appropriate for the actual container.

    Real legacy .xls files are OLE/BIFF containers and require xlrd.  Modern
    .xlsx files are ZIP containers and are read by openpyxl.  Looking at the
    magic bytes (instead of trusting a filename) also supports ERP exports
    whose extension was changed by a browser or mail client.
    """
    stream = io.BytesIO(content)
    if content.startswith(OLE_XLS_SIGNATURE):
        try:
            return pd.ExcelFile(stream, engine="xlrd"), "xlrd (.xls)"
        except ImportError as exc:
            raise ParseError("Legacy .xls support is unavailable because the xlrd reader is not installed.") from exc
        except Exception as exc:
            raise ParseError("The legacy .xls workbook is unreadable or corrupt.") from exc
    if content.startswith(ZIP_XLSX_SIGNATURE):
        try:
            return pd.ExcelFile(stream, engine="openpyxl"), "openpyxl (.xlsx)"
        except Exception as exc:
            raise ParseError("The .xlsx workbook is unreadable or corrupt.") from exc
    try:
        return pd.ExcelFile(stream), "auto-detected Excel"
    except Exception as exc:
        raise ParseError("The file is not a readable Excel workbook. It may be corrupt or an unsupported spreadsheet format.") from exc


def _read_pdf_table(content: bytes, kind: str) -> pd.DataFrame:
    if kind != FORM26AS:
        raise ParseError("PDF upload is supported only for 26AS / Form 16A statements.")
    candidates, has_text = [], False
    try:
        # pypdf extracts portal text dramatically faster than geometric table
        # analysis. Use it first for the standard repeating 26AS layout.
        portal_records = _read_form26as_portal_pdf(PdfReader(io.BytesIO(content)).pages)
        if portal_records:
            table = pd.DataFrame(portal_records)
            table.attrs.update({"sheet_name": "PDF text", "header_row": 0, "header_score": 8, "header_detected": True, "detected_file_type": "Government 26AS / Form 16A", "parser": "26AS portal text blocks"})
            return table
        with pdfplumber.open(io.BytesIO(content)) as document:
            for page_number, page in enumerate(document.pages, start=1):
                has_text = has_text or bool((page.extract_text() or "").strip())
                tables = page.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"})
                if not tables:
                    tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text", "min_words_vertical": 2, "min_words_horizontal": 1})
                for table in tables:
                    raw = pd.DataFrame(table).dropna(how="all")
                    if not raw.empty:
                        candidates.append(_select_table(raw, kind, f"Page {page_number}"))
    except Exception as exc:
        raise ParseError("The PDF could not be read. Upload an unprotected, text-based 26AS or Form 16A PDF.") from exc
    if not has_text:
        raise ParseError("PDF appears to be scanned/image-based. OCR is required to extract 26AS transaction data. Upload the original text-based 26AS/Form 16A PDF or an Excel/CSV export.")
    if not candidates:
        raise ParseError("The PDF contains text but no extractable transaction table. Export the 26AS/Form 16A as Excel/CSV or use the original text-based PDF.")
    return max(candidates, key=lambda table: (table.attrs.get("header_score", -1), len(table.index)))


def _read_form26as_portal_pdf(document) -> list[dict]:
    """Extract the ordinary portal PDF layout without geometric table scans."""
    records, current_tan, current_name = [], "", ""
    tan_pattern = re.compile(r"^\s*\d+\s+(.+?)\s+([A-Z]{4}\d{5}[A-Z])\s+[\d,]+(?:\.\d+)?\s+[\d,]+(?:\.\d+)?\s+[\d,]+(?:\.\d+)?\s*$")
    # Some portal PDFs wrap a deductor summary immediately before the TAN.
    # Keep this deliberately narrow: only a numbered summary line ending in a
    # continuation marker may supply the name for a following TAN-and-totals
    # line. Transaction rows never contain a TAN in that position.
    wrapped_summary_pattern = re.compile(r"^\s*\d+\s+(.+?)\s+-\s*$")
    wrapped_tan_pattern = re.compile(r"^\s*([A-Z]{4}\d{5}[A-Z])\s+[\d,]+(?:\.\d+)?\s+[\d,]+(?:\.\d+)?\s+[\d,]+(?:\.\d+)?\s*$")
    detail_pattern = re.compile(r"^\s*\d+\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+([A-Za-z]+)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+.*?\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s*$")
    for page in getattr(document, "pages", document):
        extract_simple = getattr(page, "extract_text_simple", None)
        text = (extract_simple() if extract_simple else page.extract_text()) or ""
        pending_name = ""
        for line in text.splitlines():
            summary = tan_pattern.match(line)
            if summary:
                current_name, current_tan = summary.group(1).strip(), summary.group(2)
                pending_name = ""
                continue
            wrapped_summary = wrapped_summary_pattern.match(line)
            if wrapped_summary:
                pending_name = wrapped_summary.group(1).strip()
                continue
            wrapped_tan = wrapped_tan_pattern.match(line)
            if wrapped_tan and pending_name:
                current_name, current_tan = pending_name, wrapped_tan.group(1)
                pending_name = ""
                continue
            if pending_name and re.match(r"^\s*[A-Z][A-Z &'().-]*\s*$", line):
                pending_name = f"{pending_name} {line.strip()}"
                continue
            detail = detail_pattern.match(line)
            if detail and current_tan:
                section, transaction_date, status, _booking_date, amount_paid, tax_deducted, tds_deposited = detail.groups()
                records.append({"tan": current_tan, "deductor_name": current_name, "section": section, "transaction_date": transaction_date, "status": status, "amount_paid": amount_paid, "tax_deducted": tax_deducted, "tds_deposited": tds_deposited})
    return records


def _read_form26as_block_workbook(content: bytes) -> pd.DataFrame | None:
    """Read the repeating deductor-summary/detail blocks used by portal 26AS Excel exports.

    A deductor's name and TAN appear in a summary row, while its transaction date
    and TDS values appear under a separate header immediately below it.  Keeping
    that context is necessary; the generic rectangular-table reader cannot map
    the two headers as one table.
    """
    workbook, _ = _open_workbook(content)
    records, source_sheets = [], []
    fields = ("tan", "deductor_name", "transaction_date", "tax_deducted", "tds_deposited", "status", "section", "amount_paid")

    for sheet in workbook.sheet_names:
        raw = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, keep_default_na=False)
        current_tan = current_name = ""
        detail_positions = {}
        found_rows = False

        for row_index in range(len(raw.index)):
            values = raw.iloc[row_index].tolist()
            keys = [header_key(value) for value in values]
            summary_name = next((i for i, key in enumerate(keys) if key in {"name_of_deductor", "deductor_name"}), None)
            summary_tan = next((i for i, key in enumerate(keys) if key in {"tan_of_deductor", "deductor_tan"}), None)
            if summary_name is not None and summary_tan is not None:
                detail_positions = {}
                continue

            if summary_tan is not None or ("tan_of_deductor" in keys and "name_of_deductor" in keys):
                continue

            # A summary data row is recognised by its real TAN, never by position alone.
            tan_value = next((normalize for normalize in (_clean_cell(value).upper() for value in values) if TAN_RE.fullmatch(normalize)), "")
            name_candidate = next((
                _clean_cell(value) for value in values
                if _clean_cell(value) and not TAN_RE.fullmatch(_clean_cell(value).upper()) and not parse_date(value) and parse_amount(value) is None
            ), "")
            if tan_value and name_candidate:
                current_tan, current_name = tan_value, name_candidate
                detail_positions = {}
                continue

            mapping, _ = _header_mapping(values, FORM26AS)
            if "transaction_date" in mapping.values() and "tax_deducted" in mapping.values():
                detail_positions = mapping
                continue

            if not detail_positions or not current_tan:
                continue
            date_index = next((index for index, field in detail_positions.items() if field == "transaction_date"), None)
            tax_index = next((index for index, field in detail_positions.items() if field == "tax_deducted"), None)
            if date_index is None or tax_index is None or date_index >= len(values) or tax_index >= len(values):
                continue
            if not parse_date(values[date_index]) or parse_amount(values[tax_index]) is None:
                continue

            record = {"tan": current_tan, "deductor_name": current_name}
            for index, field in detail_positions.items():
                if index < len(values) and field in fields:
                    record[field] = _clean(values[index])
            records.append(record)
            found_rows = True
        if found_rows:
            source_sheets.append(sheet)

    if not records:
        return None
    table = pd.DataFrame(records)
    table.attrs.update({
        "sheet_name": ", ".join(source_sheets), "header_row": 0, "header_score": len(fields),
        "detected_file_type": "Government 26AS / Form 16A",
        "parser": "26AS repeating deductor/detail blocks",
    })
    return table


def _sheet_row_count(workbook, sheet_name: str) -> int:
    """Get a sheet's declared size without materialising its cells."""
    try:
        sheet = workbook.book[sheet_name]
        return int(getattr(sheet, "max_row", 0) or 0)
    except (AttributeError, KeyError, TypeError):
        try:
            return int(workbook.book.sheet_by_name(sheet_name).nrows)
        except (AttributeError, KeyError, TypeError):
            return 0


def _read_best_excel_sheet(workbook, kind: str) -> pd.DataFrame:
    """Read a bounded preview of each sheet, then materialise only the winner.

    Header detection is explicitly bounded at ``MAX_HEADER_SCAN_ROWS``. The
    previous implementation decoded every cell in every worksheet merely to
    select a sheet, which made large multi-sheet ERP workbooks needlessly
    slow. Sheet dimensions preserve the existing score/size tie-breaker.
    """
    candidates = []
    for position, sheet in enumerate(workbook.sheet_names):
        preview = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object,
                                keep_default_na=False, nrows=MAX_HEADER_SCAN_ROWS)
        if preview.empty:
            continue
        selected_preview = _select_table(preview, kind, sheet)
        candidates.append((selected_preview.attrs.get("header_score", -1), _sheet_row_count(workbook, sheet), -position, sheet))
    if not candidates:
        raise ParseError("The workbook opened successfully but contains no non-empty sheets.")
    _, _, _, selected_sheet = max(candidates)
    raw = pd.read_excel(workbook, sheet_name=selected_sheet, header=None, dtype=object, keep_default_na=False)
    return _select_table(raw, kind, selected_sheet)


def read_table(filename: str, content: bytes, kind: str, inspection: bool = False) -> pd.DataFrame:
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            return _select_table(_read_csv_raw(content), kind)
        if name.endswith(".pdf"):
            return _read_pdf_table(content, kind)
        if name.endswith((".xlsx", ".xls")):
            # The specialised parsers are required for later validation and
            # reconciliation, but source ingestion needs only structural
            # metadata. Skipping them here avoids an additional full-workbook
            # pass; validation still uses the same established parser.
            if kind == FORM26AS and not inspection:
                block_table = _read_form26as_block_workbook(content)
                if block_table is not None:
                    return block_table
            if kind == TDS_RECEIVABLE and not inspection:
                group_summary = _read_tds_receivable_group_summary(content)
                if group_summary is not None:
                    return group_summary
            workbook, engine_name = _open_workbook(content)
            selected = _read_best_excel_sheet(workbook, kind)
            selected.attrs["workbook_engine"] = engine_name
            selected.attrs["workbook_sheets"] = list(workbook.sheet_names)
            return selected
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("The file could not be read as a spreadsheet. Check that it is a valid CSV or Excel file.") from exc
    raise ParseError("Unsupported file type. Upload a CSV, XLSX or XLS file.")


def map_columns(df: pd.DataFrame, kind: str):
    positional, unmapped = _header_mapping(df.columns, kind)
    return {df.columns[position]: canonical for position, canonical in positional.items()}, unmapped


def _is_footer_or_repeated(rec, headers):
    values = [_clean_cell(value) for value in rec.values()]
    nonblank = [value for value in values if value]
    if not nonblank:
        return True
    if header_key(nonblank[0]) in {"total", "grand_total", "subtotal", "closing_balance", "opening_balance"}:
        return True
    row_keys = {header_key(value) for value in nonblank}
    header_keys = {header_key(header) for header in headers if header_key(header)}
    return len(row_keys & header_keys) >= 3


def parse_rows(df: pd.DataFrame, kind: str):
    mapping, unmapped = map_columns(df, kind)
    renamed = df.rename(columns=mapping)
    rows = []
    first_data_row = int(df.attrs.get("header_row", 1)) + 1
    # ``to_dict('records')`` eagerly creates a second Python object graph for
    # every source row (142k dicts for a large Books export). Iterate the
    # selected canonical columns directly so canonical materialisation remains
    # exactly once and preserves the parser's existing row semantics.
    canonical = list(mapping.values())
    values = renamed.loc[:, canonical].itertuples(index=False, name=None)
    headers = list(df.columns)
    for offset, values_row in enumerate(values):
        rec = dict(zip(canonical, values_row))
        if _is_footer_or_repeated(rec, headers):
            continue
        row = {field: _clean(rec.get(field)) for field in canonical}
        if all(value in ("", None) for value in row.values()):
            continue
        row["row_no"] = first_data_row + offset
        rows.append(row)
    return rows, list(mapping.values()), unmapped


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    return _clean_cell(value)


def parse_amount(value):
    if value in ("", None):
        return None
    text = re.sub(r"[₹â‚¹,\s]", "", str(value)).strip()
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if text.endswith("-"):
        neg, text = True, text[:-1]
    try:
        num = float(text)
    except ValueError:
        return None
    return round(-num if neg else num, 2)


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S")


def parse_date(value):
    if value in ("", None):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.date() if isinstance(value, datetime) else value
    text = str(value).strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        try:
            return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(text))).date()
        except Exception:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text, fmt).date()
        except ValueError:
            continue
    try:
        timestamp = pd.to_datetime(text, dayfirst=True)
        return None if pd.isna(timestamp) else timestamp.date()
    except Exception:
        return None
