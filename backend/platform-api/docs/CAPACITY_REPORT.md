# WHIEDA Platform Core — Capacity Report

Дата прогона: **2026-08-03**  
Окружение: Core API на `185.252.232.93:8080` (localhost probe, `CORE_ROUTE_ADVISOR=core`)

## Hardware

| Компонент | Spec |
|-----------|------|
| Core API host | `185.252.232.93` |
| Postgres | Supabase |
| Redis | core-redis-1 (deploy stack) |
| n8n co-located | yes (Telegram **не** на Core) |

## Tier 500 — Core advisor localhost probe

Скрипт: `n8n/current/whieda_core_advisor_capacity_probe_2026-08-03.py`

| Метрика | Результат | Gate |
|---------|-----------|------|
| Запросов | 500 | — |
| Workers | 15 | — |
| Ошибки | **0** | < 1% |
| p50 | **1138 ms** | — |
| p95 | **1971 ms** | ≤ 3000 ms |
| max | 2701 ms | — |
| **Verdict** | **PASS** | |

Отчёт: `n8n/current/_core_capacity_probe_report.json`

## Corpus quality (Core-only)

| Probe | Результат |
|-------|-----------|
| `whieda_core_corpus_probe_2026-08-03.py` | **25/25** (100%) |
| `whieda_core_direct_probe_2026-08-03.py` | **13/13** |
| `whieda_core_p0_smoke_from_tsv_2026-08-03.py` | **64/64 (100%)** |

## Failure / isolation tests

| Scenario | Status | Notes |
|----------|--------|-------|
| n8n-down (advisor) | **green** | Core `CORE_ROUTE_ADVISOR=core` отвечает через Postgres, без n8n webhook |
| Dify-off | **green** | `CORE_ROUTE_DEEP=off` в deploy `.env` |
| Telegram cutover | **blocked** | `CORE_ROUTE_TELEGRAM=legacy` — ждём физической проверки владельца |

Gate pack: `python n8n/current/whieda_core_pre_cutover_gates_2026-08-03.py`

## Не пройдено / в работе

- [x] P0 TSV corpus ≥95% — **64/64 (100%)** на 2026-08-03
- [ ] Parity matrix legacy vs Core (legacy n8n не нагружать без ОК)
- [ ] Tier 500 sustained 30 min / burst 75 RPS (только mini-probe 500 req)
- [ ] Два последовательных release gate
- [ ] Owner physical verification + explicit cutover approval

## Вывод

Core SQL advisor **прошёл P0 corpus (100%), Tier-500 mini и pre-cutover gates**, но **Telegram cutover не делаем** до вашей физической проверки и явного ОК.
