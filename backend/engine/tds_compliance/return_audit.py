from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

RETURN_AUDIT_SCHEMA_VERSION = "6A.1"
MAX_ZIP_EXTRACT_BYTES = 25 * 1024 * 1024
RETURN_FORMS = {"26Q", "27Q", "FORM_140", "FORM_144"}
ARTIFACT_TYPES = {"RETURN_SOURCE", "FVU", "RETURN_PACKAGE", "FILING_EVIDENCE", "FORM_27A"}


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def detect_return_form(text: str, filename: str = "") -> str | None:
    source = f"{filename} {text[:8000]}".upper()
    for pattern, form in ((r"\bFORM\s*140\b", "FORM_140"), (r"\bFORM\s*144\b", "FORM_144"), (r"\b26Q\b", "26Q"), (r"\b27Q\b", "27Q")):
        if re.search(pattern, source): return form
    return None


def _clean(value): return str(value or "").strip()
def _number(value):
    try: return float(Decimal(_clean(value).replace(",", "")))
    except (InvalidOperation, ValueError): return None

def _header_key(value): return re.sub(r"[^a-z0-9]", "", _clean(value).lower())
ALIASES = {
    "transaction_id": {"transactionid", "transactionreference", "reference", "voucher", "vouchernumber"},
    "payment_reference": {"paymentreference", "paymentid", "paymentnumber", "paymentidentifier"},
    "invoice_number": {"invoicenumber", "invoiceno", "invoice"},
    "document_identifier": {"documentidentifier", "documentid", "documentnumber", "documentreference"},
    "deductee_pan": {"deducteepan", "pan"},
    "deductee_name": {"deducteename", "deducteename", "name"},
    "amount_paid": {"amountpaid", "paymentamount", "amount"},
    "tds_amount": {"tdsamount", "taxdeducted", "taxdeductedandpaid", "tax"},
    "section": {"section", "sectioncode"},
    "challan_number": {"challannumber", "challan", "cin"},
    "payment_date": {"paymentdate", "creditdate", "paymentorcreditdate"},
}

