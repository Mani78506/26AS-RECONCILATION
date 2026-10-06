# FY 2026-27 statutory product-input gap analysis

## Purpose and boundary

This is a design inventory for the **68** rows currently labelled
`MISSING_PRODUCT_INPUT` in
[`statutory_source_reconciliation.csv`](statutory_source_reconciliation.csv).
It does not add an executable statutory rule, alter a rate or threshold, seed a
database, approve a policy, or change calculation behaviour.

The row conditions below are transcriptions from the project CA source manifest.
They describe what the source table says; they are **not** a conclusion that the
application has a verified statutory interpretation. All non-executable manifest rows remain
`TRANSCRIBED_UNVERIFIED`, and a future rule also needs its own source
traceability, lifecycle approval, and matching tests.

## Existing reusable contract

The current product can already retain these source-backed transaction facts:

| Reusable input | Current location | Boundary |
| --- | --- | --- |
| Transaction, credit, payment and invoice dates; financial year; amount and taxable amount | `backend/engine/tds_compliance/core.py` `PAYMENT_LEDGER_SCHEMA` | Existing ledger facts. They are not proof of a special statutory condition. |
| Payment nature | `PAYMENT_NATURES`, ledger field `payment_nature`, Classification Review | The backend taxonomy is broader than the current browser dropdown. A classified nature alone does not select a rule. |
| Recipient residency | `recipient_residency`, `controlled_recipient_residency()` | Controlled values are `RESIDENT`, `NON_RESIDENT`, `FOREIGN_COMPANY`; PAN syntax never establishes it. |
| Recipient category | `recipient_category`, `RECIPIENT_CATEGORIES` | Several source categories are already named, but the field does not prove a party's legal capacity or role. |
| Payer category | `payer_category`, `PAYER_CATEGORIES` | Several generic payer categories exist. It does not retain the evidence needed for specialised payer conditions. |
| Deductee type, section/table input and certificate references | Existing ledger/classification fields | Current UI intentionally exposes only configured contractor references; this is not a general rule-picker. |
| Aggregate transaction amount | Existing Phase 2 calculation aggregation | It can support an approved aggregate test, but does not identify the group or statutory aggregation basis. |
| Contractor evidence | `TdsTransactionClassificationBody` and `CONTRACTOR_WITHHOLDING_V1` fields | Narrow, opt-in contractor controls. They must not be reused as an implicit proof for unrelated payment types. |

`select_statutory_rule()` still requires an active lifecycle-approved rule with a
matching Act, FY, effective date, payment nature, deductee type, residency,
recipient category, payer category and section/table scope. Missing or competing
facts remain fail-closed; this analysis does not change that behaviour.

## Primary missing-input grouping

Each row is assigned one **primary** product-input family below so the counts sum
to 68. Some rows will ultimately need more than one fact; the assignment is only
for prioritisation and does not collapse their source conditions.

| Primary family | Rows | Count | Reusable basis | Product gap |
| --- | --- | ---: | --- | --- |
| Party capacity, payer/recipient role or subtype | RES-03, 04, 06, 10, 15, 17, 18, 19, 21, 22, 26, 30, 35, 36, 37, 38, 43, 44, 46; NR-07, 08, 10, 11, 18, 19, 20, 21, 22, 23, 30, 45, 46 | 32 | Residency, recipient category and payer category | Evidence-backed party subtype/role and, where stated, payer subtype. |
| Asset, instrument, scheme, unit or income-status fact | RES-02, 09, 13, 14, 27, 31; NR-02, 12, 13, 14, 15, 16, 17, 25, 26, 27, 29, 31, 32, 33, 34, 35, 36, 37 | 24 | Payment nature, dates, amount | A controlled subject-matter/instrument fact and source evidence. Several rows also cite a schedule or section that needs separate verification. |
| Service/activity or transaction-channel fact | RES-23, 24, 25, 28; NR-03, 38 | 6 | Payment nature and existing transaction data | A controlled service/activity/channel subtype, or an unresolved source dependency for `Source Note 6`. |
| Agreement or historic-provision timing | NR-39, 40, 41, 42, 43, 44 | 6 | Transaction date and residency | Agreement date/category plus durable documentary evidence. |

Row abbreviations expand to `CA-FY2026-27-RES-*` and
`CA-FY2026-27-NR-*`. The row-level source-condition map below is authoritative
for this analysis.

## Row-level source-condition map

