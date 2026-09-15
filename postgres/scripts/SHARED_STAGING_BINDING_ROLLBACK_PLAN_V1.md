# Shared staging binding rollback plan (manual)

Gate B2 does **not** run rollback. There is no `--rollback` switch and no
automatic DROP/ALTER. An owner runs these steps by hand, if at all.

Target: `whieda_platform_staging` only. Not production, not Supabase, not
`127.0.0.1`, not `whieda_platform_staging_verify_*`.

## If apply never ran

1. Stop. Leave the database untouched.
2. Keep `platform_bot_binding_context_v1.sql` in APPLY_ORDER for the next
   owner-run. Do not invent a reverse migration.

## If apply started and failed mid-file

1. Record the last successful filename from the apply log.
2. Do not re-run earlier files unless they are written to be idempotent
   (`IF NOT EXISTS` / `ON CONFLICT`). The 13-file order already is.
3. Do not drop `tenant_bot_bindings`. Additive columns stay.

## If binding-context SQL applied and WHIEDA looks wrong

Inspect only:

```sql
SELECT binding_id, tenant_id, status, processing_mode, bot_username
FROM tenant_bot_bindings
ORDER BY binding_id;
```

Expected:

- `whieda-advisor-bot` → `tenant_id=whieda`, `status=active`
- no `nsp-*` row with `tenant_id=whieda`
- `nsp-maxim` binding absent, or `status=disabled` and `tenant_id=nsp-maxim`

If an NSP row is `active` or attached to `whieda`, disable it. Do not delete
WHIEDA rows to “undo” NSP:

```sql
UPDATE tenant_bot_bindings
SET status = 'disabled',
    tenant_id = 'nsp-maxim',
    updated_at = now()
WHERE binding_id = 'nsp-maxim-advisor-bot'
  AND tenant_id <> 'whieda';
```

If that row sits on `whieda`, stop and escalate. Do not UPDATE it onto WHIEDA
and do not DELETE `whieda-advisor-bot`.

## What this plan never does

- DROP COLUMN `bot_token_ref` / `bot_username` / `processing_mode`
- DROP TABLE `tenant_bot_bindings`
- `setWebhook`, n8n publish, secret rotation
- automatic execution from `run_shared_staging_release.py`
