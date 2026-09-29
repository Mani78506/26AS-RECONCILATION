import re

BOOKS = "books"
FORM26AS = "form26as"
CUSTOMER_MASTER = "customer_master"
TDS_RECEIVABLE = "tds_receivable"
SALES_REGISTRY = "sales_registry"
PAYMENT_LEDGER = "payment_ledger"
TDS_DEPOSIT_EVIDENCE = "tds_deposit_evidence"
FILE_KINDS = (BOOKS, FORM26AS, CUSTOMER_MASTER, TDS_RECEIVABLE, SALES_REGISTRY, PAYMENT_LEDGER, TDS_DEPOSIT_EVIDENCE)
FILE_LABELS = {BOOKS: "Books", FORM26AS: "26AS / Form 16A", CUSTOMER_MASTER: "Customer Master", TDS_RECEIVABLE: "TDS Expected / Receivable", SALES_REGISTRY: "Sales Registry", PAYMENT_LEDGER: "TDS Compliance Payment Ledger", TDS_DEPOSIT_EVIDENCE: "TDS Deposit / Challan Evidence"}

# canonical column -> (required, accepted header aliases, description)
SCHEMAS = {
    BOOKS: {
        "transaction_id": (False, ["transaction_id", "txn_id", "id", "voucher_no", "voucher_number", "vouch_no", "vouch_number", "voucher", "entry_id", "reference", "ref_no"], "Unique books transaction reference (auto-generated if blank)"),
        "customer_name": (True, ["customer_name", "customer", "party_name", "customer_party_name", "party", "name", "particulars", "client_name"], "Customer / party name as per books"),
        "customer_code": (False, ["customer_code", "party_code", "code", "ledger_code", "customer_id"], "Customer code (strongly recommended)"),
        "party_pan": (False, ["party_pan", "pan", "customer_pan", "pan_no", "pan_number"], "Customer PAN"),
        "party_gstin": (False, ["party_gstin", "gstin", "gst_no", "gst_number", "customer_gstin"], "Customer GSTIN"),
        "document_number": (False, ["document_number", "doc_no", "document_no", "invoice_no", "invoice_number", "bill_no", "invoice"], "Invoice / document number"),
        "document_date": (True, ["document_date", "doc_date", "voucher_date", "posting_date", "invoice_date", "date", "bill_date", "transaction_date", "entry_date"], "Books posting / document date (DD-MM-YYYY)"),
        "taxable_value": (False, ["taxable_value", "taxable_amount", "base_amount", "net_amount", "invoice_value", "amount"], "Taxable value"),
        "gst_value": (False, ["gst_value", "gst_amount", "gst"], "GST amount"),
        "tds_expected": (False, ["tds_expected", "tds", "tds_amount", "tds_receivable", "tds_receivable_from_others", "tds_deducted", "expected_tds"], "Explicit TDS expected/receivable as recorded in Books"),
        "tds_base_amount": (False, ["tds_base_amount", "amount_paid_credited", "amount_paid_or_credited", "tds_calculation_base"], "Explicit amount paid/credited that is safe to use as a TDS calculation base"),
        "section": (False, ["section", "tds_section", "section_code"], "TDS section (194C, 194J, ...)"),
        "advance": (False, ["advance", "is_advance", "advance_flag"], "Y if advance receipt"),
        "financial_year": (False, ["financial_year", "fy"], "Financial year (derived from date if blank)"),
        # Accounting-ledger evidence is deliberately distinct from a customer
        # identity.  A Ledger column can name an account such as TDS
        # Receivable; it must never silently become a customer name.
        "source_ledger": (False, ["ledger", "ledger_name", "ledger_account", "account_name", "account", "account_description"], "Source ledger/account name"),
        "voucher_type": (False, ["voucher_type", "vouch_type", "entry_type"], "Source voucher type"),
        "debit_amount": (False, ["debit", "debit_amount", "dr"], "Source debit movement"),
        "credit_amount": (False, ["credit", "credit_amount", "cr"], "Source credit movement"),
        "narration": (False, ["narration", "description", "remarks", "memo"], "Source narration"),
        "source_period": (False, ["month", "source_period", "period", "reporting_period"], "Source reporting period"),
    },
    FORM26AS: {
        "tan": (True, ["tan", "tan_of_deductor", "deductor_tan", "tan_no"], "TAN of deductor"),
        "deductor_name": (True, ["deductor_name", "name_of_deductor", "deductor", "name", "party_name"], "Name of deductor"),
        "deductor_pan": (False, ["deductor_pan", "pan_of_deductor", "deductor_pan_no"], "Deductor PAN when explicitly supplied by the source"),
        "transaction_date": (True, ["transaction_date", "date_of_transaction", "date", "date_of_payment_credit", "date_of_payment"], "Date of transaction (DD-MM-YYYY)"),
        "tax_deducted": (True, ["tax_deducted", "tds_deducted", "tds", "tax_deducted_amount", "amount_of_tds", "amount_of_tax_deducted"], "Tax deducted"),
        "tds_deposited": (False, ["tds_deposited", "tax_deposited", "tds_deposited_amount", "amount_deposited", "deposited"], "TDS deposited when explicitly reported by the source"),
        "status": (False, ["status", "status_of_booking", "booking_status"], "Booking status F / U / P / O"),
        "section": (False, ["section", "section_code"], "Section under which deducted"),
        "amount_paid": (False, ["amount_paid", "amount_paid_credited", "total_amount_paid_credited", "amount_credited_paid", "amount_credited_or_paid", "amount"], "Amount paid / credited"),
        "financial_year": (False, ["financial_year", "fy"], "Financial year (derived from date if blank)"),
        "statement_id": (False, ["26as_record_id", "record_id", "statement_record_id", "transaction_id"], "26AS record identifier when supplied"),
    },
    CUSTOMER_MASTER: {
        "customer_code": (True, ["customer_code", "code", "party_code", "customer_id"], "Customer code"),
        "customer_name": (True, ["customer_name", "name", "party_name", "customer"], "Customer legal name"),
        "pan": (False, ["pan", "customer_pan", "party_pan"], "Customer PAN"),
        "gstin": (False, ["gstin", "gst_no", "customer_gstin"], "Customer GSTIN"),
        "tan": (False, ["tan", "tans", "deductor_tan", "tan_list"], "Deductor TAN(s), separate multiple with ;"),
        "aliases": (False, ["aliases", "alias", "alternate_names", "alternate_name", "aka"], "Alternate names, separate with ;"),
    },
    TDS_RECEIVABLE: {
        "tds_transaction_id": (False, ["tds_transaction_id", "transaction_id", "txn_id", "voucher_no", "voucher_number", "reference", "ref_no", "invoice_no"], "TDS transaction or voucher reference"),
          "party_name": (True, ["party_name", "party", "customer_name", "customer", "payee", "payer", "deductor_name", "deductor", "ledger", "particulars"], "TDS deductor / party name"),
        "party_pan": (False, ["party_pan", "pan", "customer_pan", "payee_pan", "pan_no"], "Party PAN as supplied"),
        "party_gstin": (False, ["party_gstin", "gstin", "gst_no", "gst_number", "customer_gstin"], "Party GSTIN when supplied"),
        "tan": (False, ["tan", "deductor_tan", "tan_of_deductor", "tan_no"], "Deductor TAN when present in the TDS source"),
          "transaction_date": (False, ["transaction_date", "date", "voucher_date", "document_date", "invoice_date", "entry_date"], "TDS transaction date when supplied"),
        "reference": (False, ["reference", "ref_no", "invoice_no", "invoice_number", "document_number", "voucher_no"], "Invoice or source reference"),
        "tds_expected": (True, ["tds_expected", "tds_receivable", "tds_amount", "tds", "tds_deducted", "tax_receivable", "tds_receivable_from_others"], "TDS Expected / Receivable as reported in the source"),
        "section": (False, ["section", "tds_section", "section_code"], "TDS section"),
        "tds_base_amount": (False, ["tds_base_amount", "amount_paid_credited", "amount_paid_or_credited", "tds_calculation_base"], "Explicit TDS calculation base only"),
        "financial_year": (False, ["financial_year", "fy"], "Financial year"),
    },
    SALES_REGISTRY: {
        "sales_transaction_id": (False, ["sales_transaction_id", "transaction_id", "txn_id", "id", "reference", "ref_no", "sno", "serial_no", "serial_number"], "Sales transaction reference"),
        "customer_name": (True, ["customer_name", "customer", "party_name", "customer_party_name", "party", "buyer_name", "buyer", "client_name", "client", "recipient", "recipient_name", "recipients_name", "purchaser_name"], "Sales customer name"),
        "customer_pan": (False, ["customer_pan", "party_pan", "pan", "pan_no"], "Customer PAN as supplied"),
        "customer_gstin": (False, ["customer_gstin", "party_gstin", "gstin", "gst_no", "gst_number"], "Customer GSTIN as supplied"),
        "invoice_number": (False, ["invoice_number", "invoice_no", "invoice", "bill_no", "document_number", "doc_no"], "Invoice number"),
        "invoice_date": (True, ["invoice_date", "date", "document_date", "doc_date", "transaction_date", "bill_date"], "Invoice date"),
        "sales_amount": (False, ["sales_amount", "sales_value", "gross_sales", "taxable_sales_amount", "amount_before_tax"], "Explicit configured sales comparison amount"),
        "invoice_value": (False, ["invoice_value", "invoice_amount", "gross_invoice_value"], "Invoice value as supplied; not automatically the 26AS comparison amount"),
        "taxable_amount": (False, ["taxable_amount", "taxable_value", "taxable_sales", "assessable_value"], "Taxable amount when separately supplied"),
        "igst_amount": (False, ["igst", "igst_amount", "integrated_tax"], "IGST component"),
        "cgst_amount": (False, ["cgst", "cgst_amount", "central_tax"], "CGST component"),
        "sgst_amount": (False, ["sgst", "sgst_amount", "state_tax"], "SGST component"),
        "cess_amount": (False, ["cess", "cess_amount"], "CESS component"),
        "period": (False, ["period", "reporting_period", "return_period", "tax_period"], "Reporting period used only when it unambiguously supports financial-year context"),
        "state": (False, ["state", "place_of_supply"], "State / place of supply"),
        "reference": (False, ["reference", "ref_no", "purchase_order", "po_no", "customer_reference"], "Additional source reference"),
        "financial_year": (False, ["financial_year", "fy"], "Financial year"),
    },
    PAYMENT_LEDGER: {
        "transaction_id": (False, ["transaction_id", "txn_id", "voucher_no", "voucher_number", "reference", "entry_id"], "Source transaction reference"),
        "deductee_name": (False, ["vendor_name", "vendor", "supplier_name", "supplier", "party_name", "deductee_name", "deductee", "customer_vendor_name"], "Vendor / deductee name"),
        "deductee_pan": (False, ["pan", "vendor_pan", "supplier_pan", "deductee_pan", "party_pan"], "Vendor PAN"),
        "deductee_gstin": (False, ["gstin", "vendor_gstin", "supplier_gstin", "deductee_gstin", "party_gstin"], "Vendor GSTIN"),
        "vendor_code": (False, ["vendor_code", "supplier_code", "party_code", "vendor_id"], "Vendor code"),
        "invoice_number": (False, ["invoice_no", "invoice_number", "bill_no", "bill_number", "document_no", "document_number"], "Invoice reference"),
        "invoice_date": (False, ["invoice_date", "bill_date", "document_date"], "Invoice date"),
        "transaction_date": (False, ["transaction_date", "date", "entry_date"], "Transaction date"),
        "credit_date": (False, ["credit_date", "date_of_credit", "accounting_date", "accrual_date"], "Credit date"),
        "payment_date": (False, ["payment_date", "date_of_payment", "paid_date"], "Payment date"),
        "amount": (False, ["amount", "invoice_amount", "gross_amount", "payment_amount", "transaction_amount", "bill_amount"], "Transaction amount"),
        "taxable_amount": (False, ["taxable_amount", "taxable_value"], "Taxable amount"),
        "payment_nature": (False, ["payment_nature", "nature_of_payment", "expense_type", "transaction_type"], "Payment nature"),
        "description": (False, ["description", "narration", "particulars"], "Description"),
        "section_input": (False, ["section", "tds_section", "section_code"], "Reported section"),
        "tds_expected": (False, ["tds_expected", "tds_receivable", "expected_tds", "tds_applicable", "tds_liability"], "Reported TDS expected"),
        "tds_deducted": (False, ["tds_deducted", "tds", "tax_deducted", "tds_amount"], "Reported TDS deducted"),
        "tds_deposited": (False, ["tds_deposited", "tax_deposited", "deposit_amount"], "Reported TDS deposited"),
        "deduction_date": (False, ["deduction_date", "tds_deduction_date", "date_tds_deducted"], "Deduction date"),
        "deposit_date": (False, ["deposit_date", "tds_deposit_date", "date_deposited"], "Deposit date"),
        "challan_number": (False, ["challan_no", "challan_number", "cin", "challan_reference"], "Challan reference"),
        "certificate_number": (False, ["certificate_number", "certificate_no", "lower_deduction_certificate", "nil_deduction_certificate"], "Lower/nil deduction certificate reference"),
        "certificate_rate": (False, ["certificate_rate", "lower_deduction_rate", "nil_deduction_rate"], "Certificate rate when explicitly supplied"),
        "certificate_valid_from": (False, ["certificate_valid_from", "certificate_from", "certificate_start_date"], "Certificate effective start date"),
        "certificate_valid_to": (False, ["certificate_valid_to", "certificate_to", "certificate_end_date", "certificate_expiry_date"], "Certificate effective end date"),
        "financial_year": (False, ["fy", "financial_year", "fiscal_year", "tax_year"], "Financial year"),
    },
    TDS_DEPOSIT_EVIDENCE: {
        "deposit_transaction_id": (False, ["deposit_transaction_id", "transaction_id", "payment_reference", "reference", "source_reference"], "Source deposit/evidence reference"),
        "challan_number": (False, ["challan_number", "challan_no", "challan_no."], "Source-provided challan number"),
        "bsr_code": (False, ["bsr_code", "bsr"], "Source-provided BSR code"),
        "cin": (False, ["cin", "challan_identification_number"], "Source-provided CIN"),
        "deposit_date": (False, ["deposit_date", "date_of_deposit"], "Deposit date"),
        "challan_date": (False, ["challan_date"], "Challan date"),
        "amount_deposited": (False, ["amount_deposited", "deposit_amount"], "Total amount deposited"),
        "tds_amount": (False, ["tds_amount", "tds_deposited", "tax_deposited"], "TDS component deposited"),
        "interest_amount": (False, ["interest_amount", "interest"], "Interest component"),
        "fee_amount": (False, ["fee_amount", "fee"], "Fee component"),
        "total_amount": (False, ["total_amount", "total"], "Total challan amount"),
        "tan": (False, ["tan"], "TAN when supplied"), "section": (False, ["section", "tds_section"], "Section"),
        "financial_year": (False, ["financial_year", "fy"], "Financial year"), "quarter": (False, ["quarter"], "Quarter"), "month": (False, ["month", "deduction_month"], "Relevant month"),
        "bank_reference": (False, ["bank_reference", "bank_ref"], "Bank/source reference"),
    },
}

