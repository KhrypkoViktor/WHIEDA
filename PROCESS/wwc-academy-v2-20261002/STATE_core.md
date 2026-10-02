# Академия v2 — STATE Core-сессии (02.10.2026)

Исполнитель: Core-agent. ТЗ: `TASK.md` лида (разделы 4–7, 10, 11). Сайт (`03_Website/`) не трогаю, не деплою.
Файл лежит в ветке (`PROCESS/wwc-academy-v2-20261002/STATE_core.md`): запись в базовый checkout
`D:\Projects\WHIEDA` из worktree запрещена хуком сессии.

## Ветка и база

- Ветка: `core/academy-v2-lms` от `origin/master` = `34d2c85` (02.10.2026).
- Worktree: `D:\Projects\WHIEDA\.claude\worktrees\hopeful-bohr-8bc3f5` (репозиторий WHIEDA, Core в `backend/platform-api`).
- Подпись: `Core-agent <core@whieda.local>`.

## Базовая линия тестов (origin/master, до правок)

`python -m pytest -q -p no:cacheprovider tests` → 22 failed, 4 errors (всего 26, не 31), 1809 passed, 21 skipped.
Все падения — golden/parity/product_discovery/markets/knowledge_pack/acceptance_lab/structured_sync/
website_lead_owner_routing (окружение/живые снимки). Список сохранён, сравню в конце.

## План (порядок из задачи)

1. [x] Миграция V19 `postgres/sql/platform_academy_lms_v19.sql` + testkit MIGRATIONS + schema_requirements.
2. [x] Правила открытия и завершённость (`app/academy/rules.py`, чистые функции).
3. [x] API ученика (модули, замки, файлы, домашка, эфир, подписанные ссылки).
4. [x] Медиа: chunked upload, воркер ffmpeg 720p + постер, подписанные ссылки, fallback через API с Range.
5. [x] API автора (markdown → html через `markdown` + `nh3`), зависимости в pyproject, ffmpeg в Dockerfile.
6. [x] Домашки и уведомления в бот через outbox.
7. [x] «полка» → «Академия» в текстах бота/API + тест в `test_bot_copy_audit.py`.
8. [x] `load_bundle.py`: модули из `module_title`, картинки бандла → `academy_media`.
9. [x] `backend/deploy/core/nginx-academy-media.conf` (файл для лида, не применять).

## Решения по ходу (для лида и Site-сессии)

- **Курс без доступа** (`GET /courses/{slug}`): 200 с программой, `course.locked=true`, `course.lock_reason`
  (`purchase_required`/`pro_required`), `author_contact`; у каждого урока `locked=true`, `lock_reason`
  `purchase` (и для PRO-курса: коды урока — ровно ТЗ §5, различие — в `course.lock_reason`). `course.next_lesson` у закрытого курса — первый урок (старый сайт покажет замок урока,
  а не «курс пройден»). Бот по-прежнему получает 403 замка курса.
- **Урок**: у каждого — `number` (порядковый для показа), `complete` (done + принятая обязательная домашка),
  `assignment_status` (`null` — у урока нет домашки | `none` — не сдана | `submitted` | `accepted` | `returned`),
  `opens_at` (ISO, когда известно), `kind` `lesson|live`, `live_at`. Закрытый урок: 403
  `{"error":"lesson_locked","lock_reason":…,"opens_at":…}`.
- **Автор/владелец видят свой курс без замков** (и доступа, и расписания) — превью целиком.
- **next_lesson**: первый открытый урок, где ученику есть что делать; урок с домашкой «на проверке» пропускается.
- **Сдача домашки = «Сделал»** для урока (иначе ученик мог застрять: принято, но не отмечено).
- **Модули старого импорта**: уроки без `module_id` группируются по `module_title` (модуль `module_id=null`,
  открыт). Данные в миграции не переносятся; `load_bundle.py` создаёт модули при следующей загрузке.
- **Цена**: `academy_courses.price_wusd_minor` = сумма в сотых `price_currency` (только показ), API отдаёт
  `price: {amount, currency}`; валюты WUSD, BYN, RUB, USD, EUR, KZT.
