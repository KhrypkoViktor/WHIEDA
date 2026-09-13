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
