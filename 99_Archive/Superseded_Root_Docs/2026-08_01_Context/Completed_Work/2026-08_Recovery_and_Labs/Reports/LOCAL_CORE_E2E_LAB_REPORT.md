# Local Core E2E Lab — Final Report

Date: 2026-08-08  
Branch: `feat/platform-scale-core`  
Agent machine: Windows (Docker CLI **not** on PATH)

## Summary

| Area | Status |
|------|--------|
| Block A — environment doctor | **Done** (code + static tests) |
| Block B — Docker E2E orchestrator (`--e2e`) | **Done** (code + unit tests) |
| Block C — non-mock verifier + tests | **Done** (72 new tests, 366 total pytest pass) |
| Real Docker E2E on this machine | **NOT_RUN** |

---

## Сделано и проверено локально

- `python backend\platform-api\scripts\doctor_local_core_lab.py` — runs read-only checks; on this machine: **FAIL** (no Docker CLI), exit code 1 as designed.
- Full pytest: **366 passed** including 72 new tests under `tests/local_core_e2e/`.
- Doctor static checks: compose files, `.env.local.example` safety, 12 SQL apply-order files + seed, `NOBYPASSRLS` API role script guard, acceptance target config.
- Orchestrator unit tests: mocked Docker flow, health gate, acceptance + verify wiring, report writer, Core stop in `finally`, Postgres container not removed.
- Verifier unit tests: real `urllib.request` code path (no `FakeTransport`), localhost guard, health/OpenAPI/4xx/tenant/advisor JSON checks.

## Проверено только моками

- Full `--e2e` orchestration pipeline (Postgres up → staging proof → ensure DB → Core compose → health → HTTP smoke → acceptance `--check-target` → P0 `--run` → `verify_local_core_e2e`).
- Container log capture on failure (`docker logs --tail 200`).
- P0 acceptance case assertions against live advisor catalog (staging DB has schema + tenant seed but **no** `advisor_structured_*` product tables — P0 may **FAIL** honestly until catalog seed is added; infrastructure still validates HTTP + report creation).
- `check_api_role_not_superuser` via `docker exec psql` (requires running Postgres container).

## Фактический Docker E2E

**NOT_RUN**

Reason: `docker` is not installed / not on PATH on the agent machine (`docker info` → command not found). Doctor reports:

```
[FAIL] docker_installed
[FAIL] docker_daemon
[FAIL] docker_compose
```

No Docker containers were started. No E2E report was generated under `backend/platform-api/reports/local_core_e2e/`.

## Что не запускалось и почему

| Step | Reason |
|------|--------|
| `docker compose up` (Postgres + Core) | Docker Desktop unavailable on agent host |
| Staging SQL apply via `docker exec` | Requires Postgres container |
| `/health/ready` wait against `:8080` | Requires Core container |
| Live P0 acceptance HTTP run | Requires Core + DB |
| `verify_local_core_e2e.py` live run | Requires Core on `127.0.0.1:8080` |

Not touched (per spec): n8n, Telegram, Dify, Google Sheets, Supabase/prod, VPS, site, runtime SQL, product cards.

---

## Owner command (after Docker Desktop install)

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e
```

Optional preflight:

```powershell
python backend\platform-api\scripts\doctor_local_core_lab.py
```

E2E report path (after successful/failed run with Docker):

`backend/platform-api/reports/local_core_e2e/latest_run.md`