TEMPLATE_ROWS = {
    BOOKS: [
        ["INV001", "ABC Ltd", "C001", "AAACA1234A", "27AAACA1234A1Z5", "INV/24-25/001", "15-04-2024", "100000", "18000", "10000", "194J", "N", "2024-25"],
    ],
    FORM26AS: [
        ["ABCD12345E", "ABC LIMITED", "", "20-04-2024", "10000", "10000", "F", "194J", "100000", "2024-25", ""],
    ],
    CUSTOMER_MASTER: [
        ["C001", "ABC Ltd", "AAACA1234A", "27AAACA1234A1Z5", "ABCD12345E;XYZB67890C", "ABC Limited;A B C Ltd"],
    ],
    TDS_RECEIVABLE: [["TDS-001", "ABC Ltd", "AAACA1234A", "ABCD12345E", "15-04-2024", "INV/001", "10000", "194J", "100000", "2024-25"]],
    SALES_REGISTRY: [["SAL-001", "ABC Ltd", "AAACA1234A", "27AAACA1234A1Z5", "INV/001", "15-04-2024", "100000", "100000", "PO-001", "2024-25"]],
    PAYMENT_LEDGER: [["TXN-001", "ABC Supplier", "AAACA1234A", "27AAACA1234A1Z5", "V001", "INV-001", "15-04-2026", "", "15-04-2026", "100000", "", "Professional fees", "", "194J", "", "10000", "", "", "", "", "", "2026-27"]],
}

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def header_key(header) -> str:
    text = str(header or "").replace("\ufeff", "")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def column_lookup(kind: str) -> dict:
    lookup = {}
    for canonical, (_, aliases, _) in SCHEMAS[kind].items():
        for alias in [canonical, *aliases]:
            lookup.setdefault(header_key(alias), canonical)
    return lookup


def required_columns(kind: str) -> list:
    return [c for c, (req, _, _) in SCHEMAS[kind].items() if req]


def schema_description(kind: str) -> list:
    return [{"column": c, "required": req, "aliases": aliases, "description": desc} for c, (req, aliases, desc) in SCHEMAS[kind].items()]
