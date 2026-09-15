# Task: Golden Surface Contract and Failure Triage V1

Исполнитель: Дровосек  
Дата: 2026-08-13  
Основание: local master-parity Golden run `20260813T131727Z-5391af0d`.

## Цель

Сделать Golden acceptance честным инструментом: он должен отличать настоящий
дефект Core от неверного ожидания, fixture drift или Telegram-only navigation.
Это задача контроля качества. Она **не даёт права менять поведение advisor**.

## Контекст

Реальный local E2E уже доказал:

- local Core + master fixture стартуют;
- P0 acceptance preflight: `8/8 PASS`;
- все 5 blocked/raw safety fixtures проходят безопасной boundary-веткой;
- после реальных Core fixes осталось 14 positive mismatches;
- среди них есть Telegram navigation labels, русский `ё/е`, исторические SKU и
  спорные product-policy ожидания.

### Текущий входной список для triage

Не считать этот список решениями. Это ровно 14 несовпадений из последнего
полного local E2E, которые должны получить classification и доказательство:

```text
GOLD-SMOKE-P0-001
GOLD-NBZ-NBZ-P0-006
GOLD-NBZ-NBZ-P0-014
GOLD-NBZ-NBZ-P1-022
GOLD-NBZ-NBZ-P1-023
GOLD-EXP-TG-CART-CALC-REMOVE-T2
GOLD-EXP-TG-PRES-ACTIVATOR-CHOICE-T2
GOLD-EXP-TG-SVC-CATALOG-SHOW-T1
GOLD-EXP-TG-SVC-ACTIVATOR-PRO-T2
GOLD-BUS-EXTRA-01
GOLD-BUS-EXTRA-03
GOLD-BUS-EXTRA-04
GOLD-SMOKE-FLOW-ctx-pro-T3
GOLD-CONV-CONV-F34-events-T1
```

Known evidence, not a conclusion: `GOLD-EXP-TG-SVC-CATALOG-SHOW-T1` and
`GOLD-BUS-EXTRA-01` are likely Telegram-navigation surface checks; the HTTP
advisor must not be changed merely to satisfy them. Exact `активатор`, the
cart command without a cart, historical SKU, product-card photo and event
wording remain owner-policy candidates unless a source proves otherwise.

Не пытаться сделать `PASS` ослаблением expected или массовым `SKIP`.

## Жёсткие границы

Разрешено:

- `qa/telegram_golden/**`;
- `backend/platform-api/tests/test_telegram_golden*.py`;
- `backend/platform-api/scripts/local_core_lab/**` только для отчётного hook;
- `backend/platform-api/docs/**` для контракта и отчёта.

Запрещено:

- `backend/platform-api/app/**`;
- `n8n/**`, `postgres/**`, Sheets, production, SSH, Telegram token;
- runtime SQL, seed, master snapshot, карточки товаров;
- менять `expected` ради зелёного статуса без classification record и source;
- удалять negative fixtures или превращать их в positive.

## A. Surface contract

Добавить в каждый Golden case/flow явное поле:

```json
"execution_surface": "advisor_http | telegram_text | telegram_callback | telegram_delivery"
```

Правила:

1. `/v1/advisor/query` запускает только `advisor_http`.
2. Текстовые reply-keyboard labels (`📦 Товары`, `📈 Бизнес`, `🏢 О компании`,
   `🧮 Калькулятор`, `🧭 Подбор`, `📅 Встречи`) — `telegram_text`.
3. `nav:*`, `cat:*`, `act:*` — `telegram_callback`.
4. Проверки photo-first, `reply_markup`, callback ACK — `telegram_delivery`.
5. При запуске HTTP case другой поверхности должен получить именно
   `SKIP_SURFACE` с причиной, а не искусственный FAIL.

Добавить отдельные mock/ASGI tests для text/callback processors; не заменять их
обычным advisor HTTP запросом.

## B. Failure taxonomy

Создать строго ограниченный enum и требовать classification для каждого
non-PASS case:

```text
core_bug
fixture_data_gap
surface_mismatch
expectation_mismatch
policy_decision_required
dependency_failure
```

Запрещено автоматически присваивать `expectation_mismatch`. Для него нужны:

- actual mode/text preview;
- ссылка на case/flow source;
- краткое объяснение, почему текущее поведение приемлемо;
- `owner_decision: pending`.

`core_bug` должен иметь minimal reproduction command and current local report
id. `surface_mismatch` — ссылку на Telegram handler/test.

## C. Policy decision registry

Создать versioned JSON:

```text
qa/telegram_golden/telegram_golden_policy_decisions_v1.json
```

Только для спорных, уже обнаруженных классов. Не ставить решения от себя;
статус по умолчанию `pending_owner`.

Занести минимум:

1. exact `активатор` -> clarification base/PRO или base card;
2. `партнер` vs `партнёр` — семантически эквивалентны в assertions;
3. output SKU historical fixture vs current runtime SKU;
4. photo allowed/required/none для card и product detail;
5. plain `убери активатор` без сохранённой корзины;
6. `сколько PV в активаторе` -> product price/PV или business FAQ;
7. must-contain `эфир` для events, если event data не гарантирует это слово.

Никакое `pending_owner` не меняет Core и не становится PASS. В отчёте идёт
отдельно, а не скрывается.

## D. Triage report and deterministic baseline

Добавить:

```text
qa/telegram_golden/reports/GOLDEN_TRIAGE_<run_id>.md
qa/telegram_golden/reports/GOLDEN_TRIAGE_<run_id>.json
```

Отчёт обязан показать:

- total/pass/fail/skip/not-run по execution_surface;
- total по classification;
- table P0 first, затем P1/P2;
- отдельно real `core_bug` backlog with repro;
- отдельно pending owner decisions;
- negative safety fixtures (all must remain a separate gate);
- fixture manifest identity and report run id;
- no full raw medical dialogue, tokens, DSN, session ids or secrets.

Baseline принимается только при явном `--accept-triage-baseline`, и только если:

- no P0 `core_bug`;
- all safety negative fixtures PASS;
- every skipped case has a permitted `surface_mismatch` reason;
- no pending policy decision is silently marked PASS.

## E. Tests

Добавить тесты минимум на:

1. schema validation: unknown execution surface/classification rejects corpus;
2. advisor HTTP never runs `telegram_callback` as normal case;
3. every current non-PASS receives one valid classification;
4. surface mismatch remains visible in report;
5. `ё/е` assertion equivalence;
6. baseline refuses P0 core bug;
7. baseline refuses failed negative safety fixture;
8. report redacts sensitive/raw fields;
9. deterministic same-input triage JSON;
10. owner-pending policy never becomes PASS automatically.

## Commands

Run from Git Bash:

```bash
cd /d/Projects/WHIEDA
python qa/telegram_golden/run_telegram_golden.py --offline
python -m pytest backend/platform-api/tests/test_telegram_golden*.py -q
python backend/platform-api/scripts/run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
```

## Deliver

1. one focused commit;
2. `TELEGRAM_GOLDEN_SURFACE_TRIAGE_LOCAL_REPORT.md`;
3. latest triage JSON/MD report paths;
4. exact list of `core_bug` only, each with repro;
5. list of `pending_owner` decisions; no Core fixes.
