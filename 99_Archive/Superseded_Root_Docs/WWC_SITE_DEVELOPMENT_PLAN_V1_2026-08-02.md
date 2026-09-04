# WWC Partner Pilot Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Версия:** 1.0  
**Дата:** 2026-08-02  
**Последнее обновление:** 2026-08-14  
**Владелец исполнения:** разработчик сайта WWC  
**Горизонт:** 2–4 недели, базовый план — 3 недели  
**Статус:** канонический план продолжения сайта для Partner Pilot

**Goal:** Довести статический сайт `wwc.best` до безопасного frontend-контура пилота 5–10 партнёров, который потребляет фиксированные публичные API, сохраняет корректную ref-атрибуцию и не зависит от внутренних схем backend.

**Architecture:** Astro остаётся статическим генератором и SEO-слоем. Browser-код обращается только к same-origin API-фасаду `wwc.best`; единый frontend adapter нормализует ответы, управляет таймаутами, feature flags и безопасными pilot-fallback, а backend повторно разрешает tenant/ref/owner. Локальные данные сайта остаются только временным display-fallback и никогда не являются полномочием назначить владельца лида.

**Tech Stack:** Astro 4 static output, vanilla browser JavaScript, nginx same-origin proxy, Yandex Metrika, Playwright для E2E и visual QA, Node content checks.

## Global Constraints

- Граница владения: website DEV меняет только `03_Website/`; backend DEV не меняет `03_Website/`.
- Website DEV не меняет n8n/Postgres/Sheets/Dify и не выполняет backend deploy.
- Frontend не читает Google Sheets, Postgres/Supabase или n8n admin API напрямую.
- Frontend не отправляет и не принимает как доверенные `owner_id`, `assigned_owner_id`, `attributed_owner_id`, Telegram token или SQL-данные.
- Публичные целевые контракты: `GET /api/v1/public/ref/{code}`, `POST /api/v1/leads`, `POST /api/v1/advisor/query`.
- Неизвестный, отключённый или недоступный ref не назначает owner и не раскрывает приватные данные.
- `first_ref` неизменяем после первого валидного ref; `active_ref` меняется при каждом новом валидном ref.
- Канонический контент хранится в Obsidian; сайт не переписывает owner-approved тексты молча.
- Один сайт и общий контент; партнёрские копии страниц не создаются.
- UI меняется по одной секции; цвета, шрифты и интервалы — только из дизайн-токенов.
- Перед выпуском любого UI-изменения обязательны Playwright-скриншоты 1440×900 и 390×844 и проверка кириллицы.
- Не добавлять RAG, платежи, авторизацию партнёров, web-CRM/admin, новый каталог API или персональную медицинскую память в этот цикл.
- Утвержденный следующий модуль `web + PWA + Telegram Mini App каталог/калькулятор` не расширяет текущий pilot scope. До завершения pilot gate разрешены только API fixtures/mock и подготовка совместимых UI primitives; production-интеграция выполняется отдельным этапом по Advisor Experience Contract.
- **Core API cutover gate (site):** переключение `leadApi`/`advisorApi` в `wwc-runtime-config.json` на `v1` запрещено до явного backend parity sign-off владельцем. Nginx/Core могут обслуживать v1-маршруты, но frontend остаётся на `leadApi: legacy` и `advisorApi: legacy`, пока старый same-origin путь не доказан эквивалентным по latency, доставке и качеству ответов (без «не знаю»/пустых ответов). `refApi` может оставаться в `fallback`/`canary` независимо; site DEV не меняет runtime flags без подтверждения.

---

## 1. Evidence base

План основан на чтении и сопоставлении:

- `00_READ_FIRST_WHIEDA_CANON.md`;
- `backend/platform-api/docs/WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md`;
- `WHIEDA_FILE_MAP_CURRENT.md`;
- `WWC_SITE_MATERIALS_INDEX.md`;
- `WHIEDA_SITE_REFERRAL_MVP_SPEC_V1_2026-07-26.md`;
- `WHIEDA_WWC_SITE_BOT_LEADS_MULTI_REF_SPEC_V1_2026-07-27.md`;
- `n8n/current/WHIEDA_ADVISOR_API_CONTRACT_V1.md`;
- `n8n/current/WHIEDA_BACKEND_CORE_AUDIT_2026-07-29.md`;
- `WHIEDA_LIVE_STATUS.md`;
- исходников `03_Website/wwc-best`;
- Obsidian-карты материалов, актуального текста главной и финального ТЗ типографики.

Проверки 2026-08-02:

- `npm run build` прошёл: 36 позиций контента, 35 товаров, 11 технологий, 93 статические HTML-страницы; postbuild внедрил advisor и Metrika во все 93 страницы.
- `https://wwc.best/` и `https://wwc.best/robots.txt` доступны.
- `https://wwc.best/sitemap.xml` ответил HTTP 500 — текущий live P0-дефект.
- backend ref endpoint `https://sysarchn8n.duckdns.org/webhook/whieda-public-ref-v1?ref=ladnaya` ответил 200 публичным профилем версии 95.
- same-origin `https://wwc.best/webhook/whieda-public-ref-v1?...` ответил 404; публичного site proxy для ref пока нет.
- `https://wwc.best/admin/qr/` доступен публично и помечен `noindex,nofollow`.

## 2. CURRENT STATE

> **Update 2026-08-02 (Week 4 site pass):** sitemap fixed (200, no partner URLs); design tokens in `design/`; ladnaya ref canary via `refApiCanaryRefs`; Playwright visual QA + e2e partner tests; Webvisor masking on lead forms; release/canary docs added.

### 2.1 Архитектура и маршруты

