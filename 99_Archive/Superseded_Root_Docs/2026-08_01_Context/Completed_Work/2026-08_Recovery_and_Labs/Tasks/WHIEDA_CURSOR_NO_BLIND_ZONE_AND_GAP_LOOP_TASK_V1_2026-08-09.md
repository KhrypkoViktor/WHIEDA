# WHIEDA: No Blind Zone + Gap Loop — Cursor Task

## Goal

Turn an unrecognised, incomplete or out-of-catalogue user request into a useful
next step. The user must not receive a dead end such as "I do not know", "there
is nothing in the database", "I could not process it", or a promise that a
human will check it when there is no real hand-off.

At the same time, every unresolved request must be recorded as an operational
gap without turning unverified RAW material, testimonials or medical claims into
facts.

This is a **Core-only local/staging task**. Do not publish anything to production.

## Scope

Allowed:

- `backend/platform-api/app/advisor/`
- `backend/platform-api/app/telegram/`
- existing Core interaction/gap persistence and its staging SQL only;
- `qa/`, Core tests and local Docker lab;
- documentation and generated local reports.

Forbidden:

- Google Sheets, Dify, n8n workflows, Telegram webhook, production SQL;
- owner-locked product cards and product wording;
- importing RAW testimonials, medical materials, bundles or deep corpus;
- creating a second independent lead/gap queue;
- inventing a product, price, medical mechanism, evidence or a human follow-up.

## Read first

1. `00_READ_FIRST_WHIEDA_CANON.md`
2. `WHIEDA_FEATURE_STATUS_MATRIX_2026-08-09.md`
3. `WHIEDA_DATA_TO_FEATURE_MAP_2026-08-09.md`
4. `backend/platform-api/app/advisor/sql/engine.py`
5. `backend/platform-api/app/advisor/sql/ambiguity.py`
6. `backend/platform-api/app/telegram/processor.py`
7. existing interaction/gap schema and routes. Reuse them. Do not guess a table.

## Product behaviour

### 1. A single controlled unresolved-answer policy

Keep the existing machine-readable `knowledge_gap` signal for compatibility.
Add a stable machine-readable `gap_kind` and up to three `next_steps` only when
the request is unresolved. The Telegram user only sees `answer_text`.

Allowed `gap_kind` values:

```text
unknown_product
ambiguous_product
unknown_followup
unsupported_topic
missing_resource
medical_or_safety_boundary
```

Use the following behaviour, with short human Russian texts. Do not make the
answer longer than 3 short paragraphs unless safety requires it.

| Situation | User receives | Must not happen |
|---|---|---|
| Product name is incomplete or has several plausible matches | A direct choice of up to 3 known products or one exact clarification | Guess a SKU or say that the bot has no database |
| Product is not found | “Я пока не нашёл такой товар в текущем каталоге. Могу помочь с товаром, ценой, сравнением или собрать корзину. Как называется товар или что вы хотите решить?” | Fictional product, fake escalation |
| Bare follow-up without a valid product in session | “Подскажи, о каком товаре речь: название, фото, цена или сравнение?” | Treat it as an error |
| User asks for a resource that is absent for a known product | Honest statement that this material is not yet attached; offer card, price, photo/video that actually exist | “Передам команде”, unless a real ticket is created and visible to an operator |
| General business/service question outside catalogue | A short capabilities route: business FAQ, product, price, comparison, basket, event/lead if those routes exist | Empty fallback |
| Medical diagnosis, treatment, urgent/unsafe request | Soft boundary with a safe next action; retain safety wording | Diagnosis, treatment plan, claim of clinical proof |

The text must never contain these user-facing fragments except inside an explicit
safety boundary that is reviewed by an existing safety rule:

```text
не знаю
не смог обработать
нет в базе
передам на проверку
needs human review
This needs human review
```

### 2. Context rules

- Reuse existing session context only when it is tenant-safe and carries a real
  resolved product.