| Primary family | Source rows and recorded condition | Required product input, without legal inference | Evidence / approval boundary |
| --- | --- | --- | --- |
| Party capacity / role | RES-03 `Resident other than company`; RES-04 `Domestic company`; RES-06 `Qualifying individual/HUF payer`; RES-10 `Resident individual/HUF`; RES-15 `Individual/HUF or other person`; RES-17/18 `Bank/co-op bank/post-office; senior citizen/other than senior citizen`; RES-19 `Any other payer`; RES-21 `Other contractor`; RES-22 `Resident contractor/professional/commission/brokerage`; RES-26 `Non-employee director`; RES-30 `shareholder individual`; RES-35/36 `Individual/HUF participant` / `Other participant`; RES-37/38 `Specified person` / `Other person`; RES-43/44 `Co-operative society` / `Other person`; RES-46 `Firm to partner` | Recipient legal subtype/role; payer subtype/role; participant/counterparty role; where applicable age/status flag. | Party master evidence or transaction-attached attestation/reference, plus CA-approved mapping before execution. |
| Party capacity / role | NR-07/08 `Co-operative society` / `Other person`; NR-10 `Firm to partner`; NR-11 `Foreign sportsman/entertainer/sports association`; NR-18/19, 20/21, 22/23 and 45/46 distinguish `non-resident other than foreign company` from `foreign company`; NR-30 `Qualifying Indian citizen` | Recipient legal subtype/role and, where stated, payer relationship. Existing residency is a prerequisite but insufficient. | Identity/constitutional/relationship evidence; CA must approve accepted evidence and category mapping. |
| Asset / scheme / income-status | RES-02 `Taxable accumulated balance`; RES-09 `Resident transferor; not rural agricultural land`; RES-13 `Schedule V Table 3/4 to resident unitholders`; RES-14 `Section 224 units`; RES-27 `Cinematographic films`; RES-31 `Exemption unavailable` | Controlled payment subject, asset/instrument/scheme unit, transferor/land status, or tax/exemption status. | Transaction document plus evidence reference. Schedule/section references need primary-source verification before a selectable value exists. |
| Asset / scheme / income-status | NR-02 `Taxable accumulated balance`; NR-12/13/14 `foreign-currency approved loan/long-term bonds`, `listed IFSC exchange`, `qualifying post-1-Jul-2023 IFSC bond`; NR-15 `Infrastructure debt fund`; NR-16/17 `SPV interest/dividend to unitholder`; NR-25 `long-term capital gain on section 208 units`; NR-26/27 `foreign-currency bonds/GDR`; NR-29 `Specified fund`; NR-31 `foreign-exchange asset long-term capital gain`; NR-32/33/34 `section 196/197/198` short/long-term gain; NR-35 `IFSC unit dividend`; NR-36 `Other dividend`; NR-37 `Government/Indian-concern FC borrowing interest` | Controlled instrument/asset/scheme class, income-character classification, SPV/unitholder relationship, payer funding context, and—where the source names a section—an approved source-reference selector rather than free text. | Primary transaction/instrument evidence and CA-approved classification/evidence rules. The table transcription does not itself establish each legal condition. |
| Service/activity/channel | RES-23 `Call-centre-only business`; RES-24 `Other professional service`; RES-25 `Not professional service`; RES-28 `Other royalty`; RES-39 `Other than online games`; NR-03 `Other than online games` | Controlled service/activity subtype, channel/game mode, and a relationship to the base payment nature. | Source document or operational record required; current free-text description cannot be used as proof. |
| Service/activity/channel | NR-38 `Source Note 6` | No safe input can be designed until the referenced note is obtained and inspected. | `VERIFICATION_REQUIRED`; source dependency precedes product design. |
| Agreement/historic provision | NR-39/40 `Agreement 1961-1976` split by non-foreign company/foreign company; NR-41 `Agreement on/after 1-Apr-1976`; NR-42/43 `Agreement 1964-1976` split by non-foreign company/foreign company; NR-44 `Agreement on/after 1-Apr-1976` | Agreement execution date/category and counterparty class, stored as an evidence-backed transaction fact. | Executed agreement/reference and CA-approved interpretation. Never derive it from payment date. |

## Reusable product inputs