class OfficialProteanParser:
    """Versioned, metadata-led parser for the published Protean source grammar."""
    parser_name = "protean-caret"
    parser_version = "1.0"
    expected_counts = {"FH": 18, "BH": 72, "CD": None, "DD": None}

    def detect(self, lines: list[list[str]]) -> str | None:
        headers = [fields for fields in lines if len(fields) > 4 and fields[1] == "BH"]
        return headers[0][4].upper() if headers else None

    def validate_structure(self, lines: list[list[str]], form: str) -> list[str]:
        errors=[]; expected={"FH":18,"BH":72,"CD":30 if form in {"FORM_140","FORM_144"} else 41,"DD":45 if form in {"FORM_140","FORM_144"} else 54}
        for number, fields in enumerate(lines, 1):
            kind=fields[1] if len(fields)>1 else ""
            if kind in expected and len(fields) != expected[kind]: errors.append(f"Line {number} {kind} has {len(fields)} fields; official {form} format requires {expected[kind]}.")
            elif not kind: errors.append(f"Line {number} has no record type.")
        return errors

    @staticmethod
    def _v(fields, index): return fields[index] if len(fields) > index and fields[index] != "" else None
    @staticmethod
    def _amount(value):
        if value is None: return None
        try: return float(Decimal(value))
        except (InvalidOperation, ValueError): return None
    @staticmethod
    def _date(value):
        if value is None: return None
        try: return datetime.strptime(value, "%d%m%Y").date().isoformat()
        except ValueError: return None
    @staticmethod
    def _fy(value):
        return f"{value[:4]}-{value[4:]}" if value and re.fullmatch(r"\d{6}",value) else None

    def canonicalize(self, lines: list[list[str]], form: str, filename: str) -> dict:
        legacy=form in {"26Q","27Q"}; records=[]; rows=[]; challans=[]; metadata={}
        form_name={"26Q":"26Q","27Q":"27Q","FORM_140":"FORM_140","FORM_144":"FORM_144"}[form]
        for line_no, fields in enumerate(lines,1):
            kind=fields[1] if len(fields)>1 else ""
            category={"FH":"HEADER","BH":"DEDUCTOR","CD":"CHALLAN","DD":"DEDUCTEE"}.get(kind,"UNKNOWN")
            base={"record_category":category,"record_type":kind or None,"source_line_number":line_no,"raw_record":"^".join(fields),"raw_fields":fields,"parser_name":self.parser_name,"parser_version":self.parser_version,"return_form":form_name,"format_version":self.parser_version,"source_filename":filename}
            records.append(base)
            if kind=="FH":
                metadata.update({"statement_type":"REGULAR" if self._v(fields,3)=="R" else "CORRECTION" if self._v(fields,3)=="C" else None,"source_creation_date":self._v(fields,4),"deductor_tan":self._v(fields,7),"return_preparation_utility":self._v(fields,9)})
            elif kind=="BH":
                # These indexes are shared by the published legacy and current BH layouts.
                metadata.update({"deductor_tan":self._v(fields,12),"deductor_pan":self._v(fields,14),"source_fy_or_tax_year":self._v(fields,16),"normalized_financial_year":self._fy(self._v(fields,16)),"tax_year":self._v(fields,15),"quarter":self._v(fields,17),"deductor_name":self._v(fields,18),"original_return_reference":self._v(fields,7),"correction_reference":self._v(fields,8)})
            elif kind=="CD":
                if legacy: mapped={"challan_reference_number":self._v(fields,3),"challan_serial_number":self._v(fields,11),"bsr_code":self._v(fields,15),"challan_deposit_date":self._date(self._v(fields,17)),"challan_amount":self._amount(self._v(fields,26)),"total_tax_allocated":self._amount(self._v(fields,28)),"total_interest_allocated":self._amount(self._v(fields,33))}
                else: mapped={"challan_reference_number":self._v(fields,3),"challan_serial_number":self._v(fields,16),"bsr_code":self._v(fields,14),"challan_deposit_date":self._date(self._v(fields,18)),"challan_amount":self._amount(self._v(fields,11)),"total_tax_allocated":self._amount(self._v(fields,20)),"total_interest_allocated":self._amount(self._v(fields,23))}
                challans.append({**base,**mapped})
            elif kind=="DD":
                if legacy: mapped={"deductee_reference_number":self._v(fields,11),"deductee_code":self._v(fields,7),"deductee_pan_or_aadhaar":self._v(fields,9),"deductee_name":self._v(fields,12),"tax_amount":self._amount(self._v(fields,13)),"surcharge":self._amount(self._v(fields,14)),"cess":self._amount(self._v(fields,15)),"total_tax_deducted":self._amount(self._v(fields,16)),"total_tax_deposited":self._amount(self._v(fields,18)),"amount_paid_or_credited":self._amount(self._v(fields,21)),"payment_or_credit_date":self._date(self._v(fields,22)),"deduction_date":self._date(self._v(fields,23)),"deduction_rate":self._amount(self._v(fields,25)),"reason_for_non_or_lower_or_higher_deduction":self._v(fields,29),"section":self._v(fields,32),"certificate_number":self._v(fields,33)}
                else: mapped={"deductee_reference_number":self._v(fields,3),"deductee_code":self._v(fields,5),"deductee_pan_or_aadhaar":self._v(fields,7),"deductee_name":self._v(fields,8),"section":self._v(fields,14),"payment_or_credit_date":self._date(self._v(fields,18)),"amount_paid_or_credited":self._amount(self._v(fields,19)),"tax_amount":self._amount(self._v(fields,23)),"total_tax_deducted":self._amount(self._v(fields,23)),"total_tax_deposited":self._amount(self._v(fields,24)),"deduction_date":self._date(self._v(fields,26)),"deduction_rate":self._amount(self._v(fields,27)),"reason_for_non_or_lower_or_higher_deduction":self._v(fields,31),"certificate_number":self._v(fields,32)}
                if form in {"27Q","FORM_144"}: mapped.update({"applicable_rate_indicator":self._v(fields,34 if legacy else 29),"nature_of_remittance":self._v(fields,35 if legacy else 15),"remittance_country":self._v(fields,37 if legacy else 16),"tin":self._v(fields,41 if legacy else 40)})
                rows.append({**base,**mapped,"return_transaction_id":mapped["deductee_reference_number"],"deductee_pan":mapped["deductee_pan_or_aadhaar"],"tds_amount":mapped["total_tax_deducted"],"source_row_number":line_no})
        return {"metadata":metadata,"records":records,"return_rows":rows,"challans":challans}

    def parse(self, text: str, filename: str) -> dict | None:
        # split preserves every empty official field, including a trailing empty field.
        lines=[line.split("^") for line in text.splitlines() if line != ""]
        form=self.detect(lines)
        normal={"26Q":"26Q","27Q":"27Q","140":"FORM_140","144":"FORM_144"}
        if form not in normal: return None
        form=normal[form]; errors=self.validate_structure(lines,form); parsed=self.canonicalize(lines,form,filename)
        status="VALID" if not errors else "PARSE_ERROR"
        return {"artifact_type":"RETURN_SOURCE","parser_status":status,"parser_message":"Official Protean caret-delimited source parsed." if not errors else " ".join(errors),"source_form":form,"source_version":self.parser_version,"statement_type":parsed["metadata"].get("statement_type"),"return_rows":parsed["return_rows"],"challans":parsed["challans"],"filing_evidence":[],"archive_entries":[],"metadata":parsed["metadata"],"records":parsed["records"]}

