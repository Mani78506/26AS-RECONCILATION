"""Relationship construction for the isolated Sales + TDS + 26AS workflow.

The component graph is built before reconciliation.  Therefore a name-only
candidate relationship remains one auditable REVIEW_REQUIRED relationship,
rather than becoming separate Sales-only and 26AS-only rows.
"""
from collections import defaultdict
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
import re

from .normalizer import core_name

_IDENTITY_STOPWORDS = {"A", "AN", "AND", "M", "MEDICAL", "HEALTHCARE", "HOSPITAL", "OF", "P", "PRIVATE", "S", "SCIENCE", "SERVICES", "THE", "UNIT", "INSTITUTE", "LIMITED"}


def _compact_identity_name(value):
    """Compare punctuation/spacing variants only; this never expands abbreviations."""
    return re.sub(r"[^A-Z0-9]+", "", core_name(value))


def _difference(left, right):
    return None if left is None or right is None else round(left - right, 2)


def _within(left, right, cfg):
    if left is None or right is None:
        return False
    delta = abs(left - right)
    return delta <= cfg["amount_tolerance_abs"] or delta <= max(abs(left), abs(right), 1) * cfg["amount_tolerance_pct"] / 100


def _sum(rows, field):
    values = [row.get(field) for row in rows]
    return None if not rows or any(value is None for value in values) else round(sum(values), 2)


def _sales_tds_evidence(sale, tds):
    if sale.get("customer_pan") and sale.get("customer_pan") == tds.get("party_pan"):
        return "EXACT_PAN"
    if sale.get("customer_gstin") and sale.get("customer_gstin") == tds.get("party_gstin"):
        return "EXACT_GSTIN"
    sale_refs = {str(sale.get(key) or "").strip() for key in ("invoice_number", "reference", "sales_transaction_id")}
    tds_refs = {str(tds.get(key) or "").strip() for key in ("reference", "tds_transaction_id")}
    if (sale_refs - {""}) & (tds_refs - {""}):
        return "EXACT_REFERENCE"
    if sale.get("customer_name_norm") and sale.get("customer_name_norm") == tds.get("party_name_norm"):
        return "EXACT_NORMALIZED_NAME"
    return None


def _tds_statement_evidence(tds, statement):
    if tds.get("tan") and tds.get("tan") == statement.get("tan"):
        return "EXACT_TAN"
    statement_name = statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", ""))
    if tds.get("party_name_norm") and tds.get("party_name_norm") == statement_name:
        # A supplied, different TDS TAN is contradictory evidence and must not
        # be masked by a same-name fallback.
        return "CONFLICTING_TAN" if tds.get("tan") and tds.get("tan") != statement.get("tan") else "EXACT_TDS_DEDUCTOR_NAME"
    if tds.get("party_name") and _compact_identity_name(tds.get("party_name")) == _compact_identity_name(statement.get("deductor_name", "")):
        return "COMPACT_NORMALIZED_NAME"
    return None


def _tds_statement_review_candidate(tds, statement):
    """Return a controlled identity lead without confirming a match.

    A candidate is never promoted by amount equality. Acronyms are review-only:
    useful accounting leads, but insufficient legal-entity identity evidence.
    """
    statement_norm = statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", ""))
    ledger_norm = tds.get("party_name_norm") or core_name(tds.get("party_name", ""))
    statement_tokens = set(statement_norm.split())
    ledger_tokens = set(ledger_norm.split())
    common = statement_tokens & ledger_tokens
    if len(common) >= 2:
        return "PARTIAL_NORMALIZED_NAME"
    if ledger_tokens and ledger_tokens <= statement_tokens:
        return "NAME_PREFIX_VARIANT"
    # A leading ledger acronym may provide a review candidate for a legal-name
    # family. It is never an automatic expansion or a confirmed identity.
    ignored = {"A", "AN", "AND", "OF", "THE", "PRIVATE", "LIMITED", "PVT", "LTD"}
    meaningful = [token for token in statement_norm.split() if token not in ignored and len(token) > 1]
    acronym = "".join(token[0] for token in meaningful if token)
    ledger_lead = (ledger_norm.split() or [""])[0]
    if 3 <= len(ledger_lead) <= 8 and acronym.startswith(ledger_lead):
        return "ACRONYM_FAMILY_CANDIDATE"
    return None


def _sales_statement_evidence(sale, statement):
    """A direct, exact-name candidate bridge; TAN/PAN are never inferred."""
    statement_name = statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", ""))
    if sale.get("customer_pan") and sale.get("customer_pan") == statement.get("deductor_pan"):
        return "26AS_EXPLICIT_PAN"
    if sale.get("customer_name_norm") and sale.get("customer_name_norm") == statement_name:
        return "EXACT_SALES_DEDUCTOR_NAME"
    if sale.get("customer_name") and _compact_identity_name(sale.get("customer_name")) == _compact_identity_name(statement.get("deductor_name", "")):
        return "COMPACT_NORMALIZED_SALES_NAME"
    return None


