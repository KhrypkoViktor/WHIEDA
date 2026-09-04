# WHIEDA Drovosek: Telegram Service Intent and Duplicate Lab

## Goal

Build a local-only regression lab for Telegram service-intents and duplicate
update handling around the current Core webhook path.

This is not a production deploy task. It is meant to harden the bot against
weak first-turn phrases, typo variants, and duplicate Telegram updates.

## Hard boundaries

- Work only in:
  - `backend/platform-api/tests/`
  - `qa/telegram_experience/`
  - `backend/platform-api/docs/` if a short note is needed
- Do not modify production, n8n, Sheets, Dify, Telegram live settings, cron,
  runtime Postgres, or website files.
- No deploy, no live webhook switching, no DB writes.
- No secrets, tokens, DSNs, raw Telegram IDs, or chat logs in reports.

## Context already verified by architect

1. Production route flags are:
   - `CORE_ROUTE_TELEGRAM=core`
   - `CORE_ROUTE_ADVISOR=shadow`
   - `CORE_ROUTE_DEEP=off`

2. In `app/telegram/routes.py`, when `CORE_ROUTE_TELEGRAM=core`, the request
   goes through `process_core_telegram_update(...)` and returns early.
   That means the `shadow` advisor path is **not** the active explanation for
   Telegram duplicates on this route.

3. Recent architect fixes already shipped:
   - `че ты можеь?`, `можешь?`, `какие есть товары?`, `любой товар` →
     capabilities / help path
   - `активатор` → clarification
   - `обычный` after activator clarification → base product card

## Block A — service-intent fuzz matrix

Expand Telegram-first-turn regression around these classes:

1. Greeting variants:
   - `привет`
   - `здарова`
   - `здрасьте`
   - `добрый день`

2. Capability/help variants:
   - `что можешь`
   - `че ты можеь`
   - `а что моешь`
   - `можешь?`
   - `помощь`
   - `что умеешь`

3. Catalog/opening prompts:
   - `какие есть товары`
   - `какой товар есть`
   - `любой товар`
   - `покажи любой товар`

4. Activator clarification loop:
   - `активатор`
   - `обычный`
   - `pro`
   - `обычный цена`
   - `обычный фото`

5. Out-of-scope but casual slang:
   - `пивка хочешь`
   - `ты живой`
   - `как дела`

Requirements:

- Add cases only when the expected behavior is clear
- Do not weaken existing expectations
- Every new case must assert:
  - `expected_mode`
  - at least one `must_contain`
  - at least one meaningful `must_not_contain` when useful

## Block B — duplicate update lab

Build local tests around `app/telegram/sequencer.py` and route behavior:

1. same `chat_id` + same `update_id` twice → second is ignored
2. same `chat_id` + different `update_id` in quick succession → preserved order
3. two different chats in parallel → no global serialization
4. duplicate update must not produce duplicate delivery callback
5. duplicate update after handler exception must still remain dedup-safe

If a helper test abstraction is needed, keep it inside tests only.

## Block C — route-level truth table

Add a small route-level test matrix for `_process_telegram_update_body(...)`
or the nearest safe seam that proves:

- `CORE_ROUTE_TELEGRAM=core` never forwards to legacy consultant
- `CORE_ROUTE_TELEGRAM=legacy` does forward
- `CORE_ROUTE_TELEGRAM=shadow` forwards legacy and does not deliver Core answer
- ignored group messages do not deliver anything

This must stay mock-only.

## Deliverables

1. Extended tests in `backend/platform-api/tests/`
2. Extended corpus or builder in `qa/telegram_experience/`
3. Short local report:
   `WHIEDA_TELEGRAM_SERVICE_INTENT_AND_DUPLICATE_LAB_LOCAL_REPORT.md`
4. Focused commit(s)

## Acceptance

Run:

```powershell
python -m pytest backend/platform-api/tests -q
python qa/telegram_experience/run_telegram_experience.py --offline
```

Report must clearly separate:

- newly covered service-intent phrases
- duplicate-update guarantees
- what is still **not** proven in live Telegram

Do not claim live duplicate bug fixed unless there was a real live reproduction.
