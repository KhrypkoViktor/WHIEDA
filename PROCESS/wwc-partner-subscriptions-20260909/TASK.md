# WWC: подписка партнёра, ручной учёт платежей и закрытый доступ

**Task ID:** `wwc-partner-subscriptions-20260909`
**Версия:** 1.1
**Дата:** 2026-09-09
**Статус:** утверждённое ТЗ на разработку
**Владелец продукта:** Виктор Хрипко
**Среда выпуска по этому документу:** локально и staging. Production только после отдельного прямого разрешения Виктора.

## 1. Зачем делаем

Нужен простой коммерческий контур персональных сайтов WWC:

1. Виктор получает оплату вне платформы и вручную сообщает об этом существующему Telegram-боту.
2. Бот записывает платёж и продлевает доступ конкретного партнёра ровно на три календарных месяца.
3. Пока подписка действует, партнёрский субдомен, закрытые материалы и повторные цены доступны.
4. После окончания оплаты действует `grace` ровно три дня без урезания функций.
5. После `grace` весь партнёрский субдомен перенаправляется на `https://wwc.best/`, закрытые материалы и повторные цены перестают открываться, новые заявки больше не закрепляются за просроченным партнёром.

Это **не платёжная система, не касса и не бухгалтерия**. Платформа не принимает деньги, не проверяет банковскую операцию, не формирует чек и не отправляет отчёт в ФНС. Она хранит внутреннюю запись о платеже, который Виктор уже проверил самостоятельно.

## 2. Решения владельца, которые нельзя переосмысливать

- Стандартное продление всегда равно **трём календарным месяцам**.
- `grace` всегда равен **трём суткам**.
- В течение `grace` сайт и оплаченные функции работают полностью.
- После `grace` **любой URL** просроченного партнёрского субдомена получает временный redirect на соответствующий путь основного домена WWC.
- Платёж подтверждает только Виктор. Роли кассиров, операторов и выбор поля «кто подтвердил» не нужны.
- Команда боту содержит минимум данных: партнёр, сумма, валюта. Дату и время ставит сервер.
- В MVP разрешены только две валюты оплаты сайта: `RUB` и WHIEDA-доллары.
  Пользователь пишет и видит `W$`; в базе хранится безопасный код `WUSD`.
- Фактические оплаты обрабатываются вручную вне системы.
- Повторные цены относятся к закрытому оплаченному доступу.
- Презентации, курсы, шаблоны визиток, печатные материалы и другие внутренние файлы относятся к закрытой библиотеке партнёра.

## 3. Что не входит в этот релиз

- эквайринг, Robokassa, банковские webhooks и автопроверка переводов;
- онлайн-касса, чеки, ОФД, налоговые отчёты и интеграция с ФНС;
- рассрочка, промокоды, возвраты денег и частичная оплата;
- тарифный конструктор, периоды на один, шесть или двенадцать месяцев;
- третья валюта, конвертация валют и курсы обмена;
- кабинет нескольких кассиров и делегирование права подтверждать платёж;
- автоматические списания;
- полноценная CMS для загрузки и редактирования учебных материалов;
- production deploy.

Если позднее понадобится один из этих пунктов, он оформляется отдельным ТЗ и не усложняет MVP заранее.

## 4. Обязательные источники перед началом

Исполнитель читает только следующий маршрут:

1. `AGENTS.md`.
2. `WHIEDA_FILE_MAP_CURRENT.md`.
3. Этот `TASK.md` и соседний `STATE.md`.
4. Локальный `AGENTS.md` конкретного репозитория или worktree.
5. `03_Website/wwc-best/docs/WWC_CLOSED_CONTENT_ACCESS_TZ_V1_2026-08-26.md` только для уже существующего Telegram challenge/session flow.
6. `03_Website/wwc-best/docs/PARTNER_ONBOARDING_STANDARD_V1.md` только для действующего контракта `ref_code`, поддомена и партнёрского профиля.
7. `WWC_SUBDOMAIN_RUNTIME_AND_WILDCARD_SSL_TZ_V1_2026-09-01.md` только для текущего поддоменного runtime.

Исторический файл `WWC_PERSONAL_SITE_SUBSCRIPTION_AND_ACCESS_TZ_V1_2026-08-29.md` физически отсутствует. Его не искать и не восстанавливать по догадкам: этот документ заменяет его для подписки, ручного платежа и закрытого доступа.

## 5. Текущее устройство, которое нужно сохранить

- Сайт/Astro/nginx и Platform Core находятся на разных серверах.
- Браузер обращается к Core только через разрешённые `/api/v1/*` маршруты nginx сайта.
- Все партнёры WHIEDA принадлежат одному tenant `whieda`.
- Партнёр определяется через `referral_profiles.ref_code` и связанного владельца в `lead_actors`.
- `referral_profiles.enabled` означает, что профиль опубликован и технически разрешён. Это не признак оплаты.
- `tenants.status` и `tenant_entitlements` действуют на весь tenant. Их запрещено использовать для подписки отдельного партнёра.
- Уже существует Telegram browser challenge и public content session со scope `telegram_verified`.
- Уже существующий scope `telegram_verified` не удалять и не менять задним числом.

## 6. Главные инварианты

1. Подписка хранится по ключу `(tenant_id, ref_code)`.
2. Просрочка одного партнёра никогда не меняет `tenants.status`, tenant-wide entitlement или работу других партнёров.
3. `referral_profiles.enabled` и платёжный статус независимы.
4. Платёжный статус не редактируется вручную. Он вычисляется из `paid_until` и текущего времени БД.
5. Источник времени один: PostgreSQL `now()` в UTC. Время клиента и Telegram не использовать.
6. Старый browser cookie не сохраняет платный доступ после окончания `grace`: каждый защищённый запрос перепроверяет актуальную подписку.
7. Username Telegram используется только для удобного поиска получателя платежа. Для авторизации используется числовой `telegram_user_id`.
8. Повторные цены и закрытые файлы не попадают в статический HTML, Astro `dist`, `public/`, client JS, View Source или общедоступный JSON.
9. Nginx-редирект является быстрым UX-слоем. Core независимо проверяет доступ, право на повторные цены и право на получение новой заявки.
10. При недоступности Core сервер сайта сохраняет последний корректный список активных субдоменов и не выключает всех партнёров.

## 7. Состояния подписки

Состояние вычисляет единая backend-функция. Копировать эту логику по контроллерам запрещено.

```text
no_subscription: записи нет
active:          now() < paid_until
grace:           paid_until <= now() < paid_until + interval '3 days'
suspended:       now() >= paid_until + interval '3 days'
```

