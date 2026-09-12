#!/usr/bin/env python3
"""Check one Telegram binding without exposing its bot token."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, init_pool
from app.telegram.bindings import resolve_bot_binding_context
from app.telegram.log_safe import redact_telegram_secrets


async def run(args: argparse.Namespace) -> dict[str, object]:
    await init_pool()
    try:
        binding = await resolve_bot_binding_context(args.binding_id)
        if binding is None:
            raise RuntimeError("active bot binding not found")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{binding.bot_token}/getWebhookInfo"
            )
            response.raise_for_status()
            payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram rejected getWebhookInfo")
        info = payload.get("result") or {}
        url = str(info.get("url") or "")
        pending = int(info.get("pending_update_count") or 0)
        healthy = bool(url) and pending <= args.max_pending
        if args.expected_url_fragment:
            healthy = healthy and args.expected_url_fragment in url
        return {
            "ok": healthy,
            "binding_id": binding.binding_id,
            "webhook_configured": bool(url),
            "webhook_host": url.split("/", 3)[2] if url.startswith("https://") else "",
            "pending_update_count": pending,
            "last_error_date": info.get("last_error_date"),
            "last_error_message": info.get("last_error_message"),
        }
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--max-pending", type=int, default=3)
    parser.add_argument("--expected-url-fragment", default="")
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
