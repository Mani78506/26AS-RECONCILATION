from datetime import datetime, timezone

from .classifier import build_results, build_summary
from .identity import resolve_identities
from .matcher import run_matching
from .normalizer import NORMALIZERS, apply_books_accounting_semantics, customers_from_books, normalize_code, normalize_fy, normalize_statement
from ..shared.parser import ParseError, map_columns, parse_rows, read_table
from ..shared.schema import BOOKS, CUSTOMER_MASTER, FILE_LABELS, FORM26AS, SALES_REGISTRY, TDS_RECEIVABLE
from .sales_tds_workflow import run_sales_tds_26as
from .source_profile import source_profile
from .validator import validate_file

DEFAULT_SETTINGS = {"amount_tolerance_abs": 10.0, "amount_tolerance_pct": 1.0, "fuzzy_high_threshold": 90, "fuzzy_review_threshold": 70, "fuzzy_min_gap": 8, "fuzzy_candidate_limit": 250, "max_group_size": 6, "tds_rules": []}
STEPS = [("validate", "Validate"), ("normalize", "Normalize"), ("resolve_identity", "Resolve Identity"), ("match", "Match Transactions"), ("claimability", "Apply Claimability"), ("results", "Generate Results")]


class PipelineError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def inspect_upload_source(kind: str, filename: str, content: bytes, progress=None):
    """Read just enough of a source to describe it on the upload card.

    This deliberately does not normalize rows or run validation.  Those steps
    belong to the explicit validation job, where their progress and outcome
    are persisted independently from receiving the file.
    """
    try:
        if progress:
            progress("OPENING_WORKBOOK", 0, None)
        df = read_table(filename, content, kind, inspection=True)
    except ParseError as exc:
        return {
            "readable": False, "diagnostic": str(exc), "row_count": 0,
            "columns_mapped": [], "recognized_headers": [],
            "columns_unmapped": [], "semantic_profile": None, "source": {},
        }
    if progress:
        progress("DETECTING_COLUMNS", 0, len(df.index))
    # Source ingestion needs structural metadata only. Validation later
    # creates the canonical records once, with its established row-level
    # checks. Avoiding ``to_dict('records')`` here removes a second 142k-row
    # Python-object conversion from every uploaded workbook.
    header_mapping, unmapped = map_columns(df, kind)
    mapped = list(header_mapping.values())
    source = {key: df.attrs[key] for key in (
        "sheet_name", "workbook_sheets", "workbook_engine", "header_row",
        "header_score", "header_detected", "non_empty_rows",
        "detected_file_type", "report_type", "source_granularity",
        "report_period", "financial_year_context", "header_structure",
        "control_totals",
    ) if key in df.attrs}
    return {
        "readable": True, "diagnostic": None, "row_count": len(df.index),
        "columns_mapped": mapped, "recognized_headers": list(header_mapping.keys()),
        "columns_unmapped": unmapped,
        "semantic_profile": source_profile(list(df.columns), header_mapping, unmapped),
        "source": source,
        "source_processing_stage": "SOURCE_PROFILE_READY",
    }


