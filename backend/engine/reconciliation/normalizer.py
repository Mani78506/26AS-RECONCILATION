import re
from datetime import date

from ..shared.parser import parse_amount, parse_date
from ..shared.schema import BOOKS, CUSTOMER_MASTER, FORM26AS, SALES_REGISTRY, TDS_RECEIVABLE
from .tds_calculator import calculate_expected_tds, public_calculation

_SUFFIXES = {"PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "INC", "CO", "COMPANY", "CORP", "CORPORATION", "THE", "PLC", "P", "L", "ENTERPRISES", "ENTERPRISE"}


def normalize_name(name: str) -> str:
    text = str(name or "").upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t]
    return " ".join(tokens)


def core_name(name: str) -> str:
    tokens = [t for t in normalize_name(name).split() if t not in _SUFFIXES]
    return " ".join(tokens) or normalize_name(name)


def normalize_code(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def financial_year_of(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def quarter_of(d: date) -> str:
    return {4: "Q1", 5: "Q1", 6: "Q1", 7: "Q2", 8: "Q2", 9: "Q2", 10: "Q3", 11: "Q3", 12: "Q3", 1: "Q4", 2: "Q4", 3: "Q4"}[d.month]


def normalize_fy(value: str) -> str:
    text = str(value or "").strip().upper().replace("FY", "").replace(" ", "")
    m = re.fullmatch(r"(\d{4})[-/](\d{2,4})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)[-2:]}"
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-{str(int(text) + 1)[-2:]}"
    return text


def split_list(value: str) -> list:
    return [v.strip() for v in re.split(r"[;,|/]", str(value or "")) if v.strip()]


def normalize_books(rows: list, rules: list | None = None) -> list:
    out = []
    for i, r in enumerate(rows, start=1):
        d = parse_date(r.get("document_date"))
        record = {
            "row_no": r["row_no"],
            # Invoice/document references are source values too.  Preserve
            # them as the transaction reference when a dedicated transaction
            # ID was not exported, rather than presenting a synthetic ID.
            "transaction_id": r.get("transaction_id") or r.get("document_number") or f"SYS-BOOK-{i:04d}",
            "transaction_id_source": "SOURCE_PROVIDED" if (r.get("transaction_id") or r.get("document_number")) else "SYSTEM_GENERATED",
            "customer_name": r.get("customer_name", ""),
            "customer_name_norm": core_name(r.get("customer_name", "")),
            "customer_code": normalize_code(r.get("customer_code")),
            "party_pan": normalize_code(r.get("party_pan")),
            "party_gstin": normalize_code(r.get("party_gstin")),
            "document_number": r.get("document_number", ""),
            "document_date": d.isoformat() if d else None,
            "taxable_value": parse_amount(r.get("taxable_value")),
            "gst_value": parse_amount(r.get("gst_value")),
            "tds_expected": parse_amount(r.get("tds_expected")),
            "tds_base_amount": parse_amount(r.get("tds_base_amount")),
            "section": normalize_code(r.get("section")),
            "advance": str(r.get("advance", "")).strip().upper() in ("Y", "YES", "TRUE", "1"),
            "financial_year": normalize_fy(r.get("financial_year")) or (financial_year_of(d) if d else None),
            "financial_year_source": "EXPLICIT" if normalize_fy(r.get("financial_year")) else "DERIVED_FROM_DATE" if d else None,
            "quarter": quarter_of(d) if d else None,
            "source_ledger": r.get("source_ledger", ""),
            "voucher_type": r.get("voucher_type", ""),
            "debit_amount": parse_amount(r.get("debit_amount")),
            "credit_amount": parse_amount(r.get("credit_amount")),
            "narration": r.get("narration", ""),
            "source_period": r.get("source_period", ""),
            "accounting_role": "NOT_DETERMINED",
            "matching_eligible": True,
        }
        # The calculator never reads 26AS data and only uses an explicitly
        # labelled Books calculation base when the Books TDS column is absent.
        record.update(public_calculation(calculate_expected_tds(record, rules or [], base_field="tds_base_amount")))
        out.append(record)
    return out


def apply_books_accounting_semantics(rows: list) -> dict:
    """Derive the full-reconciliation Books population from explicit ledger context.

    An amount becomes matchable only where the source account itself identifies
    a TDS Receivable movement.  Debit is the established receivable-side
    comparison amount used by the existing account-group workflow; credit is
    retained as adjustment evidence and never netted or fabricated into TDS.
    """
    summary = {"total_source_rows": len(rows), "tds_receivable_rows": 0, "tds_receivable_debit_rows": 0,
               "tds_receivable_credit_rows": 0, "non_tds_rows_excluded": 0, "accounting_role_review_rows": 0}
    for row in rows:
        ledger = normalize_name(row.get("source_ledger"))
        context = " ".join((ledger, normalize_name(row.get("voucher_type")), normalize_name(row.get("narration"))))
        is_balance = any(marker in context for marker in ("OPENING BALANCE", "CLOSING BALANCE", "GRAND TOTAL", "SUB TOTAL", "SUBTOTAL"))
        # Require both terms in the account label itself.  Narration and
        # voucher type are retained evidence, never enough on their own.
        is_tds_receivable = "TDS" in ledger and "RECEIVABLE" in ledger and not is_balance
        if not is_tds_receivable:
            row["matching_eligible"] = False
            row["validation_state"] = "EXCLUDED_NON_TDS_ACCOUNTING_ROW"
            summary["non_tds_rows_excluded"] += 1
            continue
        summary["tds_receivable_rows"] += 1
        debit, credit = row.get("debit_amount"), row.get("credit_amount")
        row.update({"accounting_role": "TDS_RECEIVABLE", "tds_receivable_debit": debit,
                    "tds_receivable_credit": credit})
        if debit is not None and debit > 0:
            row.update({"accounting_direction": "DEBIT", "tds_expected": debit,
                        "tds_book_amount": debit, "tds_amount_source": "TDS_RECEIVABLE_DEBIT_MOVEMENT",
                        "tds_calculation_status": "AVAILABLE", "tds_calculation_method": "SOURCE_TDS_RECEIVABLE_DEBIT"})
            summary["tds_receivable_debit_rows"] += 1
        elif credit is not None and credit > 0:
            row.update({"accounting_direction": "CREDIT", "tds_book_amount": None,
                        "matching_eligible": False, "validation_state": "ACCOUNTING_ADJUSTMENT_REVIEW"})
            summary["tds_receivable_credit_rows"] += 1
            summary["accounting_role_review_rows"] += 1
        else:
            row.update({"accounting_direction": "UNDETERMINED", "matching_eligible": False,
                        "validation_state": "ACCOUNTING_ROLE_REVIEW_REQUIRED"})
            summary["accounting_role_review_rows"] += 1
    return summary


def normalize_statement(rows: list, rules: list | None = None) -> list:
    out = []
    for i, r in enumerate(rows, start=1):
        d = parse_date(r.get("transaction_date"))
        deducted = parse_amount(r.get("tax_deducted"))
        deposited = parse_amount(r.get("tds_deposited"))
        record = {
            "row_no": r["row_no"],
              "statement_id": r.get("statement_id") or f"SYS-26AS-{i:04d}",
              "statement_id_source": "SOURCE_PROVIDED" if r.get("statement_id") else "SYSTEM_GENERATED",
            "tan": normalize_code(r.get("tan")),
            "deductor_name": r.get("deductor_name", ""),
            "deductor_name_norm": core_name(r.get("deductor_name", "")),
            "deductor_pan": normalize_code(r.get("deductor_pan")),
            "transaction_date": d.isoformat() if d else None,
            "tax_deducted": deducted,
            # Deposited and deducted are distinct source facts.  A missing
            # deposited field must remain missing; treating it as deducted
            # would fabricate claimability evidence.
            "tds_deposited": deposited,
            "deposited_not_reported": deposited is None,
            "status": (str(r.get("status", "")).strip().upper()[:1] or "F"),
            "section": normalize_code(r.get("section")),
            "amount_paid": parse_amount(r.get("amount_paid")),
            "financial_year": normalize_fy(r.get("financial_year")) or (financial_year_of(d) if d else None),
            "financial_year_source": "EXPLICIT" if normalize_fy(r.get("financial_year")) else "DERIVED_FROM_DATE" if d else None,
            "quarter": quarter_of(d) if d else None,
        }
        # For 26AS-only analysis this may calculate an independent expected
        # value from Amount Paid/Credited.  tax_deducted is never an input.
        record.update(public_calculation(calculate_expected_tds(record, rules or [], base_field="amount_paid")))
        out.append(record)
    return out


def normalize_customers(rows: list) -> list:
    out = []
    for r in rows:
        out.append({
            "row_no": r["row_no"],
            "customer_code": normalize_code(r.get("customer_code")),
            "customer_name": r.get("customer_name", ""),
            "customer_name_norm": core_name(r.get("customer_name", "")),
            "pan": normalize_code(r.get("pan")),
            "gstin": normalize_code(r.get("gstin")),
            "tans": [normalize_code(t) for t in split_list(r.get("tan"))],
            "aliases": split_list(r.get("aliases")),
            "source": "customer_master",
        })
    return out


def normalize_tds_receivable(rows: list, rules: list | None = None) -> list:
    out = []
    for i, r in enumerate(rows, start=1):
        d = parse_date(r.get("transaction_date"))
        out.append({
            "row_no": r["row_no"], "tds_transaction_id": r.get("tds_transaction_id") or f"SYS-TDS-{i:04d}",
            "tds_transaction_id_source": "SOURCE_PROVIDED" if r.get("tds_transaction_id") else "SYSTEM_GENERATED",
            "party_name": r.get("party_name", ""), "party_name_norm": core_name(r.get("party_name", "")),
              "party_pan": normalize_code(r.get("party_pan")), "party_gstin": normalize_code(r.get("party_gstin")), "tan": normalize_code(r.get("tan")),
            "transaction_date": d.isoformat() if d else None, "reference": r.get("reference", ""),
            "tds_expected": parse_amount(r.get("tds_expected")), "tds_base_amount": parse_amount(r.get("tds_base_amount")),
            "section": normalize_code(r.get("section")),
            "financial_year": normalize_fy(r.get("financial_year")) or (financial_year_of(d) if d else None),
            "financial_year_source": "EXPLICIT" if normalize_fy(r.get("financial_year")) else "DERIVED_FROM_DATE" if d else None,
            "quarter": quarter_of(d) if d else None,
            # Group-summary accounting exports are not vouchers. These values
            # are carried as source evidence so validation can keep credit
            # movements out of the positive TDS receivable stream.
            "source_granularity": r.get("source_granularity"),
            "tds_book_amount": parse_amount(r.get("tds_book_amount")),
            "accounting_direction": r.get("accounting_direction"),
        })
    return out


def normalize_sales_registry(rows: list) -> list:
    out = []
    for i, r in enumerate(rows, start=1):
        d = parse_date(r.get("invoice_date"))
        period = str(r.get("period", "") or "")
        # ``normalize_fy`` preserves unknown text for display elsewhere; only
        # call it for an actual FY representation here. A month such as
        # "Apr,2025" is period context, not an FY string.
        period_fy = normalize_fy(period) if re.fullmatch(r"(?:FY\s*)?\d{4}\s*[-/]\s*\d{2,4}", period.strip(), re.I) else None
        period_date = parse_date(period) if not period_fy else None
        derived_fy = period_fy or (financial_year_of(period_date) if period_date else None)
        invoice_value = parse_amount(r.get("invoice_value"))
        taxable_amount = parse_amount(r.get("taxable_amount"))
        out.append({
            "row_no": r["row_no"], "sales_transaction_id": r.get("sales_transaction_id") or f"SYS-SALES-{i:04d}",
            "sales_transaction_id_source": "SOURCE_PROVIDED" if r.get("sales_transaction_id") else "SYSTEM_GENERATED",
            "customer_name": r.get("customer_name", ""), "customer_name_norm": core_name(r.get("customer_name", "")),
            "customer_pan": normalize_code(r.get("customer_pan")), "customer_gstin": normalize_code(r.get("customer_gstin")),
            "invoice_number": r.get("invoice_number", ""), "invoice_date": d.isoformat() if d else None,
            # A GST register may provide multiple legitimate amounts. Do not
            # silently select invoice or taxable value as a 26AS comparison.
            "sales_amount": parse_amount(r.get("sales_amount")), "sales_amount_basis": "SOURCE_SPECIFIC_AMOUNT" if parse_amount(r.get("sales_amount")) is not None else "REVIEW_REQUIRED" if invoice_value is not None or taxable_amount is not None else "NOT_DETERMINABLE",
            "invoice_value": invoice_value, "taxable_amount": taxable_amount,
            "tax_components": {"igst": parse_amount(r.get("igst_amount")), "cgst": parse_amount(r.get("cgst_amount")), "sgst": parse_amount(r.get("sgst_amount")), "cess": parse_amount(r.get("cess_amount"))},
            "period": period or None, "state": r.get("state", "") or None,
            "reference": r.get("reference", ""),
            "financial_year": normalize_fy(r.get("financial_year")) or derived_fy or (financial_year_of(d) if d else None),
            "financial_year_source": "SOURCE_COLUMN" if period_fy else "PERIOD_DERIVED_DATE" if period_date else "INVOICE_DATE" if d else None,
            "quarter": quarter_of(d) if d else None,
        })
    return out


def customers_from_books(books: list) -> list:
    seen = {}
    for b in books:
        code = b["customer_code"]
        entry = seen.setdefault(code, {"customer_code": code, "customer_name": b["customer_name"], "customer_name_norm": b["customer_name_norm"], "pan": "", "gstin": "", "tans": [], "aliases": [], "source": "books", "row_no": b["row_no"]})
        entry["pan"] = entry["pan"] or b["party_pan"]
        entry["gstin"] = entry["gstin"] or b["party_gstin"]
    return list(seen.values())


NORMALIZERS = {BOOKS: normalize_books, FORM26AS: normalize_statement, CUSTOMER_MASTER: normalize_customers, TDS_RECEIVABLE: normalize_tds_receivable, SALES_REGISTRY: normalize_sales_registry}
