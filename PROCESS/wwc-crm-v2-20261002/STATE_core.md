# WWC CRM v2 — STATE Core (02.10.2026)

ТЗ: `PROCESS/wwc-crm-v2-20261002/TASK.md` в основном checkout (разделы 4–6, 8–10).
Исполнитель: Core-agent (Claude). Этот файл лежит в ветке `core/crm-v2`: хук сессии не даёт
писать в основной checkout `D:\Projects\WHIEDA`.

| что | значение |
|---|---|
| worktree | `D:\Projects\WHIEDA\.claude\worktrees\eloquent-bhabha-2fc8b8` |
| ветка | `core/crm-v2` от origin/master `34d2c85`, не запушена, upstream снят (в master не уйдёт случайно) |
| подпись | Core-agent / core@whieda.local |
| миграция | `postgres/sql/platform_crm_v20.sql` (V19 занята Академией v2) |
| выпуск | не делаю — лид |

## Базовая линия origin/master (до правок)

- `pytest -q -p no:cacheprovider tests`: 22 failed + 4 errors = 26 старых падений
  (golden/parity/markets/discovery/acceptance; на этой машине 26, не 31).
- `run_postgres_integration_tests.ps1`: 20 passed.

## План

1. [x] V20: crm_activities + backfill, поля crm_contacts, crm_templates, индексы; testkit + schema_requirements.
2. [x] Сервис: активности, мягкое удаление/восстановление/очистка, snooze/done, шаблоны, метки.
3. [x] API раздела 5: курсоры, поиск ILIKE, pipeline, activities, log, tags, templates, me.
4. [x] Напоминание о встрече за 60 минут, дайджест с именами и кнопками.
5. [x] Тексты бота «Ежедневник» → «WWC CRM».
6. [x] Тесты раздела 9; полный прогон, postgres, check_zone — см. «Проверки».

## Что сделано (кратко)

- **V20** (`platform_crm_v20.sql`): `crm_contacts` + tags (text[], CHECK ≤ 20), priority (0/1),
  last_touch_at, meeting_reminded_at, deleted_at; `platform_accounts` +
  crm_install_hint_dismissed_at, crm_templates_seeded_at; таблицы crm_activities и crm_templates
  с RLS; индексы: lower(name), встречи к напоминанию, удалённые, лента (contact, created_at desc);
  уникальные частичные индексы «один created на карточку» и «одна note на заметку» → backfill
  `on conflict do nothing`. Без `$`, без pg_trgm, только add/create if not exists.
- **Лента**: каждое действие пишет строку в той же транзакции: created (manual / import / site),
  note (note_id; удалили заметку — строка уходит), status {from, to}, step {action: done / snooze / set},
  call / message {channel, template_id, template_title}, meeting {set / cancel}, lead (повторная заявка).
  last_touch_at: звонок, сообщение, заметка, новая встреча.
- **Мягкое удаление**: все чтения (списки, «Сегодня», экспорт, дубли, дайджест, напоминания,
  заявка → карточка) пропускают deleted_at. «Вернуть» 24 ч; номер занят живой карточкой → 409.
  Воркер стирает карточки старше 24 ч (каскад заметок и ленты).
- **«Сделано»** (таблица в `rules.py`): invite → invited (нужна встреча: в запросе или будущая на
  карточке, иначе 400 meeting_at_required) → result → presented (+2 дня) → decide → deciding (без даты);
  resume → deciding; ping: client/partner +30, иначе без даты. Присланный next_at побеждает.
- **«Перенести»**: `{days}` 1–366 или `{date}` от сегодня до года вперёд; шаг тот же.
- **Встреча**: за 50–65 минут до начала, раз в 5 минут; meeting_reminded_at ставится в одной
  транзакции с outbox; перенос встречи сбрасывает отметку и даёт новое напоминание (новый ключ).
- **Дайджест 09:00**: «Доброе утро! Сегодня в WWC CRM:» + группы «Встречи сегодня / Позвонить /
  Напомнить», до 10 имён, «…и ещё N — в приложении.»; кнопки «Открыть «Сегодня»» и три первых
  человека. Имена читаются только для аккаунтов, которым письмо уходит сейчас.
