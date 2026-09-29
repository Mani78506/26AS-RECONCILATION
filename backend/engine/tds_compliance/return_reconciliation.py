"""Phase 6A.2: read-only comparisons of committed evidence. No statutory rules."""

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date
import re

SCHEMA_VERSION = "6A.2"


def value(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None and row[k] != ""), None)


def number(v):
    try:
        result = Decimal(str(v))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def compare(left, right, numeric=False):
    if numeric:
        left, right = number(left), number(right)
    if left is None or right is None or left == "" or right == "":
        return "REVIEW_REQUIRED"
    return "MATCHED" if left == right else "DIFFERENCE"


def pan(row):
    v = str(row.get("deductee_pan") or "").strip().upper()
    return v if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v) else None


def in_period(book, context):
    """Use source period metadata; never compare another quarter's transactions."""
    for key in ("financial_year", "quarter"):
        if context.get(key) and book.get(key) and context[key] != book[key]:
            return False
    event = value(book, "credit_date", "payment_date", "transaction_date")
    if event:
        try:
            day = date.fromisoformat(str(event)[:10])
        except ValueError:
            return True  # Missing/invalid period evidence is handled as review below.
        start = day.year if day.month >= 4 else day.year - 1
        if (
            context.get("financial_year")
            and context["financial_year"] != f"{start}-{str(start + 1)[2:]}"
        ):
            return False
        quarter = f"Q{((day.month - 4) % 12) // 3 + 1}"
        if context.get("quarter") and context["quarter"] != quarter:
            return False
    return True


def _index_add(index, key, position):
    if key not in (None, ""):
        index[str(key).strip()].append(position)


def build_match_indexes(ledger):
    """Build bounded evidence indexes once; reconciliation must not scan each row."""
    indexes = {
        "transaction": defaultdict(list),
        "document": defaultdict(list),
        "pan": defaultdict(list),
        "identity": defaultdict(list),
        "authoritative_name": defaultdict(list),
    }
    for position, book in enumerate(ledger):
        for field in ("transaction_id", "source_reference"):
            _index_add(indexes["transaction"], book.get(field), position)
        for field in ("payment_reference", "invoice_number", "document_identifier"):
            _index_add(indexes["document"], book.get(field), position)
        _index_add(indexes["pan"], pan(book), position)
        _index_add(indexes["identity"], book.get("deductee_identity_id"), position)
        if book.get("identity_authoritative") is True and book.get("deductee_name"):
            _index_add(
                indexes["authoritative_name"],
                str(book["deductee_name"]).strip().upper(),
                position,
            )
    return indexes


def candidates(row, ledger, indexes=None):
    # Local DD sequence numbers in Protean sources are not payment references.
    indexes = indexes or build_match_indexes(ledger)
    references = {row[k] for k in ("transaction_id", "source_reference") if row.get(k)}
    if (
        not references
        and row.get("parser_name") != "protean-caret"
        and row.get("return_transaction_id")
    ):
        references.add(row["return_transaction_id"])
    document_references = {
        row[field]
        for field in ("payment_reference", "invoice_number", "document_identifier")
        if row.get(field)
    }
    identity_references = set(indexes["identity"].get(str(row.get("deductee_identity_id") or "").strip(), []))
    if row.get("identity_authoritative") is True and row.get("deductee_name"):
        identity_references.update(indexes["authoritative_name"].get(str(row["deductee_name"]).strip().upper(), []))
    tiers = (
        ("EXACT_TRANSACTION_REFERENCE", {index for reference in references for index in indexes["transaction"].get(str(reference).strip(), [])}),
        ("EXACT_DOCUMENT_REFERENCE", {index for reference in document_references for index in indexes["document"].get(str(reference).strip(), [])}),
        ("EXACT_PAN", set(indexes["pan"].get(str(pan(row) or "").strip(), []))),
        ("AUTHORITATIVE_IDENTITY", identity_references),
        ("EXPLICIT_ASSIGNMENT_MAPPING", set(indexes["transaction"].get(str(row.get("assigned_payment_transaction_id") or "").strip(), []))),
    )
    for method, found in tiers:
        if found:
            return method, sorted(found)
    return None, []


