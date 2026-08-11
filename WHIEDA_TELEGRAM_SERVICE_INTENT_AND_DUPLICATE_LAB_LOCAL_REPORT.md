# Telegram Service Intent and Duplicate Lab — Local Report

Date: 2026-08-11  
Task: `WHIEDA_DROVOSEK_TELEGRAM_SERVICE_INTENT_AND_DUPLICATE_LAB_V1_2026-08-11.md`  
Scope: local tests and offline corpus only. No deploy, no live webhook, no DB writes.

## Commands

```powershell
python -m pytest backend/platform-api/tests -q
python qa/telegram_experience/run_telegram_experience.py --offline
```

## Results

```
731 passed, 1 skipped
TELEGRAM_EXPERIENCE: PASS (offline) — 31 flows / 36 turns
```

## Block A — service-intent fuzz matrix

New file: `backend/platform-api/tests/test_telegram_service_intent_fuzz.py`

| Class | Phrases covered in routing tests | Notes |
|-------|----------------------------------|-------|
| Greeting | `привет`, `добрый день` | `structured_business`, fallback greeting |
| Capabilities/help | `что можешь`, `че ты можеь`, `а что моешь`, `можешь?`, `помощь`, `что умеешь` | not confused with OOS |
| Catalog/opening | `какие есть товары`, `любой товар`, `покажи любой товар` | capabilities path |
| Activator loop | `pro`, `обычный цена`, `обычный фото` (with pending session) | base/pro choice + follow-ups |
| Casual OOS / smalltalk | `пивка хочешь`, `как дела` | `unsupported_topic` vs capabilities menu |

**Detection-only gaps (expected `None`, no routing test yet):**

- `здарова`, `здрасьте` — not matched by current `GREETING_RE`
- `какой товар есть` — not in `CAPABILITY_RE`
- `ты живой` — no dedicated intent (may fall through to product/gap path in live)

Corpus additions in `qa/telegram_experience/build_flows.py` (+7 flows):

- `TG-SVC-GREET-HI`, `TG-SVC-GREET-DAY`
- `TG-SVC-CAP-SLANG`, `TG-SVC-CAP-MOZH`
- `TG-SVC-CATALOG-SHOW`
- `TG-SVC-OOS-SMALLTALK` (как дела → пивка хочешь)
- `TG-SVC-ACTIVATOR-PRO` (активатор → pro)

## Block B — duplicate update lab

Extended `backend/platform-api/tests/test_telegram_chat_sequencer.py`:

| Guarantee | Test |
|-----------|------|
| same `chat_id` + same `update_id` → second ignored | existing + `test_duplicate_update_does_not_run_handler_twice_after_success` |
| different `update_id` in one chat → FIFO order | existing rapid capabilities/OOS test |
| different chats → parallel | existing |
| duplicate webhook → single Core handler invocation | `test_routes_duplicate_update_skips_delivery_callback` |
| failed handler not remembered → Telegram retry may re-run | `test_failed_update_is_not_remembered_so_retry_allowed` |

**Limitation:** in-memory sequencer is per Core container; cross-replica dedup is **not** proven.

## Block C — route truth table

New file: `backend/platform-api/tests/test_telegram_route_truth_table.py` (mock-only)

| `CORE_ROUTE_TELEGRAM` | Legacy forward | Core processor | Core delivery |
|-----------------------|----------------|----------------|---------------|
| `core` | never | yes | via processor |
| `legacy` | yes | never | n/a |
| `shadow` | yes | via structured query | never |
| group w/o mention | never | never | never |

Aligns with architect note: on production route `core`, shadow advisor path is not the duplicate explanation.

## Not proven in live Telegram

- Real duplicate webhook delivery from Telegram servers (only local mock/route tests)
- Cross-container / multi-replica deduplication
- Phrases with detection gaps (`здарова`, `здрасьte`, `ты живой`, `какой товар есть`)
- Live latency and media delivery for new corpus flows (offline HTTP lab validates contract only when E2E run separately)

No claim that a live duplicate bug was reproduced or fixed in this task.