- **Кнопки входа**: ссылки входа (`with_site_login`) воркер делает в момент отправки; в
  platform_outbox лежат обычные ссылки (токена нет, 30 минут считаются от отправки).
- **HTML**: `<` и `>` в именах заменяются на ‹ › — имя из заявки не сломает сообщение (400 от Telegram).
- **Бот**: «📒 WWC CRM», «📒 Открыть WWC CRM», тексты без «ежедневника»; понимает «wwc crm»,
  «открыть crm» и старые слова.

## API для сайта v2 (всё под `/api/v1/content-access/crm`)

| метод и путь | тело / параметры | ответ |
|---|---|---|
| GET /me | — | + `install_hint_dismissed`, `my_name` (для `{мое_имя}`, из public_profile.display_name, может быть '') |
| PATCH /me | `{timezone?, install_hint_dismissed?}` | как GET /me |
| GET /today | — | v1 `groups` + новый `sections` [{key: meetings / call / remind / overdue, title, contacts}] |
| GET /contacts | `q, status, tag, cursor, limit (1–100, по умолчанию 50), sort=updated / name / next` | `{items, next_cursor, total}` |
| POST /contacts/search | те же поля в теле JSON | как GET; **для поиска по строке сайту лучше этот** (имя / телефон не попадут в URL и в access-лог nginx) |
| POST /contacts | + `tags[]`, `priority` | 201 `{contact}` |
| PATCH /contacts/{id} | + `tags[]` (null — очистить), `priority` 0/1 | `{contact}` |
| DELETE /contacts/{id} | — | 204, мягко |
| POST /contacts/{id}/restore | — | `{contact}`; 404 после 24 ч; 409 duplicate |
| GET /contacts/{id}/activities | `cursor, limit` | `{items: [{id, kind, payload, created_at, note?: {id, body}}], next_cursor}` |
| POST /contacts/{id}/log | `{kind: call / message, channel: phone / whatsapp / telegram / viber / max / sms, template_id?}` | 201 `{contact, activity}` |
| POST /contacts/{id}/snooze | `{days}` или `{date}` | `{contact}` |
| POST /contacts/{id}/done | `{meeting_at?, next_at?}` | `{contact}`; next_at = null → дату выбирает партнёр |
| GET /pipeline | — | `{columns: [{status, title, count, items (20), next_cursor}], total}` |
| GET /pipeline/{status} | `cursor, limit` | `{status, title, items, next_cursor, total}` |
| GET /tags | — | `{items: [{tag, count}]}` |
| GET / POST /templates, PATCH / DELETE /templates/{id} | `{title ≤ 60, body ≤ 1000, position?}` | `{items}` / 201 `{template}` / `{template}` / 204 |

Контактная карточка теперь содержит `tags`, `priority`, `last_touch_at`. Ошибки — `{error: <код>}`:
invalid_cursor, invalid_sort, invalid_status, invalid_snooze, snooze_required, meeting_at_required,
no_next_step, next_at_required, invalid_kind, invalid_channel, template_not_found, too_many_templates,
title_required / title_too_long, body_required / body_too_long, tag_too_long, too_many_tags,
invalid_priority, duplicate (+contact_id), contact_not_found.

**Совместимость с сайтом v1** до выпуска v2: `GET /contacts` без limit / cursor / sort / tag отдаёт
до 300 карточек и старый ключ `contacts` (плюс items / next_cursor / total). Сайту v2 — всегда слать `limit`.

## Решения исполнителя (на согласование лиду)

- Владелец строк — `account_id` (как у crm_contacts в V14), а не `telegram_user_id` из ТЗ:
  один account на (tenant, telegram_user_id), RLS и каскад удаления те же.
- Ссылка «Открыть карточку» из бота: `https://<хост>/crm/?contact=<id>#contact/<id>`.
  `with_site_login` заменяет фрагмент на `#wwc-login=…`, а BotLogin.astro после входа
  оставляет только path+query — фрагмент `#contact/<id>` теряется. Поэтому id едет в query.
- Удалённые карточки физически стираются воркером после окна восстановления (24 ч):
  v1 обещал «удаление настоящее», мягкое удаление нужно только для «Вернуть».
