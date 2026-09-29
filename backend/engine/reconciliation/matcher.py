from collections import defaultdict
from bisect import bisect_left, bisect_right
from itertools import combinations

from .identity import EXACT, HIGH_CONFIDENCE

MATCHED_CLAIMABLE, MATCHED_NOT_CLAIMABLE = "MATCHED_CLAIMABLE", "MATCHED_NOT_CLAIMABLE"
AMOUNT_MISMATCH, MISSING_IN_BOOKS, MISSING_IN_26AS = "AMOUNT_MISMATCH", "MISSING_IN_BOOKS", "MISSING_IN_26AS"
IDENTITY_UNMAPPED, DUPLICATE_BOOK, DUPLICATE_26AS = "IDENTITY_UNMAPPED", "DUPLICATE_BOOK", "DUPLICATE_26AS"
RESULT_TYPES = [MATCHED_CLAIMABLE, MATCHED_NOT_CLAIMABLE, AMOUNT_MISMATCH, MISSING_IN_BOOKS, MISSING_IN_26AS, IDENTITY_UNMAPPED, DUPLICATE_BOOK, DUPLICATE_26AS]
MATCH_METHODS = ["EXACT_1_TO_1_SAME_QUARTER", "EXACT_1_TO_1_OUTSIDE_QUARTER", "GROUP_SAME_QUARTER", "GROUP_OUTSIDE_QUARTER", "UNMATCHED"]
CLAIMABLE, NOT_CLAIMABLE, NOT_APPLICABLE = "CLAIMABLE", "NOT_CLAIMABLE", "NOT_APPLICABLE"

QUARTER_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
_MAPPED = (EXACT, HIGH_CONFIDENCE)
MAX_INDIVIDUAL_GROUP_SCOPE = 500


def _tol(amount, cfg):
    return max(cfg["amount_tolerance_abs"], abs(amount) * cfg["amount_tolerance_pct"] / 100)


def _within(a, b, cfg):
    return abs(a - b) <= _tol(a, cfg)


def _qdist(a, b):
    return abs(QUARTER_ORDER.get(a, 0) - QUARTER_ORDER.get(b, 0))


def flag_duplicates(books, stmts):
    seen, dup_b = {}, []
    for b in books:
        key = (b["customer_code"] or b["customer_name_norm"], b["document_number"] or b["transaction_id"], b["document_date"], b["tds_expected"])
        if key in seen:
            b["duplicate_of"] = seen[key]["transaction_id"]
            dup_b.append(b)
        else:
            seen[key] = b
    seen, dup_s = {}, []
    for s in stmts:
        key = (s["tan"], s["transaction_date"], s["section"], s["tax_deducted"], s["tds_deposited"], s["status"])
        if key in seen:
            s["duplicate_of"] = seen[key]["statement_id"]
            dup_s.append(s)
        else:
            seen[key] = s
    return dup_b, dup_s


def _pick(cands, b, same_quarter):
    return min(cands, key=lambda s: (0 if s["section"] and s["section"] == b["section"] else 1, 0 if same_quarter else _qdist(b["quarter"], s["quarter"]), abs((b["tds_expected"] or 0) - (s["tax_deducted"] or 0))))


def _subset_summing(pool, target, cfg, max_size):
    sizes = range(2, min(len(pool), max_size) + 1)
    if len(pool) > 14:
        sizes = range(2, min(len(pool), 3) + 1)
    for size in sizes:
        for combo in combinations(pool, size):
            if _within(target, sum(x["_amt"] for x in combo), cfg):
                return list(combo)
    return None


def _amount_index(rows):
    """Sorted, source-order-stable amount index used only to narrow candidates."""
    indexed = sorted((row["_amt"], position, row) for position, row in enumerate(rows))
    return [item[0] for item in indexed], indexed


def _nearby(amounts, indexed, target, tolerance, used, limit=None):
    start = bisect_left(amounts, target - tolerance)
    end = bisect_right(amounts, target + tolerance)
    candidates = [item for item in indexed[start:end] if id(item[2]) not in used]
    candidates.sort(key=lambda item: item[1])
    if limit is not None and len(candidates) > limit:
        candidates.sort(key=lambda item: (abs(item[0] - target), item[1]))
        candidates = candidates[:limit]
        candidates.sort(key=lambda item: item[1])
    return [item[2] for item in candidates]