def reconcile(
    return_rows,
    calculation_rows,
    ledger_rows,
    *,
    challans=(),
    deposit_rows=(),
    interest_rows=(),
    context=None,
):
    context = context or {}
    ledger_rows = [b for b in ledger_rows if in_period(b, context)]
    match_indexes = build_match_indexes(ledger_rows)
    transaction_ids = {b.get("transaction_id") for b in ledger_rows}
    calculation_rows = [
        c for c in calculation_rows if c.get("transaction_id") in transaction_ids
    ]
    deposit_rows = [
        d for d in deposit_rows if d.get("transaction_id") in transaction_ids
    ]
    calculation_by_transaction = defaultdict(list)
    deposit_by_transaction = defaultdict(list)
    interest_by_transaction = defaultdict(list)
    for item in calculation_rows:
        calculation_by_transaction[item.get("transaction_id")].append(item)
    for item in deposit_rows:
        deposit_by_transaction[item.get("transaction_id")].append(item)
    for item in interest_rows:
        interest_by_transaction[item.get("transaction_id")].append(item)
    challans_by_batch = defaultdict(list)
    for challan in challans:
        challans_by_batch[tuple(challan.get("raw_fields", [])[2:4])].append(challan)
    results, referenced = [], set()
    resolutions = [candidates(r, ledger_rows, match_indexes) for r in return_rows]
    counts = Counter(ids[0] for _, ids in resolutions if len(ids) == 1)
    for row, (method, ids) in zip(return_rows, resolutions):
        referenced.update(ids)
        book = ledger_rows[ids[0]] if len(ids) == 1 else {}
        tx = book.get("transaction_id")
        calculations = calculation_by_transaction.get(tx, [])
        calc = calculations[0] if len(calculations) == 1 else {}
        confirmed = len(ids) == 1 and counts[ids[0]] == 1
        if pan(row) and pan(book) and pan(row) != pan(book):
            confirmed = False
        row_references = {
            row[field]
            for field in ("transaction_id", "source_reference")
            if row.get(field)
        }
        conflict = (
            len(ids) > 1
            and len(row_references) > 1
            and method in {"EXACT_TRANSACTION_REFERENCE", "EXACT_DOCUMENT_REFERENCE"}
        )
        identity = "CONFLICT" if conflict else "CONFIRMED" if confirmed else "REVIEW_REQUIRED"
        no_identity = not any(
            (
                pan(row),
                row.get("transaction_id"),
                row.get("source_reference"),
                row.get("deductee_identity_id"),
                row.get("assigned_payment_transaction_id"),
                row.get("invoice_number"),
                row.get("document_identifier"),
                row.get("payment_reference"),
                (
                    row.get("return_transaction_id")
                    if row.get("parser_name") != "protean-caret"
                    else None
                ),
                method,
            )
        )
        missing = not ids and not no_identity
        payment = {
            "identity": (
                compare(pan(row), pan(book))
                if pan(row)
                else (
                    "MATCHED"
                    if confirmed
                    and method
                    in {"AUTHORITATIVE_IDENTITY", "EXPLICIT_ASSIGNMENT_MAPPING"}
                    else "REVIEW_REQUIRED"
                )
            ),
            "section": compare(
                row.get("section"),
                value(calc, "section_reference", "section_input", "section"),
            ),
            "date": compare(
                row.get("payment_or_credit_date"), calc.get("effective_event_date")
            ),
            "amount": compare(
                value(row, "amount_paid_or_credited", "amount_paid"),
                book.get("amount"),
                True,
            ),
        }
        if row.get("deductee_name") and book.get("deductee_name"):
            payment["name"] = compare(
                row["deductee_name"].strip().upper(),
                book["deductee_name"].strip().upper(),
            )
        actual = number(value(calc, "tds_deducted", "actual_tds"))
        expected, returned = number(calc.get("expected_tds")), number(
            value(row, "total_tax_deducted", "tds_amount")
        )
        difference = (
            float(returned - expected)
            if returned is not None and expected is not None and confirmed
            else None
        )
        tax_status = (
            "MISSING_IN_BOOKS"
            if missing
            else (
                "REVIEW_REQUIRED"
                if not confirmed
                or difference is None
                or calc.get("calculation_status") != "CALCULATED"
                else "MATCHED" if difference == 0 else "TAX_DIFFERENCE"
            )
        )
        payment_status = (
            "MISSING_IN_BOOKS"
            if missing
            else (
                "REVIEW_REQUIRED"
                if not confirmed or "REVIEW_REQUIRED" in payment.values()
                else (
                    "PAYMENT_DIFFERENCE"
                    if "DIFFERENCE" in payment.values()
                    else "MATCHED"
                )
            )
        )
        deposits = deposit_by_transaction.get(tx, [])
        deposit = deposits[0] if confirmed and len(deposits) == 1 else {}
        entries = deposit.get("evidence_entries") or []
        # Relationships are consumed from Phase 4; never construct or allocate one.
        challan_status = (
            "MISSING_IN_DEPOSIT_EVIDENCE" if not deposits else "REVIEW_REQUIRED"
        )
        if (
            deposit
            and not entries
            and deposit.get("deposit_status") == "MISSING_DEPOSIT_EVIDENCE"
        ):
            challan_status = "MISSING_IN_DEPOSIT_EVIDENCE"
        challan_comparisons = []
        if (
            deposit.get("relationship_status") == "ESTABLISHED"
            and entries
            and len(deposit.get("liability_ids", [tx])) == 1
        ):
            for evidence in entries:
                source_challans = (
                    challans_by_batch.get(tuple(row.get("raw_fields", [])[2:4]), [])
                    if row.get("parser_name") == "protean-caret"
                    else challans
                )
                matches = [
                    c
                    for c in source_challans
                    if evidence.get("challan_number")
                    and c.get("challan_serial_number") == evidence.get("challan_number")
                ]
                if row.get("parser_name") == "protean-caret":
                    # The source's batch/challan reference is authoritative, including
                    # when the printed serial differs from the frozen evidence.
                    matches = source_challans
                source = matches[0] if len(matches) == 1 else {}
                checks = {
                    "number": compare(
                        source.get("challan_serial_number"),
                        evidence.get("challan_number"),
                    ),
                    "bsr": compare(source.get("bsr_code"), evidence.get("bsr_code")),
                    "date": compare(
                        source.get("challan_deposit_date"), evidence.get("deposit_date")
                    ),
                    "amount": compare(
                        source.get("challan_amount"),
                        value(evidence, "total_amount", "amount_deposited"),
                        True,
                    ),
                }
                challan_comparisons.append(
                    {"return": source, "evidence": evidence, "comparisons": checks}
                )
            statuses = [
                s for c in challan_comparisons for s in c["comparisons"].values()
            ]
            statuses.append(
                compare(
                    row.get("total_tax_deposited"), deposit.get("deposited_tds"), True
                )
            )
            challan_status = (
                "REVIEW_REQUIRED"
                if "REVIEW_REQUIRED" in statuses
                or any(e.get("validation_status") == "INVALID" for e in entries)
                else "CHALLAN_DIFFERENCE" if "DIFFERENCE" in statuses else "MATCHED"
            )
        if deposit.get("overall_status") == "REVIEW_REQUIRED":
            challan_status = "REVIEW_REQUIRED"
        interests = interest_by_transaction.get(tx, [])
        interest = interests[0] if confirmed and len(interests) == 1 else {}
        interest_status = (
            "AVAILABLE"
            if interest.get("overall_status")
            in {"NO_INTEREST_INDICATED", "INTEREST_DUE"}
            else "REVIEW_REQUIRED"
        )
        if interest_status == "AVAILABLE" and number(
            interest.get("interest_difference")
        ) not in (None, Decimal(0)):
            interest_status = "INTEREST_DIFFERENCE"
        statuses = (payment_status, tax_status, challan_status, interest_status)
        overall = (
            "REVIEW_REQUIRED"
            if not calculation_rows
            else (
                "MISSING_IN_BOOKS"
                if missing
                else (
                    "REVIEW_REQUIRED"
                    if "REVIEW_REQUIRED" in statuses
                    else (
                        "DIFFERENCE"
                        if any(s.endswith("DIFFERENCE") for s in statuses)
                        else (
                            "MISSING_IN_DEPOSIT_EVIDENCE"
                            if challan_status == "MISSING_IN_DEPOSIT_EVIDENCE"
                            else "MATCHED"
                        )
                    )
                )
            )
        )
        if conflict:
            overall = "CONFLICT"
        if context.get("statement_type") == "CORRECTION":
            # A correction is kept isolated; this workflow does not reconstruct
            # the final amended return from a regular/correction chain.
            overall = "CORRECTION_REVIEW_REQUIRED"
        result = {
            **context,
            "schema_version": SCHEMA_VERSION,
            "return_row_id": row.get("return_row_id")
            or f"{context.get('return_artifact_id')}:{row.get('source_row_number')}",
            "return_transaction_id": row.get("return_transaction_id"),
            "payment_transaction_id": tx,
            "transaction_id": tx,
            "payment_ledger_version_id": book.get("ledger_version_id"),
            "calculation_result_id": calc.get("result_id")
            or (f"{calc.get('calculation_id')}:{tx}" if calc else None),
            "deposit_evidence_id": [e.get("evidence_id") for e in entries],
            "interest_result_id": interest.get("result_id")
            or (f"{interest.get('interest_run_id')}:{tx}" if interest else None),
            "identity_status": identity,
            "payment_status": payment_status,
            "tax_status": tax_status,
            "challan_status": challan_status,
            "interest_status": interest_status,
            "overall_status": overall,
            "status": overall,
            "match_method": method,
            "candidate_transaction_ids": [
                ledger_rows[i].get("transaction_id") for i in ids
            ],
            "payment_comparisons": payment,
            "return_tds": float(returned) if returned is not None else None,
            "return_tds_amount": float(returned) if returned is not None else None,
            "expected_tds": float(expected) if expected is not None else None,
            "actual_tds": value(calc, "tds_deducted", "actual_tds"),
            "difference": difference,
            "deductee_pan": row.get("deductee_pan"),
            "interest_evidence_available": bool(interest),
            "interest_difference": interest.get("interest_difference"),
            "return": row,
            "books": book,
            "calculation": calc,
            "deposit": deposit,
            "interest": interest,
            "challan_comparisons": challan_comparisons,
            "source_references": {
                "return": {
                    k: row.get(k)
                    for k in (
                        "source_filename",
                        "source_hash",
                        "source_row_number",
                        "source_line_number",
                    )
                },
                "books": {
                    k: book.get(k)
                    for k in (
                        "source_file_id",
                        "source_file_name",
                        "source_row_number",
                        "source_reference",
                    )
                },
                "calculation_id": calc.get("calculation_id"),
                "deposit_run_id": deposit.get("deposit_run_id"),
                "interest_run_id": interest.get("interest_run_id"),
            },
            "reason": (
                "Evidence comparisons are independent; differences are not compliance conclusions."
                if confirmed
                else "No unique authoritative payment relationship; inspect candidates and source references."
            ),
        }
        result.update(
            {
                "classification_snapshot": {
                    k: calc.get(k)
                    for k in (
                        "payment_nature",
                        "payment_nature_status",
                        "payment_nature_source",
                        "payment_nature_confidence",
                        "classification_reason",
                        "section_reference",
                        "rule_snapshot",
                    )
                },
                "actual_tds_difference": (
                    float(returned - actual)
                    if confirmed and returned is not None and actual is not None
                    else None
                ),
                "actual_tds_status": (
                    compare(returned, actual, True) if confirmed else "REVIEW_REQUIRED"
                ),
                "return_tax_deposited": row.get("total_tax_deposited"),
                "deposit_tax_difference": (
                    float(
                        number(row.get("total_tax_deposited"))
                        - number(deposit.get("deposited_tds"))
                    )
                    if confirmed
                    and number(row.get("total_tax_deposited")) is not None
                    and number(deposit.get("deposited_tds")) is not None
                    else None
                ),
            }
        )
        if result["actual_tds_status"] == "DIFFERENCE" and overall == "MATCHED":
            result["overall_status"] = result["status"] = "DIFFERENCE"
        explanations = []
        review_reasons = []
        if context.get("statement_type") == "CORRECTION":
            review_reasons.append("Correction statement cannot be deterministically reconstructed into a final amended position.")
        if conflict:
            review_reasons.append("Conflicting authoritative transaction or document identifiers resolve to multiple payment candidates.")
        elif len(ids) > 1:
            review_reasons.append("Ambiguous identity resolves to multiple payment candidates.")
        if not confirmed:
            explanations.append(
                "No unique authoritative payment relationship; inspect candidate references."
            )
            if not review_reasons:
                review_reasons.append("Identity cannot be confirmed from authoritative evidence.")
        if not calc:
            explanations.append("Frozen Phase 2 result is missing or ambiguous.")
            review_reasons.append("Frozen Phase 2 calculation snapshot is missing or ambiguous.")
        for field, status in payment.items():
            if status != "MATCHED":
                explanations.append(f"Payment {field}: {status}.")
        if tax_status != "MATCHED":
            explanations.append(f"Expected TDS comparison: {tax_status}.")
        if result["actual_tds_status"] == "DIFFERENCE":
            explanations.append("Return TDS differs from frozen actual TDS.")
        if challan_status != "MATCHED":
            explanations.append(f"Challan/deposit comparison: {challan_status}.")
            if challan_status == "MISSING_IN_DEPOSIT_EVIDENCE":
                review_reasons.append("Phase 4 deposit evidence is missing.")
            elif not any(c.get("challan_serial_number") for c in challans):
                review_reasons.append("Return source does not provide a challan serial number for deterministic comparison.")
            else:
                review_reasons.append("Challan relationship or evidence requires review.")
        if interest_status == "INTEREST_DIFFERENCE":
            explanations.append(
                "The existing Phase 5 snapshot represents an interest difference."
            )
        elif interest_status != "AVAILABLE":
            explanations.append(
                "Phase 5 interest evidence is missing, ambiguous or requires review."
            )
        result["reason"] = (
            " ".join(explanations)
            or "Authoritative identity and available frozen payment, tax and deposit evidence agree; Phase 5 evidence is available."
        )
        result["review_reasons"] = review_reasons
        results.append(result)
    for i, book in enumerate(ledger_rows):
        if i in referenced:
            continue
        calculation_candidates = calculation_by_transaction.get(book.get("transaction_id"), [])
        calc = calculation_candidates[0] if len(calculation_candidates) == 1 else {}
        status = (
            "REVIEW_REQUIRED"
            if context.get("statement_type") == "CORRECTION" or not calc
            else "MISSING_IN_RETURN"
        )
        results.append(
            {
                **context,
                "schema_version": SCHEMA_VERSION,
                "return_row_id": None,
                "payment_transaction_id": book.get("transaction_id"),
                "transaction_id": book.get("transaction_id"),
                "payment_ledger_version_id": book.get("ledger_version_id"),
                "calculation_result_id": (
                    f"{calc.get('calculation_id')}:{book.get('transaction_id')}"
                    if calc
                    else None
                ),
                "deposit_evidence_id": [],
                "interest_result_id": None,
                "interest_evidence_available": False,
                "interest_difference": None,
                "return_tds": None,
                "return_tds_amount": None,
                "actual_tds": value(calc, "tds_deducted", "actual_tds"),
                "difference": None,
                "identity_status": "REVIEW_REQUIRED",
                "payment_status": status,
                "tax_status": status,
                "challan_status": "REVIEW_REQUIRED",
                "interest_status": "REVIEW_REQUIRED",
                "overall_status": status,
                "status": status,
                "expected_tds": calc.get("expected_tds"),
                "books": book,
                "calculation": calc,
                "return": {},
                "source_references": {
                    "books": {
                        k: book.get(k)
                        for k in (
                            "source_file_id",
                            "source_file_name",
                            "source_row_number",
                            "source_reference",
                        )
                    },
                    "calculation_id": calc.get("calculation_id"),
                },
                "reason": "No return relationship in this artifact. Correction omissions require review of the regular statement.",
            }
        )
    status_counts = Counter(result["overall_status"] for result in results)
    summary = {
        "total": len(results),
        "total_audit_results": len(results),
        "total_return_rows": len(return_rows),
        "return_rows": len(return_rows),
        "matched": status_counts["MATCHED"],
        "review_rows": status_counts["REVIEW_REQUIRED"],
        "conflicts": status_counts["CONFLICT"],
        "correction_review_required": status_counts["CORRECTION_REVIEW_REQUIRED"],
        "exceptions": len(results) - status_counts["MATCHED"],
        "status_counts": dict(status_counts),
        "status_count_total": sum(status_counts.values()),
    }
    for key, field, status in (
        ("missing_in_return", "tax_status", "MISSING_IN_RETURN"),
        ("missing_in_books", "tax_status", "MISSING_IN_BOOKS"),
        ("tax_differences", "tax_status", "TAX_DIFFERENCE"),
        ("challan_differences", "challan_status", "CHALLAN_DIFFERENCE"),
        ("missing_deposit_evidence", "challan_status", "MISSING_IN_DEPOSIT_EVIDENCE"),
    ):
        summary[key] = sum(r[field] == status for r in results)
    for field in ("amount", "section", "date"):
        summary[f"{field}_differences"] = sum(
            r.get("payment_comparisons", {}).get(field) == "DIFFERENCE" for r in results
        )

    def total(rows, *keys):
        values = [number(value(r, *keys)) for r in rows]
        return (
            float(sum(values, Decimal(0)))
            if all(v is not None for v in values)
            else None
        )

    summary.update(
        return_amount_paid=total(return_rows, "amount_paid_or_credited", "amount_paid"),
        books_payment_amount=total(ledger_rows, "amount"),
        return_tds=total(return_rows, "total_tax_deducted", "tds_amount"),
        expected_tds=(
            total(calculation_rows, "expected_tds")
            if calculation_rows
            and len(calculation_rows) == len(ledger_rows)
            and len({c.get("transaction_id") for c in calculation_rows})
            == len(calculation_rows)
            else None
        ),
        return_tax_deposited=total(return_rows, "total_tax_deposited"),
    )
    summary["tds_difference"] = (
        float(
            Decimal(str(summary["return_tds"])) - Decimal(str(summary["expected_tds"]))
        )
        if summary["return_tds"] is not None and summary["expected_tds"] is not None
        else None
    )
    unique_evidence = {
        e["evidence_id"]: e
        for d in deposit_rows
        if d.get("relationship_status") == "ESTABLISHED"
        for e in d.get("evidence_entries", [])
        if e.get("evidence_id") and e.get("validation_status") != "INVALID"
    }
    summary["approved_deposit_evidence"] = (
        total(list(unique_evidence.values()), "tds_amount") if unique_evidence else None
    )
    return deepcopy(
        {
            "items": results,
            "summary": summary,
            "overall_status": (
                "REVIEW_REQUIRED"
                if not calculation_rows
                or any(r["status"] == "REVIEW_REQUIRED" for r in results)
                else (
                    "MATCHED"
                    if all(r["status"] == "MATCHED" for r in results)
                    else "EXCEPTIONS"
                )
            ),
        }
    )
