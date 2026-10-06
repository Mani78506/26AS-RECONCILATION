# CA review ? TDS Interest policy schema for FY 2026-27

**Status:** `CA_REVIEW_ONLY_NOT_FOR_PROVISIONING`. This is a source-to-policy design. It creates no record, authorisation, activation, or database write.

## Source-grounded facts

| Fact | Value | Evidence | Classification |
| --- | --- | --- | --- |
| Governing Act / period | Income-tax Act, 2025 for events on or after 2026-04-01; this review's FY is 2026-27. | [Tax Payments FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/tax-payments-faq?mobile-app=1), FAQ 4; project PDF p.14. | STATUTORY_FACT |
| Deduction/collection delay rate and period | 1% for every month or part, from the date tax was deductible/collectible to actual deduction/collection. | [Income-tax Act, 2025 s.398](https://incometaxindia.gov.in/Documents/Act/Income-tax-Act-2025.pdf), s.398(3)(a)(i); project PDF p.14, Para 234.1. | STATUTORY_FACT |
| Deposit delay rate and period | 1.5% for every month or part, from actual deduction/collection to actual payment. | [Income-tax Act, 2025 s.398](https://incometaxindia.gov.in/Documents/Act/Income-tax-Act-2025.pdf), s.398(3)(a)(ii); project PDF p.14, Para 234.1. | STATUTORY_FACT |
| Relief from assessee-in-default | Applies only to the section 398(2) conditions; interest under clause (a)(i) remains payable to the relevant return-furnishing date. | Act s.398(2)?(3)(c); project PDF p.14, Para 234.3. | STATUTORY_FACT |
| TDS relief certificate | Rule 221(1)(a) prescribes Form 149 for non-/short-deduction; the form records section 398(3)(a) interest and its payment status. | [Income-tax Rules, 2026](https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-03/En-Notified-IT-Rules-2026-20-03-2026.pdf), rule 221(1)(a), pp.164 and 832?835. | STATUTORY_FACT |
| Deposit deadline | The applicable deadline comes from a separately governed Rule 218 branch. | Rules 2026, rule 218; existing due-date decision matrix. | STATUTORY_FACT; SEPARATE_POLICY |

The project reference `Scan_20261006_112447.pdf` has SHA-256 `BEA3EE599418D272514516D9B36FB9621170EA365124C35846766C15ED437D8B`. Only p.14 Para 234.1?234.3 and the TDS/TCS-specific notes on p.15 were used.

## Required records

Two records are mandatory and independently selected:

1. `DEDUCTION_DELAY_INTEREST` ? rate **1**, legal start `DATE_TAX_DEDUCTIBLE_OR_COLLECTIBLE`, legal end `DATE_OF_ACTUAL_DEDUCTION_OR_COLLECTION`.
2. `DEPOSIT_DELAY_INTEREST` ? rate **1.5**, legal start `DATE_OF_ACTUAL_DEDUCTION_OR_COLLECTION`, legal end `DATE_OF_ACTUAL_PAYMENT`.

They cannot substitute for one another. The deposit-deadline configuration is also separate: it decides whether a payment default exists, but does not change the section 398(3)(a)(ii) start date.

## Field decision matrix

| Field | Required by current policy model | Status for CA review |
| --- | --- | --- |
| `financial_year`, `effective_from`, `effective_to`, `governing_act` | Yes | FY `2026-27`, `2026-04-01` through `2027-03-31`, `INCOME_TAX_ACT_2025` are source-supported. |
| `interest_type`, `rate`, `rate_unit` | Yes | Source-supported: separate types, `1` / `1.5`, `PERCENT_PER_PERIOD`. |
| `interest_period_start`, `interest_period_end` | Review-schema evidence fields | Source-supported semantics listed above; current engine derives them from type and must retain that mapping. |
| `payment_nature`, `deductee_type`, organisation/client/assignment scope, `priority` | Yes | **GOVERNANCE_REQUIRED.** The statute does not turn the generic section 398 rates into a single contractor-only scope. |
| `interest_base` | Yes | **GOVERNANCE_REQUIRED.** The source says ?amount of such tax?; the CA must map this safely to one existing engine basis per delay type and scope. |
| `period_counting_method`, `period_day_block` | Yes | **GOVERNANCE_REQUIRED for FY 2026-27.** Section 398 says ?month or part? but accessible authoritative 2025 material inspected here does not define a calculation unit. PDF p.15 reports a 30-day judicial interpretation for old section 201(1A), not a verified rule for section 398. |
| `rounding_method`, `rounding_precision` | Yes | **GOVERNANCE_REQUIRED.** Neither Para 234 nor the inspected 2025 Act/Rules source provides interest-rounding instruction. |
| `deposit_due_date_policy_reference` | Proposed review field | Required to identify the separately governed Rule 218 decision. It must resolve to exactly one frozen approved due-date-policy snapshot. |
| `deposit_due_date_mode` / parameters | Required today for a deposit-interest record | **RESOLVED ? PHASE 4 OWNER.** Phase 5 activation no longer accepts or requires duplicate deadline configuration. Its calculation requires the persisted, validated Phase 4 due-date snapshot and branch inputs. |
| source locator, provision, dates, retrieval/verification evidence | Required for governed activation | Project PDF and official-source references are documented; the final policy must attach the exact source bundle and review evidence. |
| lifecycle, `approved_at`, `approved_by`, approval authority/reference | Required for manual policy activation | **GOVERNANCE_REQUIRED.** These are application lifecycle facts, never statutory facts and are intentionally blank in the example. |

## Section 398(2) / Form 149 evidence branch

This is a **transaction-level evidence branch**, not a third interest-rate policy and not an automatic waiver. A future implementation needs at least:

| Proposed evidence field | Required evidence |
| --- | --- |
| `relief_398_2_status` | Controlled state: `NOT_CLAIMED`, `EVIDENCE_INCOMPLETE`, `SUBMITTED`, `VERIFIED`, or `REJECTED`. No supplied evidence may imply `VERIFIED`. |
| `payee_return_furnished_date` | Return evidence under section 263. This is the end date of the 1% interest period when relief is verified. |
| `income_included_evidence_reference` | Evidence that the payment was included in the payee's return computation. |
| `payee_tax_paid_evidence_reference` | Evidence that tax due on the declared income was paid. |
| `form_149_certificate_reference` | Accountant certificate reference and attached/evidence hash. |
| `form_149_submission_reference` | Electronic furnishing reference under rule 221(1)(a). |
| `relief_decision_snapshot` | Reviewer, date, source references and status frozen with the result. |

Until these inputs and a reviewed evidence workflow exist, the application must not claim section 398(2) relief or alter a calculated interest result. `FORM_150` is the separate TCS form; it is outside this **TDS-only** policy design.

## FY 2025-26 separation

The corresponding 1961 Act section 201(1A) policy must retain its own Act, effective period, source snapshot and any period-method decision. It must not share an FY 2026-27/Act-2025 snapshot. The PDF's p.15 30-day case-law note may inform a CA's legacy policy decision, but it is not a global FY 2026-27 default.