def load_and_validate(kind: str, filename: str, content: bytes, expected_fy: str | None, rules: list | None = None, workflow: str | None = None):
    try:
        df = read_table(filename, content, kind)
    except ParseError as e:
        return {"kind": kind, "label": FILE_LABELS[kind], "filename": filename, "status": "BLOCKED", "row_count": 0, "checks": [{"code": "file_detected", "label": "File detected", "passed": False, "detail": str(e)}], "issues": [{"severity": "error", "file": FILE_LABELS[kind], "field": "—", "problem": "File could not be read", "explanation": str(e), "recommended_fix": "Upload a CSV or Excel file exported from your accounting system.", "rows": [], "row_count": 0}], "row_exceptions": [], "row_summary": {"transaction_rows": 0, "valid_rows": 0, "warning_rows": 0, "exception_rows": 0, "excluded_non_transaction_rows": 0}, "columns_mapped": [], "columns_unmapped": []}, []
    raw, mapped, unmapped = parse_rows(df, kind)
    is_group_summary = kind == TDS_RECEIVABLE and df.attrs.get("report_type") == "TDS_RECEIVABLE_GROUP_SUMMARY"
    if is_group_summary:
        raw = df.attrs["ledger_raw_rows"]
    required_for_normalization = set(_min_cols(kind, workflow))
    # A 26AS/Form 16A source may legitimately omit a physical FY column. The
    # selected assignment is context, never a fabricated source value.
    if kind == FORM26AS and expected_fy:
        required_for_normalization.discard("financial_year")
    normalized = NORMALIZERS[kind](raw, rules) if kind in (BOOKS, FORM26AS) and required_for_normalization.issubset(mapped) else (NORMALIZERS[kind](raw) if required_for_normalization.issubset(mapped) else [])
    # An assignment is valid workflow context when a source contains no FY
    # evidence at all.  It never overwrites an explicit or date-derived source
    # FY, which remains available for the validator to flag as a conflict.
    if kind in (BOOKS, FORM26AS, TDS_RECEIVABLE, SALES_REGISTRY) and normalized and not any(row.get("financial_year") for row in normalized):
        assignment_fy = normalize_fy(expected_fy) if expected_fy else None
        metadata_fy = normalize_fy(df.attrs.get("financial_year_context")) if df.attrs.get("financial_year_context") else None
        resolved_fy, provenance = (assignment_fy, "ASSIGNMENT") if assignment_fy else (metadata_fy, "SOURCE_METADATA") if metadata_fy else (None, None)
        if resolved_fy:
            for row in normalized:
                row["financial_year"] = resolved_fy
                row["financial_year_source"] = provenance
    accounting_summary = None
    if kind == BOOKS and workflow == "FULL_RECONCILIATION" and "source_ledger" in mapped:
        accounting_summary = apply_books_accounting_semantics(normalized)
    report = validate_file(kind, filename, raw, mapped, unmapped, normalized, expected_fy, workflow, accounting_summary)
    header_mapping = map_columns(df, kind)[0]
    report["recognized_headers"] = list(header_mapping.keys())
    report["header_mappings"] = [{"source_header": str(source), "canonical_field": canonical, "mapping_method": "CANONICAL_HEADER" if str(source) == canonical else "CANONICAL_ALIAS"} for source, canonical in header_mapping.items()]
    report["semantic_profile"] = source_profile(list(df.columns), header_mapping, unmapped)
    if is_group_summary:
        semantic_rows = df.attrs.get("ledger_semantic_rows", [])
        semantics_by_row = {row["row_no"]: row for row in semantic_rows if row.get("row_type") == "ACCOUNT_GROUP"}
        for row in normalized:
            row.update(semantics_by_row.get(row["row_no"], {}))
            row["tds_transaction_id"] = f"SYS-TDS-GROUP-{row['row_no']:04d}"
            row["tds_transaction_id_source"] = "SYSTEM_GENERATED_FROM_SOURCE_ROW"
            row["financial_year_source"] = "REPORT_PERIOD_CONTEXT"
        account_rows = [row for row in semantic_rows if row.get("row_type") == "ACCOUNT_GROUP" and row.get("accounting_role") == "TDS_RECEIVABLE"]
        debit_rows = [row for row in account_rows if row.get("tds_book_amount") is not None]
        credit_rows = [row for row in account_rows if row.get("accounting_direction") == "CREDIT"]
        calculated_debit = round(sum(row.get("debit_amount") or 0 for row in account_rows), 2)
        calculated_credit = round(sum(row.get("credit_amount") or 0 for row in account_rows), 2)
        control = df.attrs.get("control_totals", {})
        report["ledger_analysis"] = {"report_type": df.attrs["report_type"], "source_granularity": df.attrs["source_granularity"], "report_period": df.attrs.get("report_period"), "financial_year_context": df.attrs.get("financial_year_context"), "header_structure": df.attrs.get("header_structure"), "account_rows": len(account_rows), "eligible_debit_rows": len(debit_rows), "credit_only_review_rows": len(credit_rows), "non_transaction_rows": len([row for row in semantic_rows if row.get("row_type") == "GRAND_TOTAL"]), "rows": semantic_rows, "control_totals": {**control, "calculated_debit_total": calculated_debit, "calculated_credit_total": calculated_credit, "debit_difference": round(calculated_debit - (control.get("source_debit_total") or 0), 2) if control.get("source_debit_total") is not None else None, "credit_difference": round(calculated_credit - (control.get("source_credit_total") or 0), 2) if control.get("source_credit_total") is not None else None}}
        report["semantic_profile"] = {"columns": [{"source_column": item.get("raw_header"), "canonical_field": item["canonical_field"], "semantic_field": item["canonical_field"], "confidence": "HIGH", "status": "DETECTED"} for item in df.attrs["header_structure"]["columns"]], "ignored_columns": [], "accounting_context": "TDS_RECEIVABLE_GROUP_SUMMARY", "warnings": [{"code": "GROUP_SUMMARY_NOT_VOUCHER_LEVEL", "message": "The report has account-group movements only; no line-level transaction dates, PAN, GSTIN, or voucher references were supplied.", "source_columns": []}]}
    # 26AS deposited is relevant to the established claimability workflow.
    # Sales + TDS + 26AS compares TDS Expected/Receivable only with tax
    # deducted, so this informational message is not applicable there.
    if workflow == "SALES_TDS_26AS" and kind == FORM26AS:
        report["issues"] = [issue for issue in report["issues"] if issue.get("field") != "tds_deposited"]
    report["source"] = {key: df.attrs[key] for key in ("sheet_name", "workbook_sheets", "workbook_engine", "header_row", "header_score", "header_detected", "non_empty_rows", "detected_file_type", "report_type", "source_granularity", "report_period", "financial_year_context", "header_structure", "control_totals") if key in df.attrs}
    detected_type = report["source"].get("detected_file_type")
    if kind == BOOKS and detected_type == "Government 26AS / Form 16A":
        detail = f"The selected sheet '{report['source'].get('sheet_name')}' contains a TAN-wise 26AS/Form 16A table, not a Books/ERP transaction table."
        report["status"] = "BLOCKED"
        report["checks"][0] = {"code": "file_detected", "label": "File detected", "passed": False, "detail": detail}
        report["issues"].insert(0, {
            "severity": "error", "file": FILE_LABELS[kind], "field": "source type",
            "problem": "26AS / Form 16A file uploaded as Books", "explanation": detail,
            "recommended_fix": "Upload the client Books/ERP TDS receivable export in the Books slot and place this TAN-wise tax-credit statement in the 26AS / Form 16A slot.",
            "rows": [], "row_count": 0,
        })
    elif not mapped and report["source"].get("non_empty_rows", 0):
        detail = f"{report['source']['non_empty_rows']} non-empty rows were found in sheet '{report['source'].get('sheet_name')}', but no recognised {FILE_LABELS[kind]} header row was detected in the first 500 rows."
        problem = "Transaction header not detected"
        fix = "Use a sheet containing the transaction table and ensure its column headers include accepted Books fields such as Customer/Party, Date and TDS Receivable/TDS."
        report["checks"][0] = {"code": "file_detected", "label": "File detected", "passed": False, "detail": detail}
        report["issues"] = [issue for issue in report["issues"] if issue["problem"] != "No data rows found"]
        report["issues"].insert(0, {
            "severity": "error", "file": FILE_LABELS[kind], "field": "header row",
            "problem": problem, "explanation": detail,
            "recommended_fix": fix,
            "rows": [], "row_count": 0,
        })
    if kind == FORM26AS and report["source"].get("detected_file_type") == "ERP TDS Receivable / Books Report":
        report["issues"].insert(0, {
            "severity": "error", "file": FILE_LABELS[kind], "field": "source type",
            "problem": "ERP Books report uploaded as 26AS / Form 16A",
            "explanation": "This report contains Books/ERP ledger fields such as TDS receivable, vouchers or particulars, but does not contain the TAN-wise government tax-credit data required for 26AS reconciliation.",
            "recommended_fix": "Upload this report in the Books slot and provide the genuine 26AS or Form 16A export in the 26AS / Form 16A slot.", "rows": [], "row_count": 0,
        })
    # The downstream Sales/TDS matcher accepts only positive, source-evidenced
    # receivable debit movements. Credit-only accounts remain visible in the
    # validation report for CA review and are never presented as TDS amounts.
    matchable_rows = [row for row in normalized if row.get("validation_state") != "UNAVAILABLE_FOR_MATCHING" and row.get("matching_eligible", True)]
    return report, [row for row in matchable_rows if row.get("tds_book_amount") is not None] if is_group_summary else matchable_rows


