# WHIEDA Telegram Live Path Audit — 2026-08-14

**Task:** `WHIEDA_DROVOSEK_TELEGRAM_LIVE_PATH_RECOVERY_AUDIT_TASK_V1_2026-08-14.md`  
**Mode:** read-only — **no production changes were made**  
**Machine:** Git Bash on Windows, SSH + read-only DSN + n8n REST  
**JSON artifact:** `WHIEDA_TELEGRAM_LIVE_PATH_AUDIT_2026-08-14.json`

---

## Executive answer

**Живой Telegram-бот уже отвечает через Platform Core, а не через legacy n8n SQL-хвост.**

Подтверждённый путь:

```text
Telegram @WHIEDA_Advisor_bot
  → https://sysarchn8n.duckdns.org/whieda-platform/v1/telegram/whieda-advisor-bot/webhook
  → nginx (185.252.232.93) → core-api-1:8080
  → /v1/advisor/query + PostgreSQL structured runtime
  → исходящая доставка через Core → Telegram Bot API
```

Все `CORE_ROUTE_*` в `.env` и в running container: **`core`** (кроме `CORE_ROUTE_DEEP=off`).

Прямые HTTP-пробы Core на 9 фраз из Golden-flow **прошли** (greeting, capabilities, catalog, typo `активаор`, clarification, PRO card, comparison, photo, price в одной сессии). Это согласуется с локальным Golden после master seed.

**Почему может казаться, что бот «старый»:** не потому что ingress всё ещё legacy, а скорее из‑за (a) расхождения Telegram UI/доставки vs HTTP `answer_text`, (b) устаревших ops-констант в репо, (c) активного, но отключённого от webhook workflow `advisor-whieda-phase1`.

---

## A. Route truth (evidence table)

| Hop | Host / service | Endpoint | State | Route mode | Version / identity | Evidence | Confidence |
|-----|----------------|----------|-------|------------|-------------------|----------|------------|
| Telegram webhook | `@WHIEDA_Advisor_bot` | `/whieda-platform/v1/telegram/whieda-advisor-bot/webhook` | active, 0 pending errors | Core direct | bot_id 8159293641 | getWebhookInfo via server-side token read | **confirmed** |
| nginx → Core | `185.252.232.93` / `core-api-1` | localhost:8080 | Up 20h | `CORE_ROUTE_TELEGRAM=core` | container created **2026-08-13T14:36Z** | `docker ps`, `printenv` | **confirmed** |
| Advisor engine | `core-api-1` | `/v1/advisor/query` | live=200, ready=200 | `CORE_ROUTE_ADVISOR=core` | OpenAPI **0.1.0** (with `Host: wwc.best`) | curl health + HTTP probes | **confirmed** |
| Structured runtime | Supabase Postgres | `advisor_structured_*` | sync healthy | structured SQL | last success **2026-08-14T10:00:17Z** | `structured_sync_health_2026-08-10.py` | **confirmed** |
| Outbound Telegram | Core → Bot API | sendMessage / sendPhoto | active (inferred from Core ingress) | core | — | architecture + cutover docs | **confirmed** |
| Legacy n8n Phase 1 | `n8n-n8n-1` | `advisor-whieda-phase1`, `/webhook/whieda-advisor-api-v1` | **active in DB**, not Telegram ingress | legacy | recent executions ~5 min | n8n `workflow_entity`, REST executions | **confirmed** (not user path) |

**Not the live Telegram path:** `https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0` — webhook URL on Telegram API does **not** point here.

---

## B. Runtime and data parity

### Core health / OpenAPI

| Check | Result | Exit |
|-------|--------|------|
| `GET /health/live` (localhost:8080) | 200 | 0 |
| `GET /health/ready` | 200 | 0 |
| `GET /openapi.json` without tenant Host | 404 `tenant_not_found` | 0 |
| `GET /openapi.json` with `Host: wwc.best` | 200, version **0.1.0** | 0 |
| Public n8n health | 200 | 0 |

Git commit on server: **unknown** (`git rev-parse` unavailable in deploy tree; `APP_VERSION` unset). Deploy proxy: container recreated **2026-08-13** (matches telegram-ux-slice backup date in `WHIEDA_LIVE_STATUS.md`).

### Structured sync vs master Golden seed

