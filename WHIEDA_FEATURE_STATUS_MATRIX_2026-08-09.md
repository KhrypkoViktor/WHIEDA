# WHIEDA Feature Status Matrix

Date: 2026-08-09
Scope: read-only inventory — facts only, no «everything is ready» claim.

Status legend:
- **LIVE_CONFIRMED** — fresh live routing/response proof in WHIEDA_LIVE_STATUS.md
- **IMPLEMENTED_NOT_LIVE_PROVEN** — Core code + local parity, not user-visible on prod
- **DATA_READY_NOT_CONNECTED** — files/Dify/DB staging exist, runtime off
- **STAGING_OR_REVIEW** — intentionally withheld pending review
- **MISSING** — no code and no data path
- **BLOCKED_BY_OWNER_OR_DOCTOR** — external approval required

| # | Function | Status | Evidence | User sees today | Blocker |
|---:|---|---|---|---|---|
| 1 | Карточка товара | `IMPLEMENTED_NOT_LIVE_PROVEN` | engine.py structured_card; parity CAP-02; local 85/85 | Telegram идёт в Core; прямой live-probe и webhook подтверждены 2026-08-09 | Нужен один smoke реального пользователя; сайт advisor остаётся shadow |
| 2 | Цена / первичка / повторка / PV / W$ | `IMPLEMENTED_NOT_LIVE_PROVEN` | formatters.format_price; parity CAP-03/24; Sheets→Postgres on prod for legacy | Telegram идёт в Core; Core parity PASS local | Нужен один smoke реального пользователя; сайт advisor остаётся shadow |
| 3 | Фото | `IMPLEMENTED_NOT_LIVE_PROVEN` | structured_photo; Resource_Links; parity CAP-05/20 | Telegram идёт в Core; Core local PASS | Нужен smoke фото реального пользователя |
| 4 | Видео | `IMPLEMENTED_NOT_LIVE_PROVEN` | structured_video; resources table; parity P1-113 | Доступно при привязке товара; Core local PASS | Нужен smoke видео реального пользователя |
| 5 | PDF / сертификаты | `IMPLEMENTED_NOT_LIVE_PROVEN` | structured_certificate; L-ACT-PDF seed; parity P1-126 | Core local PASS; Telegram route is Core | Distillate resource links not fully synced |
| 6 | Сравнение товаров | `IMPLEMENTED_NOT_LIVE_PROVEN` | comparison.py; parity CAP-09; seed comparisons | Core local PASS; Telegram route is Core | Full distillate comparisons not imported |
| 7 | Follow-up контекст | `IMPLEMENTED_NOT_LIVE_PROVEN` | platform_session_context; parity CAP-06/07 | Core session merge proven local; Telegram route is Core | Нужен multi-turn smoke реального пользователя |
| 8 | Опечатки и алиасы | `IMPLEMENTED_NOT_LIVE_PROVEN` | resolver.py QUERY_TYPO_MAP; parity CAP-04 | Core resolver local PASS; Telegram route is Core | Distillate 64 aliases not fully published |
| 9 | Уточнения вместо fallback | `IMPLEMENTED_NOT_LIVE_PROVEN` | ambiguity.py; clarification prompts; parity CAP-08 | Core local PASS for color/pasta/activator | Distillate 24 rules not imported wholesale |
| 10 | Корзина (явный список) | `IMPLEMENTED_NOT_LIVE_PROVEN` | cart_list.py; parity CAP-10 | Core local PASS; Telegram route is Core | Нужен smoke реального пользователя |
| 11 | Стартовый подбор / basket | `IMPLEMENTED_NOT_LIVE_PROVEN` | basket.py; parity P1-100; starter templates seed | Core local PASS; Telegram route is Core | Sheets starter rules not fully on prod |
| 12 | Акции РБ | `IMPLEMENTED_NOT_LIVE_PROVEN` | advisor_promotions; parity CAP-12 | Structured layer exists; needs daily freshness check | No automated expiry smoke on prod |
| 13 | Бизнес FAQ | `IMPLEMENTED_NOT_LIVE_PROVEN` | business_faq; parity CAP-13; distillate 35 FAQ candidates | Core local PASS; Telegram route is Core | Distillate FAQ not auto-imported |
| 14 | Возражения | `IMPLEMENTED_NOT_LIVE_PROVEN` | objections + coach; parity CAP-14; distillate 37 | Core local PASS; Telegram route is Core | Distillate objections not fully published |
| 15 | Safety (no treatment advice) | `IMPLEMENTED_NOT_LIVE_PROVEN` | SAFETY_TREATMENT_RE; parity CAP-18; distillate 56 signals blocked | Core clarifies instead of diagnosing — local PASS; Telegram route is Core | Safety signals remain review-only data |
| 16 | Отзывы | `STAGING_OR_REVIEW` | 07_TESTIMONIALS.tsv 73 rows; RAW ~9276+ lines | Not shown to users as structured layer | Consent + manual review; no auto-publish |
| 17 | Problem → Solution | `STAGING_OR_REVIEW` | 08_PROBLEM_SOLUTION 59 candidates | Not connected to runtime routing | Medical review on flagged rows |
| 18 | Бандлы | `STAGING_OR_REVIEW` | 09_BUNDLE_CANDIDATES 25; starter basket partial | Starter basket only — not full bundle catalog | blocked_raw — doctor/leader review |
| 19 | Глубокий RAG | `DATA_READY_NOT_CONNECTED` | deep-corpus 14 docs; Dify Deep_corpus loaded | CORE_ROUTE_DEEP=off — users do not see | Owner decision to enable deep route |
| 20 | Интернет / web search | `MISSING` | No implementation in Core or n8n advisor | Not available | Out of scope for current Core SQL advisor |
| 21 | Коуч (7 дней / возражения) | `IMPLEMENTED_NOT_LIVE_PROVEN` | coach.py in engine; objections seed | Code path exists; Telegram route is Core | Not live-proven; no operator UI |
| 22 | Пользователи / доступ | `STAGING_OR_REVIEW` | platform_identity_journey DDL; Users_Access historical | Bot should not silence new user — admin flow unverified | Not deployed to prod |
| 23 | Лиды / ref | `LIVE_CONFIRMED` | WHIEDA_LIVE_STATUS: CORE_ROUTE_PUBLIC_REF=core, CORE_ROUTE_LEADS=core | Ref reads and lead ingest on Core (verified 2026-08-07) | Site pilot still mixed legacy API flags |
| 24 | События / рассылки / community | `IMPLEMENTED_NOT_LIVE_PROVEN` | events/community tables; parity CAP-15 | Core local PASS; n8n broadcast layer not productized | No production smoke for mailings |
| 25 | Аналитика / gaps | `DATA_READY_NOT_CONNECTED` | interaction_events catalog; 14_KNOWLEDGE_GAPS 34 rows | Events spec exists; operator gap screen not live | Owner cabinet analytics partial local only |

## Summary counts

- `LIVE_CONFIRMED`: 1
- `IMPLEMENTED_NOT_LIVE_PROVEN`: 17
- `DATA_READY_NOT_CONNECTED`: 2
- `STAGING_OR_REVIEW`: 4
- `MISSING`: 1