- Astro 4.16.19, `output: static`, canonical site `https://wwc.best`.
- 27 source route-файлов генерируют 93 HTML-страницы из-за 35 карточек в `/catalog/[slug]` и 35 legacy-карточек в `/produktsiya/[slug]`.
- Основные индексируемые маршруты: `/`, `/catalog/`, `/catalog/{slug}/`, `/articles/`, четыре статьи, `/technologies/`, `/about/`, `/dlya-praktikov/`.
- Legacy/utility: `/produktsiya/*`, `/tehnologii/`, `/o-klube/`, `/razbor/*`, `/success/`, `/v-razrabotke/`, `/admin/qr/`, `/partner/`.
- Часть legacy-маршрутов редиректится компонентами/конфигом, но всё ещё физически генерируется.
- `robots.txt` разрешает обход и указывает на sitemap; live sitemap сейчас 500.

### 2.2 Deploy

- Build-команда: `npm run build`; она сначала запускает два content-check, затем Astro и обязательный postbuild injector.
- Postbuild внедряет advisor, Metrika и content-hash cache bust во все HTML.
- nginx обслуживает статику и wildcard-поддомены; поддомены получают `X-Robots-Tag: noindex, follow`.
- Документы расходятся по live web root: nginx-конфиг указывает `/var/www/whieda-sysarch`, `n8n/current/WHIEDA_DEPLOY_HOSTS.md` — `/var/www/mlm-sysarch`.
- В репозитории не найден канонический site deploy/rollback script или release report template.
- Текущий website tree и `dist/` в git status в основном untracked; надёжной commit-based точки отката не видно.

### 2.3 Referral registry и поддомены

- `src/data/referrals.js` содержит локальный реестр: `nnm` и шесть enabled named-профилей (`ladnaya`, `mariam`, `harold`, `onlineelena`, `alena-ivanova`, `olga-samtsova`).
- Поддомены локально сопоставляются для `ladnaya`, `mariam`, `harold`, `onlineelena`, `olga`.
- `ReferralBootstrap.astro` читает query ref, host ref, cookie и localStorage; валидирует только по локальному реестру.
- `whieda_active_ref` хранится 30 дней в cookie `.wwc.best` и localStorage.
- `whieda_first_ref` хранится только в origin-scoped localStorage, поэтому не гарантированно сохраняется между `wwc.best` и партнёрскими поддоменами.
- Новый валидный ref меняет active, а first записывается только при отсутствии; это правильное направление, но источник валидности пока локальный.
- Advisor читает другой ключ `wwc_active_ref`, которого referral bootstrap не записывает; ref-контекст advisor может теряться.

### 2.4 Партнёрские страницы

- Один client-rendered `/partner/`, всегда `noindex,follow`; wildcard nginx дополнительно ставит `X-Robots-Tag`.
- Полные/расширенные данные есть для `ladnaya`; short/custom записи — для `mariam`, `harold`, `olga-samtsova`; остальные используют общий fallback либо не имеют PRO-страницы.
- Профили и страницы строятся из локальных JS-данных, не из public ref API.
- Sitemap добавляет поддоменные `/partner/`, хотя страницы и wildcard host объявлены noindex; это SEO-противоречие.
- Страница содержит page-level hex, inline styles, emoji-иконки и собственные карточки; это не соответствует текущим правилам дизайн-системы.

### 2.5 Lead forms

- Главная и `OrderModal` отправляют JSON на legacy `POST /api/lead`.
- Есть consent checkbox, honeypot, client-generated idempotency key, disabled submit, сохранение полей при ошибке и success redirect.
- Payload использует `initial_ref`, `active_ref`, `page_url`, но не передаёт версию согласия, structured UTM, landing URL, country/locale и явный first-touch timestamp.
- В named/anonymous ref-режиме `ReferralBootstrap` заменяет общую форму прямой Telegram-ссылкой. Значит большинство pilot ref-переходов обходят lead API, idempotency и backend attribution.
- `owner_id` сейчас из формы не отправляется — это безопасно.
- nginx ограничивает body и timeout, но rate limit для `/api/lead` в показанном конфиге не настроен; UX для HTTP 429 отсутствует.

### 2.6 Advisor

- Widget postbuild-инжектится на все 93 HTML-страницы.
- Endpoint: same-origin `POST /api/advisor/query` → live webhook `wwc-advisor-public-v1`.
- Есть стабильная browser session, 15-секундный AbortController timeout, neutral fallback, debug mode, quick questions и CTA в order modal.
- Текущий request shape (`question`, `session_id`, `product_context`, `ref_context`, `locale`) отличается от канонического contract shape (`tenant`, `session`, `question`, `slug`, `page_url`, `country`, `language`, `surface`).
- Widget принимает несколько исторических response-форм, но не реализует канонические `media`, `clarifications`, `sources`, `context.last_product_sku` и `error_id/trace id` как единый тип.
- Live backend пережил P0 incident с пустыми website-ответами; внешний canary сейчас покрывает advisor, но frontend не имеет собственного canary/contract fixture.

### 2.7 Analytics и privacy

- Yandex Metrika counter `111158320` инжектится во все страницы с Webvisor, clickmap и link tracking.
- События: `ref_visit`, `advisor_open`, `advisor_question`, `lead_submit`, `video_open`, `pdf_open`.
- Page context включает ref, product, country и page type; PII формы не отправляется явным custom event.
- Полной referral-воронки нет: отсутствуют отдельные CTA, form start, submit error, rate-limit, advisor answer/fallback/latency и API source events.
- `ref_visit` берёт сырой query ref до серверной проверки и может учитывать invalid ref.
- Webvisor на страницах с PII-формами требует отдельной privacy/masking проверки.

### 2.8 RF/RB, цены и utility pages

- Каталог одновременно показывает RUB и BYN; управляемого RF/RB selector нет.
- Metrika ищет region control, но фактического контрола в source не найдено и по умолчанию записывает `Россия`.
- Отдельной страницы скачивания прайса нет; есть цены в каталоге и ссылки на PDF/презентации/сертификаты.
- `/admin/qr/` — client-only генератор на внешнем `api.qrserver.com`, без авторизации, но `noindex,nofollow`.
- Решения о region selector и price download не приняты; этот план не реализует их без product decision.

### 2.9 Design system

