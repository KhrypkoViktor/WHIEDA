# Telegram compact command menu

The old persistent reply keyboard is removed when the user next sends `/start`.
Telegram's standard menu button is configured once per active bot binding.

## Interface profile

The bot bindings use different Telegram tokens, but the current staging and
production Core instances share one database. Test screens and command menus on
staging; do not create fake payments, referrals, or people there.

Set the environment before deploying the matching Core instance:

- production (`whieda-advisor-bot`): `PLATFORM_TELEGRAM_UI_PROFILE=minimal`;
- staging (`wwc-cabinet-staging-bot`): `PLATFORM_TELEGRAM_UI_PROFILE=full`.

`minimal` publishes only `/cabinet`, `/invite`, `/calculator`, and `/support`.
The cabinet still gives access to the personal site, copied referral link,
invitation, calculator and support. Site creation, renewals, payment-proof
collection and bonus redemption are hidden. An old button receives a manual
contact message rather than starting a transaction.

Run inside the Core API container, with its normal database and bot-token env:

```bash
python scripts/configure_telegram_menu.py <binding-id> --dry-run
python scripts/configure_telegram_menu.py <binding-id>
```

The command resolves the token through the existing binding secret reference and
never prints it. It installs `setMyCommands` and forces the default
`setChatMenuButton` type to `commands`.

Canary in a private chat:

1. Send `/start`; the old large keyboard must disappear.
2. Tap Telegram's menu icon beside the message field.
3. Select `products`; the catalog opens without entering advisor routing.
4. Select `calculator` on WHIEDA; the calculator link opens.
5. Confirm that NSP has no calculator command.

Do not change a production bot while validating staging.

## Three menus by the person's stage (04.11.2026, migration V49)

With `PLATFORM_BOT_STAGES=true` (the default) the WWC bot shows one of three screens by the person's stage
(`lead_actors.bot_stage`, code in `app/bot_stages/`): `guest` (any `/start`, `src_<tag>`, partner link),
`partner_whieda` (the host confirmed him in WWC CRM, or the owner wrote «партнёр @ник») and `partner_wwc` (an enabled
`referral_profiles`, i.e. a paid site; the owner and the admins too). The profile above then no longer chooses the
cabinet buttons; it still decides whether site orders and renewals are manual (`minimal`).

- Apply `postgres/sql/platform_bot_stage_v49.sql` to the database first (twice is safe); code that reads the column before
  the migration falls back to «partner_wwc by the site profile, else guest» and does not fail.
- The blue «Меню» button is set per chat (`setMyCommands`, `scope=chat`) at the first `/start` and when the stage changes:
  guest `/start /support`, WHIEDA partner `/start /site /course /support`, WWC partner the six `minimal` commands.
  `configure_telegram_menu.py` still sets the default list for everyone; leave it as it is.
- The host question «… стал партнёром WHIEDA — подтвердить?» is a `platform_outbox` row (`bot_stage_confirm_asked`), sent by
  the production worker (`PLATFORM_SCHEDULED_NOTIFY_BINDINGS`); staging without that variable sends nothing.
- `PLATFORM_DEMO_PARTNER_URL` — the demo site for «Пример сайта партнёра» when the inviter has no site.
- Rollback without a deploy: `PLATFORM_BOT_STAGES=false` and restart Core; stages already written stay in the table.

