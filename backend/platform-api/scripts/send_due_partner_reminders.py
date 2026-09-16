#!/usr/bin/env python3
"""Preview or send all currently due subscription reminders."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, init_pool
from app.subscriptions.reminders import (
    build_due_reminder_text,
    claim_reminder,
    list_due_reminders,
    mark_reminder_failed,
    mark_reminder_sent,
)
from app.telegram.bindings import resolve_bot_binding_context
from app.telegram.delivery import send_telegram_text
from app.telegram.log_safe import redact_telegram_secrets


async def run(args: argparse.Namespace) -> dict[str, object]:
    await init_pool()
    try:
        binding = await resolve_bot_binding_context(args.binding_id)
        if binding is None:
            raise RuntimeError("active bot binding not found")
        reminders = await list_due_reminders(binding.tenant.tenant_id, limit=args.limit)
        if args.ref_code:
            reminders = [row for row in reminders if row["ref_code"] == args.ref_code]
        preview = [
            {
                "ref_code": row["ref_code"],
                "event_type": row["event_type"],
                "chat_id": str(row["telegram_chat_id"]),
                "text": build_due_reminder_text(row),
            }
            for row in reminders
        ]
        if not args.send:
            return {"ok": True, "dry_run": True, "count": len(preview), "items": preview}
        if args.confirm_send != "SEND-DUE":
            raise RuntimeError("--send requires --confirm-send SEND-DUE")
        sent = 0
        failed = 0
        for row in reminders:
            delivery_id = await claim_reminder(binding.tenant.tenant_id, row)
            if delivery_id is None:
                continue
            try:
                result = await send_telegram_text(
                    chat_id=str(row["telegram_chat_id"]),
                    text=build_due_reminder_text(row),
                    bot_token=binding.bot_token,
                    reply_markup={
                        "inline_keyboard": [[
                            {"text": "Продлить платформу", "callback_data": "renew:start"},
                            {"text": "Поддержка", "url": "https://t.me/sunraysword"},
                        ]]
                    },
                )
                if not result.get("ok"):
                    raise RuntimeError("Telegram rejected reminder")
                await mark_reminder_sent(
                    binding.tenant.tenant_id,
                    delivery_id,
                    telegram_message_id=result.get("message_id"),
                )
                sent += 1
            except Exception as exc:
                await mark_reminder_failed(
                    binding.tenant.tenant_id, delivery_id, error=str(exc)
                )
                failed += 1
        # Gemini: licence-ending nudges and the low-deposit notice (v10).
        from app.service_sales.reminders import send_service_notices
        from app.settings import get_settings

        settings = get_settings()
        service = await send_service_notices(
            tenant_id=binding.tenant.tenant_id,
            binding_id=binding.binding_id,
            bot_token=binding.bot_token,
            admin_telegram_user_id=settings.platform_support_admin_telegram_id,
            owner_telegram_user_id=settings.platform_billing_owner_telegram_id,
        )
        return {
            "ok": failed == 0,
            "dry_run": False,
            "candidate_count": len(reminders),
            "sent": sent,
            "failed": failed,
            "service": service,
        }
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--ref-code")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirm-send")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        error = redact_telegram_secrets(str(exc)) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
