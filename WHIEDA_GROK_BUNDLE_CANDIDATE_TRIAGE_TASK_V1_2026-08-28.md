# WHIEDA: разбор заготовок наборов для Grok

## Зачем

В Core уже будут подключены только три старых активных набора. В базе лежит ещё
51 заготовка, собранная из RAW-диалогов. Их нельзя просто включить: среди них
есть повторы, неясные товары, старые формулировки и записи без понятного
источника. Нужен аккуратный список для владельца, а не новая публикация.

## Что разрешено

Работать только в `qa/whieda_bundle_triage/` и создать там:

1. `bundle_candidate_triage_v1.tsv` — одна строка на каждую текущую запись из
   `advisor_bundle_staging_records` (ожидается 51).
2. `unknown_item_resolution_v1.tsv` — отдельная очередь для позиций, которые
   не удалось уверенно связать с каталогом.
3. `active_bundle_regression_cases_v1.jsonl` — по 8-12 живых фраз на каждый
   из трёх уже активных наборов.
4. `README.md` с одной командой проверки.
5. `run_bundle_candidate_triage.py --offline` и минимум 8 pytest-проверок.
6. `BUNDLE_CANDIDATE_TRIAGE_LOCAL_REPORT.md` с цифрами и списком того, что
   ждёт решения Виктора.

## Откуда читать

Можно только читать:

- `n8n/current/run_whieda_bundles_staging.py` — поля и статусы staging.
- `n8n/current/build_whieda_bundle_review_packs.py` — существующий экспорт
  review-пакета.
- локальный `09_BUNDLE_CANDIDATES.tsv`, если он есть в рабочей папке или в
  сохранённых выгрузках;
- `n8n/current/whieda_bundle_aliases_v1.json`;
- master snapshot каталога (`products`, `aliases`, `product_cards`);
- existing `qa/inventory/`, `qa/no_blind_zone/`, `qa/telegram_golden/` —
  только как источники реальных фраз.

Если файла с 51 строкой нет в этом worktree, не выдумывай записи: добавь
понятный `source_missing` в отчёт и сделай инструмент, который примет путь
`--input <09_BUNDLE_CANDIDATES.tsv>`.

## Как размечать

Для каждой записи `record_id` заполнить:

- `decision`: `ready_for_owner_review`, `duplicate`, `needs_product_mapping`,
  `needs_source`, `blocked_claim`, `archive`;
- `canonical_candidate_id`: ID основной записи, если это дубль;
- `catalog_mapping`: SKU только при точном или сильном alias-match;
- `confidence`: `high`, `medium`, `low`;
- `reason`: коротко и по факту;
- `source_id`, `source_locator`, `raw_quote_ref`: provenance, без копирования
  длинных RAW-диалогов;
- `owner_question`: один конкретный вопрос, если без Виктора не решить.

Правила:

- Не придумывать товары, состав, дозировки, цены или источники.
- Не менять текст исходной записи и не писать за владельца «approved».
- Не переносить в regression corpus фразы с диагнозом или обещанием результата.
- Повтор с тем же смыслом и SKU отмечать `duplicate`, не плодить варианты.
- Неясный товар отправлять в `unknown_item_resolution_v1.tsv`, а не угадывать.

## Три активных набора: только тестовые фразы

Сделай regression cases для уже включённых наборов:

| ID | Фразы-ориентиры |
|---|---|
| `bundle_energy_immunity` | энергия, усталость, туман в голове, батарейка |
| `bundle_vessels_belly` | вздутие, живот, кишечник, сосуды |
| `bundle_shape_recovery` | стройность, вес, аппетит, пептид |

Каждый case: `case_id`, `user_text`, `expected_bundle_id`, `must_contain`,
`source.ref`. Не трогать Core, тесты только lint/offline.

## Жёстко не делать

Не менять:

- `backend/platform-api/app/**`;
- `postgres/**`;
- `n8n/**`;
- Google Sheets, live Postgres, Telegram, Docker, deploy;
- активные 3 runtime-набора.

Не создавать SQL, не запускать publish/import, не трогать чужие изменения.

## Приёмка

```bash
python qa/whieda_bundle_triage/run_bundle_candidate_triage.py --offline
python -m pytest qa/whieda_bundle_triage/tests -q
```

В отчёте обязательно отдельно написать:

- сколько исходных строк реально прочитано;
- сколько ready / duplicate / needs mapping / needs source / blocked;
- какие 10 вопросов владельцу имеют наибольший эффект;
- что **не опубликовано**.
