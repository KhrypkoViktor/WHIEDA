# WHIEDA live Telegram route audit - 2026-08-10

## Verdict

The public Telegram bot is routed to Platform Core, not to the legacy n8n
Advisor workflow.

## Verified live path

```text
Telegram -> https://sysarchn8n.duckdns.org/whieda-platform/v1/telegram/whieda-advisor-bot/webhook
         -> Core API -> Telegram sendMessage/sendPhoto
```

Verified on 2026-08-10 with the approved test account:

- Telegram `getWebhookInfo`: current Core webhook URL, no pending updates, no
  Telegram webhook error.
- effective Core route: `CORE_ROUTE_TELEGRAM=core`;
- direct signed webhook smoke: HTTP `200 {"ok":true}`;
- Core log: successful Telegram `sendMessage` HTTP 200.

## Legacy n8n status

`advisor-whieda-phase1` remains active only as a fast rollback artifact. Its
Telegram tail is intentionally parked:

```text
Webhook Trigger -> Respond: 200 ACK
```

The detached legacy branch starts at `Code: Normalize Payload`. It must not be
reconnected while Core owns Telegram delivery. This is intentional and was
introduced by `park_legacy_telegram_tail_2026-08-09.py` after Core cutover.

`Advisor 1.0` is an older mock workflow on a different webhook path and is not
the bot's Telegram route.

## Operational rule

For real bot diagnostics and feature work, test the Core Telegram webhook or
the bot itself. Do not use the legacy n8n webhook as a liveness test unless
performing an explicit rollback.

## Follow-up

Conversation reliability, No Blind Zone, product SQL answers and media must be
verified against Core. Legacy n8n remains untouched until a deliberate rollback
or final retirement decision.

## Group delivery guard: verified live

Core ignores ordinary messages in Telegram groups and supergroups. It processes
a group message only when it contains `@WHIEDA_Advisor_bot` or is a reply to
that bot. Private chats are unchanged.

Confirmed after deployment on 2026-08-10:

- `CORE_ROUTE_TELEGRAM=core` and `CORE_ROUTE_ADVISOR=shadow` stayed unchanged;
- `PLATFORM_TELEGRAM_BOT_USERNAME=WHIEDA_Advisor_bot` is explicit in live Core;
- a signed synthetic supergroup update returned the normal webhook ACK and was
  logged as `telegram_group_message_ignored` before advisor/delivery handling.

## Telegram experience restore: verified live 2026-08-11

Platform Core was deployed with the restored Telegram card renderer, ordered
per-chat delivery, first-turn routes, and cart remove/recalculate flow.

- health endpoints returned `200` after the deploy;
- a signed private-chat smoke was accepted for the approved test account:
  product flow, capabilities, out-of-scope request, cart calculation, and cart
  removal;
- live structured answers confirmed the full six-section product card for
  `Активатор клеток` and the cart recalculated after removing the activator;
- `interaction_events` was absent in production, so the idempotent
  `platform_interaction_events_v1.sql` migration was applied. `advisor_gap`
  events now persist without the former false warning in Core logs.

The exact word `активатор` deliberately asks which model is meant; the precise
`Активатор клеток` request opens the full card.

## Structured master sync Safety P0: verified live 2026-08-11

- captured a 20-layer read-only master snapshot at
  `n8n/live-exports/structured-master/20260811T172219Z`;
- applied the idempotent sync-run audit migration;
- deployed the atomic structured-sync workflow and linked Error Audit while
  preserving the already active main and error workflows;
- local n8n workflow backups were saved before the replacement;
- controlled execution `15745` completed successfully;
- immediate runtime check was healthy: products `40`, aliases `118`, resources
  `239`, product cards `23`, with no audit error on the successful run.