На точной границе `paid_until` начинается `grace`. На точной границе `grace_until` начинается `suspended`.

Права по состояниям:

| Возможность | active | grace | suspended / нет записи |
| --- | --- | --- | --- |
| Партнёрский субдомен | работает | работает | redirect на `wwc.best` |
| Персональная страница «Стать партнёром» | работает | работает | доступна только общая версия на `wwc.best` |
| Закрытая библиотека | доступна | доступна | закрыта |
| Повторные цены | доступны | доступны | закрыты |
| Режим повторной корзины/калькулятора | доступен | доступен | закрыт |
| Новые заявки на партнёра | закрепляются | закрепляются | идут общему владельцу органики |
| Уже закреплённые заявки | без изменений | без изменений | без изменений |

## 8. Правило продления

Каждое подтверждение добавляет ровно три календарных месяца.

- Если состояние `active` или `grace`, новая дата считается от существующего `paid_until`.
- Если состояние `suspended` либо записи нет, новый период начинается от `now()` БД.
- Три месяца задаются `interval '3 months'`, а не 90 днями.
- `grace` не прибавляется к оплаченному сроку.
- Сумма платежа хранится для внутреннего учёта, но MVP не решает, достаточна ли она. Решение уже принял Виктор, когда нажал подтверждение.

Операция выполняется в одной транзакции с блокировкой строки подписки (`SELECT ... FOR UPDATE` либо эквивалентный атомарный upsert). Два параллельных подтверждения не должны потерять одно продление.

### 8.1. Однократный стартовый доступ для уже созданных сайтов

При первом включении системы всем **уже существующим реальным партнёрским субдоменам** выдать стартовый доступ по 21 сентября 2026 года включительно.

Точные границы:

```text
paid_until  = 2026-09-22 00:00:00 Europe/Moscow
grace_until = 2026-09-25 00:00:00 Europe/Moscow
```

Следовательно:

- весь день 21 сентября сайт находится в `active`;
- 22, 23 и 24 сентября сайт находится в `grace`;
- с 00:00 МСК 25 сентября неоплаченный сайт находится в `suspended` и редиректится на основной домен.

Правила стартовой выдачи:

1. В выборку входят все опубликованные реальные партнёрские профили tenant `whieda`, которым действующий Core-resolver выдаёт hostname вида `<partner>.wwc.best`.
2. `wwc.best`, `www`, `dev`, `staging`, `admin` и остальные технические hostname исключаются явным allow/deny contract.
3. Профили сайта, которых нет в Core/referral runtime, сначала проходят штатную сверку и onboarding. Запрещено создавать платёжную запись для несуществующего `ref_code`.
4. Уже существующий `paid_until`, который позже стартовой даты, не сокращать. Применять `max(current_paid_until, 2026-09-22 00:00:00 Europe/Moscow)`.
5. Операция идемпотентна: повторный запуск не добавляет новый срок и не меняет более позднюю дату.
6. Это бесплатный переходный доступ, а не полученный платёж. Строку в `partner_payment_ledger` не создавать и фиктивную сумму не записывать.
7. Перед применением команда выводит dry-run manifest: `tenant_id`, `ref_code`, hostname, старая и новая дата. После применения сохраняется тот же manifest с количеством изменённых строк и SHA.
8. Новые партнёры, созданные после стартовой выдачи, автоматически этот срок не получают. Их сайт включается только после реального ручного подтверждения оплаты Виктором.

Рекомендуемая отдельная команда Core:

```text
python -m app.subscriptions.seed_initial_access \
  --tenant whieda \
  --paid-until 2026-09-22T00:00:00+03:00 \
  --dry-run
```

Применение требует отдельного `--apply` и ожидаемого SHA dry-run manifest, чтобы состав партнёров не изменился между просмотром и записью.

## 9. Данные PostgreSQL

Миграции только добавочные и повторно применяемые. Не переписывать существующие таблицы tenant/referral/content access без необходимости.

### 9.1. `partner_subscriptions`

Обязательные поля:

```text
tenant_id          text        not null
ref_code           text        not null
paid_until         timestamptz null
created_at         timestamptz not null default now()
updated_at         timestamptz not null default now()
```

Ограничения:

- primary key `(tenant_id, ref_code)`;
- внешний ключ на действующий referral profile по реальной схеме проекта;
- `paid_until` хранится в UTC;
- отдельное поле `status` не создавать;
- `grace_until` в таблице не хранить: это всегда `paid_until + interval '3 days'`.

Поддомен не дублировать в платёжной таблице. Для edge-карты брать его из действующего канонического referral/subdomain runtime. Если аудит покажет несколько источников поддомена, исполнитель останавливает только edge-блок и фиксирует конкретное расхождение в `STATE.md`; SQL и бот можно продолжать.

### 9.2. `partner_payment_ledger`

Обязательные поля:

```text
payment_id             uuid        primary key
tenant_id              text        not null
ref_code               text        not null
amount_minor           bigint      not null check (amount_minor > 0)
currency               text        not null check (currency in ('RUB', 'WUSD'))
access_months           smallint    not null default 3 check (access_months = 3)
period_start            timestamptz not null
period_end              timestamptz not null
previous_paid_until     timestamptz null
source                  text        not null check (source = 'telegram_manual')
telegram_chat_id        bigint      not null
telegram_message_id     bigint      not null
telegram_user_id        bigint      not null
created_at              timestamptz not null default now()
```

Ограничения:

- foreign key `(tenant_id, ref_code)` на `partner_subscriptions`;
- unique `(tenant_id, source, telegram_chat_id, telegram_message_id)` для идемпотентности;
- `period_end > period_start`;
- строки ledger не редактировать и не удалять обычным runtime-кодом;
- дата, время и `telegram_user_id` записываются автоматически, а не вводятся Виктором.

`telegram_user_id` здесь является техническим аудитом и проверкой единственного разрешённого владельца команды. Интерфейса выбора «кто подтвердил» не делать.

### 9.3. Числовая Telegram-идентичность партнёра

В `lead_actors` добавить nullable-поле `telegram_user_id bigint`, если его ещё нет в фактической live-схеме, и partial unique index по `(tenant_id, telegram_user_id)` для ненулевых значений.

- Заполнять ID только из подтверждённого Telegram flow.
- Не считать username доказательством личности.
- `telegram_chat_id` не подменяет `telegram_user_id` в групповых чатах.
- Если в актуальной схеме уже есть эквивалентная однозначная связь владельца referral с числовым Telegram ID, повторную колонку не создавать; использовать существующую связь и записать её точный путь в `STATE.md`.

