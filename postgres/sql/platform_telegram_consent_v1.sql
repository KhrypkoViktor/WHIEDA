-- WHIEDA Platform — Telegram privacy-policy consent V1 (152-ФЗ, ст. 9 ч. 1: факт согласия должен быть доказуем)
-- Status: local DDL; apply to staging first, production only in a release window.
-- Writes: app/telegram/consent.py (one row per user and policy version on /start).

begin;

create table if not exists telegram_consents (
  tenant_id text not null,
  telegram_user_id bigint not null,
  telegram_chat_id bigint not null,
  policy_version text not null,
  consented_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id, policy_version)
);

alter table if exists telegram_consents enable row level security;

drop policy if exists telegram_consents_tenant on telegram_consents;
create policy telegram_consents_tenant on telegram_consents
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
