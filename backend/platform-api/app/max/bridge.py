"""Мост «группа потока в Telegram ↔ группа потока в Max» и контроль состава (V27, 10.10.2026).

Владелец (10.10.2026):
- его сообщения в Telegram-группе потока бот повторяет в Max-группе потока (текст,
  фото, видео, файлы, голосовые); куратор (Самцова) уходит в Max, только когда
  отвечает «реплаем» на сообщение бота — то есть на сообщение, пришедшее из Max;
- сообщения участников в Max бот переносит в Telegram-группу с подписью «Имя · Max»;
  ответ на сообщение в одной группе становится ответом в другой (chat_bridge_links);
- «пусть следит за составом: участники не должны появляться рандомно без оплаты» —
  в Telegram новый участник без оплаченного курса (shop_access) и не добавленный
  админом → владельцу сообщение с кнопками «Удалить» / «Оставить»; в Max оплату не
  сверить (другой аккаунт) — о каждом, кого добавил не владелец, тоже сообщение.

Правки и удаления сообщений не повторяются. Повтор доставки из Telegram отсекает
channel_crossposts (V24), повтор вебхука Max — chat_bridge_inbox.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import Any

import httpx

from app.db import fetch_one, tenant_connection
from app.max.bridges import BRIDGES, ChatBridge
from app.max.client import get_max_video_url, remove_max_member, send_max_message
from app.max.crosspost import ALBUM_WAIT_SEC, _finish, build_message, claim, record_post
from app.settings import get_settings

logger = logging.getLogger("whieda.max")

TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024  # Bot API принимает файлы до 50 МБ
CAPTION_LIMIT = 1024
_CONTENT_KEYS = ("text", "caption", "photo", "video", "animation", "video_note", "document", "voice", "audio", "sticker")
_ADMIN = {"administrator", "creator"}


def owner_telegram_id() -> int | None:
    value = get_settings().platform_billing_owner_telegram_id
    return int(value) if value else None


def bot_id_from_token(token: str) -> int | None:
    head = str(token or "").split(":", 1)[0]
    return int(head) if head.isdigit() else None


# ---- Telegram → Max ---------------------------------------------------------------------


def telegram_role(message: dict[str, Any], bridge: ChatBridge, *, owner_id: int | None, bot_id: int | None) -> str | None:
    """Кого из Telegram-группы везём в Max: владельца — всегда; в группе «all» (клуб) — всех;
    в группе потока куратора — только ответом на сообщение бота. Служебные — никогда."""
    if not any(message.get(key) for key in _CONTENT_KEYS):
        return None
    sender_info = message.get("from") or {}
    sender = int(sender_info.get("id") or 0)
    if owner_id and sender == owner_id:
        return "owner"
    if bridge.mode == "all":
        return "member" if sender and not sender_info.get("is_bot") else None
    if sender in bridge.curators:
        replied = ((message.get("reply_to_message") or {}).get("from") or {}).get("id")
        if bot_id and replied and int(replied) == bot_id:
            return "curator"
    return None


async def _max_mid_for(tenant_id: str, tg_chat_id: int, tg_message_id: int | None) -> str | None:
    if not tg_message_id:
        return None
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select max_mid from chat_bridge_links where tenant_id = %s and tg_chat_id = %s and tg_message_id = %s",
            (tenant_id, tg_chat_id, int(tg_message_id)),
        )
    return str(row["max_mid"]) if row else None


async def _tg_message_for(tenant_id: str, max_chat_id: int, max_mid: str | None) -> int | None:
    if not max_mid:
        return None
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select tg_message_id from chat_bridge_links
            where tenant_id = %s and max_chat_id = %s and max_mid = %s
            order by tg_message_id limit 1
            """,
            (tenant_id, max_chat_id, str(max_mid)),
        )
    return int(row["tg_message_id"]) if row else None


