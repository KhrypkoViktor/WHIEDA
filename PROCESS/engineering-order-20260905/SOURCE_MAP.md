# Карта источников: кто чем владеет

Дата: 2026-09-05. Блок A. Проверено чтением файлов в task-worktree
`D:/Projects/_worktrees/wwc-engineering-order-20260905` на базе `bb603a9`.

Все перечисленные файлы существуют на этой базе — verified 2026-09-05.
Новых master-таблиц эта карта не вводит. Найденные конфликты вынесены в конец
одним списком с предложением; объединять их самостоятельно нельзя.

---

## Партнёры

| Вопрос | Ответ |
|---|---|
| Master | `Partners_Ref` (Google Sheets) — писатель определяется владельцем, не сайтом |
| Кто вправе менять | владелец / ответственный за онбординг; сайт master не редактирует |
| Runtime/API | `GET /api/v1/public/ref/<code>` → Core. Отдаёт **только идентичность**: `ref_code`, `display_mode`, `enabled`, `profile_version`, `display_name`, `page_mode`, `public_site_url`, `site_type`, `focus_group`, `access_tier` |
| Сборочный источник | `src/data/referrals.js` (реестр, subdomain-карта, профили), `src/data/referral-platform.js` (константы), `src/data/partner-pages.js` (страницы) |
| Генерация | `scripts/sync-runtime-assets.mjs` → `public/wwc-api/referral-registry.js` |
| Render | `src/components/ReferralBootstrap.astro` → `window.__whiedaReferral.profile`; страница `src/pages/partner/index.astro` |
| Fallback-приоритет | **одно правило на всех**: Core говорит, кто партнёр; сборка — как он выглядит; присланное Core побеждает. При недоступности Core — рендер из сборки (`source: pilot-fallback`) |
| Sync-команда | `npm run build` (postbuild вызывает `sync-runtime-assets.mjs`) |
| Тест | `tests/unit/referral-api-bootstrap.test.mjs` — прогоняет **весь** реестр реф за рефом |

**Важно:** `access_tier` из Core — это НЕ рендерный `tier`. `/partner/` строит
личную страницу только при `tier === 'pro'`; подстановка `access_tier`
(`test_pilot`) ломала личную страницу. Правило зафиксировано в каноне.

## Цены

| Вопрос | Ответ |
|---|---|
| Master | `WORK/data-contracts/WHIEDA_PRODUCTS_PRICES_MASTER.tsv` + `.manifest.json` (корневой repo) |
| Кто вправе менять | **только отдельный запрос владельца на обновление цен**. Ни один фикс скорости, верстки или рефералов права менять цену не имеет |
| Сборочный источник | `src/data/products.js`, `src/data/repeat-purchase-prices.js` |
| Runtime/API | Core отдаёт рыночные цены; при пустом ответе утверждённая цена **не затирается** |
| Генерация | `scripts/export-price-list.mjs`, `scripts/export-price-list-pdf.mjs` → `public/downloads/whieda-price-list.{csv,pdf}` |
| Render | `src/components/ProductCardPrice.astro`, `ProductListingCard.astro`, `src/lib/market/market-centers-bootstrap.js` |
| Fallback-приоритет | утверждённая статическая цена > пустой ответ Core. Валюта по умолчанию — рубль |
| Тест | `tests/unit/runtime-twin-sync.test.mjs` — стережёт защиту утверждённой цены **в обеих** копиях модуля |

Первичная и повторная цена — разные величины; рынок и валюта тоже. Неизвестное
или отсутствующее значение не равно нулю и не равно снятому с продажи товару.

## Товары и медиа

| Вопрос | Ответ |
|---|---|
| Сборочный источник | `src/data/product-content.js`, `src/data/official-catalog-content.js`, `src/data/catalog-photos.js`, `src/data/catalog-official-photos.js` |
| Кто вправе менять | контентная задача по своему товару; общий шаблон — отдельно |
| Генерация | `scripts/sync-official-product-media.mjs` (`npm run sync:official-product-media`) |
| Render | `src/components/ProductDetailTemplate.astro`, `ProductListingCard.astro`, карусель `src/scripts/card-slider.js` |
| Отдача | nginx-правила `/media/products/official/` и `/media/products/catalog/`; кэш 7 дней |
| Тест | `tests/unit/product-extra-docs.test.mjs`, `tests/unit/ui-system-contract.test.mjs` |

