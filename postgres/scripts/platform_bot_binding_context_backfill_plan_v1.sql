-- SQL-only backfill PLAN. Do not apply from apply_staging_platform_all.ps1,
-- run_local_staging_proof.py, shared staging, production, n8n, or setWebhook.
--
-- Apply only after:
--   1. platform_bot_binding_context_v1.sql is already on the target DB
--   2. NSP token/secret values exist as env:NAME outside git/DB
--   3. explicit owner command for that environment
--
-- Goal: create nsp-maxim as draft with a disabled core binding. Never copy
-- WHIEDA token refs onto an active NSP binding. Never attach NSP to tenant_id=whieda.

begin;

insert into tenants (tenant_id, display_name, status, default_locale, default_country)
values ('nsp-maxim', 'NSP Maxim', 'draft', 'ru', null)
on conflict (tenant_id) do update
set display_name = excluded.display_name,
    status = case
      when tenants.status = 'active' then tenants.status
      else 'draft'
    end,
    updated_at = now();

insert into tenant_bot_bindings (
  binding_id,
  tenant_id,
  webhook_secret_ref,
  bot_token_ref,
  bot_username,
  processing_mode,
  status
)
values (
  'nsp-maxim-advisor-bot',
  'nsp-maxim',
  'env:NSP_TELEGRAM_WEBHOOK_SECRET',
  'env:NSP_TELEGRAM_BOT_TOKEN',
  'NSP_Leader_bot',
  'core',
  'disabled'
)
on conflict (binding_id) do update
set tenant_id = 'nsp-maxim',
    webhook_secret_ref = excluded.webhook_secret_ref,
    bot_token_ref = excluded.bot_token_ref,
    bot_username = excluded.bot_username,
    processing_mode = 'core',
    status = case
      when tenant_bot_bindings.status = 'active' then tenant_bot_bindings.status
      else 'disabled'
    end,
    updated_at = now();

-- Guard: this binding must not land on WHIEDA.
do $$
begin
  if exists (
    select 1
    from tenant_bot_bindings
    where binding_id = 'nsp-maxim-advisor-bot'
      and tenant_id = 'whieda'
  ) then
    raise exception 'nsp-maxim-advisor-bot must not attach to whieda';
  end if;
end
$$;

commit;
