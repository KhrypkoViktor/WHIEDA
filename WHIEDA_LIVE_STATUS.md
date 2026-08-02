# WHIEDA Live Status

Updated: 2026-08-02 (Partner Pilot P0, gate fixes)

## Current focus
Partner pilot backend/Telegram. RAG/distillate frozen in staging only.

## Release gate
```powershell
$env:WHIEDA_N8N_EMAIL="..."
$env:WHIEDA_N8N_PASSWORD="..."
$env:WHIEDA_SSH_PASSWORD="..."   # or NORDMAN_LIVE_SSH_PASSWORD
$env:PGPASSWORD="..."          # or fetched via fetch_pg_password_from_n8n.py
python n8n/current/run_whieda_release_gate_2026-08-02.py
python n8n/current/run_whieda_release_gate_2026-08-02.py --require-consecutive 2
```

Gate now includes: TEMP=0, canary, unified smoke, lead/ref pilot smoke, P0≥30, regression≥98%.

## External canary
`python n8n/current/whieda_external_canary_2026-08-02.py`

Checks: `/healthz`, public advisor query, public ref API (`200`/`404`), Telegram SQL actor.

## P0 incident (2026-08-01)
- Root cause: website structured lookup used Telegram-only envelope node → empty site answers.
- Secondary: TEMP workflow sprawl overloaded n8n.
- Report: `n8n/live-exports/2026-08-02/WHIEDA_site_bot_incident_2026-08-02.json`

## Security changes (P0)
- Public partner-admin webhook removed; partner edits via `Partners_Ref` sync only.
- Smoke/ops use direct Supabase reads (`whieda_runtime_read.py`), no TEMP workflows.
- Credentials via `WHIEDA_*` env vars (`whieda_runtime_env.py`).

## Pilot tracks
| Track | Script / contract | Status |
|-------|-------------------|--------|
| Ref/lead loop | `whieda_lead_ref_pilot_smoke_2026-08-02.py` | in progress |
| Structure Basic | `run_whieda_sql_quality_daily_report_2026-08-02.py` | in progress |
| Telegram menus | `publish_telegram_role_menu_v1_2026-08-02.py` | staged |
| Pilot metrics | `run_whieda_pilot_metrics_2026-08-02.py` | ready |

## Public API contracts (site developer)
- `GET /webhook/whieda-public-ref-v1?ref={code}` — public fields only
- `POST /webhook/wwc-advisor-public-v1` — advisor query
- `POST /webhook/whieda-advisor-api-v1` — contract API

## Do not publish
- Distillate importer to runtime (`--no-publish` only)
- RAG vector search
- `whieda-partner-profile-admin-v1` admin webhook
