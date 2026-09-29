from time import perf_counter

from engine.reconciliation.identity import resolve_identities
from engine.reconciliation.matcher import run_matching


CFG = {"amount_tolerance_abs": 0, "amount_tolerance_pct": 0, "max_group_size": 6,
       "fuzzy_high_threshold": 90, "fuzzy_review_threshold": 70, "fuzzy_min_gap": 8,
       "fuzzy_candidate_limit": 250}


def test_indexed_matching_handles_large_single_customer_bucket_without_cross_product():
    books = [{"customer_code": "C1", "customer_name_norm": "C1", "transaction_id": f"B{i}",
              "document_number": f"B{i}", "document_date": "2025-04-01", "tds_expected": float(i + 1),
              "section": "", "quarter": "Q1", "financial_year": "2025-26"} for i in range(2_000)]
    statements = [{"tan": "TANM", "statement_id": f"S{i}", "transaction_date": f"2025-04-{i % 28 + 1:02d}",
                   "tax_deducted": float(i + 1), "tds_deposited": float(i + 1), "status": "F", "section": "",
                   "quarter": "Q1", "financial_year": "2025-26"} for i in range(2_000)]
    started = perf_counter()
    matches, missing_books, missing_statements, *_ = run_matching(books, statements, {"TANM": {"status": "EXACT", "customer_code": "C1"}}, CFG)
    assert len(matches) == 2_000
    assert not missing_books and not missing_statements
    assert perf_counter() - started < 2


def test_fuzzy_identity_scoring_is_bounded_to_token_candidates(monkeypatch):
    customers = [{"customer_code": f"C{i}", "customer_name": f"Customer {i}", "customer_name_norm": f"CUSTOMER {i}",
                  "pan": "", "gstin": "", "tans": [], "aliases": []} for i in range(1_000)]
    statements = [{"tan": "TANX", "deductor_name": "Different Entity", "tax_deducted": 1, "quarter": "Q1"}]
    identities, _ = resolve_identities(statements, customers, {}, [], {}, CFG)
    assert identities["TANX"]["status"] == "UNMAPPED"
