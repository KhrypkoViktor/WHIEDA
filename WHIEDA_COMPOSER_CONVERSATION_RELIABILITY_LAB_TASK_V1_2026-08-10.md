# WHIEDA: Core Conversation Reliability Lab - Composer Task

## Product goal

Make the local Core advisor reliable in a real multi-turn conversation.

The advisor must not lose the selected product after clarification, card, photo, video, price, comparison or basket. It must never emit a blank response, raw exception, English legacy phrase, or a dead-end `no blind zone` answer.

This task does not change product facts, marketing cards, prices, Google Sheets, RAG, n8n, Dify, Telegram webhook or production.

## Scope

Only:

- `backend/platform-api/app/advisor/`;
- `backend/platform-api/app/telegram/` only for response-delivery contract tests;
- `backend/platform-api/tests/`;
- `qa/conversation_reliability/`;
- local Core seed and local Docker lab scripts;
- docs/reports for this task.

Do not touch:

- `03_Website/`;
- production, SSH, deploy scripts, n8n, Dify, Google Sheets;
- owner-locked card texts, real product aliases/prices/materials;
- RAG corpus;
- user access/roles/leads/referrals;
- existing no-blind-zone source data or its approved wording.

## Existing contracts to preserve

- Structured fast answers remain SQL/Core-only.
- `answer_text` is never empty for user-visible answers.
- Stable response fields: `answer_text`, `answer_mode`, `product`, `media`, `clarifications`, `context`, `gap_kind`, `next_steps` when applicable.
- Photo delivery is photo first without caption, then full text. If photo fails, text still sends.
- Unknown or safety requests use the existing No Blind Zone layer; do not create a parallel fallback.
- Session key comes from existing request contract. Do not log raw Telegram IDs.
- No automatic content publishing.

## Deliverable A - deterministic conversation state contract

Create a small documented contract for session context and transitions. Reuse existing session storage; do not make a second session table.

Minimum context fields:

- `last_product_sku`;
- `last_product_name`;
- `last_intent`;
- `clarification_pending` boolean or equivalent;
- `clarification_candidates` with no more than three safe candidates;
- `last_compare_left_sku` / `last_compare_right_sku` when comparison is active;
- `basket_draft` only if existing basket design supports it; otherwise do not invent persistent cart.

Rules:

1. Full product card saves the product context.
2. Price/photo/video/PDF after a card resolve against that product.
3. A clarification saves candidates but does not pretend a product was selected.
4. A valid user selection resolves exactly one candidate and then saves it as last product.
5. A non-matching selection keeps clarification state and returns one guided clarification.
6. A new explicit product name replaces old product context.
7. Context may never cross tenant or session.
8. Context expiry/failure must lead to an honest guided question, never an invented product.

Write the contract:

`backend/platform-api/docs/ADVISOR_CONVERSATION_STATE_CONTRACT_V1.md`.

## Deliverable B - local multi-turn corpus

Create `qa/conversation_reliability/whieda_conversation_flows_v1.jsonl` with at least **80 turns across at least 24 named flows**. A flow is multiple requests with one session, not unrelated single requests.

Required flows:

1. card → price;
2. card → photo;
3. card → video;
4. card → certificate/PDF;
5. card → “подробнее”;
6. typo `ативатор` → card → price;
7. exact ambiguous `активатор` → selection → price;
8. `паста` → selection → card;
9. `пояс` → selection → photo;
10. `красный` → clarification → red elixir price;
11. green/blue elixir explicit flows;
12. comparison → price of left product;
13. comparison → photo of right product;
14. product A → explicit product B → price must use B;
15. cart with three known products;
16. cart with one unknown item → guided clarification, no invented total;
17. mixed promotion + price;
18. no-photo product → guided resource answer;
19. no-certificate product → guided resource answer;
20. bare `цена` with no context → `unknown_followup`;
21. bare `видео` with no context → `unknown_followup`;
22. medical boundary after known product;
23. external topic after known product must not reuse the product;
24. separate session must not inherit context from another session.

For every user-visible turn assert:

- expected answer mode;
- required/forbidden text fragments;
- expected product only when known;
- expected context change or no context change;
- media contract: `required`, `none`, or `allow`;
- no prohibited legacy fragments:
  - `не знаю`;
  - `нет в базе`;
  - `передам на проверку`;
  - `this needs human review`;
  - `needs human review`;
  - `не смог обработать`.

Do not weaken expected answers merely to make the current Core pass. Any changed expectation needs `rationale` explaining why it is product-correct.

## Deliverable C - runner and failure report

Create a local HTTP runner:

```powershell
python qa/conversation_reliability/run_conversation_reliability.py --offline
python qa/conversation_reliability/run_conversation_reliability.py --live
```

Features:

- executes each named flow in order with its own session;
- continues after a failed flow and reports every failed turn;
- P0/P1 priorities and per-turn timeout;
- local target guard: only `127.0.0.1:8080` or `localhost:8080`;
- raw HTTP bodies are stored only under ignored local report folder;
- JSON and Markdown reports include flow id, turn number, result, latency, mode and safe error reason;
- reports never include credentials, raw session ID, phone, Telegram ID, or complete headers;
- offline mode validates corpus and unit tests without HTTP.

Add local lab flag:

```powershell
python backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability
```

It must run after the ordinary preflight and before Core cleanup. Failure of one P0 flow fails the lab. Core cleanup remains in `finally`.

## Deliverable D - code fixes only where corpus exposes a real defect

Fix Core only when an observed failure violates the contract above.

Allowed fixes:

- context state handling;
- intent ordering;
- follow-up resolution;
- response envelope consistency;
- error handling to avoid a 500 / blank answer;
- local seed additions strictly for synthetic test-only products.

Not allowed:

- replacing approved WHIEDA cards;
- making up prices, medical claims, materials or aliases;
- adding LLM/RAG fallback;
- bypassing no-blind-zone;
- editing real Sheets or runtime SQL;
- changing Telegram production delivery.

## Deliverable E - tests

Required automated tests:

1. Flow context carries forward inside one session.
2. Context never leaks to another session or tenant.
3. Clarification selection works; invalid selection does not select a random product.
4. Explicit product replaces previous context.
5. After known card, price/photo/video/PDF work without restating product.
6. Empty answer text is rejected for all user-visible answer modes.
7. Response includes no forbidden legacy fragment.
8. Unknown follow-up and missing-resource use existing No Blind Zone kinds.
9. A local 500 / malformed dependency error produces a safe typed error or guided answer, not a traceback body.
10. Telegram photo-first delivery contract remains intact.
11. Corpus lint rejects a flow that lacks a starting context/session, assertions or a terminal expected state.
12. Runner’s target guard rejects public URLs.

## Acceptance

Run:

```powershell
python -m pytest backend/platform-api/tests -q
python qa/conversation_reliability/run_conversation_reliability.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability
```

Create:

- `CORE_CONVERSATION_RELIABILITY_LAB_REPORT.md`;
- `backend/platform-api/docs/ADVISOR_CONVERSATION_STATE_CONTRACT_V1.md`;
- `qa/conversation_reliability/`.

Report truthfully separates:

- static/unit results;
- local Docker HTTP result;
- not run in production;
- any flows that remain blocked.

Commit only files belonging to this task. Do not stage unrelated dirty files.

Final response: commit hash, total flows/turns, pytest count, E2E result, defects fixed, items not verified.
