-- Мастерская WWC: карточка мини-курса «Алхимия капитала» (slug mini-dhando), 16.10.2026.
-- НЕ миграция: применяется руками, когда курс загружен в Академию (load_bundle.py) и проверен.
-- ТЗ: PROCESS/wwc-mini-courses-20261016/TZ_CORE.md, раздел D. Прецедент — preza-vozrazheniya
-- (platform_shop_v22.sql). Решение владельца 16.10: клубу и участникам «Онлайн-старта» —
-- бесплатно (academy_courses.free_for, V35), остальным 500 ₽ = 5 WUSD.
--
-- Тексты карточки — служебные, как у kurs-vozrazheniya; продающий текст — решение владельца.
-- status 'pilot': карточку видят только аккаунты владельца; продажа всем — правкой статуса
-- на 'published' после проверки. Повторный прогон ничего не перезаписывает (on conflict do nothing).
-- Знака доллара в файле нет (n8n/current/wwc_sql.py).

begin;

insert into shop_items (
  tenant_id, code, kind, category, title, subtitle, description_md,
  price_wusd_minor, price_rub_minor, price_club_wusd_minor, course_slug,
  confirmer, status, sort_order, requisites_note, delivery_note
) values (
    'whieda', 'mini-dhando', 'course', 'courses',
    'Мини-курс: Алхимия капитала',
    'Мини-курс в Академии WWC',
    'Мини-курс в Академии WWC. Доступ откроется в Академии сразу после подтверждения оплаты.',
    500, 50000, null, 'mini-dhando',
    'owner', 'pilot', 20,
    null, null
  )
on conflict (tenant_id, code) do nothing;

commit;