| Layer | Live runtime | Master seed `20260810T083328Z` | Match |
|-------|-------------|--------------------------------|-------|
| products | 40 | 40 | yes |
| aliases | 118 | 118 | yes |
| product_cards | 23 | 23 | yes |
| resources | 239 rows | 176 accepted (active) | partial — live table includes inactive rows |

Sync status: **healthy**, age ~8 min at audit time. Last failure: 2026-08-10 (SQL syntax in sync job).

### Key alias presence (live DB)

| Alias | SKU | Present |
|-------|-----|---------|
| активатор | M015-00 | yes |
| активатор pro | EU-N000031-25 | yes |
| активатор про | EU-N000031-25 | yes |
| активаор | M015-00 | yes |
| ативатор | M015-00 | yes |

### Duplicate delivery path

| Finding | Level |
|---------|-------|
| Single Telegram webhook URL (Core) | **confirmed** |
| `advisor-whieda-phase1` still `active=true` in n8n | **confirmed** |
| Dual reply on current ingress | **not confirmed** |
| Operational rollback / confusion risk | **inferred** |

---

## C. Safe direct probes (audit session `drovo-audit-20260814T100712Z`)

No Telegram Bot API calls. Isolated HTTP sessions only.

### Core public (`/whieda-platform/v1/advisor/query`)

| Input | Status | Mode | SKU | Preview (redacted) |
|-------|--------|------|-----|-------------------|
| хай | 200 | structured_business | — | Привет! Я советник WHIEDA… |
| че ты можеь? | 200 | structured_business | — | Я могу помочь с WHIEDA: 📦 Товары… |
| какие есть товары? | 200 | structured_business | — | В каталоге WHIEDA есть приборы… |
| активаor | 200 | structured_card | M015-00 | Активатор клеток… (+ photo) |
| активатор | 200 | clarification | — | Вы про Активатор клеток или PRO?… |
| активатор pro | 200 | structured_card | EU-N000031-25 | Активатор клеток PRO (комплект)… |
| сравни активатор и pro | 200 | structured_comparison_layer | M015-00 | **Активатор клеток или PRO?**… |
| фото pro | 200 | structured_photo | EU-N000031-25 | Отправляю фото: PRO… |
| сколько стоит? | 200 | structured_price | EU-N000031-25 | 2275 BYN retail… |

All Core probes: **9/9 OK**. Latency p50 ~2.5s.

### Legacy n8n (`/webhook/whieda-advisor-api-v1`)

All 9 inputs: **HTTP 200, empty body** (0 bytes). This endpoint ACKs asynchronously; it is **not** a synchronous advisor surface and cannot be compared turn-by-turn to Core HTTP.

---

## D. Likely root causes (ranked)

1. **Live ingress is already Core** — local Golden and live Core HTTP behave consistently; the «old bot» hypothesis is **not supported** at the routing layer.
2. **Telegram rendering gap** — HTTP passes while user may still see old UX on callbacks, keyboard, or photo ordering; needs real-device canary, not HTTP alone.
3. **Stale repo ops rails** — `whieda_core_route_ops.PRODUCTION_ROUTES` still says `TELEGRAM=legacy`, `ADVISOR=shadow`; live server is all `core`.
4. **Legacy workflow still active** — `advisor-whieda-phase1` creates rollback/confusion risk though not current ingress.

---

## Unknowns

- Exact git SHA inside `core-api` image
- Purpose of periodic `advisor-whieda-phase1` executions (every ~5 min) — not proven Telegram-related
- Owner visual proof of Telegram UI after 2026-08-13 UX slice

---

## Commands executed (summary)

All via Git Bash, read-only:

- `python .tmp/drovo_sek_telegram_live_path_audit_2026-08-14.py` → exit **0**
- `python .tmp/audit_supplement_2026-08-14.py` → exit **0**
- `python .tmp/audit_n8n_pg_2026-08-14.py` → exit **0**
- `python n8n/current/structured_sync_health_2026-08-10.py` → exit **0**
- SSH: docker ps, route grep, health curls, n8n postgres SELECT (workflow_entity)

Credentials, tokens, webhook secrets, and DSNs were **not** copied into this report.

---

## Cutover implication

**Cutover to Core for Telegram appears already done** (2026-08-09 initial, 2026-08-12/13 recovery patches). Next step is not «switch webhook to Core» but **confirm Telegram UX** and **park legacy tail** — see `backend/platform-api/docs/WHIEDA_TELEGRAM_LIVE_CUTOVER_CHECKLIST_V1.md`.

**No production change was made during this audit.**
