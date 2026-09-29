from collections import defaultdict

from rapidfuzz import fuzz

from .normalizer import core_name

EXACT, HIGH_CONFIDENCE, REVIEW_REQUIRED, UNMAPPED = "EXACT", "HIGH_CONFIDENCE", "REVIEW_REQUIRED", "UNMAPPED"


def _merge_customers(customers: list) -> dict:
    merged = {}
    for c in customers:
        code = c["customer_code"]
        if not code:
            continue
        m = merged.setdefault(code, {**c, "tans": [], "aliases": []})
        m["tans"] = sorted(set(m["tans"]) | set(c["tans"]))
        m["aliases"] = sorted(set(m["aliases"]) | set(c["aliases"]))
        m["pan"] = m["pan"] or c["pan"]
        m["gstin"] = m["gstin"] or c["gstin"]
    return merged


def resolve_identities(statements: list, customers: list, saved_mappings: dict, saved_aliases: list, decisions: dict, cfg: dict):
    by_code = _merge_customers(customers)
    tan_index = {t: code for code, c in by_code.items() for t in c["tans"]}
    alias_index = {}
    for code, c in by_code.items():
        alias_index[c["customer_name_norm"]] = (code, "EXACT_NAME")
        for a in c["aliases"]:
            alias_index.setdefault(core_name(a), (code, "MASTER_ALIAS"))
    for a in saved_aliases:
        alias_index.setdefault(a["alias_norm"], (a["customer_code"], "SAVED_ALIAS"))
    # Build fuzzy candidate indexes once.  A TAN is compared only with names
    # sharing a meaningful normalized token; global TAN × customer scoring is
    # prohibitive on real Books exports and cannot yield a high token-set score
    # for wholly unrelated names.
    fuzzy_names, token_index = {}, defaultdict(set)
    for code, c in by_code.items():
        names = [c["customer_name_norm"]] + [core_name(a) for a in c["aliases"]]
        fuzzy_names[code] = names
        for name in names:
            for token in set(name.split()):
                if len(token) > 1:
                    token_index[token].add(code)

    groups = defaultdict(list)
    for s in statements:
        groups[s["tan"]].append(s)

    identities = {}
    for tan, rows in groups.items():
        names = sorted({r["deductor_name"] for r in rows}, key=lambda n: -sum(1 for r in rows if r["deductor_name"] == n))
        name = names[0] if names else ""
        name_norm = core_name(name)
        decision = decisions.get(tan)
        rec = {
            "tan": tan, "deductor_name": name, "other_names": names[1:], "transaction_count": len(rows),
            "total_tax_deducted": round(sum(r["tax_deducted"] or 0 for r in rows), 2),
            "quarters": sorted({r["quarter"] for r in rows if r["quarter"]}),
            "customer_code": None, "customer_name": None, "customer_pan": None, "customer_gstin": None,
            "status": UNMAPPED, "method": "NONE", "score": 0.0, "confidence": 0.0, "confidence_gap": 0.0,
            "signals": [], "candidates": [], "historical_mapping": saved_mappings.get(tan), "decision": decision,
        }

        def assign(code, method, score, signal):
            c = by_code.get(code)
            rec.update({"customer_code": code, "customer_name": c["customer_name"] if c else code, "customer_pan": c["pan"] if c else "", "customer_gstin": c["gstin"] if c else "", "method": method, "score": score, "confidence": score, "status": EXACT})
            rec["signals"].append(signal)

        if tan in tan_index:
            assign(tan_index[tan], "CUSTOMER_MASTER", 1.0, f"TAN {tan} is listed against customer {tan_index[tan]} in the Customer Master")
        elif tan in saved_mappings and saved_mappings[tan]["customer_code"] in by_code:
            m = saved_mappings[tan]
            assign(m["customer_code"], "SAVED_MAPPING", 1.0, f"Mapping confirmed by {m.get('reviewer', 'reviewer')} on {str(m.get('timestamp', ''))[:10]}")
        elif name_norm in alias_index and alias_index[name_norm][0] in by_code:
            code, how = alias_index[name_norm]
            assign(code, how, 1.0, f"Deductor name matches {'the customer legal name' if how == 'EXACT_NAME' else 'a saved alias'} for {code}")

        if rec["status"] != EXACT:
            scored = []
            candidate_codes = set()
            for token in set(name_norm.split()):
                candidate_codes.update(token_index.get(token, ()))
            # A common word can still produce a large bucket.  Its deterministic
            # cap keeps fuzzy matching bounded and leaves uncertain identities
            # in review instead of auto-confirming them.
            limit = cfg.get("fuzzy_candidate_limit", 250)
            if len(candidate_codes) > limit:
                candidate_codes = set(sorted(candidate_codes)[:limit])
            for code in candidate_codes:
                c = by_code[code]
                s1 = fuzz.token_set_ratio(name_norm, c["customer_name_norm"])
                s2 = max((fuzz.token_set_ratio(name_norm, a) for a in fuzzy_names[code][1:]), default=0)
                pan_bonus = 0
                scored.append({"customer_code": code, "customer_name": c["customer_name"], "customer_pan": c["pan"], "customer_gstin": c["gstin"], "score": round(max(s1, s2) / 100 + pan_bonus, 4)})
            scored.sort(key=lambda x: -x["score"])
            rejected = set(decision.get("rejected_codes", [])) if decision else set()
            scored = [c for c in scored if c["customer_code"] not in rejected]
            rec["candidates"] = scored[:5]
            if scored:
                best = scored[0]
                gap = round(best["score"] - (scored[1]["score"] if len(scored) > 1 else 0), 4)
                rec.update({"score": best["score"], "confidence": best["score"], "confidence_gap": gap})
                high, review = cfg["fuzzy_high_threshold"] / 100, cfg["fuzzy_review_threshold"] / 100
                if decision and decision.get("action") == "KEEP_UNMAPPED":
                    rec["status"] = UNMAPPED
                    rec["signals"].append(f"Kept unmapped by {decision.get('reviewer', 'reviewer')} on {str(decision.get('timestamp', ''))[:10]}")
                elif best["score"] >= high and gap >= cfg["fuzzy_min_gap"] / 100:
                    rec.update({"status": HIGH_CONFIDENCE, "method": "FUZZY", "customer_code": best["customer_code"], "customer_name": best["customer_name"], "customer_pan": best["customer_pan"], "customer_gstin": best["customer_gstin"]})
                    rec["signals"].append(f"Name similarity {best['score']:.0%} with '{best['customer_name']}' (gap {gap:.0%} to next candidate)")
                elif best["score"] >= review:
                    rec.update({"status": REVIEW_REQUIRED, "method": "FUZZY", "customer_code": None, "customer_name": None, "suggested_customer_code": best["customer_code"], "suggested_customer_name": best["customer_name"], "customer_pan": best["customer_pan"], "customer_gstin": best["customer_gstin"]})
                    rec["signals"].append(f"Closest customer '{best['customer_name']}' at {best['score']:.0%} similarity — below the auto-map threshold of {high:.0%}" if best["score"] < high else f"Similarity {best['score']:.0%} but confidence gap {gap:.0%} is too small to auto-map")
                else:
                    rec["signals"].append(f"No customer resembles '{name}' (best {best['score']:.0%})")
            if rec["status"] in (REVIEW_REQUIRED, UNMAPPED):
                rec["signals"].append("TAN not found in Customer Master or saved mappings")
        rec.setdefault("suggested_customer_code", rec["customer_code"])
        rec.setdefault("suggested_customer_name", rec["customer_name"])
        identities[tan] = rec

    for s in statements:
        ident = identities[s["tan"]]
        s["identity"] = {k: ident[k] for k in ("customer_code", "customer_name", "status", "method", "score", "suggested_customer_code", "suggested_customer_name")}
    return identities, by_code
