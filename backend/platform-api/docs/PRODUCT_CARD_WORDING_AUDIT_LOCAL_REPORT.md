# Product Card Wording Audit — Local Report

Mode: read-only review markings. No cards, Sheets, runtime or Telegram changes.

- Snapshot: `20260811T172219Z`
- Approved cards: 23
- Candidate cards: 16
- Findings total: 26
- Cards with findings: 12
- Cards without findings: 27

## Findings by rule

- `PROV-NEEDS-REVIEW`: 18
- `REPEAT-CROSS`: 4
- `VAGUE-PHRASE`: 4

## Findings by severity

- `attention`: 18
- `review`: 8

## Reproduce

```bash
python qa/product_card_wording_audit/run_product_card_wording_audit.py
python -m pytest backend/platform-api/tests/test_product_card_wording_audit.py -q
```

Generated CSV/MD/JSON live under `qa/product_card_wording_audit/reports/` (gitignored).
