"""Human rollback plan for shared-staging tenant canary. Never executes SQL."""

from __future__ import annotations


def render_rollback_plan(
    *,
    database: str,
    tenant_id: str,
    package_id: str,
) -> str:
    db = database or "<WHIEDA_SHARED_STAGING_EXPECTED_DB>"
    tenant = tenant_id or "<tenant_id>"
    package = package_id or "<package_id>"
    return f"""# Shared-staging tenant canary rollback plan

This plan is documentation only. It does not connect to a database, does not
run DDL, and does not call Telegram.

Target database name: `{db}`
Tenant: `{tenant}`
Package: `{package}`

## If apply never ran

1. Stop. Leave the database untouched.
2. Keep the 18 APPLY_ORDER files for the next owner-run.
3. Do not invent a reverse migration.

## If apply failed during SQL migrations

1. Read the immutable run record for the last successful filename.
2. Re-run is safe only for idempotent IF NOT EXISTS / ON CONFLICT files.
3. Do not reverse a migration by dropping platform objects.
4. Release the advisory lock if the failed session still holds it:
   SELECT pg_advisory_unlock(hashtext('whieda.shared_staging.tenant_canary'));

## If import transaction failed

The tenant catalog transaction rolled back. Other tenants, including WHIEDA,
must be unchanged. Confirm with a read-only count of advisor_structured_*
rows grouped by client_id against the backup snapshot.

## If import committed and the tenant catalog is wrong

Restore tenant-scoped rows from the timestamped backup directory. Do not
delete or update another tenant. Do not touch whieda-advisor-bot.

Disable the canary binding if it is not already disabled:

```sql
UPDATE tenant_bot_bindings
SET status = 'disabled',
    updated_at = now()
WHERE tenant_id = '{tenant}'
  AND tenant_id <> 'whieda';
```

If a canary row sits on whieda, stop and escalate. Do not delete
whieda-advisor-bot.

## What this plan never does

- drop platform tables or columns
- Telegram webhook, n8n, Sheets, or production Core deploy
- auto-upload media or Google Drive
- enable the tenant binding
- import review-required SKU
"""