## 10. Команда оплаты в Telegram

Команда работает только в личном чате с ботом и только для Telegram user ID из env:

```text
PLATFORM_BILLING_OWNER_TELEGRAM_ID=<numeric Viktor Telegram user id>
```

Не переиспользовать список партнёров, username или публичный admin login как разрешение на оплату.

### 10.1. Формат

Основной формат без slash:

```text
оплата @onlineelena 3000 RUB
оплата @onlineelena 30 W$
```

Резервный стабильный идентификатор:

```text
оплата ref:onlineelena 3000 RUB
```

Правила парсинга:

- слово `оплата` без учёта регистра;
- ровно один идентификатор, одна сумма, одна валюта;
- `RUB`, `W$` и технический алиас `WUSD` без учёта регистра; другие валюты отклоняются;
- RUB: положительное целое число;
- W$: положительное число, допускаются точка или запятая и максимум два знака после разделителя;
- сумма нормализуется в minor units до записи;
- дата, время, период, способ оплаты, ФИО плательщика, номер чека и комментарий не запрашиваются;
- лишние поля вызывают короткую подсказку с правильным форматом и ничего не записывают.

### 10.2. Поиск партнёра

- `@username` ищется без учёта регистра по актуальному `lead_actors.telegram_username` и должен дать ровно одного владельца активного referral profile.
- `ref:<code>` ищется по точному `ref_code` внутри tenant `whieda`.
- Если совпадений ноль или больше одного, платёж не записывать.
- В ответе показать найденные имя, `ref_code` и партнёрский субдомен, чтобы Виктор увидел ошибку до продления.

### 10.3. Подтверждение

После разбора бот ничего не меняет, а показывает:

```text
Партнёр: Елена ...
Сайт: onlineelena.wwc.best
Платёж: 3 000 RUB
Было оплачено до: 12.09.2026 18:10 МСК
Станет оплачено до: 12.12.2026 18:10 МСК
Льготный срок до: 15.12.2026 18:10 МСК
```

Кнопки: `Подтвердить` и `Отмена`.

Только callback `Подтвердить` запускает транзакцию. Callback имеет короткий TTL и связан с исходными нормализованными данными. Повторное нажатие или повторная доставка Telegram update возвращает уже созданный результат и не добавляет ещё три месяца.

После успеха:

```text
Платёж записан.
ID: <короткий payment_id>
Доступ до: <дата/время МСК>
Grace до: <дата/время МСК>
```

БД хранит UTC; Telegram показывает время в `Europe/Moscow` с явной подписью `МСК`.

### 10.4. Минимальные служебные команды

```text
статус @onlineelena
статус ref:onlineelena
/due
```

`статус` показывает состояние, `paid_until`, `grace_until`, ref и субдомен. `/due` показывает три коротких списка: истекают в ближайшие семь дней, находятся в grace, suspended. Эти команды доступны только Виктору.

Автоматические напоминания партнёрам и рассылки в этом релизе не делать.

## 11. Backend-сервис подписок

Создать один сервисный модуль, который предоставляет остальному Core следующие операции:

- `get_subscription(tenant_id, ref_code, at=None)`;
- `get_subscription_state(tenant_id, ref_code, at=None)`;
- `is_paid_access_allowed(tenant_id, ref_code, at=None)`;
- `record_manual_payment(...)`;
- `resolve_paid_partner_by_telegram_user_id(tenant_id, telegram_user_id)`;
- `list_due_subscriptions(tenant_id, horizon_days=7)`;
- `export_active_partner_hosts(tenant_id, at=None)`.

Контроллеры, content access, referral routing и Telegram handler вызывают этот сервис. Собственные варианты вычисления `active/grace/suspended` в каждом модуле запрещены.

## 12. Telegram login и платный scope

Существующий `telegram_verified` означает только подтверждённый Telegram и продолжает работать для старых policy, где этого достаточно.

Добавить вычисляемое право `partner_paid`:

1. Browser challenge подтверждает числовой `telegram_user_id` существующим безопасным flow.
2. Core находит referral owner, связанного с этим ID.
3. Core проверяет подписку в момент каждого запроса.
4. Только `active` или `grace` дают `partner_paid=true`.

Клиент не может запросить или присвоить себе `partner_paid` через `requested_scope`. Requested scope является запросом интерфейса, а окончательное право вычисляет сервер.

Расширить `GET /api/v1/content-access/me` без выдачи лишних персональных данных:

```json
{
  "authenticated": true,
  "telegram_verified": true,
  "partner_paid": true,
  "subscription_status": "active",
  "paid_until": "2026-12-12T15:10:00Z",
  "grace_until": "2026-12-15T15:10:00Z"
}
```

Если Telegram подтверждён, но оплаченного партнёра нет, возвращать `partner_paid=false`; не раскрывать, кому принадлежит введённый username.

Любой endpoint закрытой библиотеки и повторных цен повторно проверяет live state. Срок cookie в 30 дней не является сроком подписки.

## 13. Закрытый раздел партнёра

Создать единый раздел, рабочий URL: `/partner/resources/`.

Категории первого релиза:

- презентации;
- обучение и курсы;
- шаблоны визиток;
- печатные материалы и каталоги;
- скрипты, чек-листы и рабочие документы;
- повторные цены.

Публичная страница может показать название раздела и кнопку входа через Telegram. Список закрытых материалов, прямые URL, тексты, файлы и повторные цены до авторизации не отдавать.

### 13.1. Реестр материалов

Создать серверную сущность `partner_library_items`:

```text
item_id        uuid primary key
tenant_id      text not null
slug           text not null
category       text not null
title          text not null
description    text null
kind           text not null
storage_key    text not null
mime_type      text not null
size_bytes     bigint null
status         text not null check (status in ('draft', 'published', 'archived'))
sort_order      integer not null default 0
published_at   timestamptz null
created_at     timestamptz not null default now()
updated_at     timestamptz not null default now()
```

Unique `(tenant_id, slug)`. В выдачу попадает только `published`.

Файлы лежат вне web docroot и Astro `public/`. Для MVP их добавляет контролируемый import/admin script с manifest; полноценную CMS не строить.

**Решение владельца 2026-09-09 (v1.1): хранилище — приватный S3-совместимый bucket.**

Закрытая библиотека позже станет мини-GetCourse: курсы, модули, уроки, видео, файлы и прогресс обучения. Поэтому хранилище проектируется сразу под это, но LMS в этом релизе не строится.

