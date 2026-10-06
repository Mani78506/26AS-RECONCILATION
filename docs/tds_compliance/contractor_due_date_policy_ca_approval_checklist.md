# Contractor deposit due-date policy — CA approval checklist

**Record status:** PROPOSED / NOT APPROVED / NOT ACTIVATED
**Prepared:** 2026-10-01
**Product status:** `PILOT_NOT_READY — STOP BEFORE BEHAVIOR CHANGE`

This is a review record only. It is not a `tds_compliance_rules` document and
does not supply a deadline, create a policy, record an approval, or authorize
an activation.

## Approval record fields

| Record item | Verified fact / allowed value | Required CA entry or evidence | Status |
| --- | --- | --- | --- |
| Policy kind | `CONTRACTOR_DEPOSIT_DUE_DATE` is the only supported due-date policy type. | Confirm the policy is limited to the approved contractor branch. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| Payer eligibility | For Table 6(i), the payer is a **designated person**. The application requires reviewed `payer_eligibility_status=CONFIRMED_ELIGIBLE` and an evidence reference; it never infers eligibility from a payer label. | Name the accepted source records and reviewer standard for designated-person eligibility. Reference each source record in the classification review. | VERIFIED STATUTORY FACT; CA_DECISION_REQUIRED |
| Recipient and reporting scope | The configured 2026 catalog row is section 393(1), Table 6(i), contractor/payee `INDIVIDUAL_HUF`, and `NOT_APPLICABLE_TABLE_6_I` for Form 141. | Confirm this is the intended initial product scope. Table 6(ii)/Form 141 requires a separate future decision. | VERIFIED STATUTORY FACT; CA_DECISION_REQUIRED |
| Governing Act, provision and FY | Pre-transition contractor branch: Income-tax Act, 1961, section 194C, FY 2025-26. Post-transition branch: Income-tax Act, 2025, section 393(1), Table 6(i), FY 2026-27. | Select exactly one Act/FY/section/table scope for each policy record. | VERIFIED STATUTORY FACT; CA_DECISION_REQUIRED |
| Effective period | Policy admission requires an ISO effective start/end date intersecting the selected FY. | Enter source-supported dates for the selected branch; do not infer a date range from the FY label alone. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| Deductor category | Allowed values are `GOVERNMENT_OFFICE` and `OTHER_DEDUCTOR`. | Select one and attach classification evidence. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| Challan route | `GOVERNMENT_OFFICE` requires exactly `WITH_CHALLAN` or `WITHOUT_CHALLAN`; it is otherwise optional. | Select and evidence the route where the deductor is a Government office. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| 1961 timing source | Rule 30(1)(a), (1)(b), (2)(a) and (2)(b) contain the verified Government/no-challan, Government/challan, other-deductor March, and other-deductor non-March branches. | Choose the applicable branch. Map its source-supported timing to one supported deadline mode and enter all required parameters. | VERIFIED STATUTORY FACT; CA_DECISION_REQUIRED |
| 2026 timing source | Rule 218(1)(a), (1)(b), (2)(a) and (2)(b) contain the corresponding verified branches. | Choose the applicable branch. Map its source-supported timing to one supported deadline mode and enter all required parameters. | VERIFIED STATUTORY FACT; CA_DECISION_REQUIRED |
| Deadline configuration | Supported modes are `DEDUCTION_DATE`, `MONTH_END_PLUS_DAYS`, and `FIXED_MONTH_DAY`; the latter two require their corresponding parameters. | Enter only the parameters approved for the selected Rule 30/218 branch. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| Authoritative source | Submission requires source authority/reference, HTTPS URL, document title, provision reference, retrieval date and evidence extract. | Attach the official source package and CA interpretation. | VERIFIED IN CODE; CA_DECISION_REQUIRED |
| Approval authority | The authenticated approval route records `approved_by` and `approved_at`; activation is a separate action. | Identify the authorized CA/product owner by name and role. | CA_DECISION_REQUIRED |
| Decision evidence | `approval_metadata` is available for a decision reference. | Attach an approval reference, signed decision/source record, named approver and decision timestamp. | CA_DECISION_REQUIRED; IMPLEMENTATION_GAP_NOTED |
| Snapshot requirement | Phase 4 freezes selected policy/version/source/branch inputs; Phase 5 uses that snapshot rather than a live due-date policy. | Confirm the frozen snapshot satisfies the required audit evidence. | VERIFIED IN CODE; CA_DECISION_REQUIRED |

## Primary-source references

- [Income-tax Rules, 1962, rule 30](https://www.incometaxindia.gov.in/documents/20117/11892059/Rule%2B-%2B30_en.pdf/d159c547-6aaa-99bf-ea3b-213295715a56?t=1766001159027&version=1.0), retrieved 2026-10-01: verified timing branches listed above.
- [Income-tax Act, 2025, as amended by Finance Act 2026](https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf), retrieved 2026-10-01: section 393(1), Table 6(i), and section 402(11) establish the contractor/designated-person branch.
- [Income-tax Rules, 2026, rule 218](https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-03/En-Notified-IT-Rules-2026-20-03-2026.pdf), retrieved 2026-10-01: verified 2026 timing branches listed above.
- [Form 141 FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/form-141-faqs?mobile-app=1), retrieved 2026-10-01: supporting evidence that Form 141 Schedule C is the distinct Table 6(ii) route; it does not approve or configure a Table 6(i) policy.

## Lifecycle gate

The application creates every policy as `DRAFT` and `active=false`. Submission
calls `_contractor_due_date_policy_approval_errors`; an incomplete draft cannot
enter `PENDING_APPROVAL`. Approval is permitted only from `PENDING_APPROVAL`,
and activation only from `APPROVED`; activation rejects an overlapping active
scope. Selection additionally requires one active, source-verified policy with
an approval actor/time and a matching scope.

**Observed limitation:** `approval_metadata` is accepted and persisted but is
not currently required by the due-date submission validator. The lifecycle does
record `approved_by` and `approved_at` when an authorized user invokes the
approval action. If a documentary decision reference must be mandatory, that is
a separate, approved validation change; it must not be implied by this checklist.

## CA sign-off block — intentionally blank

| Field | Value |
| --- | --- |
| Selected statutory branch | `CA_DECISION_REQUIRED` |
| Policy scope / organisation / client / assignment | `CA_DECISION_REQUIRED` |
| Effective from / to | `CA_DECISION_REQUIRED` |
| Deadline mode and parameters | `CA_DECISION_REQUIRED` |
| Official-source package reference | `CA_DECISION_REQUIRED` |
| CA/product-owner name and role | `CA_DECISION_REQUIRED` |
| Approval timestamp | `CA_DECISION_REQUIRED` |
| Decision evidence reference | `CA_DECISION_REQUIRED` |
| Approval decision | `CA_DECISION_REQUIRED` |

No field in this document authorizes submission, approval or activation.
