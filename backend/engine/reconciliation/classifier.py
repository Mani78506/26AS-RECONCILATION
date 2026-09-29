from .identity import EXACT, HIGH_CONFIDENCE, REVIEW_REQUIRED
from .matcher import (AMOUNT_MISMATCH, CLAIMABLE, DUPLICATE_26AS, DUPLICATE_BOOK, IDENTITY_UNMAPPED, MATCHED_CLAIMABLE, MATCHED_NOT_CLAIMABLE, MISSING_IN_26AS, MISSING_IN_BOOKS, NOT_APPLICABLE, NOT_CLAIMABLE)

STATUS_LABELS = {"F": "Final", "U": "Unmatched", "P": "Provisional", "O": "Overbooked"}
CATEGORY = {AMOUNT_MISMATCH: "AMOUNT_MISMATCH", MISSING_IN_BOOKS: "MISSING_IN_BOOKS", MISSING_IN_26AS: "MISSING_IN_26AS", IDENTITY_UNMAPPED: "IDENTITY", DUPLICATE_BOOK: "DUPLICATES", DUPLICATE_26AS: "DUPLICATES", MATCHED_NOT_CLAIMABLE: "NOT_CLAIMABLE"}


def inr(v):
    if v is None:
        return "—"
    neg = v < 0
    s = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    whole, _, frac = s.partition(".")
    whole = whole.replace(",", "")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{'-' if neg else ''}₹{whole}{'.' + frac if frac else ''}"


def _books_fields(b):
    return {"transaction_id": b["transaction_id"], "transaction_id_source": b.get("transaction_id_source", "SOURCE_PROVIDED"), "customer_name": b["customer_name"], "customer_code": b["customer_code"], "customer_code_source": b.get("customer_code_source", "SOURCE_PROVIDED"), "party_pan": b["party_pan"], "party_gstin": b["party_gstin"], "document_number": b["document_number"], "document_date": b["document_date"], "taxable_value": b["taxable_value"], "gst_value": b["gst_value"], "tds_expected": b["tds_expected"], "section": b["section"], "advance": b["advance"], "financial_year": b["financial_year"], "quarter": b["quarter"], "row_no": b["row_no"], **{key: b.get(key) for key in ("tds_rate", "tds_calculation_method", "tds_calculation_status", "tds_calculation_basis", "tds_calculation_reason", "tds_rule_id", "tds_rule_version", "tds_rule_source", "tds_rule_effective_from", "tds_rule_effective_to")}}


def _stmt_fields(s):
    return {"statement_id": s["statement_id"], "statement_id_source": s.get("statement_id_source", "SOURCE_PROVIDED"), "tan": s["tan"], "deductor_name": s["deductor_name"], "transaction_date": s["transaction_date"], "tax_deducted": s["tax_deducted"], "tds_deposited": s["tds_deposited"], "status": s["status"], "status_label": STATUS_LABELS.get(s["status"], s["status"]), "section": s["section"], "amount_paid": s["amount_paid"], "financial_year": s["financial_year"], "quarter": s["quarter"], "row_no": s["row_no"], **{key: s.get(key) for key in ("tds_expected", "tds_rate", "tds_calculation_method", "tds_calculation_status", "tds_calculation_basis", "tds_calculation_reason", "tds_rule_id", "tds_rule_version", "tds_rule_source", "tds_rule_effective_from", "tds_rule_effective_to")}}


def _claimability(stmts, cfg):
    bad_status = [s for s in stmts if s["status"] != "F"]
    unreported_deposit = [s for s in stmts if s.get("tds_deposited") is None]
    deducted = sum(s["tax_deducted"] or 0 for s in stmts)
    deposited = sum(s["tds_deposited"] or 0 for s in stmts)
    if bad_status:
        labels = sorted({STATUS_LABELS.get(s["status"], s["status"]) for s in bad_status})
        return NOT_CLAIMABLE, f"26AS booking status is {'/'.join(labels)} — credit is not final until the deductor's statement is processed."
    if unreported_deposit:
        return NOT_CLAIMABLE, "TDS Deposited was not reported for one or more linked 26AS entries, so deposited-credit claimability cannot be evidenced from this source."
    if deposited + cfg["amount_tolerance_abs"] < deducted:
        return NOT_CLAIMABLE, f"Only {inr(deposited)} of {inr(deducted)} deducted has been deposited by the deductor."
    return CLAIMABLE, None