- **Медиа**: ключи `whieda/academy/<media_id>/original.<ext>`, видео `720.mp4` + `poster.jpg`; имя файла
  пользователя в путь не попадает. Ученик загружает только картинки (`/media/init`), автор — видео/файлы/картинки
  (`/author/media/init`). Кусок 5 МБ, докачка по `GET /media/{id}` → `chunks_received`.
- **Ссылка на медиа**: формат nginx `secure_link`: `/academy-media/<key>?u=<tg>&e=<exp>&s=<md5>`, 1 час, привязка к
  Telegram id. При `PLATFORM_ACADEMY_MEDIA_VIA_API=true` — `/api/v1/content-access/academy/media/files/<key>?…`
  (та же подпись), Core отдаёт с Range.
- **Очередь перекодирования**: строка `platform_outbox` `academy_media_transcode` в статусе `scheduled` без
  `binding_id` — старые сборки на общей базе её не трогают (они помечают незнакомые `pending` как `done`).
  Одна задача на всю базу (advisory lock + heartbeat). Битое видео — `failed` сразу; исходник после ошибки
  остаётся на диске.
- **Уведомления**: outbox `academy_hw_submitted` / `academy_hw_reviewed`, бот = `PLATFORM_ACADEMY_NOTIFY_BINDING`
  или первый из `PLATFORM_SCHEDULED_NOTIFY_BINDINGS`; кнопка входит на сайт в момент отправки.
  Ссылка автору — `/academy/author/?view=inbox&submission=<id>` (query, не `#inbox`, см. вопрос 1).
- **Автор** = есть строка `academy_shelf` (любой статус) или свой курс; владелец/preview-админ — автор всех курсов.
  Новый курс — черновик, доступ `purchase`; `pro` ставит только владелец. Удаление урока = архив.
- Доп. ручки сверх ТЗ (нужны редактору): `GET /author/courses/{slug}` (структура), `GET …/lessons/{lesson_id}`,
  `POST /author/preview {markdown}` → `{html}`; ученику — `POST /media/init` и т.д. для фото домашки.

## Ручки для Site-сессии (префикс `/api/v1/content-access/academy`, сессия входа как сейчас)

Ошибки: `{"ok": false, "error": "<код>", …}` с HTTP-статусом; ответы `Cache-Control: private, no-store`.

Ученик:
- `GET /courses` → `courses[]`: `slug, title, subtitle, kind, access_rule, lessons_total, lessons_done,
  locked, lock_reason, cover_url, price{amount,currency}|null, author_name, author_contact?`.
- `GET /courses/{slug}` → `course{slug,title,subtitle,kind,description_html,cover_url,price,author_name,locked,
  lock_reason,author_contact?,lessons_total,lessons_done,progress_pct,next_lesson,started_at}`,
  `modules[]{module_id,title,position,locked,lock_reason,lessons[]}`, `lessons[]` (плоско, как раньше).
  Урок: `slug,position,number,module_id,module_title,title,short_title,result,minutes,kind,live_at,done,complete,
  locked,lock_reason,opens_at,assignment_status`.
- `GET /courses/{slug}/lessons/{ls}` → `lesson{…, body_html (очищен, картинки — подписанные ссылки), checklist,
  video{provider:'file',media_id,status,id(url),poster,duration_sec} | {provider,id}, files[]{media_id,name,size,
  mime,url}, live_url (только kind=live), assignment{prompt_html,required}|null, my_submission{submission_id,status,
  text,media[],author_comment,created_at,reviewed_at}|null}`, `prev`, `next`. Закрыт: 403 `lesson_locked`
  (`lock_reason`, `opens_at`) или 403 `purchase_required`/`pro_required` (+ `lock_reason`, `author_contact`).
- `POST /courses/{slug}/lessons/{ls}/done {done}` → `lessons_total, lessons_done, progress_pct, next_lesson{…}`.
- `POST /courses/{slug}/lessons/{ls}/submission {text, media_ids[]}` → `submission{…}`, прогресс.
  Ошибки: `empty_submission`, `bad_media`, `text_too_long`, `too_many_files`, `assignment_not_found`,
  `already_accepted` (409), `lesson_locked`.