def _min_cols(kind, workflow: str | None = None):
    if kind == BOOKS and workflow == "FULL_RECONCILIATION":
        # An all-income ledger can establish account semantics without
        # asserting that its Ledger column is a customer identity.
        return ["document_date"]
    if workflow == "SALES_TDS_26AS":
        sales_requirements = {FORM26AS: ["deductor_name", "tan", "amount_paid", "tax_deducted", "financial_year"], TDS_RECEIVABLE: ["party_name", "tds_expected", "financial_year"], SALES_REGISTRY: ["customer_name", "invoice_date"]}
        if kind in sales_requirements:
            return sales_requirements[kind]
    return {BOOKS: ["customer_name", "document_date"], FORM26AS: ["tan", "transaction_date", "tax_deducted"], CUSTOMER_MASTER: ["customer_code", "customer_name"], TDS_RECEIVABLE: ["party_name", "tds_expected"], SALES_REGISTRY: ["customer_name", "invoice_date"]}[kind]


def run_sales_tds_26as_analysis(run_id: str, files: dict, expected_fy: str | None, settings: dict, progress):
    """Additive workflow; it intentionally bypasses Books identity/matcher/classifier."""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    progress("validate", "RUNNING", None)
    reports, data = {}, {}
    for kind in (TDS_RECEIVABLE, FORM26AS, SALES_REGISTRY):
        reports[kind], data[kind] = load_and_validate(kind, files[kind][0], files[kind][1], expected_fy, cfg.get("tds_rules"), "SALES_TDS_26AS")
    failed = [r for r in reports.values() if r["status"] == "BLOCKED"]
    if failed:
        progress("validate", "FAILED", f"{len(failed)} file(s) failed validation")
        raise PipelineError("Source files failed validation. Fix the reported issues and upload again.", {"validation": list(reports.values())})
    progress("validate", "COMPLETED", " · ".join(f"{r['label']}: {r['row_count']} rows" for r in reports.values()))
    progress("normalize", "COMPLETED", f"{len(data[SALES_REGISTRY])} Sales rows, {len(data[TDS_RECEIVABLE])} TDS rows, {len(data[FORM26AS])} 26AS rows")
    progress("resolve_identity", "RUNNING", "Resolving only source-evidenced Sales/TDS/TAN relationships")
    progress("match", "RUNNING", "Comparing Sales Amount to 26AS Amount Paid and TDS Receivable to 26AS TDS Deducted")
    out = run_sales_tds_26as(run_id, data[SALES_REGISTRY], data[TDS_RECEIVABLE], data[FORM26AS], cfg)
    counts = out["summary"]["identity_counts"]
    progress("resolve_identity", "COMPLETED", ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in counts.items()))
    progress("match", "COMPLETED", f"{out['summary']['amount_matched_count']} amount matches · {out['summary']['tds_matched_count']} TDS matches")
    progress("claimability", "COMPLETED", "Claimability is not reclassified by this independent workflow")
    progress("results", "COMPLETED", f"{len(out['results'])} result rows")
    return {"validation": list(reports.values()), **out, "settings": cfg}


