# STATE_core — вход без Telegram (Google/Max по коду владельца), Core-срез

Обновлено: 2026-10-15. Исполнитель: Core-сессия. Лид выпускает; на серверы, в бой и к базе Core-сессия не ходила.

## Git

- Repo: `D:\Projects\WHIEDA` (корень); worktree `D:\Projects\_worktrees\whieda-core-login`.
- Ветка `core/login-identities-20261015` от `origin/core/academy-threads-20261013` = 17ce3e6
  (бой 8c8dab6 — предок той же ветки, +5 коммитов академии между ними).
- Commit кода: **f988861** («login: вход через Google и Max по коду владельца (V34) — без Telegram»);
  правки по ревью (P0–P3 + два пункта Site) — **см. «Коммиты»** ниже.
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
7. Google выключен (пустые настройки) → `/google/start` возвращает браузер на страницу с
   `#wwc-link-error=google_disabled` (Site: JSON в адресной строке человек видел бы текстом); Max-часть и
   коды работают без Google.

### После ревью f988861 (15.10.2026)

8. **P0, привязка к браузеру (после повторного ревью — строгие режимы).** Режим решается один раз на
   `/google/start` по хосту входа относительно хоста callback из `PLATFORM_GOOGLE_OAUTH_REDIRECT`:
   - **хост в семье cookie** (`.wwc.best`): ключ генерирует **только сервер** (`new_browser_key()`), он
     уходит в cookie `wwc_oauth` (HttpOnly, Secure, Lax, 15 мин), хеш — `login_pending.browser_key_hash`.
     `browser_key` из query **игнорируется** (не попадает ни в cookie, ни в базу). Callback и
     `/login/link` сверяют **только cookie** — ключ в body не помогает;
   - **хост вне семьи** (витрины на своём домене): `browser_key` из query обязателен (иначе
     `browser_key_required`), хеш — отдельная колонка `site_key_hash`, cookie не ставится. Callback cookie
     **не смотрит** (нет ложного `oauth_browser_mismatch`) и всегда отдаёт `#wwc-link=`; `/login/link`
     сверяет **только `browser_key` из body** — cookie не помогает.
   Между cookie и body подмены нет ни в одну сторону (`service.presented_key`). Закрытая атака: чужая
   страница открывает жертве `start?browser_key=K`, атакующий делает свой start с K и ведёт жертву на свой
   callback — у жертвы cookie = серверный ключ ≠ K, строка атакующего помнит свой серверный ключ → отказ.
9. **P1, бот.** Не-владелец — это не команда: `owner_allowed` проверяется до разбора, возвращается None,
   «код входа не приходит» партнёра идёт дальше по цепочке (в поддержку), без «Команда недоступна».
10. **P2, лимит попыток.** Под `pg_advisory_xact_lock` на личность (provider, external_id); считается по всем
    строкам личности в окне; само увеличение — `update … where attempts < MAX returning`. Десять параллельных
    попыток → ровно пять `code_invalid`, остальные `code_locked` (PG-тест).
11. **P2, гонка redeem.** Код забирается атомарно: `update login_link_codes set used_at … where used_at is null
    and revoked_at is null and expires_at > now() returning` (по частичному индексу); привязка — в той же
    транзакции, `identity_taken` откатывает захват (код остаётся владельцу); параллельная привязка той же
    личности ловится UniqueViolation → `identity_taken`. Два параллельных redeem одним кодом → один `ok`,
    один `code_used` (PG-тест).
12. **P2, чистка.** `login_cleanup_step` в `app/jobs/worker.py` раз в час (`LOGIN_CLEANUP_INTERVAL_SEC`):
    `delete … where expires_at < now() - interval '1 day'` для `login_pending` и `login_link_codes` по всем
    активным тенантам (RLS → по одному). **Лиду:** rate-limit nginx на `/api/v1/content-access/login/google/start`
    и `/login/link` (неаутентифицированная запись / перебор) — не в этом срезе.