async def _link(tenant_id: str, bridge: ChatBridge, tg_message_ids: list[int], max_mid: str, direction: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        for tg_message_id in tg_message_ids:
            await conn.execute(
                """
                insert into chat_bridge_links (tenant_id, tg_chat_id, tg_message_id, max_chat_id, max_mid, direction)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (tenant_id, tg_chat_id, tg_message_id) do nothing
                """,
                (tenant_id, bridge.tg_chat_id, int(tg_message_id), bridge.max_chat_id, str(max_mid), direction),
            )


async def bridge_telegram_message(
    tenant_id: str, bridge: ChatBridge, message: dict[str, Any], *, role: str, bot_token: str, wait: float = ALBUM_WAIT_SEC
) -> dict[str, Any]:
    if await record_post(tenant_id, message) is None:
        return {"ok": True, "status": "duplicate"}
    group = message.get("media_group_id")
    if group:
        await asyncio.sleep(wait)
    rows = await claim(tenant_id, bridge.tg_chat_id, message_id=int(message["message_id"]), media_group_id=group)
    if not rows:
        return {"ok": True, "status": "claimed_elsewhere"}
    ids = [r["crosspost_id"] for r in rows]
    try:
        text, attachments = await build_message(rows, bot_token, rich=True)
        if role == "curator":
            name = bridge.curators.get(int((message.get("from") or {}).get("id") or 0)) or "Куратор"
            text = f"<b>{html.escape(name)}</b>:\n{text}" if text else f"<b>{html.escape(name)}</b>:"
        elif role == "member":
            label = f"<b>{html.escape(telegram_sender_name(message.get('from') or {}))}</b> · Telegram"
            text = f"{label}\n{text}" if text else label
        reply_mid = await _max_mid_for(
            tenant_id, bridge.tg_chat_id, (message.get("reply_to_message") or {}).get("message_id")
        )
        sent = await send_max_message(
            chat_id=bridge.max_chat_id, text=text, attachments=attachments, html_format=True, reply_to_mid=reply_mid
        )
    except Exception as exc:  # noqa: BLE001 — сообщение уже в журнале, статус виден
        logger.exception("chat_bridge_tg_to_max_failed")
        await _finish(tenant_id, ids, "failed", [], str(exc)[:500])
        return {"ok": False, "status": "failed"}
    ok = bool(sent.get("ok"))
    if ok and sent.get("mid"):
        await _link(tenant_id, bridge, [int(r["source_message_id"]) for r in rows], str(sent["mid"]), "tg_to_max")
    await _finish(
        tenant_id, ids, "sent" if ok else "failed",
        [{"chat_id": bridge.max_chat_id, "ok": ok, "mid": sent.get("mid")}], None if ok else "max_send_failed",
    )
    logger.info("chat_bridge_tg_to_max", extra={"ok": ok, "posts": len(rows), "role": role})
    return {"ok": ok, "status": "sent" if ok else "failed", "mid": sent.get("mid")}


def telegram_sender_name(sender: dict[str, Any]) -> str:
    name = " ".join(p for p in (str(sender.get("first_name") or "").strip(), str(sender.get("last_name") or "").strip()) if p)
    if not name and sender.get("username"):
        name = f"@{sender['username']}"
    return (name or "Участник")[:120]


# ---- Max → Telegram ---------------------------------------------------------------------


def max_sender_name(sender: dict[str, Any]) -> str:
    full = " ".join(
        p.strip() for p in (str(sender.get("first_name") or ""), str(sender.get("last_name") or "")) if p.strip()
    )
    name = full or str(sender.get("name") or "").strip()
    if not name and sender.get("username"):
        name = f"@{sender['username']}"
    return (name or "Участник")[:120]


async def _claim_max(tenant_id: str, bridge: ChatBridge, mid: str) -> bool:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into chat_bridge_inbox (tenant_id, max_chat_id, max_mid) values (%s, %s, %s)
            on conflict (tenant_id, max_chat_id, max_mid) do nothing
            returning max_mid
            """,
            (tenant_id, bridge.max_chat_id, mid),
        )
    return row is not None


async def _finish_max(tenant_id: str, bridge: ChatBridge, mid: str, status: str, error: str | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        await conn.execute(
            """
            update chat_bridge_inbox set status = %s, error = %s, updated_at = now()
            where tenant_id = %s and max_chat_id = %s and max_mid = %s
            """,
            (status, error, tenant_id, bridge.max_chat_id, mid),
        )


async def _max_media(attachment: dict[str, Any]) -> dict[str, Any] | None:
    """Вложение Max → что и откуда грузить в Telegram; None — передать нельзя (стикер, контакт…)."""
    kind = str(attachment.get("type") or "")
    payload = attachment.get("payload") or {}
    url = str(payload.get("url") or "")
    if kind == "image" and url:
        return {"method": "sendPhoto", "field": "photo", "url": url, "filename": "photo.jpg"}
    if kind == "video":
        url = url or (await get_max_video_url(str(payload.get("token") or "")) if payload.get("token") else "") or ""
        return {"method": "sendVideo", "field": "video", "url": url, "filename": "video.mp4"} if url else None
    if kind == "audio" and url:
        return {"method": "sendAudio", "field": "audio", "url": url, "filename": str(attachment.get("filename") or "audio.ogg")}
    if kind == "file" and url:
        return {"method": "sendDocument", "field": "document", "url": url, "filename": str(attachment.get("filename") or "file")}
    return None


async def _download(url: str) -> bytes | None:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400 or len(response.content) > TELEGRAM_UPLOAD_LIMIT:
        return None
    return response.content


async def _tg(method: str, bot_token: str, data: dict[str, Any], files: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        if files:
            form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in data.items()}
            response = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", data=form, files=files)
        else:
            response = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=data)
    body = response.json() if response.text else {}
    if response.status_code >= 400 or not body.get("ok"):
        logger.warning("chat_bridge_telegram_failed", extra={"method": method, "description": str(body.get("description") or "")[:200]})
        return {"ok": False}
    return {"ok": True, "message_id": (body.get("result") or {}).get("message_id")}


def _reply(tg_message_id: int | None) -> dict[str, Any]:
    return {"reply_parameters": {"message_id": tg_message_id, "allow_sending_without_reply": True}} if tg_message_id else {}


async def bridge_max_message(tenant_id: str, bridge: ChatBridge, update: dict[str, Any], *, bot_token: str) -> dict[str, Any]:
    message = update.get("message") or {}
    body = message.get("body") or {}
    mid = str(body.get("mid") or "")
    if not mid:
        return {"ok": False, "status": "no_mid"}
    if not await _claim_max(tenant_id, bridge, mid):
        return {"ok": True, "status": "duplicate"}
    sender = message.get("sender") or {}
    header = f"<b>{html.escape(max_sender_name(sender))}</b> · Max"
    text = str(body.get("text") or "").strip()
    caption = header + (f"\n{html.escape(text)}" if text else "")
    link = message.get("link") or {}
    reply_to = None
    if str(link.get("type") or "") == "reply":
        reply_to = await _tg_message_for(tenant_id, bridge.max_chat_id, str(((link.get("message") or {}).get("mid")) or ""))
    try:
        media: list[dict[str, Any]] = []
        skipped = 0
        for attachment in body.get("attachments") or []:
            item = await _max_media(attachment) if isinstance(attachment, dict) else None
            if item is None:
                skipped += int(str((attachment or {}).get("type") or "") not in {"inline_keyboard"})
                continue
            media.append(item)
        sent_ids: list[int] = []
        notes: list[str] = []
        files: list[tuple[dict[str, Any], bytes]] = []
        for item in media:
            data = await _download(item["url"])
            if data is None:
                notes.append("вложение больше 50 МБ — смотрите в Max")
            else:
                files.append((item, data))
        if skipped:
            notes.append("стикер или вложение, которое Telegram не покажет, — смотрите в Max")
        if notes:
            caption += "\n<i>" + html.escape("; ".join(notes)) + "</i>"
        base = {"chat_id": bridge.tg_chat_id, **_reply(reply_to)}
        if not files or len(caption) > CAPTION_LIMIT:
            sent = await _tg("sendMessage", bot_token, {**base, "text": caption[:4096], "parse_mode": "HTML", "disable_web_page_preview": True})
            if sent.get("message_id"):
                sent_ids.append(int(sent["message_id"]))
            first_caption = None
        else:
            first_caption = caption
        for index, (item, data) in enumerate(files):
            fields = dict(base) if not sent_ids else {"chat_id": bridge.tg_chat_id}
            if index == 0 and first_caption:
                fields.update({"caption": first_caption, "parse_mode": "HTML"})
            sent = await _tg(item["method"], bot_token, fields, {item["field"]: (item["filename"], data)})
            if not sent.get("ok") and item["method"] != "sendDocument":
                sent = await _tg("sendDocument", bot_token, fields, {"document": (item["filename"], data)})
            if sent.get("message_id"):
                sent_ids.append(int(sent["message_id"]))
    except Exception as exc:  # noqa: BLE001
        logger.exception("chat_bridge_max_to_tg_failed")
        await _finish_max(tenant_id, bridge, mid, "failed", str(exc)[:500])
        return {"ok": False, "status": "failed"}
    if sent_ids:
        await _link(tenant_id, bridge, sent_ids, mid, "max_to_tg")
    await _finish_max(tenant_id, bridge, mid, "sent" if sent_ids else "failed", None if sent_ids else "telegram_send_failed")
    logger.info("chat_bridge_max_to_tg", extra={"ok": bool(sent_ids), "messages": len(sent_ids)})
    return {"ok": bool(sent_ids), "status": "sent" if sent_ids else "failed", "tg_message_ids": sent_ids}


# ---- контроль состава -------------------------------------------------------------------


async def has_access(tenant_id: str, bridge: ChatBridge, telegram_user_id: int) -> bool:
    """Право быть в группе: активный клуб (группа клуба) или оплаченный курс (группа потока)."""
    if bridge.access == "club":
        from app.shop.service import is_club_member

        return await is_club_member(tenant_id, int(telegram_user_id))
    return await has_paid_course(tenant_id, bridge.course_item_code, int(telegram_user_id))


async def has_paid_course(tenant_id: str, item_code: str, telegram_user_id: int) -> bool:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select 1 as ok from shop_access
            where tenant_id = %s and item_code = %s and telegram_user_id = %s and revoked_at is null
            limit 1
            """,
            (tenant_id, item_code, int(telegram_user_id)),
        )
    return row is not None


