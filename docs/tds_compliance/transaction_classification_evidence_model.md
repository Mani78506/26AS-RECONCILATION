# Controlled Transaction Classification / Evidence model

## Scope

This model now has a source-anchored API, persistence and frozen-result implementation for the 68 FY 2026-27 source rows classified as
`MISSING_PRODUCT_INPUT` in `statutory_source_reconciliation.csv`. It reuses the
existing classification API and does not create an executable rule, add a
frontend control, change a rate/threshold, or select a statutory outcome.

A source-row condition is not a transaction fact. A transaction must provide
the required controlled fact and its evidence; otherwise the existing workflow
must remain `REVIEW_REQUIRED`. The source manifest remains
`TRANSCRIBED_UNVERIFIED`; any eventual taxonomy value and executable rule need
separate source verification and CA approval.

## Existing fields to retain

| Existing field / mechanism | Reuse decision | Limit |
| --- | --- | --- |
| `payment_nature` | Reuse as the primary transaction route. | It does not distinguish source conditions within one nature. |
| `recipient_residency` | Reuse for resident, non-resident and foreign-company routing. | It cannot establish legal subtype or operational status. |
| `recipient_category` | Reuse where the approved category exactly matches. | It does not establish relationship, legal role or evidence. |
| `payer_category` | Reuse where the approved payer category exactly matches. | It does not establish specialised payer status/evidence. |
| Ledger dates, amounts and aggregation | Reuse for event time and approved threshold evaluation. | They must not infer agreement timing or subject/instrument status. |
| `section_input`, deductee type, certificate references | Retain for the existing governed calculation contract. | No free-form section/rule selection. |
| Opt-in contractor evidence fields | Keep isolated to `CONTRACTOR_WITHHOLDING_V1`. | They are not generic evidence for these 68 rows. |

The existing inputs are exposed through `TdsTransactionClassificationBody` in
`backend/server.py`, persisted against the committed ledger version through the
classification mapping, and typed in `frontend/src/types.ts`. The rule engine
already matches only active, lifecycle-approved rules in
`select_statutory_rule()`; this design must feed that fail-closed path, never
bypass it.

## Minimum new data shape

Use one transaction-level, versioned collection rather than section-specific
columns. It is deliberately optional until a future approved rule explicitly
requires a fact.

```text
classification_facts: [
  {
    fact_type: controlled enum,
    value_code: controlled value within the approved fact-type taxonomy,
    evidence_status: controlled enum,
    evidence_reference: string,
    source_condition_reference: source-row/configuration reference,
    review_reason: optional string
  }
]
```

No field has a default that establishes eligibility. A fact with absent,
conflicting, unverified, or unsupported evidence is unusable for rule matching
and leaves the transaction `REVIEW_REQUIRED`.

| Proposed field | Purpose | Controlled values | Level | Evidence required |
| --- | --- | --- | --- | --- |
| `classification_facts` | Holds repeatable evidence-backed facts without duplicating base classification fields. | Array; no untyped/free-form fact is accepted. | Transaction; copied into the immutable calculation snapshot when used. | Yes, for every fact used by a rule. |
| `classification_facts[].fact_type` | Identifies the reusable concept being asserted. | `RECIPIENT_LEGAL_SUBTYPE`, `PAYER_LEGAL_SUBTYPE`, `COUNTERPARTY_RELATIONSHIP`, `PAYMENT_SUBJECT_CLASS`, `INCOME_TRANSACTION_CHARACTER`, `AGREEMENT_CLASSIFICATION`. `SOURCE_DEPENDENCY` is non-selectable and blocks execution. | Transaction. | Yes. |
| `classification_facts[].value_code` | Carries the approved taxonomy member for the fact type. | A versioned, source-traceable taxonomy owned by the rule/configuration lifecycle; no values are introduced by this design. | Transaction. | Yes; must support this exact code. |
| `classification_facts[].evidence_status` | Records whether evidence may be relied on. | `NOT_PROVIDED`, `PROVIDED_UNVERIFIED`, `VERIFIED`, `CONFLICTING`, `REVIEW_REQUIRED`. Only `VERIFIED` may satisfy a future rule predicate. | Transaction. | The status is mandatory whenever a fact is present. |
| `classification_facts[].evidence_reference` | Points to the document, attestation or evidence record. | Non-empty identifier/reference; not a legal conclusion. | Transaction. | Mandatory for `VERIFIED`; missing reference fails closed. |
| `classification_facts[].source_condition_reference` | Ties the fact to the approved source-condition/taxonomy definition and supports frozen audit evidence. | Approved source-row/configuration identifier only; free text cannot select a statutory condition. | Transaction and frozen snapshot. | Mandatory when the fact is used for matching. |
| `classification_facts[].review_reason` | Preserves why a submitted fact cannot be used. | Free text explanation only; never rule-selecting. | Transaction. | Required for `CONFLICTING` and `REVIEW_REQUIRED`. |