13. **P3.** Секрет Google — `field(repr=False)`; коллизия кода — до 5 генераций, дальше `code_unavailable`
    (503); `_claim_code` ищет только активный код (частичный индекс), причина отказа — вторым запросом.
    **Хеш кода — HMAC-SHA256 с `PLATFORM_LOGIN_CODE_SECRET`**, пусто → `PLATFORM_MEDIA_SIGNING_SECRET`
    (уже есть на бою), оба пустые → sha256 (только разработка). Выбрал новый ключ с запасным: смена
    медиа-секрета не должна гасить коды входа, и наоборот. Смена секрета = все выданные коды недействительны.
14. **Site + P3 повторного ревью:** после успешного Google браузер возвращается **только** через
    `#wwc-login=<одноразовый>` (`create_bot_login`, 10 мин); cookie сессии на callback **не ставится** —
    одна сессия на вход, её создаёт сайт, погасив ссылку (выбран вариант «только `#wwc-login`», как
    предложил ревьюер: сайту нужен явный сигнал). `POST /login/link` по-прежнему ставит cookie сессии в
    ответе на XHR — там сигнал и так явный (200 `authenticated`).

## Контракт ручек (для Site-сессии)

Все ручки — в двух адресах: `/api/v1/content-access/…` (бой) и `/v1/content-access/…` (staging без /api).
Ошибки — как везде: `{"ok": false, "error": "<код>", "trace_id": …}`.

### Google

- `GET /api/v1/content-access/login/google/start?return_to=<путь|https://host/путь>[&visitor_session_id=<uuid>][&browser_key=<ключ>]`
  Браузерный переход (не XHR). Хост берётся из `return_to`, если он абсолютный, иначе из запроса
  (`X-Forwarded-Host`/`Host`); проверка как у Telegram-входа (`wwc.best`, `*.wwc.best`, витрины).
  **Хост входа в семье `.wwc.best`** (wwc.best и все партнёрские поддомены):
  → `302` на `accounts.google.com` (PKCE S256, scope `openid email profile`, `prompt=select_account`)
    **+ `Set-Cookie: wwc_oauth=<серверный ключ>`** (Domain `.wwc.best`, HttpOnly, Secure, SameSite=Lax, 15 мин).
    `browser_key` в query **не передавать** — он игнорируется.
  **Хост вне семьи** (витрины Академии на своём домене):
  → сайт генерирует ключ (16–128 символов `[A-Za-z0-9_-]`, лучше 32+ случайных байт), кладёт в
    sessionStorage и передаёт как `browser_key` в query; → `302` на Google, cookie **не ставится**;
    без ключа → `302 https://<host><return_to>#wwc-link-error=browser_key_required`.
  Общее: Google выключен → `302 https://<host><return_to>#wwc-link-error=google_disabled`; негодный путь
    (`/admin`, `/api`, чужой хост в return_to): `302 https://<host>/#wwc-link-error=invalid_return_to`
    (host — хост запроса). JSON `400 invalid_return_to` — только если сам хост запроса не наш.
- `GET /api/v1/content-access/login/google/callback?state=&code=` (или `&error=`) — ходит только Google.
  → `302 https://<host><return_to>#wwc-login=<одноразовый>` — личность привязана, хост в семье: **только**
    ссылка, cookie сессии не ставится; сайт гасит `#wwc-login=` как после бота (`BotLogin.astro`);
  → `302 https://<host><return_to>#wwc-link=<pending>` — нужен шаг на сайте: `GET …/login/pending/<pending>`
    скажет, что это — `awaiting_code` (экран «Код от Виктора») или `awaiting_claim` (Google уже привязан,
    витрина: сайт шлёт `POST …/login/link` с `browser_key` **без кода** — и входит). Для витрины callback
    **всегда** ведёт сюда (cookie до него не доходит, браузер подтверждает ключ из sessionStorage);
  → `302 https://<host><return_to>#wwc-link-error=<код>` — `google_denied` (человек отказал / без code),
    `google_token_invalid`, `email_unverified`, `code_locked` (5 неверных кодов за 15 минут);
  → `400 google_state_invalid` (JSON) — неизвестный, просроченный или повторный state;
  → `400 oauth_browser_mismatch` (JSON) — хост в семье, а cookie `wwc_oauth` нет или чужая (чужая
    ссылка; pending закрыт). Для витрины cookie не смотрится. Хоста для возврата в этих случаях нет.
