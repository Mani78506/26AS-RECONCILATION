# Engine layout

## `reconciliation/`

The three existing reconciliation workflows and all their matcher/classifier,
identity, normalisation, validation and reporting code.

## `tds_compliance/`

The independent deductor-side Payment Ledger and statutory-calculation domain.

## `shared/`

- `parser.py` — CSV/XLS/XLSX/PDF table reading shared where appropriate
- `schema.py` — shared file-schema and alias registry
- `sample_data.py` — reconciliation sample data
- `ai_assistant.py` — application AI-context helper

All active imports use these folders directly; root-level engine files are not
used for implementation code.
