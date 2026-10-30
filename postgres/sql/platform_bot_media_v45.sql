-- WWC: хранилище медиа бота V45 (30.10.2026): видео-тур, голосовые, кружки, фото для бота-воронки.
-- Код — app/bot_media/service.py (поиск, запись, отправка в Telegram и Max),
-- app/telegram/bot_media_admin.py (загрузка и команды владельца в боте), app/funnel/system_word.py (слово СИСТЕМА).
--
--   bot_media — одна строка на один файл бота; ключ — короткое имя для кода («system_tour»).
--   key              — латиница, цифры, «_» и «-», от 1 до 64 знаков; уникален в арендаторе.
--   kind             — video | voice | video_note (кружок) | photo | audio | document.
--   telegram_file_id — идентификатор файла в Telegram-боте WWC; по нему файл шлётся повторно без загрузки.
--                      File_id действует только у бота, который его получил.
--   max_token        — токен вложения в Max; пусто, пока файл не отправляли в Max: при первой отправке
--                      код скачивает файл из Telegram, грузит его в Max и записывает токен сюда.
--                      При замене файла в Telegram токен сбрасывается.
--   caption          — подпись по умолчанию (необязательна; отправляющий код может передать свою).
--   source_note      — откуда файл: бот и сообщение (например, bot=whieda-advisor-bot telegram:<чат>:<сообщение>).
--   created_by       — кто загрузил: 'telegram:<id>'.
--
-- Нужен хотя бы один из двух идентификаторов: telegram_file_id или max_token.
-- Один арендатор видит только свои строки (RLS, как у соседних таблиц).
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists bot_media (
  media_id bigserial primary key,
  tenant_id text not null,
  key text not null
    check (char_length(key) between 1 and 64 and key !~ '[^a-z0-9_-]'),
  kind text not null
    check (kind in ('video', 'voice', 'video_note', 'photo', 'audio', 'document')),
  telegram_file_id text,
  max_token text,
  caption text,
  source_note text,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint bot_media_tenant_key_unique unique (tenant_id, key),
  constraint bot_media_has_file check (telegram_file_id is not null or max_token is not null)
);

alter table bot_media enable row level security;

drop policy if exists bot_media_tenant_isolation on bot_media;
create policy bot_media_tenant_isolation on bot_media
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
