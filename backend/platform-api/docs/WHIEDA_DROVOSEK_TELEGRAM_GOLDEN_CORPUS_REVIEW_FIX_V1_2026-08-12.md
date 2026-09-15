# Review Fix: Telegram Golden Corpus V1.1

Исполнитель: Дровосек  
Основание: `WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_TASK_V1_2026-08-12.md`  
Режим: **QA data only**. Не менять runtime.

## Принято

- Builder воспроизводимо создаёт 186 cases и 35 multi-turn flows.
- Все 11 классов проходят минимальные пороги.
- Offline runner и 7 corpus-тестов зелёные.

Не переписывать уже сделанное. Сделать только следующие закрывающие правки.

## A. Расширить snapshot карточек

В `qa/telegram_golden/fixtures/snapshot_cards/` добавить минимум 4 карточки,
которых нет в текущем manifest, только из master snapshot
`n8n/live-exports/structured-master/20260810T083328Z/product_cards.tsv`.

Обязательный набор:

1. Низкомолекулярный соевый пептид (`F038-00`);
2. Эликсир Фохоу (`F001-02`);
3. Эликсир 3 Драгоценности (`F002-02`);
4. Эликсир Саньцин (`F003-02`);
5. Паста Цинфэн (`F071-00`) — добавить, если в snapshot есть полный approved card.

Для каждого: файл JSON, `sku`, canonical name, snapshot source, golden markers.
Обновить manifest и offline snapshot validation. Не менять renderer в `app/**`.

## B. RAG observed-dialogue safety fixtures

Создать отдельный файл:

`qa/telegram_golden/whieda_telegram_golden_negative_fixtures_v1.jsonl`

Источник: только пять `observed_dialogue` из
`RAG/1 компиляция. диалоги с врачами/2_SQL_корпус_из_RAW/18_DIALOGUE_FLOWS.tsv`,
у которых `provenance_verified=да`.

Это **не positive golden** и не текст для публикации. Каждый fixture хранит:

- `fixture_id`;
- `source.flow_id`, `source_id`, `source_locator`, `provenance_verified`;
- исходный вопрос без лишней цитаты;
- `class: safe_boundary`;
- `expected_mode: clarification`;
- `expected_gap_kind: medical_or_safety_boundary`;
- запреты: не возвращать product card, цену, инструкцию по лечению;
- `review_status: blocked_raw_internal_only`.

Не копировать медицинские ответы из RAW как эталон. Извлекать только безопасную
границу маршрута: в экстренном/диагностическом/детском/ветеринарном вопросе
бот не подменяет врача или ветеринара и не продолжает товарный сценарий.

## C. Реальный provenance вместо копирования фраз

`service_intent_fuzz` сейчас описан как mirror. Исправить provenance:

- либо детерминированно читать фразы из
  `backend/platform-api/tests/test_telegram_service_intent_fuzz.py`;
- либо пометить каждую вручную заданную фразу `source.kind=golden_test_fixture`,
  `source.provenance=manual_test_matrix`, а не выдавать за импорт.

В lint добавить проверку: source kind/provenance обязательны и не могут
утверждать несуществующий внешний источник.

## D. Hygiene and report

- Не добавлять `__pycache__`, `reports/`, generated `build_lint.json` в Git.
- Добавить local `.gitignore` в `qa/telegram_golden/`, если текущий корневой
  ignore этого не обеспечивает.
- Обновить `TELEGRAM_GOLDEN_CORPUS_LOCAL_REPORT.md`: positive cases отдельно,
  negative safety fixtures отдельно; явно написать, что raw fixtures internal-only.
- Обновить README с одной командой полного offline прогона.

## Acceptance

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\build_golden_corpus.py
python qa\telegram_golden\run_telegram_golden.py --offline
python -m pytest backend\platform-api\tests\test_telegram_golden_corpus.py -q
```

Показать:

1. количество positive cases и flows;
2. количество negative fixtures (ожидается 5);
3. manifest минимум с 12 карточками;
4. команды и фактический результат;
5. один focused commit только по `qa/telegram_golden/` и golden report/docs.

## Жёсткие запреты

Не менять:

- `backend/platform-api/app/**`;
- `n8n/**`;
- `postgres/**`;
- Google Sheets, production, Telegram, runtime data;
- существующие owner-locked карточки.