| Candidate controlled input | Why it is needed / affected primary groups | Existing? | Level | Controlled values and evidence |
| --- | --- | --- | --- | --- |
| `recipient_legal_subtype` | Separates company, individual/HUF, partner, unitholder, participant, senior citizen, sports role, co-operative society and non-resident/foreign-company variants. | Partial: `recipient_category`; it lacks durable legal-role evidence. | Party master default with transaction override. | Enumerated values only after source/CA review; evidence reference and review status required. |
| `payer_legal_subtype` | Represents qualifying individual/HUF, bank/co-op bank/post office, firm, government/Indian concern and similar source-stated payer facts. | Partial: `payer_category`. | Assignment/client default with transaction override. | Controlled values and payer evidence reference; do not infer from organisation name. |
| `counterparty_relationship_role` | Represents firm-to-partner, SPV-to-unitholder, shareholder/participant and transferor relationships. | No general controlled field. | Transaction-level. | Controlled relationship values, identity links/evidence and an explicit review outcome. |
| `payment_subject_class` | Covers asset/instrument/unit/scheme/land/film/foreign-currency/GDR/IFSC/VDA and related source conditions. | No. | Transaction-level. | Controlled taxonomy linked to source row; documentary evidence reference mandatory. |
| `income_or_transaction_character` | Covers taxable accumulated balance, exemption status, capital-gain duration/type, service/activity subtype and online-game distinction. | Partial: `payment_nature`; generic `description` is not controlled evidence. | Transaction-level. | Separate controlled values only for source-verified conditions; evidence/attestation and review status required. |
| `agreement_reference_and_execution_date` | Needed for the six agreement-dated NR rows. | No general field; invoice/payment dates are not substitutes. | Transaction-level, optionally linked to contract master. | Immutable agreement identifier, execution date, counterparty evidence reference and review status. |
| `statutory_source_dependency_reference` | Rows that cite Schedule V, named sections or `Source Note 6` cannot be made executable until the dependency is inspected. | Partial: `section_input`; no source-dependency/evidence record. | Rule/configuration level, with transaction selection only after approval. | Controlled approved dependency identifier; no free-text statutory selection. |

## Reuse before new fields

1. Reuse `recipient_residency` for resident/non-resident/foreign-company routing;
   do not create another residency field.
2. Extend the existing controlled recipient and payer category contracts only when
   a verified source/CA decision specifies the exact values and evidence. Do not
   duplicate them with unvalidated booleans.
3. Reuse `payment_nature` as the first routing discriminator. A new field is
   required only where a single nature has distinct source conditions.
4. Reuse ledger date, amount and the existing aggregation mechanism for an
   approved threshold test. They do not replace asset, relationship, agreement
   or status evidence.
5. Keep contractor-specific evidence inside its existing opt-in control until an
   independently approved generic evidence model exists.

## Highest-value controlled-input sequence

The following order maximises reuse without deciding statutory scope:

1. **Evidence-backed recipient and payer legal subtype**: enables the largest
   primary group (32 rows) and complements existing residency/category fields.
2. **Evidence-backed payment subject class**: addresses the 24 instrument,
   asset, scheme and income-character rows while keeping the table conditions
   explicit.
3. **Relationship role and service/activity subtype**: covers the 7 activity
   rows and removes ambiguity where an existing payment nature is too broad.
4. **Agreement evidence**: isolates the 6 historic-agreement rows; it should
   not be modelled as a payment-date shortcut.
5. **Source dependency register**: before any configuration for rows that cite
   schedules, named provisions or `Source Note 6`.

This is a product-data sequence, not authorisation to configure any rule.

## Required separation at implementation time

| Layer | Must contain |
| --- | --- |
| Source information | Source row ID, PDF page/row, transcribed condition and any cited dependency. |
| Product input | Controlled, evidence-backed transaction/party/assignment fact. |
| Derived rule-selection condition | An approved rule's explicit match predicate; it must not be inferred from a description or source row alone. |
| Evidence requirement | Accepted document/attestation type, reference, verifier/review status and conflict handling. |
| Approval/policy requirement | Source-traceable rule lifecycle approval; separate due-date and interest policies where the existing workflow requires them. |

## Rows blocked before product-input work can be completed

- **NR-38** is blocked by the uninspected `Source Note 6`; it is
  `VERIFICATION_REQUIRED`, not a request for a new transaction field.
- Schedule/provision-dependent rows (RES-13, RES-14, NR-18, NR-19, NR-25 and
  NR-32 through NR-34) need the referenced primary material and CA-approved
  interpretation before their source conditions become selectable product values.
- The agreement rows NR-39 through NR-44 require a CA decision on accepted
  agreement evidence and the applicable interpretation; dates cannot be
  inferred from a payment or invoice date.
- Every row remains non-executable until its source traceability, controlled
  inputs, evidence contract, rule lifecycle approval and direct tests exist.

## Current conclusion

The 68 rows are not a single "missing field" problem. Existing residency,
party category, payment nature, amount and date fields provide the reusable
base. The smallest high-value additions are controlled, evidence-backed
party/payer subtype and payment-subject classification, followed by relationship
and agreement evidence. No source row becomes executable from this analysis.

