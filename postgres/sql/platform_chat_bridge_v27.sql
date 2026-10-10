-- WWC: мост «группа потока в Telegram ↔ группа потока в Max» (V27, 10.10.2026).
--
-- Владелец пишет в Telegram-группе потока — бот повторяет в Max-группе потока;
-- участники пишут в Max — бот переносит в Telegram-группу с подписью «Имя · Max».
-- Куратор (Самцова) уходит в Max, только когда отвечает на сообщение из Max.
-- Пары групп — в коде (app/max/bridge.py); здесь только журнал сообщений.
--
-- chat_bridge_links — какое сообщение Telegram соответствует какому сообщению Max:
--                     по нему ответ «реплаем» в одной группе становится ответом в другой;
-- chat_bridge_inbox — сообщения Max, уже взятые в работу: повтор вебхука Max не дублирует.
-- Из Telegram в Max повтор отсекает channel_crossposts (V24): одна строка на сообщение.

begin;

create table if not exists chat_bridge_links (
  tenant_id text not null references tenants (tenant_id),
  tg_chat_id bigint not null,
  tg_message_id bigint not null,
  max_chat_id bigint not null,
  max_mid text not null,
  direction text not null check (direction in ('tg_to_max', 'max_to_tg')),
  created_at timestamptz not null default now(),
  primary key (tenant_id, tg_chat_id, tg_message_id)
);

create index if not exists chat_bridge_links_max_idx
  on chat_bridge_links (tenant_id, max_chat_id, max_mid);

create table if not exists chat_bridge_inbox (
  tenant_id text not null references tenants (tenant_id),
  max_chat_id bigint not null,
  max_mid text not null,
  status text not null default 'sending' check (status in ('sending', 'sent', 'failed', 'skipped')),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, max_chat_id, max_mid)
);

alter table chat_bridge_links enable row level security;
drop policy if exists chat_bridge_links_tenant_isolation on chat_bridge_links;
create policy chat_bridge_links_tenant_isolation on chat_bridge_links
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table chat_bridge_inbox enable row level security;
drop policy if exists chat_bridge_inbox_tenant_isolation on chat_bridge_inbox;
create policy chat_bridge_inbox_tenant_isolation on chat_bridge_inbox
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