- Core определяет абстракцию `StorageBackend` с операциями `exists`, `stat` и `signed_url(storage_key, ttl)`.
- Production-адаптер — приватный S3-совместимый bucket. Bucket закрыт, публичного listing нет.
- Core хранит только метаданные и `storage_key`. После проверки `partner_paid` он выдаёт подписанный URL с коротким TTL.
- Большие файлы через Core не проксировать.
- `PLATFORM_TENANT_MEDIA_BASE_URL` (публичный media host) для закрытых материалов не использовать.
- Локальный каталог допускается только как dev/test adapter той же абстракции, не как production-хранилище.
- Ключи и endpoint bucket — только env/secret store.

API:

- `GET /api/v1/partner-library/items` — список после `partner_paid`;
- `GET /api/v1/partner-library/items/{item_id}/download` — поток файла после повторной проверки `partner_paid`;
- произвольный файловый путь от клиента не принимать.

Удалённые unlisted-ссылки без контроля доступа не считать защищёнными файлами. Если источник не поддерживает подписанные URL, зафиксировать это как ограничение конкретного материала, а не раскрывать URL в публичном HTML.

## 14. Повторные цены

Текущий статический источник `03_Website/wwc-best/src/data/repeat-purchase-prices.js` и любые его производные нельзя оставлять в клиентской сборке после включения закрытого доступа.

Исполнитель должен:

1. Найти все места, где repeat price импортируется, сериализуется или встраивается в HTML/JS/JSON.
2. Оставить на публичной `/price/` только розничные/первичные данные, разрешённые публично.
3. Сделать `/price/repeat/` оболочкой с Telegram gate; реальные цены загружаются только после `partner_paid`.
4. Добавить защищённый Core endpoint повторных цен либо расширить существующий price endpoint явным режимом `repeat`, который требует `partner_paid`.
5. Закрыть режим повторной корзины/калькулятора той же серверной проверкой.
6. Проверить собранный `dist`, View Source и JS chunks по нескольким контрольным повторным ценам: значений там быть не должно.

Скрыть блок CSS, удалить кнопку или поставить frontend-флаг недостаточно.

## 15. Поведение партнёрского субдомена после grace

Client-side redirect запрещён: HTML и ресурсы просроченного сайта не должны сначала загружаться в браузер.

### 15.1. Источник edge-состояния

Core формирует внутренний snapshot разрешённых партнёрских hostnames для состояний `active` и `grace`:

```json
{
  "tenant_id": "whieda",
  "generated_at": "2026-09-09T12:00:00Z",
  "version": "<monotonic/version hash>",
  "allowed_hosts": ["onlineelena.wwc.best", "fedorov.wwc.best"]
}
```

Endpoint только внутренний, с отдельным secret/mTLS по принятому инфраструктурному способу. Публично список не отдавать.

### 15.2. Синхронизация на сервер сайта

На site VPS создать отдельный скрипт и systemd timer раз в 60 секунд:

1. получить snapshot;
2. проверить HTTP status, JSON schema, tenant, дату генерации и подпись/secret;
3. построить новый nginx `map` во временном файле;
4. сравнить с текущим;
5. при изменении выполнить `nginx -t`;
6. только после успешной проверки атомарно заменить map и сделать reload;
7. записать version и результат в журнал.

При timeout, ошибке Core, пустом/битом snapshot или неуспешном `nginx -t` оставить последний корректный map. Запрещено заменять его пустым списком.

### 15.3. Redirect

- Active/grace hostname обслуживается как сейчас.
- Suspended/unknown partner hostname: `302 https://wwc.best$uri` без query string.
- Query string удаляется, чтобы старый `?ref=` не вернул просроченного партнёра в routing.
- Технические hostnames (`staging`, `dev`, `admin`, apex и другие системные имена) задаются отдельным allowlist и никогда не попадают под партнёрское правило.
- Первый релиз использует `302`, не постоянный `301`, потому что после оплаты субдомен снова включается.
- Допустимая задержка включения/выключения edge-слоя: до 60 секунд.

Работать только по фактическому `nginx -T`: конфиг в репозитории может отставать от live. Staging и production могут делить nginx process/listener; изменение общего `:443`, TLS, HTTP/2 или gzip этим ТЗ не разрешено.

## 16. Заявки и просроченный ref

Перед окончательным назначением новой заявки Core проверяет:

1. `referral_profiles.enabled = true`;
2. подписка referral находится в `active` или `grace`.

Если ref просрочен, отсутствует или выключен:

- новую заявку назначить текущему общему владельцу органического трафика tenant `whieda` (сейчас `viktor`, но брать из действующего server config/канона, не размазывать строку по коду);
- применить действующий канон `last-touch`: `assigned_owner_id` и `attributed_owner_id` записать равными общему владельцу органического трафика;
- исходный первый ref сохранить только в предназначенных для истории полях `initial_ref_code` / `first_ref_code`; он не получает владение или атрибуцию новой заявки;
- не присваивать просроченному партнёру active owner;
- не менять владельца уже существующих заявок;
- public ref lookup не представляет просроченного партнёра активным и возвращает одинаковый безопасный `referral_not_available` для отсутствующего, выключенного и suspended ref.

Старая ref-cookie или сохранённая ссылка не обходят эту проверку.

## 17. UI

Использовать действующие tokens и компоненты сайта. Основная CTA золотая. Новые произвольные зелёные кнопки, шрифты и отдельная тема запрещены.

Для партнёра после Telegram login показывать:

- `Доступ оплачен до <дата>` для active;
- `Льготный срок до <дата>` для grace;
- `Доступ приостановлен` и контакт/инструкцию по продлению для suspended.

Не показывать посетителю сумму, валюту, ledger, Telegram ID или внутренние причины отказа.

После успешного продления и обновления edge-map субдомен снова работает без пересборки Astro и без ручной правки nginx на конкретного партнёра.

## 18. Безопасность и журналирование

- Owner-only Telegram команды проверяют numeric ID до парсинга и обращения к данным.
- Секреты только в env/secret store, не в repo и не в Telegram-ответах.
- SQL параметризованный; username/ref не вставлять строковой конкатенацией.
- Callback подтверждения одноразовый, ограничен TTL и защищён от подмены суммы/ref.
- В лог не писать cookie, browser nonce, bot token и полные закрытые URL.
- Для payment event писать payment_id, tenant, ref_code, amount/currency, старый и новый срок, source update identity и результат.
- Доступ к файлу логировать по item_id и ref_code без содержимого файла.
- Rate limit на owner-команды мягкий; на content/file endpoints обязательный общий rate limit.
- Все ошибки пользователю короткие; подробности в структурном server log.

