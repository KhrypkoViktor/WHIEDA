"""HTTP-клиент Max Bot API (https://dev.max.ru/docs-api).

Токен — только из настроек (PLATFORM_MAX_BOT_TOKEN) и только в заголовке
Authorization; в логи и ответы не попадает.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.settings import get_settings

logger = logging.getLogger("whieda.max")


class MaxNotConfigured(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    token = get_settings().max_bot_token
    if not token:
        raise MaxNotConfigured("PLATFORM_MAX_BOT_TOKEN is not set")
    return {"Authorization": token, "Content-Type": "application/json"}


async def send_max_text(
    *,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    text: str,
    disable_link_preview: bool = False,
) -> dict[str, Any]:
    """POST /messages?chat_id=… | ?user_id=…  {text}. Пустой текст не шлём."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "skipped": "empty"}
    if not user_id and not chat_id:
        raise ValueError("user_id or chat_id required")
    params: dict[str, str] = {}
    if chat_id:
        params["chat_id"] = str(chat_id)
    elif user_id:
        params["user_id"] = str(user_id)
    if disable_link_preview:
        params["disable_link_preview"] = "true"
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base}/messages", params=params, headers=_headers(), json={"text": text[:4000]})
    if resp.status_code >= 400:
        logger.warning("max_send_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
        return {"ok": False, "status": resp.status_code}
    return {"ok": True, "result": resp.json()}


async def subscribe_webhook(url: str, *, secret: str | None, update_types: list[str] | None = None) -> dict[str, Any]:
    """POST /subscriptions — регистрирует webhook (делается один раз при выкладке)."""
    base = get_settings().max_api_base.rstrip("/")
    body: dict[str, Any] = {"url": url, "update_types": update_types or ["bot_started", "message_created"]}
    if secret:
        body["secret"] = secret
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base}/subscriptions", headers=_headers(), json=body)
    return {"ok": resp.status_code < 400, "status": resp.status_code, "body": resp.text[:500]}


async def get_me() -> dict[str, Any]:
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base}/me", headers=_headers())
    return {"ok": resp.status_code < 400, "status": resp.status_code, "body": resp.json() if resp.status_code < 400 else resp.text[:300]}
