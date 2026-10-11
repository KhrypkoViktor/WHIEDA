-- Витрина Академии ALMIRA (11.10.2026). Данные, не миграция: применена к общей базе
-- через n8n/current/wwc_sql.py --file. Повторный прогон безопасен. Без знака доллара.
--
-- Автор — Александра Пармон, личный Telegram 739736805 (проверка домашек, ключи).
-- Ученикам — контакт студии @alm_school. Бот витрины — @ALMIRA_I_bot (он же шлёт
-- заявки с almira.by через lead.php; токен и секрет вебхука — в env Core боя:
-- PLATFORM_ALMIRA_BOT_TOKEN, PLATFORM_ALMIRA_WEBHOOK_SECRET).
-- Адреса: almira.wwc.best — пилот, пока у academy.almira.by нет DNS; первым в hosts
-- стоит адрес, куда ведут ссылки бота. После DNS поменять порядок (внизу).

begin;

insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id, telegram_username)
values ('almira', 'whieda', 'Александра Пармон', '739736805', 739736805, 'almira_art_studio')
on conflict (actor_id) do nothing;

insert into tenant_bot_bindings (binding_id, tenant_id, telegram_bot_id, webhook_secret_ref, bot_token_ref,
                                 bot_username, status, processing_mode)
values ('almira-academy-bot', 'whieda', 8876715302, 'env:PLATFORM_ALMIRA_WEBHOOK_SECRET',
        'env:PLATFORM_ALMIRA_BOT_TOKEN', 'ALMIRA_I_bot', 'active', 'core')
on conflict (binding_id) do update
  set bot_username = excluded.bot_username, bot_token_ref = excluded.bot_token_ref,
      webhook_secret_ref = excluded.webhook_secret_ref, updated_at = now();

insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               theme, assets, links, telegram_binding_id, more_mode)
values (
  'whieda', 'almira', array['almira.wwc.best', 'academy.almira.by'], 'almira', 'ALMIRA', 'Онлайн-академия ALMIRA',
  '{"--gold": "#E4541B", "--gold-light": "#E4541B", "--gold-ink": "#A8471F", "--app-on-gold": "#140C08",
    "--bg": "#F7F2EA", "--cream-2": "#EEE6DA", "--line": "#D6C9B8", "--ink": "#221D19",
    "--ink-soft": "#3A322C", "--muted": "#6E6256"}'::jsonb,
  '{"logo_url": "/academy/brands/almira/logo.png", "logo_alt": "ALMIRA",
    "favicon_url": "/academy/brands/almira/favicon-32.png",
    "icon_url": "/academy/brands/almira/apple-touch-icon.png", "theme_color": "#E4541B"}'::jsonb,
  '{"home_url": "https://almira.by/", "contact_url": "https://t.me/alm_school",
    "contact_label": "Написать студии", "contact_telegram": "alm_school",
    "privacy_url": "https://almira.by/privacy-policy/"}'::jsonb,
  'almira-academy-bot', 'none'
)
on conflict (tenant_id, showcase_id) do update
  set theme = excluded.theme, assets = excluded.assets, links = excluded.links,
      telegram_binding_id = excluded.telegram_binding_id, updated_at = now();

insert into tenant_domains (domain, tenant_id, kind, is_active, verified_at)
values ('academy.almira.by', 'whieda', 'custom', true, now())
on conflict (domain) do nothing;

commit;

select showcase_id, hosts, owner_actor_id, telegram_binding_id from academy_showcases order by showcase_id;

-- После DNS academy.almira.by (ссылки бота — на свой адрес):
-- update academy_showcases set hosts = array['academy.almira.by', 'almira.wwc.best'], updated_at = now()
-- where tenant_id = 'whieda' and showcase_id = 'almira';
