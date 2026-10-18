-- WWC Academy: дата вебинара, выбранная в боте витрины V38 (18.10.2026).
-- Код — app/academy/webinars.py, app/telegram/showcase_bot.py.
--
--   academy_ad_opens.webinar — метка вебинара (ig_web24 …), которую человек выбрал кнопкой «Перенести на …»
--                              в боте. Важнее пункта формы: по ней идут напоминания и список «не в боте».
--
-- Только add column if not exists: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

alter table academy_ad_opens add column if not exists webinar text;

commit;
