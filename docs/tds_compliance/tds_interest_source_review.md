# TDS/TCS interest source review — FY 2025-26 and FY 2026-27

**Scope.** This review is limited to TDS/TCS interest under Income-tax Act,
2025 section 398(3), its stated 1961 Act counterparts, and governed deposit
due-date selection. It does not configure, approve, activate, or provision a
policy.

## Source record

| Item | Evidence |
| --- | --- |
| Project reference | `Scan_20261006_112447.pdf`, SHA-256 `BEA3EE599418D272514516D9B36FB9621170EA365124C35846766C15ED437D8B`; inspected 2026-10-06. |
| Relevant PDF text | Physical page 14, **Para 234.1–234.3**. Physical page 15 supplies a 1961-Act case-law/circular note; physical page 16 is unrelated section 439 demand interest. |
| Official transition / rate support | [Income Tax Department, Tax Payments FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/tax-payments-faq?mobile-app=1), FAQs 4, 7 and 13, retrieved 2026-10-06. It states the 2026-04-01 Act transition, Rule 218 timing continuity, and section 398(3) 1%/1.5% statements. |
| Official FY 2026-27 due-date source | [Income-tax Rules, 2026](https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-03/En-Notified-IT-Rules-2026-20-03-2026.pdf), rule 218, Gazette p.160–162, retrieved 2026-10-06. |
| Official FY 2025-26 due-date source | [Income-tax Rules, 1962, rule 30](https://www.incometaxindia.gov.in/documents/20117/11892059/Rule%2B-%2B30_en.pdf/d159c547-6aaa-99bf-ea3b-213295715a56?t=1766001159027&version=1.0), retrieved 2026-10-06. |

## Interest facts extracted separately

| Policy type | Statutory fact supported | Traceability | Product treatment |
| --- | --- | --- | --- |
| `DEDUCTION_DELAY_INTEREST` | **1% per month or part** from the date tax was deductible/collectible to the date it was actually deducted/collected. | PDF p.14, Para 234.1 Default one; section 398(3)(a)(i) is also summarized in Tax Payments FAQ 13. | A separate policy record is required. Its base, scope, period method, rounding and approval remain governed configuration. |
| `DEPOSIT_DELAY_INTEREST` | **1.5% per month or part** from the date tax was actually deducted/collected to the date it was actually paid. | PDF p.14, Para 234.1 Default two; section 398(3)(a)(ii) is also summarized in Tax Payments FAQ 13. | A separate policy record is required. A due date establishes whether the payment default occurred; it does not replace the statutory interest start date. |
| Section 398(2) relief | A payer/collector is not deemed in default only where the recipient has furnished the specified return, included the amount, paid tax due, and the payer/collector submits the prescribed CA certificate (Form 149). The PDF says 1% remains payable to the recipient-return date. | PDF p.14, Para 234.3. | **PRODUCT INPUT / GOVERNANCE REQUIRED.** The current interest workflow has no Form 149 and recipient-return evidence contract. It must not infer or grant relief. |
| 1961 Act waiver note | The PDF reports a 1961 Act section 201(1A)(ii)/206C(7) waiver/reduction route under Circular 5/2025 for payment initiated/debited by due date or qualifying technical failures, subject to the authorised authority's order. | PDF p.15, “Reduction or waiver of interest”. | **OUT OF CURRENT CALCULATION SCOPE.** No waiver is inferred from a payment date or used to reduce calculated interest. |

## Month-or-part calculation and rounding

The primary TDS table on p.14 says only “per month or part”; it does not
specify rounding. Consequently, `rounding_method` and `rounding_precision`
remain **GOVERNANCE_REQUIRED** and must be supplied by a governed policy.

Page 15 reports a section 201(1A) case-law interpretation that “month” has an
ordinary **30-day** meaning, not a British calendar month, and gives a 12 July
to 10 August 2026 example as one month. This is evidence for the legacy
1961-Act interpretation only; it does **not** establish that the Income-tax
Act, 2025 section 398 period must use the same convention. The engine therefore
requires an explicit policy method (`CALENDAR_MONTH_OR_PART` or a configured
fixed-day block) and supplies no global default. A FY 2026-27 method remains
**GOVERNANCE_REQUIRED** pending an applicable authoritative interpretation.

## Due-date branches remain separate

The verified Rule 218 branches are configuration inputs, not interest-period
substitutes:

| Act / rule | Branch supported by the rule text | Status in this product |
| --- | --- | --- |
| 2025 Act / Rule 218(1)(a) | Government office, no challan: same day. | No policy configured. |
| 2025 Act / Rule 218(1)(b) | Government office, with challan: seven days from end of month. | No policy configured. |
| 2025 Act / Rule 218(2)(a) | Other deductor, March: 30 April. | No policy configured. |
| 2025 Act / Rule 218(2)(b) | Other deductor, other cases: seven days from end of deduction/collection month. | No policy configured. |
| 2025 Act / Rule 218(3)(c)(i) | Table 6(ii) qualifying contract route: 30 days from end of deduction month and Form 141. | Outside configured catalog scope; never substitute for Table 6(i). |
| 1961 Act / Rule 30 | Corresponding Government/no-challan, Government/challan, March, and other-deductor branches. | Retained separately; no policy configured. |

Rule 218(4)'s permission-based quarterly route is not represented by a generic
policy and remains a CA/product decision.

## Current architecture and fail-closed state

`calculate_interest_from_deposit_results()` selects one governed active policy
for each interest type, freezes both selected policy snapshots, and rejects a
missing, ambiguous, inactive, or governance-incomplete policy as
`POLICY_NOT_CONFIGURED` / review-required. The deposit due date is taken from
a frozen governed due-date snapshot where present; it controls timeliness only.

No policy was created, approved, activated, seeded, or written to MongoDB for
this review. The E2E policy template remains incomplete. The runtime status is
therefore **fail closed** until legitimate policy scope, base, period method,
rounding, due-date branch, source evidence and approval metadata are supplied
through the existing lifecycle.