- `global.css` фактически содержит токены и shared primitives: Unbounded + Golos Text + IBM Plex Mono, типографическую шкалу, motion и общие buttons.
- Утверждённая кириллица и типографика из ТЗ 2026-08-02 в основном перенесены.
- `design/DESIGN.md` и `design/tokens.css` отсутствуют.
- Компонентной библиотеки shadcn/ui нет; проект не React/Next.js, а pure Astro. Добавление React только ради shadcn увеличит pilot-risk и требует отдельного решения.
- В страницах остаётся много inline CSS/hex, нативные `<select>`, emoji и page-local UI.

### 2.10 Tests

- Автоматизированы только content/positioning checks и production build.
- Unit, contract, E2E, accessibility и visual regression tests не найдены.
- Build зелёный, но live sitemap 500 показывает, что build success недостаточен как release gate.

## 3. Top 10 gaps and risks

1. **Нет same-origin public ref API.** Сайт не может мигрировать с локального реестра на целевой контракт.
2. **Pilot ref-трафик обходит lead API.** Форма заменяется Telegram CTA, поэтому backend не получает доказуемую заявку.
3. **Cross-subdomain first_ref не гарантирован.** First ref хранится только в localStorage конкретного origin.
4. **Несовместимые storage keys.** Advisor читает `wwc_active_ref`, referral layer пишет `whieda_active_ref`.
5. **Нет зафиксированных v1 schemas.** Пути из backend baseline, live webhook и текущий frontend различаются.
6. **Live sitemap возвращает 500.** SEO discovery нарушен, а sitemap включает noindex partner URLs.
7. **Нет воспроизводимого rollback.** Web root расходится в документах; deploy script/release manifest отсутствуют; source/dist не имеют чистого git baseline.
8. **Frontend tests почти отсутствуют.** Invalid/disabled/switched ref, duplicate lead, forged owner и 429 UX не защищены регрессией.
9. **Analytics неполна и может считать invalid ref.** Нет полной privacy-safe воронки и проверки Webvisor masking.
10. **Design governance незавершён.** Нет обязательных design-файлов; остаются hardcoded styles, emoji и собственные варианты контролов.

## 4. Target frontend file map

Новые файлы вводятся по ответственности; существующие страницы не переписываются целиком.

- Create `wwc-best/design/DESIGN.md` — утверждённый brief, аудитория, signature element, запреты.
- Create `wwc-best/design/tokens.css` — единственный источник brand tokens; `global.css` импортирует его.
- Create `wwc-best/public/wwc-runtime-config.json` — runtime feature modes и same-origin paths без секретов.
- Create `wwc-best/src/lib/api/http.js` — timeout, JSON parsing, typed error categories, request id.
- Create `wwc-best/src/lib/api/referrals.js` — public ref API adapter и safe local display fallback.
- Create `wwc-best/src/lib/api/leads.js` — v1 lead adapter, idempotency and error mapping.
- Create `wwc-best/src/lib/api/advisor.js` — canonical request/response normalization and trace id.
- Create `wwc-best/src/lib/referral/state.js` — normalization, first/active semantics, cookie/domain behavior.
- Create `wwc-best/src/lib/analytics/events.js` — allowlisted non-PII event schema.
- Create `wwc-best/src/components/LeadForm.astro` — единая форма для главной и product modal без ref-specific Telegram replacement.
- Create `wwc-best/tests/unit/*.test.mjs` — pure ref/API normalization tests.
- Create `wwc-best/tests/e2e/partner-pilot.spec.mjs` — browser matrix with mocked contracts.
- Create `wwc-best/playwright.config.mjs` — desktop/mobile projects and artifact paths.
- Create `wwc-best/docs/SITE-RELEASE-RUNBOOK.md` — deploy, canary, rollback.
- Create `wwc-best/docs/PARTNER-ONBOARDING-CHECKLIST.md` — данные, consent, preview, enablement.
- Create `wwc-best/docs/API-CONTRACT-FIXTURES.md` — accepted request/response examples and owner-trust boundary.

Изменяются точечно:

- `src/components/ReferralBootstrap.astro`;
- `src/data/referrals.js` — оставить как pilot fallback, пометить source/version/expiry;
- `src/pages/index.astro`;
- `src/components/OrderModal.astro`;
- `public/wwc-advisor-widget.js`;
- `public/wwc-metrika-events.js`;
- `scripts/inject-advisor.mjs`;
- `src/pages/sitemap.xml.ts`;
- `src/pages/robots.txt.ts`;
- `wwc.best.nginx.conf`;
- `package.json`;
- `src/styles/global.css`;
- `src/pages/partner/index.astro` — только после API и design foundation.

## 5. Contract boundary and frontend interfaces

### 5.1 Runtime feature modes

```json
{
  "refApi": "fallback",
  "leadApi": "legacy",
  "advisorApi": "legacy",
  "endpoints": {
    "ref": "/api/v1/public/ref",
    "leads": "/api/v1/leads",
    "advisor": "/api/v1/advisor/query"
  },
  "timeoutsMs": {
    "ref": 2500,
    "leads": 10000,
    "advisor": 8000
  }
}
```

Allowed modes: `off`, `fallback`, `canary`, `v1`; lead/advisor additionally allow `legacy` during migration. Runtime config failure resolves to checked-in conservative defaults, not to direct n8n URLs.

### 5.2 Referral adapter

```js
resolveReferral(code, context) => Promise<{
  status: 'resolved' | 'invalid' | 'disabled' | 'unavailable',
  source: 'api' | 'pilot-fallback' | 'none',
  profile: null | {
    refCode: string,
    displayMode: 'anonymous' | 'named',
    profileVersion: number | null,
    consultant: {
      displayName: string | null,
      photoUrl: string | null,
      shortBio: string | null,
      contactUrl: string | null,
      publicSiteUrl: string | null
    }
  }
}>
```

The adapter never exposes owner id to components. `invalid`, `disabled` and `unavailable` render organic/neutral UI; only `unavailable` may use an allowlisted, dated pilot display fallback. Fallback ref remains a browser hint; backend still resolves attribution.

