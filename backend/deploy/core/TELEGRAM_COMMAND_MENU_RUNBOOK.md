# Telegram compact command menu

The old persistent reply keyboard is removed when the user next sends `/start`.
Telegram's standard menu button is configured once per active bot binding.

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
