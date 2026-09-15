# Telegram Golden Local Master Parity Report

Date: 2026-08-13  
Task: `WHIEDA_DROVOSEK_GOLDEN_LOCAL_MASTER_PARITY_AND_RUNNER_FIX_TASK_V1_2026-08-13.md`

## Summary

Closed the **fixture parity gap** between Golden corpus (master snapshot `20260810T083328Z`) and local Core lab (synthetic `staging_seed_whieda_advisor_local_v1.sql`). Fixed harness so P0/full/negative phases all execute independently.

| Gate | Result |
|------|--------|
| `compile_local_master_seed.py` | **PASS** — 40 products, 118 aliases, 23 cards, 176 resources |
| Golden offline lint | **PASS** |
| Unit tests (`test_telegram_golden*.py`) | **PASS** (30/30) |
| P0 dry-run flow inclusion | **PASS** — 116 cases + **23 flows** (was 0 flows) |
| E2E `--golden-master-seed` live HTTP | **NOT_RUN** — Docker daemon unavailable on report machine |

## What changed

### A. Master-parity fixture (deterministic)

| Artifact | Purpose |
|----------|---------|
| `qa/telegram_golden/compile_local_master_seed.py` | CLI builder |
| `qa/telegram_golden/lab/master_seed_compiler.py` | Manifest SHA gate + TSV→SQL |
| `qa/telegram_golden/fixtures/local_master_seed.sql` | Idempotent upsert overlay for tenant `whieda` |
| `qa/telegram_golden/fixtures/local_master_seed_manifest.json` | Source SHA-256, layer stats, golden SKU coverage |

- Source: read-only `n8n/live-exports/structured-master/20260810T083328Z/`
- Aborts on missing TSV or SHA-256 mismatch
- Never DELETE; synthetic `LOCAL-*` fixtures remain
- Unmapped layers recorded: `intent_registry`, `partners_ref`, `structure_owners`, `users_access`
- All 13 golden snapshot SKUs present in products + cards

### B. Opt-in lab mode

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
```

Sequence: compile fixture → `ensure_local_core_database.py --apply-golden-master-seed` → golden HTTP phases.  
Default lab unchanged without `--golden-master-seed`.

### C. Runner completeness fixes

1. **`--priority P0`** now includes whole flows with ≥1 P0 turn; setup turns labeled `turn_role=setup`.
2. **E2E hook** runs all phases even after P0 fail: check-target → P0 → full positive → `--negative-only`.
3. Reports show fixture parity identity, setup vs assertion turn counts, redacted previews only.

## Before / after (failure attribution)

Previous run `20260812T132043Z-a3eb6744` used **synthetic seed only** → **38/107 P0 failures**. Many were fixture gaps, not Core logic.

| Failure cluster | Before (synthetic seed) | Expected after master seed |
|-----------------|-------------------------|----------------------------|
| Snapshot cards → `clarification` (Стельки, Очки, Палантин, Линчжи, Лювэй, Спирулина, …) | **Fixture missing** — SKUs absent or `LOCAL-*` stubs | Products/cards/aliases from master TSV |
| `GOLD-SMOKE-P0-001` activator ambiguity | **Fixture** — alias pointed to `LOCAL-ACT` not `M015-00` | Master aliases prefer real SKU |
| Price/media flow turns (F02–F04, F09, F11) | **Fixture** — context product missing/wrong card | Full master cards + resources |
| `GOLD-NBZ-*` «каталог» text | **Mixed** — may remain Core copy/routing | Catalog SKU `E028-00` now in seed |
| `GOLD-FUZZ-*` missing «WHIEDA» | **Core/copy** — greeting text from capability table | Master capability responses loaded; still Core if text differs |
| `GOLD-SMOKE-SAFE-*` disclaimer phrasing | **Core/copy** | Not a fixture issue |
| `GOLD-BUS-EXTRA-*` FAQ routing | **Mixed** | Master business_faq loaded; routing still Core |

### Previous 38 failed case IDs (synthetic seed baseline)

`GOLD-SMOKE-P0-001`, `P0-005`, `P0-012`–`P0-019`, `SAFE-001/002`,  
`GOLD-CONV-CONV-F01-card-price-T2/T3`, `F02`–`F05`, `F09`, `F11`, `F12`, `F15`, `F19`, `F20`,  
`GOLD-NBZ-NBZ-P0-001`–`006`, `GOLD-EXP-TG-CART-CALC-REMOVE-T2`,  
`GOLD-FUZZ-006/007/011`, `GOLD-BUS-EXTRA-01`–`04`, `GOLD-CO-EXTRA-02/03`

**Actual Core failure list after master seed:** pending live run (Docker required).

## Acceptance commands (executed)

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\compile_local_master_seed.py          # PASS
python qa\telegram_golden\run_telegram_golden.py --offline      # PASS
python -m pytest backend\platform-api\tests\test_telegram_golden*.py -q  # 30 passed
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
# NOT_RUN — Docker daemon not available
```

## Next step (Core owner)

When Docker is up:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
```

Compare new `qa/telegram_golden/reports/GOLDEN_HTTP_REPORT_*.md` phases:

- `telegram_golden_p0`
- `telegram_golden_full_positive`
- `telegram_golden_negative`

Failures in that report = **actual Core defects** (do not weaken golden expectations).