OFFICIAL_PARSERS={form:OfficialProteanParser() for form in ("26Q","27Q","FORM_140","FORM_144")}

def _official_parse(content: bytes, filename: str):
    try: text=content.decode("ascii")
    except UnicodeDecodeError: return None
    return OfficialProteanParser().parse(text,filename)


def parse_return_artifact(content: bytes, filename: str, declared_type: str | None = None) -> dict:
    lower = filename.lower(); artifact_type=(declared_type or ("RETURN_PACKAGE" if lower.endswith(".zip") else "FILING_EVIDENCE" if lower.endswith(".pdf") else "FVU" if lower.endswith(".fvu") else "RETURN_SOURCE")).upper()
    if artifact_type not in ARTIFACT_TYPES: raise ValueError("Unsupported return artifact type.")
    report={"artifact_type":artifact_type,"parser_status":"REVIEW_REQUIRED","parser_message":"The source was retained, but no deterministic return-row parser is available for this format.","source_form":None,"source_version":None,"statement_type":None,"return_rows":[],"challans":[],"filing_evidence":[],"archive_entries":[],"metadata":{},"records":[]}
    if lower.endswith(".pdf") or content.startswith(b"%PDF"):
        report.update({"artifact_type":"FORM_27A" if "27a" in lower else "FILING_EVIDENCE","parser_status":"VALID","parser_message":"PDF retained as filing evidence; it is not treated as a return source."}); report["filing_evidence"]=[{"evidence_type":report["artifact_type"],"filename":filename,"status":"PRESENT"}]; return report
    if lower.endswith(".zip") or content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members=[item for item in archive.infolist() if not item.is_dir()]
                if len(members)>50 or sum(item.file_size for item in members)>MAX_ZIP_EXTRACT_BYTES: raise ValueError("Archive exceeds Return Audit extraction limits.")
                names=[item.filename for item in members]
                if any(name.startswith("/") or ".." in name.replace("\\","/").split("/") or name.lower().endswith(".zip") for name in names): raise ValueError("Archive contains an unsafe or nested member.")
                report["archive_entries"]=names
                candidates=[item for item in members if item.filename.lower().endswith((".txt",".csv"))]
                if len(candidates)!=1: report["parser_message"]="Archive inventory retained; exactly one deterministic return source is required."; return report
                nested=parse_return_artifact(archive.read(candidates[0]),candidates[0].filename,"RETURN_SOURCE"); nested["artifact_type"]="RETURN_PACKAGE"; nested["archive_entries"]=names; return nested
        except (zipfile.BadZipFile,ValueError) as exc: report.update({"parser_status":"PARSE_ERROR","parser_message":str(exc)}); return report
    official=_official_parse(content,filename)
    if official: return official
    # Existing non-Protean structured audit extracts remain supported.
    try: text=content.decode("utf-8-sig")
    except UnicodeDecodeError: return report
    csv_text=text
    csv_lines=text.splitlines()
    if csv_lines and ("FORM" in csv_lines[0].upper() or "REGULAR" in csv_lines[0].upper() or "CORRECTION" in csv_lines[0].upper()): csv_text="\n".join(csv_lines[1:])
    try:
        dialect=csv.Sniffer().sniff(csv_text[:8192], delimiters=",\t|;")
        reader=csv.DictReader(io.StringIO(csv_text), dialect=dialect); headers=reader.fieldnames or []
    except csv.Error: return report
    mapped={canonical:next((header for header in headers if _header_key(header) in aliases),None) for canonical,aliases in ALIASES.items()}
    if not mapped["transaction_id"] or not mapped["deductee_pan"] or not mapped["tds_amount"]: return report
    rows=[]
    for number, source in enumerate(reader,2):
        transaction_id,pan,tax=_clean(source.get(mapped["transaction_id"])),_clean(source.get(mapped["deductee_pan"])).upper(),_number(source.get(mapped["tds_amount"]))
        if not transaction_id or not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]",pan) or tax is None: continue
        rows.append({"source_row_number":number,"return_transaction_id":transaction_id,"deductee_pan":pan,"tds_amount":tax,"raw_source":source,
                     "payment_reference": _clean(source.get(mapped["payment_reference"])) or None,
                     "invoice_number": _clean(source.get(mapped["invoice_number"])) or None,
                     "document_identifier": _clean(source.get(mapped["document_identifier"])) or None,
                     "deductee_name": _clean(source.get(mapped["deductee_name"])) or None,
                     "section": _clean(source.get(mapped["section"])) or None,
                     "amount_paid_or_credited": _number(source.get(mapped["amount_paid"])),
                     "payment_or_credit_date": _clean(source.get(mapped["payment_date"])) or None,
                     "challan_number": _clean(source.get(mapped["challan_number"])) or None})
    report.update({"parser_status":"VALID" if rows else "REVIEW_REQUIRED","parser_message":"Structured return rows parsed without statutory recalculation." if rows else "No valid rows found in structured source.","source_form":detect_return_form(text,filename),"return_rows":rows})
    return report