### 5.3 Referral state

```js
getReferralState() => {
  firstRef: string,
  activeRef: string,
  activeProfileVersion: number | null,
  source: 'query' | 'subdomain' | 'cookie' | 'none'
}

acceptValidReferral({ refCode, profileVersion, source }) => ReferralState
```

- Query has display priority over subdomain only after API validation.
- First valid ref writes `wwc_first_ref` cookie for `.wwc.best`, `SameSite=Lax`, `Secure`, 30 days, only when absent.
- Every new valid ref updates `wwc_active_ref` with the same domain and lifetime.
- Invalid/disabled ref does not overwrite either cookie and displays organic/neutral mode for that request.
- localStorage is migration compatibility only; canonical browser state becomes cross-subdomain cookies.
- Backend receives first/active as untrusted hints and re-resolves both.

### 5.4 Lead adapter

```js
submitLead({
  idempotencyKey,
  name,
  contact,
  topic,
  message,
  productSlug,
  productVariant,
  firstRef,
  activeRef,
  refProfileVersion,
  landingUrl,
  currentUrl,
  utm,
  country,
  locale,
  consent: { accepted: true, version: 'wwc-lead-consent-2026-08-02' },
  honeypot
}) => Promise<{
  ok: boolean,
  leadPublicId: string | null,
  duplicate: boolean,
  traceId: string | null
}>
```

Forbidden request properties: `owner_id`, `assigned_owner_id`, `attributed_owner_id`, `watchers`, `tenant_id` supplied as an authority. If tenant is present for compatibility, backend derives/verifies it from host.

### 5.5 Advisor adapter

```js
askAdvisor({
  session,
  question,
  slug,
  pageUrl,
  ref,
  country,
  language: 'ru',
  surface: 'website'
}) => Promise<{
  ok: boolean,
  answerText: string,
  answerMode: string,
  route: string,
  product: null | { sku: string | null, canonicalName: string | null, slug: string | null },
  media: { photoUrl: string | null, videos: Array, documents: Array },
  clarifications: Array,
  sources: Array,
  context: object,
  traceId: string | null
}>
```

`traceId` is normalized from approved `trace_id` or `error_id`; it is shown only in an expandable error detail and analytics records only presence/category, not the question text.

## 6. Phased implementation

### Task 1: P0 release baseline and guardrails

**Files:**
- Create: `wwc-best/docs/SITE-RELEASE-RUNBOOK.md`
- Create: `wwc-best/public/wwc-runtime-config.json`
- Modify: `wwc-best/package.json`
- Modify: `wwc-best/wwc.best.nginx.conf`

**Dependencies:** none for documentation; site operator must confirm actual web root before deploy.

**Deliverables:**

- [ ] Record exact live host, web root, nginx config path, deploy command and current release directory/hash.
- [ ] Add scripts `check`, `test:unit`, `test:e2e`, `release:verify` without changing `npm run build` postbuild semantics.
- [ ] Add immutable-release layout: timestamped release directory + atomic `current` symlink, or equivalent proven host mechanism.
- [ ] Define rollback as switching to the preceding verified release and reloading nginx only when config changed.
- [ ] Add same-origin `/health` static build marker containing release id, build time and git commit; no secrets.
- [ ] Fix live `/sitemap.xml` 500 before any pilot feature release.
- [ ] Exclude runtime config and health marker from 30-day immutable cache.
- [ ] Establish rule: generated `dist/` is never the sole rollback source.

**Acceptance criteria:**

- Fresh checkout can run `npm ci && npm run build`.
- Release verification checks homepage, one product, robots, sitemap, advisor asset hashes and build marker.
- Rollback rehearsal restores the previous build in under 10 minutes.
- Website source has a committed baseline before API migration starts.

### Task 2: Contract freeze and API adapter foundation

**Files:**
- Create: `wwc-best/src/lib/api/http.js`
- Create: `wwc-best/src/lib/api/referrals.js`
- Create: `wwc-best/src/lib/api/leads.js`
- Create: `wwc-best/src/lib/api/advisor.js`
- Create: `wwc-best/docs/API-CONTRACT-FIXTURES.md`
- Create: `wwc-best/tests/unit/api-adapters.test.mjs`

**Dependency:** **[BLOCKED UNTIL CONTRACT AVAILABLE]** backend must publish versioned examples and error/status matrix for all three `/api/v1` routes.

**Deliverables:**

- [ ] Store sanitized success, invalid, disabled, duplicate, validation, 429, timeout and 5xx fixtures.
- [ ] Implement one timeout/error taxonomy: `validation`, `invalid_ref`, `disabled_ref`, `rate_limited`, `timeout`, `unavailable`, `bad_response`.
- [ ] Accept only same-origin endpoint paths from runtime config.
- [ ] Reject non-JSON and schema-incompatible success responses as `bad_response`.
- [ ] Attach frontend request id; preserve backend trace id without exposing internals.
- [ ] Unit-test every fixture before connecting UI.

**Acceptance criteria:**

- Components never call `fetch` for pilot APIs directly.
- Contract tests fail on field/path drift.
- No adapter returns trusted owner fields.
- Feature mode switches independently for ref, lead and advisor.

### Task 3: Referral state and public ref API migration

**Files:**
- Create: `wwc-best/src/lib/referral/state.js`
- Modify: `wwc-best/src/components/ReferralBootstrap.astro`
- Modify: `wwc-best/src/data/referrals.js`
- Create: `wwc-best/tests/unit/referral-state.test.mjs`
- Create: `wwc-best/tests/e2e/referral.spec.mjs`

**Dependency:** **[BLOCKED UNTIL CONTRACT AVAILABLE]** same-origin `GET /api/v1/public/ref/{code}` must return named display fields required by current UI: name, approved photo URL, bio, contact URL/label, display mode, enabled/status, profile version and optional public page URL. Current live webhook omits photo, bio and contact.

