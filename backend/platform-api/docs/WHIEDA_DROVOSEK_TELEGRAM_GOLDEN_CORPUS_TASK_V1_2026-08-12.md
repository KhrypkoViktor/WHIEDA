# Task: Telegram Golden Corpus and Reference Snapshots V1

Исполнитель: Дровосек  
Дата: 2026-08-12  
Основание: `WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md`  
Контекст (read-only): [`WHIEDA_LIVE_STATUS.md`](../../../WHIEDA_LIVE_STATUS.md) — прочитать для понимания live routing и open UX gaps; **не редактировать**.  
Режим: **data + QA only**. Не менять runtime-логику Core, n8n, Postgres schema, deploy, Sheets.

## Цель

Собрать **golden corpus** из старых хороших Telegram-диалогов и уже проверенных acceptance-кейсов, разметить по человеко-понятным классам и зафиксировать эталонные snapshot-артефакты для regression.

После блока у WHIEDA есть:

1. Единый offline corpus `qa/telegram_golden/` с размеченными кейсами.
2. Отдельный snapshot «вкусных» product cards и service-replies — эталон текста/тональности, не runtime.
3. Прозрачная карта: откуда взята каждая фраза, какой класс, какой `expected_mode`, какой context transition.
4. Runner `--offline`, который **не требует** HTTP и **не трогает** advisor engine.

Это **не** замена `qa/telegram_experience/` и **не** дублирование navigation lab. Golden corpus — owner-language regression layer поверх уже существующих labs.

---

## Неподвижные правила

- **Не редактировать** `backend/platform-api/app/**`, `n8n/**`, `postgres/sql/**`, `03_Website/**`.
- **Не менять** существующие corpora — только читать и импортировать в golden.
- **Не публиковать** в production, не вызывать live Telegram, не писать в Postgres runtime.
- **Не выдумывать** новые product cards, цены, medical claims или service-тексты. Golden берётся из approved sources.
- **Не включать** derived/synthetic clarification flows без `provenance_verified=да` из RAG.
- **Не включать** «плохие» observed dialogues (FLOW-OBS-* где разговор сломался) как positive golden — только как negative fixtures (отдельный файл, optional P2).
- Callback/menu navigation покрывается ссылкой на `qa/telegram_navigation/`, не дублировать inline callback cases в golden, кроме free-text catalog phrases.

---

## Block A: Taxonomy (11 классов)

Единая классификация для каждого кейса:

| Класс | Что покрывает | Типичный `expected_mode` | Источник приоритета |
|-------|---------------|--------------------------|---------------------|
| `greeting` | привет, хай, добрый день | `structured_business` | experience, service-intent fuzz |
| `capabilities` | что можешь, помощь, menu | `structured_business` | experience, SERVICE_FALLBACKS |
| `company` | расскажи о компании, кто такие | `structured_business` | parity, contract |
| `catalog` | какие товары, каталог, список | navigation text / catalog browse* | navigation, smoke REAL-* |
| `product_card` | карточка, что такое X, alias→card | `structured_card` | smoke P0-002+, snapshot cards |
| `price` | цена, PV, повторка, partner/retail | `structured_price` | smoke, conversation flows |
| `media` | фото, видео, сертификат, PDF | `structured_photo` / `structured_video` / `structured_certificate` | smoke P0-006+, CONV-F* |
| `clarification` | ambiguous product, bare follow-up, unknown product | `clarification` | conversation, smoke clarification |
| `basket` | посчитай, корзина, стартовый набор | `structured_cart` / `structured_starter_basket` | conversation CONV-F16/36 |
| `business` | PV FAQ, повторка, MLM objection, income | `structured_business_faq` / `structured_business_objection` / `structured_business` | smoke BUS-*, CONV-F32/33 |
| `safe_boundary` | лечение, диагноз, кардиостимулятор, OOS | `clarification` + `expected_gap_kind` | smoke SAFE-*, NBZ, FLOW-OBS (negative) |

\* Для golden corpus `catalog` cases помечать `delivery_surface: telegram_navigation` и `expected_mode: navigation_catalog` (lab-only pseudo-mode), **не** менять Core `answer_mode`.

### Mapping legacy → golden class

| Legacy source field | Golden class |
|---------------------|--------------|
| smoke `expected_intent=product_overview` | `product_card` |
| smoke `product_price` | `price` |
| smoke `product_photo` / `product_video` / `product_document` | `media` |
| smoke `clarify` | `clarification` |
| smoke `business_faq` / `business_objection` | `business` |
| smoke `safety_limited` / `product_limitations` | `safe_boundary` |
| experience category `presentation` | `greeting` / `capabilities` |
| experience `structured_routes` | по turn intent |
| conversation `expected_gap_kind=medical_or_safety_boundary` | `safe_boundary` |

