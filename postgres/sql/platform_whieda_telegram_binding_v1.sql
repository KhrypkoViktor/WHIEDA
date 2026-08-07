-- WHIEDA primary Telegram bot binding for Platform Core webhook path.
-- Secret token is configured in Telegram setWebhook + PLATFORM_TELEGRAM_WEBHOOK_SECRET on Core host.

begin;

insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status)
values (
  'whieda-advisor-bot',
  'whieda',
  'env:PLATFORM_TELEGRAM_WEBHOOK_SECRET',
  'active'
)
on conflict (binding_id) do update
set tenant_id = excluded.tenant_id,
    status = excluded.status,
    webhook_secret_ref = excluded.webhook_secret_ref;

commit;
