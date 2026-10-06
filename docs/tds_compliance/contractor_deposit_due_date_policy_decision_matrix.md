# Contractor deposit due-date policy decision matrix

**Status:** PROPOSED / AWAITING CA-OWNER APPROVAL
**Retrieved:** 2026-10-01
**Scope:** governed contractor deposit deadlines only. This document neither creates nor approves a policy.

## Product controls already enforced

| Dimension | Enforced requirement | Code evidence |
| --- | --- | --- |
| Policy type | `policy_kind` must be `CONTRACTOR_DEPOSIT_DUE_DATE`; it cannot also be an interest policy. | `backend/server.py:TdsComplianceRuleBody.validate_scope` |
| Scope | Organisation is required; client/assignment scopes require their matching IDs. | `TdsComplianceRuleBody.validate_scope`, `_rule_document` |
| Legal scope | Approval requires FY, effective dates, Act, contractor payment nature, deductee type, section reference and deductor type. Government-office scope also requires a challan route. | `_contractor_due_date_policy_approval_errors` |
| Branch configuration | Exactly one configured mode is required: `DEDUCTION_DATE`, `MONTH_END_PLUS_DAYS`, or `FIXED_MONTH_DAY`; mode-specific parameters are validated. | `_contractor_due_date_policy_approval_errors`, `determine_configured_contractor_deposit_deadline` |
| Evidence | Approval requires source, source reference, HTTPS official URL, document title, provision reference, retrieval date and verification evidence. | `_contractor_due_date_policy_approval_errors`, `source_traceability_snapshot` |
| Lifecycle | The existing rule lifecycle remains `DRAFT → PENDING_APPROVAL → APPROVED → ACTIVE`; only an `ACTIVE` record with `approved_at`, `approved_by`, and `source_traceability_status=VERIFIED` is selectable. | `submit_tds_compliance_rule`, `approve_tds_compliance_rule`, `activate_tds_compliance_rule`, `determine_configured_contractor_deposit_deadline` |
| Selection | One record must match Act, FY, contractor nature, deductee type, section, deductor type, challan route and effective deduction date. Zero or multiple matches return review-required. | `determine_configured_contractor_deposit_deadline` |
| Conflict | Activation rejects an overlapping active record with the same governed scope and priority. | `_conflicting_active_due_date_policy` |
| Historical evidence | Phase 4 persists policy/version/source snapshot and branch inputs. Phase 5 consumes that frozen snapshot, not a live deadline rule. | `calculate_deposit_compliance`, `calculate_interest_from_deposit_results`, `run_persisted_interest_compliance` |

## CA approval readiness fields

| Field group | Current state | CA action required before activation |
| --- | --- | --- |
| Policy identity and lifecycle | **VERIFIED IN CODE.** `policy_kind=CONTRACTOR_DEPOSIT_DUE_DATE`, organisation scope, rule ID/version, DRAFT → PENDING_APPROVAL → APPROVED → ACTIVE, approval actor/time and conflict checks are required. | Assign the approving authority and retain the approval document/reference through the existing audited lifecycle. |
| Statutory rule scope | **VERIFIED FOR TABLE 6(i).** The rule snapshot carries `payer_category=DESIGNATED_PERSON`, `recipient_category=INDIVIDUAL_HUF_CONTRACTOR`, `form_141_applicability=NOT_APPLICABLE_TABLE_6_I`, Act, FY, payment nature, deductee type, section and effective interval. Runtime does not infer this from `payer_type`; it requires the existing reviewed `payer_eligibility_status` and evidence reference. | Confirm the reviewed evidence standard that permits a classifier to record `CONFIRMED_ELIGIBLE` for a designated person. |
| Deposit branch identity | **VERIFIED IN CODE; VALUES NOT APPROVED.** `deductor_type`; `challan_route` for a Government office; and exactly one deadline mode with its mode-specific parameters are mandatory. | Approve the applicable Rule 30/218 branch and provide every branch parameter, with its precise effective period. |
| Source traceability | **VERIFIED IN CODE; RECORD NOT CREATED.** Source, reference, HTTPS URL, document title, provision, retrieval date and verification evidence are mandatory at approval. | Attach the approved official source extract and CA interpretation to each individual policy. |
| Snapshot and Phase 5 reuse | **VERIFIED IN CODE.** Phase 4 stores the selected policy/version/source snapshot and branch inputs; Phase 5 reuses that frozen record. | Confirm that this snapshot is the required audit record for the initial product scope. |
| Table 6(ii)/Form 141 | **OUT OF CONFIGURED SCOPE.** It has no catalog rule or due-date policy. | Make a separate scope, statutory evidence, implementation and approval decision before any future configuration. |

## Source-backed branch matrix

The values below are legal findings, not application configuration. A row is not eligible for activation until the CA records scope, source evidence and approval through the lifecycle above.