def _base(run_id, seq, customer, identity_status):
    return {"id": f"{run_id}:{seq}", "run_id": run_id, "seq": seq, "customer": customer.get("customer_name", "") if customer else "", "customer_code": customer.get("customer_code", "") if customer else "", "party_pan": customer.get("pan", "") if customer else "", "party_gstin": customer.get("gstin", "") if customer else "", "identity_status": identity_status, "match_group_id": None, "group_size": 1, "books_group_total": None, "statement_group_total": None, "matched_books_ids": [], "matched_tans": [], "books": None, "statement_entries": []}


def build_results(run_id, matches, missing_26as, missing_books, unmapped_s, dup_b, dup_s, identities, customers, cfg):
    rows, seq = [], 0

    def nxt():
        nonlocal seq
        seq += 1
        return seq

    def cust_for_books(b):
        c = customers.get(b["customer_code"]) or {}
        return {"customer_name": c.get("customer_name") or b["customer_name"], "customer_code": b["customer_code"], "pan": c.get("pan") or b["party_pan"], "gstin": c.get("gstin") or b["party_gstin"]}

    def cust_for_stmt(s):
        ident = identities[s["tan"]]
        return {"customer_name": ident["customer_name"] or "", "customer_code": ident["customer_code"] or "", "pan": ident.get("customer_pan") or "", "gstin": ident.get("customer_gstin") or ""}, ident["status"]

    for m in matches:
        bs, ss = m["books"], m["stmts"]
        b_total = round(sum(b["tds_expected"] or 0 for b in bs), 2)
        s_total = round(sum(s["tax_deducted"] or 0 for s in ss), 2)
        dep_total = round(sum(s["tds_deposited"] or 0 for s in ss), 2)
        diff = round(b_total - s_total, 2)
        pct = round(diff / b_total * 100, 2) if b_total else None
        tans = sorted({s["tan"] for s in ss})
        ident_status = min((identities[s["tan"]]["status"] for s in ss), key=lambda x: 0 if x == EXACT else 1)
        same_q = all(s["quarter"] == bs[0]["quarter"] for s in ss) and all(b["quarter"] == bs[0]["quarter"] for b in bs)
        if m["method"] == AMOUNT_MISMATCH:
            result, method, claim = AMOUNT_MISMATCH, "UNMATCHED", NOT_APPLICABLE
            reason = f"Books expects {inr(b_total)} but the closest 26AS entry from TAN {tans[0]} shows {inr(s_total)} ({'same' if same_q else 'different'} quarter). Difference {inr(diff)} exceeds the tolerance of {inr(cfg['amount_tolerance_abs'])} / {cfg['amount_tolerance_pct']}%."
            action = "Obtain Form 16A from the deductor and confirm the TDS actually deducted. If the deductor short-deducted, request a correction statement; otherwise adjust books." if diff > 0 else "26AS shows more TDS than expected in books. Verify the invoice value and TDS rate recorded in books before claiming the higher credit."
            severity = "HIGH" if pct is not None and abs(pct) > 10 else "MEDIUM"
        else:
            claim, claim_reason = _claimability(ss, cfg)
            result = MATCHED_CLAIMABLE if claim == CLAIMABLE else MATCHED_NOT_CLAIMABLE
            method = m["method"]
            shape = "one books transaction with one 26AS entry" if len(bs) == 1 and len(ss) == 1 else f"{len(bs)} books transaction{'s' if len(bs) > 1 else ''} with {len(ss)} 26AS entr{'ies' if len(ss) > 1 else 'y'}" + (f" across {len(tans)} TANs" if len(tans) > 1 else "")
            q_text = "in the same quarter" if same_q else "across different quarters of the financial year"
            reason = f"Matched {shape} {q_text}: books {inr(b_total)} vs 26AS {inr(s_total)}" + (f" (difference {inr(diff)} within tolerance)." if diff else " with zero difference.")
            if claim == CLAIMABLE:
                action = "Claim the TDS credit in the return. Keep invoice, ledger extract and 26AS extract in the working papers."
                severity = "NONE"
            else:
                reason += f" {claim_reason}"
                action = "Do not claim yet. Follow up with the deductor to file/correct the TDS statement, then re-run reconciliation."
                severity = "MEDIUM"
        for b in bs:
            row = _base(run_id, nxt(), cust_for_books(b), ident_status)
            single = ss[0] if len(ss) == 1 else None
            row.update({
                "transaction_id": b["transaction_id"], "source": "BOOKS", "tan": tans[0] if len(tans) == 1 else f"{tans[0]} +{len(tans) - 1}", "deductor_name": ss[0]["deductor_name"] if len({s['deductor_name'] for s in ss}) == 1 else f"{len(tans)} deductors",
                "books_date": b["document_date"], "statement_date": single["transaction_date"] if single else (min(s["transaction_date"] for s in ss if s["transaction_date"]) if any(s["transaction_date"] for s in ss) else None),
                "financial_year": b["financial_year"], "books_quarter": b["quarter"], "statement_quarter": ss[0]["quarter"] if len({s["quarter"] for s in ss}) == 1 else "Multiple",
                "section": b["section"] or ss[0]["section"], "tds_expected": b["tds_expected"], "tax_deducted": s_total, "tds_deposited": dep_total,
                "difference": diff, "difference_pct": pct, "status": "".join(sorted({s["status"] for s in ss})), "claimability": claim, "match_method": method, "result": result, "reason": reason, "recommended_action": action,
                "severity": severity, "exception_category": CATEGORY.get(result), "match_group_id": m["group_id"], "group_size": len(bs) + len(ss), "books_group_total": b_total, "statement_group_total": s_total,
                "matched_books_ids": [x["transaction_id"] for x in bs], "matched_tans": tans, "books": _books_fields(b), "statement_entries": [_stmt_fields(s) for s in ss],
            })
            rows.append(row)

    for b in missing_26as:
        row = _base(run_id, nxt(), cust_for_books(b), EXACT)
        row.update({"transaction_id": b["transaction_id"], "source": "BOOKS", "tan": "", "deductor_name": "", "books_date": b["document_date"], "statement_date": None, "financial_year": b["financial_year"], "books_quarter": b["quarter"], "statement_quarter": None, "section": b["section"], "tds_expected": b["tds_expected"], "tax_deducted": None, "tds_deposited": None, "difference": b["tds_expected"], "difference_pct": 100.0 if b["tds_expected"] else None, "status": "", "claimability": NOT_APPLICABLE, "match_method": "UNMATCHED", "result": MISSING_IN_26AS,
                    "reason": f"Books records TDS of {inr(b['tds_expected'])} for {b['customer_name']} ({b['quarter']} {b['financial_year']}) but no unconsumed 26AS entry from a mapped TAN of this customer matches, alone or in a group.",
                    "recommended_action": "Request Form 16A / TDS certificate from the customer and ask them to file or correct their TDS return. Re-run after the next 26AS refresh.", "severity": "HIGH", "exception_category": CATEGORY[MISSING_IN_26AS], "books_group_total": b["tds_expected"], "statement_group_total": 0, "books": _books_fields(b)})
        rows.append(row)

    for s in missing_books:
        cust, ident_status = cust_for_stmt(s)
        row = _base(run_id, nxt(), cust, ident_status)
        row.update({"transaction_id": s["statement_id"], "source": "26AS", "tan": s["tan"], "deductor_name": s["deductor_name"], "books_date": None, "statement_date": s["transaction_date"], "financial_year": s["financial_year"], "books_quarter": None, "statement_quarter": s["quarter"], "section": s["section"], "tds_expected": None, "tax_deducted": s["tax_deducted"], "tds_deposited": s["tds_deposited"], "difference": -(s["tax_deducted"] or 0), "difference_pct": None, "status": s["status"], "claimability": NOT_APPLICABLE, "match_method": "UNMATCHED", "result": MISSING_IN_BOOKS,
                    "reason": f"26AS shows TDS of {inr(s['tax_deducted'])} deducted by {s['deductor_name']} (TAN {s['tan']}, {s['quarter']}) mapped to customer {cust['customer_code']}, but no books transaction remains to absorb it.",
                    "recommended_action": "Check whether the invoice/receipt is missing from books or booked under a different customer or period. Record the income and TDS receivable if genuine.", "severity": "MEDIUM", "exception_category": CATEGORY[MISSING_IN_BOOKS], "matched_tans": [s["tan"]], "books_group_total": 0, "statement_group_total": s["tax_deducted"], "statement_entries": [_stmt_fields(s)]})
        rows.append(row)

    for s in unmapped_s:
        ident = identities[s["tan"]]
        row = _base(run_id, nxt(), None, ident["status"])
        suggestion = f" Closest customer: {ident['suggested_customer_name']} ({ident['score']:.0%} similarity) — awaiting CA confirmation." if ident["status"] == REVIEW_REQUIRED and ident.get("suggested_customer_name") else ""
        row.update({"transaction_id": s["statement_id"], "source": "26AS", "tan": s["tan"], "deductor_name": s["deductor_name"], "books_date": None, "statement_date": s["transaction_date"], "financial_year": s["financial_year"], "books_quarter": None, "statement_quarter": s["quarter"], "section": s["section"], "tds_expected": None, "tax_deducted": s["tax_deducted"], "tds_deposited": s["tds_deposited"], "difference": -(s["tax_deducted"] or 0), "difference_pct": None, "status": s["status"], "claimability": NOT_APPLICABLE, "match_method": "UNMATCHED", "result": IDENTITY_UNMAPPED,
                    "reason": f"Deductor '{s['deductor_name']}' (TAN {s['tan']}) could not be mapped to any customer in books. TAN is not in the Customer Master or saved mappings.{suggestion}",
                    "recommended_action": "Resolve the deductor in Identity Review: confirm the suggested customer, search for the right customer, or keep unmapped. Matching re-runs automatically after confirmation.", "severity": "HIGH", "exception_category": CATEGORY[IDENTITY_UNMAPPED], "matched_tans": [s["tan"]], "statement_group_total": s["tax_deducted"], "statement_entries": [_stmt_fields(s)]})
        rows.append(row)

    for b in dup_b:
        row = _base(run_id, nxt(), cust_for_books(b), EXACT)
        row.update({"transaction_id": b["transaction_id"], "source": "BOOKS", "tan": "", "deductor_name": "", "books_date": b["document_date"], "statement_date": None, "financial_year": b["financial_year"], "books_quarter": b["quarter"], "statement_quarter": None, "section": b["section"], "tds_expected": b["tds_expected"], "tax_deducted": None, "tds_deposited": None, "difference": b["tds_expected"], "difference_pct": None, "status": "", "claimability": NOT_APPLICABLE, "match_method": "UNMATCHED", "result": DUPLICATE_BOOK,
                    "reason": f"Duplicate of books transaction {b['duplicate_of']}: same customer, document, date and TDS amount. Excluded from matching to avoid double-claiming.",
                    "recommended_action": "Verify in the ledger and reverse the duplicate entry if confirmed.", "severity": "LOW", "exception_category": CATEGORY[DUPLICATE_BOOK], "books": _books_fields(b), "matched_books_ids": [b["duplicate_of"]]})
        rows.append(row)

    for s in dup_s:
        cust, ident_status = cust_for_stmt(s)
        row = _base(run_id, nxt(), cust, ident_status)
        row.update({"transaction_id": s["statement_id"], "source": "26AS", "tan": s["tan"], "deductor_name": s["deductor_name"], "books_date": None, "statement_date": s["transaction_date"], "financial_year": s["financial_year"], "books_quarter": None, "statement_quarter": s["quarter"], "section": s["section"], "tds_expected": None, "tax_deducted": s["tax_deducted"], "tds_deposited": s["tds_deposited"], "difference": -(s["tax_deducted"] or 0), "difference_pct": None, "status": s["status"], "claimability": NOT_APPLICABLE, "match_method": "UNMATCHED", "result": DUPLICATE_26AS,
                    "reason": f"Duplicate of 26AS entry {s['duplicate_of']}: identical TAN, date, section, amounts and status. Excluded from matching.",
                    "recommended_action": "Check whether 26AS was downloaded twice or the deductor filed the same entry twice; do not claim both.", "severity": "LOW", "exception_category": CATEGORY[DUPLICATE_26AS], "matched_tans": [s["tan"]], "statement_entries": [_stmt_fields(s)]})
        rows.append(row)
    return rows


