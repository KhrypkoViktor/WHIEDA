# STATE_core — вход без Telegram (Google/Max по коду владельца), Core-срез

Обновлено: 2026-10-15. Исполнитель: Core-сессия. Лид выпускает; на серверы, в бой и к базе Core-сессия не ходила.

## Git

- Repo: `D:\Projects\WHIEDA` (корень); worktree `D:\Projects\_worktrees\whieda-core-login`.
- Ветка `core/login-identities-20261015` от `origin/core/academy-threads-20261013` = 17ce3e6
  (бой 8c8dab6 — предок той же ветки, +5 коммитов академии между ними).
- Commit кода: **f988861** («login: вход через Google и Max по коду владельца (V34) — без Telegram»).
- Изменённые файлы (только они в commit):
  - `postgres/sql/platform_login_identities_v34.sql` — новая миграция;
  - `postgres/scripts/apply_staging_platform_all.ps1`, `postgres/scripts/staging_proof_lib.py` (+строка V34),
    `postgres/scripts/shared_staging_release_lib.py` (EXPECTED_APPLY_COUNT 56 → 57);
  - `backend/platform-api/app/login_identities/{__init__,rules,service,google,routes,bot}.py` — новый пакет;
  - `backend/platform-api/app/max/webapp.py` — Max: код владельца, `browser_login_url`, account_identities;
  - `backend/platform-api/app/content_access/routes.py` — `/me` → `login_methods`, `code` в теле max-webapp;
  - `backend/platform-api/app/content_access/service.py` — только добавлен `create_session_for_user`;
  - `backend/platform-api/app/settings.py` — `PLATFORM_GOOGLE_OAUTH_CLIENT_ID/_SECRET/_REDIRECT`;
  - `backend/platform-api/app/main.py` — роутер; `app/telegram/processor.py` — хук команд владельца;
  - `backend/platform-api/app/schema_requirements.py` — три таблицы в core;
  - `backend/platform-api/tests/postgres_testkit.py` (+V34), `tests/test_staging_sql_order.py` (пин 56 → 57),
    `tests/test_max_webapp.py` (фикстура под новые запросы + 7 тестов), новые `tests/test_login_identities.py`,
    `tests/test_login_identities_postgres.py`.
- Не тронуто: Telegram-путь входа (challenges/poll/бот `/start content_access_`), таблицы с `telegram_user_id`,
  рассылки, n8n, `lead_actor_channels` (по нему живёт переписка с ботом Max).

## Решения Core (для лида)

1. **Существующие привязки Max (V30/V31)** — бэкфилл в миграции V34 из `lead_actor_channels(max)` →
   `account_identities(max, linked_by='telegram:/max')` + чтение при входе: сначала `account_identities`,
   потом прежняя таблица (и тогда строка дописывается). **Отозванная** в кабинете привязка (`revoked_at`)
   прежней таблицей не воскрешается — вход через Max закрыт до нового кода. Новые привязки через `/max` и
   кнопку maxlink тоже пишутся в `account_identities` (`telegram:/max`, `telegram:maxlink`).
2. **Google state** хранится не подписью, а строкой `login_pending` (state/nonce хешами, PKCE verifier,
   host, return_to, visitor_session_id; 15 минут; state одноразовый). Секрета для подписи не нужно.
   `pending` после callback — та же таблица (`status='awaiting_code'`, `link_token_hash`).
3. **id_token** проверяется по iss/aud/exp/nonce/email_verified без проверки подписи: токен приходит по TLS
   прямо из token endpoint с client secret (так разрешает документация Google OIDC).
4. **Перебор кода** (6 цифр): 5 попыток на одну чужую личность (Google `sub` / Max id) в окне 15 минут,
   дальше `code_locked`; новый вход через Google/новый initData в том же окне попыток не добавляет.
   Попытка пишется отдельной транзакцией до проверки кода (иначе откат проверки стирал счётчик — найдено
   на настоящем PostgreSQL).
5. **Cookie после Google**: callback на `wwc.best`; если хост из state в той же семье cookie (`.wwc.best`) —
   cookie ставится прямо на редиректе (`Domain=.wwc.best`, как у Telegram-входа); витрина Академии на чужом
   домене — редирект `#wwc-login=<одноразовый>` (`create_bot_login`, 10 минут).
6. Поиск «кого»: точный ref → id Telegram (число) → имя в `referral_profiles.public_profile->>'display_name'`
   (`position()`, регистр не важен) → имя в `lead_actors.display_name` (люди без сайта). Несколько — список с ref и id.
7. Google выключен (пустые настройки) → 503 `google_disabled`; Max-часть и коды работают без Google.

## Контракт ручек (для Site-сессии)

