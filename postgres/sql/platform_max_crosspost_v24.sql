-- WWC: посты Telegram-канала → Max (V24, 08.10.2026).
--
-- Владелец пишет пост в «WWC Official channel» (Telegram, со своими смайликами),
-- бот повторяет его в группах и каналах Max, куда его добавили админом
-- (владелец, 08.10.2026: «телега работает всё сложнее»).
--
-- max_chats          — чаты Max, где состоит бот: приходят событием bot_added
--                      (список чатов в API Max убрали в июне 2026);
-- channel_crossposts — один пост канала = одна строка: повтор доставки Telegram
--                      не дублирует пост, альбом собирается в одно сообщение.

begin;

create table if not exists max_chats (
  tenant_id text not null references tenants (tenant_id),
  chat_id bigint not null,
  title text,
  chat_type text,
  is_channel boolean not null default false,
  status text not null default 'active' check (status in ('active', 'removed')),
  added_by_user_id bigint,
  crosspost boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, chat_id)
);

create table if not exists channel_crossposts (
  crosspost_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  source_chat_id bigint not null,
  source_message_id bigint not null,
  media_group_id text,
  payload jsonb not null,
  status text not null default 'pending' check (status in ('pending', 'sending', 'sent', 'failed', 'skipped')),
  delivered jsonb not null default '[]'::jsonb,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, source_chat_id, source_message_id)
);

create index if not exists channel_crossposts_group_idx
  on channel_crossposts (tenant_id, source_chat_id, media_group_id) where media_group_id is not null;

alter table max_chats enable row level security;
drop policy if exists max_chats_tenant_isolation on max_chats;
create policy max_chats_tenant_isolation on max_chats
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table channel_crossposts enable row level security;
drop policy if exists channel_crossposts_tenant_isolation on channel_crossposts;
create policy channel_crossposts_tenant_isolation on channel_crossposts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