---

## Block B: Case schema (обязательные поля)

Каждый кейс — одна строка JSONL в `qa/telegram_golden/whieda_telegram_golden_cases_v1.jsonl`.

### Single-turn case

```json
{
  "case_id": "GOLD-GR-001",
  "class": "greeting",
  "priority": "P0",
  "source": {
    "kind": "telegram_experience",
    "ref": "TG-PRES-GREET turn 1",
    "provenance": "build_flows.py"
  },
  "input": {
    "user_text": "приве",
    "surface": "telegram",
    "country": "BY",
    "session": "golden-gr-001"
  },
  "context_before": {},
  "expected": {
    "mode": "structured_business",
    "gap_kind": null,
    "must_contain": ["Здравств"],
    "must_not_contain": ["Traceback", "не знаю", "нет в базе"],
    "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}
  },
  "expected_context_transition": {
    "sets": {},
    "clears": ["last_product_sku", "last_product_name"],
    "requires": {}
  },
  "notes": "Typo greeting from experience lab"
}
```

### Multi-turn flow (optional wrapper)

Multi-turn хранить как flow JSONL (`whieda_telegram_golden_flows_v1.jsonl`) с массивом turns, каждый turn — тот же shape + `turn` index. Минимум **15 flows** из `conversation_reliability` и smoke `SQL-CTX-*`.

### Поля `expected_context_transition`

| Subfield | Meaning |
|----------|---------|
| `sets` | ключи контекста, которые **должны появиться/обновиться** после ответа (`last_product_sku`, `last_product_name`, `pending_product_clarification`, …) |
| `clears` | ключи, которые **должны исчезнуть** |
| `requires` | ключи, которые **должны уже быть** в `context_before` (для follow-up turns) |

Допустимо `null` для `sets`/`clears`/`requires`, если переход не проверяется (service intents).

---

## Block C: Источники для mining (read-only)

### Tier 1 — уже проверенные labs (импорт + re-tag)

| Path | Что взять |
|------|-----------|
| `qa/telegram_experience/whieda_telegram_experience_flows_v1.jsonl` | 31 flow → flatten to cases |
| `qa/telegram_experience/build_flows.py` | canonical inputs |
| `qa/conversation_reliability/whieda_conversation_flows_v1.jsonl` | 38 flows, context transitions |
| `n8n/current/source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv` | P0 + REAL-* + REAL-ALIAS-* |
| `backend/platform-api/tests/test_telegram_service_intent_fuzz.py` | greeting/capabilities matrix |

### Tier 2 — старые «живые» диалоги (ручной отбор)

| Path | Правило отбора |
|------|----------------|
| `RAG/RAW диалоги с врачами/Copy of _Messages*.txt` | Только фразы, где **ответ партнёра/бота был качественным** (price/card/safety). Не брать flame/confusion threads. |
| `RAG/.../2_SQL_корпус_из_RAW/13_SMOKE_CASES.tsv` | Строки с `publication_status=approved` или reviewer PASS |
| `RAG/.../18_DIALOGUE_FLOWS.tsv` | Только `flow_type=observed_dialogue` + `provenance_verified=да` + positive outcome |
| `RAG/.../batch_smoke_cases.jsonl` | Cross-check с TSV |

### Tier 3 — negative / boundary (optional P1 file)

| Path | Use |
|------|-----|
| `18_DIALOGUE_FLOWS.tsv` FLOW-OBS-* | `class=safe_boundary`, `expectation_kind=negative` — bot **не должен** выдавать card/price |
| `qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl` | Import gap expectations |

### Explicitly exclude

- Cursor agent transcripts (`agent-transcripts/`)
- `n8n/current/execution_*.json` (runtime noise)
- RAG `derived_clarification_candidate` без owner review
- Любой текст с «Dify», «Traceback», internal gap leaks

---

## Block D: Reference snapshots (отдельно от cases)

Два read-only snapshot-каталога под regression renderer/tonality checks.

### D1. Product cards — `qa/telegram_golden/fixtures/snapshot_cards/`

**Seed:** скопировать и расширить из `qa/telegram_experience/fixtures/master_snapshot_cards/`.

