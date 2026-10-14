-- WWC Academy: адресаты обращений бота витрины V33 (14.10.2026).
--
-- academy_showcases.support — кому уходят обращения из бота витрины:
-- {"admin_telegram_user_id": 123, "author_telegram_user_id": 456}. Автор по умолчанию —
-- Telegram владельца витрины (lead_actors), поэтому поле может оставаться пустым.
-- Идемпотентно; без знака доллара (n8n/current/wwc_sql.py).

begin;

alter table academy_showcases add column if not exists support jsonb not null default '{}'::jsonb;
alter table academy_showcases drop constraint if exists academy_showcases_support_check;
alter table academy_showcases add constraint academy_showcases_support_check check (jsonb_typeof(support) = 'object');

commit;
