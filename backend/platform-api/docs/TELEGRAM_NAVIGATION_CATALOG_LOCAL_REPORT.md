# Telegram Navigation and Catalog Browse — Local Report

Date: 2026-08-12  
Branch: `feat/platform-scale-core`  
Task: `WHIEDA_DROVOSEK_TELEGRAM_NAVIGATION_AND_CATALOG_TASK_V1_2026-08-12.md`

## Implemented locally

| Block | Status | Notes |
|-------|--------|-------|
| A — Delivery primitives | DONE | `reply_markup` on `send_telegram_text`, `answer_callback_query`, `navigation.py` keyboards |
| B — Runtime catalog browse | DONE | Repository list/count/resolve + `catalog_browse.py` handlers |
| C — Callback ingress | DONE | `callback_query` parser, processor/routes wiring, sequencer + dedup |
| D — Tests + QA lab | DONE | `test_telegram_navigation.py`, `qa/telegram_navigation/` (24 offline flows) |

## Local tests

```text
cd backend/platform-api && python -m pytest tests -q
→ 757 passed, 1 skipped (after navigation block)

cd .. && python qa/telegram_navigation/run_telegram_navigation.py --offline
→ TELEGRAM_NAVIGATION: PASS (offline) — 24/24 flows
```

## Docker E2E

```text
python backend/platform-api/scripts/run_local_core_lab.py --e2e --telegram-navigation
→ NOT RUN in this session (hook wired; requires Docker Desktop + local Core stack)
```

## Explicitly not done

- Production deploy / n8n publish / webhook route flip
- Google Sheets or new catalog tables
- Full visual Telegram acceptance on real bot token
- Live HTTP navigation corpus (offline contract lab is the gate)

## Behaviour notes

- Menu reply keyboard: 6 labels from `navigation.py` (single source of truth).
- Catalog: paginated from `advisor_structured_products` per tenant, sorted `canonical_name`, `sku`; page size clamp 1–8.
- Callback data allowlisted (`nav:`, `cat:`, `act:`); invalid/stale → safe menu text, no SQL from raw callback.
- Free text `какие есть товары` / `📦 Товары` → catalog browse (not capabilities fallback).
- Photo-first preserved: card actions use existing `deliver_structured_advisor_response` (photo without caption, then text).

## Suggested commits (3 logical slices)

1. `feat: add Telegram navigation delivery primitives`
2. `feat: add runtime catalog browse and callback routing`
3. `test: add Telegram navigation acceptance lab`