def build_summary(rows, books, stmts, identities):
    books_total = round(sum(b["tds_expected"] or 0 for b in books), 2)
    deducted_total = round(sum(s["tax_deducted"] or 0 for s in stmts), 2)
    deposited_total = round(sum(s["tds_deposited"] or 0 for s in stmts), 2)
    matched_rows = [r for r in rows if r["result"] in (MATCHED_CLAIMABLE, MATCHED_NOT_CLAIMABLE)]
    matched_books = round(sum(r["tds_expected"] or 0 for r in matched_rows), 2)
    matched_groups = {r["match_group_id"] or r["id"]: r for r in matched_rows}
    matched_statement = round(sum(r["statement_group_total"] or 0 for r in matched_groups.values()), 2)
    counts = {t: 0 for t in ["MATCHED_CLAIMABLE", "MATCHED_NOT_CLAIMABLE", "AMOUNT_MISMATCH", "MISSING_IN_BOOKS", "MISSING_IN_26AS", "IDENTITY_UNMAPPED", "DUPLICATE_BOOK", "DUPLICATE_26AS"]}
    methods = {m: 0 for m in ["EXACT_1_TO_1_SAME_QUARTER", "EXACT_1_TO_1_OUTSIDE_QUARTER", "GROUP_SAME_QUARTER", "GROUP_OUTSIDE_QUARTER", "UNMATCHED"]}
    by_quarter = {}
    for r in rows:
        counts[r["result"]] += 1
        methods[r["match_method"]] += 1
        q = r["books_quarter"] or r["statement_quarter"] or "—"
        bucket = by_quarter.setdefault(q, {"quarter": q, "books_tds": 0, "statement_tds": 0, "matched": 0, "exceptions": 0})
        bucket["books_tds"] = round(bucket["books_tds"] + (r["tds_expected"] or 0), 2)
        bucket["statement_tds"] = round(bucket["statement_tds"] + (r["tax_deducted"] or 0 if r["source"] == "26AS" or r["group_size"] == 2 else 0), 2)
        bucket["matched" if r["result"] in (MATCHED_CLAIMABLE, MATCHED_NOT_CLAIMABLE) else "exceptions"] += 1
    ident_counts = {"EXACT": 0, "HIGH_CONFIDENCE": 0, "REVIEW_REQUIRED": 0, "UNMAPPED": 0}
    for i in identities.values():
        ident_counts[i["status"]] += 1
    return {
        "books_count": len(books), "statement_count": len(stmts), "result_count": len(rows),
        "matched_claimable_count": counts["MATCHED_CLAIMABLE"], "matched_not_claimable_count": counts["MATCHED_NOT_CLAIMABLE"],
        "exceptions_count": sum(1 for r in rows if r["result"] != MATCHED_CLAIMABLE), "identity_review_count": ident_counts["HIGH_CONFIDENCE"] + ident_counts["REVIEW_REQUIRED"] + ident_counts["UNMAPPED"],
        "reconciliation_percentage": round(matched_books / books_total * 100, 1) if books_total else 0.0,
        "coverage_percentage": round(matched_statement / deducted_total * 100, 1) if deducted_total else 0.0,
        "books_tds_expected": books_total, "statement_tax_deducted": deducted_total, "statement_tds_deposited": deposited_total,
        "matched_amount": matched_books, "matched_claimable_amount": round(sum(r["tds_expected"] or 0 for r in rows if r["result"] == MATCHED_CLAIMABLE), 2),
        "matched_not_claimable_amount": round(sum(r["tds_expected"] or 0 for r in rows if r["result"] == MATCHED_NOT_CLAIMABLE), 2),
        "difference": round(books_total - deducted_total, 2), "result_counts": counts, "match_method_counts": methods,
        "identity_counts": ident_counts, "by_quarter": sorted(by_quarter.values(), key=lambda x: x["quarter"]),
        "severity_counts": {k: sum(1 for r in rows if r["severity"] == k) for k in ("HIGH", "MEDIUM", "LOW")},
    }
