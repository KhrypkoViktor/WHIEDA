-- Multi-tenant Telegram outbound identity and processing mode.
-- Additive only. Secret values remain outside Postgres; refs use env:NAME.

begin;

alter table tenant_bot_bindings
  add column if not exists bot_token_ref text,
  add column if not exists bot_username text,
  add column if not exists processing_mode text not null default 'core';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'tenant_bot_bindings_processing_mode_check'
  ) then
    alter table tenant_bot_bindings
      add constraint tenant_bot_bindings_processing_mode_check
      check (processing_mode in ('core', 'shadow', 'legacy'));
  end if;
end
$$;

update tenant_bot_bindings
set bot_token_ref = 'env:PLATFORM_TELEGRAM_BOT_TOKEN',
    webhook_secret_ref = 'env:PLATFORM_TELEGRAM_WEBHOOK_SECRET',
    bot_username = 'WHIEDA_Advisor_bot',
    processing_mode = 'core',
    updated_at = now()
where binding_id = 'whieda-advisor-bot'
  and tenant_id = 'whieda';

update tenant_bot_bindings
set bot_token_ref = 'env:PLATFORM_TELEGRAM_BOT_TOKEN',
    webhook_secret_ref = 'env:PLATFORM_TELEGRAM_WEBHOOK_SECRET',
    bot_username = 'wwc_admin_staging_bot',
    processing_mode = 'core',
    updated_at = now()
where binding_id = 'wwc-cabinet-staging-bot'
  and tenant_id = 'whieda';

commit;
