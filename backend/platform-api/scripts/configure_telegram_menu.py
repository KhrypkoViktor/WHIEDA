#!/usr/bin/env python3
"""Configure the compact Telegram command menu for one active bot binding."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, init_pool
from app.telegram.bindings import resolve_bot_binding_context
from app.telegram.delivery import configure_telegram_command_menu
from app.telegram.log_safe import redact_telegram_secrets
from app.telegram.navigation import telegram_menu_commands


async def configure(binding_id: str, *, dry_run: bool) -> dict[str, object]:
    await init_pool()
    try:
        binding = await resolve_bot_binding_context(binding_id)
        if binding is None:
            raise RuntimeError("active bot binding not found")
        commands = telegram_menu_commands(
            include_calculator=binding.tenant.tenant_id == "whieda"
        )
        if not dry_run:
            result = await configure_telegram_command_menu(
                bot_token=binding.bot_token,
                commands=commands,
            )
            if not result.get("ok"):
                raise RuntimeError("Telegram rejected menu configuration")
        return {
            "ok": True,
            "dry_run": dry_run,
            "binding_id": binding.binding_id,
            "tenant_id": binding.tenant.tenant_id,
            "commands": [item["command"] for item in commands],
        }
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binding_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(configure(args.binding_id, dry_run=args.dry_run))))
    except Exception as exc:
        error = redact_telegram_secrets(str(exc)) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": error}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
