-- WWC Academy: кто нажал «Присоединиться к вебинару» V41 (24.10.2026).
-- Код — app/academy/webinars.py (record_join), app/telegram/showcase_bot.py, app/academy/ad_leads.py (lead_flags).
--
--   academy_ad_opens.joined — {"ig_web24": "<время первого нажатия>", …}: кнопка эфира в напоминаниях
--                             за час и за 5 минут. По ней колонка «На эфире» в таблице заявок.
--
-- Только add column if not exists: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

alter table academy_ad_opens add column if not exists joined jsonb not null default '{}'::jsonb;

commit;
