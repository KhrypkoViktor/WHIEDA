# WHIEDA Telegram Live Cutover Checklist V1

**Date:** 2026-08-14  
**Based on:** `WHIEDA_TELEGRAM_LIVE_PATH_AUDIT_2026-08-14.md`  
**Status:** Core-direct Telegram ingress is **already live**. This checklist is for **confirmation, UX validation, and safe legacy parking** — not a first-time cutover.

**Do not execute steps marked OWNER without explicit owner approval.**

---

## 0. Pre-flight (read-only — done in audit)

- [x] Telegram webhook → Core `/v1/telegram/whieda-advisor-bot/webhook`
- [x] `CORE_ROUTE_TELEGRAM=core` in running container
- [x] `CORE_ROUTE_ADVISOR=core` in running container
- [x] Structured sync healthy; products/aliases/cards match master seed counts
- [x] Core HTTP 9-phrase audit session passes
- [ ] Owner visual Telegram check (keyboard, photo, callbacks) — **pending**

---

## 1. Backup points (before any future change)

| Asset | Location / command | Notes |
|-------|-------------------|-------|
| Core advisor slice | `/opt/whieda-platform-core/backups/telegram-ux-slice-20260813T143540Z.tar.gz` | Latest known good UX patch |
| Core advisor slice (prior) | `/opt/whieda-platform-core/backups/advisor-slice-20260812T100243Z.tar.gz` | Recovery baseline |
| Server `.env` | `/opt/whieda-platform-core/src/deploy/core/.env` | Copy before route/image change |
| n8n workflow | SSH export `advisor-whieda-phase1` → `n8n/backups/` | Before deactivating legacy tail |
| Telegram webhook state | `python n8n/current/whieda_telegram_webhook_readonly_status.py` | Record URL + pending count |

---

## 2. Target configuration (steady state)

Single route truth after any remediation:

| Variable | Target value |
|----------|--------------|
| `CORE_ROUTE_PUBLIC_REF` | `core` |
| `CORE_ROUTE_LEADS` | `core` |
| `CORE_ROUTE_ADVISOR` | `core` |
| `CORE_ROUTE_TELEGRAM` | `core` |
| `CORE_ROUTE_DEEP` | `off` |

Telegram webhook URL (only one):

```text
https://sysarchn8n.duckdns.org/whieda-platform/v1/telegram/whieda-advisor-bot/webhook
```

Legacy paths **must not** receive Telegram updates:

- `https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0` — rollback only
- n8n `advisor-whieda-phase1` — park/deactivate after backup

---

## 3. Deploy input (if Core code refresh needed)

| Item | Value |
|------|-------|
| Host | `185.252.232.93` (`whieda-n8n`) |
| Deploy dir | `/opt/whieda-platform-core/src/deploy/core` |
| Script | `python n8n/current/deploy_platform_core_2026-08-02.py` |
| Image | `core-api` / `core-worker` (local build on server) |
| Recreate | `docker compose up -d --force-recreate api worker` |
| OpenAPI version observed | `0.1.0` |
| Post-deploy health | `curl localhost:8080/health/live` + `/health/ready` → 200 |

Use `--dry-run` first. Do **not** change routes during code-only deploy.

---

## 4. Ten-message Telegram canary (OWNER — real chat)

Run in **owner-approved test chat** only. Wait 3–4s between messages. One session.

| # | Message | Expected |
|---|---------|----------|
| 1 | `хай` | Greeting, no product card, no stray videos |
| 2 | `че ты можешь?` | Capability / menu text |
| 3 | `какие есть товары?` | Catalog guidance + inline/menu actions |
| 4 | `активаor` | Base Activator card M015-00, photo-first if data exists |
| 5 | `активатор` | Clarification обычный vs PRO |
| 6 | `обычный` | Base Activator card in same session |
| 7 | `активатор pro` | PRO card EU-N000031-25 |
| 8 | `сравни активатор и pro` | Comparison with readable emphasis |
| 9 | `фото pro` | Photo for PRO SKU |
| 10 | `сколько стоит?` | Price for last product (PRO) |

**Also click one inline catalog button** (e.g. «📦 Товары») and confirm callback reply — HTTP audit cannot prove this.

Automated ingress probe (signed webhook, no Telegram history read):

```bash
python n8n/current/run_whieda_direct_core_telegram_canary_2026-08-13.py
```

---

## 5. Rollback path

Only if canary fails or duplicate delivery confirmed.

### 5a. Telegram webhook → legacy n8n

```bash
python n8n/current/rollback_telegram_legacy_2026-08-03.py
```

Sets webhook to `https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0` and `CORE_ROUTE_TELEGRAM=legacy`.

### 5b. Restore Core routes from backup `.env`

```bash
# On server — restore .env from backup, then:
cd /opt/whieda-platform-core/src/deploy/core
docker compose up -d --force-recreate api worker
```

### 5c. Restore Core code from tarball

```bash
# Example — adjust path to chosen backup
tar -xzf /opt/whieda-platform-core/backups/telegram-ux-slice-20260813T143540Z.tar.gz -C /opt/whieda-platform-core/src
cd /opt/whieda-platform-core/src/deploy/core
docker compose up -d --force-recreate api worker
```

Verify rollback:

```bash
python n8n/current/whieda_telegram_webhook_readonly_status.py
python n8n/current/whieda_assert_production_routing_2026-08-04.py
```

---

## 6. Success criteria

- [ ] Exactly **one** Telegram reply per user message (no duplicates within 10s)
- [ ] Service intents: greeting, capabilities, catalog entry work without false «товар не найден»
- [ ] Product choice preserves context (`обычный` / `PRO` / price follow-up)
- [ ] Cards are **photo-first** where structured resources exist
- [ ] Comparison emphasis renders in Telegram (`<b>` visible, not raw tags)
- [ ] No `telegram_core_processor_failed` / `advisor_gap_write_failed` in Core logs during canary window
- [ ] Structured sync remains healthy (< 30 min age)

---

## 7. Recommended follow-ups (non-executed)

1. **Park legacy tail:** `python n8n/current/park_legacy_telegram_tail_2026-08-09.py` after fresh n8n backup — reduces duplicate/rollback confusion.
2. **Update ops constants:** align `whieda_core_route_ops.PRODUCTION_ROUTES` with live `core/core/core/core/off`.
3. **Repo doc sync:** reconcile older sections of `WHIEDA_LIVE_STATUS.md` that still mention shadow/legacy advisor.
4. **Golden live parity run:** re-run `--telegram-golden --golden-master-seed` E2E against staging Core on server if Docker lab unavailable locally.

---

**Audit reference:** `WHIEDA_TELEGRAM_LIVE_PATH_AUDIT_2026-08-14.json`  
**No production change was made while producing this checklist.**