def _closest_pool(rows, target, used, limit=14):
    """Bound subset matching without ever constructing an all-to-all graph."""
    amounts, indexed = _amount_index(rows)
    pivot = bisect_left(amounts, target)
    selected, left, right = [], pivot - 1, pivot
    while len(selected) < limit and (left >= 0 or right < len(indexed)):
        left_item = indexed[left] if left >= 0 else None
        right_item = indexed[right] if right < len(indexed) else None
        choose_left = right_item is None or (left_item is not None and (abs(left_item[0] - target), left_item[1]) <= (abs(right_item[0] - target), right_item[1]))
        item = left_item if choose_left else right_item
        if choose_left: left -= 1
        else: right += 1
        if id(item[2]) not in used:
            selected.append(item)
    selected.sort(key=lambda item: item[1])
    return [item[2] for item in selected]


def _nearest(amounts, indexed, target, used, accept=lambda row: True):
    pivot = bisect_left(amounts, target)
    left, right, best = pivot - 1, pivot, None
    while left >= 0 or right < len(indexed):
        left_item = indexed[left] if left >= 0 else None
        right_item = indexed[right] if right < len(indexed) else None
        choose_left = right_item is None or (left_item is not None and (abs(left_item[0] - target), left_item[1]) <= (abs(right_item[0] - target), right_item[1]))
        item = left_item if choose_left else right_item
        if choose_left: left -= 1
        else: right += 1
        if best is not None and abs(item[0] - target) > abs(best[0] - target):
            break
        if id(item[2]) not in used and accept(item[2]) and (best is None or (abs(item[0] - target), item[1]) < (abs(best[0] - target), best[1])):
            best = item
    return best[2] if best else None