- `GET /media/{id}/url` → `url`, `poster_url?`, `expires_in` (сек). 403 `media_forbidden`.
- Фото домашки: `POST /media/init {name,size,mime,kind:"image"}` → `media_id, chunk_size, chunks_total,
  chunks_received`; `PUT /media/{id}/chunks/{n}` (тело — байты куска, кусок n с 0, последний короче);
  `POST /media/{id}/complete` → `status` (`ready`), `url`; `GET /media/{id}` → статус и `chunks_received`
  для докачки. Ошибки: `bad_mime`, `too_large` (413), `bad_chunk_size`, `upload_incomplete` (`missing`),
  `bad_image` (422), `too_many_uploads` (429), `upload_not_allowed` (403 — нет ни одного открытого курса),
  `daily_limit` (429, `limit_bytes`: ученик 200 МБ/сутки, автор 20 ГБ/сутки).

Автор (`/author/…`, 403 `not_author` — нет кабинета):
- `GET /author/courses` → `courses[]` (+ `lessons_total, students, submissions_pending`), `shelf{active,paid_until}`,
  `is_admin`. `POST /author/courses {title, slug?, subtitle?}` → `course` (черновик, `purchase`).
- `GET /author/courses/{slug}` → `course{… description_md, cover_media_id, status}`, `modules[]{module_id,title,
  position,unlock,lessons[]{lesson_id,slug,position,title,kind,status,live_at,unlock,has_assignment,
  submissions_pending,video}}`, `unassigned_lessons[]`, `can_publish`.
- `PATCH /author/courses/{slug}` — любые из `title, subtitle, description_md, cover_media_id, access_rule
  (free|purchase; pro — владелец), price (число|null), currency (WUSD|BYN|RUB|USD|EUR|KZT), status (draft|review|published)`.
  Автор: `published`/`review` → курс `review` (премодерация), без оплаченной Академии автора → 403 `shelf_inactive`;
  любая правка курса в `review` возвращает его в `draft`. Владелец/админ: `published` сразу. В `course` —
  `review_note` (причина возврата) и `review_requested_at`. Решение владельца на сайте:
  `POST /author/courses/{slug}/review {status: published|returned, comment}` (вернуть — с причиной;
  не на проверке → 409 `not_in_review`, не владелец → 403 `not_owner`). Лишнее поле → 422.
- Модули: `POST …/modules {title, unlock?}`, `PATCH …/modules/reorder {order:[module_id…]}` (весь список),
  `PATCH …/modules/{id} {title?, unlock?}`, `DELETE …/modules/{id}` (409 `module_not_empty`).
  `unlock`: `{"type":"open"}` | `{"type":"after_prev"}` | `{"type":"date","at":"2026-10-10T10:00"}` (без пояса —
  UTC+3) | `{"type":"days_after_start","days":7}`; ошибка → 400 `bad_unlock`.
- Уроки: `POST …/modules/{id}/lessons`, `PATCH …/modules/{id}/lessons/reorder {order}`,
  `GET|PATCH|DELETE …/modules/{id}/lessons/{lesson_id}` (урок ищется по курсу и lesson_id; удаление = архив).
  Поля: `title, short_title, body_md, video ({media_id} | {provider: kinescope|youtube|rutube|vk, id} | null),
  files [media_id…], kind (lesson|live), live_at, live_url (https), unlock (null — как у модуля),
  assignment ({prompt_md, required} | null), status (draft|published), module_id (перенос)`.
  Картинка в тексте: `![подпись](media:<media_id>)`.
- `GET /author/courses/{slug}/students` → `students[]{name,username,access,started_at,lessons_total,
  lessons_complete,progress_pct,last_activity_at,submissions_pending}`.
- `GET /author/submissions?status=submitted|accepted|returned|all&course=<slug>` → `submissions[]{submission_id,
  status,text,media[],author_comment,created_at,reviewed_at,student_name,student_username,course_slug,course_title,
  lesson_slug,lesson_title}`; `POST /author/submissions/{id}/review {status: accepted|returned, comment}`
  (вернуть — с комментарием, иначе 400 `comment_required`; повторно — 409 `already_reviewed`).