def _sales_statement_review_candidate(sale, statement):
    """Identify a reviewable name relationship without linking invoices."""
    statement_tokens = {token for token in (statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", ""))).split() if token not in _IDENTITY_STOPWORDS}
    sales_tokens = {token for token in (sale.get("customer_name_norm") or core_name(sale.get("customer_name", ""))).split() if token not in _IDENTITY_STOPWORDS}
    shared_tokens = statement_tokens & sales_tokens
    # Source exports can truncate a legal name after a clear embedded business
    # phrase, such as "A unit of <deductor name>". Match its ordered leading
    # business words as a review lead only; it never confirms a legal identity.
    ignored = {"A", "AN", "AND", "OF", "THE", "PRIVATE", "LIMITED", "PVT", "LTD", "UNIT"}
    statement_words = [word for word in (statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", ""))).split() if word not in ignored]
    sales_words = [word for word in (sale.get("customer_name_norm") or core_name(sale.get("customer_name", ""))).split() if word not in ignored]
    cursor = 0
    matched = 0
    for target in statement_words:
        for index in range(cursor, len(sales_words)):
            candidate = sales_words[index]
            if candidate == target or (min(len(candidate), len(target)) >= 5 and (candidate.startswith(target) or target.startswith(candidate))):
                matched += 1
                cursor = index + 1
                break
        else:
            break
    if matched >= 2 and statement_words and sales_words and statement_words[0] == sales_words[0]:
        return "EMBEDDED_DEDUCTOR_NAME_VARIANT"
    # A single distinctive legal-name token can be a review lead, but generic
    # healthcare/accounting terms are deliberately excluded above.
    if len(shared_tokens) >= 2 or any(len(token) >= 6 for token in shared_tokens):
        return "PARTIAL_NORMALIZED_SALES_NAME"
    return None


class _UnionFind:
    def __init__(self, nodes):
        self.parent = {node: node for node in nodes}

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _components(sales, tds, statements):
    """Return FY-bounded connected source components and their evidence."""
    nodes = {f"S:{row['sales_transaction_id']}": ("sales", row) for row in sales}
    nodes.update({f"T:{row['tds_transaction_id']}": ("tds", row) for row in tds})
    nodes.update({f"A:{row['statement_id']}": ("statement", row) for row in statements})
    uf, edges = _UnionFind(nodes), defaultdict(list)
    for sale in sales:
        for tds_row in tds:
            evidence = _sales_tds_evidence(sale, tds_row)
            if evidence:
                left, right = f"S:{sale['sales_transaction_id']}", f"T:{tds_row['tds_transaction_id']}"
                if evidence != "CONFLICTING_TAN":
                    uf.join(left, right)
                edges[(left, right)].append(evidence)
    for tds_row in tds:
        for statement in statements:
            evidence = _tds_statement_evidence(tds_row, statement)
            if evidence:
                left, right = f"T:{tds_row['tds_transaction_id']}", f"A:{statement['statement_id']}"
                if evidence != "CONFLICTING_TAN":
                    uf.join(left, right)
                edges[(left, right)].append(evidence)
    # A direct Sales/26AS bridge is valuable when a group-summary TDS ledger
    # does not name every deductor. Exact names create a reviewable candidate;
    # only an explicitly supplied PAN is authoritative.
    for sale in sales:
        for statement in statements:
            evidence = _sales_statement_evidence(sale, statement)
            if evidence:
                left, right = f"S:{sale['sales_transaction_id']}", f"A:{statement['statement_id']}"
                uf.join(left, right); edges[(left, right)].append(evidence)
    grouped = defaultdict(lambda: {"sales": [], "tds": [], "statements": [], "evidence": []})
    for node, (kind, row) in nodes.items():
        grouped[uf.find(node)]["statements" if kind == "statement" else kind].append(row)
    for (left, right), values in edges.items():
        root_left, root_right = uf.find(left), uf.find(right)
        if root_left == root_right:
            grouped[root_left]["evidence"].extend(values)
    return list(grouped.values())


def _identity(component):
    sales, tds, statements, evidence = component["sales"], component["tds"], component["statements"], component["evidence"]
    if "CONFLICTING_TAN" in evidence:
        return "CONFLICT", "CONFLICTING_TAN", "A supplied TDS TAN conflicts with a same-name 26AS deductor TAN."
    # In the 26AS-first workflow, name + TAN + FY is a source-provided
    # primary identity.  Absence of a supporting Sales or TDS ledger record
    # is a reconciliation gap, not an identity failure.
    if component.get("primary_26as_anchor") and statements and (not sales or not tds):
        return "CONFIRMED", "26AS_SOURCE_TAN_NAME_FY", "The primary deductor identity is explicitly supplied by the 26AS statement; a supporting source counterpart is missing."
    authoritative_sales = {"EXACT_PAN", "EXACT_GSTIN", "EXACT_REFERENCE", "26AS_EXPLICIT_PAN"} & set(evidence)
    authoritative_tds = "EXACT_TAN" in evidence
    if sales and statements and "26AS_EXPLICIT_PAN" in evidence:
        return "CONFIRMED", "26AS_EXPLICIT_PAN", "The 26AS source explicitly supplies the Sales customer PAN."
    if sales and statements and not tds and "EXACT_SALES_DEDUCTOR_NAME" in evidence:
        return "REVIEW_REQUIRED", "EXACT_SALES_DEDUCTOR_NAME", "The Sales customer and 26AS deductor have the same normalized business name, but no TDS Ledger account-group evidence connects them. CA review is required."
    if not sales or not tds or not statements:
        return "UNMAPPED", "INCOMPLETE_RELATIONSHIP", "No complete same-financial-year Sales, TDS and 26AS candidate relationship was found."
    if "26AS_EXPLICIT_PAN" in evidence or (authoritative_sales and (authoritative_tds or "EXACT_TDS_DEDUCTOR_NAME" in evidence)):
        method = "TDS_PAN_TO_TAN" if "EXACT_PAN" in evidence and "EXACT_TAN" in evidence else "__".join(sorted(set(evidence)))
        return "CONFIRMED", method, "The cross-source relationship is supported by authoritative identifiers supplied in the source files."
    if component.get("allow_exact_name_confirmation"):
        return "CONFIRMED", "EXACT_NAME_POLICY", "Exact source names establish the relationship under the configured review policy."
    return "REVIEW_REQUIRED", "__".join(sorted(set(evidence))) or "NONE", "Exact source names establish a candidate relationship, but no authoritative common identifier was supplied. CA confirmation is required."


def _comparison(left_rows, left_value, right_rows, right_value, missing_right, missing_left, not_determinable, matched, difference, cfg):
    """Classify a numeric comparison without consulting identity status."""
    left_total, right_total = _sum(left_rows, left_value), _sum(right_rows, right_value)
    if left_rows and not right_rows:
        return left_total, right_total, _difference(left_total, right_total), missing_right
    if right_rows and not left_rows:
        return left_total, right_total, _difference(left_total, right_total), missing_left
    if left_total is None or right_total is None:
        return left_total, right_total, _difference(left_total, right_total), not_determinable
    delta = _difference(left_total, right_total)
    return left_total, right_total, delta, matched if _within(left_total, right_total, cfg) else difference


def _tds_reconciliation_status(status, candidates=None):
    """Expose the CA-facing TDS outcome without changing the existing API status."""
    if status == "MISSING_IN_TDS":
        return "REVIEW_REQUIRED" if candidates else "MISSING_TDS_LEDGER_COUNTERPART"
    return {
        "TDS_MATCHED": "MATCHED",
        "TDS_DIFFERENCE": "DIFFERENCE",
        "TDS_NOT_DETERMINABLE": "NOT_DETERMINABLE",
    }.get(status, "REVIEW_REQUIRED")


def _sales_counterpart(sales, candidates, statements, cfg):
    """Resolve Sales evidence using the configured canonical Taxable Value basis."""
    basis = cfg.get("sales_amount_basis", "taxable_value")
    label = "Taxable Value" if basis == "taxable_value" else "Unavailable"
    statement_amount = _sum(statements, "amount_paid")

    def amount_for(rows):
        return _sum(rows, "taxable_amount") if basis == "taxable_value" else None

    def base_fields(rows):
        return {
            "invoice_value_total": _sum(rows, "invoice_value"),
            "taxable_value_total": _sum(rows, "taxable_amount"),
            "tax_components": {key: _sum(rows, key) for key in ("igst_amount", "cgst_amount", "sgst_amount", "cess_amount")},
            "sales_amount_basis": basis,
            "sales_amount_basis_label": label,
            "sales_amount_source_field": "taxable_value" if basis == "taxable_value" else None,
        }

    if not statements:
        sale = sales[0] if sales else {}
        amount = amount_for(sales)
        return {
            "status": "NOT_DETERMINABLE", "identity_status": "UNMAPPED", "identity_method": "NONE", "identity_confidence": 0,
            "identity_evidence": [], "customer_name": sale.get("customer_name") or None, "customer_gstin": sale.get("customer_gstin") or None,
            "invoice_count": len(sales), "source_record_ids": [row["sales_transaction_id"] for row in sales],
            **base_fields(sales), "amount_basis": basis, "amount": amount, "difference": None, "amount_status": "MISSING_IN_26AS" if sales and amount is not None else "AMOUNT_NOT_DETERMINABLE",
            "review_reason": "No 26AS deductor is available in this source-diagnostic workflow.",
        }
    if not sales and not candidates:
        return {
            "status": "MISSING_SALES_COUNTERPART", "identity_status": "MISSING_SALES_COUNTERPART", "identity_method": "NONE", "identity_confidence": 0,
            "identity_evidence": [], "customer_name": None, "customer_gstin": None, "invoice_count": 0, "source_record_ids": [],
            **base_fields([]), "amount_basis": basis, "amount": None, "difference": None, "amount_status": "MISSING_IN_SALES",
            "review_reason": "No same-financial-year Sales Registry counterpart was identified from source evidence.",
        }
    if not sales:
        grouped = {}
        for item in candidates:
            key = (item.get("customer_name") or "", item.get("customer_gstin") or "")
            grouped.setdefault(key, []).append(item)
        candidate_groups = list(grouped.values())
        evidence_rank = {"EMBEDDED_DEDUCTOR_NAME_VARIANT": 2, "PARTIAL_NORMALIZED_SALES_NAME": 1}
        group_rank = [max(evidence_rank.get(item["sales_identity_candidate_evidence"], 0) for item in rows) for rows in candidate_groups]
        strongest = max(group_rank, default=0)
        strongest_groups = [rows for rows, rank in zip(candidate_groups, group_rank) if rank == strongest]
        # A single highest-evidence customer can be displayed as a review
        # aggregate. It is not confirmed and amount equality is never used.
        safe_candidate_rows = strongest_groups[0] if strongest >= 2 and len(strongest_groups) == 1 else candidate_groups[0] if len(candidate_groups) == 1 else []
        candidate = safe_candidate_rows[0] if safe_candidate_rows else candidates[0]
        candidate_customers = [{
            "customer_name": rows[0].get("customer_name") or None,
            "customer_gstin": rows[0].get("customer_gstin") or None,
            "invoice_count": len(rows),
            "source_record_ids": [item["sales_transaction_id"] for item in rows],
            "identity_evidence": rows[0]["sales_identity_candidate_evidence"],
            "confidence": 85 if any(item["sales_identity_candidate_evidence"] == "EMBEDDED_DEDUCTOR_NAME_VARIANT" for item in rows) else 75,
            "customer_pan": rows[0].get("customer_pan") or None,
            "taxable_value_total": _sum(rows, "taxable_amount"),
        } for rows in candidate_groups]
        amount = amount_for(safe_candidate_rows)
        status = "REVIEW_REQUIRED" if safe_candidate_rows and amount is not None else "AMOUNT_NOT_DETERMINABLE"
        return {
            "status": "REVIEW_REQUIRED", "identity_status": "REVIEW_REQUIRED", "identity_method": candidate["sales_identity_candidate_evidence"], "identity_confidence": 75,
            "identity_evidence": sorted({item["sales_identity_candidate_evidence"] for item in candidates}), "customer_name": candidate.get("customer_name"), "customer_gstin": candidate.get("customer_gstin") or None,
            "invoice_count": 0, "source_record_ids": [], "candidate_source_record_ids": [item["sales_transaction_id"] for item in candidates],
            "candidate_customers": candidate_customers, "candidate_sales_entries": safe_candidate_rows,
            **base_fields(safe_candidate_rows), "amount_basis": basis, "amount": amount,
            "difference": _difference(amount, statement_amount), "amount_status": status,
            "review_reason": "A single similarly named Sales customer provides Taxable Value evidence, but CA identity confirmation is required." if safe_candidate_rows else "Multiple Sales customer candidates exist; no taxable-value aggregate can be safely selected.",
        }
    sale = sales[0]
    amount = amount_for(sales)
    status = "AMOUNT_NOT_DETERMINABLE" if amount is None else "AMOUNT_MATCHED" if _within(amount, statement_amount, cfg) else "AMOUNT_DIFFERENCE"
    return {
        "status": status, "identity_status": "CONFIRMED", "identity_method": _sales_statement_evidence(sale, statements[0]) or "EXACT_SALES_DEDUCTOR_NAME", "identity_confidence": 100,
        "identity_evidence": sorted({evidence for row in sales for statement in statements if (evidence := _sales_statement_evidence(row, statement))}),
        "customer_name": sale.get("customer_name"), "customer_gstin": sale.get("customer_gstin") or None,
        "invoice_count": len(sales), "source_record_ids": [row["sales_transaction_id"] for row in sales],
        **base_fields(sales), "amount_basis": basis, "amount": amount,
        "difference": _difference(amount, statement_amount), "amount_status": status,
        "review_reason": "The configured Taxable Value basis is unavailable for one or more linked Sales rows." if amount is None else None,
    }

def _tds_check(tds, candidates, statements, cfg, relationship_id):
    """Compare a TAN-scoped 26AS relationship with its ledger evidence."""
    statement_amount = _sum(statements, "tax_deducted")
    confirmed_amount = _sum(tds, "tds_expected")
    candidate_amount = _sum(candidates, "tds_expected")
    ledger_rows = tds if tds else candidates
    ledger_amount = confirmed_amount if tds else candidate_amount
    difference = _difference(ledger_amount, statement_amount)
    if tds and not statements:
        comparison_status, reconciliation_status = "MISSING_IN_26AS", "MISSING_IN_26AS"
        identity_status = "CONFIRMED"
        identity_reason = "A TDS ledger group is present, but no 26AS statement row is available for comparison."
    elif tds:
        if statement_amount is None or confirmed_amount is None:
            comparison_status, reconciliation_status = "TDS_NOT_DETERMINABLE", "NOT_DETERMINABLE"
        elif _within(confirmed_amount, statement_amount, cfg):
            comparison_status, reconciliation_status = "TDS_MATCHED", "MATCHED"
        else:
            comparison_status, reconciliation_status = "TDS_DIFFERENCE", "DIFFERENCE"
        identity_status = "CONFIRMED"
        identity_reason = "Ledger identity is supported by an exact source identifier or normalized source name."
    elif candidates:
        comparison_status, reconciliation_status = "MISSING_IN_TDS", "REVIEW_REQUIRED"
        identity_status = "REVIEW_REQUIRED"
        identity_reason = "Candidate ledger groups have source-name or acronym-family evidence, but require CA identity confirmation."
    else:
        comparison_status, reconciliation_status = "MISSING_IN_TDS", "MISSING_TDS_LEDGER_COUNTERPART"
        identity_status = "UNMAPPED"
        identity_reason = "No same-financial-year TDS Receivable ledger counterpart has sufficient source evidence."
    statement = statements[0] if statements else {}
    candidate_rows = candidates if not tds else []
    return {
        "statement_amount": statement_amount,
        "statement_amount_paid_credited": _sum(statements, "amount_paid"),
        "ledger_amount": ledger_amount,
        "ledger_candidate_tds": candidate_amount,
        "difference": difference,
        "status": comparison_status,
        "comparison_status": comparison_status,
        "reconciliation_status": reconciliation_status,
        "identity_status": identity_status,
        "identity_reason": identity_reason,
        "statement_tan": statement.get("tan") or None,
        "statement_deductor_name": statement.get("deductor_name") or None,
        "statement_row_ids": [row["statement_id"] for row in statements],
        "statement_entry_count": len(statements),
        "ledger_candidate_group_ids": [row["tds_transaction_id"] for row in candidate_rows],
        "ledger_candidate_names": [row.get("party_name") for row in candidate_rows],
        # Backward-compatible aliases. In this workflow, TDS Expected means
        # the ledger/TDS Receivable amount, including review-only candidates.
        "tds_26as": statement_amount,
        "tds_book": ledger_amount,
        "tds_difference": difference,
        "tds_expected": ledger_amount,
        "statement_tds_deducted": statement_amount,
        "review_reason": identity_reason if reconciliation_status == "REVIEW_REQUIRED" else None,
        "match_group_id": f"TDS:{relationship_id}" if tds and statements else None,
        "group_size": len(ledger_rows) + len(statements),
        "tds_entries": tds,
        "candidate_tds_entries": candidates,
        "statement_entries": statements,
    }


def _transaction_reconciliation(relationship_id, statements, sales, cfg, identity_status):
    """Additive, source-row-preserving 26AS-to-Sales allocation.

    This deliberately never changes the relationship identity or its aggregate
    Sales/TDS outcomes.  Only supported date-and-amount evidence can allocate
    a transaction automatically; amount-only candidates stay reviewable.
    """
    tolerance = int(cfg.get("transaction_date_tolerance_days", 7))
    review_window = int(cfg.get("review_candidate_date_days", 365))
    # A review list is supporting evidence only. Bound it so a same-day bulk
    # export cannot create an unbounded number of displayed candidate objects.
    review_limit = max(1, int(cfg.get("transaction_review_candidate_limit", 50)))
    max_group_size = int(cfg.get("max_group_size", 2))

    def as_date(value):
        try:
            return date.fromisoformat(str(value)[:10]) if value else None
        except ValueError:
            return None

    def date_index(rows, field):
        indexed = sorted(((parsed, row) for row in rows if (parsed := as_date(row.get(field))) is not None), key=lambda item: item[0])
        return [item[0] for item in indexed], indexed

    sales_dates, sales_index = date_index(sales, "invoice_date")
    statement_dates, statement_index = date_index(statements, "transaction_date")

    def date_window(dates, indexed, pivot, days, identifier, used):
        if pivot is None:
            return []
        left = bisect_left(dates, pivot - timedelta(days=days))
        right = bisect_right(dates, pivot + timedelta(days=days))
        return [row for _parsed, row in indexed[left:right] if row.get(identifier) not in used]

    def pair_candidates(rows, amount_field, target):
        """Find at most two valid two-row sums without materialising nC2.

        Two candidates are enough: one is a potentially unique allocation and
        two prove that automatic allocation is unsafe.  Larger groups remain
        out of scope of this controlled two-row transaction allocator.
        """
        if max_group_size < 2 or len(rows) < 2 or target is None:
            return []
        values = sorted((float(row[amount_field]), index, row) for index, row in enumerate(rows) if row.get(amount_field) is not None)
        amounts = [item[0] for item in values]
        if len(values) < 2:
            return []
        absolute = float(cfg.get("amount_tolerance_abs", 0))
        percentage = max(0.0, float(cfg.get("amount_tolerance_pct", 0)) / 100)
        # This is a conservative envelope for _within(target, pair_sum, cfg)
        # when source amounts are non-negative. _within remains the final gate.
        lower = float(target) - max(absolute, abs(float(target)) * percentage)
        upper = max(float(target) + absolute, float(target) / max(1.0 - percentage, 0.000001) + absolute)
        found = []
        for left, (amount, _index, row) in enumerate(values):
            start = max(left + 1, bisect_left(amounts, lower - amount, left + 1))
            end = bisect_right(amounts, upper - amount, left + 1)
            for right in range(start, min(end, start + 2)):
                candidate = values[right][2]
                if _within(target, amount + values[right][0], cfg):
                    found.append((row, candidate))
                    if len(found) == 2:
                        return found
        return found

    def statement_row(row):
        return {"source_row_id": row.get("statement_id"), "transaction_date": row.get("transaction_date"), "amount_paid_credited": row.get("amount_paid"), "tds_deducted": row.get("tax_deducted"), "tan": row.get("tan"), "deductor_name": row.get("deductor_name"), "source_provenance": row.get("source_provenance") or row.get("source_file_name")}

    def sales_row(row):
        return {"source_row_id": row.get("sales_transaction_id"), "invoice_no": row.get("invoice_number"), "invoice_date": row.get("invoice_date"), "taxable_value": row.get("taxable_amount"), "customer_name": row.get("customer_name"), "gstin": row.get("customer_gstin"), "source_provenance": row.get("source_provenance") or row.get("source_file_name")}

    def group(statement_rows, sales_rows, match_type, status, method, reason, evidence):
        statement_amount, sales_amount = _sum(statement_rows, "amount_paid"), _sum(sales_rows, "taxable_amount")
        difference = _difference(sales_amount, statement_amount)
        dates = [abs((as_date(left.get("transaction_date")) - as_date(right.get("invoice_date"))).days) for left in statement_rows for right in sales_rows if as_date(left.get("transaction_date")) and as_date(right.get("invoice_date"))]
        absolute = abs(difference) if difference is not None else None
        variance = round((absolute / abs(statement_amount)) * 100, 4) if absolute is not None and statement_amount else None
        key = f"TXN:{relationship_id}:{len(groups) + 1}"
        return {"match_group_id": key, "relationship_key": relationship_id, "match_type": match_type, "status": status, "26as_rows": [statement_row(row) for row in statement_rows], "sales_rows": [sales_row(row) for row in sales_rows], "26as_amount": statement_amount, "sales_taxable_value": sales_amount, "difference": difference, "absolute_difference": absolute, "variance_percentage": variance, "date_difference_days": min(dates) if dates else None, "evidence": evidence, "method": method, "reason": reason, "recommended_action": "Review the source transaction evidence before accepting this allocation." if status == "REVIEW_REQUIRED" else "Retain this source-linked transaction allocation in the working papers." if status == "MATCHED" else "Review the amount difference against the source transactions."}

    groups, used_statement, used_sales = [], set(), set()
    # Strong automatic 1:1 matching: existing confirmed relationship, exact
    # taxable-value/26AS amount, and a controlled date tolerance.
    for statement in statements:
        if statement.get("statement_id") in used_statement or statement.get("amount_paid") is None:
            continue
        possible = [sale for sale in date_window(sales_dates, sales_index, as_date(statement.get("transaction_date")), tolerance, "sales_transaction_id", used_sales) if sale.get("taxable_amount") is not None and _within(statement["amount_paid"], sale["taxable_amount"], cfg)]
        if len(possible) == 1 and identity_status == "CONFIRMED":
            sale = possible[0]
            groups.append(group([statement], [sale], "ONE_TO_ONE", "MATCHED", "EXACT_AMOUNT_WITHIN_DATE_TOLERANCE", "Exact Taxable Value and controlled transaction-date tolerance support this allocation.", [{"type": "AMOUNT_AND_DATE", "description": "Exact amount within configured date tolerance.", "strength": "STRONG"}]))
            used_statement.add(statement["statement_id"]); used_sales.add(sale["sales_transaction_id"])

    # Exact two-row aggregates, only where dates also support the allocation.
    for statement in statements:
        if statement.get("statement_id") in used_statement or statement.get("amount_paid") is None or identity_status != "CONFIRMED":
            continue
        remaining = [sale for sale in date_window(sales_dates, sales_index, as_date(statement.get("transaction_date")), tolerance, "sales_transaction_id", used_sales) if sale.get("taxable_amount") is not None]
        candidates = pair_candidates(remaining, "taxable_amount", statement["amount_paid"])
        if len(candidates) == 1:
            pair = candidates[0]
            groups.append(group([statement], list(pair), "ONE_TO_MANY", "MATCHED", "AGGREGATE_EXACT_AMOUNT_WITHIN_DATE_TOLERANCE", "Two Sales invoices aggregate to the 26AS amount within the configured date tolerance.", [{"type": "AGGREGATE_AMOUNT_AND_DATE", "description": "Two invoices aggregate to the 26AS amount.", "strength": "STRONG"}]))
            used_statement.add(statement["statement_id"]); used_sales.update(row["sales_transaction_id"] for row in pair)
    for sale in sales:
        if sale.get("sales_transaction_id") in used_sales or sale.get("taxable_amount") is None or identity_status != "CONFIRMED":
            continue
        remaining = [statement for statement in date_window(statement_dates, statement_index, as_date(sale.get("invoice_date")), tolerance, "statement_id", used_statement) if statement.get("amount_paid") is not None]
        candidates = pair_candidates(remaining, "amount_paid", sale["taxable_amount"])
        if len(candidates) == 1:
            pair = candidates[0]
            groups.append(group(list(pair), [sale], "MANY_TO_ONE", "MATCHED", "AGGREGATE_EXACT_AMOUNT_WITHIN_DATE_TOLERANCE", "Two 26AS transactions aggregate to the Sales Taxable Value within the configured date tolerance.", [{"type": "AGGREGATE_AMOUNT_AND_DATE", "description": "Two 26AS rows aggregate to the Sales Taxable Value.", "strength": "STRONG"}]))
            used_sales.add(sale["sales_transaction_id"]); used_statement.update(row["statement_id"] for row in pair)

    # A unique controlled-date relationship with different amounts is an
    # amount difference.  It remains independent of relationship identity.
    for statement in statements:
        if statement.get("statement_id") in used_statement or statement.get("amount_paid") is None or identity_status != "CONFIRMED":
            continue
        possible = [sale for sale in date_window(sales_dates, sales_index, as_date(statement.get("transaction_date")), tolerance, "sales_transaction_id", used_sales) if sale.get("taxable_amount") is not None]
        if len(possible) == 1:
            sale = possible[0]
            groups.append(group([statement], [sale], "ONE_TO_ONE", "AMOUNT_DIFFERENCE", "CONTROLLED_DATE_AMOUNT_DIFFERENCE", "The transaction dates are within the configured tolerance, but Sales Taxable Value differs from the 26AS Amount Paid/Credited.", [{"type": "DATE", "description": "Transaction dates are within configured tolerance.", "strength": "SUPPORTING"}]))
            used_statement.add(statement["statement_id"]); used_sales.add(sale["sales_transaction_id"])

    # Review candidates are suggestions, never allocations. An exact amount
    # inside the wider review window remains visible even when it is outside
    # the automatic date tolerance. Review candidates do not consume rows.
    review_candidates = []
    candidate_limit_reached_for = []
    for statement in statements:
        if statement.get("statement_id") in used_statement or statement.get("amount_paid") is None:
            continue
        possible = [sale for sale in date_window(sales_dates, sales_index, as_date(statement.get("transaction_date")), review_window, "sales_transaction_id", used_sales) if sale.get("taxable_amount") is not None and _within(statement["amount_paid"], sale["taxable_amount"], cfg)]
        # Date-window order is deterministic. The cap preserves a bounded,
        # auditable review queue without allocating or discarding source rows.
        if len(possible) > review_limit:
            candidate_limit_reached_for.append(statement.get("source_row_id") or statement.get("statement_id"))
        for sale in possible[:review_limit]:
            review_candidates.append(group([statement], [sale], "ONE_TO_ONE", "REVIEW_CANDIDATE", "EXACT_AMOUNT_OUTSIDE_AUTO_DATE_TOLERANCE", "Exact amount supports the candidate, but transaction dates are outside the automatic matching tolerance.", [{"type": "AMOUNT", "result": "EXACT", "strength": "STRONG"}, {"type": "DATE", "result": "OUTSIDE_AUTO_TOLERANCE", "strength": "SUPPORTING"}]))
    sales_candidate_counts, statement_candidate_counts = defaultdict(int), defaultdict(int)
    for candidate in review_candidates:
        for row in candidate["sales_rows"]:
            sales_candidate_counts[row["source_row_id"]] += 1
        for row in candidate["26as_rows"]:
            statement_candidate_counts[row["source_row_id"]] += 1
    for candidate_index, candidate in enumerate(review_candidates, start=1):
        sales_ambiguous = any(sales_candidate_counts[row["source_row_id"]] > 1 for row in candidate["sales_rows"])
        statement_ambiguous = any(statement_candidate_counts[row["source_row_id"]] > 1 for row in candidate["26as_rows"])
        if sales_ambiguous or statement_ambiguous:
            candidate["status"] = "AMBIGUOUS_REVIEW"
            candidate["method"] = "EXACT_AMOUNT_AMBIGUOUS_REVIEW"
            candidate["reason"] = "Multiple Sales transactions and/or 26AS transactions provide similarly supported evidence. CA review is required before allocation."
            candidate["ambiguity_id"] = f"TXNA:{relationship_id}:{candidate_index}"
        candidate["candidate_id"] = f"TXNC:{relationship_id}:{candidate_index}"
        candidate["match_group_id"] = None
        candidate["recommended_action"] = "CA_REVIEW"

    unmatched_26as = [statement_row(row) for row in statements if row.get("statement_id") not in used_statement]
    unmatched_sales = [sales_row(row) for row in sales if row.get("sales_transaction_id") not in used_sales]
    for row in unmatched_26as:
        row.update({"status": "UNMATCHED_26AS", "reason": "No supported Sales Registry transaction was found for this 26AS transaction.", "recommended_action": "Review whether the Sales Registry contains the corresponding invoice."})
    for row in unmatched_sales:
        row.update({"status": "UNMATCHED_SALES", "reason": "This Sales invoice was not allocated to a supported 26AS transaction.", "recommended_action": "Review the corresponding 26AS transaction evidence."})
    candidate_limit_reached = bool(candidate_limit_reached_for)
    candidate_limit_message = (
        "Additional candidates were not displayed because the review candidate limit was reached. Manual review is required."
        if candidate_limit_reached else None
    )
    return {"amount_basis": "taxable_value", "summary": {"26as_transaction_count": len(statements), "sales_invoice_count": len(sales), "matched_group_count": sum(item["status"] == "MATCHED" for item in groups), "one_to_one_count": sum(item["match_type"] == "ONE_TO_ONE" and item["status"] == "MATCHED" for item in groups), "one_to_many_count": sum(item["match_type"] == "ONE_TO_MANY" and item["status"] == "MATCHED" for item in groups), "many_to_one_count": sum(item["match_type"] == "MANY_TO_ONE" and item["status"] == "MATCHED" for item in groups), "review_candidate_count": len(review_candidates), "review_candidate_limit": review_limit, "candidate_limit_reached": candidate_limit_reached, "candidate_limit_reached_for": candidate_limit_reached_for, "candidate_limit_message": candidate_limit_message, "ambiguous_review_count": sum(item["status"] == "AMBIGUOUS_REVIEW" for item in review_candidates), "unmatched_26as_count": len(unmatched_26as), "unmatched_sales_count": len(unmatched_sales), "amount_difference_group_count": sum(item["status"] == "AMOUNT_DIFFERENCE" for item in groups), "26as_amount_total": _sum(statements, "amount_paid"), "sales_taxable_value_total": _sum(sales, "taxable_amount"), "matched_26as_amount": round(sum(item["26as_amount"] or 0 for item in groups if item["status"] == "MATCHED"), 2), "matched_sales_amount": round(sum(item["sales_taxable_value"] or 0 for item in groups if item["status"] == "MATCHED"), 2), "unmatched_26as_amount": round(sum(item.get("amount_paid_credited") or 0 for item in unmatched_26as), 2), "unmatched_sales_amount": round(sum(item.get("taxable_value") or 0 for item in unmatched_sales), 2)}, "candidate_limit_reached": candidate_limit_reached, "candidate_limit_reached_for": candidate_limit_reached_for, "candidate_limit_message": candidate_limit_message, "match_groups": groups, "unmatched_26as": unmatched_26as, "unmatched_sales": unmatched_sales, "review_candidates": review_candidates}


def _relationship(run_id, seq, component, cfg):
    sales, tds, statements, tds_candidates, sales_candidates = component["sales"], component["tds"], component["statements"], component.get("tds_candidates", []), component.get("sales_candidates", [])
    component["allow_exact_name_confirmation"] = bool(cfg.get("sales_tds_allow_exact_name_confirmation", False))
    component_identity, component_method, component_reason = _identity(component)
    sales_check = _sales_counterpart(sales, sales_candidates, statements, cfg)
    # The canonical relationship identity answers one question only: whether
    # the statutory 26AS deductor has a supported Sales customer/party link.
    # TDS-ledger linkage is independent evidence and must never promote an
    # unmapped or review-only customer relationship to CONFIRMED.
    if component_identity == "CONFLICT":
        identity, method, reason = "CONFLICT", component_method, component_reason
    elif sales_check["identity_status"] == "REVIEW_REQUIRED":
        identity = "REVIEW_REQUIRED"
        method = sales_check["identity_method"]
        reason = sales_check["review_reason"] or "A Sales customer candidate exists, but the available evidence requires CA confirmation."
    elif sales_check["identity_status"] == "MISSING_SALES_COUNTERPART":
        identity = "UNMAPPED"
        method = sales_check["identity_method"]
        reason = sales_check["review_reason"] or "No supported Sales customer mapping has been established for this 26AS deductor."
    else:
        identity, method, reason = component_identity, component_method, component_reason
    sale_amount = sales_check["amount"]
    statement_amount = _sum(statements, "amount_paid")
    amount_difference = sales_check["difference"]
    amount_status = sales_check["amount_status"]
    relationship_id = f"{run_id}:relationship:{seq}"
    tds_check = _tds_check(tds, tds_candidates, statements, cfg, relationship_id)
    tds_status = tds_check["comparison_status"]
    overall = "IDENTITY_CONFLICT" if identity == "CONFLICT" else "IDENTITY_REVIEW_REQUIRED" if identity in {"REVIEW_REQUIRED", "UNMAPPED"} else "FULLY_RECONCILED" if amount_status == "AMOUNT_MATCHED" and tds_status == "TDS_MATCHED" else "COMBINED_EXCEPTION" if amount_status == "AMOUNT_DIFFERENCE" and tds_status == "TDS_DIFFERENCE" else "AMOUNT_EXCEPTION" if amount_status == "AMOUNT_DIFFERENCE" else "TDS_EXCEPTION" if tds_status == "TDS_DIFFERENCE" else "REVIEW_REQUIRED"
    sale, statement = sales[0] if sales else {}, statements[0] if statements else {}
    primary_transaction_id = statement.get("statement_id") if statement else (tds[0].get("tds_transaction_id") if tds else sale.get("sales_transaction_id"))
    primary_fy = (statement or (tds[0] if tds else sale)).get("financial_year")
    primary_quarter = statement.get("quarter") if statement else (tds[0].get("quarter") if tds else sale.get("quarter"))
    primary_section = statement.get("section") if statement else (tds[0].get("section") if tds else "")
    confidence = sales_check.get("identity_confidence", 0) if identity != "CONFLICT" else 0
    source_records = {"sales_transaction_ids": [row["sales_transaction_id"] for row in sales], "tds_transaction_ids": [row["tds_transaction_id"] for row in tds], "tds_candidate_transaction_ids": [row["tds_transaction_id"] for row in tds_candidates], "statement_ids": [row["statement_id"] for row in statements]}
    deductor_name = statement.get("deductor_name") or None
    tan = statement.get("tan") or None
    customer = sales_check.get("customer_name") or sale.get("customer_name") or None
    customer_code = sale.get("customer_code") or None
    customer_pan = sale.get("customer_pan") or None
    customer_gstin = sales_check.get("customer_gstin") or sale.get("customer_gstin") or None
    candidate_count = len(sales_check.get("candidate_customers") or []) or (1 if customer else 0)
    identity_record = {"customer_name": customer, "customer_code": customer_code, "customer_pan": customer_pan, "customer_gstin": customer_gstin, "deductor_name": deductor_name, "deductor_pan": statement.get("deductor_pan") or None, "tan": tan, "status": identity, "method": method, "confidence": confidence, "evidence": sorted(set([*component["evidence"], *sales_check.get("identity_evidence", [])])), "candidate_count": candidate_count, "source_records": source_records, "candidate_ids": source_records, "review_reason": reason if identity == "REVIEW_REQUIRED" else None, "reason": reason}
    ledger_candidates = [{"ledger_group_id": row.get("tds_transaction_id"), "ledger_name": row.get("party_name"), "ledger_tds_amount": row.get("tds_expected"), "accounting_role": row.get("accounting_role") or "TDS_RECEIVABLE", "accounting_direction": row.get("accounting_direction") or "DEBIT", "evidence": row.get("identity_candidate_evidence") or ("EXACT_TAN" if row.get("tan") else "EXACT_TDS_DEDUCTOR_NAME"), "confidence": 75 if row.get("identity_candidate_evidence") else 100 if row.get("tan") else 85} for row in [*tds, *tds_candidates]]

    # The identity bridge is a presentation record. It groups source Sales rows
    # by the strongest identifier already supplied by that source, retaining
    # every contributing row and its Taxable Value without affecting matching.
    def sales_identity_key(row):
        for field in ("customer_code", "customer_pan", "customer_gstin", "customer_name_norm", "customer_name"):
            value = row.get(field)
            if value not in (None, ""):
                return field, str(value).strip().upper()
        return "sales_transaction_id", str(row.get("sales_transaction_id") or "")

    bridge_sales_groups = {}
    for row in [*sales, *sales_candidates]:
        key = sales_identity_key(row)
        group = bridge_sales_groups.setdefault(key, {"rows": [], "evidence": [], "statuses": [], "methods": []})
        group["rows"].append(row)
        group["evidence"].append(row.get("sales_identity_candidate_evidence") or sales_check.get("identity_method"))
        group["statuses"].append("REVIEW_REQUIRED" if row.get("sales_identity_candidate_evidence") else sales_check.get("identity_status"))
        group["methods"].append(row.get("sales_identity_candidate_evidence") or sales_check.get("identity_method"))

    bridge_sales_candidates = []
    for group in bridge_sales_groups.values():
        rows = group["rows"]
        exemplar = rows[0]
        bridge_sales_candidates.append({
            "customer_name": exemplar.get("customer_name"),
            "customer_code": exemplar.get("customer_code") or None,
            "gstin": exemplar.get("customer_gstin") or None,
            "pan": exemplar.get("customer_pan") or None,
            "sales_row_ids": [row.get("sales_transaction_id") for row in rows if row.get("sales_transaction_id")],
            "sales_row_count": len(rows),
            "taxable_value": _sum(rows, "taxable_amount"),
            "evidence": sorted({value for value in group["evidence"] if value}),
            "candidate_status": "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in group["statuses"] else sales_check.get("identity_status"),
            "method": next((value for value in group["methods"] if value), None),
            "reason": sales_check.get("review_reason"),
            "confidence": sales_check.get("identity_confidence", 0),
        })
    # Alias evidence connects the canonical Sales customer to an already
    # discovered ledger candidate. It is explanatory bridge evidence only:
    # the established 26AS-to-Sales identity decision remains unchanged.
    identity_alias_stopwords = {"THE", "OF", "PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "INC", "CORPORATION", "CORP"}

    def sales_initialism(name):
        normalized = re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper().replace("'S", "")).strip()
        tokens = [token for token in normalized.split() if token not in identity_alias_stopwords and len(token) > 1]
        alias = "".join(token[0] for token in tokens)
        return alias if len(alias) >= 3 else None

    alias_evidence = []
    canonical_customer_name = str(customer or "").strip()
    canonical_candidates = [candidate for candidate in bridge_sales_candidates if str(candidate.get("customer_name") or "").strip().upper() == canonical_customer_name.upper()]
    for candidate in canonical_candidates:
        alias = sales_initialism(candidate.get("customer_name"))
        if not alias:
            continue
        for ledger in ledger_candidates:
            ledger_name = str(ledger.get("ledger_name") or "").strip().upper()
            if ledger_name == alias or ledger_name.startswith(f"{alias} "):
                alias_evidence.append({
                    "alias": alias,
                    "canonical_name": candidate.get("customer_name"),
                    "source": "sales_registry",
                    "method": "INITIALISM",
                    "matched_ledger_name": ledger.get("ledger_name"),
                    "ledger_group_id": ledger.get("ledger_group_id"),
                    "confidence": candidate.get("confidence", 0),
                    "reason": "Ledger name equals the deterministic initialism of the canonical Sales customer name.",
                })

    identity_bridge = {"statement": {"tan": tan, "deductor_name": deductor_name, "financial_year": primary_fy, "statement_row_ids": [row.get("statement_id") for row in statements]}, "ledger_candidates": ledger_candidates, "sales_candidates": bridge_sales_candidates, "alias_evidence": alias_evidence, "identity_decision": {"status": identity, "selected_ledger_groups": [row.get("tds_transaction_id") for row in tds if row.get("tds_transaction_id")], "selected_sales_customer": customer if identity in {"CONFIRMED", "REVIEW_REQUIRED"} else None, "method": method, "reason": reason, "evidence_summary": "Amounts are reconciliation evidence only and do not prove identity."}}
    transaction_sales = {row.get("sales_transaction_id"): row for row in [*sales, *sales_check.get("candidate_sales_entries", [])] if row.get("sales_transaction_id")}
    transaction_reconciliation = _transaction_reconciliation(relationship_id, statements, list(transaction_sales.values()), cfg, identity)
    return {"id": relationship_id, "relationship_id": relationship_id, "relationship_key": relationship_id, "run_id": run_id, "seq": seq, "workflow": "SALES_TDS_26AS", "source": "26AS" if statements else "TDS" if tds else "SALES", "transaction_id": primary_transaction_id, "financial_year": primary_fy, "quarter": primary_quarter, "section": primary_section, "identity": identity_record, "identity_bridge": identity_bridge, "transaction_reconciliation": transaction_reconciliation, "deductor_name": deductor_name, "tan": tan, "identity_status": identity, "identity_method": method, "identity_reason": reason, "customer_name": customer, "customer_code": customer_code, "customer_pan": customer_pan, "customer_gstin": customer_gstin, "sales_check": sales_check, "amount_check": {"sales_amount": sale_amount, "sales_amount_basis": sales_check["sales_amount_basis"], "sales_amount_basis_label": sales_check["sales_amount_basis_label"], "sales_amount_source_field": sales_check["sales_amount_source_field"], "statement_amount_paid": statement_amount, "difference": amount_difference, "status": amount_status, "amount_match_stage": "CUSTOMER_FY_AGGREGATION", "amount_match_reason": sales_check["review_reason"] or "Taxable Value was aggregated by the confirmed Sales customer and financial year.", "sales_row_ids": [row["sales_transaction_id"] for row in sales], "candidate_sales_row_ids": [row["sales_transaction_id"] for row in sales_check.get("candidate_sales_entries", [])], "statement_row_ids": [row["statement_id"] for row in statements], "sales_row_count": len(sales), "candidate_sales_row_count": len(sales_check.get("candidate_sales_entries", [])), "statement_row_count": len(statements), "match_group_id": f"AMT:{relationship_id}" if sales and statements else None, "group_size": len(sales) + len(statements), "sales_entries": sales, "candidate_sales_entries": sales_check.get("candidate_sales_entries", []), "statement_entries": statements}, "tds_check": tds_check, "overall_status": overall, "reason": reason, "recommended_action": "Confirm the candidate TDS Receivable account evidence." if tds_check["reconciliation_status"] == "REVIEW_REQUIRED" else "Locate the missing same-financial-year TDS Receivable ledger counterpart." if tds_check["reconciliation_status"] == "MISSING_TDS_LEDGER_COUNTERPART" else "Review the source evidence and confirm the candidate relationship." if identity == "REVIEW_REQUIRED" else "Resolve the conflicting TAN evidence before reconciliation." if identity == "CONFLICT" else "Review the linked amount and TDS evidence." if overall != "FULLY_RECONCILED" else "Retain the source-linked reconciliation in the working papers.", "severity": "NONE" if overall == "FULLY_RECONCILED" else "HIGH" if identity in {"CONFLICT", "UNMAPPED"} and tds_check["reconciliation_status"] == "MISSING_TDS_LEDGER_COUNTERPART" else "MEDIUM", "schema_version": 7}


def _sales_dashboard_control_totals(sales, statements, results):
    """Return display-only control populations without changing reconciliation.

    Source-wide totals answer what each uploaded source contains. Primary Sales
    evidence contains only unique Sales rows selected for a 26AS primary
    relationship, including a single review-only candidate when the existing
    workflow has already selected one. Those are deliberately separate
    populations, so no displayed difference mixes source-wide and relationship
    totals.
    """
    selected_sales, selected_statement_ids = {}, set()
    matched_sales, matched_statement_ids = {}, set()
    confirmed_tds = review_tds = missing_tds = amount_evidence = 0.0

    for result in results:
        amount = result["amount_check"]
        selected_rows = [*amount.get("sales_entries", []), *amount.get("candidate_sales_entries", [])]
        for row in selected_rows:
            selected_sales.setdefault(row["sales_transaction_id"], row)
        if selected_rows:
            selected_statement_ids.update(amount.get("statement_row_ids", []))
        if amount.get("status") == "AMOUNT_MATCHED":
            for row in amount.get("sales_entries", []):
                matched_sales.setdefault(row["sales_transaction_id"], row)
            matched_statement_ids.update(amount.get("statement_row_ids", []))

        tds = result["tds_check"]
        statement_amount = float(tds.get("statement_amount") or 0)
        reconciliation_status = tds.get("reconciliation_status")
        if reconciliation_status == "MATCHED":
            confirmed_tds += statement_amount
        elif reconciliation_status == "REVIEW_REQUIRED":
            review_tds += statement_amount
        elif reconciliation_status == "MISSING_TDS_LEDGER_COUNTERPART":
            missing_tds += statement_amount
        if tds.get("ledger_amount") is not None:
            amount_evidence += statement_amount

    sales_by_id = {row["sales_transaction_id"]: row for row in sales}
    statements_by_id = {row["statement_id"]: row for row in statements}
    primary_sales_total = _sum(list(selected_sales.values()), "taxable_amount") or 0.0
    primary_statement_total = _sum([statements_by_id[row_id] for row_id in selected_statement_ids], "amount_paid") or 0.0
    matched_sales_total = _sum(list(matched_sales.values()), "taxable_amount") or 0.0
    matched_statement_total = _sum([statements_by_id[row_id] for row_id in matched_statement_ids], "amount_paid") or 0.0
    sales_only_total = _sum([row for row_id, row in sales_by_id.items() if row_id not in selected_sales], "taxable_amount") or 0.0
    statement_only_total = _sum([row for row_id, row in statements_by_id.items() if row_id not in selected_statement_ids], "amount_paid") or 0.0
    source_sales_total = _sum(sales, "taxable_amount") or 0.0
    source_statement_total = _sum(statements, "amount_paid") or 0.0

    # The conservation checks document the exact populations behind every
    # dashboard number and catch accidental duplicate assignment in future UI
    # changes. They do not alter matching outcomes.
    if round(primary_sales_total + sales_only_total, 2) != round(source_sales_total, 2):
        raise ValueError("Sales dashboard control populations do not reconcile to the Sales source total.")
    if round(primary_statement_total + statement_only_total, 2) != round(source_statement_total, 2):
        raise ValueError("Sales dashboard control populations do not reconcile to the 26AS source total.")

    return {
        "sales_source_taxable_total": round(source_sales_total, 2),
        "26as_source_amount_total": round(source_statement_total, 2),
        "sales_source_difference": _difference(source_sales_total, source_statement_total),
        "primary_sales_taxable_total": round(primary_sales_total, 2),
        "primary_26as_amount_total": round(primary_statement_total, 2),
        "sales_difference_for_primary_population": _difference(primary_sales_total, primary_statement_total),
        "sales_only_taxable_total": round(sales_only_total, 2),
        "26as_only_amount_total": round(statement_only_total, 2),
        "matched_sales_taxable_total": round(matched_sales_total, 2),
        "matched_26as_amount_total": round(matched_statement_total, 2),
        "matched_sales_difference": _difference(matched_sales_total, matched_statement_total),
        "relationship_difference_with_selected_sales_evidence": round(sum(row["amount_check"].get("difference") or 0 for row in results if row["amount_check"].get("difference") is not None), 2),
        "primary_sales_row_count": len(selected_sales),
        "sales_only_row_count": len(sales_by_id) - len(selected_sales),
        "confirmed_tds_amount": round(confirmed_tds, 2),
        "review_tds_amount": round(review_tds, 2),
        "missing_tds_amount": round(missing_tds, 2),
        "total_tds_amount": round(sum(float(row.get("tax_deducted") or 0) for row in statements), 2),
        "tds_amount_evidence_available": round(amount_evidence, 2),
    }


def run_sales_tds_26as(run_id, sales, tds, statements, cfg):
    """Build one auditable primary relationship per 26AS deductor and FY.

    Earlier component construction exposed every Sales-only customer as a
    primary reconciliation row.  Sales is supporting coverage in this flow;
    26AS deductors are the statutory reconciliation universe.
    """
    # Preserve the source-only diagnostic behavior for callers that have not
    # supplied 26AS. The production three-source workflow always takes the
    # 26AS-first branch below.
    if not statements:
        components = _components(sales, tds, [])
        results = [_relationship(run_id, seq, component, cfg or {}) for seq, component in enumerate(components, start=1)]
        return {"results": results, "summary": {"workflow": "SALES_TDS_26AS", "primary_universe": "SOURCE_DIAGNOSTIC", "sales_count": len(sales), "tds_count": len(tds), "statement_count": 0, "result_count": len(results), "identity_counts": {status: sum(row["identity"]["status"] == status for row in results) for status in ("CONFIRMED", "REVIEW_REQUIRED", "UNMAPPED", "CONFLICT")}}, "identities": [], "customers": []}
    anchors = defaultdict(list)
    for statement in statements:
        anchors[(statement.get("financial_year"), statement.get("tan"), statement.get("deductor_name_norm") or core_name(statement.get("deductor_name", "")))].append(statement)
    ordered_anchors = sorted(anchors.items())
    linked_by_anchor, candidate_by_anchor, assigned_tds_ids = defaultdict(list), defaultdict(list), set()
    # A ledger group is assigned at most once. Exact source evidence takes
    # precedence; review candidates are assigned only if exactly one
    # TAN-bounded primary relationship is possible.
    for row in tds:
        possible = []
        for key, anchor_rows in ordered_anchors:
            if row.get("financial_year") != key[0]:
                continue
            evidence = next((value for statement in anchor_rows if (value := _tds_statement_evidence(row, statement))), None)
            if evidence and evidence != "CONFLICTING_TAN":
                possible.append(key)
        if len(possible) == 1:
            linked_by_anchor[possible[0]].append(row)
            assigned_tds_ids.add(row["tds_transaction_id"])
    for row in tds:
        if row["tds_transaction_id"] in assigned_tds_ids:
            continue
        possible = []
        for key, anchor_rows in ordered_anchors:
            if row.get("financial_year") != key[0]:
                continue
            evidence = next((value for statement in anchor_rows if (value := _tds_statement_review_candidate(row, statement))), None)
            if evidence:
                possible.append((key, evidence))
        if len(possible) == 1:
            key, evidence = possible[0]
            candidate_by_anchor[key].append({**row, "identity_candidate_evidence": evidence})
            assigned_tds_ids.add(row["tds_transaction_id"])

    results, matched_sales_ids = [], set()
    for seq, (anchor_key, anchor_rows) in enumerate(ordered_anchors, start=1):
        fy, _tan, _deductor_norm = anchor_key
        linked_tds, tds_candidates = linked_by_anchor[anchor_key], candidate_by_anchor[anchor_key]
        linked_sales, sales_candidates = [], []
        for row in sales:
            if row.get("financial_year") != fy:
                continue
            evidence_for_link = next((value for statement in anchor_rows if (value := _sales_statement_evidence(row, statement))), None)
            if evidence_for_link:
                linked_sales.append(row)
                continue
            candidate_evidence = next((value for statement in anchor_rows if (value := _sales_statement_review_candidate(row, statement))), None)
            if candidate_evidence:
                sales_candidates.append({**row, "sales_identity_candidate_evidence": candidate_evidence})
        matched_sales_ids.update(row["sales_transaction_id"] for row in linked_sales)
        evidence = []
        for tds_row in linked_tds:
            evidence.extend(filter(None, (_tds_statement_evidence(tds_row, statement) for statement in anchor_rows)))
        for sale_row in linked_sales:
            evidence.extend(filter(None, (_sales_statement_evidence(sale_row, statement) for statement in anchor_rows)))
            evidence.extend(filter(None, (_sales_tds_evidence(sale_row, tds_row) for tds_row in linked_tds)))
        component = {"sales": linked_sales, "sales_candidates": sales_candidates, "tds": linked_tds, "tds_candidates": tds_candidates, "statements": anchor_rows, "evidence": evidence, "primary_26as_anchor": True, "allow_exact_name_confirmation": bool((cfg or {}).get("sales_tds_allow_exact_name_confirmation", False))}
        results.append(_relationship(run_id, seq, component, cfg or {}))
    coverage = [row for row in sales if row["sales_transaction_id"] not in matched_sales_ids]
    unmapped_reasons = defaultdict(int)
    for row in results:
        if row["identity"]["status"] == "UNMAPPED":
            records = row["identity"]["source_records"]
            unmapped_reasons["MISSING_TDS_LEDGER_COUNTERPART" if records["sales_transaction_ids"] else "MISSING_SALES_AND_TDS_COUNTERPART"] += 1
    statement_tds_total = round(sum(row.get("tax_deducted") or 0 for row in statements), 2)
    relationship_tds_total = round(sum(row["tds_check"]["statement_amount"] or 0 for row in results), 2)
    assigned_ledger_ids = [entry["tds_transaction_id"] for row in results for entry in [*row["tds_check"]["tds_entries"], *row["tds_check"]["candidate_tds_entries"]]]
    if relationship_tds_total != statement_tds_total:
        raise ValueError("26AS primary relationship control total does not equal normalized statement TDS total.")
    if len(assigned_ledger_ids) != len(set(assigned_ledger_ids)):
        raise ValueError("A TDS Receivable ledger group was assigned to more than one primary relationship.")
    dashboard_controls = _sales_dashboard_control_totals(sales, statements, results)
    summary = {"workflow": "SALES_TDS_26AS", "primary_universe": "FORM26AS_DEDUCTORS", "sales_amount_basis": "taxable_value", "sales_amount_basis_label": "Taxable Value", "deductor_count": len(anchors), "sales_count": len(sales), "tds_count": len(tds), "statement_count": len(statements), "result_count": len(results), "sales_side_coverage": {"customers_total": len({row.get("customer_name_norm") for row in sales}), "customers_linked_to_26as": len({row.get("customer_name_norm") for row in sales if row["sales_transaction_id"] in matched_sales_ids}), "customers_without_26as_counterpart": len({row.get("customer_name_norm") for row in coverage}), "invoice_rows_excluded_from_primary_universe": len(coverage), "taxable_value_without_26as_counterpart": _sum(coverage, "taxable_amount")}, "amount_matched_count": sum(row["amount_check"]["status"] == "AMOUNT_MATCHED" for row in results), "amount_difference_count": sum(row["amount_check"]["status"] == "AMOUNT_DIFFERENCE" for row in results), "amount_not_determinable_count": sum(row["amount_check"]["status"] == "AMOUNT_NOT_DETERMINABLE" for row in results), "sales_counterpart_found_count": sum(row["sales_check"]["identity_status"] == "CONFIRMED" for row in results), "tds_matched_count": sum(row["tds_check"]["reconciliation_status"] == "MATCHED" for row in results), "tds_difference_count": sum(row["tds_check"]["reconciliation_status"] == "DIFFERENCE" for row in results), "missing_tds_ledger_count": sum(row["tds_check"]["reconciliation_status"] == "MISSING_TDS_LEDGER_COUNTERPART" for row in results), "review_candidate_count": sum(row["tds_check"]["reconciliation_status"] == "REVIEW_REQUIRED" for row in results), "identity_counts": {status: sum(row["identity"]["status"] == status for row in results) for status in ("CONFIRMED", "REVIEW_REQUIRED", "UNMAPPED", "CONFLICT")}, "sales_identity_counts": {status: sum(row["sales_check"]["identity_status"] == status for row in results) for status in ("CONFIRMED", "REVIEW_REQUIRED", "MISSING_SALES_COUNTERPART", "CONFLICT", "UNMAPPED")}, "sales_amount_status_counts": {status: sum(row["sales_check"]["amount_status"] == status for row in results) for status in ("AMOUNT_MATCHED", "AMOUNT_DIFFERENCE", "AMOUNT_NOT_DETERMINABLE", "REVIEW_REQUIRED")}, "tds_status_counts": {status: sum(row["tds_check"]["reconciliation_status"] == status for row in results) for status in ("MATCHED", "DIFFERENCE", "MISSING_TDS_LEDGER_COUNTERPART", "REVIEW_REQUIRED", "NOT_DETERMINABLE")}, "sales_status_counts": {"COUNTERPART_FOUND": sum(row["sales_check"]["identity_status"] == "CONFIRMED" for row in results), "MISSING_SALES_COUNTERPART": sum(row["sales_check"]["identity_status"] == "MISSING_SALES_COUNTERPART" for row in results), "REVIEW_REQUIRED": sum(row["sales_check"]["identity_status"] == "REVIEW_REQUIRED" for row in results), "NOT_DETERMINABLE": sum(row["sales_check"]["amount_status"] == "AMOUNT_NOT_DETERMINABLE" for row in results)}, "unmapped_reasons": dict(sorted(unmapped_reasons.items())), "sales_amount_total": _sum(sales, "taxable_amount"), "statement_amount_paid_total": round(sum(row.get("amount_paid") or 0 for row in statements), 2), "tds_expected_total": round(sum(row.get("tds_expected") or 0 for row in tds), 2), "statement_tds_deducted_total": statement_tds_total, "control_totals": {"sales_registry_taxable_value_total": _sum(sales, "taxable_amount"), "statement_amount_paid_total": round(sum(row.get("amount_paid") or 0 for row in statements), 2), "matched_sales_taxable_value": round(sum(row["amount_check"]["sales_amount"] or 0 for row in results if row["amount_check"]["status"] == "AMOUNT_MATCHED"), 2), "matched_statement_amount": round(sum(row["amount_check"]["statement_amount_paid"] or 0 for row in results if row["amount_check"]["status"] == "AMOUNT_MATCHED"), 2), "sales_difference": round(sum(row["amount_check"]["difference"] or 0 for row in results if row["amount_check"]["difference"] is not None), 2), "statement_tds_total": statement_tds_total, "relationship_statement_tds_total": relationship_tds_total, "eligible_ledger_tds_total": round(sum(row.get("tds_expected") or 0 for row in tds), 2), "assigned_ledger_tds_total": round(sum(row["tds_check"]["ledger_amount"] or 0 for row in results), 2), "matched_ledger_tds_total": round(sum(row["tds_check"]["ledger_amount"] or 0 for row in results if row["tds_check"]["reconciliation_status"] == "MATCHED"), 2), "review_candidate_ledger_tds_total": round(sum(row["tds_check"]["ledger_amount"] or 0 for row in results if row["tds_check"]["reconciliation_status"] == "REVIEW_REQUIRED"), 2), "missing_ledger_statement_tds_total": round(sum(row["tds_check"]["statement_amount"] or 0 for row in results if row["tds_check"]["reconciliation_status"] == "MISSING_TDS_LEDGER_COUNTERPART"), 2), "assigned_ledger_group_count": len(assigned_ledger_ids)}}
    # Preserve legacy keys for existing reports, while making their population
    # explicit and providing the non-mixed controls used by the dashboard.
    summary["control_totals"].update(dashboard_controls)
    summary["control_totals"].update({
        "sales_registry_taxable_value_total": dashboard_controls["sales_source_taxable_total"],
        "statement_amount_paid_total": dashboard_controls["26as_source_amount_total"],
        "matched_sales_taxable_value": dashboard_controls["matched_sales_taxable_total"],
        "matched_statement_amount": dashboard_controls["matched_26as_amount_total"],
        "sales_difference": dashboard_controls["sales_difference_for_primary_population"],
    })
    return {"results": results, "summary": summary, "identities": [], "customers": []}


