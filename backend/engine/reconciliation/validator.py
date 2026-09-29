from collections import Counter

from ..shared.schema import BOOKS, CUSTOMER_MASTER, FILE_LABELS, FORM26AS, GSTIN_RE, PAN_RE, TAN_RE, SALES_REGISTRY, TDS_RECEIVABLE, SCHEMAS, required_columns

CHECK_LABELS = [
    ("file_detected", "File detected"),
    ("required_columns", "Required columns"),
    ("valid_dates", "Valid dates"),
    ("valid_amounts", "Valid amounts"),
    ("financial_year", "Financial year"),
    ("required_identifiers", "Required identifiers"),
]


def _issue(severity, file_kind, field, problem, explanation, fix, rows=None):
    return {"severity": severity, "file": FILE_LABELS[file_kind], "field": field, "problem": problem, "explanation": explanation, "recommended_fix": fix, "rows": (rows or [])[:20], "row_count": len(rows or [])}


def validate_file(kind: str, filename: str, raw_rows: list, mapped: list, unmapped: list, normalized: list, expected_fy: str | None, workflow: str | None = None, accounting_summary: dict | None = None):
    checks, issues, row_exceptions = {}, [], []
    raw_by_row = {row.get("row_no"): row for row in raw_rows}

    def row_exception(field, problem, explanation, fix, rows):
        """Keep bad transaction rows auditable but unavailable for matching."""
        issues.append(_issue("row_exception", kind, field, problem, explanation, fix, rows))
        affected = set(rows)
        for row in normalized:
            if row["row_no"] in affected:
                row["validation_state"] = "UNAVAILABLE_FOR_MATCHING"
                row.setdefault("validation_messages", []).append(problem)
                row_exceptions.append({"source_row": row["row_no"], "field": field, "value": (raw_by_row.get(row["row_no"]) or {}).get(field, ""), "problem": problem, "action": fix, "severity": "ROW_EXCEPTION"})
    is_group_summary = kind == TDS_RECEIVABLE and any(r.get("source_granularity") == "ACCOUNT_GROUP_SUMMARY" for r in normalized)
    checks["file_detected"] = (len(raw_rows) > 0, f"{len(raw_rows)} data rows · {len(mapped)} recognised columns")
    if not raw_rows:
        issues.append(_issue("error", kind, "—", "No data rows found", "The file has a header row but no data.", "Export the file again with the transaction rows included."))

    required = required_columns(kind)
    if kind == BOOKS and workflow == "FULL_RECONCILIATION" and "source_ledger" in mapped:
        # Ledger-account sources establish their reconciliation population by
        # account role, not by claiming the account label is a customer.
        required = ["document_date"]
    if workflow == "SALES_TDS_26AS":
        required = {TDS_RECEIVABLE: ["party_name", "tds_expected", "financial_year"], FORM26AS: ["deductor_name", "tan", "amount_paid", "tax_deducted", "financial_year"]}.get(kind, required)
    missing = [c for c in required if c not in mapped]
    if kind == FORM26AS and expected_fy and "financial_year" in missing:
        missing.remove("financial_year")
    checks["required_columns"] = (not missing, "All required columns present" if not missing else f"Missing: {', '.join(missing)}")
    for col in missing:
        aliases = SCHEMAS[kind][col][1]
        issues.append(_issue("error", kind, col, f"Required column '{col}' not found", f"The engine needs this column to {'identify the deductor' if col == 'tan' else 'process the file'}. Unrecognised headers: {', '.join(unmapped[:8]) or 'none'}.", f"Rename the column header to '{col}' (accepted aliases: {', '.join(aliases[:5])})."))

    # Books can omit a reported TDS column only when the export explicitly
    # supplies a calculation base.  A generic taxable/invoice amount is not
    # automatically a valid TDS base, so it cannot silently enable a rule.
    accounting_books = kind == BOOKS and workflow == "FULL_RECONCILIATION" and accounting_summary is not None
    if kind == BOOKS and not accounting_books and "tds_expected" not in mapped and ("tds_base_amount" not in mapped or not any(r.get("tds_base_amount") is not None for r in normalized)):
        issues.append(_issue("error", kind, "tds_expected", "No expected TDS source found", "Books contains neither an explicit TDS expected/receivable column nor an explicit TDS calculation-base column. The system will not infer a TDS base from another amount column.", "Provide a Books TDS / TDS receivable column, or an explicitly labelled TDS calculation base together with section and configured rule coverage."))

    date_field = {BOOKS: "document_date", FORM26AS: "transaction_date", TDS_RECEIVABLE: "transaction_date", SALES_REGISTRY: "invoice_date"}.get(kind)
    if (kind == TDS_RECEIVABLE or (workflow == "SALES_TDS_26AS" and kind == FORM26AS)) and date_field not in mapped:
        checks["valid_dates"] = (True, "No transaction date supplied; financial year is used as the workflow boundary")
    elif date_field:
        bad = [r["row_no"] for r in normalized if not r[date_field]]
        checks["valid_dates"] = (not bad, "All dates parsed" if not bad else f"{len(bad)} rows have unreadable or blank dates")
        if bad:
            row_exception(date_field, f"{len(bad)} rows have invalid dates", "Dates decide the financial year and quarter used for matching. These rows are retained for review and excluded from matching.", "Use DD-MM-YYYY or an Excel date format for every row.", bad)
    else:
        checks["valid_dates"] = (True, "Not applicable")

    amount_field = {BOOKS: "tds_expected", FORM26AS: "tax_deducted", TDS_RECEIVABLE: "tds_expected", SALES_REGISTRY: "sales_amount"}.get(kind)
    if accounting_books:
        debit_rows = [r for r in normalized if r.get("accounting_role") == "TDS_RECEIVABLE" and r.get("tds_book_amount") is not None]
        credit_rows = [r for r in normalized if r.get("accounting_role") == "TDS_RECEIVABLE" and r.get("accounting_direction") == "CREDIT"]
        checks["valid_amounts"] = (bool(debit_rows), f"{accounting_summary['total_source_rows']} source rows · {len(debit_rows)} TDS Receivable debit movements · {len(credit_rows)} credit adjustments · {accounting_summary['non_tds_rows_excluded']} non-TDS rows excluded")
        if not debit_rows:
            issues.append(_issue("error", kind, "source_ledger", "SOURCE SEMANTIC REVIEW REQUIRED", "No positive debit movement in an explicitly labelled TDS Receivable account was identified. The system did not infer TDS from generic debit or credit columns.", "Confirm the TDS Receivable account context or provide a Books TDS Receivable export."))
        if credit_rows:
            issues.append(_issue("warning", kind, "credit_amount", f"{len(credit_rows)} credit-side TDS Receivable movements retained", "Credit movements are preserved as accounting adjustment or reversal evidence and are not netted into the reconciliation amount.", "Review the adjustment evidence if it must be linked to a TDS receivable debit." , [r["row_no"] for r in credit_rows]))
    elif is_group_summary:
        eligible = [r for r in normalized if r.get("tds_book_amount") is not None]
        credit_only = [r for r in normalized if r.get("accounting_direction") == "CREDIT" and r.get("tds_book_amount") is None]
        checks["valid_amounts"] = (bool(eligible), f"{len(eligible)} explicit TDS Receivable debit movements; {len(credit_only)} credit-only movements require review")
        if credit_only:
            issues.append(_issue("warning", kind, "credit_amount", f"{len(credit_only)} credit-only TDS Receivable account movements", "A credit movement is preserved for accounting review and is not converted into a positive TDS receivable amount.", "Review the accounting adjustment; provide voucher-level evidence if it must be reconciled.", [r["row_no"] for r in credit_only]))
    elif kind == SALES_REGISTRY:
        candidates = [r for r in normalized if r.get("invoice_value") is not None or r.get("taxable_amount") is not None or r.get("sales_amount") is not None]
        missing_values = [r["row_no"] for r in normalized if r.get("invoice_value") is None and r.get("taxable_amount") is None and r.get("sales_amount") is None]
        explicit_basis = [r for r in normalized if r.get("sales_amount") is not None]
        checks["valid_amounts"] = (bool(candidates) and not missing_values, f"{len(candidates)} rows have source amounts; {'configured comparison basis available' if explicit_basis else 'invoice and taxable values retained separately'}")
        if missing_values:
            issues.append(_issue("error", kind, "invoice_value", f"{len(missing_values)} rows have no invoice, taxable, or source-specific amount", "A Sales Registry row needs at least one source-reported amount for auditability.", "Provide Invoice Value, Taxable Value, or an explicitly configured Sales Amount.", missing_values))
        if candidates and not explicit_basis:
            issues.append(_issue("warning", kind, "sales_amount_basis", "Sales amount basis requires confirmation", "Invoice Value and Taxable Value are both preserved. The engine will not choose one as the 26AS Amount Paid/Credited comparison basis without an explicit source-specific amount or controlled confirmation.", "Confirm the comparison basis before treating Sales amounts as matched."))
    elif amount_field and amount_field in mapped:
        bad = [r["row_no"] for r in normalized if r[amount_field] is None]
        neg = [r["row_no"] for r in normalized if r[amount_field] is not None and r[amount_field] < 0]
        checks["valid_amounts"] = (not bad, "All amounts numeric" if not bad else f"{len(bad)} rows have non-numeric amounts")
        if bad:
            row_exception(amount_field, f"{len(bad)} rows have non-numeric amounts", "Amounts must be numeric to reconcile TDS. These rows are retained for review and excluded from matching.", "Remove text, currency symbols or blank cells from the amount column.", bad)
        if neg:
            issues.append(_issue("warning", kind, amount_field, f"{len(neg)} rows have negative amounts", "Negative TDS usually indicates a reversal or credit note.", "Confirm these are intended; they are reconciled as-is.", neg))
        if kind == FORM26AS:
            unreported = [r["row_no"] for r in normalized if r.get("deposited_not_reported")]
            if unreported:
                issues.append(_issue("info", kind, "tds_deposited", f"TDS deposited not reported for {len(unreported)} rows", "TDS Deposited is kept unknown and is never inferred from TDS Deducted.", "Include 'TDS Deposited' to assess claimability precisely.", unreported))
    elif kind == BOOKS:
        available = [r for r in normalized if r.get("tds_calculation_status") == "AVAILABLE"]
        unavailable = [r for r in normalized if r.get("tds_calculation_status") != "AVAILABLE"]
        checks["valid_amounts"] = (bool(available), f"{len(available)} rows have reported or determinable expected TDS; {len(unavailable)} require review")
        if unavailable:
            issues.append(_issue("warning", kind, "tds_expected", f"{len(unavailable)} rows have no determinable expected TDS", "These rows have neither an explicit Books TDS amount nor sufficient configured-rule inputs. They remain available for review but are not assigned an invented expected amount.", "Provide the Books TDS column, or an explicit TDS calculation base, section and an applicable configured rule.", [r["row_no"] for r in unavailable]))
    else:
        checks["valid_amounts"] = (amount_field is None, "Not applicable" if amount_field is None else "Amount column missing")

    if kind in (BOOKS, FORM26AS, TDS_RECEIVABLE, SALES_REGISTRY):
        fys = Counter(r["financial_year"] for r in normalized if r["financial_year"])
        detail = ", ".join(f"{fy} ({n})" for fy, n in fys.most_common())
        ok = bool(fys) and len(fys) <= 1 and (not expected_fy or expected_fy in fys)
        checks["financial_year"] = (ok, detail or "No financial year detected")
        if not fys:
            issues.append(_issue("error", kind, "financial_year", "Financial year could not be determined", "No valid transaction date or financial-year value was available to derive the financial year.", "Provide valid transaction dates or a financial year column for every transaction."))
        if len(fys) > 1:
            issues.append(_issue("warning", kind, "financial_year", "Multiple financial years detected", f"Rows span {detail}. Matching only pairs transactions within the same financial year.", "Filter the export to a single financial year if this is unintended."))
        if expected_fy and fys and expected_fy not in fys:
            issues.append(_issue("warning", kind, "financial_year", "FINANCIAL_YEAR_CONFLICT", f"The source reports {detail}, while the selected assignment is FY {expected_fy}. Source values were preserved and no assignment value replaced them.", f"Select the matching assignment FY or review the source reporting period before reconciliation."))
    else:
        checks["financial_year"] = (True, "Not applicable")

    if kind == FORM26AS:
        bad_tan = [r["row_no"] for r in normalized if not TAN_RE.match(r["tan"])]
        checks["required_identifiers"] = (not bad_tan, "All TANs valid" if not bad_tan else f"{len(bad_tan)} rows have invalid TAN")
        if bad_tan:
            row_exception("tan", f"{len(bad_tan)} rows have an invalid TAN", "A TAN is 4 letters, 5 digits, 1 letter (e.g. ABCD12345E). These rows are retained for review and excluded from matching.", "Correct the TAN values or remove blank rows.", bad_tan)
    elif kind == BOOKS and accounting_books:
        evidence_rows = [r for r in normalized if r.get("accounting_role") == "TDS_RECEIVABLE" and r.get("tds_book_amount") is not None]
        no_identity = [r for r in evidence_rows if not r["customer_name"] and not r["customer_code"] and not r["party_pan"] and not r["party_gstin"]]
        checks["required_identifiers"] = (True, f"{len(evidence_rows) - len(no_identity)} TDS Receivable rows have customer evidence; {len(no_identity)} require identity review")
        if no_identity:
            issues.append(_issue("warning", kind, "customer_name", f"{len(no_identity)} TDS Receivable rows require identity review", "The ledger account is not treated as a customer identity. These source-backed TDS movements remain auditable, but no customer was invented for automatic identity matching.", "Provide a party/customer, PAN, GSTIN, or an explicit offsetting-party relationship for the relevant voucher rows.", [r["row_no"] for r in no_identity]))
    elif kind == BOOKS:
        no_name = [r["row_no"] for r in normalized if not r["customer_name"] and not r["customer_code"]]
        no_code = [r["row_no"] for r in normalized if not r["customer_code"]]
        bad_pan = [r["row_no"] for r in normalized if r["party_pan"] and not PAN_RE.match(r["party_pan"])]
        checks["required_identifiers"] = (not no_name, "Customer identifiers present" if not no_name else f"{len(no_name)} rows have no customer")
        if no_name:
            issues.append(_issue("error", kind, "customer_name", f"{len(no_name)} rows have no customer name or code", "Identity resolution needs a customer to map deductors to.", "Fill the customer name or code for every row.", no_name))
        if no_code and len(no_code) < len(normalized):
            issues.append(_issue("warning", kind, "customer_code", f"{len(no_code)} rows have no customer code", "Rows without a code are grouped by normalised customer name.", "Add customer codes for reliable grouping.", no_code))
        if bad_pan:
            issues.append(_issue("warning", kind, "party_pan", f"{len(bad_pan)} rows have malformed PAN", "PAN must be 5 letters, 4 digits, 1 letter.", "Correct the PAN or leave blank.", bad_pan))
        dup_ids = [tid for tid, n in Counter(r["transaction_id"] for r in normalized).items() if n > 1]
        if dup_ids:
            issues.append(_issue("warning", kind, "transaction_id", f"{len(dup_ids)} duplicate transaction IDs", "Duplicate references make audit trails ambiguous; duplicates are flagged as DUPLICATE_BOOK.", "Ensure each transaction has a unique reference."))
    elif kind == TDS_RECEIVABLE:
        no_name = [r["row_no"] for r in normalized if not r["party_name"]]
        bad_pan = [r["row_no"] for r in normalized if r["party_pan"] and not PAN_RE.match(r["party_pan"])]
        bad_tan = [r["row_no"] for r in normalized if r["tan"] and not TAN_RE.match(r["tan"])]
        checks["required_identifiers"] = (not no_name, "Party identifiers present" if not no_name else f"{len(no_name)} rows have no party")
        if no_name: issues.append(_issue("error", kind, "party_name", f"{len(no_name)} rows have no party name", "TDS records need a source party for an explainable identity chain.", "Provide a party/customer/payee name for each row.", no_name))
        if bad_pan: issues.append(_issue("warning", kind, "party_pan", f"{len(bad_pan)} rows have malformed PAN", "PAN is used only when explicitly supplied.", "Correct it or leave it blank.", bad_pan))
        if bad_tan: issues.append(_issue("warning", kind, "tan", f"{len(bad_tan)} rows have malformed TAN", "TAN is used only when explicitly supplied.", "Correct it or leave it blank.", bad_tan))
    elif kind == SALES_REGISTRY:
        no_name = [r["row_no"] for r in normalized if not r["customer_name"]]
        bad_pan = [r["row_no"] for r in normalized if r["customer_pan"] and not PAN_RE.match(r["customer_pan"])]
        bad_gst = [r["row_no"] for r in normalized if r["customer_gstin"] and not GSTIN_RE.match(r["customer_gstin"])]
        checks["required_identifiers"] = (not no_name, "Customer identifiers present" if not no_name else f"{len(no_name)} rows have no customer")
        if no_name: issues.append(_issue("error", kind, "customer_name", f"{len(no_name)} rows have no customer name", "Sales records need a customer name for identity review.", "Provide a customer name for each row.", no_name))
        if bad_pan: issues.append(_issue("warning", kind, "customer_pan", f"{len(bad_pan)} rows have malformed PAN", "PAN is used only when explicitly supplied.", "Correct it or leave it blank.", bad_pan))
        if bad_gst: issues.append(_issue("warning", kind, "customer_gstin", f"{len(bad_gst)} rows have malformed GSTIN", "GSTIN is used only when explicitly supplied.", "Correct it or leave it blank.", bad_gst))
        duplicate_keys = Counter((r.get("invoice_number"), r.get("invoice_date"), r.get("customer_gstin") or r.get("customer_name_norm"), r.get("invoice_value"), r.get("taxable_amount")) for r in normalized if r.get("invoice_number"))
        duplicates = [key for key, count in duplicate_keys.items() if count > 1]
        if duplicates:
            duplicate_rows = [r["row_no"] for r in normalized if (r.get("invoice_number"), r.get("invoice_date"), r.get("customer_gstin") or r.get("customer_name_norm"), r.get("invoice_value"), r.get("taxable_amount")) in duplicates]
            issues.append(_issue("warning", kind, "invoice_number", f"{len(duplicate_rows)} possible duplicate invoice rows", "Matching invoice number, date, customer/GSTIN and amount are preserved for CA review; rows were not removed.", "Review the source for duplicate, credit-note, or amended invoice entries.", duplicate_rows))
    else:
        bad_tan = [r["row_no"] for r in normalized for t in r["tans"] if not TAN_RE.match(t)]
        bad_pan = [r["row_no"] for r in normalized if r["pan"] and not PAN_RE.match(r["pan"])]
        bad_gst = [r["row_no"] for r in normalized if r["gstin"] and not GSTIN_RE.match(r["gstin"])]
        dup_codes = [c for c, n in Counter(r["customer_code"] for r in normalized).items() if n > 1 and c]
        checks["required_identifiers"] = (not bad_tan, "Identifiers valid" if not bad_tan else f"{len(bad_tan)} rows have invalid TAN")
        if bad_tan:
            issues.append(_issue("error", kind, "tan", f"{len(bad_tan)} rows have an invalid TAN", "Master TANs are trusted identity signals and must be well-formed.", "Correct the TAN values (format ABCD12345E).", bad_tan))
        if bad_pan:
            issues.append(_issue("warning", kind, "pan", f"{len(bad_pan)} rows have malformed PAN", "PAN format is 5 letters, 4 digits, 1 letter.", "Correct the PAN or leave blank.", bad_pan))
        if bad_gst:
            issues.append(_issue("warning", kind, "gstin", f"{len(bad_gst)} rows have malformed GSTIN", "GSTIN must be 15 characters.", "Correct the GSTIN or leave blank.", bad_gst))
        if dup_codes:
            issues.append(_issue("info", kind, "customer_code", f"{len(dup_codes)} customer codes repeat", "Repeated codes are merged; TANs and aliases are combined for that customer.", "No action needed if intentional (one row per TAN)."))

    usable_rows = [row for row in normalized if row.get("validation_state") != "UNAVAILABLE_FOR_MATCHING" and row.get("matching_eligible", True)]
    if normalized and not usable_rows and not any(i["severity"] == "error" for i in issues):
        issues.append(_issue("error", kind, "—", "No usable transaction rows", "Every detected transaction row requires review, so matching cannot proceed safely.", "Correct at least one transaction row and validate again."))
    status = "BLOCKED" if any(i["severity"] == "error" for i in issues) else "READY_WITH_EXCEPTIONS" if row_exceptions else "READY_WITH_WARNINGS" if any(i["severity"] == "warning" for i in issues) else "VALID"
    return {
        "kind": kind, "label": FILE_LABELS[kind], "filename": filename, "status": status, "row_count": len(normalized),
        "checks": [{"code": code, "label": label, "passed": checks[code][0], "detail": checks[code][1]} for code, label in CHECK_LABELS],
        "issues": issues, "row_exceptions": row_exceptions, "row_summary": {"transaction_rows": len(normalized), "valid_rows": len(usable_rows), "warning_rows": 0, "exception_rows": len({item["source_row"] for item in row_exceptions}), "excluded_non_transaction_rows": accounting_summary["non_tds_rows_excluded"] if accounting_summary else 0, "tds_receivable_rows": accounting_summary["tds_receivable_rows"] if accounting_summary else None, "tds_receivable_debit_rows": accounting_summary["tds_receivable_debit_rows"] if accounting_summary else None, "tds_receivable_credit_rows": accounting_summary["tds_receivable_credit_rows"] if accounting_summary else None, "accounting_role_review_rows": accounting_summary["accounting_role_review_rows"] if accounting_summary else None}, "columns_mapped": mapped, "columns_unmapped": unmapped,
    }