## 19. Порядок разработки

Каждый блок выполняется отдельным commit и отчётом. Не делать один общий commit сайта, Core, SQL и nginx.

### Блок 0. Read-only preflight

1. Зафиксировать root repo, website repo/worktree, branch, HEAD и исходный `git status --short`.
2. Проверить фактические таблицы referral/identity/content access.
3. Найти канонический источник соответствия `ref_code -> hostname`.
4. Найти все статические места утечки repeat prices.
5. Проверить реальный Telegram update routing и место owner-only handler.
6. Заполнить `STATE.md` точными файлами и тестовыми командами.

Никаких изменений live, миграций или deploy в блоке 0.

### Блок 1. SQL и сервис подписки

- миграции `partner_subscriptions`, `partner_payment_ledger`, числовая Telegram identity при необходимости;
- единый subscription service;
- идемпотентная dry-run/apply команда стартового доступа по 21 сентября включительно;
- manifest всех включённых реальных партнёрских субдоменов с SHA; технические hostname исключены;
- unit/integration tests границ дат, продления, конкурентности и идемпотентности;
- локальные fixtures active/grace/suspended.

### Блок 2. Owner-only Telegram учёт

- parser команды;
- точный resolver партнёра;
- preview и inline confirmation;
- атомарная запись;
- `статус` и `/due`;
- tests без реальной отправки Telegram и без live БД.

### Блок 3. Платный content access и repeat prices

- вычисляемый `partner_paid`;
- расширение `/me`;
- защищённый repeat-price API;
- удаление repeat data из статической сборки;
- закрытие repeat calculator mode;
- тест одной эталонной страницы на staging.

### Блок 4. Закрытая библиотека

- server registry и private storage;
- list/download API;
- `/partner/resources/` на существующем UI;
- controlled import script и пример manifest;
- один материал каждого требуемого типа для smoke, без массовой загрузки всего архива.

### Блок 5. Edge-map и redirect

- внутренний snapshot Core;
- безопасный sync script и systemd units;
- отдельный staging nginx include;
- fail-last-known-good;
- active/grace/suspended canary.

Общий listener `:443`, TLS, HTTP/2, gzip, DNS и production не менять.

### Блок 6. Сквозной staging canary

Использовать три тестовых referral:

- один active;
- один grace;
- один suspended.

Проверить оплату, обновление статуса, Telegram login, библиотеку, repeat prices, назначение заявок и redirect. После отчёта остановиться. Production только отдельной командой Виктора.

## 20. Что можно отдать младшему исполнителю параллельно

После завершения блока 1 младшему исполнителю можно дать только один изолированный пакет:

- UI `/partner/resources/` на mock API;
- manifest/importer закрытых материалов на локальных fixtures;
- поиск и тест отсутствия repeat prices в `dist`;
- тестовые fixtures active/grace/suspended.

Младшему запрещено менять SQL, Telegram ingress, auth/cookie, nginx, staging/prod и lead routing. Интеграцию делает ведущий разработчик.

## 21. Обязательные тесты приёмки

### Подписка и платёж

1. Новая подписка: подтверждение в `10:00` даёт `paid_until = now() + 3 months`.
2. Active: продление добавляет три месяца к старому `paid_until`.
3. Grace: продление добавляет три месяца к старому `paid_until`, а не к текущему времени.
4. Suspended: продление начинает новый период от текущего DB time.
5. Ровно в `paid_until` состояние `grace`.
6. Ровно в `grace_until` состояние `suspended`.
7. RUB и WUSD записываются в minor units без float; пользователю WUSD показывается как W$.
8. Третья валюта и отрицательная/нулевая сумма отклоняются.
9. Повторный Telegram update/callback не продлевает второй раз.
10. Два конкурентных подтверждения не теряют период.
11. Чужой Telegram ID не может вызвать preview, status, due или запись.

### Доступ

12. `telegram_verified=true` без оплаченного партнёра не даёт `partner_paid`.
13. Active и grace дают `partner_paid`.
14. Suspended не даёт `partner_paid` даже со старой 30-дневной cookie.
15. Прямой download без доступа возвращает `401/403`; с active/grace возвращает `200`.
16. Произвольный storage path не принимается.
17. В static `dist`, HTML source, JSON и JS chunks нет repeat prices и закрытых file URLs.

### Субдомен и заявки

18. Active hostname работает.
19. Grace hostname работает.
20. Suspended hostname на любом пути получает `302` на тот же путь `wwc.best` без query string.
21. После оплаты suspended-партнёра сайт восстанавливается не позднее 60 секунд без rebuild.
22. Ошибка Core или битый snapshot сохраняет last-known-good map.
23. System hostname не попадает под партнёрский redirect.
24. Новая заявка с suspended ref уходит общему organic owner.
25. Существующая заявка suspended-партнёра не переназначается.
26. Старая ref-cookie не обходит платёжную проверку.

### Регрессия

27. Старые policy со scope `telegram_verified` продолжают работать.
28. Admin auth и public content auth не смешиваются.
29. Публичная retail price page и первичный калькулятор работают без Telegram.
30. Новый партнёр создаётся стандартным onboarding pipeline, без ручного nginx `if` и без копии сайта.
31. Стартовый seed включает все реальные существующие партнёрские hostname и не включает технические.
32. Seed ставит `paid_until = 2026-09-22 00:00 МСК`, поэтому redirect начинается с 25 сентября 00:00 МСК после трёх полных суток grace.
33. Повторный seed ничего не продлевает и не создаёт payment ledger rows.
34. Seed не сокращает уже оплаченный срок, если он позже 21 сентября.
35. Для suspended ref `assigned_owner_id == attributed_owner_id == organic owner`, а исходный ref остаётся только в `initial_ref_code` / `first_ref_code`.

## 22. Остановки

Исполнитель обязан остановить затронутый блок и записать факты в `STATE.md`, если:

- не найден однозначный `ref_code -> hostname` source of truth;
- один Telegram numeric ID связан с несколькими партнёрами tenant;
- repeat prices требуются существующему публичному функционалу и их удаление ломает согласованный контракт;
- staging и production используют общий изменяемый nginx include/listener;
- для edge snapshot пришлось бы открыть публичный список всех партнёров;
- миграция требует destructive DDL;
- пересекаются файлы с активной параллельной задачей без выделенного worktree.

Остановка одного блока не запрещает продолжить независимый блок.

## 23. Отчёт исполнителя по каждому блоку

Отчёт должен содержать:

