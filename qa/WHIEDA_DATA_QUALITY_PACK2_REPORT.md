# WHIEDA Data Quality — Local Tasks Pack #2 Report

**Date:** 2026-08-08  
**Commits:** `394b915`, `6393551`, `20745d2`

---

## Block A — Release modes and sync gate

**Commit:** `394b915` — `feat: add data quality release modes and sync gate`

### Commands

```powershell
python qa\data_quality\run_data_quality.py --mode dev --validate
python qa\data_quality\run_data_quality.py --mode release --validate
python qa\data_quality\run_data_quality.py --mode full --validate
```

### Actual output (production manifest, no exports)

| Mode | Status | Exit | Can sync |
|---|---|---:|---|
| `dev` | WARN | 0 | yes |
| `release` | FAIL | 1 | no |
| `full` | FAIL | 1 | no |

Release requires: `products_prices`, `product_cards`, `product_aliases`, `resource_links`.

Fixtures (`manifest.test.json`, release mode): **PASS**, can sync **yes**.

### Confirmed

- Report shows mode, required/found/missing layers, can sync yes/no
- Baseline blocked on FAIL (release without exports)
- Baseline blocked on empty dataset (`empty_products`)
- 6 new pytest cases for modes + baseline guards

### Not verified on real data

Cross-layer validation on live catalog — exports still absent in `qa/data_quality/exports/`.

---

## Block B — Read-only export snapshots

**Commit:** `6393551` — `feat: add read-only export snapshots for data quality`

### Commands

```powershell
python qa\data_quality\snapshot_exports.py --manifest qa\data_quality\source_manifest.json
python qa\data_quality\snapshot_exports.py --verify qa\data_quality\snapshots\<file>.json
```

### Actual output (fixture manifest)

```
Snapshot saved: qa\data_quality\snapshots\test_fixture_snapshot.json
Layers found: 12  missing: 0
Snapshot verify status: PASS
No layer file changes detected.
```

Production manifest snapshot: 12 layers **missing**, metadata-only (no row content, no PII).

### Confirmed

- SHA-256, size, row count, found/missing status per layer
- Verify detects changed / removed / added files (7 pytest cases)
- `qa/data_quality/snapshots/*` gitignored except `.gitkeep`

---

## Block C — Rule coverage registry

**Commit:** `20745d2` — `test: enforce data quality rule coverage`

### Command

```powershell
python qa\data_quality\verify_rules_coverage.py
```

### Actual output

```
Rules coverage status: PASS
Active rules: 21
Covered by fixture: 21
Covered by pytest: 21
All active rules are registered, fixtured, and tested.
```

Registry: `qa/data_quality/quality_rules_registry.json`

### Confirmed

- Fails if active rule lacks fixture or pytest
- Fails if code check not in registry
- Fixed `missing_product_ref` for empty SKU on resource links

---

## Full gate run

```powershell
.\qa\run_data_quality_all.ps1
```

**Result:** PASS — validate + report + rules coverage + **70 pytest** passed.

---

## Honest limits

- **No real WHIEDA exports** in `qa/data_quality/exports/` — production gate is WARN/FAIL by design
- Snapshot/baseline on production = metadata only until files are dropped locally
- xlsx support unchanged (requires openpyxl if present)

### Operator next step

1. Export Sheets → `qa/data_quality/exports/*.tsv`
2. `python qa\data_quality\snapshot_exports.py` (baseline fingerprint)
3. `python qa\data_quality\run_data_quality.py --mode release --all`
4. Fix errors in Sheets, re-export, re-run until **PASS** + can sync **yes**
