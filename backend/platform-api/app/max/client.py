"""HTTP-клиент Max Bot API (https://dev.max.ru/docs-api).

Токен — только из настроек (PLATFORM_MAX_BOT_TOKEN) и только в заголовке
Authorization; в логи и ответы не попадает.
"""

from __future__ import annotations

import asyncio
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


# bot_added / bot_removed — единственный способ узнать чаты бота: GET /chats убрали в июне 2026.
# user_added — контроль состава группы потока (мост V27, 10.10.2026).
UPDATE_TYPES = ["bot_started", "message_created", "bot_added", "bot_removed", "user_added"]


async def send_max_message(
    *,
    chat_id: int | str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    html_format: bool = False,
    reply_to_mid: str | None = None,
) -> dict[str, Any]:
    """POST /messages?chat_id=… с вложениями. Вложение только что загружено — Max
    может ответить attachment.not.ready: ждём и повторяем. reply_to_mid — ответ
    на сообщение (link type reply). В ответе — mid нового сообщения."""
    body: dict[str, Any] = {"text": (text or "")[:4000] or None}
    if attachments:
        body["attachments"] = attachments
    if html_format:
        body["format"] = "html"
    if reply_to_mid:
        body["link"] = {"type": "reply", "mid": str(reply_to_mid)}
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for delay in (0, 1, 2, 4, 8):
            if delay:
                await asyncio.sleep(delay)
            resp = await client.post(f"{base}/messages", params={"chat_id": str(chat_id)}, headers=_headers(), json=body)
            if resp.status_code < 400:
                result = resp.json()
                mid = (((result or {}).get("message") or {}).get("body") or {}).get("mid")
                return {"ok": True, "result": result, "mid": str(mid) if mid else None}
            if "attachment.not.ready" not in resp.text:
                break
    logger.warning("max_send_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
    return {"ok": False, "status": resp.status_code}


DEFAULT_UPLOAD_NAMES = {"image": "photo.jpg", "video": "video.mp4", "audio": "audio.ogg", "file": "file.bin"}


async def upload_max_media(kind: str, body: bytes, *, filename: str | None = None) -> dict[str, Any]:
    """POST /uploads?type=image|video|audio|file → загрузка файла (поле data) → вложение
    для /messages. Токен картинки приходит в ответе загрузки, видео и аудио — сразу в
    /uploads, у файла — в ответе загрузки; берём тот, что есть."""
    base = get_settings().max_api_base.rstrip("/")
    name = filename or DEFAULT_UPLOAD_NAMES.get(kind, "file.bin")
    async with httpx.AsyncClient(timeout=120.0) as client:
        slot = await client.post(f"{base}/uploads", params={"type": kind}, headers=_headers())
        slot.raise_for_status()
        info = slot.json()
        done = await client.post(str(info["url"]), files={"data": (name, body)})
        done.raise_for_status()
    token = info.get("token")
    try:
        uploaded = done.json() or {}
    except ValueError:
        uploaded = {}
    if kind == "image":
        photos = uploaded.get("photos") or {}
        token = next(iter(photos.values()))["token"] if photos else token
    elif not token:
        token = uploaded.get("token")
    if not token:
        raise RuntimeError("max_upload_without_token")
    return {"type": kind, "payload": {"token": token}}


async def get_chat(chat_id: int | str) -> dict[str, Any]:
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base}/chats/{chat_id}", headers=_headers())
    return resp.json() if resp.status_code < 400 else {}


async def subscribe_webhook(url: str, *, secret: str | None, update_types: list[str] | None = None) -> dict[str, Any]:
    """POST /subscriptions — регистрирует webhook (делается один раз при выкладке)."""
    base = get_settings().max_api_base.rstrip("/")
    body: dict[str, Any] = {"url": url, "update_types": update_types or UPDATE_TYPES}
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


async def get_max_video_url(token: str) -> str | None:
    """GET /videos/{token}: во входящем видео Max ссылки нет — только токен; берём лучшее качество."""
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base}/videos/{token}", headers=_headers())
    if resp.status_code >= 400:
        return None
    urls = (resp.json() or {}).get("urls") or {}
    for key in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144"):
        if urls.get(key):
            return str(urls[key])
    return next((str(v) for v in urls.values() if v), None)


async def remove_max_member(chat_id: int | str, user_id: int | str) -> dict[str, Any]:
    """DELETE /chats/{chat_id}/members?user_id=… — убрать участника из группы (бот — админ)."""
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.delete(f"{base}/chats/{chat_id}/members", params={"user_id": str(user_id)}, headers=_headers())
    if resp.status_code >= 400:
        logger.warning("max_remove_member_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
        return {"ok": False, "status": resp.status_code}
    return {"ok": True}