Все ручки — в двух адресах: `/api/v1/content-access/…` (бой) и `/v1/content-access/…` (staging без /api).
Ошибки — как везде: `{"ok": false, "error": "<код>", "trace_id": …}`.

### Google

- `GET /api/v1/content-access/login/google/start?return_to=<путь|https://host/путь>[&visitor_session_id=<uuid>]`
  Браузерный переход (не XHR). Хост берётся из `return_to`, если он абсолютный, иначе из запроса
  (`X-Forwarded-Host`/`Host`); проверка как у Telegram-входа (`wwc.best`, `*.wwc.best`, витрины).
  → `302` на `accounts.google.com` (PKCE S256, scope `openid email profile`, `prompt=select_account`).
  → `503 google_disabled` (настроек нет), `400 invalid_return_to`.
- `GET /api/v1/content-access/login/google/callback?state=&code=` (или `&error=`) — ходит только Google.
  → `302 https://<host><return_to>` — личность привязана, cookie `wwc_content_session` уже стоит;
  → `302 https://<host><return_to>#wwc-login=<одноразовый>` — то же, но хост вне семьи `.wwc.best`
    (сайт уже умеет `#wwc-login=` — `BotLogin.astro`);
  → `302 https://<host><return_to>#wwc-link=<pending>` — Google-аккаунт не привязан: показать экран
    «Код от Виктора» и отправить `POST …/login/link`;
  → `302 https://<host><return_to>#wwc-link-error=<код>` — `google_denied` (человек отказал / без code),
    `google_token_invalid`, `email_unverified`, `code_locked` (5 неверных кодов за 15 минут);
  → `400 google_state_invalid` (JSON) — неизвестный, просроченный или повторный state; хоста для возврата нет.
- `POST /api/v1/content-access/login/link` body `{"pending_id": "<из #wwc-link>", "code": "483 921"}`
  (пробел/дефис в коде допустимы). → `200 {"ok": true, "status": "authenticated", "expires_at": "<iso>"}`
  + `Set-Cookie` сессии (та же, что после Telegram; `/me` дальше работает как обычно).
  Ошибки: `400 code_invalid`, `410 code_expired`, `409 code_used`, `429 code_locked`, `409 identity_taken`
  (этот Google уже у другого человека), `404 pending_invalid` (чужой/использованный pending — начать вход
  заново), `410 pending_expired` (15 минут прошли — начать заново).

### Max (мини-приложение `/max-app/`)

- `POST /api/v1/content-access/max-webapp` body `{"init_data": "...", "return_to": "https://host/max-app/", "code": "483 921"?}`
  Ответ `{"ok": true, "status": ..., "url": "...", "browser_login_url": "...", "telegram_url"?: "..."}`:
  - `status` `signed_in` | `linked` | `login_confirmed` (+ `login_expired|login_used|login_not_found`,
    `link_*` — как раньше): **`browser_login_url`** — `https://<host><страница>#wwc-login=<одноразовый>`,
    10 минут; мини-приложение сразу `WebApp.openLink(browser_login_url)` и закрывается. Страница — та, где
    нажали «Войти» (из заявки `login_<challenge>`), иначе `return_to`. `url` (вход внутри Max) оставлен.
  - `status: "not_linked"` — Max не привязан и кода нет: показать поле «Код от Виктора», повторить тот же
    POST с `code`. `telegram_url` ещё приходит (совместимость), сайт его не показывает.
  - `status` `code_invalid` | `code_expired` | `code_used` | `code_locked` | `identity_taken` — код не
    подошёл (HTTP 200, `url` и `browser_login_url` пустые): показать текст, оставить поле.
  - Ссылка со страницы входа: `https://max.ru/<бот>?startapp=login_<challenge>` — как сейчас (`channel: "max"`
    в `POST …/challenges`).

### Кабинет `/me/`

- `GET /api/v1/content-access/me` → новое поле `login_methods: [{provider, label, linked_at}]`:
  Telegram всегда первый (`label: "Telegram"`, `linked_at: null`), затем `google` (`label` = e-mail) и
  `max` (`label` = имя в Max), `linked_at` — ISO. При сбое списка — только Telegram (кабинет не ломается).
- `DELETE /api/v1/content-access/login/identities/{google|max}` (с cookie сессии)
  → `200 {"ok": true, "provider": "google", "revoked": 1}` (`revoked: 0` — нечего отвязывать);
  `400 invalid_provider` (в т.ч. `telegram`), `401` без сессии.

### Бот (Telegram, только владелец и super-admins; партнёру бот ничего не шлёт)

- `код входа <ref | id | имя>` → «Код входа для Евгения Волкова (volkova): 483 921 / Действует 24 часа, один раз …».
  Повторная выдача гасит прежний код. Несколько совпадений — список «имя (ref) · id».
