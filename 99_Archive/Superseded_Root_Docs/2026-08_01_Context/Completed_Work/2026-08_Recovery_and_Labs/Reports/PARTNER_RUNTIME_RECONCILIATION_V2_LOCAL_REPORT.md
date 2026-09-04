# Partner Runtime Reconciliation V2 — Local Report

**Date:** 2026-08-10  
**Status:** report buckets fixed; live read-only run **safe** (per architect review)

## Report bucket correction

`master_only_needing_upsert` now lists **only** master actor IDs absent from active runtime. Added disjoint bucket `in_sync_master_actors`.

## Live snapshot (architect read-only run, 2026-08-10)

```text
in_sync_master_actors: 6
master_only_needing_upsert: 0
runtime_only_disable_candidates: 0
allowlisted_platform_roots: 2  (viktor, viktor-test)
summary: safe
```

No runtime mutation was performed.

## Fixture dry-run (master_six + runtime_with_extras)

```text
in_sync_master_actors: 6
master_only_needing_upsert: 0
runtime_only_disable_candidates: 1  (retired-partner)
allowlisted_platform_roots: 2
summary: review_required
```

## Verification

```powershell
python -m pytest backend/platform-api/tests/test_partner_runtime_reconciliation.py -q
```

Includes `test_v2_report_buckets_disjoint_and_exhaustive`.

## CLI (runtime read-only)

```powershell
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py `
  --master-tsv <Partners_Ref export.tsv> `
  --runtime-dsn-env WHIEDA_RUNTIME_READONLY_DSN `
  --tenant whieda `
  --report-out .tmp/partner_parity.json `
  --markdown-out .tmp/partner_parity.md
```

Rules unchanged: `BEGIN READ ONLY` + SELECTs only; `--apply` + `--runtime-dsn-env` aborts before connect.