- `GET /api/v1/content-access/login/pending/<pending>` → `200 {"ok": true, "provider": "google",
  "email_masked": "e***a@gmail.com", "display_name": "…", "status": "awaiting_code"|"awaiting_claim"}`;
  `404 pending_invalid`, `410 pending_expired`. Для подписи «привязываем Google e***a@gmail.com».
- `POST /api/v1/content-access/login/link` body `{"pending_id": "<из #wwc-link>", "code": "483 921",
  "browser_key": "<ключ sessionStorage>"}` — `code` обязателен для `awaiting_code`, не нужен для
  `awaiting_claim`. Браузер проверяется **строго по хосту входа, без подмены**: хост в семье — только
  cookie `wwc_oauth` (запрос с того же сайта, `credentials: 'include'`; `browser_key` в body не помогает);
  витрина — только `browser_key` в body (cookie не помогает). Пробел/дефис в коде допустимы.
  → `200 {"ok": true, "status": "authenticated", "expires_at": "<iso>"}` + `Set-Cookie` сессии (та же,
  что после Telegram; `/me` дальше работает как обычно), cookie `wwc_oauth` гасится.
  Ошибки: `400 oauth_browser_mismatch` (не тот браузер — pending закрыт, начать вход заново),
  `400 code_required`, `400 code_invalid`, `410 code_expired`, `409 code_used`, `429 code_locked`,
  `409 identity_taken` (этот Google уже у другого человека), `404 pending_invalid` (чужой/использованный
  pending — начать вход заново), `410 pending_expired` (15 минут прошли — начать заново).

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
- `PLATFORM_LOGIN_CODE_SECRET` (новый, ≥32 случайных символа, **одинаковый у API и worker**, в обоих .env):
  HMAC кодов входа; пусто → берётся `PLATFORM_MEDIA_SIGNING_SECRET`. Задать до первого «код входа».

## Миграция V34 (применяет лид)

`postgres/sql/platform_login_identities_v34.sql`: `account_identities`, `login_link_codes`, `login_pending`
(RLS `platform_current_tenant_id()`, `lock_timeout 5s`), бэкфилл Max из `lead_actor_channels`. Идемпотентна
(в тесте применяется трижды). После ревью в тот же файл добавлены `login_pending.browser_key_hash`,
`login_pending.site_key_hash` (в `create table` и `add column if not exists`) и статус `awaiting_claim`
(именованный constraint drop/add) — файл самообновляющийся, повторный прогон доведёт таблицу. Зарегистрирована: testkit, `apply_staging_platform_all.ps1`, `staging_proof_lib`,
`EXPECTED_APPLY_COUNT=57`, `schema_requirements` (core). Без неё новый код при старте пишет
`schema_compatibility_missing_tables`; релизный гейт не пустит.

## Что проверено на 8bdce22 (повторное ревью P0)

- `test_login_identities.py` — 68 passed; новые: ключ из query не становится cookie на хосте семьи;
  витрина — ключ отдельно, cookie нет, без ключа `browser_key_required`; `presented_key`: хост семьи —
  body без cookie → отказ, витрина — cookie без body → отказ; callback после `signed_in` — только
  `#wwc-login`, без `wwc_content_session`. Задетые файлы вместе — 177 passed.
- PostgreSQL: `test_login_identities_postgres.py` passed — атака из ревью end-to-end (жертве навязан K →
  cookie жертвы серверная ≠ K; callback атакующего ни с K, ни с cookie жертвы не проходит; K нигде не
  сохранён), хост семьи: body-ключ без cookie → отказ; витрина: «совпадающая» cookie без body → отказ,
  посторонняя cookie в callback витрины — без ложного mismatch, claim с body-ключом проходит.
- Полные прогоны на 8bdce22: unit `pytest` — **26 failed / 4 errors / 2230 passed / 50 skipped** (тот же
  известный набор, новых падений нет); все 33 файла `*_postgres` на одноразовом кластере — **49 passed, 0 failed**.

## Что проверено (на 0598976)

