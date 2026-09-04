# WHIEDA: Restore Telegram Advisor Experience

## Purpose

Restore the fast, clear, presentable Telegram advisor experience without
changing Google Sheets, owner-approved product text, runtime data, n8n or Dify.

This task repairs the Core presentation and conversation layer. It must use the
existing Sheet snapshot as representative data, not a tiny synthetic seed.

## Evidence to reproduce

The owner visual test is stored in the task attachment from 2026-08-11. It
shows these confirmed defects:

1. A product card arrives as a long unlabelled sheet of text.
2. Several fast messages are processed concurrently, so a reply can arrive
   after the next question and look like an answer to the wrong message.
3. `че ты можешь?`, `калькулятор`, `виды входа`, `расскажи о компании`,
   `как заработать?`, `болят колени` and `болит спина` fall into a generic
   catalogue miss instead of a useful next step.
4. The cart calculates a list but `убери активатор` does not mutate that list.

The expected visual language is compact, structured Telegram text: a photo
first where available, short labelled sections, restrained emoji, bullets and
real line breaks. Do not send literal Markdown stars.

## Hard boundaries

- Do not deploy or change live Core, n8n, Sheets, Telegram webhook, Dify or
  runtime Postgres.
- Do not rewrite `Product_Cards`, owner-approved wording, medical statements,
  prices, resources or aliases in the master Sheet.
- Do not invent medical claims, indications, documents or media.
- Keep the existing group policy: private chat works normally; a group message
  requires a mention of the bot or a reply to it.
- Work in `backend/platform-api`, `qa/` and local test/fixture tooling only.
- Separate commits by block. No generated reports, Docker volumes, secrets or
  `.env` files in commits.

## Block A: product-card presentation renderer

### A1. Source truth and local realistic fixture

Create a local fixture compiler that reads the already captured immutable
snapshot:

`n8n/live-exports/structured-master/20260810T083328Z/`

It may generate local test fixtures only. It must not connect to Sheets or a
runtime database. Use real rows for at least:

- Activator Cells;
- Activator Cells PRO;
- Ba-Gua;
- BEM;
- Wentun;
- glasses;
- insoles;
- one food/cosmetic product.

### A2. Renderer rules

Replace raw concatenation in `format_product_card()` with a dedicated Telegram
card renderer. Preserve every nonempty structured field and present it in this
order:

```text
<product name>

🔥 If simple: <what_it_is>

👥 For whom: <who_asks_about_it>

✅ When it is usually considered:
• <one use case per line>

🧭 How it is used:
<how_to_use_short>

🧠 Why it is interesting:
<what_to_expect_soft>

⚠️ Limitations:
<contraindications_short>

I can give price/PV, photo, video, certificate or compare it with another product.
```

Rules:

- Include only sections that actually have data.
- Split list-like source values on semicolon/newline into bullets, but do not
  mechanically break normal sentences.
- No duplicated product name or duplicated “if simple”.
- HTML is allowed only if delivery escapes source text and explicitly requests
  Telegram HTML parse mode. If this cannot be made safe, use plain Unicode
  headings and bullets. Literal `**` must never be visible to the user.
- Photo remains a separate `sendPhoto` without caption before the text.
- Add a compact mode for explicit short requests, but do not silently shorten
  the normal approved card.

### A3. Tests

Add snapshot-based tests for all named products and assert:

- headings and bullets are present where the relevant source fields exist;
- every populated core field is represented;
- no raw literal Markdown stars;
- no accidental HTML injection from master text;
- photo-first delivery stays a separate photo and text message.

## Block B: ordered Telegram conversation and idempotency

Current `/webhook` ACK remains fast, but messages in one chat must be handled
in Telegram update order. Two rapid messages must never swap their replies.

1. Implement a bounded per-chat sequencing mechanism in Core.
2. Deduplicate the same `update_id`; duplicate delivery must not send a second
   response or write a duplicate advisor interaction.
3. Different chats may still process concurrently.
4. Clean idle local locks/entries so memory cannot grow forever.
5. The design must be explicit about its current one-Core-container scope. Do
   not pretend an in-memory lock is a cross-replica distributed queue.
6. Write a small documented upgrade path for a durable outbox/worker, but do
   not build it in this task.

Required tests:

- send `что можешь` then `пивка хочешь` rapidly in one chat; the first response
  must be capabilities and the second must be an honest out-of-scope/gap reply;
- two different chats are not serialized behind each other;
- same Telegram `update_id` twice results in one delivery;
- failure in one message releases the chat for the next one.

## Block C: useful first-turn and goal routing

Do not turn every non-product phrase into “unknown product”. Add explicit,
safe Core routes and tests for:

| User input | Required outcome |
|---|---|
| `приве` | friendly greeting / typo tolerance |
| `че ты можешь?`, `чо умеешь?` | capabilities menu |
| `пивка хочешь?` | clear out-of-scope answer, never capabilities |
| `калькулятор` | one-line calculator instruction with `Посчитай: ...` format |
| `какие виды входа?` | approved start-options/basket route, or a concrete clarification if data is unavailable |
| `расскажи о компании` | company/business introduction, no product-miss fallback |
| `как заработать?` | compliant business route: explain that income is not promised and offer the approved start/marketing-plan material |
| `болят колени`, `болит спина`, `хочу совет` | safe boundary: acknowledge the topic, no diagnosis, ask whether the person wants to choose a home product or needs a limitation/usage question; never generic catalogue miss |
| `убери активатор` after a cart | mutate the active cart and return a recalculated list, or clearly state that no active cart exists |

Requirements:

- First use existing structured FAQ, basket templates and approved texts.
- Where no approved answer exists, return a specific next question with a typed
  gap. Do not say merely “not in the catalogue”.
- Preserve No Blind Zone `gap_kind` in the response envelope.
- Do not add health recommendations beyond the existing approved safety layer.

## Block D: realistic acceptance proof

Create `qa/telegram_experience/` with a compact but strict corpus built from
the owner test plus normal product follow-ups. It must run against a local Core
seeded from the immutable master snapshot fixture.

Required cases:

- greeting/capability/typo/out-of-scope;
- product card, photo, price, video and certificate;
- rapid two-message ordering;
- card formatting for all A1 named products;
- cart calculate, remove and recalculate;
- business/start/company prompts;
- safe discomfort/limitation prompts;
- group ignore, group mention and reply-to-bot.

The report must separate:

- presentation PASS/FAIL;
- ordering/idempotency PASS/FAIL;
- structured route PASS/FAIL;
- intentional safe gaps;
- remaining data gaps that require owner/medical review.

## Required commands

```powershell
python -m pytest backend/platform-api/tests -q
python qa/telegram_experience/run_telegram_experience.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --telegram-experience
```

Do not call this task complete until the live local Docker run is green. Report
the exact number of flows/turns, actual remaining data gaps and all deliberate
scope limitations.
