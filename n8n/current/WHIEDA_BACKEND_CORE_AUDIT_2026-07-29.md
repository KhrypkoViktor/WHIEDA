# WHIEDA Backend Core: фактическое состояние

Дата: 2026-07-29

## Готово

- `advisor-whieda-phase1` активен: Telegram, Postgres runtime, structured lookup, фото первым сообщением, follow-up и Dify fallback.
- Google Sheet остаётся master, есть отдельный runtime-кэш и штатная синхронизация.
- Release 3.3: алиасы цветов эликсиров и магнитного пояса, live-smoke, 25 бандлов в staging. Бандлы не опубликованы.
- Access: новые пользователи создаются в `Users_Access`; с 2026-07-29 `candidate` сразу получает обычные ответы, `blocked` отключён.
- База leads/ref уже работает: live `/report` увидел 19 заявок с ref-атрибуцией и статусами. В миграции закреплены first ref, current owner, история владельцев/статусов, watcher и ref-профили.
- В existing workflow уже работают `/leads`, `/lead <id>`, кнопки «в работу/завершить». Добавлена запись суммы без второй таблицы: `/lead <id> сумма <число> BYN|RUB|W$`.
- Исправлена передача прав в ветку команд и allowlist отдельного тестового super-admin; товарный SQL smoke после изменения зелёный.

## Частично

- Structured-ответы, материалы, сравнения и контекст работают, но нужен единый smoke по API/ref/правам/отказам внешних сервисов.
- Права лидеров переведены на `lead_actors` + `lead_actor_roles` (2026-08-01); watcher-маршрут через `website_lead_watchers` в SQL-проверках доступа.
- Аудит вопросов/gap есть; `/report` расширен и проверен live (пользователи, SQL/RAG/fallback, уточнения, заявки, ref-воронка).
- Website API live (`wwc-advisor-public-v1`, smoke с `ref=ladnaya` зелёный); контракт `whieda-advisor-api-v1`.
- `Partners_Ref`: колонки поддоменов/focus-group (`public_site_url`, `site_type`, `focus_group`, `access_tier`) заполнены для фокус-группы.

## Отсутствует

- Явное ручное управление профилем партнёра и персонализацией через backend API (sheet → runtime sync).

## Следующие изменения

1. ~~Зафиксировать единый контракт API без правок сайта.~~ `WHIEDA_ADVISOR_API_CONTRACT_V1.md`.
2. ~~Перевести права лидеров с hardcode на существующие `lead_actors`/`lead_actor_roles`.~~ Сделано 2026-08-01.
3. ~~Собрать read-only super-admin отчёт и расширить smoke.~~ `/report` расширен; smoke `whieda_leads_report_smoke_2026-08-01.py`.
4. ~~Реализовать endpoint API строго по контракту, без изменений сайта.~~ Live: `wwc-advisor-public-v1` + `whieda-advisor-api-v1` (2026-08-01).
5. ~~Structure Basic: sync `Partners_Ref` → `referral_profiles` / runtime; профиль, ref, персонализация.~~ Runtime sync script `run_partners_ref_runtime_sync_2026-08-01.py` (live `success: true`); встроить в 15-мин cron — следующий шаг.