def _person(member: dict[str, Any]) -> str:
    name = " ".join(p for p in (str(member.get("first_name") or "").strip(), str(member.get("last_name") or "").strip()) if p)
    name = name or str(member.get("name") or "").strip() or "Без имени"
    username = str(member.get("username") or "").strip()
    return html.escape(name + (f" (@{username})" if username else ""))


def _buttons(kind: str, index: int, user_id: int) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "Удалить", "callback_data": f"brg:{kind}:{index}:{user_id}"},
        {"text": "Оставить", "callback_data": "brg:keep"},
    ]]}


async def _alert_owner(text: str, markup: dict[str, Any], bot_token: str) -> None:
    owner = owner_telegram_id()
    if owner:
        await _tg("sendMessage", bot_token, {"chat_id": owner, "text": text, "parse_mode": "HTML", "reply_markup": markup})


async def watch_telegram_join(tenant_id: str, bridge: ChatBridge, message: dict[str, Any], *, bot_token: str) -> list[dict[str, Any]]:
    """Вступил в Telegram-группу потока: оплатил курс, свой (владелец, куратор) или добавлен
    админом — молчим; иначе — владельцу кнопки «Удалить» / «Оставить»."""
    adder = int((message.get("from") or {}).get("id") or 0)
    owner = owner_telegram_id()
    added_by_admin = False
    results: list[dict[str, Any]] = []
    for member in message.get("new_chat_members") or []:
        if not isinstance(member, dict) or member.get("is_bot"):
            continue
        user_id = int(member.get("id") or 0)
        if user_id == owner or user_id in bridge.curators:
            results.append({"user_id": user_id, "status": "staff"})
            continue
        if await has_access(tenant_id, bridge, user_id):
            results.append({"user_id": user_id, "status": "paid"})
            continue
        if adder and adder != user_id:
            if not added_by_admin:
                state = await _tg_member_status(bot_token, bridge.tg_chat_id, adder)
                added_by_admin = state in _ADMIN
            if added_by_admin:
                results.append({"user_id": user_id, "status": "added_by_admin"})
                continue
        index = BRIDGES.index(bridge)
        paid = "активного клуба" if bridge.access == "club" else "оплаты курса"
        await _alert_owner(
            f"⚠️ В Telegram-группу «{html.escape(bridge.title)}» вошёл {_person(member)}, id {user_id}.\n"
            f"{paid.capitalize()} у него не вижу. Удалить из группы?",
            _buttons("tk", index, user_id), bot_token,
        )
        results.append({"user_id": user_id, "status": "alerted"})
    logger.info("chat_bridge_tg_join", extra={"members": results})
    return results


