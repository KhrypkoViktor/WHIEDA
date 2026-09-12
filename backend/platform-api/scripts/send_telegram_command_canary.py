#!/usr/bin/env python3
"""Run one real Telegram command through Core for the configured billing owner."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, init_pool
from app.settings import get_settings
from app.telegram.bindings import resolve_bot_binding_context
from app.telegram.log_safe import redact_telegram_secrets
from app.telegram.processor import process_core_telegram_update


async def run(binding_id: str, command: str, username: str) -> dict[str, object]:
    owner_id = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    if not owner_id.isdigit():
        raise RuntimeError("PLATFORM_BILLING_OWNER_TELEGRAM_ID is not configured")
    await init_pool()
    try:
        binding = await resolve_bot_binding_context(binding_id)
        if binding is None:
            raise RuntimeError("active bot binding not found")
        marker = int(time.time())
        update = {
            "update_id": marker,
            "message": {
                "message_id": marker,
                "text": command,
                "chat": {"id": int(owner_id), "type": "private"},
                "from": {"id": int(owner_id), "username": username},
            },
        }
        result = await process_core_telegram_update(
            binding.tenant,
            update,
            f"telegram-canary-{marker}",
            binding=binding,
        )
        return {
            "ok": bool(result.get("ok")),
            "binding_id": binding.binding_id,
            "command": command,
            "route": result.get("route"),
        }
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding_id")
    parser.add_argument("--command", default="/cabinet")
    parser.add_argument("--username", default="sunraysword")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.binding_id, args.command, args.username))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        error = redact_telegram_secrets(str(exc)) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