**Deliverables:**

- [ ] Normalize query and subdomain refs to lowercase allowlisted code format.
- [ ] Resolve candidate ref by API before rendering sensitive named data.
- [ ] Move first/active state to cross-subdomain cookies with one canonical key set.
- [ ] Remove `wwc_active_ref`/`whieda_active_ref` mismatch and provide one-time migration.
- [ ] Keep local registry as dated 5–10 pilot display fallback only in `fallback` mode.
- [ ] Add fallback expiry and per-profile consent/version metadata.
- [ ] Ensure invalid/disabled ref never silently falls back to another partner.
- [ ] Prevent flash of Viktor/name/photo/contact before resolution.

**Acceptance criteria:**

- Anonymous, named, invalid, disabled, switched and API-unavailable scenarios match the matrix in section 8.
- First ref survives `wwc.best` → `partner.wwc.best` → `wwc.best`.
- New valid ref updates active only.
- No sensitive profile data remains in hidden DOM, `alt`, `aria-label`, title or initial HTML for anonymous mode.

### Task 4: Unified lead forms and backend delivery

**Files:**
- Create: `wwc-best/src/components/LeadForm.astro`
- Modify: `wwc-best/src/pages/index.astro`
- Modify: `wwc-best/src/components/OrderModal.astro`
- Modify: `wwc-best/src/components/ReferralBootstrap.astro`
- Create: `wwc-best/tests/e2e/leads.spec.mjs`

**Dependency:** **[BLOCKED UNTIL CONTRACT AVAILABLE]** `POST /api/v1/leads` must define required product/topic behavior, consent fields/version, idempotency header/body rule, response for duplicate, 400/409/422/429/5xx and maximum payload lengths. Current live workflow requires non-empty `product`, while the homepage sends an empty product.

**Deliverables:**

- [ ] Stop replacing forms with Telegram in ref mode; Telegram remains secondary contact CTA.
- [ ] Use one form component/state model on home and order modal.
- [ ] Generate one idempotency key per user intent and reuse it across transport retry.
- [ ] Submit first/active ref, profile version, landing/current URL, UTM, country, locale and consent version.
- [ ] Never submit owner id.
- [ ] Preserve entered fields on validation/network/rate-limit failure.
- [ ] Map 429 to calm retry-after UX; prevent rapid duplicate click and duplicate retry.
- [ ] Fire privacy-safe start/success/error events.
- [ ] Show the recipient statement from resolved display mode before consent.

**Acceptance criteria:**

- Named, anonymous and organic submissions create one backend lead each.
- Forged owner fields inserted in DevTools have no effect and are not produced by the adapter.
- Duplicate submit returns the same success state without a second lead.
- Backend rejection never produces false success.
- Telegram remains available if API is unavailable, but UI clearly distinguishes “write directly” from “application submitted”.

### Task 5: Advisor contract, traceability and performance

**Files:**
- Modify: `wwc-best/public/wwc-advisor-widget.js`
- Modify: `wwc-best/public/wwc-advisor-widget.css`
- Modify: `wwc-best/docs/ADVISOR-WIDGET.md`
- Create: `wwc-best/tests/e2e/advisor.spec.mjs`

**Dependency:** **[BLOCKED UNTIL CONTRACT AVAILABLE]** reconcile `/api/v1/advisor/query` with current `/api/advisor/query` and freeze canonical fields, HTTP errors, trace id, media URL allowlist, rate-limit and target latency.

**Deliverables:**

- [ ] Route all calls through advisor adapter.
- [ ] Send canonical session, slug, page URL, active ref, country, language and surface.
- [ ] Render approved photo/video/document resources, clarifications and sources safely.
- [ ] Preserve short session context using returned context, without storing question text in analytics/localStorage.
- [ ] Use 8-second interaction budget: visible progress by 300 ms; useful fallback by 8 seconds; backend may continue only if contract explicitly supports it.
- [ ] Expose trace id in error detail and console only in debug mode.
- [ ] Add `advisor_answer`, `advisor_fallback`, `advisor_error`, `advisor_latency_bucket` events without question text.
- [ ] Keep order CTA product/ref context when opening lead form.

**Acceptance criteria:**

- Structured fixture renders within 100 ms after response receipt.
- Timeout, 429, malformed JSON and backend error each yield distinct safe UX.
- Advisor ref equals referral state active ref.
- No private RAG/internal library route is exposed by frontend controls.

### Task 6: Pilot partner onboarding and page completeness

**Files:**
- Create: `wwc-best/docs/PARTNER-ONBOARDING-CHECKLIST.md`
- Modify: `wwc-best/src/pages/partner/index.astro`
- Modify: `wwc-best/src/data/partner-pages.js`
- Modify: `wwc-best/src/data/referrals.js`

**Dependency:** **[BLOCKED UNTIL PARTNER DATA APPROVED]** for each pilot: public name, photo, bio, contact, display mode/tier, subdomain/ref, country, consent evidence, enabled status and optional unique page content.

**Deliverables:**

- [ ] Inventory 5–10 pilots against completeness checklist.
- [ ] Separate Basic named personalization from PRO unique page eligibility.
- [ ] Make API profile the status/display source; local content remains editorial page enhancement.
- [ ] Disable incomplete profiles rather than invent data.
- [ ] Remove emoji icons and hardcoded styles one section at a time using shared tokens/primitives.
- [ ] Verify long names, absent bio/photo/contact and disabled subscription.
- [ ] Keep PRO partner pages `noindex,follow` until unique-content SEO review explicitly approves indexing.

**Acceptance criteria:**

- Every enabled pilot has a tested basic ref URL and named/anonymous expected mode.
- Every PRO page has approved unique content or remains noindex.
- Disabling backend profile removes personalization without breaking the shared site.
- No partner onboarding requires component edits.

### Task 7: Analytics funnel and privacy

**Canonical doc:** [`wwc-best/docs/ANALYTICS-PRIVACY-MAP.md`](wwc-best/docs/ANALYTICS-PRIVACY-MAP.md) — goals allowlist, payload schema, Webvisor masking inventory, PII rules.