- A recognised product selected from a clarification becomes the context for the
  immediate next turn.
- Never carry a product across tenants or from an unresolved clarification.
- A request such as `видео`, `цена`, `сертификат`, `подробнее` without context
  must use `unknown_followup`, not a generic error.

### 3. Gap capture

Inspect the existing interaction/gap persistence first. Extend it only if a
field is missing. There must be one operational stream, not a new parallel
queue.

For every unresolved request, write exactly one idempotent event containing:

```text
tenant_id, channel, user/session reference (privacy-safe), question_normalized,
gap_kind, detected_product_or_null, trace_id, answer_mode, created_at
```

Requirements:

- do not store Telegram token, phone number, raw secrets or medical profile;
- repeated same gap inside a short session window increments/links an existing
  event rather than flooding the queue;
- a failed gap write must never suppress a user response;
- if persistence is unavailable, log a structured warning and return the guided
  response;
- all data changes remain local/staging. No production migration/apply.

### 4. Operator-facing summary, local only

Provide one read-only service/query or local report that groups unresolved
questions by `gap_kind`, normalized question and count. It must show no raw
phone/token and no medical conclusion.

Do not build a UI. JSON/CSV/Markdown report is enough.

## Test work

### A. Unit and contract tests

Add tests for all six `gap_kind` values. Include:

- unknown product;
- typo that resolves to a known product and therefore creates no gap;
- `паста` / colour / activator ambiguity;
- `цена`, `видео`, `сертификат`, `подробнее` without context;
- known product + missing video/certificate;
- safety request;
- repeated unresolved question is deduplicated/linked;
- persistence error still returns text;
- cross-tenant session context is ignored;
- every unresolved answer contains a useful next step;
- prohibited dead-end fragments never reach a user response.

### B. Regression corpus

Create `qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl` with at least
40 cases, P0/P1 marked. Add a runner that works offline/mocked and supports
the existing local Core E2E lab when it is available.

Required case groups:

```text
unknown products / bad spelling / ambiguous names / empty follow-ups /
missing resources / business unknowns / safety boundaries / context isolation
```

Do not weaken an assertion merely to make Core green. Every corpus change needs
`rationale` when it changes an existing expectation.

### C. Local staging proof

Use the existing local Docker Core/Postgres lab. Add a separate proof command
that:

1. creates only a guarded temporary staging database;
2. sends representative unresolved requests;
3. checks user answers, stored gap events and deduplication;
4. validates tenant isolation;
5. cleans up the temporary database and stops only containers it started.

If Docker is unavailable, the command must state `NOT_RUN`, not pretend PASS.

## Commands and reports

Provide:

```powershell
python -m pytest backend/platform-api/tests -q
python qa/no_blind_zone/run_no_blind_zone.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --no-blind-zone
```

Write:

- `NO_BLIND_ZONE_LOCAL_REPORT.md` — factual result, case counts and limitations;
- a timestamped local E2E report under the existing reports convention;
- `NO_BLIND_ZONE_OPERATOR_SAMPLE.md` — 10 grouped unresolved questions using
  fixtures only, for owner review.

## Acceptance

1. No unresolved normal request produces a dead-end text or silent failure.
2. Existing product/price/card/media paths do not regress.
3. All six `gap_kind` values have tests.
4. One unresolved request produces at most one active operational gap event.
5. Storage failure cannot block Telegram/Core delivery.
6. Tests are honest about Docker/E2E being run or not run.
7. Production, Sheets, Dify, n8n and owner-locked cards are untouched.
8. Commit only task files and code/tests directly needed for this block.

## Delivery format

Return:

1. commits;
2. exact test commands and actual outputs;
3. what is local-only versus staging-proven;
4. any real blocker;
5. list of files changed.

Do not claim that RAW reviews, medical material, testimonials, bundles or RAG
were published. They are deliberately outside this task.
