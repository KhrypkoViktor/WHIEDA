-- Staging-only Telegram binding for WWC Owner Cabinet (@wwc_admin_staging_bot).
-- Production advisor bot binding (whieda-advisor-bot) is NOT modified.

begin;

insert into tenant_bot_bindings (
  binding_id,
  tenant_id,
  bot_token_ref,
  webhook_secret_ref,
  bot_username,
  processing_mode,
  status
)
values (
  'wwc-cabinet-staging-bot',
  'whieda',
  'env:PLATFORM_TELEGRAM_BOT_TOKEN',
  'env:PLATFORM_TELEGRAM_WEBHOOK_SECRET',
  'wwc_admin_staging_bot',
  'core',
  'active'
)
on conflict (binding_id) do update
set tenant_id = excluded.tenant_id,
    status = excluded.status,
    bot_token_ref = excluded.bot_token_ref,
    webhook_secret_ref = excluded.webhook_secret_ref,
    bot_username = excluded.bot_username,
    processing_mode = excluded.processing_mode;

commit;