**Files:**
- Create: `wwc-best/src/lib/analytics/events.js`
- Modify: `wwc-best/public/wwc-metrika-events.js`
- Modify: `wwc-best/scripts/inject-advisor.mjs`
- Create: `wwc-best/docs/ANALYTICS-PRIVACY-MAP.md`

**Dependency:** product owner approves event names; privacy owner confirms Metrika Webvisor masking/consent basis.

**Deliverables:**

- [ ] Allowlist: `ref_resolved`, `ref_invalid`, `ref_cta_click`, `lead_form_start`, `lead_submit_success`, `lead_submit_error`, `advisor_open`, `advisor_question`, `advisor_answer`, `advisor_fallback`.
- [ ] Record only validated ref code, display mode, path, product slug, country, UTM allowlist, result category and latency bucket.
- [ ] Remove owner id, names, phone, Telegram handle, message text, full URLs with arbitrary query and raw backend errors.
- [ ] Count `ref_visit` only after successful validation.
- [ ] Verify form fields are masked/excluded from Webvisor; disable Webvisor for lead forms if masking cannot be proven.
- [ ] Document retention/access owner for analytics.

**Acceptance criteria:**

- Network inspection shows no PII in Metrika calls.
- Full funnel can be measured per validated ref without identifying a lead.
- Invalid ref is separable from organic and never attributed to a partner.

### Task 8: SEO, sitemap and utility decisions

**Canonical docs:** [`wwc-best/docs/SEO-ROUTE-MATRIX.md`](wwc-best/docs/SEO-ROUTE-MATRIX.md) — route indexability, sitemap membership, canonical/robots; [`wwc-best/docs/UTILITY-PAGES-DECISIONS.md`](wwc-best/docs/UTILITY-PAGES-DECISIONS.md) — forum/club/partner/admin/price/RF-RB decisions.

**Files:**
- Modify: `wwc-best/src/pages/sitemap.xml.ts`
- Modify: `wwc-best/src/pages/robots.txt.ts`
- Modify: `wwc-best/wwc.best.nginx.conf`
- Create: `wwc-best/docs/SEO-ROUTE-MATRIX.md`
- Create: `wwc-best/docs/UTILITY-PAGES-DECISIONS.md`

**Deliverables:**

- [ ] Repair live sitemap and add release check for XML 200/content-type.
- [ ] Remove noindex partner/subdomain URLs from sitemap unless indexing is explicitly approved.
- [ ] Confirm every indexable route has canonical without ref/UTM/country.
- [ ] Confirm legacy routes 301 address-to-address and preserve allowed ref/UTM parameters.
- [ ] Add Yandex `Clean-param` rules for ref and approved UTM keys.
- [ ] Keep `/success/`, `/admin/qr/`, `/v-razrabotke/` and non-unique partner pages out of sitemap with explicit noindex rules.
- [ ] Decide price download: no page; generated current price document; or link to approved official document. Do not implement until freshness owner and source are named.
- [ ] Decide RF/RB selector: no selector; display both currencies; or persisted country selector backed by backend prices/routing. Do not implement until country routing and price source are contracted.
- [ ] Decide QR admin exposure: retain obscure public utility, add site-operator access control, or replace with offline generation. Default recommendation is access control because `noindex` is not authorization.

**Acceptance criteria:**

- Sitemap is 200, valid XML and contains only canonical indexable URLs.
- Query variants never change canonical.
- Utility decisions are signed by product owner before implementation work begins.

### Task 9: Design foundation, accessibility and visual QA

**Docs:** [`wwc-best/design/DESIGN.md`](wwc-best/design/DESIGN.md), [`wwc-best/design/tokens.css`](wwc-best/design/tokens.css), [`wwc-best/docs/VISUAL-QA-LOG.md`](wwc-best/docs/VISUAL-QA-LOG.md)

**Files:**
- Create: `wwc-best/design/DESIGN.md`
- Create: `wwc-best/design/tokens.css`
- Modify: `wwc-best/src/styles/global.css`
- Create: `wwc-best/design/screenshots/`
- Modify one page/section per review unit.

**Dependency:** product owner confirms that current jade/gold visual direction remains; typography is already fixed by final 2026-08-02 spec.

**Deliverables:**

- [x] Extract existing approved tokens from `global.css` without visual redesign. (`design/tokens.css` 2026-08-02)
- [x] Import tokens into global CSS; forbid new page-level hex/font declarations. (documented in DESIGN.md)
- [x] Document Astro component policy. Because the project is not React, shadcn/ui requires React integration; do not add React during P0 solely to replace stable primitives. If React islands are approved later, new buttons/cards/forms must use shadcn mapped to WWC tokens.
- [ ] Replace native select only when its section is touched, using approved accessible custom-select pattern.
- [x] Header slice 2026-08-03: skip link, `aria-current` on active nav, focus-visible on skip link.
- [x] Mobile nav slice 2026-08-03: hamburger + panel below 820px, `aria-expanded`/`aria-controls`, Escape closes, focus first link on open.
- [x] Dialog a11y slice 2026-08-03: OrderModal `role="dialog"`/`aria-modal`, Escape + focus return; advisor panel Escape + `aria-modal` + focus return.
- [ ] Full keyboard audit: labels, error association, live regions and 200% zoom (partial — modals done; form errors/200% smoke captured).
- [ ] Respect reduced motion and minimum 44×44 targets (partial — buttons/marquee pause already in global.css).
- [x] Capture 1440×900 and 390×844 for homepage + price mobile (`docs/qa/screenshots/2026-08-03/`).
- [x] Additionally smoke 360×800, 768×1024 and 200% zoom (`docs/qa/screenshots/2026-08-03/`).
- [x] Price breadcrumb slice 2026-08-03: inline hex → `var(--ink-mute)` / `var(--ink-soft)` from tokens.
- [x] Check Cyrillic rendering is Unbounded/Golos/IBM Plex Mono, not fallback Arial (see VISUAL-QA-LOG 2026-08-03).