- `POST /author/preview {markdown}` → `html` (тот же рендер и очистка, что при сохранении).
- Загрузки автора: `POST /author/media/init {name,size,mime,kind: image|file|video}` (видео ≤ 4 ГБ, файл ≤ 200 МБ,
  картинка ≤ 20 МБ), `PUT /author/media/{id}/chunks/{n}`, `POST /author/media/{id}/complete` (видео →
  `processing`, потом `ready` с `url`/`poster_url` или `failed` с `error`), `GET /author/media/{id}`.

- **access_rule**: в V19 ограничений на `access_rule` нет (старое из V1 — `pro|purchase|free` — не трогал);
  автор выбирает правило в коде (`author.py`), неизвестное правило код считает закрытым (как `purchase`).
  Будущее `team:<ref>` — новой миграцией (пересоздать check из V1, как V15 для `source`) + ветка в
  `service.course_lock_reason`.
- **Общие с соседними ветками файлы** (`schema_requirements.py`, `postgres_testkit.py`, `jobs/worker.py`,
  `Dockerfile`, `settings.py`, `pyproject.toml`) — только добавленные строки (`git diff --numstat` — 0 удалений).

## Ревью (независимый ревьюер, 02.10) — найдено и исправлено

1. Доступ к медиа по упоминанию `media:<id>` текстом в описании/уроке своего открытого курса открывал чужой
   файл всем → доступ теперь только по настоящей ссылке `src/href="media:<id>"` внутри тега, проверка владельца
   видит и упоминания текстом. Регрессионный тест падает на старом поведении.
2. Загрузки могли забить общий диск → фото домашки только при открытом курсе, суточный объём на человека,
   уборка брошенных загрузок воркером раз в час.
3. Сбой heartbeat во время ffmpeg перепланировал задачу при живом ffmpeg → ожидание доводит поток до конца.
4. Мелкое: `resolve_media` падал на тексте `src="media:…"` без ссылки; кабинет автора не чистил HTML на выдаче;
   гонка адреса при создании курса (500 вместо 409); ffmpeg/ffprobe — только протокол `file` и только
   контейнеры видео (HLS/concat под видом видео — отказ до кодирования).

## Вопросы лиду (одной строкой)

1. ТЗ п.7: ссылка автору `/academy/author/#inbox` — `with_site_login` заменяет фрагмент на `#wwc-login=…`, сделал `?view=inbox&submission=<id>`; Site-сессии читать query.
2. ТЗ п.5 «HMAC», п.6 «nginx secure_link» — модуль secure_link умеет только md5(строка+секрет); сделал формат secure_link (один и для nginx, и для fallback в API). Нужен строго HMAC — тогда nginx через auth_request в Core, скажите.
3. Ответ лида 02.10: нужна премодерация — сделана (строка в «Сделано»). Вопросы 1 и 2 лид принял как есть.
4. Решено лидом 02.10: правка опубликованного курса повторной проверки не требует (опечатки — без ожидания; HTML чистится на сервере); снимает владелец командой «вернуть <адрес> <причина>» (см. «Сделано»).

## Действия лида перед staging (инфраструктура, сам не делал)

1. Миграция `postgres/sql/platform_academy_lms_v19.sql` на общую базу (повторный прогон безопасен,
   `lock_timeout 5s`), запись в `APPLIED.log`. Проверить права роли API на новые таблицы
   `academy_modules/assignments/submissions/media` (как было для V15). Без миграции релизный гейт
   (`check_schema_compatibility.py`) не пустит код — таблицы добавлены в `schema_requirements.py`.
2. Каталог `install -d -m 0755 /opt/whieda-platform-core/media/academy` на сервере Core; compose из ветки
   (сервер берёт compose из своего `src/deploy`, релиз его не везёт): том в api+worker боя и в api staging,
   лимиты боевого воркера 1.5 CPU / 1536M (было 0.5 / 384M — под ffmpeg мало).
