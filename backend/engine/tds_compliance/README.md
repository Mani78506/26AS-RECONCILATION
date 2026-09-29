# TDS Compliance engine

This folder contains the separate deductor-side TDS Compliance domain:

- `core.py` — assignments, payment-ledger normalization/validation, configured-rule calculation, thresholds, PAN/certificate handling and immutable calculation snapshots.

It does not import or alter the reconciliation matcher, classifier,
claimability, or reconciliation results. The package export in `__init__.py`
keeps the established `engine.tds_compliance` public API available.