def reconcile_return_rows(return_rows: list[dict], calculation_rows: list[dict], ledger_rows: list[dict]) -> list[dict]:
    books = {str(row.get("transaction_id") or ""): row for row in calculation_rows if row.get("transaction_id")}
    ledger = {str(row.get("transaction_id") or ""): row for row in ledger_rows if row.get("transaction_id")}
    results = []
    matched = set()
    for row in return_rows:
        key = str(row.get("return_transaction_id") or "")
        calculation = books.get(key); ledger_row = ledger.get(key)
        result = {"return_transaction_id": key, "return_source_row_number": row.get("source_row_number"), "deductee_pan": row.get("deductee_pan"), "return_tds_amount": row.get("tds_amount"), "calculation_id": calculation.get("calculation_id") if calculation else None, "ledger_version_id": ledger_row.get("ledger_version_id") if ledger_row else None, "match_method": "EXACT_TRANSACTION_REFERENCE" if calculation else None}
        if not calculation:
            result.update({"status": "MISSING_IN_BOOKS", "reason": "No frozen calculation row has the same explicit transaction reference."})
        elif str(calculation.get("deductee_pan") or "").upper() != str(row.get("deductee_pan") or "").upper():
            result.update({"status": "REVIEW_REQUIRED", "reason": "Transaction reference matched but deductee PAN differs."}); matched.add(key)
        else:
            expected = calculation.get("expected_tds")
            if expected is None: result.update({"status": "REVIEW_REQUIRED", "reason": "Frozen calculation has no determinate expected TDS."})
            elif abs(float(expected) - float(row.get("tds_amount") or 0)) > .01: result.update({"status": "TDS_AMOUNT_DIFFERENCE", "reason": "Return TDS differs from frozen calculation snapshot.", "expected_tds": expected})
            else: result.update({"status": "MATCHED", "reason": "Exact transaction reference, PAN, and TDS amount agree.", "expected_tds": expected})
            matched.add(key)
        results.append(result)
    for key, calculation in books.items():
        if key not in matched:
            results.append({"return_transaction_id": None, "transaction_id": key, "deductee_pan": calculation.get("deductee_pan"), "expected_tds": calculation.get("expected_tds"), "status": "MISSING_IN_RETURN", "reason": "No return row has the same explicit transaction reference.", "calculation_id": calculation.get("calculation_id"), "ledger_version_id": calculation.get("ledger_version_id"), "match_method": None})
    return results