def match_customer(books, stmts, cfg, new_group_id):
    matches, used_b, used_s = [], set(), set()
    for b in books:
        b["_amt"] = b["tds_expected"] or 0
    for s in stmts:
        s["_amt"] = s["tax_deducted"] or 0

    def rem_b(q=None, fy=None):
        return [b for b in books if id(b) not in used_b and (q is None or b["quarter"] == q) and (fy is None or b["financial_year"] == fy)]

    def rem_s(q=None, fy=None):
        return [s for s in stmts if id(s) not in used_s and (q is None or s["quarter"] == q) and (fy is None or s["financial_year"] == fy)]

    def record(method, bs, ss):
        matches.append({"method": method, "books": bs, "stmts": ss, "group_id": new_group_id() if len(bs) + len(ss) > 2 else None})
        used_b.update(id(x) for x in bs)
        used_s.update(id(x) for x in ss)

    statement_scope_index = {}
    for same in (True, False):
        for b in books:
            scope = (b["financial_year"], b["quarter"] if same else None)
            if scope not in statement_scope_index:
                pool = [s for s in stmts if s["financial_year"] == scope[0] and (not same or s["quarter"] == scope[1])]
                statement_scope_index[scope] = _amount_index(pool)

    for same in (True, False):
        for b in sorted(rem_b(), key=lambda x: -x["_amt"]):
            # Same-quarter matching uses the direct bucket; the outside-quarter
            # pass intentionally retains its established FY-wide semantics.
            scope = (b["financial_year"], b["quarter"] if same else None)
            amounts, indexed = statement_scope_index[scope]
            cands = _nearby(amounts, indexed, b["_amt"], _tol(b["_amt"], cfg), used_s)
            if not same:
                cands = [s for s in cands if s["quarter"] != b["quarter"]]
            if cands:
                record("EXACT_1_TO_1_SAME_QUARTER" if same else "EXACT_1_TO_1_OUTSIDE_QUARTER", [b], [_pick(cands, b, same)])

    same_quarter_scopes = sorted({(x["financial_year"], x["quarter"]) for x in books + stmts if x["financial_year"] and x["quarter"]}, key=lambda scope: (scope[0], QUARTER_ORDER.get(scope[1], 9)))
    for fy, quarter in same_quarter_scopes:
        scoped_books, scoped_stmts = rem_b(quarter, fy), rem_s(quarter, fy)
        # The aggregate outcome is already part of the established matching
        # semantics.  For a large scope, use it before individual subset search
        # so a 100k × 10k candidate graph is never constructed.
        if len(scoped_books) + len(scoped_stmts) > MAX_INDIVIDUAL_GROUP_SCOPE:
            if scoped_books and scoped_stmts and _within(sum(x["_amt"] for x in scoped_books), sum(x["_amt"] for x in scoped_stmts), cfg):
                record("GROUP_SAME_QUARTER", scoped_books, scoped_stmts)
            continue
        for b in sorted(rem_b(quarter, fy), key=lambda x: -x["_amt"]):
            combo = _subset_summing(_closest_pool(rem_s(quarter, fy), b["_amt"], used_s), b["_amt"], cfg, cfg["max_group_size"])
            if combo:
                record("GROUP_SAME_QUARTER", [b], combo)
        for s in sorted(rem_s(quarter, fy), key=lambda x: -x["_amt"]):
            combo = _subset_summing(_closest_pool(rem_b(quarter, fy), s["_amt"], used_b), s["_amt"], cfg, cfg["max_group_size"])
            if combo:
                record("GROUP_SAME_QUARTER", combo, [s])
        rb, rs = rem_b(quarter, fy), rem_s(quarter, fy)
        if len(rb) >= 1 and len(rs) >= 1 and len(rb) + len(rs) > 2 and _within(sum(x["_amt"] for x in rb), sum(x["_amt"] for x in rs), cfg):
            record("GROUP_SAME_QUARTER", rb, rs)

    financial_years = sorted({x["financial_year"] for x in books + stmts if x["financial_year"]})
    for fy in financial_years:
        yearly_books, yearly_stmts = rem_b(fy=fy), rem_s(fy=fy)
        if len(yearly_books) + len(yearly_stmts) > MAX_INDIVIDUAL_GROUP_SCOPE:
            if yearly_books and yearly_stmts and _within(sum(x["_amt"] for x in yearly_books), sum(x["_amt"] for x in yearly_stmts), cfg):
                record("GROUP_OUTSIDE_QUARTER", yearly_books, yearly_stmts)
            continue
        for b in sorted(rem_b(fy=fy), key=lambda x: -x["_amt"]):
            combo = _subset_summing(_closest_pool(rem_s(fy=fy), b["_amt"], used_s), b["_amt"], cfg, cfg["max_group_size"])
            if combo:
                record("GROUP_OUTSIDE_QUARTER", [b], combo)
        for s in sorted(rem_s(fy=fy), key=lambda x: -x["_amt"]):
            combo = _subset_summing(_closest_pool(rem_b(fy=fy), s["_amt"], used_b), s["_amt"], cfg, cfg["max_group_size"])
            if combo:
                record("GROUP_OUTSIDE_QUARTER", combo, [s])
        rb, rs = rem_b(fy=fy), rem_s(fy=fy)
        if len(rb) >= 1 and len(rs) >= 1 and len(rb) + len(rs) > 2 and _within(sum(x["_amt"] for x in rb), sum(x["_amt"] for x in rs), cfg):
            record("GROUP_OUTSIDE_QUARTER", rb, rs)

    for same in (True, False):
        for b in sorted(rem_b(), key=lambda x: -x["_amt"]):
            scope = (b["financial_year"], b["quarter"] if same else None)
            amounts, indexed = statement_scope_index[scope]
            closest = _nearest(amounts, indexed, b["_amt"], used_s, lambda s: same or s["quarter"] != b["quarter"])
            if closest:
                record("AMOUNT_MISMATCH", [b], [closest])
    return matches, rem_b(), rem_s()


def run_matching(books, stmts, identities, cfg):
    dup_b, dup_s = flag_duplicates(books, stmts)
    dup_ids = {id(x) for x in dup_b + dup_s}
    by_customer_b, by_customer_s = defaultdict(list), defaultdict(list)
    for b in books:
        if id(b) not in dup_ids:
            by_customer_b[b["customer_code"] or b["customer_name_norm"]].append(b)
    unmapped_s = []
    for s in stmts:
        if id(s) in dup_ids:
            continue
        ident = identities[s["tan"]]
        if ident["status"] in _MAPPED and ident["customer_code"]:
            by_customer_s[ident["customer_code"]].append(s)
        else:
            unmapped_s.append(s)
    counter = {"n": 0}

    def new_group_id():
        counter["n"] += 1
        return f"G{counter['n']:04d}"

    all_matches, missing_26as, missing_books = [], [], []
    for code in sorted(set(by_customer_b) | set(by_customer_s)):
        m, rb, rs = match_customer(by_customer_b.get(code, []), by_customer_s.get(code, []), cfg, new_group_id)
        all_matches.extend(m)
        missing_26as.extend(rb)
        missing_books.extend(rs)
    return all_matches, missing_26as, missing_books, unmapped_s, dup_b, dup_s