**Acceptance criteria:**

- No horizontal scroll or clipped Cyrillic at target viewports.
- Anonymous mode leaves no layout hole or sensitive accessible text.
- New/changed UI has no gradient text, rainbow buttons, default cream/terracotta pattern, emoji icons or repeated generic cards.
- Screenshots are attached to each UI review.

### Task 10: Canary release and pilot observation

**Files:**
- Modify: `wwc-best/docs/SITE-RELEASE-RUNBOOK.md`
- Create per release: `wwc-best/docs/releases/YYYY-MM-DD-<release-id>.md`

**Dependencies:** backend release gate green; API canary green; 2–3 selected pilot refs approved.

**Deliverables:**

- [ ] Deploy first with API modes `canary/legacy` and no user-visible behavior change.
- [ ] Compare adapter API result against local fallback for approved refs; log only mismatch category.
- [ ] Enable v1 ref for 2–3 refs, then v1 leads, then v1 advisor; never switch all three simultaneously.
- [ ] Check anonymous, named, invalid, disabled, switched ref and organic live URLs from desktop and phone.
- [ ] Monitor frontend error categories, lead success ratio, duplicate ratio, advisor fallback/latency and sitemap.
- [ ] Roll back the individual feature mode first; roll back static release if regression persists.
- [ ] Expand to 5–10 partners only after 48 hours without P0 attribution/privacy/delivery defect.

**Acceptance criteria:**

- Two consecutive frontend smoke runs pass before expansion.
- Backend confirms one end-to-end lead for each canary mode reaches correct Telegram owner/watcher without frontend owner data.
- Rollback mode is tested, not merely documented.
- Release report names enabled refs, flags, API versions, screenshots, smoke result and known limitations.

## 7. Three-week sequence for one website developer

### Week 1 — Stabilize and freeze contracts

**Day 1**

- Baseline git/source inventory, release id, build and live route capture.
- Resolve web-root mismatch with site operator.
- Repair sitemap 500 or stop release work until root cause is known.
- Checkpoint with backend: exact v1 paths, schemas and error matrix.

**Day 2**

- Add release runbook, runtime config design and build/release verification.
- Create contract fixtures and failing unit tests for adapters.
- Backend checkpoint: named ref public fields and disabled-ref semantics.

**Day 3**

- Implement HTTP/ref adapters behind `off/fallback/canary/v1`.
- Implement referral pure state and cross-subdomain cookie tests.
- Review: no owner fields and no direct n8n URL in browser.

**Day 4**

- Integrate ref API in canary mode without changing visible behavior.
- E2E: organic/named/anonymous/invalid/disabled/switched/API-down.
- Visual QA for referral-sensitive section at 1440 and 390.

**Day 5 checkpoint**

- Demo first/active behavior across root and subdomain.
- Backend compares API registry with local 5–10 pilot fallback list.
- Gate: no migration to lead v1 unless ref contract and fallback behavior pass.

### Week 2 — Leads and advisor

**Day 6**

- Build unified LeadForm state and lead adapter against fixtures.
- Preserve current form appearance; no page redesign.

**Day 7**

- Connect homepage and order modal.
- Remove ref-mode form replacement; retain direct Telegram as secondary CTA.
- E2E duplicate, honeypot, consent, validation, timeout, 429 and 5xx.

**Day 8 checkpoint**

- Backend DEV enables canary lead endpoint.
- Submit organic, anonymous and two named canary leads.
- Backend confirms attributed/assigned/watchers; frontend confirms it sent no owner id.

**Day 9**

- Advisor adapter and canonical response rendering.
- Trace/error, media/source/clarification and 8-second fallback tests.

**Day 10 checkpoint**

- Backend public advisor canary + frontend performance report.
- Gate: no pilot expansion if structured answer or fallback contract drifts.

### Week 3 — Partners, SEO, analytics and release

**Day 11**

- Complete partner onboarding inventory for 5–10 profiles.
- Disable incomplete public personalization; prepare preview links.

**Day 12**

- Analytics allowlist and privacy inspection.
- Decide Webvisor masking; verify no PII in network.

**Day 13**

- SEO route matrix, sitemap/canonical/noindex cleanup.
- Product decisions meeting: price download, RF/RB selector, QR access.

**Day 14**

- Extract design files; accessibility review and one-section-at-a-time fixes only where pilot flow requires them.
- Playwright screenshots 1440×900 and 390×844; 360/768/200% smoke.

**Day 15 release gate**

- Build, unit, E2E, accessibility, visual and live smoke.
- Enable v1 flags sequentially for 2–3 canary partners.
- Create release report and rehearse flag rollback.

### Optional Week 4 — Controlled expansion

- Observe canary for 48 hours.
- Fix only pilot-blocking defects.
- Expand to 5–10 approved partners.
- Review conversion, delivery success, latency and support incidents with backend DEV.
- Freeze pilot baseline before any catalog API, RAG, payment or admin work.

## 8. Frontend test matrix

Each scenario verifies display, storage, request payload, analytics and recovery.

