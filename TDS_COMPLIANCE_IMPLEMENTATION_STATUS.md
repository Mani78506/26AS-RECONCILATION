# TDS Compliance Implementation Status

## DONE

- Isolated workflow/domain constants, law-determination service, honest PAN
  status, versioned approved-rule contract, calculation contract and provenance.
- Isolated Mongo collection/index foundation for assignments, compliance rules
  and append-only audit events.
- Protected API route contracts for schema, rules and assignments.
- Frontend workflow card, route, empty state and assignment-form skeleton.
- Unit tests for transition law, missing rule/PAN, rule version and provenance.
- Payment-ledger CSV/XLSX/XLS ingestion using the existing spreadsheet parser,
  conservative header aliases, basic PAN format status, explicit date/FY
  review states, source provenance and duplicate-review markers.
- Assignment-scoped payment-ledger preview, immutable commit/version snapshot,
  latest-ledger retrieval and source-file deletion protection.
- A selected-assignment Payment Ledger upload/preview/commit UI. Missing TDS,
  deposit and challan values are displayed as `Not Provided`, never as zero.
- Phase 3 configured-rule calculation service: governing-law cutover,
  controlled payment-nature classification, threshold/aggregate evaluation,
  source PAN and certificate treatment, precision-aware expected TDS, and
  actual-versus-expected comparison with traceable explanation data.
- Assignment-scoped, immutable calculation preview/run/history API contracts
  and a committed-ledger-only calculation workbench.

## PHASE 3 HARDENING AUDIT (2026-09-12)

### Verified

- Earlier-of-credit/payment governing-law transition and post-transition
  section-393 rule-reference guard.
- Decimal calculation, explicit threshold grouping, configured PAN treatment,
  null actual-TDS handling and immutable per-result rule snapshots.
- Rule, ledger and calculation reads remain assignment-scoped.

### Defects fixed

- Overlapping same-priority rules previously selected by lexical rule version;
  they now require an explicit unique configured priority.
- Duplicate and negative ledger rows are now review-required and excluded from
  automatic calculation.
- A zero taxable amount no longer falls back to gross amount.
- Unsupported configured rounding now fails for review instead of silently
  using HALF_UP.
- Certificate rates now require a certificate reference and valid dates.
- Historical calculation rows retain the complete selected-rule snapshot,
  not only a live rule ID/version reference.
- Final closure regression persists a deep-frozen V1 calculation payload,
  mutates the live V1 fixture and runs V2; V1 remains unchanged while V2 has
  its own expected TDS, difference and rule snapshot.

### Configuration required

- No authoritative production statutory rules were inserted. Only clearly
  labelled test fixtures exercise the calculator. CA-approved rule data and a
  real OIDC token-verification adapter remain required before production use.

## PHASE 4 — INTEREST & DEPOSIT COMPLIANCE

### Implemented

- A downstream, assignment-scoped interest engine that consumes immutable
  Phase 3 calculation rows and their committed ledger source dates.
- Separate configured deduction-delay and deposit-delay rule selection,
  configurable period counting, interest bases, rounding and deposit-deadline
  modes (`FIXED_OFFSET_DAYS`, `NEXT_MONTH_CONFIGURED_DAY`, and
  `CHALLAN_CUM_STATEMENT`).
- Immutable interest runs with deduction/deposit rule snapshots, previews,
  history, detail and summary endpoints, plus a Phase-4 UI workbench.
- Explicit fail-closed states for ambiguous rules, zero expected TDS, missing
  actual TDS, missing deduction/deposit evidence and invalid date sequences.

### Verified

- Phase 4 unit coverage includes configurable period/deadline calculation,
  deduction and deposit delays, missing evidence, invalid sequences, missing
  and ambiguous rules, zero expected TDS, and V1-to-V2 snapshot immutability.
- Preview and persisted runs call the same Phase 4 engine; assignment and
  Phase 3 calculation IDs are required on every Phase 4 API operation.

### Configuration required / deliberately not assumed

- No production interest rate, due date, March exception, interest base or
  month/part treatment has been inserted or hardcoded.
- Missing actual deduction/deposit dates, actual TDS, calculation rules or
  interest rules fail closed as explicit review statuses.

### Future scope

- Phase 5 may address later compliance work only after CA-approved Phase 3
  and Phase 4 rules plus real OIDC verification are configured.

## NOT DONE

- Due dates, interest, challan, 26AS/TRACES checks, exceptions, reviewer
  actions, reports and filing exports.
- Real production OIDC token-verification adapter.

## BLOCKED

The office has not supplied an OIDC provider configuration or token claim
contract. TDS Compliance APIs intentionally return a transparent fail-closed
response rather than accept a fabricated local identity.

## NEXT

With an approved OIDC provider: implement cryptographic JWT verification and
organisation/client claim enforcement, then enable the already-built
assignment and payment-ledger APIs for office users.