## Complete 68-row coverage ledger
This ledger makes the primary grouping auditable. It repeats the exact source condition without adding a legal conclusion.
| Source row | Primary family | Source condition | Product-design disposition |
| --- | --- | --- | --- |
| CA-FY2026-27-RES-02 | Asset / scheme / income-status | Taxable accumulated balance | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-03 | Party capacity / role | Resident other than company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-04 | Party capacity / role | Domestic company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-06 | Party capacity / role | Qualifying individual/HUF payer | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-09 | Asset / scheme / income-status | Resident transferor; not rural agricultural land | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-10 | Party capacity / role | Resident individual/HUF | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-13 | Asset / scheme / income-status | Schedule V Table 3/4 to resident unitholders | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-14 | Asset / scheme / income-status | Section 224 units | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-15 | Party capacity / role | Individual/HUF or other person | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-17 | Party capacity / role | Bank/co-op bank/post-office; senior citizen | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-18 | Party capacity / role | Bank/co-op bank/post-office; other than senior citizen | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-19 | Party capacity / role | Any other payer | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-21 | Party capacity / role | Other contractor | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-22 | Party capacity / role | Resident contractor/professional/commission/brokerage | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-23 | Service/activity/channel | Call-centre-only business | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-RES-24 | Service/activity/channel | Other professional service | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-RES-25 | Service/activity/channel | Not professional service | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-RES-26 | Party capacity / role | Non-employee director | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-27 | Asset / scheme / income-status | Cinematographic films | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-28 | Service/activity/channel | Other royalty | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-RES-30 | Party capacity / role | Source note: ₹10000 if shareholder individual | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-31 | Asset / scheme / income-status | Exemption unavailable | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-RES-35 | Party capacity / role | Individual/HUF participant | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-36 | Party capacity / role | Other participant | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-37 | Party capacity / role | Specified person | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-38 | Party capacity / role | Other person | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-39 | Service/activity/channel | Other than online games | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-RES-43 | Party capacity / role | Co-operative society | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-44 | Party capacity / role | Other person | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-RES-46 | Party capacity / role | Firm to partner | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-02 | Asset / scheme / income-status | Taxable accumulated balance | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-03 | Service/activity/channel | Other than online games | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-NR-07 | Party capacity / role | Co-operative society | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-08 | Party capacity / role | Other person | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-10 | Party capacity / role | Firm to partner | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-11 | Party capacity / role | Foreign sportsman/entertainer/sports association | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-12 | Asset / scheme / income-status | Foreign-currency approved loan/long-term bonds outside India | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-13 | Asset / scheme / income-status | Long-term/rupee bond listed IFSC exchange | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-14 | Asset / scheme / income-status | Qualifying post-1-Jul-2023 IFSC bond | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-15 | Asset / scheme / income-status | Infrastructure debt fund | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-16 | Asset / scheme / income-status | SPV interest to unitholder | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-17 | Asset / scheme / income-status | SPV dividend to unitholder | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-18 | Party capacity / role | Schedule V Table 4; non-resident other than foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-19 | Party capacity / role | Schedule V Table 4; foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-20 | Party capacity / role | Non-resident other than foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-21 | Party capacity / role | Foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-22 | Party capacity / role | Non-resident other than foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-23 | Party capacity / role | Foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-25 | Asset / scheme / income-status | Long-term capital gain on section 208 units | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-26 | Asset / scheme / income-status | Foreign-currency bonds/GDR interest | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-27 | Asset / scheme / income-status | Long-term capital gain bonds/GDR | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-29 | Asset / scheme / income-status | Specified fund; surcharge not applicable | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-30 | Party capacity / role | Qualifying Indian citizen | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-31 | Asset / scheme / income-status | Foreign-exchange asset long-term capital gain; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-32 | Asset / scheme / income-status | Section 196 short-term gain; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-33 | Asset / scheme / income-status | Section 197 long-term gain; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-34 | Asset / scheme / income-status | Section 198 long-term gain; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-35 | Asset / scheme / income-status | IFSC unit dividend; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-36 | Asset / scheme / income-status | Other dividend; SC cap 15% | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-37 | Asset / scheme / income-status | Government/Indian-concern FC borrowing interest | Requires controlled subject/instrument or income-character evidence; cited schedules/sections remain source dependencies. |
| CA-FY2026-27-NR-38 | Service/activity/channel | Source Note 6 | Requires controlled activity/channel evidence; NR-38 remains blocked on Source Note 6. |
| CA-FY2026-27-NR-39 | Agreement/historic provision | Agreement 1961-1976; non-resident other than foreign company | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-40 | Agreement/historic provision | Agreement 1961-1976; foreign company | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-41 | Agreement/historic provision | Agreement on/after 1-Apr-1976 | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-42 | Agreement/historic provision | Agreement 1964-1976; non-foreign company | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-43 | Agreement/historic provision | Agreement 1964-1976; foreign company | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-44 | Agreement/historic provision | Agreement on/after 1-Apr-1976 | Requires agreement evidence/date and counterparty-class evidence; do not derive from payment date. |
| CA-FY2026-27-NR-45 | Party capacity / role | Non-resident other than foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
| CA-FY2026-27-NR-46 | Party capacity / role | Foreign company | Use reusable residency/category as a prerequisite; add evidence-backed legal subtype/role only after approval. |
