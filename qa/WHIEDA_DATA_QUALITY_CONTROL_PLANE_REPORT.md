# WHIEDA Data Quality Control Plane — Delivery Report

**Date:** 2026-08-08  
**Scope:** Local read-only quality gate before sync. No Sheets, n8n, prod, or source file writes.

## Entry points

```powershell
python qa\data_quality\run_data_quality.py --validate   # schema + checks
python qa\data_quality\run_data_quality.py --baseline   # hash snapshot
python qa\data_quality\run_data_quality.py --diff         # vs baseline
python qa\data_quality\run_data_quality.py --report       # MD + JSON
python qa\data_quality\run_data_quality.py --all          # validate + baseline + diff + report

.\qa\run_data_quality_all.ps1                           # validate + report + pytest
```

Drop real exports into `qa/data_quality/exports/` (paths in `source_manifest.json`).

## What was verified

| Check | Result |
|---|---|
| Fixture validate (`manifest.test.json`) | **PASS** — 12 layers, 0 errors |
| Production manifest (`source_manifest.json`) | **WARN** — 12/12 layers **SOURCE MISSING** (expected; no exports yet) |
| pytest `tests/data_quality/` | **49 passed** |
| `run_data_quality_all.ps1` | **PASS** |
| Network calls in runner | **None** (static guard) |
| Writes to export sources | **None** (static guard) |
| Prod URLs / credentials in runner | **None** (static guard) |

## Fixtures

- **72 scenarios**, **121 fixture files** under `qa/data_quality/fixtures/scenarios/`
- Covers all 15 quality checks, baseline/diff (price, alias removal, safety), csv/tsv/json/jsonl loaders
- Index: `qa/data_quality/fixtures/fixtures_index.json`

## Production exports (honest status)

Nothing was read from live WHIEDA data. All 12 expected TSV paths under `qa/data_quality/exports/` are absent:

- `products_prices`, `product_cards`, `product_aliases`, `resource_links`
- `product_comparisons`, `solution_bundles`, `business_faq`, `promotions`
- `events`, `users_access`, `structure_owners`, `certificates`

Until exports are placed locally, cross-layer rules (alias→SKU, bundle→SKU, cert→SKU, price conflicts) **cannot run on real catalog data**. The engine reports `SOURCE MISSING` per layer — not a validation failure.

## Rules not yet exercised on real data

Without exports, these remain **unverified against production**:

1. SKU uniqueness across full catalog
2. Active price conflicts per country/type
3. Resource/certificate orphan detection at scale
4. Bundle inactive-product detection on live bundles
5. Medical/safety warnings on real card copy
6. Baseline diff on actual sync deltas

Contracts and checks are implemented; they run fully on fixtures only today.

## Artifacts

| Path | Purpose |
|---|---|
| `qa/data_quality/source_manifest.json` | Production export paths |
| `qa/data_quality/contracts/*.json` | 12 layer schema contracts |
| `qa/data_quality/dqc/` | loader, schema, checks, baseline, diff, report, engine |
| `qa/data_quality/reports/` | Generated reports (gitignored) |
| `qa/data_quality/baselines/` | Generated baselines (gitignored) |

## Next step for operators

1. Export Sheets tables to TSV (or csv/json/jsonl) into `qa/data_quality/exports/`
2. `python qa\data_quality\run_data_quality.py --all`
3. Fix blocking errors in Sheets, re-export, re-run until **PASS** or acceptable **WARN**