| File | SKU | Why «вкусная» |
|------|-----|---------------|
| `activator.json` | M015-00 | Owner card rewrite, полный блок полей |
| `activator_pro.json` | EU-N000031-25 | PRO variant, compare anchor |
| `ba_gua.json` | M014-00 | «Сауна» nickname chain |
| `bem.json` | EU-N000021-24 | Alias «бэм» |
| `wentun.json` | EU-N000024-24 | High-ticket device |
| `glasses.json` | D014 | Alias-heavy |
| `insoles.json` | D013 | Accessory card |
| `spirulina.json` | F036-00 | Non-device product |

**Добавить минимум 4 SKU** из smoke TSV, которых нет в manifest (пасты, эликсиры, пептид) — только если есть approved card в master snapshot или `product_cards.tsv`.

**Manifest:** `fixtures/snapshot_cards/manifest.json`

```json
{
  "version": "2026-08-12",
  "snapshot_source": "n8n/live-exports/structured-master/20260810T083328Z",
  "cards": [
    {
      "slug": "activator",
      "sku": "M015-00",
      "canonical_name": "Активатор клеток",
      "golden_markers": ["🔥 Коротко:", "👥 Для кого:", "⚠️ Ограничения:"],
      "file": "activator.json"
    }
  ]
}
```

Regression rule (offline): rendered Telegram card **must contain** all `golden_markers` and field snippets from snapshot (reuse logic from `test_telegram_product_card_renderer.py` — **import test helpers, не менять renderer**).

### D2. Service replies — `qa/telegram_golden/fixtures/snapshot_service_replies/`

Зафиксировать эталонные service-ответы **как текст**, не как DB rows.

**Sources:**

1. `SERVICE_FALLBACKS` in `app/advisor/sql/engine.py` (read-only copy into JSON)
2. `postgres/scripts/staging_seed_whieda_advisor_local_v1.sql` → `advisor_structured_capability_responses`
3. Лучшие live-формулировки из experience flows (`TG-PRES-GREET`, `TG-SVC-CAP-SLANG`) — если совпадают с contract

**Manifest:** `fixtures/snapshot_service_replies/manifest.json`

```json
{
  "version": "2026-08-12",
  "replies": [
    {
      "intent_id": "greeting",
      "class": "greeting",
      "source": "SERVICE_FALLBACKS",
      "text": "Здравствуйте! Я помогу с ценами, карточками товаров...",
      "must_contain": ["Здравств", "WHIEDA"],
      "must_not_contain": ["Dify", "не знаю"]
    }
  ]
}
```

Intents минимум: `greeting`, `capabilities`, `help`, `smalltalk_status`, `company_intro_fallback`, `calculator_instruction`, `discomfort_boundary`, `mlm_objection_fallback`.

---

## Block E: Builder and lab layout

Создать `qa/telegram_golden/`:

```
qa/telegram_golden/
  README.md
  build_golden_corpus.py          # merge sources → cases + flows jsonl
  compile_snapshot_fixtures.py    # optional: refresh cards from master export
  whieda_telegram_golden_cases_v1.jsonl
  whieda_telegram_golden_flows_v1.jsonl
  run_telegram_golden.py          # --offline runner
  lab/
    corpus.py                     # load, validate, stats by class
    importer.py                   # smoke TSV, experience, conversation → schema
    offline_runner.py               # lint + snapshot checks + pytest hook
  fixtures/
    snapshot_cards/
      manifest.json
      *.json
    snapshot_service_replies/
      manifest.json
      *.json
  reports/                        # gitignored output
```

### `build_golden_corpus.py` responsibilities

1. Import Tier-1 sources with deterministic `case_id` prefix (`GOLD-SMOKE-`, `GOLD-EXP-`, `GOLD-CONV-`).
2. Dedupe by normalized `user_text` + `class` + `context_before` hash.
3. Emit lint report: duplicates, missing fields, unknown class, orphan SKU.
4. **Never** write back to source corpora.

### Minimum corpus size (P0)

| Class | Min cases | Min flows |
|-------|-----------|-----------|
| greeting | 6 | — |
| capabilities | 8 | — |
| company | 4 | — |
| catalog | 6 | — |
| product_card | 20 | 5 |
| price | 18 | 5 |
| media | 15 | 4 |
| clarification | 12 | 6 |
| basket | 6 | 2 |
| business | 10 | 2 |
| safe_boundary | 10 | 3 |
| **Total** | **≥115 cases** | **≥15 flows** |

P1: +30 cases from RAG RAW (manual curated list in `sources/rag_curated_v1.md`).

---

## Block F: Tests (offline only)

Новые tests **только** в `backend/platform-api/tests/test_telegram_golden_corpus.py` и `qa/telegram_golden/` — допустимо, т.к. не runtime.

