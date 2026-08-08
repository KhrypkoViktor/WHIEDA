# WHIEDA QA Coverage Matrix

**Corpus:** `qa/cases/whieda_regression_cases_v1.jsonl`  
**Runner:** `qa/run_whieda_regression.py` (offline)  
**Last validated:** 262 cases, validation PASS

## Coverage by block

| Блок | Кейсов | P0 | Что проверяем | Пробелы |
|---|---:|---:|---|---|
| `catalog_card` | 38 | 35 | Карточка товара, canonical name, описание | Нет проверки live-фото URL |
| `catalog_price` | 36 | 35 | Розница, PV, повторка, partner price | Live-курс валют не проверяется |
| `aliases_typo` | 38 | 5+ | Опечатки, транслит, короткие алиасы | Не все sheet-алиасы из prod |
| `followup_context` | 31 | 28 | «а цена?», «дай фото» после карточки | Session persistence только в live Core |
| `photo_video_certificate` | 26 | 25 | structured_photo/video/certificate/detail | Байты медиа не проверяются офлайн |
| `comparison` | 21 | 0 | PRO vs base, эликсиры, наборы | Сложные compare-layer — частично |
| `cart_and_basket` | 21 | 1 | Корзина, starter basket, сумма PV | Starter basket rules — legacy parity |
| `business_faq` | 21 | 0 | PV, повторка, MLM-возражения | Нет live marketing plan numbers |
| `promotion_event` | 16 | 0 | Акции, ивенты, community | Контент акций устаревает — P2 only |
| `safety_and_clarification` | 16 | 7 | Не-диагноз, clarification паста/цвет | Нет юридической экспертизы формулировок |

## Что уже можно проверять Core (offline corpus + unit tests)

- Режим ответа (`expected_mode`): `structured_*`, `clarification`, `knowledge_gap`
- Tenant/host изоляция (отдельный local core lab)
- Must contain / must not contain контракт на текст ответа
- Follow-up с `context_before`
- Ambiguity: «паста», «красный», короткий «активатор»
- Safety: отсутствие диагноза/лечения/гарантии в must_not

## Что пока только legacy n8n / live

- Review commands (`/review_stats`)
- Sheet sync freshness (Google Sheets master)
- Telegram delivery bytes (photo caption split)
- Deep route / Dify shadow
- Live promotion dates and event calendars

## Что невозможно без live-данных

- Актуальные цены и PV из runtime DB/sheets
- Реальные URL фото/видео/сертификатов
- Telegram webhook E2E с Bot API
- n8n execution latency and backlog
- Partner-specific discounts beyond corpus fixtures

## Release gate — 20 обязательных кейсов перед релизом

| # | case_id | Зачем |
|---|---------|-------|
| 1 | `CAT-001` | Базовая карточка активатора |
| 2 | `CPR-001` | Цена активатора + PV |
| 3 | `ALI-001` | Короткий алиас «активатор» |
| 4 | `ALI-002` | Опечатка «ативатор» |
| 5 | `FUP-001` | Follow-up «а сколько стоит?» |
| 6 | `FUP-002` | Follow-up «а повторка?» |
| 7 | `MED-001` | Фото активатора |
| 8 | `MED-011` | Видео активатора |
| 9 | `CMP-001` | PRO vs base compare |
| 10 | `CRT-001` | Корзина активатор+бэм+ба-гуа |
| 11 | `BUS-001` | Бизнес FAQ «повторка» |
| 12 | `SAF-001` | Медицинский limit |
| 13 | `SAF-003` | Clarification «паста» |
| 14 | `SAF-004` | Clarification «красный» |
| 15 | `FUP-031` | «а цена?» с context |
| 16 | `CPR-004` | PRO price |
| 17 | `CAT-007` | PRO card |
| 18 | `ALI-037` | Алиас activator pro |
| 19 | `MED-026` | Фото+видео combo |
| 20 | `CRT-021` | Корзина с PV sum |

## One-command QA

```powershell
cd D:\Projects\WHIEDA
.\qa\run_all_qa.ps1
```

## Known gaps (honest)

1. Corpus offline — не вызывает advisor API; нужен отдельный live smoke для PASS/FAIL по ответам.
2. `platform_tenant_rls_legacy_leads_v1.sql` всё ещё вне git — влияет на local Core lab, не на corpus.
3. Promotion/community cases — P2, зависят от свежести контента.
4. Media cases проверяют mode contract, не HTTP 200 на CDN URL.