- Таблица «Сделано» (см. выше) — предложение исполнителя по «следующий по правилам rules.py».
- Метки: регистр сохраняется, «VIP» и «vip» на одной карточке — одна метка; до 10 меток по 32 символа.
- Шесть шаблонов по умолчанию (`rules.DEFAULT_TEMPLATES`) — черновик исполнителя: короткие,
  вежливые, без обещаний дохода и здоровья (тест проверяет слова). Нужно согласие владельца.

## Вопросы лиду (одна строка — один вопрос)

- Сайту v2: после входа читать `?contact=<id>` на `/crm/` и открывать карточку (см. решения).
- Сайту v2: всегда передавать `limit` в GET /contacts (без него — режим совместимости v1, до 300).
- Сайту v2: поиск по введённой строке — `POST /contacts/search` (тело), а не `GET ?q=`: nginx сайта пишет
  query string в access-лог, а это имя или телефон. Core свой uvicorn access-лог для `/crm/` уже чистит.
- Лиду (сервер, не моя зона): если GET `?q=` останется — убрать `$args` из access-лога nginx для
  `/api/v1/content-access/crm` или согласиться, что поиск идёт только через POST.
- Тексты шести шаблонов — согласовать с владельцем (`app/crm/rules.py`, DEFAULT_TEMPLATES).
- V20 не внесена в `apply_staging_platform_all.ps1` / `staging_proof_lib.py` / `EXPECTED_APPLY_COUNT`
  (как и V18 на master): внести при интеграции вместе с V18/V19, счётчик 41 → 44.
- Порядок выката: сначала V20 на общую базу (`wwc_sql.py --file postgres/sql/platform_crm_v20.sql`),
  потом код; после выката V20 можно прогнать повторно — добьёт ленту карточек, созданных старой сборкой.
- `postgres_testkit.py`: и я, и Академия v2 добавили строку после V18 — при слиянии оставить обе (V19, V20).

## Ревью (независимый агент, 02.10)

Критичных нет. Исправлено:
- ПДн в access-логах: курсор «по имени» теперь только id карточки (имя сервер читает сам);
  фильтр `uvicorn.access` срезает query string у путей `/crm/`; добавлен `POST /contacts/search`.
- Подделанный курсор с не-строкой давал 500 → теперь 400 invalid_cursor.
- Одна сбойная карточка откатывала все напоминания тенанта за проход → теперь пропускается только она.
- Таблица «Сделано»: дописан случай «Пауза + ping» (нужна дата) и что шаг важнее статуса.

## Проверки (02.10, коммит — последний в `core/crm-v2`)

| команда | результат |
|---|---|
| `python -m pytest -q -p no:cacheprovider tests` (backend/platform-api) | 22 failed + 4 errors, 1875 passed, 24 skipped; падения те же 26, что на origin/master, новых нет |
| `pwsh backend/platform-api/scripts/run_postgres_integration_tests.ps1` | 23 passed, 0 failed (было 20: +V20/backfill, +сценарий v2, +HTTP-сценарий) |
| фокус-список `release_core.ps1` / gate.yml | 183 passed |
| `python .github/scripts/check_zone.py --base origin/master` | OK (после коммита) |

Новые тесты (раздел 9): `test_crm_v2_rules.py` (Сделано, Перенести, метки, «Сегодня», шаблоны, курсоры,
V20 без `$`), `test_crm_meetings.py` (текст, окно, ключ, доступ, сбой одной карточки, вход в момент
отправки), `test_crm_v2_routes.py` (все новые маршруты: 401 / 402 / no-store, тела, совместимость v1,
фильтр access-лога), `test_crm_v2_postgres.py` (V20 дважды на данных v1 + backfill; сценарий карточки,
поиск, курсоры, воронка, удаление / «Вернуть» / очистка, шаблоны, напоминания: окно, дубли, перенос,
таймзона, доступ), `test_crm_v2_http_postgres.py` (маршруты + реальная база), `test_crm_digest.py`
переписан под дайджест v2, `test_bot_copy_audit.py` + тексты CRM.

Не проверено: живой Telegram и сайт (staging не деплою — лид); серверный schema-гейт
`check_schema_compatibility.py` (нужна база с V20); nginx access-лог сайта.