3. `.env` Core (бой и staging): `PLATFORM_MEDIA_SIGNING_SECRET` (≥ 32 символа, один на оба) и
   `PLATFORM_ACADEMY_MEDIA_VIA_API=true`, пока nginx не применён (иначе ссылки `/academy-media/` — 404).
   Staging: `PLATFORM_ACADEMY_NOTIFY_BINDING=wwc-cabinet-staging-bot`, если нужны уведомления о домашках
   на staging (иначе их шлёт только боевой бот, если у боя задан `PLATFORM_SCHEDULED_NOTIFY_BINDINGS`).
4. Образ теперь ставит `ffmpeg` (apt, образ api тоже вырастет — образ один на api и worker).
5. `backend/deploy/core/nginx-academy-media.conf`: блок A — шлюз Core (secure_link + alias, том медиа
   в docker-nginx-1 только чтение), блок B — nginx сайта (`/academy-media/` и
   `/api/v1/content-access/academy/` с `client_max_body_size 6m`; у общего блока content-access 16k —
   куски загрузки без блока B получат 413). Проверить `client_max_body_size` у `/whieda-platform/` в шлюзе.
6. Название тарифа в базе `academy_shelf_3m` («Полка Академии на 3 месяца», если вставлено) — переименовать
   («Автор Академии на 3 месяца»): оно приходит в сообщение «Оплата подтверждена: …» из базы, не из кода.

## Сделано / проверено

- 02.10, по ответу лида: премодерация курсов сторонних авторов — автор «Опубликовать» → `review` (статус в V19),
  владельцу в бот карточка «Курс на проверку: «…», автор …, N уроков» с «Опубликовать» / «Вернуть» (причина —
  ответом на вопрос бота или «вернуть <адрес> <причина>») и «Посмотреть курс», автору — ответ с кнопкой в кабинет
  (outbox); правка на проверке → `draft`; владелец публикует сразу; то же решение — `POST /author/courses/{slug}/review`.
- 02.10, по слову лида: «вернуть <адрес> <причина>» и кнопка «Вернуть» снимают и опубликованный курс — черновик, автору «↩️ Курс «…» снят с публикации: …» с кнопкой «Открыть кабинет», владельцу «снят с публикации»; черновик → «и так в черновике» (409 `not_published`); свой курс владелец снимает без сообщения себе.

Коммиты ветки `core/academy-v2-lms` (от `34d2c85`): миграция V19; правила открытия; markdown + nh3;
ученик (модули, замки, медиа урока); загрузка кусками + перекодирование; домашки + уведомления;
кабинет автора; HTTP-ручки; «полка» → «Академия»; бот и замки; load_bundle/build_bundle; nginx/compose/Dockerfile.

Проверено локально (Windows, Python 3.14, PostgreSQL из scoop, ffmpeg 9.0.1):
- `python -m pytest -q -p no:cacheprovider tests` → 22 failed, 4 errors, 1913 passed, 29 skipped.
  Список падений совпадает с origin/master один в один (26 старых, новых нет); +104 новых теста.
- `pwsh backend/platform-api/scripts/run_postgres_integration_tests.ps1` → 28 passed, 0 failed
  (8 новых: премодерация курса; лимиты загрузок и уборка брошенных; V19 двойной прогон + RLS; ученик; загрузка + перекодирование настоящим ffmpeg 1080p→720p
  с постером; домашки + уведомления; кабинет автора; load_bundle).
- Фокус-тесты гейта `release_core.ps1` (тот же список) → 182 passed.
- `python .github/scripts/check_zone.py --base origin/master` → ZONE CHECK OK (zone: core).

Не проверено:
- nginx-конфиг на живом nginx (`nginx -t`, secure_link) — локально нет nginx/Docker; формат подписи
  покрыт юнит-тестом по алгоритму nginx (`test_signature_is_the_nginx_secure_link_md5`).
- Перекодирование в контейнере с лимитами 1.5 CPU / 1536M и длинным видео (40 мин) — время и память не мерил.
- Уведомления в настоящий Telegram и вход по кнопке — только с моками отправки.
- Сайт (`03_Website/`) не трогал; контракт ответов — в «Решениях» выше.

Открыто (не входило или отложено):
- Исходник видео после неудачного перекодирования остаётся на диске (для разбора руками).
- Общей квоты места на автора нет — только суточный объём (20 ГБ) и до 20 незавершённых загрузок.