- repo/worktree, branch и base SHA;
- точный список изменённых файлов;
- migration names и способ rollback;
- команды тестов и фактический результат;
- что проверено локально, что на staging, что не проверено;
- ссылки на commit каждого блока;
- известные ограничения;
- следующий разрешённый шаг.

Фразы «готово» без SHA, тестов и названия среды не принимаются.

## 24. Definition of Done первого релиза

Релиз готов к отдельному решению о production только когда на staging доказан полный путь:

```text
Виктор пишет боту: оплата @partner <amount> <RUB|W$>
-> видит точного партнёра и новые даты
-> нажимает Подтвердить
-> ledger получает одну запись
-> подписка продлевается на 3 календарных месяца
-> партнёр входит через Telegram
-> видит библиотеку и повторные цены
-> его субдомен работает в active/grace
-> после grace весь субдомен уходит на wwc.best
-> просроченный ref больше не получает новые заявки
-> повторная оплата возвращает сайт в течение 60 секунд
```

При этом retail-каталог, основной сайт, другие партнёры, старый `telegram_verified` и admin auth проходят регрессию.

---

## 25. Результаты preflight блока 0 и уточнения к v1.1

Раздел добавлен 2026-09-09 после фактической read-only проверки кода и схемы.
Он не отменяет решения выше, а исправляет места, где v1.0 расходилась с runtime.

### 25.1. Что подтвердилось

- `lead_actors` действительно не содержит числового Telegram ID. Есть только
  `telegram_chat_id text` и `telegram_username text`, плюс `unique (tenant_id, telegram_chat_id)`.
  Пункт §9.3 обязателен и является настоящей добавочной миграцией.
- `content_access_sessions.telegram_user_id` существует. Цепочка `partner_paid`
  собирается без новых таблиц идентичности:
  cookie -> `content_access_sessions` -> `telegram_user_id` ->
  `lead_actors.telegram_user_id` -> `referral_profiles.owner_id` -> `partner_subscriptions`.
- `ALLOWED_SCOPES` в `app/content_access/service.py` равен `{"telegram_verified"}`.
  Требование §12 «клиент не может запросить `partner_paid`» соответствует коду:
  `partner_paid` остаётся вычисляемым и в `ALLOWED_SCOPES` не добавляется.
- Повторные цены реально утекают в статическую сборку. Контрольные значения
  `1575` и `5250` присутствуют в `dist/price/repeat/index.html`.
- Маршрутизация заявок — один SQL CTE в `save_lead` (`app/leads/service.py`) с двумя
  `left join referral_profiles ... and enabled = true`. Проверка подписки врезается
  ровно в эти два места, а не размазывается по контроллерам.

### 25.2. Исправления к тексту v1.0

1. **Ключ referral.** `referral_profiles.ref_code` объявлен как **глобальный**
   `primary key`, а не составной `(tenant_id, ref_code)`. Поэтому:
   - `partner_subscriptions` сохраняет составной primary key `(tenant_id, ref_code)`;
   - внешний ключ на referral возможен **только** как `ref_code references referral_profiles(ref_code)`;
   - составной FK на referral не создавать: такого уникального ключа в схеме нет.

2. **Место врезки Telegram-команды.** Порядок в
   `app/telegram/processor.py::_process_core_telegram_update_scoped`:
   `admin_login -> content_access -> callback -> start token -> onboarding -> newcomer -> navigation -> advisor`.
   Свободное слово `оплата` без правки попадёт в **advisor-LLM**. Обязательно:
   - owner-only billing handler вызывается **до** `handle_onboarding` и `handle_navigation_text`;
   - callback подтверждения разбирается **до** `handle_callback_query` каталога либо
     отделяется собственным префиксом `data`, который catalog-роутер не принимает;
   - handler сначала проверяет `PLATFORM_BILLING_OWNER_TELEGRAM_ID`, и только потом парсит текст.

3. **Slash-алиасы (решение владельца).** Кроме `оплата` и `статус` поддерживаются
   `/pay` и `/status` с той же грамматикой аргументов. Алиасы срабатывают только по
   **точному совпадению** команды в начале строки; свободный текст ими не перехватывается.

4. **RLS (решение владельца).** `tenant_connection` выставляет `app.tenant_id`, соседние
   таблицы работают под RLS. Обе новые таблицы обязаны получить
   `enable row level security` и политику по образцу `platform_tenant_rls_v1.sql`
   через `platform_current_tenant_id()`. Обязательные негативные тесты:
   tenant A не читает и не изменяет подписки и ledger tenant B.

5. **`access_months` (решение владельца).** Ограничение остаётся строгим:
   `check (access_months = 3)`. Заранее разрешать иные сроки нельзя.

6. **Порядок применения SQL (решение владельца).** Новый файл миграции обязан быть
   добавлен и в `postgres/scripts/apply_staging_platform_all.ps1`, и в `EXPECTED_ORDER`
   теста `backend/platform-api/tests/test_staging_sql_order.py`.
   Существующий дрейф этого списка (см. 25.4) в рамках задачи **не чинить**.

### 25.3. Источник поддомена: принятое решение

Стоп-условие §22 «не найден однозначный `ref_code -> hostname`» сработало: источников
четыре, и они расходятся.

- `app/theme_access/service.py::ISSUED_SUBDOMAIN_TO_REF` — 11 записей;
- `app/leads/service.py::SUBDOMAIN_TO_REF` — 2 записи, подмножество;
- `03_Website/wwc-best/src/data/referrals.js::subdomainToRef` — 13 записей,
  среди них `fedorov` и `dev`, которых нет в картах Core;
- `03_Website/wwc-best/src/data/referral-canary-fallback.js` — производная карта;
- `referral_profiles.public_profile->>'subdomain'` в БД, заполняется Partners_Ref sync.
- Отдельной SQL-колонки `subdomain` не существует.

**Решение владельца: инвертировать действующий Core-resolver, новую колонку не создавать.**

`app/ref/service.py::load_public_ref_by_subdomain` уже документирует приоритет
`public_profile->>'subdomain'` -> `ISSUED_SUBDOMAIN_TO_REF` -> сам `ref_code`.
`export_active_partner_hosts` использует тот же приоритет в обратную сторону:

```text
subdomain(ref) = public_profile->>'subdomain'
              -> REF_TO_ISSUED_SUBDOMAIN[ref]
              -> ref_code
```

Обязательные правила экспорта:

1. website-карты в runtime не читать; новую колонку не заводить — она стала бы пятым источником правды;
2. hostname нормализовать: нижний регистр, без завершающей точки, без пробелов;
3. системные имена `dev`, `staging`, `admin`, `www` и apex исключать явным deny-contract;
   `dev` партнёром не является, стартовый доступ не получает и в snapshot не попадает;
