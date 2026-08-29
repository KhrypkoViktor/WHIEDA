# Core API rollback runbook

## Trigger

- error rate ≥ 1%
- p95 > 3s
- tenant mismatch
- duplicate/lost write
- incorrect owner/ref
- broken audit
- Telegram quality regression (templates instead of SQL answers)

## Steps

1. Identify affected route (`CORE_ROUTE_*`).
2. Set route to `legacy` (ref/leads/advisor/telegram) or `off` (deep).
3. Reload reverse proxy / restore n8n webhook binding.
4. Run one contract canary for the route.
5. Verify last write/audit in Postgres.

## Legacy targets

- public ref: `/webhook/whieda-public-ref-v1`
- leads: `/webhook/wwc-website-lead-v1`
- advisor: `/webhook/wwc-advisor-public-v1`
- telegram: `/webhook/advisor-whieda-v0` (legacy SQL workflow)

## Production rails (до проверки владельца)

Целевое состояние на сервере:

| Route | Value |
|-------|-------|
| `CORE_ROUTE_ADVISOR` | `shadow` (не `core`) |
| `CORE_ROUTE_TELEGRAM` | `legacy` |
| `CORE_ROUTE_DEEP` | `off` |

Проверка и принудительный возврат:

```bash
python n8n/current/whieda_assert_production_routing_2026-08-04.py --restore
```

Probe-скрипты (`*_probe_*.py`, P0 smoke) временно ставят `CORE_ROUTE_ADVISOR=core`
на localhost и **в конце возвращают shadow** (`whieda_core_route_ops.py`).

## Telegram rollback (2026-08-03)

Scope: **only** Telegram transport. Do not change ref/leads/advisor routes.

```powershell
$env:WHIEDA_SSH_PASSWORD='...'
python n8n/current/rollback_telegram_legacy_2026-08-03.py
```

Script actions:

1. Timestamped backup (`n8n/backups/telegram-rollback-before-*.json`)
2. `setWebhook` → `https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0`
3. `CORE_ROUTE_TELEGRAM=legacy` in Core `.env` + recreate api/worker
4. Probe POST to legacy webhook

Post-rollback smoke (after n8n stable):

```powershell
python n8n/current/run_whieda_gentle_recovery_2026-08-02.py
python n8n/current/whieda_telegram_legacy_smoke_2026-08-03.py --timeout 90 --delay 3
```

Success criteria: HTTP 200 on all cases, single Telegram delivery, no duplicate replies.

## Core route command

```bash
python set_core_route.py --env-file .env --core_route_leads legacy
docker compose restart api
```

## Parity before re-cutover

```powershell
python n8n/current/whieda_advisor_parity_matrix_2026-08-03.py --timeout 45 --delay 2
pytest backend/platform-api/tests/failure -q
```

See `WHIEDA_SQL_ADVISOR_PARITY_DEVELOPER_PLAN_V1_2026-08-03.md`.