- `pytest tests/test_login_identities.py tests/test_max_webapp.py tests/test_content_access.py
  tests/test_schema_requirements.py tests/test_staging_sql_order.py tests/test_cabinet_bot.py` — 174 passed
  (из них `test_login_identities.py` — 65).
- Настоящий PostgreSQL (`scripts/run_postgres_integration_tests.ps1`, одноразовый кластер):
  `tests/test_login_identities_postgres.py` — passed (миграция ×3, бэкфилл, HMAC кодов, коды, Google
  start→callback→link с подделанным обменом, привязка к браузеру: чужая/отсутствующая cookie в callback и
  чужой ключ в link → отказ, claim-путь без кода для хоста вне семьи, повторный вход, identity_taken с откатом
  захвата, отвязка, Max-код, замок 5 попыток держится всё окно и для Google, 10 параллельных попыток →
  ровно 5, 2 параллельных redeem → 1, чистка worker, RLS).
  Полный прогон всех 33 файлов `*_postgres` (V34 в общем списке testkit) — **49 passed, 0 failed**.
- Полный `pytest` (unit): до среза 28 failed / 4 errors / 2149 passed; после 0598976 — **26 failed /
  4 errors / 2227 passed / 50 skipped**. Оставшиеся падения — те же известные (build/golden/staging-harness,
  acceptance lab, showcase bot ×2, wwc_markets RLS без DSN, lead owner routing); новых нет.
- На настоящем PostgreSQL за два прогона найдены и исправлены три вещи, которые подделки не ловили:
  счётчик попыток и закрытие pending откатывались вместе с ошибкой (теперь — своей транзакцией до raise);
  «failed» строка от чужого браузера запирала человеку его же Google (замок теперь только по сумме попыток).

## Что не проверено

- Настоящий Google OAuth (обмен кода, реальный id_token) — нужен OAuth client владельца и staging.
- Max на телефоне: реальный `initData`, `openLink(browser_login_url)` — Site-сессия + владелец на staging.
- Миграция на общей базе staging/бой — применяет лид по регламенту.
- Поведение команд владельца при открытом диалоге заявки на сайт в боте (как и у `referral_admin`:
  диалоги стоят раньше в цепочке).
- Два настоящих браузера с одной cookie семьи `.wwc.best` (партнёрский поддомен → callback на wwc.best) —
  логика покрыта тестами, живой прогон — на staging.
- Rate-limit nginx на `/login/google/start` и `/login/link` — не делал (сервер, лид).

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

- f988861 — код, миграция, тесты (22 файла); 2761b27 — STATE.
- **0598976** — правки по ревью P0–P3 + два пункта Site (10 файлов: V34, rules/service/google/routes/bot,
  settings, jobs/worker, оба теста). Отправлено в `origin/core/login-identities-20261015`. В master не вливать без лида.
- Ревью-прогон (после 0598976): `pytest tests/test_login_identities.py` — 65 passed (ручки с cookie/ключом,
  подмена и отсутствие cookie в callback/link → отказ, бот через processor, worker);
  `test_login_identities_postgres.py` на одноразовом PostgreSQL — passed (браузер: чужая cookie/без cookie
  в callback и чужой ключ в link → отказ, штатный путь и claim-путь проходят; 10 параллельных попыток →
  5 code_invalid + 5 code_locked; 2 параллельных redeem одним кодом → 1 ok + 1 code_used; чистка; HMAC).
  Полные прогоны — см. «Проверено».
- 7160f53 — STATE после первого ревью.
- **8bdce22** — P0 повторного ревью: ключ браузера строго по режиму хоста (серверный ключ в cookie для
  семьи, `site_key_hash` для витрин, без подмены cookie↔body), после `signed_in` только `#wwc-login`
  (6 файлов: V34, rules/service/routes, оба теста). Отправлено в origin.

## Следующий шаг

Лид: V34 на общей базе по регламенту → `release_core.ps1 -Target staging` → Site-сессия (fixtures по
контракту выше) → владелец: код из бота → Google на staging (нужен OAuth client и три переменные) →
Max на телефоне → бой (Core, затем сайт). Сосед по Академии предупреждается перед релизом Core.
