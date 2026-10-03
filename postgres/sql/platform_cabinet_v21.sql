-- WWC: личный кабинет партнёра на сайте /me/, V21 (02.10.2026).
--
-- partner_profile_requests — заявка партнёра изменить свой сайт из кабинета:
--   changes  — только изменённые поля (display_name, bio, photo_url,
--              contacts{phone, whatsapp, viber, max_url, email, address},
--              socials{telegram_channel_url, vk_url, instagram_url, youtube_url, tiktok_url});
--   previous — что было в этих полях на момент заявки («было → стало» у владельца).
--   Владелец применяет или отклоняет в боте. Одна pending на ref_code: новая
--   заявка переводит прежнюю в replaced, отзыв партнёром — cancelled; кнопки
--   под старым сообщением после этого ничего не применяют.
-- partner_journey_marks — ручные отметки «Следующего шага» (presentation,
--   invite_sent); остальные шаги считаются по данным.
-- partner_media — фото профиля из кабинета: JPEG не больше 1200 px, без EXIF.
--   Байты лежат в общей БД, потому что staging и бой — разные контейнеры при
--   одной базе: фото, загруженное через staging, должно открываться и на бою.
--   Раздаёт Core: GET /api/v1/content-access/partner-media/<media_id>.jpg.
--
-- Идемпотентно (create … if not exists, drop policy if exists): двойной
-- прогон ничего не меняет. В файле нет знака доллара: боевые правки идут
-- через n8n, а он режет этот знак.

begin;

set local lock_timeout = '5s';

create table if not exists partner_profile_requests (
  request_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ref_code text not null,
  telegram_user_id bigint not null,
  changes jsonb not null default '{}'::jsonb,
  previous jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'applied', 'rejected', 'replaced', 'cancelled')),
  reject_reason text check (reject_reason is null or length(reject_reason) <= 500),
  -- карточка у владельца и вопрос «причина отказа» (Reply на него — причина)
  owner_chat_id bigint,
  owner_message_id bigint,
  reason_prompt_message_id bigint,
  -- бот, который прислал карточку (staging и бой делят базу): «/profiles» показывает свои
  binding_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  reviewed_at timestamptz,
  reviewed_by bigint
);

alter table partner_profile_requests add column if not exists binding_id text;

create unique index if not exists partner_profile_requests_one_pending
  on partner_profile_requests (tenant_id, ref_code)
  where status = 'pending';
create index if not exists partner_profile_requests_ref_created
  on partner_profile_requests (tenant_id, ref_code, created_at desc);

create table if not exists partner_journey_marks (
  tenant_id text not null,
  telegram_user_id bigint not null,
  step text not null check (step in ('presentation', 'invite_sent')),
  done_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id, step)
);

create table if not exists partner_media (
  tenant_id text not null,
  media_id uuid not null default gen_random_uuid(),
  ref_code text not null,
  telegram_user_id bigint not null,
  kind text not null default 'profile_photo' check (kind in ('profile_photo')),
  content_type text not null check (content_type in ('image/jpeg')),
  body bytea not null check (octet_length(body) between 1 and 5242880),
  width integer not null check (width between 1 and 1200),
  height integer not null check (height between 1 and 1200),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}\Z'),
  created_at timestamptz not null default now(),
  primary key (media_id),
  unique (tenant_id, media_id)
);

create index if not exists partner_media_ref_created
  on partner_media (tenant_id, ref_code, created_at desc);
create index if not exists partner_media_uploader_created
  on partner_media (tenant_id, telegram_user_id, created_at desc);

alter table partner_profile_requests enable row level security;
alter table partner_journey_marks enable row level security;
alter table partner_media enable row level security;

drop policy if exists partner_profile_requests_tenant_isolation on partner_profile_requests;
create policy partner_profile_requests_tenant_isolation on partner_profile_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_journey_marks_tenant_isolation on partner_journey_marks;
create policy partner_journey_marks_tenant_isolation on partner_journey_marks
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_media_tenant_isolation on partner_media;
create policy partner_media_tenant_isolation on partner_media
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