Регулярные `location` в nginx матчатся в порядке появления: compat-правила для
медиа однажды перехватывали запрос раньше общего блока кэширования, из-за чего
фото каталога не кэшировались вообще.

## Статьи

| Вопрос | Ответ |
|---|---|
| Master текста | внешний pipeline, индекс `D:/Obsidian/WWC.Best/Pipline/_INDEX.md` (вне репозитория) |
| Сборочный источник | `src/data/articles.js`, страницы `src/pages/articles/` |
| Render | `src/components/ArticleHero.astro`, `ArticleNext.astro` |
| Индексация | корень индексируется; копии на поддоменах — `noindex, follow` + `canonical` на корень |
| Тест | `scripts/check-indexing-invariants.mjs` (`npm run check:seo`) — **есть не во всех ветках**, см. конфликт 2 |

## UI

| Вопрос | Ответ |
|---|---|
| Источник | `design/DESIGN.md`, `design/tokens.css`, `src/styles/global.css`, общие компоненты |
| Правило | основные CTA золотые; статусные и брендовые цвета не перекрашивать автоматически; фото — не CSS-токен |
| Тест | `tests/unit/ui-system-contract.test.mjs` |

## Генерация

| Генератор | Из чего | Во что |
|---|---|---|
| `scripts/sync-runtime-assets.mjs` | `src/lib/api`, `src/lib/market`, `src/lib/referral/state.js`, `src/data/referrals.js` | `public/wwc-api/*.js`, `public/wwc-api/referral-registry.js`, `public/health.json` |
| `scripts/sync-runtime-config.mjs` | флаги среды | `public/wwc-runtime-config.json` |
| `scripts/inject-advisor.mjs` | шаблоны виджета | теги в 141 HTML (виджет советника сейчас выключен) |
| `scripts/export-price-list*.mjs` | данные цен | `public/downloads/whieda-price-list.{csv,pdf}` |

Генерируемый файл правится **через источник и штатный генератор**. Список
генерируемого задаётся кодом генератора, а не предположением обо всём `public/`.

---

## Найденные конфликты источников

Каждый — с одним предложением лиду. Самостоятельно не объединяю.

**1. Две физические копии одного runtime-кода.** `src/lib/*` и
`public/wwc-api/*` — один код в двух файлах, браузер грузит `public`. Правка в
одну копию живёт до первой сборки с чистого чекаута. Сейчас расхождение только
стережёт тест `runtime-twin-sync`.
*Предложение:* генерировать `public/wwc-api` на сборке и не хранить вторую
копию в гите. Минимальный diff: `.gitignore` + шаг сборки; тест остаётся как
страховка на переходный период.

**2. `check:seo` есть не везде.** Канон утверждает, что команда входит в
`release:verify`, но в checkout `03_Website/wwc-best`
(`fix/fedorov-runtime-context` @ `e1cd939`) её нет: и скрипт, и запись в
`package.json` живут на ветке `fix/staging-referral-isolation`. Doc-check
подтверждает это двумя ошибками (см. `REPORT.md`).
*Предложение:* лид определяет интеграционную базу и переносит правила одним
согласованным Git-пакетом; до переноса канон не должен утверждать наличие
команды как факт.

**3. Копия nginx-конфига в репозитории устарела.** В
`03_Website/wwc-best/wwc.best.nginx.conf` нет `location` для
`/api/v1/theme-access`, хотя маршрут живой.
*Предложение:* либо привести файл в соответствие живому конфигу, либо явно
пометить его как нерабочий черновик. Применять его в текущем виде нельзя.

**4. Master цен — в корневом репозитории, потребители — в сайте.** Master
лежит в `WORK/data-contracts/`, а `src/data/products.js` живёт в отдельном
Git-репозитории сайта. Связь между версией master и сборкой сайта сейчас
ничем не фиксируется.
*Предложение:* добавить версию/hash master в manifest релиза сайта, чтобы
было видно, из какого снимка цен собран артефакт. Реализация — блок C.