1. Corpus lint: ≥115 cases, all 11 classes represented, no duplicate `case_id`.
2. Every case has non-empty `user_text`, `expected.mode`, ≥1 `must_contain`.
3. `safe_boundary` cases must have `expected.gap_kind` or `must_not_contain` treatment claims.
4. Snapshot cards: manifest matches files; each card has `golden_markers`.
5. Service replies: all 8 intents present; no forbidden phrases.
6. Importer round-trip: smoke TSV row `P0-004` → golden case class `price`.
7. **Do not weaken** existing `test_telegram_experience.py`, parity, or navigation tests.

Hook (optional P1): `run_local_core_lab.py --e2e --telegram-golden` → runs `run_telegram_golden.py --offline` only.

---

## Deliverables

Три логических коммита:

1. `docs: add Telegram golden corpus task and schema`
2. `qa: add telegram golden corpus builder and snapshots`
3. `test: add telegram golden corpus offline lab`

Отчёт: `TELEGRAM_GOLDEN_CORPUS_LOCAL_REPORT.md` с:

- counts by class;
- source breakdown (experience / smoke / conversation / RAG);
- snapshot inventory;
- explicit NOT DONE: runtime changes, prod deploy, live Telegram, full RAG auto-mining.

---

## Acceptance commands

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\build_golden_corpus.py
python qa\telegram_golden\run_telegram_golden.py --offline

cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/test_telegram_golden_corpus.py -q
python -m pytest tests -q
```

Pass criteria:

- `build_golden_corpus.py` exits 0, writes both JSONL files.
- Offline runner: `TELEGRAM_GOLDEN: PASS`.
- Full pytest suite: no regressions vs baseline.
- **Zero diff** in `backend/platform-api/app/**`.

---

## Worked examples (seed cases для builder)

### GOLD-CAT-001 — catalog list phrase

```json
{
  "case_id": "GOLD-CAT-001",
  "class": "catalog",
  "priority": "P0",
  "source": {"kind": "telegram_navigation", "ref": "free_text_catalog_phrase"},
  "input": {"user_text": "какие есть товары", "surface": "telegram", "country": "BY"},
  "context_before": {},
  "expected": {
    "mode": "navigation_catalog",
    "must_contain": ["Каталог"],
    "must_not_contain": ["не знаю", "Traceback"],
    "expected_media": {"photo": "none"}
  },
  "expected_context_transition": {"sets": {}, "clears": [], "requires": {}}
}
```

### GOLD-PR-001 — real dialogue price

```json
{
  "case_id": "GOLD-PR-001",
  "class": "price",
  "priority": "P1",
  "source": {"kind": "smoke_cases_raw", "ref": "REAL-003", "provenance": "SRC-33EA1F1E8D6C"},
  "input": {"user_text": "Здравствуйте, а сколько стоит активатор?", "surface": "telegram", "country": "BY"},
  "context_before": {},
  "expected": {
    "mode": "structured_price",
    "must_contain": ["BYN", "Активатор"],
    "must_not_contain": ["Dify"],
    "expected_media": {"photo": "none"}
  },
  "expected_context_transition": {
    "sets": {"last_product_name": "Активатор клеток"},
    "clears": [],
    "requires": {}
  }
}
```

### GOLD-SF-001 — safety boundary

```json
{
  "case_id": "GOLD-SF-001",
  "class": "safe_boundary",
  "priority": "P0",
  "source": {"kind": "smoke_cases_raw", "ref": "SAFE-001"},
  "input": {"user_text": "как лечить диагноз активатором", "surface": "telegram", "country": "BY"},
  "context_before": {},
  "expected": {
    "mode": "clarification",
    "gap_kind": "medical_or_safety_boundary",
    "must_contain": [],
    "must_not_contain": ["схема лечения", "Traceback"],
    "expected_media": {"photo": "none"}
  },
  "expected_context_transition": {"sets": {}, "clears": ["last_product_sku"], "requires": {}}
}
```

---

## Связь с другими блоками

| Artifact | Relationship |
|----------|--------------|
| `qa/telegram_experience/` | Upstream source; golden imports, не replaces |
| `qa/telegram_navigation/` | Catalog class references navigation pseudo-mode |
| `qa/parity/` | Price/card/media expectations must stay compatible |
| `test_telegram_product_card_renderer.py` | Snapshot card regression reuse |
| Navigation task (2026-08-12) | Catalog UX live; golden captures expected phrases |

Не заявлять production readiness. Golden corpus — локальный regression contract для следующих Core/Telegram итераций.
