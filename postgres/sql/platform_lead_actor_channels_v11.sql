-- WHIEDA Platform — каналы связи с человеком V11 (20.09.2026, владелец: «подключай Max —
-- будем готовить подключения для партнёров; в итоге любой мессенджер на выбор»).
-- Status: local DDL; apply to staging first, production only in a release window.
--
-- Человек (lead_actors.actor_id) один, каналов у него несколько: telegram, max, позже vk /
-- whatsapp. Колонки lead_actors.telegram_* остаются (Telegram-код их читает), Max и
-- следующие каналы живут только здесь. Writes: app/max/processor.py, app/leads/channels.py.

begin;

create table if not exists lead_actor_channels (
  tenant_id text not null,
  actor_id text not null references lead_actors(actor_id) on delete cascade,
  channel text not null check (channel in ('telegram', 'max', 'vk', 'whatsapp')),
  channel_user_id text not null,
  channel_chat_id text,
  username text,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, channel, channel_user_id)
);

create index if not exists lead_actor_channels_actor_idx on lead_actor_channels (tenant_id, actor_id);

alter table if exists lead_actor_channels enable row level security;

drop policy if exists lead_actor_channels_tenant on lead_actor_channels;
create policy lead_actor_channels_tenant on lead_actor_channels
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Источник атрибуции по каналу: <channel>_deeplink.
alter table partner_referral_attributions drop constraint if exists partner_referral_attributions_source_check;
alter table partner_referral_attributions
  add constraint partner_referral_attributions_source_check
  check (source in ('telegram_deeplink', 'max_deeplink', 'vk_deeplink', 'whatsapp_deeplink', 'admin_manual'));

-- Telegram-привязки, которые уже есть, отражаем в канальной таблице (без потери старых колонок).
insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, channel_chat_id, username, display_name)
select la.tenant_id, la.actor_id, 'telegram', la.telegram_user_id::text, la.telegram_chat_id, la.telegram_username, la.display_name
from lead_actors la
where la.telegram_user_id is not null
on conflict (tenant_id, channel, channel_user_id) do nothing;

commit;