def fill_customer_codes(books):
    for b in books:
        if not b["customer_code"]:
            # This is an internal grouping key, not an ERP customer code.  It
            # must be stable across rows for the same source customer; using
            # the row number here would break identity and matching grouping.
            key = normalize_code(b.get("customer_name_norm") or b.get("customer_name") or "")
            b["customer_code"] = f"SYS-CUST-{key[:32] or 'UNKNOWN'}"
            b["customer_code_source"] = "SYSTEM_GENERATED"
        else:
            b["customer_code_source"] = "SOURCE_PROVIDED"


def run_26as_analysis(run_id: str, files: dict, expected_fy: str | None, settings: dict, progress):
    """Standalone 26AS analysis; it deliberately does not invoke matcher.py."""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    progress("validate", "RUNNING", None)
    reports, data = {}, {}
    for kind in (FORM26AS, CUSTOMER_MASTER):
        if kind in files:
            reports[kind], data[kind] = load_and_validate(kind, files[kind][0], files[kind][1], expected_fy, cfg.get("tds_rules"))
    failed = [r for r in reports.values() if r["status"] == "BLOCKED"]
    if failed:
        progress("validate", "FAILED", f"{len(failed)} file(s) failed validation")
        raise PipelineError("Source files failed validation. Fix the reported issues and upload again.", {"validation": list(reports.values())})
    stmts = data[FORM26AS]
    progress("validate", "COMPLETED", f"26AS / Form 16A: {len(stmts)} rows")
    progress("normalize", "RUNNING", None)
    progress("normalize", "COMPLETED", f"{len(stmts)} 26AS rows")
    progress("resolve_identity", "COMPLETED", "Identity mapping is optional for 26AS-only analysis")
    progress("match", "COMPLETED", "Independent expected-TDS analysis; no Books matching performed")
    progress("claimability", "COMPLETED", "26AS booking status retained for review")
    progress("results", "RUNNING", None)
    rows = []
    for seq, statement in enumerate(stmts, start=1):
        expected = statement.get("tds_expected")
        deducted = statement.get("tax_deducted")
        if expected is None:
            status, difference = "NOT_DETERMINABLE", None
        else:
            difference = round(expected - (deducted or 0), 2)
            status = "CONSISTENT_WITH_CONFIGURED_RULE" if abs(difference) <= cfg["amount_tolerance_abs"] else "TDS_DIFFERENCE"
        rows.append({"id": f"{run_id}:{seq}", "run_id": run_id, "seq": seq, "source": "26AS", "analysis_mode": "26AS_ONLY", "transaction_id": statement["statement_id"], "customer": "", "customer_code": "", "party_pan": "", "party_gstin": "", "tan": statement["tan"], "deductor_name": statement["deductor_name"], "books_date": None, "statement_date": statement["transaction_date"], "financial_year": statement["financial_year"], "books_quarter": None, "statement_quarter": statement["quarter"], "section": statement["section"], "amount_paid": statement.get("amount_paid"), "tds_expected": expected, "tax_deducted": deducted, "tds_deposited": statement["tds_deposited"], "difference": difference, "difference_pct": None, "status": statement["status"], "analysis_status": status, "claimability": "NOT_APPLICABLE", "identity_status": "UNMAPPED", "match_method": "ANALYSIS_ONLY", "result": status, "reason": statement["tds_calculation_reason"], "recommended_action": "Configure an authoritative TDS rule or complete the source data." if status == "NOT_DETERMINABLE" else "Review the configured rule and the deductor's reported TDS." if status == "TDS_DIFFERENCE" else "No Books match is implied; this is only consistency with the configured rule.", "severity": "MEDIUM" if status == "TDS_DIFFERENCE" else "LOW" if status == "NOT_DETERMINABLE" else "NONE", "exception_category": "TDS_ANALYSIS" if status != "CONSISTENT_WITH_CONFIGURED_RULE" else None, "match_group_id": None, "group_size": 1, "books_group_total": None, "statement_group_total": deducted, "matched_books_ids": [], "matched_tans": [statement["tan"]], "books": None, "statement_entries": [statement]})
    expected_total = round(sum(r["tds_expected"] or 0 for r in rows), 2)
    deducted_total = round(sum(r["tax_deducted"] or 0 for r in rows), 2)
    summary = {"analysis_mode": "26AS_ONLY", "books_count": 0, "statement_count": len(stmts), "deductor_count": len({s["tan"] for s in stmts if s.get("tan")}), "result_count": len(rows), "amount_paid_total": round(sum(r.get("amount_paid") or 0 for r in rows), 2), "expected_tds_calculated": expected_total, "statement_tax_deducted": deducted_total, "difference": round(expected_total - deducted_total, 2), "consistent_count": sum(r["analysis_status"] == "CONSISTENT_WITH_CONFIGURED_RULE" for r in rows), "difference_count": sum(r["analysis_status"] == "TDS_DIFFERENCE" for r in rows), "not_determinable_count": sum(r["analysis_status"] == "NOT_DETERMINABLE" for r in rows)}
    progress("results", "COMPLETED", f"{len(rows)} analysis rows")
    return {"validation": list(reports.values()), "results": rows, "summary": summary, "identities": [], "customers": [], "settings": cfg}