### Fact-type responsibilities

| Fact type | Reusable purpose | Existing field that remains prerequisite | Rows |
| --- | --- | --- | --- |
| `RECIPIENT_LEGAL_SUBTYPE` | Legal capacity, category, age/status or role not proven by basic recipient category. | `recipient_residency`, `recipient_category` | 25 primary rows. |
| `PAYER_LEGAL_SUBTYPE` | Payer class or operating role beyond basic payer category. | `payer_category` | 10 primary rows; it can coexist with another fact. |
| `COUNTERPARTY_RELATIONSHIP` | Firm/partner, SPV/unitholder, participant, shareholder, transferor and similar relationship facts. | Recipient/payer categories | 12 primary rows; it can coexist with subject class. |
| `PAYMENT_SUBJECT_CLASS` | Asset, instrument, unit, scheme, land, fund, bond/GDR, IFSC or analogous payment subject. | `payment_nature`, dates and amount | 24 primary rows. |
| `INCOME_TRANSACTION_CHARACTER` | Taxable/exempt status, gain/period characteristic, activity, channel or other source-stated transaction character. | `payment_nature` | 7 primary activity/channel rows plus some subject rows requiring multiple facts. |
| `AGREEMENT_CLASSIFICATION` | Agreement date/category and counterparty class. | Transaction dates are contextual only. | NR-39 through NR-44 (6 rows). |
| `SOURCE_DEPENDENCY` | Signals an unavailable source dependency rather than a user-selectable input. | None. | NR-38 only; permanently review-required until Source Note 6 is inspected. |

The counts describe primary groupings from the gap analysis and are not a claim
that one fact alone makes a row selectable.

## Fail-closed acceptance rules for a future implementation

1. A rule can require one or more facts by `fact_type`, `value_code`, source
   condition and `evidence_status=VERIFIED`.
2. Zero matching facts, multiple contradictory verified facts, a missing
   evidence reference, or a non-approved taxonomy version produces
   `REVIEW_REQUIRED`.
3. A fact must be scoped to the transaction's committed ledger version and be
   frozen with any calculation result that relied on it.
4. Classification facts never create or activate a statutory rule, due-date
   policy, Phase 5 policy, or CA approval.
5. The current contractor fields retain their own validation and cannot be
   converted implicitly into a generic fact.

## Source-dependent exclusions

- **NR-38** remains `SOURCE_DEPENDENCY` / `REVIEW_REQUIRED`: `Source Note 6`
has not been inspected.
- **NR-39 to NR-44** require executed-agreement evidence. Invoice, credit and
payment dates are not substitutes for agreement execution date/category.
- Rows referring to Schedule V or named provisions remain blocked until the
referenced primary material is inspected and CA-approved. This includes
RES-13, RES-14, NR-18, NR-19, NR-25, and NR-32 through NR-34.

## Row-to-fact mapping

The following complete map preserves the recorded source condition. The
proposed field is the same controlled `classification_facts[]` collection; the
listed fact type says which reusable concept would be required. It does **not**
authorise the value or activate a rule.

