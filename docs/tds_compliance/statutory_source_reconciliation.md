# FY 2026-27 statutory source reconciliation

Source of record for this project: the supplied six-page Taxmann *Direct Taxes Ready Reckoner* scan, `Scan_20261005_121033.pdf`, Para R1.6, printed pages 28?33. The scan is image-only; its table was visually inspected for the Category-B reconciliation described below.

This artifact reconciles the source transcription to the **current** executable catalog. It does not verify law beyond the project source, create CA approvals, activate rules, or change the calculation workflow.

## Counts

- Source rows: **92** ? 46 resident and 46 non-resident/foreign-company rows.
- Supported for configuration: **14**.
- Current executable rules mapped to a FY 2026-27 source row: **14**.
- Source ambiguous: **3**.
- Missing product input: **63**.
- Not executable yet: **12**.
- Source-row unresolved: **0**.

The complete per-row reconciliation is in `statutory_source_reconciliation.csv`; it contains all requested source, Act, section/table, legacy section, party, rate, threshold, special-condition, page, catalog, executable-state, result, and dependency fields.

## Classification rule

- `SUPPORTED_FOR_CONFIGURATION`: exact source dimensions match a current verified catalog rule; this is not CA approval.
- `SOURCE_AMBIGUOUS`: the table gives a dash/blank or points to an external schedule rather than supplying the rate necessary for this source row.
- `MISSING_PRODUCT_INPUT`: the source is legible but its stated condition needs a controlled fact/evidence contract absent from the current general ledger/classification path.
- `NOT_EXECUTABLE_YET`: source row is transcribed but no lifecycle-governed catalog rule exists.
- `SOURCE_ROW_UNRESOLVED`: reserved only for an illegible or incomplete source row; none remain in the current manifest.

## Category-B discrepancy

The claimed 46th Category-B row is present in the current source and manifest. On printed PDF page 32, section 393(2), Table 17, legacy section 195(1), item **b** is ?income by way of long-term capital gains on transfer of foreign exchange assets? at **12.5%**. It is transcribed as `CA-FY2026-27-NR-33` with the condition ?Section 197 long-term gain; SC cap 15%?. The Category-B count is therefore **46**, not 45 plus a placeholder. It is `MISSING_PRODUCT_INPUT`, not executable, because the product has no controlled Section-197/capital-gain evidence contract. No row was invented or activated.

## Existing contractor rules

- `STAT-393-6I-INDHUF-2026-V1` exactly maps to `CA-FY2026-27-RES-20`: section 393(1), Table 6(i), legacy 194C, contractor, individual/HUF contractor, 1%, and 30,000 single / 100,000 aggregate threshold. It is the sole mapped executable source row.
- `STAT-194C-INDHUF-2025-V1` is a FY 2025-26 legacy rule. The FY 2026-27 source table supplies its legacy cross-reference through `CA-FY2026-27-RES-20`, but cannot itself establish the earlier FY rule. Its separate existing primary-source traceability remains the relevant evidence. The rule was not changed.

## Source dependencies retained as non-executable

The rows with `External salary-rate schedule` are `CA-FY2026-27-RES-01`, `CA-FY2026-27-RES-33`, and `CA-FY2026-27-NR-01`; their source-table rates are blank and they remain `SOURCE_AMBIGUOUS`. Other condition-bearing rows retain their exact stated condition in the CSV and remain gated on the identified product input/evidence contract. The manifest retains `SOURCE_VERIFIED` for the thirteen PDF rows represented by active verified catalog rules; all remaining rows remain `TRANSCRIBED_UNVERIFIED`. The manifest is not an activation mechanism.

## Verified executable batch

`CA-FY2026-27-RES-05`, `CA-FY2026-27-RES-39`, `CA-FY2026-27-RES-40`, and `CA-FY2026-27-RES-41` are active verified global catalog rules. `RES-05` is section 393(1), Table 1(ii) commission/brokerage. It requires verified specified-person payer evidence and verified non-BSNL/MTNL-PCO-franchisee evidence before selection.

`CA-FY2026-27-RES-39`, `CA-FY2026-27-RES-40`, and `CA-FY2026-27-RES-41` are active verified global catalog rules. The supplied PDF was visually inspected at printed page 30 and is retained by filename, page reference and SHA-256 fingerprint. They map respectively to section 393(3), Table 1 / legacy 194B (lottery winnings other than online games, 30%, Rs. 10,000 per transaction), Table 2 / legacy 194BA (online-game winnings, 30%, zero threshold), and Table 3 / legacy 194BB (horse-race winnings, 30%, Rs. 10,000 per transaction). The non-resident counterparts `CA-FY2026-27-NR-03`, `CA-FY2026-27-NR-04`, and `CA-FY2026-27-NR-05` remain non-executable because source note 1 requires surcharge/cess treatment not modelled by the engine.

`CA-FY2026-27-RES-34`, `CA-FY2026-27-RES-42`, `CA-FY2026-27-RES-45`, and `CA-FY2026-27-RES-46` are also active verified global catalog rules. They map to section 393(1), Table 8(iv) / legacy 194R (benefit or perquisite, 10%, Rs. 20,000 aggregate), and section 393(3), Tables 4, 6 and 7 / legacy 194G, 194EE and 194T (lottery-ticket commission, 2%, Rs. 20,000 aggregate; section 80CCA(2)(a) National Savings Scheme amount, 10%, Rs. 2,500 aggregate; and firm-to-partner remuneration or interest, 10%, Rs. 20,000 aggregate). Each selection requires the source-specific verified evidence configured in the rule.

`CA-FY2026-27-RES-32`, `CA-FY2026-27-RES-35`, `CA-FY2026-27-RES-36`, `CA-FY2026-27-RES-37`, and `CA-FY2026-27-RES-38` are active verified global catalog rules. They cover purchase of goods (0.1% only on the amount exceeding Rs. 50 lakh, after eligible-buyer and no-other-TDS/TCS evidence), e-commerce participant payments (0.1%, with the individual/HUF PAN/Aadhaar and Rs. 5 lakh no-deduction branch represented), and VDA transfer consideration (1%, with the Rs. 50,000 specified-person and Rs. 10,000 other-person no-deduction branches represented). VDA rules require verified transfer, payer-capacity, and release/tax-payment-condition evidence. Rent rows remain non-executable because monthly-threshold and REIT no-deduction branches are not yet represented.


## Behavior boundary

This reconciliation records the verified active mapping for the listed source rows only. It does not add a due-date policy, interest policy, source approval, database record, or change the existing workflow or 26AS Reconciliation.
