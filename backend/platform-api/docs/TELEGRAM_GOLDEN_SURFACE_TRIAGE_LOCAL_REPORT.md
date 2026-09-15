# Telegram Golden Surface Contract and Triage — Local Report

Date: 2026-08-13  
Task: `WHIEDA_DROVOSEK_GOLDEN_SURFACE_AND_TRIAGE_TASK_V1_2026-08-13.md`  
Input run: `20260813T143036Z-b706fbfa` (master-parity full positive)

## Summary

Golden acceptance now separates **execution surface**, **failure taxonomy**, and **owner-pending policy** from raw HTTP PASS/FAIL. No Core or expected changes were made to force green status.

| Gate | Result |
|------|--------|
| Surface contract on corpus | **PASS** — 188 cases / 35 flows tagged |
| Offline lint + registries | **PASS** |
| `test_telegram_golden*.py` | **PASS** (45/45) |
| Triage from latest full E2E | **PASS** — report generated |
| Live E2E re-run (Git Bash) | **NOT_RUN** this session — triage built from existing HTTP report |

## Surface contract

Distribution after `apply_surface_contract.py`:

| Surface | Cases |
|---------|------:|
| `advisor_http` | 175 |
| `telegram_callback` | 9 |
| `telegram_text` | 4 |

Rules enforced in HTTP lab:

- Non-`advisor_http` cases → `SKIP_SURFACE` **without** HTTP call
- `navigation_catalog` legacy mode → `SKIP_SURFACE`
- Mock processors: `lab/surface_processors_mock.py` (text labels + callbacks)

## Triage artifacts

| File | Purpose |
|------|---------|
| `telegram_golden_policy_decisions_v1.json` | 7 `pending_owner` policies |
| `telegram_golden_triage_classifications_v1.json` | Manual classification for 14 input mismatches |
| `reports/GOLDEN_TRIAGE_20260813T143036Z-b706fbfa.json` | Machine triage |
| `reports/GOLDEN_TRIAGE_20260813T143036Z-b706fbfa.md` | Human triage |

Baseline gate: `--accept-triage-baseline` (refuses P0 `core_bug`, failed negative safety, pending policy marked PASS).

## Classification totals (from input run)

| Classification | Count |
|----------------|------:|
| `core_bug` | 5 |
| `policy_decision_required` | 7 |
| `surface_mismatch` | 2 |

## Core bug backlog only (repro via local HTTP)

1. **GOLD-NBZ-NBZ-P0-006** — clarification expected, got `knowledge_gap`  
   `python qa/telegram_golden/run_telegram_golden.py --live --case-id GOLD-NBZ-NBZ-P0-006`

2. **GOLD-NBZ-NBZ-P1-022** — `knowledge_gap` expected, got clarification  
   `python qa/telegram_golden/run_telegram_golden.py --live --case-id GOLD-NBZ-NBZ-P1-022`

3. **GOLD-NBZ-NBZ-P1-023** — `knowledge_gap` expected, got clarification  
   `python qa/telegram_golden/run_telegram_golden.py --live --case-id GOLD-NBZ-NBZ-P1-023`

4. **GOLD-BUS-EXTRA-03** — FAQ answer missing «Step»  
   `python qa/telegram_golden/run_telegram_golden.py --live --case-id GOLD-BUS-EXTRA-03`

5. **GOLD-SMOKE-FLOW-ctx-pro-T3** — context SKU `LOCAL-PRO` vs expected `EU-N000031-25`  
   `python qa/telegram_golden/run_telegram_golden.py --live --case-id GOLD-SMOKE-FLOW-ctx-pro-T3`

## Pending owner decisions (not PASS, not Core fixes)

| Policy ID | Topic |
|-----------|-------|
| `POL-ACTIVATOR-EXACT` | bare «активатор» → card vs clarification |
| `POL-PARTNER-YO-E` | ё/е equivalence in partner price assertions |
| `POL-HISTORICAL-SKU-CONTEXT` | LOCAL-PRO vs EU-N000031-25 in flow context |
| `POL-PHOTO-CARD-POLICY` | photo allow/required/none on cards |
| `POL-CART-REMOVE-WITHOUT-SESSION` | cart remove without saved session |
| `POL-PV-IN-PRODUCT-QUESTION` | PV question → price vs business FAQ |
| `POL-EVENT-EFIR-WORDING` | must-contain «эфир» vs event copy |

## Negative safety gate

Separate phase: 5/5 `NEGATIVE_PASS` in run `20260813T143048Z-acb846c5` (unchanged).

## Acceptance commands (Git Bash)

```bash
cd /d/Projects/WHIEDA
python qa/telegram_golden/run_telegram_golden.py --offline
python -m pytest backend/platform-api/tests/test_telegram_golden_corpus.py \
  backend/platform-api/tests/test_telegram_golden_http.py \
  backend/platform-api/tests/test_telegram_golden_master_parity.py \
  backend/platform-api/tests/test_telegram_golden_surface_triage.py -q
python backend/platform-api/scripts/run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
python qa/telegram_golden/run_telegram_golden.py --triage-from qa/telegram_golden/reports/GOLDEN_HTTP_REPORT_<run_id>.json
```

## Next step

Re-run E2E after surface tagging so HTTP reports show updated `SKIP_SURFACE` counts for `telegram_text` / `telegram_callback` cases (e.g. `GOLD-BUS-EXTRA-01`, catalog nav). Triage will auto-attach surfaces from corpus.
