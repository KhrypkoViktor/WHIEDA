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
