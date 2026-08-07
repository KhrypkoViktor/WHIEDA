# WHIEDA Platform Core API

FastAPI modular monolith for tenant-aware public ref, leads, structured advisor and Telegram transport.

## Local dev

```bash
cd backend/platform-api
pip install -e ".[dev]"
export PLATFORM_DATABASE_URL=postgresql://...
export PLATFORM_DEFAULT_HOST_TENANT=whieda   # dev only
uvicorn app.main:app --reload --port 8080
```

## Tests

```bash
python -m pytest tests -q
```

## SQL migrations (staging first)

1. `postgres/sql/platform_tenant_registry_v1.sql`
2. `postgres/sql/platform_tenant_rls_v1.sql`
3. `postgres/sql/platform_api_session_context_v1.sql`

## Deploy

See `backend/deploy/core/` — docker-compose, `.env.example`, `set_core_route.py`, `ROLLBACK_RUNBOOK.md`.

Handoff: `WHIEDA_PLATFORM_SCALE_DEVELOPER_PLAN_V1_2026-08-02.md`