1. Organic, no prior cookies: organic UI; first/active empty; backend applies default.
2. Anonymous `nnm`: no name/photo/contact in DOM; first=active=nnm; neutral recipient message.
3. Named valid ref: approved profile only; first=active=named; named recipient message.
4. Invalid query ref: organic/neutral UI; cookies unchanged; no owner/ref attribution event.
5. Disabled ref: same safe behavior as invalid plus disabled result category.
6. API unavailable + allowlisted fallback: approved fallback display; lead still sends ref hint only; explicit telemetry category.
7. API unavailable + non-fallback ref: organic/neutral UI; no named data.
8. Switched valid A→B: first=A, active=B; both sent as hints; UI shows B.
9. Subdomain A→root→subdomain B: cross-domain first=A, active=B.
10. Forged `owner_id`/tenant/watchers in browser: adapter drops fields; backend ignores injected raw fields.
11. Duplicate click: button locks; one idempotency key; one backend lead.
12. Retry after timeout: same user intent reuses key; duplicate response becomes success, not a second lead.
13. Honeypot filled: no false success; generic safe message; no bot detail exposed.
14. Consent unchecked: no request.
15. 400/422: field-level correction; values preserved.
16. 429: retry-after message and bounded cooldown.
17. 5xx/non-JSON: safe unavailable state; values preserved; direct Telegram secondary path remains.
18. Advisor structured success: canonical answer/media/source/CTA render.
19. Advisor clarification: options are keyboard-accessible.
20. Advisor timeout/rate limit/malformed/error: safe fallback and trace handling.
21. Canonical with ref/UTM/country: canonical remains clean.
22. Partner/noindex utility pages: absent from sitemap.
23. Anonymous accessibility snapshot: no hidden Viktor/partner data.
24. Reduced motion, keyboard-only, 200% zoom, 390×844 and 1440×900.

## 9. Backend dependencies and questions

### Release blockers

1. Provide the exact site-facing route mapping for all three target `/api/v1` endpoints. Current live routes are `/api/lead`, `/api/advisor/query` and n8n `/webhook/whieda-public-ref-v1`.
2. Freeze JSON schemas and HTTP status/error codes, including request/trace id and cache headers.
3. Extend public ref response with approved photo, bio, contact URL/label and explicit status, or confirm that the site must keep these fields in an editorial source.
4. Define disabled ref response: 404 indistinguishable from unknown, or explicit safe status without private details.
5. Define lead homepage semantics: current live workflow requires non-empty product, while a general question has no product.
6. Define idempotency transport and lifetime: body versus `Idempotency-Key`, duplicate HTTP code and stable response.
7. Define consent payload/version and retention requirements.
8. Confirm backend derives tenant from host and re-resolves both first/active ref; confirm all owner-like frontend fields are ignored.
9. Define 429 response and `Retry-After` behavior for lead, ref and advisor.
10. Freeze advisor canonical request: `session` versus current `session_id`, `slug` versus `product_context`, `ref` versus `ref_context`.
11. Freeze advisor media/source/CTA URL allowlist and trace id property.
12. Agree target latency/SLO: proposed frontend budget is ref 2.5 s, lead 10 s, advisor useful result/fallback by 8 s.
13. Expose canary refs including one anonymous, two named, one disabled and one invalid fixture without private data.
14. Confirm which 5–10 profiles are pilot-enabled and provide publication consent/version.
15. Confirm country routing semantics before any RF/RB selector: accepted country codes, default, currency source and effect on lead routing.

### Coordination rule

Backend answers are incorporated only into frontend adapters/fixtures. They do not authorize website DEV to patch workflows, tables or Sheets, and do not authorize backend DEV to patch `03_Website/`.

## 10. Decision items — explicitly not implementation

- **Price download:** decide source, freshness owner, country/currency and expiry. Recommendation: do not create a downloadable price until it can be generated from an approved published source and dated.
- **RF/RB selector:** decide whether showing both currencies is sufficient for pilot. Recommendation: defer selector until backend country routing and price publication contract are fixed.
- **QR admin:** decide access control. Recommendation: protect at nginx/operator layer; `noindex` alone is insufficient.
- **shadcn/ui on Astro:** decide whether React islands are allowed after pilot. Recommendation: no framework migration in P0; retain shared Astro primitives, enforce tokens, and use shadcn for any later approved React UI.
- **Partner page indexing:** approve individually only when unique content and consent exist; otherwise retain `noindex,follow`.

### Approved next module after Partner Pilot

После выполнения текущего Definition of Done site-разработчик получает отдельный frontend-этап из `backend/platform-api/docs/WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md`:

1. один responsive интерфейс каталога/калькулятора;
2. публичная индексируемая web-страница;
3. PWA shell и установка на рабочий стол;
4. тот же интерфейс внутри Telegram Mini App;
5. крупный quantity stepper, сброс, totals, восстановление и share;
6. только утвержденный same-origin Core API, без локальной формулы цены и второго runtime-каталога.

Core/бот-разработчик заранее предоставляет fixtures и API. Website DEV не реализует серверную корзину и не меняет Telegram launch. Этот раздел фиксирует следующий scope, но не дает права перескочить незакрытые задачи текущего pilot.

## 11. Do not do

- Do not connect browser code to DB, Supabase, Sheets or internal n8n REST.
- Do not trust or calculate owner/assigned owner/watchers in browser.
- Do not embed direct production webhook host as the permanent public contract.
- Do not create separate site copies per partner.
- Do not add new visual variants, page-local palettes or improvised fonts.
- Do not rewrite the whole frontend, migrate away from Astro or add React only for cosmetic reasons during pilot.
- Do not implement RAG, payments, auth, CRM/admin, lead exports or partner analytics dashboard.
- Do not publish unapproved partner photos, contacts, bios, claims or service-center ownership.
- Do not put contacts, message text or arbitrary full URL/query in analytics.
- Do not deploy all ref/lead/advisor v1 switches at once.
- Do not treat build success as release success; live canary, sitemap, lead delivery and rollback are mandatory.

## 12. Definition of Done

The website pilot layer is complete when:

- `/api/v1` adapters are the only component-facing integration boundary;
- local referral registry is a dated fallback, not the runtime authority;
- first/active ref semantics work across root and subdomains;
- organic, anonymous and named forms create exactly one real backend lead;
- frontend never sends trusted owner assignment;
- advisor uses the fixed contract, trace handling and performance fallback;
- 5–10 approved partner profiles pass onboarding completeness;
- analytics measures the funnel without PII;
- sitemap/canonical/noindex behavior is correct and live sitemap is 200;
- price/RF-RB/QR decisions are recorded without unauthorized implementation;
- unit/E2E/accessibility/visual checks pass;
- canary and rollback are proven;
- release report confirms backend delivery while no backend code was changed by website DEV.
