#!/usr/bin/env python3
"""Preview or send one partner payment reminder through a selected bot binding."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, init_pool
from app.subscriptions.reminder_text import build_partner_payment_reminder
from app.subscriptions.service import resolve_partner_for_billing
from app.telegram.bindings import resolve_bot_binding_context
from app.telegram.delivery import send_telegram_text
from app.telegram.log_safe import redact_telegram_secrets


async def run(args: argparse.Namespace) -> dict[str, object]:
    await init_pool()
    try:
        binding = await resolve_bot_binding_context(args.binding_id)
        if binding is None:
            raise RuntimeError("active bot binding not found")
        partner = await resolve_partner_for_billing(
            binding.tenant.tenant_id, args.identifier
        )
        paid_until = partner.get("paid_until")
        if paid_until is None:
            raise RuntimeError("partner has no paid_until date")
        name = args.name or partner.get("display_name") or partner["ref_code"]
        text = build_partner_payment_reminder(
            recipient_name=name,
            hostname=partner["hostname"],
            country=args.country,
            paid_until=paid_until,
        )
        result: dict[str, object] = {
            "ok": True,
            "dry_run": not args.send,
            "binding_id": binding.binding_id,
            "ref_code": partner["ref_code"],
            "chat_id": str(args.chat_id),
            "text": text,
        }
        if args.send:
            if args.confirm_send != "SEND-ONE":
                raise RuntimeError("--send requires --confirm-send SEND-ONE")
            delivery = await send_telegram_text(
                chat_id=str(args.chat_id),
                text=text,
                bot_token=binding.bot_token,
            )
            if not delivery.get("ok"):
                raise RuntimeError("Telegram rejected the reminder")
            result["message_id"] = delivery.get("message_id")
        return result
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--identifier", required=True, help="ref:code or @username")
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--country", required=True, help="РБ/BY or РФ/RU")
    parser.add_argument("--name")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirm-send")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, default=str))
    except Exception as exc:
        error = redact_telegram_secrets(str(exc)) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
