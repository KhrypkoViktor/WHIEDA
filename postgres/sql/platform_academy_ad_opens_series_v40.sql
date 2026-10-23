-- WWC Academy: серии вебинаров витрины V40 (23.10.2026).
-- Код — app/academy/ad_leads.py, app/academy/webinars.py, app/telegram/showcase_bot.py.
--
--   academy_ad_opens.series — серия вебинаров (''  — композиция ALMIRA, 'anatomy' — анатомия). У человека
--                             своя строка в каждой серии: своя заявка (lead_id) и свой выбор даты (webinar),
--                             поэтому записанный и на композицию, и на анатомию получает напоминания обеих.
--   Первичный ключ — (tenant_id, showcase_id, telegram_user_id, series); прежние строки — серия ''.
--
-- Повторный прогон безопасен: add column if not exists, ключ пересоздаётся тем же составом.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

alter table academy_ad_opens add column if not exists series text not null default '';

alter table academy_ad_opens drop constraint if exists academy_ad_opens_pkey;
alter table academy_ad_opens add constraint academy_ad_opens_pkey
  primary key (tenant_id, showcase_id, telegram_user_id, series);

commit;
