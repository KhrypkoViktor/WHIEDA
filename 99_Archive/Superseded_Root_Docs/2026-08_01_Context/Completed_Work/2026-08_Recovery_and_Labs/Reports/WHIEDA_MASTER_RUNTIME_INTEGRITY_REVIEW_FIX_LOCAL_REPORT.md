# Master Runtime Integrity — Review Fix Local Report

Date: 2026-08-11  
Task: `WHIEDA_DROVOSEK_MASTER_RUNTIME_INTEGRITY_REVIEW_FIX_V1_2026-08-11.md`  
Scope: read-only auditor normalization only. No Sheets, Postgres writes, n8n, cron, deploy.

## What changed

### A. Alias business-key parity
- Compare `(alias, canonical_sku)` unique sets, not raw TSV row count alone
- Report: `master_raw_rows`, `master_unique_rows`, `runtime_unique_rows`, `duplicate_rows_in_master`, `duplicate_examples` (cap 10)
- Matching unique keys → `in_sync` + `master_duplicate_rows` when master TSV has duplicate pairs

### B. Semantic title normalization
- Narrow normalization: strip quotes (`"`, `«»`, …), collapse whitespace, trim
- Applied per stable id on `products`, `product_cards`, `resources`
- Raw hash may differ; normalized hash drives parity

### C. Explicit reasons
- `equivalent_after_normalization`
- `master_duplicate_rows`
- `true_runtime_missing_ids` / `true_runtime_extra_ids`
- `true_content_mismatch_after_normalization`

## Commands

```powershell
python -m pytest qa/master_integrity backend/platform-api/tests/test_master_integrity.py -q
python n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py
```

## Test results

```
24 passed
```

New cases: alias 120/118/118, duplicate surfacing, quoted БА-ГУА product/resource equivalence, true title mismatch, existing stale/extra/missing preserved.

## Live read-only run (20260811T172219Z snapshot)

**Overall: `safe` (exit 0)**

| Layer | Before | After | Notes |
|-------|--------|-------|-------|
| aliases | runtime_stale | in_sync | 120 raw / 118 unique = runtime 118; 2 duplicate pairs in master |
| products | runtime_stale | in_sync | equivalent_after_normalization (quoted titles) |
| resources | runtime_stale | in_sync | equivalent_after_normalization |
| product_cards | runtime_stale | in_sync | equivalent_after_normalization |
| partners_ref | not_runtime_backed | not_runtime_backed | unchanged |

Sync freshness: last success `2026-08-11T19:45:15Z`.

## Limitations

- Normalization is presentation-only; it does not fuzzy-match different SKUs or resource IDs
- Alias duplicate cleanup in master is informational (`master_duplicate_rows`), not auto-reconciled
- `partners_ref` remains not runtime-backed by design