async def _tg_member_status(bot_token: str, chat_id: int, user_id: int) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{bot_token}/getChatMember", json={"chat_id": chat_id, "user_id": user_id}
        )
    body = response.json() if response.text else {}
    return str(((body.get("result") or {}).get("status")) or "")


async def watch_max_join(bridge: ChatBridge, update: dict[str, Any], *, bot_token: str) -> dict[str, Any]:
    """Вступил в Max-группу потока. Оплату по аккаунту Max не сверить — молчим, только если
    добавил владелец (его Max-аккаунт); иначе — владельцу кнопки «Удалить» / «Оставить»."""
    user = update.get("user") or {}
    user_id = int(user.get("user_id") or 0)
    if not user_id or user.get("is_bot"):
        return {"status": "ignored"}
    inviter = int(update.get("inviter_id") or 0)
    if inviter and inviter in bridge.max_staff:
        return {"status": "added_by_owner", "user_id": user_id}
    if user_id in bridge.max_staff:
        return {"status": "staff", "user_id": user_id}
    how = "по ссылке" if not inviter else f"(добавил участник id {inviter})"
    await _alert_owner(
        f"⚠️ В Max-группу «{html.escape(bridge.title)}» вошёл {_person(user)} {how}.\n"
        "В Max оплату не сверить — проверьте, что он оплатил. Удалить из группы?",
        _buttons("mk", BRIDGES.index(bridge), user_id), bot_token,
    )
    return {"status": "alerted", "user_id": user_id}


