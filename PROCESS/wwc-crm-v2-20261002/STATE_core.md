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

## Отклонения от ТЗ §5 и уточнения формата (для сайта)

Коды статусов и шагов **не менялись**: статусы new, invited, presented, deciding, client, partner, paused;
шаги invite, result, decide, ping, resume. Каждый ответ, кроме 204, содержит `ok: true`.

1. **Курсор** — непрозрачная строка base64url (`[A-Za-z0-9_-]`), не номер и не смещение. Передавать
   как есть в следующий запрос того же списка с той же сортировкой. Курсор от другого списка или
   сортировки, мусор, а при `sort=name` — уже стёртая карточка → 400 `invalid_cursor` (сайт начинает
   список заново). Конец списка — `next_cursor: null`. В курсоре нет имён и телефонов.
2. **Добавлено сверх ТЗ:** `POST /contacts/search` (тело вместо query string), `POST /contacts/{id}/done`
   принимает `{meeting_at?, next_at?}`, `GET /me` → `my_name`, `GET /today` → `sections`.
3. **`limit`**: 1–100, больше — урезается до 100. Без `limit` / `cursor` / `sort` / `tag` → режим v1 (см. выше).
4. **Воронка**: колонки всегда все 7 в порядке статусов выше, включая пустые (`count: 0, items: []`).
   В колонке: сначала ⭐ (`priority` 1), потом недавно изменённые. Первая страница — 20 карточек.
5. **`priority`** в ответе — число 0 или 1; на вход принимаются 0 / 1 / true / false.
6. **`tags`**: до 10 штук по 32 символа; `#` в начале и лишние пробелы отрезаются; повтор без учёта
   регистра отбрасывается; регистр первого написания сохраняется. Фильтр `tag` — точное совпадение.
7. **`snooze`**: ровно одно из `{days}` (1–366) или `{date}` (`YYYY-MM-DD`, от сегодня до +366 дней).
8. **`done`**: «Пригласить» без встречи → 400 `meeting_at_required` (сайт спрашивает время и шлёт
   `meeting_at`); после «Довести до решения» и «Вернуться к разговору» `next_at` = null — дату выбирает партнёр.
9. **Шаблон**: `{id, title, body, position, created_at, updated_at}`; до 30 на аккаунт; список по `position`.
10. **Ссылки бота на карточку**: `/crm/?contact=<id>#contact/<id>`; после входа остаётся `/crm/?contact=<id>`.

**Лента `GET /contacts/{id}/activities`** — элемент `{id, kind, payload, created_at, note?}`, новые сверху:

| kind | payload | когда пишется |
|---|---|---|
| created | `{source: manual / import / site}`; старые карточки — `{backfill: true}` (заявка — ещё `source: site`) | карточка создана |
| note | `{note_id}`; текст — в `note: {id, body}` | заметка; удалили заметку — строки нет |
| status | `{from, to}` | статус сменился (PATCH или «Сделано») |
| step | `{action: done, step, next_step, at}` / `{action: snooze, step, from, at}` / `{action: set, step, at}` | «Сделано» / «Перенести» / шаг или дата вручную |
| call | `{channel}` | нажали «Позвонить» (`log`) |
| message | `{channel, template_id?, template_title?}` | нажали мессенджер или «Написать по шаблону» |
| meeting | `{action: set, at}` / `{action: cancel}` | встречу назначили / убрали |
| lead | `{}` | повторная заявка с сайта на существующую карточку |

Даты `at` / `from` в step — `YYYY-MM-DD` (или null); `at` в meeting и `created_at` — ISO с часовым поясом.

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

## Тексты на утверждение владельца (до выпуска)

Скопированы из кода выводом самих функций; правка любого текста — одна строка в указанном файле.

### Шесть шаблонов по умолчанию (`app/crm/rules.py`, DEFAULT_TEMPLATES)

