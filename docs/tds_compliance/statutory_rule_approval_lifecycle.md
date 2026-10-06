# Statutory rule lifecycle

## Global verified statutory catalog

Global catalog rows are source-controlled configurations. An `ACTIVE` catalog
row is calculation-eligible only when its source traceability is `VERIFIED`:
authority, official HTTPS URL, document title, provision reference, effective
period, retrieval date, verification evidence, and verification date.

The catalog importer turns a validated `ACTIVE` row into an `ACTIVE` global
runtime rule. It does not require `approved_by`, `approved_at`,
`approval_authority`, or `approval_reference`, and it never invents those
fields. A `DRAFT` catalog row stays inactive and cannot calculate.

## Manual and client-specific rules

Manual organisation, client, and assignment rules remain governed:

```text
DRAFT -> PENDING_APPROVAL -> APPROVED -> ACTIVE
```

The manual activation endpoint requires verified source traceability, its
recorded approver and timestamp, approval authority/reference, and existing
conflict checks. `DRAFT`, `PENDING_APPROVAL`, and `APPROVED` rules cannot
calculate.

## Calculation eligibility

`select_statutory_rule()` selects only `ACTIVE` rules. Source verification does
not override missing, ambiguous, unsupported, or out-of-scope transaction
conditions; those continue to fail closed through the established calculation
statuses.

## Existing contractor catalog records

`STAT-194C-INDHUF-2025-V1` and `STAT-393-6I-INDHUF-2026-V1` are verified
global catalog rows. They remain `ACTIVE` through the catalog import path and
do not claim CA approval that is not recorded.

## Out of scope

This lifecycle does not activate any FY 2026-27 source row and does not change
statutory rates, thresholds, due-date policies, interest policies, Return
Audit, Review Queue, assignment locking, or 26AS Reconciliation.
