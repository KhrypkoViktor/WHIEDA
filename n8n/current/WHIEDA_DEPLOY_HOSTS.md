# WHIEDA deploy hosts

| Alias | URL / host | Use |
|---|---|---|
| `whieda-n8n` | `185.252.232.93` (SSH `root`) | n8n Docker, `publish:workflow`, restart |
| n8n API | `https://sysarchn8n.duckdns.org` | REST + webhooks |
| Site | `173.249.45.83` | nginx `/var/www/mlm-sysarch` (static Astro; read-only for backend dev) |

SSH: `ssh whieda-n8n` (password in publish scripts until env migration).

## n8n ops (2026-08-01)

**Symptom:** `sysarchn8n.duckdns.org` timeout, nginx `499`, local `:5678/healthz` no response while container is `Up`.

**Root cause:** dozens of leftover `TEMP*` workflows were `active=true` in n8n Postgres. Startup/workload activation pegged CPU (~200%+) and blocked the HTTP event loop.

**Fix on server:**
```bash
docker exec n8n-postgres-1 psql -U n8n -d n8n -c \
  "UPDATE workflow_entity SET active=false WHERE name ILIKE 'TEMP%';"
docker restart n8n-n8n-1
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5678/healthz
```

**Expected active workflows (8):** `advisor-whieda-phase1`, structured sync cron, broadcast worker, meeting reminder, website leads, public advisor, FAQ import, Advisor 1.0.

**Prevention:** publish scripts create `TEMP*` workflows with `active=false`; if n8n becomes slow again, deactivate `TEMP%` before restart.
