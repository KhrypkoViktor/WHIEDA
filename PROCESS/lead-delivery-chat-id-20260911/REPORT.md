# Заявки с партнёрских страниц не доходят в бота — 2026-09-11

Симптом: на `igoref.wwc.best` форма «Заказ через консультанта» показывает
«Не удалось отправить заявку», в Telegram партнёру ничего не приходит. Партнёр
при этом «дохера раз дергал /start».

## Причина (цепочка, с доказательствами)

1. Сайт POST `/api/lead` → n8n `WWC Website Leads P0.3` (`wwc-website-leads-p0`).
   Лид **сохраняется** (`L-EA2F6C7CE3`, owner `igoref`).
2. Узел «Postgres: prepare lead delivery» ищет получателя с chat id.
   `lead_actors.igoref.telegram_chat_id = NULL` → строк нет → n8n отдаёт пустой
   item `{}`.
3. «Telegram: send lead delivery» → `Bad Request: chat_id is empty` → execution
   падает (executions 29672, 29675, 29685).
4. Ветка доставки по позиции на канвасе выполнялась **раньше** «Respond: lead
   accepted» → сайт получает HTTP 200 с пустым телом → клиент честно считает
   ответ сломанным (`public/wwc-api/http.js`) → «Не удалось отправить заявку».

Почему chat id пустой у всех новых партнёров:

- Бот после переезда в Core (2026-08-02) на `/start` **ничего не записывал**:
  `app/telegram/processor.py` показывал меню и всё. Старый n8n-бот писал в лист
  `Users_Access`; после переезда лист замер (8 строк, 2026-08-21).
  `PARTNER_ONBOARDING_STANDARD_V1.md` обещал «ID появится после /start» —
  описывал уже несуществующий механизм.
- Реестр партнёров переехал с вкладки `Partners_Ref` (gid 1733124410) на
  `Partner_Subscriptions` (gid 1209283579), а синхронизация
  `run_partners_ref_runtime_sync_2026-08-01.py` читает старую вкладку.
  `natali` есть только в новой → в runtime её нет вообще → заявки с
  `natali.wwc.best` уходят владельцу корня.

Итог на утро: 28 попыток доставки в `pending` (igoref 6 лидов, fedorov 3,
ladnaya 9, harold 2, viktor 1, makarova 1); реальные клиенты среди них —
Жанна Другак (igoref, Вентун, 31.08), Анжела и Павел (fedorov, 05–06.09).

## Что сделано

| Шаг | Статус | Где |
|---|---|---|
| Chat id вписаны по нику: igoref, fedorov, sofiya, olesya-vselennaya | сделано, проверено в БД | `n8n/current/link_partner_telegram_chat_ids_2026-09-11.py` |
| Очередь отправлена: 8 лидов igoref + fedorov, помечены `sent` | сделано (execution 29695) | `n8n/current/backfill_pending_lead_deliveries_2026-09-11.py` |
| Ladnaya: 7 лидов | **не ушло** — `bot can't initiate conversation with a user`: она не нажимала `/start` у текущего бота. Повторить прогон после её `/start` | тот же скрипт |
| Тестовые лиды (harold/viktor 28.07, Platform Core Smoke, makarova «Проверка доставки», тест Claude) | намеренно оставлены `pending` | — |
| Core: автопривязка chat id по нику на любом личном сообщении боту | код + 6 тестов, коммит `be7c003`; **деплой не выполнен** | `backend/platform-api/app/leads/actor_link.py`, `app/telegram/processor.py` |
| n8n: Respond → потом доставка; IF на пустой chat id; Telegram-ошибка пишется в `error_text`, не роняет execution | скрипт готов, dry-run ок; **не применён** | `n8n/current/patch_wwc_website_leads_respond_first_2026-09-11.py` |
| Доки: переезд реестра, `/start` вместо ручного ID | обновлены | канон §0 и «Партнёр, ref и поддомен»; `WHIEDA_FILE_MAP_CURRENT.md`; сайт `docs/PARTNER_ONBOARDING_STANDARD_V1.md`, `docs/PARTNER_INFORMATION_REQUEST_TEMPLATE.md` |

## Осталось (владелец)

1. Применить патч workflow:
   `cd n8n/current && python patch_wwc_website_leads_respond_first_2026-09-11.py`
   (бэкап кладёт в `n8n/backups/`).
2. Задеплоить Core: `cd n8n/current && python deploy_platform_core_2026-08-02.py`.
3. Ladnaya, Sofiya, Олеся — попросить нажать `/start` у бота; затем повторить
   backfill для Ladnaya.
4. `natali` (`@Ptuwe4ka`, id 137699884): в runtime её нет. Либо строка в
   `Partners_Ref` + sync, либо сначала перевести sync на
   `Partner_Subscriptions` (отдельная задача — колонки другие).
5. Решить судьбу sync: перевод на `Partner_Subscriptions` закрывает двойное
   ведение реестра.

## Побочно замечено, не трогал

- На чистом браузере на `igoref.wwc.best` `firstRef` резолвится в `dev`
  (`initial_ref: "dev"` в теле заявки); атрибуция всё равно по `active_ref`.
- В suite `backend/platform-api` 83 старых падения (advisor-фейки без `cursor`,
  отсутствующие файлы `n8n/current/wwc_website_lead_v1_2026-08-02.json`,
  `constants`) — к этому срезу не относятся.