async def handle_owner_callback(callback: dict[str, Any], *, bot_token: str) -> dict[str, Any]:
    """Кнопки владельца: brg:tk:<пара>:<id> — удалить из Telegram, brg:mk:… — из Max, brg:keep."""
    data = str(callback.get("data") or "")
    owner = owner_telegram_id()
    sender = int((callback.get("from") or {}).get("id") or 0)
    message = callback.get("message") or {}
    if not owner or sender != owner:
        await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Только для владельца"})
        return {"ok": False, "status": "not_owner"}
    note = "Оставлен."
    if data != "brg:keep":
        try:
            _, kind, index, user_id = data.split(":")
            bridge = BRIDGES[int(index)]
            if kind == "tk":
                await _tg("banChatMember", bot_token, {"chat_id": bridge.tg_chat_id, "user_id": int(user_id)})
                await _tg("unbanChatMember", bot_token, {"chat_id": bridge.tg_chat_id, "user_id": int(user_id), "only_if_banned": True})
                note = "Удалён из Telegram-группы."
            elif kind == "mk":
                removed = await remove_max_member(bridge.max_chat_id, int(user_id))
                note = "Удалён из Max-группы." if removed.get("ok") else "Удалить из Max не вышло — удалите вручную."
        except (ValueError, IndexError):
            note = "Кнопка устарела."
    await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": note})
    if message.get("message_id"):
        await _tg(
            "editMessageText", bot_token,
            {
                "chat_id": (message.get("chat") or {}).get("id"), "message_id": message["message_id"],
                "text": f"{message.get('text') or ''}\n\n→ {note}"[:4096],
            },
        )
    return {"ok": True, "status": note}