| Source row | Required fact from recorded source condition | Proposed field | Evidence requirement |
| --- | --- | --- | --- |
| CA-FY2026-27-RES-02 | Taxable accumulated balance | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-03 | Resident other than company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-04 | Domestic company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-06 | Qualifying individual/HUF payer | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-09 | Resident transferor; not rural agricultural land | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-10 | Resident individual/HUF | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-13 | Schedule V Table 3/4 to resident unitholders | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-14 | Section 224 units | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-15 | Individual/HUF or other person | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-17 | Bank/co-op bank/post-office; senior citizen | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-18 | Bank/co-op bank/post-office; other than senior citizen | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-19 | Any other payer | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-21 | Other contractor | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-22 | Resident contractor/professional/commission/brokerage | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-23 | Call-centre-only business | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-RES-24 | Other professional service | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-RES-25 | Not professional service | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-RES-26 | Non-employee director | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-27 | Cinematographic films | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-28 | Other royalty | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-RES-30 | Source note: ₹10000 if shareholder individual | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-31 | Exemption unavailable | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-RES-35 | Individual/HUF participant | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-36 | Other participant | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-37 | Specified person | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-38 | Other person | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-39 | Other than online games | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-RES-43 | Co-operative society | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-44 | Other person | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-RES-46 | Firm to partner | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-02 | Taxable accumulated balance | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-03 | Other than online games | `classification_facts[]`: `INCOME_TRANSACTION_CHARACTER` | Evidence of the stated activity, service, channel or transaction character. |
| CA-FY2026-27-NR-07 | Co-operative society | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-08 | Other person | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-10 | Firm to partner | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-11 | Foreign sportsman/entertainer/sports association | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-12 | Foreign-currency approved loan/long-term bonds outside India | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-13 | Long-term/rupee bond listed IFSC exchange | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-14 | Qualifying post-1-Jul-2023 IFSC bond | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-15 | Infrastructure debt fund | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-16 | SPV interest to unitholder | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-17 | SPV dividend to unitholder | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-18 | Schedule V Table 4; non-resident other than foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-19 | Schedule V Table 4; foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-20 | Non-resident other than foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-21 | Foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-22 | Non-resident other than foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-23 | Foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-25 | Long-term capital gain on section 208 units | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-26 | Foreign-currency bonds/GDR interest | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-27 | Long-term capital gain bonds/GDR | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-29 | Specified fund; surcharge not applicable | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-30 | Qualifying Indian citizen | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-31 | Foreign-exchange asset long-term capital gain; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-32 | Section 196 short-term gain; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-33 | Section 197 long-term gain; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-34 | Section 198 long-term gain; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-35 | IFSC unit dividend; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-36 | Other dividend; SC cap 15% | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-37 | Government/Indian-concern FC borrowing interest | `classification_facts[]`: `PAYMENT_SUBJECT_CLASS / INCOME_TRANSACTION_CHARACTER` | Primary transaction/instrument/scheme evidence. Named schedule/provision references remain blocked pending source verification and CA approval. |
| CA-FY2026-27-NR-38 | Source Note 6 | `classification_facts[]`: `SOURCE_DEPENDENCY` | Source Note 6 must be obtained and inspected; no transaction evidence can substitute for it. |
| CA-FY2026-27-NR-39 | Agreement 1961-1976; non-resident other than foreign company | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-40 | Agreement 1961-1976; foreign company | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-41 | Agreement on/after 1-Apr-1976 | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-42 | Agreement 1964-1976; non-foreign company | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-43 | Agreement 1964-1976; foreign company | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-44 | Agreement on/after 1-Apr-1976 | `classification_facts[]`: `AGREEMENT_CLASSIFICATION` | Executed agreement reference and execution-date/category evidence; payment/invoice date is insufficient. |
| CA-FY2026-27-NR-45 | Non-resident other than foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |
| CA-FY2026-27-NR-46 | Foreign company | `classification_facts[]`: `RECIPIENT_LEGAL_SUBTYPE / PAYER_LEGAL_SUBTYPE / COUNTERPARTY_RELATIONSHIP` | Evidence of the stated party capacity, payer status or relationship; exact type is selected only after source/CA taxonomy approval. |

## Implementation boundary

The backend now accepts `classification_facts[]` through the existing transaction
classification API, persists it by assignment/ledger-version/transaction, passes
it into calculation, and freezes it in the calculation result. Fact type and
evidence status are controlled; `VERIFIED` requires both a value code and an
evidence reference, while unresolved statuses require a review reason. A future
rule can opt into `required_classification_facts`; absent, unknown, unverified,
or contradictory evidence then produces `CLASSIFICATION_FACT_REQUIRED` and a
fail-closed review outcome.

No catalog rule consumes a new fact in this phase. The product intentionally has
no UI value taxonomy yet because the source rows do not establish one without
further CA/product interpretation. No source row becomes executable merely
because this evidence contract exists.