4. `fedorov` разрешается штатным fallback на `ref_code`, ручной записи не требует;
5. при **дубликате поддомена** у двух разных `ref_code` новый snapshot не публикуется:
   сохраняется last-known-good, факт пишется в лог и в `STATE.md`;
6. расхождение четырёх источников зафиксировано как техдолг и выносится отдельным ТЗ.

### 25.4. Состояние среды на момент старта

Эти факты не являются дефектами текущей задачи, но задают базу для отчётов.

- **Базовый прогон тестов Core не зелёный до начала работы:**
  `869 passed, 83 failed, 1 skipped, 35 errors` плюс 7 ошибок сбора.
  Причина — отсутствующие внешние файлы: `n8n/current/*`, `qa/telegram_golden/*`
  и три `.sql`. Профильные задаче файлы зелёные:
  `test_content_access.py`, `test_lead_attribution.py`, `test_theme_access.py`,
  `test_lead_idempotency.py`, `test_partner_runtime_reconciliation.py` — 65 passed.
  Отчёт по каждому блоку сравнивается с этой базой, а не с «всё зелёное».
- **`postgres/sql` неполон.** `test_staging_sql_order.py` ожидает 15 файлов,
  из них отсутствуют `platform_bot_binding_context_v1.sql`,
  `platform_cart_sessions_v1.sql`, `platform_wwc_markets_v1.sql`.
  `apply_staging_platform_all.ps1` перечисляет только 9 файлов и не содержит
  миграций leads. DDL таблиц `content_access_*` в репозитории отсутствует вовсе:
  он существует только внутри
  `03_Website/wwc-best/docs/WWC_CLOSED_CONTENT_ACCESS_TZ_V1_2026-08-26.md`.
- **Организация веток.** Root `wip/consolidation-20260831` опережает `master`
  на 101 commit и является фактическим стволом Core. Website `master` опережает
  `fix/fedorov-runtime-context` на 58 commits. Поэтому:
  Core — worktree от `wip/consolidation-20260831`;
  website — worktree от `master`.
- **Существующий env.** `PLATFORM_ADMIN_SUPER_TELEGRAM_IDS` уже есть.
  Переиспользовать его как право подтверждать платёж запрещено;
  `PLATFORM_BILLING_OWNER_TELEGRAM_ID` остаётся отдельной переменной.
- **Общий владелец органики** сейчас захардкожен строкой `'viktor'` в трёх местах
  SQL внутри `save_lead`. Просроченный ref обязан уходить к нему через
  единый конфигурируемый источник, а не четвёртой копией строки.

### 25.5. Результат блока 1

Блок 1 выполнен в ветке `core/partner-subscriptions-20260909`.

- Добавлена повторно применяемая миграция `platform_partner_subscriptions_v1.sql`:
  числовой Telegram ID партнёра, подписка, неизменяемый через runtime ledger,
  SQL-функции состояния и RLS обеих новых таблиц.
- Идемпотентность Telegram update ограничена tenant: один и тот же chat/message ID
  допустим у разных bot binding, но внутри tenant повтор не создаёт второй платёж.
- Реализованы единый service, атомарное продление с блокировкой строки, dry-run/apply
  стартового доступа с SHA и экспорт активных hostname с блокировкой коллизий.
- На локальном PostgreSQL доказаны: повторное применение миграции, точные границы
  active/grace/suspended, cross-tenant RLS, идемпотентный повтор и два конкурентных
  продления без потери периода.
- Профильный набор: `83 passed`; отдельный PostgreSQL integration: `1 passed`.

Обнаружен старый дефект чистой установки, не относящийся к подпискам:
`wwc_leads_p01_runtime_migration.sql` объявляет nullable `country_code` и
`region_code`, но включает их в primary key `lead_actor_roles`, после чего seed без
этих значений падает на `NOT NULL`. В этой задаче миграция не исправляется.
Интеграционный тест подписок создаёт минимальную реальную схему `lead_actors` и
`referral_profiles`, а `apply_staging_platform_all.ps1` сохраняет прежний контракт:
эти таблицы уже должны существовать на целевой staging-базе.

### 25.6. Результат блока 2

Блок 2 выполнен в ветке `core/partner-subscriptions-20260909`.

- Добавлена отдельная настройка `PLATFORM_BILLING_OWNER_TELEGRAM_ID`; список
  администраторов и username правом подтверждать платёж не являются.
- Команды `оплата`/`/pay`, `статус`/`/status` и `/due` распознаются только по
  точному первому слову, работают только в личном чате и обрабатываются до
  onboarding/navigation/advisor.
- Billing callbacks имеют собственный префикс и обрабатываются до каталога.
- Для preview добавлена таблица `partner_payment_intents`. Это не платёжный журнал:
  intent хранит нормализованные данные 10 минут и становится подтверждённым или
  отменённым. В `partner_payment_ledger` попадает только подтверждённая операция.
- Callback содержит только действие и UUID intent, укладывается в лимит Telegram
  64 bytes. Tenant, Telegram user ID и chat ID повторно проверяются по БД.
- Подтверждение intent, блокировка подписки, продление и запись ledger выполняются
  одним DB connection. Контракт отдельно проверен при `database_pool_max=1`.
- Unit/regression: `98 passed`; PostgreSQL integration: `1 passed`.

Shared staging, production webhook, реальные Telegram-сообщения и реальные
платежи не затрагивались.

### 25.7. Результат блока 5

Блок 5 реализован коммитом Core `042fc12`.

- Внутренний snapshot не открыт через публичный сайт и не принимает tenant из
  query/path. Tenant задаётся штатным host middleware; доступ требует отдельный
  edge-secret, а тело подписывается HMAC-SHA256.
- Сайт-VPS получает только список разрешённых active/grace host. Sync отклоняет
  неверную подпись, чужой tenant, старое или будущее время, изменённую схему,
  пустой список, технический/невалидный host и неверную версию.
- Обновление карты атомарно и fail-last-known-good. До замены проверяется отдельный
  кандидат, после замены весь nginx; reload происходит только после обоих тестов.
- Nginx gate использует `302 https://wwc.best$uri`, поэтому сохраняет путь и
  намеренно удаляет query string. Подключать его можно только в wildcard-vhost
  партнёрских сайтов после отделения точных технических vhost.
- Public ref и маршрутизация новых заявок теперь проверяют active/grace. История
  first-touch сохраняется, ownership просроченному партнёру не выдаётся, organic
  owner берётся из `PLATFORM_ORGANIC_OWNER_ID`.