- `способы входа <кто>` → список Telegram / Google: e-mail (с дата) / Max: имя (с дата) + «Активный код: до … МСК».
- `отвязать google|max <кто>` → «Google отвязан у …» / «У … нет привязки Google».
- Не владелец: «Команда недоступна.» Также `/logincode`, `/loginmethods`, `/unlink google …`.

## Настройки (бой и staging, .env Core)

- `PLATFORM_GOOGLE_OAUTH_CLIENT_ID`, `PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET`,
  `PLATFORM_GOOGLE_OAUTH_REDIRECT=https://wwc.best/api/v1/content-access/login/google/callback`
  (для staging — свой redirect, он должен быть добавлен в тот же OAuth client в Google Console).
  Все три пустые → Google выключен (`google_disabled`), остальное работает.
- Владелец кодов — `PLATFORM_BILLING_OWNER_TELEGRAM_ID` + `PLATFORM_ADMIN_SUPER_TELEGRAM_IDS` (уже есть).

## Миграция V34 (применяет лид)

`postgres/sql/platform_login_identities_v34.sql`: `account_identities`, `login_link_codes`, `login_pending`
(RLS `platform_current_tenant_id()`, `lock_timeout 5s`), бэкфилл Max из `lead_actor_channels`. Идемпотентна
(в тесте применяется трижды). Зарегистрирована: testkit, `apply_staging_platform_all.ps1`, `staging_proof_lib`,
`EXPECTED_APPLY_COUNT=57`, `schema_requirements` (core). Без неё новый код при старте пишет
`schema_compatibility_missing_tables`; релизный гейт не пустит.

## Что проверено

- `pytest tests/test_login_identities.py tests/test_max_webapp.py tests/test_content_access.py
  tests/test_content_access_webapp_login.py tests/test_schema_requirements.py tests/test_staging_sql_order.py` — 153 passed.
- Настоящий PostgreSQL (`scripts/run_postgres_integration_tests.ps1`, одноразовый кластер):
  `tests/test_login_identities_postgres.py` — passed (миграция ×3, бэкфилл, коды, Google start→callback→link
  с подделанным обменом кода, повторный вход, identity_taken, отвязка, Max-код, замок 5 попыток, RLS).
  Полный прогон всех 33 файлов `*_postgres` (V34 теперь в общем списке testkit) — 49 passed, 0 failed.
- Полный `pytest` (unit): до изменений 28 failed / 4 errors / 2149 passed; после — **26 failed / 4 errors /
  2213 passed**. Оставшиеся падения — те же известные (build/golden/staging-harness, acceptance lab,
  showcase bot ×2, wwc_markets RLS без DSN, lead owner routing); новых нет. Два падения `test_staging_sql_order`
  в «до» — артефакт моих правок списков во время прогона, сейчас зелёные.

## Что не проверено

- Настоящий Google OAuth (обмен кода, реальный id_token) — нужен OAuth client владельца и staging.
- Max на телефоне: реальный `initData`, `openLink(browser_login_url)` — Site-сессия + владелец на staging.
- Миграция на общей базе staging/бой — применяет лид по регламенту.
- Поведение команд владельца при открытом диалоге заявки на сайт в боте (как и у `referral_admin`:
  диалоги стоят раньше в цепочке).

## Открытые вопросы лиду

1. Имя бота Max в `start_param` и тексты экрана кода — у Site-сессии; Core принимает `code` в любом виде
   («483 921», «483-921»).
2. Лог: `login_code_issued`, `login_code_failed` (reason), `login_identity_linked` (provider, identity_id,
   telegram_user_id, linked_by=code|telegram), `login_identity_revoked`, `login_google_denied/failed` —
   без e-mail и кодов. Достаточно ли `telegram_user_id` в логе (так пишут соседние модули)?
3. `EXPECTED_APPLY_COUNT`/пин в `test_staging_sql_order` подняты до 57; пины «41» в
   `test_shared_staging_release_harness` падали и до меня — не трогал.
4. Срок кода 24 ч и 5 попыток/15 мин — константы `rules.py`; менять по слову владельца.

## Коммиты

- f988861 — код, миграция, тесты (22 файла). Следующий — этот STATE. Ветка отправлена:
  `origin/core/login-identities-20261015`. В master не вливать без лида.

## Следующий шаг

Лид: V34 на общей базе по регламенту → `release_core.ps1 -Target staging` → Site-сессия (fixtures по
контракту выше) → владелец: код из бота → Google на staging (нужен OAuth client и три переменные) →
Max на телефоне → бой (Core, затем сайт). Сосед по Академии предупреждается перед релизом Core.
