# TDS Compliance Foundation

## Purpose and boundary

`TDS_COMPLIANCE` is a deductor/company-side CA-office module. It is not a
fourth choice in **New Reconciliation**, an assessee-side 26AS reconciliation,
Sales + TDS + 26AS reconciliation, or a claimability workflow. The client
workspace reaches it through its dedicated navigation entry and `/tds-compliance`
route. Its collections and APIs are workflow-scoped and it does not call the
existing matcher, classifier, identity resolver, or claimability engine.

## Domain contracts

- Audit assignment: organisation, client, company/TAN, tax period, lifecycle,
  version and lock metadata.
- Payment ledger: supplied vendor, invoice, credit/payment, TDS and challan
  fields. Missing evidence stays `null`; deducted TDS is never copied to
  deposited TDS.
- Deductee master: vendor identity and an honest PAN state. Format validity is
  not PAN verification.
- Compliance rules: act, FY/tax year, section/table reference, payment nature,
  deductee type, rates, thresholds, effective period, version, official source
  and approval reference. Only `APPROVED`/`ACTIVE` rules are eligible.
- Audit events: append-only action records; no update or delete route exists.

## Law transition

For non-salary transactions, the governing law is determined from the earlier
of credit date and payment date: through `2026-03-31` is the Income-tax Act,
1961; from `2026-04-01` is the Income-tax Act, 2025. A financial-year label
never replaces a missing event date. New-law rules use `section_reference` and
`table_reference`, allowing section 393 table items without inventing a mapping.

## Data flow and statuses

Future flow: assignment -> supplied payment ledger/deductee master -> law
determination -> approved-rule selection -> calculation -> compliance result ->
append-only audit event. Calculation returns explicit `RULE_NOT_FOUND`,
`LAW_NOT_DETERMINABLE`, `INSUFFICIENT_DATA`, or `REQUIRES_REVIEW`, never a
fabricated zero.

The foundation defines separate deduction, deposit, PAN, rule and overall
status vocabularies in `backend/engine/tds_compliance.py`.

## Provenance

Each canonical source row retains file, row and source-reference fields.
`SOURCE_PROVIDED` is used for a source identifier; `SYSTEM_GENERATED` is
reserved for visibly prefixed IDs when none exists.

## Security boundary

The API fails closed until a real OIDC issuer, audience, JWKS URL and
cryptographic token-verification adapter are configured. It never trusts a
display name, session storage, or caller-provided organisation/client header.
This is intentionally stricter than the legacy routes, which pre-date the
server-side OIDC integration.

## Future phases

Payment-ledger ingestion, due dates/interest, challan/TRACES reconciliation,
quarterly filings, reports, reviewer approvals and immutable audit locking
remain separate future phases.