- Unit/regression: `95 passed, 1 skipped`. Настоящий nginx и staging не менялись;
  canary на отдельном listener относится к блоку 6 и требует отдельного разрешения.

### 25.8. Локальный результат блока 6

- PostgreSQL integration canary добавлен коммитом `b9120b0` и выполнен вместе с
  тестом подписки: `2 passed`. Тест использовал временную БД на локальном сервере,
  после проверки сервер остановлен и тестовая БД удалена.
- Реальными INSERT доказана матрица lead routing active/grace/suspended и сохранение
  first-touch при переводе ownership просроченного ref к organic owner.
- Website-коммит `58c4f98` добавил видимые статусы и даты active/grace/suspended.
  Свежая Astro-сборка успешна; `248` unit и `14` browser-тестов прошли на desktop и
  mobile; SEO и private-data checks зелёные.
- Live smoke сохранил известный baseline `6/7`: production advisor отдаёт пустое
  тело. Задача подписок этот endpoint не меняет.
- Shared staging и production не изменялись. До окончательного DoD §24 остаётся
  настоящий staging deploy с секретами, S3, Telegram и isolated nginx canary.

### 25.9. Read-only preflight staging 2026-09-10

- Core staging отделён от production, отвечает `200` на `/health/ready`; сервисы
  `api`, `worker`, `redis` работают на staging compose и API-порту `8081`.
- Prerequisite `referral_profiles` и `lead_actors` есть. Таблицы подписок, ledger
  и библиотеки отсутствуют, поэтому миграции задачи на staging ещё не применялись.
- Новые billing/edge/S3 env-переменные отсутствуют. Numeric ID Виктора можно
  однозначно получить по `sunraysword`: найдена одна числовая связь, совпадающая
  ровно с одним staging super-admin. Значение в отчёты не выводить.
- `wwc_admin_staging_bot` существует, но `CORE_ROUTE_TELEGRAM=legacy`. Сквозная
  проверка owner-only оплаты требует отдельного staging cutover этого бота на Core.
- Website production находится на `61188ef`, staging на `556044f`, кандидат UI
  подписки `58c4f98` не развёрнут. Живой wildcard nginx пока не содержит
  `/api/v1/partner-library` и edge-gate.
- Nginx overlay в репозитории уже содержит правильный GET-only маршрут библиотеки;
  коммит `44789f9` добавил его регрессионный тест, `6 passed`.
- Для полного canary отсутствует один внешний ресурс: приватный S3-compatible
  bucket с credentials. Ни staging, ни production во время preflight не менялись.

### 25.10. Выбор и подготовка S3 2026-09-10

- Провайдер: Contabo Object Storage EU. Endpoint
  `https://eu2.contabostorage.com`, region `default`, addressing style `path`.
- Код подключения, env-шаблон, проверка private ACL, write/read/delete probe и
  загрузчик материалов добавлены коммитом `a243953`.
- Профильный набор подписок, content access, библиотеки, edge и nginx:
  `103 passed`; compileall успешен.
- Реальный bucket и credentials пока отсутствуют. Заказ минимального Object
  Storage и создание private bucket выполняет владелец Contabo-аккаунта, потому
  что это платная внешняя операция. После появления ключей исполнитель запускает
  `check` и `probe`, не выводя ключи в лог.
- Website не менять до завершения активной параллельной переработки его меню.
  Backend/S3 разворачивать независимо; UI синхронизировать отдельным срезом.

### 25.11. Финальное решение по хранилищу MVP 2026-09-10

Решение §25.10 о Contabo S3 не удаляется как история уже выполненной подготовки,
но перестаёт быть текущим планом и блокером запуска.

1. Файлы закрытой библиотеки физически хранятся на **Core VPS**, а не на VPS
   статического сайта: `/var/lib/wwc-partner-library/<tenant>/`.
2. Каталог находится вне nginx document root, репозитория и Docker image. API
   монтирует его read-only; запись выполняется отдельной операторской процедурой.
3. Staging/production используют `PLATFORM_PARTNER_LIBRARY_STORAGE_BACKEND=filesystem`.
   Режим `local` остаётся только для dev/test и не является production-настройкой.
4. Core проверяет оплаченный доступ при выдаче материала, затем создаёт HMAC-ссылку
   с TTL 300 секунд. Endpoint скачивания принимает только подписанный токен.
5. Signing secret длиной не менее 32 символов хранится только в server `.env`.
   В Git запрещены secret, реальные закрытые файлы и прямые публичные URL.
6. S3-адаптер и Contabo runbook сохраняются для будущей миграции без изменения
   `storage_key`, API и сайта.
7. Google Drive не использовать как прямой источник платных файлов: общая ссылка
   не отзывается автоматически вместе с подпиской и легко передаётся третьим лицам.
8. Website в рамках этого решения не менять. Он активно перерабатывается в другой
   ветке; подключение `/partner/resources/` и бокового меню выполнить отдельной
   синхронизацией после стабилизации UI.

Реализация: коммит Core `df17681`. Проверки: `106 passed`, compileall и
`git diff --check` успешны. Живой staging и production этим коммитом не менялись.

### 25.12. Финальный приоритет: оплаты без курсов 2026-09-10

Прямое решение владельца заменяет ближайшую часть блока 6:

1. Курсы, академия и закрытая файловая библиотека пока являются заглушками.
   Хранилище реализовано, но не разворачивается, материалы не загружаются.
2. Ближайший релизный срез содержит только учёт оплат, продление подписки,
   стартовый доступ, grace, блокировку неоплаченного сайта и Telegram-меню.
3. Website сейчас активно перерабатывается. Не переносить в него старый UI этой
   ветки и не менять staging сайта; интеграция выполняется отдельным срезом после
   стабилизации бокового меню.
4. Постоянная Telegram reply-клавиатура удаляется. Бот использует стандартное
   command menu Telegram, открываемое компактной кнопкой возле поля сообщения.
5. Команды меню: `/start`, `/products`, `/calculator` только для WHIEDA,
   `/business`, `/company`, `/match`, `/events`. Billing-команды в публичное меню
   не добавлять, потому что они доступны только владельцу.
6. Staging не менять до минимальной проверки владельцем. Контрольный маршрут
   записан в `backend/deploy/core/PAYMENTS_STAGING_REVIEW.md`.

Реализация Telegram-меню: `53db8ad`. Финальный профиль: `151 passed`, compileall
и `git diff --check` успешны. Production и staging не изменялись.
