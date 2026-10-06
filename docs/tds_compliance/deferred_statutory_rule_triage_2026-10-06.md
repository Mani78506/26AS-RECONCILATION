# Deferred statutory-rule triage — 2026-10-06

This is the complete triage of the 78 rows not currently `ACTIVE_EXECUTABLE` in
`statutory_activation_coverage.csv`.  It deliberately does not alter Interest
(Phase 5), Return Audit, or legacy 26AS Reconciliation.

The source manifest marks the underlying scan `TRANSCRIBED_UNVERIFIED`.  A
controlled classification-fact field is not source verification: an ACTIVE
global rule requires complete primary-source traceability.  Consequently, no
row is Category A today, even where the product can already retain the needed
evidence.  Promoting any such row would defeat the fail-closed activation gate.

| Category | Count | Rows | Decision |
| --- | ---: | --- | --- |
| A. High confidence — controlled product/evidence addition only | 0 | — | None safely activatable: every candidate still lacks verified authoritative rule/source evidence. |
| B. Moderate — limited engine/product work | 0 | — | The current evidence model already stores the identified controlled facts; no row is blocked *only* by bounded implementation work. |
| C. Engine capability required | 6 | NR-04, NR-05, NR-06, NR-09, NR-24, NR-28 | Surcharge/cess calculation is not configured. |
| D. Authoritative source clarification required | 66 | RES-01–04, RES-06–31, RES-33, RES-43–44; NR-01–03, NR-07–08, NR-10–38, NR-45–46; RES-07–08, RES-11–12, RES-16, RES-29 | Obtain and inspect primary authority (including the referenced salary schedule/source note) before configuration. This includes rows whose fact contract exists but whose rule/source remains unverified. |
| E. Governance/CA decision required | 6 | NR-39–44 | Historic agreement date/category branches require CA interpretation and a governed rule decision. |

`RES-06` (rent) remains deferred.  Its monthly threshold and REIT
no-deduction exception are not fully represented by evidence and executable
threshold behavior, so it is expressly not eligible for activation.

The existing coverage matrix remains the source-of-truth per-row inventory;
the group totals above reconcile to 78 (0 + 0 + 6 + 66 + 6).
