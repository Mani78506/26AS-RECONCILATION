# 26AS Reconciliation engine

This folder contains only assessee-side reconciliation workflows:

- `pipeline.py` — orchestration for FULL_RECONCILIATION, 26AS_ONLY and SALES_TDS_26AS
- `matcher.py`, `classifier.py`, `identity.py` — matching, classification and identity resolution
- `normalizer.py`, `validator.py` — reconciliation source normalization and validation
- `tds_rules.py`, `tds_calculator.py` — configured 26AS-only analysis rule support
- `sales_tds_workflow.py` — Sales + TDS Expected/Receivable + 26AS workflow
- `reports.py` — reconciliation reports

The small modules in `engine/` with the same names are compatibility facades.
They preserve established imports such as `engine.pipeline` while this folder
remains the implementation source of truth.
