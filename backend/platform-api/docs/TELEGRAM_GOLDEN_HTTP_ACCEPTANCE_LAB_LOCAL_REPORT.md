# Telegram Golden HTTP Acceptance Lab — Local Report

Date: 2026-08-12  
Task: `WHIEDA_DROVOSEK_GOLDEN_HTTP_ACCEPTANCE_LAB_TASK_V1_2026-08-12.md`  
Scope: `qa/telegram_golden/**` + lab tests + local Core hook (no `app/**`, n8n, postgres runtime)

## Summary

Local HTTP runner implemented: golden JSONL → `POST /v1/advisor/query` on localhost-only target → assertions (mode, text, media, context, latency, safety) → JSON/MD report + baseline diff.

| Gate | Result |
|------|--------|
| Offline corpus lint | **PASS** (187 cases, 35 flows, 5 negative fixtures) |
| Mocked HTTP lab tests | **PASS** (20/20) |
| `--check-target` (standalone, Core down) | **FAIL** — connection refused |
| E2E `--telegram-golden` (Docker Core up) | **FAIL** at P0 smoke — 38/107 executable cases failed |
| Full corpus + negative (E2E) | **NOT_RUN** — blocked after P0 fail (by design) |

Baseline not written (`--accept-baseline` gate not met).

## What was added

| Component | Path |
|-----------|------|
| Target contract | `golden_target.example.json`, `lab/target.py` |
| HTTP runner | `lab/http_runner.py`, `lab/http_assertions.py` |
| Reports / baseline | `lab/report.py`, `lab/baseline.py`, `compare_golden_runs.py` |
| CLI | `run_telegram_golden.py --live`, `--check-target`, `--dry-run`, `--include-negative`, `--accept-baseline`, `--priority` |
| E2E hook | `run_local_core_lab.py --e2e --telegram-golden` |
| Tests | `tests/test_telegram_golden_http.py` (10 scenarios) |

## Safety gate

- Allowed hosts: `127.0.0.1`, `localhost`, `host.docker.internal`
- HTTPS / production IP → fail before HTTP
- Reports: `answer_preview` ≤120 chars; no credentials

## Acceptance commands

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\run_telegram_golden.py --offline          # PASS
python qa\telegram_golden\run_telegram_golden.py --dry-run            # 187 cases, 35 flows, zero HTTP
python -m pytest backend\platform-api\tests\test_telegram_golden_corpus.py backend\platform-api\tests\test_telegram_golden_http.py -q  # 20 passed

python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden
# overall FAIL (telegram_golden P0); see below
```

## E2E run (20260812T132043Z)

Hook sequence executed: Docker staging proof → local Core → acceptance smoke → `--check-target` **PASS** → P0 `--live --priority P0` **FAIL**.

Report: `qa/telegram_golden/reports/GOLDEN_HTTP_REPORT_20260812T132043Z-a3eb6744.{json,md}` (gitignored)

| Metric | Value |
|--------|-------|
| Executed | 107 (P0 subset) |
| Pass | 69 |
| Fail | 38 |
| Skip surface | 9 (`navigation_catalog`) |
| Timeout | 0 |
| Latency p50 / p95 / max | 48 / 73 / 105 ms |

### Failure clusters (Core defects — not fixed in this task)

1. **Snapshot cards → clarification** — many P0 cards (Палантин, Стельки, Линчжи, Лювэй, Очки, …) return `clarification` instead of `structured_card`; photo missing.
2. **Ambiguous activator** — `GOLD-SMOKE-P0-001`: «активатор» → clarification, expected card.
3. **Price routing** — `GOLD-SMOKE-P0-005`, conversation price turns: FAQ/business instead of `structured_price`; missing BYN/PV.
4. **Media follow-ups** — photo/video/certificate turns in flows F02–F04, F09, F11: clarification instead of structured media modes.
5. **Safety phrasing** — `GOLD-SMOKE-SAFE-001/002`: expected disclaimer text not matched.
6. **No-blind-zone** — `GOLD-NBZ-*`: knowledge_gap answers missing «каталог»; `NBZ-P0-006` wrong `gap_kind`.
7. **Service-intent fuzz** — `GOLD-FUZZ-006/007/011`: business replies missing «WHIEDA».
8. **Business extras** — `GOLD-BUS-EXTRA-*`, `GOLD-CO-EXTRA-*`: clarification or wrong mode vs expected FAQ/business.

Full list of 38 failed `case_id` values is in the report MD failures section.

### Next steps for Core owners

Run manually after Core fixes:

```powershell
python qa\telegram_golden\run_telegram_golden.py --live --priority P0
python qa\telegram_golden\run_telegram_golden.py --live --include-negative
python qa\telegram_golden\run_telegram_golden.py --live --include-negative --accept-baseline
```

Do **not** weaken golden expectations to hide these failures.

## NOT DONE (by design)

- No changes to `app/**`, n8n, postgres production
- No production / Telegram API calls
- Core repair out of scope for Дровосек HTTP lab task
