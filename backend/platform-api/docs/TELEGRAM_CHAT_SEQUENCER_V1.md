# Telegram chat update sequencer (v1)

## Scope

The sequencer in `app/telegram/sequencer.py` runs inside **one Core API process**.
It guarantees:

- updates for the same Telegram `chat_id` are handled **serially**, in webhook arrival order;
- the same Telegram `update_id` is processed **at most once** per process;
- different chats may still run **concurrently**.

It does **not** coordinate across multiple Core replicas or survive process restarts.

## Current wiring

`/v1/telegram/{binding_id}/webhook` still ACKs immediately via FastAPI
`BackgroundTasks`. Background work is wrapped with
`ChatUpdateSequencer.run_ordered(chat_key, update_id, handler)`.

## Memory bounds

- Per-chat locks are dropped after idle TTL when not held.
- Processed `update_id` entries expire after 24h and are trimmed to a fixed cap.

## Upgrade path (not implemented here)

For production-grade ordering across replicas:

1. **Ingress:** webhook ACK stays fast; persist `(tenant, chat_id, update_id, payload, received_at)` into a durable queue/outbox table with a unique index on `(bot_binding_id, update_id)`.
2. **Worker:** one consumer per `chat_id` partition (or advisory lock on `chat_id`) pulls pending rows in `update_id` order.
3. **Delivery:** advisor result + Telegram send become idempotent steps keyed by `(update_id, step)`.
4. **Observability:** expose lag per chat, dead-letter queue for poison updates, and replay tooling.
5. **Migration:** run the durable worker in shadow mode beside the in-memory sequencer, compare ordering metrics, then cut over route handlers.

Until that exists, scale Core Telegram traffic vertically or pin webhook traffic to a single active Core instance.

## Button presses (04.11.2026)

- The fingerprint of a button is `callback_data` **plus the `message_id` of the message the button sits under**
  (`build_message_fingerprint(callback_data=..., message_id=...)`). A repeated tap on the same button of the
  same message within 12 s is a duplicate (protection against a double order stays); the same `callback_data`
  under a new message (e.g. «Пропустить» on the next step of the site request) goes through.
  Text messages keep the old fingerprint.
- For buttons `run_ordered(..., drop_inflight_duplicate=True)`: a repeat of a fingerprint that is still being
  handled (or waits for the chat queue) is dropped at once, without waiting for the chat lock; the router answers
  such a duplicate with an empty `answerCallbackQuery` so the spinner on the button stops.
- `on_accepted` fires before the chat queue: the router sends `sendChatAction typing` there (private chat of the
  WWC bot, off switch `PLATFORM_TELEGRAM_CALLBACK_TYPING=false`).
- `SequencerResult.wait_ms` / `run_ms` go to the log line `telegram_update_processed kind=... wait_ms=... run_ms=...`
  (queue wait vs handler time).
