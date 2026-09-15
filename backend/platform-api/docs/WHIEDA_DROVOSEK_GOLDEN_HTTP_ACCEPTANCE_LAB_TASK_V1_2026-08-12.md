# Task: Golden HTTP Acceptance Lab V1

Исполнитель: Дровосек  
Основание: `WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md` и принятый
`qa/telegram_golden/` V1.1.  
Режим: **local QA only**. Цель — увидеть фактические регрессии Core, а не
добавить очередной офлайн-файл.

## Проблема, которую закрываем

Golden Corpus теперь проверяет собственную структуру, но не вызывает advisor.
Значит он пока не может доказать, что `хай`, карточка, цена, follow-up и safety
boundary реально дают правильный ответ после изменения Core.

Нужен детерминированный runner:

```text
Golden JSONL -> local Platform Core HTTP -> assertions -> run report + diff
```

## Жёсткий scope

Разрешено:

- `qa/telegram_golden/**`;
- новые тесты в `backend/platform-api/tests/` только для lab/harness;
- локальные scripts/lab hook и docs/report.

Запрещено:

- `backend/platform-api/app/**`;
- n8n, Postgres schema/seed/runtime, Docker compose production;
- production, SSH, Google Sheets, Dify, Telegram API;
- изменение Golden expected, чтобы скрыть найденный Core failure;
- изменение owner-locked карточек.

## A. Target contract and hard safety gate

Создать:

```text
qa/telegram_golden/golden_target.example.json
qa/telegram_golden/golden_target.local.json  # gitignored
qa/telegram_golden/lab/target.py
```

Контракт включает `base_url`, `endpoint`, `tenant`, `country`, timeouts.

Rules:

1. По умолчанию target — `http://127.0.0.1:8080`.
2. Runner разрешает только localhost / `127.0.0.1` / `host.docker.internal`.
3. Любой публичный URL, IP production или HTTPS URL -> понятный FAIL до первого
   HTTP запроса.
4. `--dry-run` не выполняет HTTP.
5. `--check-target` проверяет health и один harmless request, но не меняет data.

Не читать target из `.env`, не сохранять credentials, headers или raw secrets.

## B. HTTP runner: cases and flows

Создать:

```text
qa/telegram_golden/run_telegram_golden.py --live --target <path>
qa/telegram_golden/lab/http_runner.py
qa/telegram_golden/lab/http_assertions.py
```

### Positive single-turn cases

Для всех 187 cases POST в advisor endpoint body:

```json
{
  "question": "...",
  "session": "run-scoped deterministic session",
  "country": "BY",
  "language": "ru",
  "surface": "telegram"
}
```

Проверять:

- HTTP 200 и JSON object;
- `ok=true`, непустой `answer_text`;
- expected `answer_mode`;
- `must_contain`, `must_not_contain`;
- expected `gap_kind`, когда он задан;
- media expectation (required / none / allow, video/document min);
- latency per priority: P0 <= 2 s, P1 <= 4 s, P2 <= 8 s;
- redacted preview in report, no full answer body by default.

`navigation_catalog` — lab pseudo-mode: не требовать Core answer mode, пометить
как `SKIP_SURFACE` с явной причиной и включить в отчёт отдельно. Нельзя называть
это PASS HTTP.

### Multi-turn flows

Каждый flow идёт в отдельную run-scoped session; turns строго последовательно.

Перед turn проверять `context_before.requires`; после turn проверять
`expected_context_transition.sets/clears` по `response.context`.

Если turn 1 не прошёл, оставшиеся turns flow — `NOT_RUN_DEPENDENCY`, не PASS.
Один failure не останавливает весь corpus.

### Negative fixtures

Прогнать все 5 только в `--include-negative`:

- требовать HTTP 200 + safe gap / clarification;
- запрещать product, price, treatment language и media delivery;
- результат помечать `NEGATIVE_PASS` / `NEGATIVE_FAIL`, считать отдельно;
- не смешивать с marketing quality score.

## C. Reports and baseline

Reports gitignored, один файл на run id:

```text
qa/telegram_golden/reports/GOLDEN_HTTP_REPORT_<run_id>.json
qa/telegram_golden/reports/GOLDEN_HTTP_REPORT_<run_id>.md
qa/telegram_golden/reports/latest.json  # pointer, optional
```

Report must show separately:

- executed/pass/fail/skip_surface/not_run_dependency/timeouts;
- by class and by priority;
- positive and negative fixture result;
- p50/p95/max latency;
- exact failed `case_id`, expected vs actual mode, failed assertions;
- target identity only (no token/DSN).

Baseline:

- `qa/telegram_golden/baselines/latest.json` stores normalized assertion outcome,
  no raw answer text;
- write only with `--accept-baseline` AND only when all executable P0/P1 cases
  pass and zero negative failures;
- ordinary `--live` must never overwrite it;
- `compare_golden_runs.py` reports regressions/improvements/new-unasserted.

## D. Local Core Lab hook

Extend local Core lab with:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden
```

The hook starts existing local Core environment, runs:

1. `--check-target`;
2. P0 smoke subset first;
3. full Golden live run;
4. `--include-negative`;
5. cleanup in `finally`.

If Docker is unavailable: honest `NOT_RUN`, non-zero only when `--e2e` was
explicitly requested. Do not invent E2E PASS from offline corpus.

## E. Tests

Add deterministic mocked tests for:

1. public target rejected before HTTP;
2. dry run has zero HTTP;
3. correct body/session isolation;
4. mode/must contain/must not/media assertions;
5. context set/clear/require;
6. failed first turn makes later turns NOT_RUN_DEPENDENCY;
7. timeout classification;
8. negative fixture never counts as marketing positive;
9. baseline write gate;
10. report does not contain raw body/credentials.

Run:

```powershell
python qa\telegram_golden\run_telegram_golden.py --offline
python -m pytest backend\platform-api\tests\test_telegram_golden*.py -q
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden
```

## Deliverable

One focused commit. Report:
`backend/platform-api/docs/TELEGRAM_GOLDEN_HTTP_ACCEPTANCE_LAB_LOCAL_REPORT.md`.

The report must state the actual local E2E result. If the full Golden reveals
Core defects, list them with case IDs; do not repair Core in this task.
