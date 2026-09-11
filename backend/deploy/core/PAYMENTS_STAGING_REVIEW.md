# Partner payments: final review before staging

## Scope now

- Payment registration, three-month extension, status and due list.
- Owner-only Telegram access.
- Initial free access through 21.09.2026 inclusive.
- Three-day grace and later partner-host redirect.
- Compact Telegram command menu.

Courses and the private file library are placeholders. Do not deploy storage,
upload course files or connect the website resources page in this canary.
Production is frozen.

## Local evidence

- Payment SQL/service: `db2eacd`.
- Telegram payment flow: `a357678`.
- Paid access and repeat prices: `2e6f224`, website `5f00fdd`.
- Edge access and redirect: `042fc12`.
- Real PostgreSQL routing canary: `b9120b0`.
- Compact Telegram menu: `53db8ad`.
- Current focused regression: `151 passed`; compileall and diff check passed.

## Minimal owner review before any deploy

1. Read the user-visible payment texts and command formats in
   `app/telegram/billing.py`.
2. Confirm that only `PLATFORM_BILLING_OWNER_TELEGRAM_ID` grants payment access.
3. Confirm the dates: active through 21.09.2026, grace 22-24.09.2026, suspended
   from 25.09.2026 00:00 Moscow time.
4. Confirm the Telegram menu command names and descriptions in
   `app/telegram/navigation.py`.
5. Run the focused regression command from `STATE.md`; no server access is needed.

## Staging canary after approval

1. Back up the staging database and current Core env; record current revision and
   `/health/ready` response.
2. Apply only `postgres/sql/platform_partner_subscriptions_v1.sql`. Do not apply
   the library migration for this canary.
3. Set the numeric owner ID in staging secrets. Never use username as authority.
4. Deploy the reviewed Core commit to staging only. Keep production unchanged.
5. Run `seed_initial_access --dry-run`, review every real partner hostname and its
   SHA, then apply that exact manifest.
6. Route only the staging admin bot binding to Core and configure its command menu
   using `TELEGRAM_COMMAND_MENU_RUNBOOK.md`.
7. In a private chat, test `status`, a cancelled payment preview, one confirmed RUB
   payment, one confirmed BYN payment, repeated confirmation and `/due`.
8. From another Telegram account, verify that billing commands return
   `Команда недоступна.` and create no rows.
9. Send `/start`: the large keyboard must disappear and Telegram's compact menu
   button must open the command list.
10. Return the staging binding to its previous mode if any result differs from the
    expected contract. Additive tables may remain; do not delete data during the
    first rollback.

Stop after the report. Website integration, edge activation on shared `:443` and
production require separate decisions.