| Proposed governed branch | Act/FY and scope | Primary source and proposition | Proposed configuration status | CA decision / evidence still required |
| --- | --- | --- | --- | --- |
| Government office, no challan | 1961 Act; FY 2025-26; contractor deduction governed by section 194C where its transaction trigger is pre-transition | [Income-tax Rules, 1962, rule 30](https://www.incometaxindia.gov.in/documents/20117/11892059/Rule%2B-%2B30_en.pdf/d159c547-6aaa-99bf-ea3b-213295715a56?t=1766001159027&version=1.0), rule 30(1)(a): office of Government pays on the same day without an income-tax challan. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Confirm that the customer is an office of Government and that the transaction is truly without challan; retain evidence and approved policy metadata. |
| Government office, with challan | 1961 Act; FY 2025-26; section 194C contractor payment | Rule 30(1)(b) in the same official source: payment with challan is on or before seven days from the end of the deduction month. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Confirm government-office classification, challan route, section 194C applicability, effective period and CA approval. |
| Other deductor, March | 1961 Act; FY 2025-26; ordinary section 194C contractor route | Rule 30(2)(a): deductors other than Government offices pay on or before 30 April where income/amount is credited or paid in March. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Confirm the rule is driven by the relevant deduction/credit/payment event for the configured branch; approve scope and evidence. |
| Other deductor, non-March | 1961 Act; FY 2025-26; ordinary section 194C contractor route | Rule 30(2)(b): in other cases payment is on or before seven days from month-end in which deduction is made. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Same as above. The product must retain the actual deduction date used by the approved branch. |
| Individual/HUF contractor special route | 1961 Act; FY 2025-26; section 194M, not the existing 194C catalog row | The official Tax Payments FAQ identifies old-Act section 194M as a challan-cum-statement exception with 30 days from the end of the deduction month. FAQ is supporting evidence only; this matrix has not inspected a current primary Rule 30(2C) text for this row. | **VERIFICATION_REQUIRED** | Obtain current operative Rule 30(2C) text, confirm section 194M/product scope, person class, form route and effective period before any policy. |
| Government office, no challan | 2025 Act; FY 2026-27; section 393 contractor branch after 2026-04-01 | [Income-tax Rules, 2026](https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-03/En-Notified-IT-Rules-2026-20-03-2026.pdf), rule 218(1)(a), Gazette p.160: same-day payment by a Government office without challan. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | CA must choose the exact applicable section 393 table item, deductee class and Government evidence contract. |
| Government office, with challan | 2025 Act; FY 2026-27; section 393 contractor branch after 2026-04-01 | Rules 2026, rule 218(1)(b), Gazette p.160: seven days from month-end where Government-office tax is paid with challan. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Same scope/approval decision as previous row. |
| Other deductor, March | 2025 Act; FY 2026-27; ordinary section 393 contractor branch | Rules 2026, rule 218(2)(a), Gazette p.160: 30 April where income/amount is credited or paid in March. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Confirm contractor table item and whether a specific statutory exception applies. |
| Other deductor, non-March | 2025 Act; FY 2026-27; ordinary section 393 contractor branch | Rules 2026, rule 218(2)(b), Gazette p.160: seven days from the end of the deduction/collection month in other cases. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; CONFIGURATION_NOT_CREATED** | Confirm section/table item, recipient class and transition trigger; approve no later than the relevant effective period. |
| Individual/HUF contractor challan-cum-statement route | 2025 Act; FY 2026-27; section 393(1) Table 6(ii), not Table 6(i) | The [Income-tax Act, 2025 (as amended by Finance Act, 2026)](https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf), section 393(1), Table 6(ii), specifies an individual/HUF **payer** (subject to its stated exclusions), a 2% rate and a Rs. 50 lakh threshold. Rules 2026, rule 218(3)(c)(i), Gazette p.160, requires 30 days from the end of deduction month and Form 141 for qualifying contract sums. The Department’s [Form 141 FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/form-141-faqs?mobile-app=1) identifies Schedule C as Individual/HUF payments to contractors/professionals under Table 6(ii), and repeats the 30-day payment requirement. Retrieved 2026-10-01. | **VERIFIED_STATUTORY_FACT; OUTSIDE_CURRENT_CATALOG_SCOPE** | Product owner must decide whether to implement Table 6(ii)/Form 141. This must not be substituted for the existing Table 6(i) record. |

## Transition and catalog scope distinction

The Department’s [Tax Payments FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/tax-payments-faq?mobile-app=1) states that sums paid or credited on or before 2026-03-31 are governed by the 1961 Act and sums on or after 2026-04-01 by the 2025 Act; it also gives the contractor reporting example under section 393(1), Table 6(i). This is transition support, not a substitute for the operative rules above.

The primary Act text resolves the catalog distinction. Table 6(i) covers contract work between a contractor and a **designated person**. It applies the 1% rate when the **contractor/payee** is an individual or HUF; section 402(11) defines the designated-person payer class. The existing `STAT-393-6I-INDHUF-2026-V1` record is therefore correctly scoped but was under-specified. It now explicitly records `payer_category=DESIGNATED_PERSON`, `recipient_category=INDIVIDUAL_HUF_CONTRACTOR`, and `form_141_applicability=NOT_APPLICABLE_TABLE_6_I` as catalog metadata only.

Table 6(ii) is distinct: it applies where the **payer** is an individual or HUF (subject to the statutory exclusions), with its own 2%/Rs. 50 lakh scope and Form 141 route. It remains outside the configured catalog and cannot be selected, used for a due-date policy, or substituted for Table 6(i).

## Unresolved CA/product-owner decisions

1. Confirm initial scope: 194C and section 393 Table 6(i) only, or include the distinct 194M/Table 6(ii)/Form 141 contractor route.
2. Approve the initial product boundary: retain Table 6(i) only, or separately implement, evidence, approve and govern Table 6(ii)/Form 141.
3. Confirm whether `deductor_type` and `challan_route` evidence is captured by CA classification, a client master, or another reviewed source.
4. Approve the documentary source package, approval authority, effective period and exact configuration values for each branch.
5. Decide whether Rule 218(4) special quarterly payment is in initial contractor scope. It requires Assessing Officer permission with Joint Commissioner approval and is **CA_DECISION_REQUIRED**; no generic policy should represent it.

`PILOT_NOT_READY — STOP BEFORE BEHAVIOR CHANGE` remains in force.