1. **Приглашение** — {имя}, здравствуйте! Это {мое_имя}. Хочу пригласить вас на короткую встречу: расскажу, чем занимаюсь, и отвечу на вопросы. Когда вам удобно?
2. **Напоминание о встрече** — {имя}, добрый день! Напоминаю о нашей встрече. Если планы поменялись, напишите — подберём другое время.
3. **После презентации** — {имя}, спасибо, что нашли время на встречу! Если появились вопросы, пишите — с удовольствием отвечу.
4. **Подумали?** — {имя}, добрый день! Удалось обдумать то, что мы обсуждали? Если остались вопросы, я на связи.
5. **Возобновление** — {имя}, здравствуйте! Это {мое_имя}. Давно не общались — как ваши дела? Если тема ещё интересна, давайте созвонимся.
6. **Спасибо клиенту** — {имя}, спасибо за доверие! Если появятся вопросы по заказу, пишите — я на связи.

Подстановки: `{имя}` — имя контакта, `{мое_имя}` — публичное имя партнёра (GET /me, поле my_name).
Партнёр видит шаблоны при первом открытии «Шаблонов» и может их править и удалять.

### Напоминание «через час встреча» (`app/crm/messages.py`)

```text
⏰ Через час встреча: Анна Петрова, +79286729288
Начало в 14:00 по вашему времени.
```
Кнопка: «👤 Открыть карточку» — карточка на сайте партнёра, вход без пароля. Без телефона — только имя.

### Утреннее сообщение 09:00 (пример, `app/crm/messages.py`)

```text
Доброе утро! Сегодня в WWC CRM:

📅 Встречи сегодня
• 14:00 Глеб

📞 Позвонить
• Борис — узнать результат (с 30.09)
• Дмитрий — пригласить на встречу

🔔 Напомнить
• Вера
```
Кнопки: «📋 Открыть «Сегодня»», «👤 Глеб», «👤 Борис», «👤 Дмитрий» (первые трое из сообщения).
Не больше 10 имён; если людей больше — последняя строка «…и ещё N — в приложении.».
«(с 30.09)» — шаг просрочен с этой даты. Если на сегодня ничего нет, сообщение не приходит.

### Бот: кнопка кабинета и ответ на «crm» / «ежедневник» (`app/crm/bot.py`)

- Кнопка кабинета: «📒 WWC CRM»; кнопка в ответе: «📒 Открыть WWC CRM».
- Ответ:
```text
📒 WWC CRM — ваши контакты, встречи и следующие шаги в одном приложении.

Утром в 09:00 бот пришлёт, с кем связаться сегодня, а за час до встречи напомнит о ней.
```
- Нет PRO: WWC CRM входит в PRO — платформу вашего сайта. Продлите PRO, и CRM откроется сразу.
- Не в пилоте: WWC CRM сейчас проверяют несколько партнёров. После проверки откроем её всем с PRO.

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
- Общие файлы с соседними ветками (`core/academy-v2-lms` V19, `core/cabinet-v1` V21): у меня только
  добавленные строки — `schema_requirements.py` +2 / −0, `postgres_testkit.py` +1 / −0 (строка V20 после V18),
  `jobs/worker.py` +31 / −0; `telegram/processor.py` не трогал. При слиянии в testkit оставить V19, V20, V21 по порядку.

## Ревью (независимый агент, 02.10)

Критичных нет. Исправлено:
- ПДн в access-логах: курсор «по имени» теперь только id карточки (имя сервер читает сам);
  фильтр `uvicorn.access` срезает query string у путей `/crm/`; добавлен `POST /contacts/search`.
- Подделанный курсор с не-строкой давал 500 → теперь 400 invalid_cursor.
- Одна сбойная карточка откатывала все напоминания тенанта за проход → теперь пропускается только она.
- Таблица «Сделано»: дописан случай «Пауза + ping» (нужна дата) и что шаг важнее статуса.

## Итоговый отчёт (02.10.2026)

Ветка `core/crm-v2` готова к интеграции. Код — коммит `e908234`; следующий коммит добавляет этот
STATE (тексты, отклонения от §5) и переводит `jobs/worker.py` на «только добавленные строки»
(логика та же). Точный SHA вершины — `git log -1 core/crm-v2`; проверки ниже прогнаны на вершине.
Не запушено, не выпущено. Ссылка на ветку для лида — локальная (тот же репозиторий).

### Проверки

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