def run_pipeline(run_id: str, files: dict, expected_fy: str | None, settings: dict, store, progress):
    """files: {kind: (filename, bytes)}. store exposes saved_mappings(), saved_aliases(), decisions(). progress(step, status, detail)."""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    progress("validate", "RUNNING", None)
    reports, data = {}, {}
    for kind in (BOOKS, FORM26AS, CUSTOMER_MASTER):
        if kind in files:
            reports[kind], data[kind] = load_and_validate(kind, files[kind][0], files[kind][1], expected_fy, cfg.get("tds_rules"), "FULL_RECONCILIATION")
    failed = [r for r in reports.values() if r["status"] == "BLOCKED"]
    if failed:
        progress("validate", "FAILED", f"{len(failed)} file(s) failed validation")
        raise PipelineError("Source files failed validation. Fix the reported issues and upload again.", {"validation": list(reports.values())})
    progress("validate", "COMPLETED", " · ".join(f"{r['label']}: {r['row_count']} rows" for r in reports.values()))

    progress("normalize", "RUNNING", None)
    books, stmts = data[BOOKS], data[FORM26AS]
    fill_customer_codes(books)
    customers = data.get(CUSTOMER_MASTER) or []
    master_codes = {c["customer_code"] for c in customers}
    customers = customers + [c for c in customers_from_books(books) if c["customer_code"] not in master_codes]
    progress("normalize", "COMPLETED", f"{len(books)} books rows, {len(stmts)} 26AS rows, {len({c['customer_code'] for c in customers})} customers")

    progress("resolve_identity", "RUNNING", None)
    identities, by_code = resolve_identities(stmts, customers, store.saved_mappings(), store.saved_aliases(), store.decisions(), cfg)
    ic = {}
    for i in identities.values():
        ic[i["status"]] = ic.get(i["status"], 0) + 1
    progress("resolve_identity", "COMPLETED", f"{len(identities)} TANs · " + ", ".join(f"{k.replace('_', ' ').title()} {v}" for k, v in ic.items()))

    progress("match", "RUNNING", None)
    matches, missing_26as, missing_books, unmapped_s, dup_b, dup_s = run_matching(books, stmts, identities, cfg)
    progress("match", "COMPLETED", f"{len(matches)} match groups · {len(missing_26as)} missing in 26AS · {len(missing_books)} missing in books · {len(unmapped_s)} unmapped")

    progress("claimability", "RUNNING", None)
    rows = build_results(run_id, matches, missing_26as, missing_books, unmapped_s, dup_b, dup_s, identities, by_code, cfg)
    progress("claimability", "COMPLETED", f"{sum(1 for r in rows if r['claimability'] == 'CLAIMABLE')} claimable · {sum(1 for r in rows if r['claimability'] == 'NOT_CLAIMABLE')} not claimable")

    progress("results", "RUNNING", None)
    summary = build_summary(rows, books, stmts, identities)
    progress("results", "COMPLETED", f"{len(rows)} result rows")
    return {"validation": list(reports.values()), "results": rows, "summary": summary, "identities": list(identities.values()), "customers": list(by_code.values()), "settings": cfg}
