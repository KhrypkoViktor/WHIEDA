"""Посты Telegram-канала → чаты Max (V24, 08.10.2026).

Владелец пишет пост в «WWC Official channel» в Telegram — красиво, со своими
смайликами; бот повторяет его во всех чатах Max, куда его добавили (max_chats,
crosspost = true). Свои смайлики Telegram в Max превращаются в обычные эмодзи:
в тексте поста уже стоит их «обычный» вариант. Жирный, курсив, ссылки — HTML Max.

Альбом приходит отдельными обновлениями с одним media_group_id: каждое пишется
строкой, через ALBUM_WAIT_SEC одна из задач забирает все — и в Max уходит одно
сообщение. Повтор доставки Telegram строку не дублирует (unique по посту).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import Any

import httpx

from app.db import fetch_all, fetch_one, tenant_connection
from app.max.client import send_max_message, upload_max_media

logger = logging.getLogger("whieda.max")

ALBUM_WAIT_SEC = 4.0
TELEGRAM_FILE_LIMIT = 20 * 1024 * 1024  # Bot API отдаёт файлы до 20 МБ
MAX_TEXT_LIMIT = 4000

_TAGS = {
    "bold": ("<b>", "</b>"),
    "italic": ("<i>", "</i>"),
    "underline": ("<u>", "</u>"),
    "strikethrough": ("<s>", "</s>"),
    "code": ("<code>", "</code>"),
    "pre": ("<pre>", "</pre>"),
}


def _utf16_index(text: str) -> list[int]:
    """Позиция UTF-16 (как считает Telegram) → индекс символа Python."""
    index = [0]
    for i, ch in enumerate(text):
        index.extend([i + 1] * (2 if ord(ch) > 0xFFFF else 1))
    return index


def entities_to_html(text: str, entities: list[dict[str, Any]] | None) -> str:
    """Текст поста с разметкой Telegram → HTML для Max. Неизвестное — просто текст."""
    if not text:
        return ""
    pos = _utf16_index(text)
    opens: dict[int, list[tuple[int, str, str]]] = {}
    for entity in entities or []:
        kind = str(entity.get("type") or "")
        if kind == "text_link" and entity.get("url"):
            tags = (f'<a href="{html.escape(str(entity["url"]), quote=True)}">', "</a>")
        elif kind in _TAGS:
            tags = _TAGS[kind]
        else:
            continue  # custom_emoji, mention, hashtag, url, spoiler — как есть
        start, length = int(entity.get("offset") or 0), int(entity.get("length") or 0)
        if length <= 0 or start >= len(pos) - 1:
            continue
        a = pos[start]
        b = pos[min(start + length, len(pos) - 1)]
        if b <= a:
            continue
        opens.setdefault(a, []).append((b, tags[0], tags[1]))
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    for i in range(len(text) + 1):
        while stack and stack[-1][0] == i:
            out.append(stack.pop()[1])
        if i == len(text):
            break
        for end, open_tag, close_tag in sorted(opens.get(i, []), key=lambda item: -item[0]):
            out.append(open_tag)
            stack.append((end, close_tag))
        out.append(html.escape(text[i], quote=False))
    while stack:
        out.append(stack.pop()[1])
    return "".join(out)


def _media(kind: str, item: dict[str, Any], filename: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "file_id": item.get("file_id"), "size": item.get("file_size"), "filename": filename}


def post_payload(post: dict[str, Any], *, rich: bool = False) -> dict[str, Any]:
    """Что из сообщения Telegram уходит в Max: текст (HTML) и одно вложение.

    Канал (rich=False) везёт только фото и видео — остальное ссылкой на пост. Мост
    группы потока (rich=True, V27) везёт ещё файлы, голосовые, аудио, гифки и кружки."""
    text = str(post.get("text") or post.get("caption") or "")
    entities = post.get("entities") or post.get("caption_entities") or []
    media: dict[str, Any] | None = None
    photos = post.get("photo") or []
    if photos:
        media = _media("image", photos[-1])
    elif post.get("video"):
        media = _media("video", post["video"], post["video"].get("file_name"))
    elif rich and (post.get("animation") or post.get("video_note")):
        media = _media("video", post.get("animation") or post["video_note"])
    elif rich and post.get("document"):
        media = _media("file", post["document"], post["document"].get("file_name"))
    elif rich and (post.get("voice") or post.get("audio")):
        item = post.get("voice") or post["audio"]
        media = _media("audio", item, item.get("file_name") or ("voice.ogg" if post.get("voice") else None))
    elif any(post.get(k) for k in ("animation", "document", "audio", "voice", "video_note", "sticker")):
        media = {"kind": "unsupported"}
    return {"html": entities_to_html(text, entities), "media": media}


def post_link(post: dict[str, Any]) -> str | None:
    chat = post.get("chat") or {}
    username = str(chat.get("username") or "").strip()
    return f"https://t.me/{username}/{post.get('message_id')}" if username else None


async def record_post(tenant_id: str, post: dict[str, Any]) -> dict[str, Any] | None:
    """Строка поста; None — этот пост уже записан (повтор доставки Telegram)."""
    chat_id = int((post.get("chat") or {})["id"])
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            insert into channel_crossposts (tenant_id, source_chat_id, source_message_id, media_group_id, payload)
            values (%s, %s, %s, %s, %s::jsonb)
            on conflict (tenant_id, source_chat_id, source_message_id) do nothing
            returning crosspost_id::text as crosspost_id
            """,
            (tenant_id, chat_id, int(post["message_id"]), post.get("media_group_id"), json.dumps(post, ensure_ascii=False)),
        )


async def claim(tenant_id: str, chat_id: int, *, message_id: int, media_group_id: str | None) -> list[dict[str, Any]]:
    """Забрать посты к отправке: весь альбом разом или один пост. Кто первый — тот и шлёт."""
    async with tenant_connection(tenant_id) as conn:
        if media_group_id:
            rows = await fetch_all(
                conn,
                """
                update channel_crossposts set status = 'sending', updated_at = now()
                where tenant_id = %s and source_chat_id = %s and media_group_id = %s and status = 'pending'
                returning crosspost_id::text as crosspost_id, source_message_id, payload
                """,
                (tenant_id, chat_id, media_group_id),
            )
        else:
            rows = await fetch_all(
                conn,
                """
                update channel_crossposts set status = 'sending', updated_at = now()
                where tenant_id = %s and source_chat_id = %s and source_message_id = %s and status = 'pending'
                returning crosspost_id::text as crosspost_id, source_message_id, payload
                """,
                (tenant_id, chat_id, message_id),
            )
    return sorted((dict(r) for r in rows), key=lambda r: int(r["source_message_id"]))


async def target_chats(tenant_id: str) -> list[int]:
    """Чаты Max для постов канала. Группы потоков с мостом (V27) — никогда: туда идёт
    только своя Telegram-группа, даже если флаг crosspost кто-то включит."""
    from app.max.bridges import bridged_max_chat_ids

    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            "select chat_id from max_chats where tenant_id = %s and status = 'active' and crosspost order by created_at",
            (tenant_id,),
        )
    bridged = bridged_max_chat_ids()
    return [int(r["chat_id"]) for r in rows if int(r["chat_id"]) not in bridged]


async def _finish(tenant_id: str, ids: list[str], status: str, delivered: list[Any], error: str | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update channel_crossposts set status = %s, delivered = %s::jsonb, error = %s, updated_at = now()
            where tenant_id = %s and crosspost_id = any(%s::uuid[])
            returning crosspost_id
            """,
            (status, json.dumps(delivered), error, tenant_id, ids),
        )


async def download_telegram_file(bot_token: str, file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0) as client:
        info = (await client.get(f"https://api.telegram.org/bot{bot_token}/getFile", params={"file_id": file_id})).json()
        path = ((info or {}).get("result") or {}).get("file_path")
        if not path:
            raise RuntimeError("telegram_get_file_failed")
        response = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{path}")
        response.raise_for_status()
        return response.content


async def build_message(
    rows: list[dict[str, Any]], bot_token: str, *, rich: bool = False
) -> tuple[str, list[dict[str, Any]]]:
    """Текст (подпись альбома — первая непустая) и вложения Max для набора постов."""
    texts: list[str] = []
    attachments: list[dict[str, Any]] = []
    missing_media = False
    link = None
    for row in rows:
        post = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        link = link or post_link(post)
        payload = post_payload(post, rich=rich)
        if payload["html"]:
            texts.append(payload["html"])
        media = payload["media"]
        if not media:
            continue
        if media["kind"] == "unsupported" or (media.get("size") or 0) > TELEGRAM_FILE_LIMIT or not media.get("file_id"):
            missing_media = True
            continue
        body = await download_telegram_file(bot_token, str(media["file_id"]))
        attachments.append(await upload_max_media(media["kind"], body, filename=media.get("filename")))
    text = "\n\n".join(texts)
    if missing_media and rich:
        text = (text + "\n\n" if text else "") + "<i>Вложение больше 20 МБ или стикер — смотрите в Telegram-группе.</i>"
    elif missing_media and link:
        text = (text + "\n\n" if text else "") + f'Видео и файлы — <a href="{html.escape(link, quote=True)}">в Telegram-канале</a>'
    return text[:MAX_TEXT_LIMIT], attachments


async def crosspost_channel_post(tenant_id: str, post: dict[str, Any], *, bot_token: str, wait: float = ALBUM_WAIT_SEC) -> dict[str, Any]:
    chat_id = int((post.get("chat") or {}).get("id") or 0)
    recorded = await record_post(tenant_id, post)
    if recorded is None:
        return {"ok": True, "status": "duplicate"}
    group = post.get("media_group_id")
    if group:
        await asyncio.sleep(wait)
    rows = await claim(tenant_id, chat_id, message_id=int(post["message_id"]), media_group_id=group)
    if not rows:
        return {"ok": True, "status": "claimed_elsewhere"}
    ids = [r["crosspost_id"] for r in rows]
    chats = await target_chats(tenant_id)
    if not chats:
        await _finish(tenant_id, ids, "skipped", [], "no_max_chats")
        return {"ok": True, "status": "no_max_chats"}
    try:
        text, attachments = await build_message(rows, bot_token)
        delivered = []
        for target in chats:
            sent = await send_max_message(chat_id=target, text=text, attachments=attachments, html_format=True)
            delivered.append({"chat_id": target, "ok": bool(sent.get("ok")), "status": sent.get("status")})
            await asyncio.sleep(0.6)  # не больше двух сообщений в секунду в чат
    except Exception as exc:  # noqa: BLE001 — пост уже в журнале, владельцу видно по статусу
        logger.exception("max_crosspost_failed", extra={"source_message_id": post.get("message_id")})
        await _finish(tenant_id, ids, "failed", [], str(exc)[:500])
        return {"ok": False, "status": "failed"}
    ok = all(d["ok"] for d in delivered)
    await _finish(tenant_id, ids, "sent" if ok else "failed", delivered, None if ok else "max_send_failed")
    logger.info("max_crosspost_sent", extra={"posts": len(rows), "chats": len(chats), "ok": ok})
    return {"ok": ok, "status": "sent" if ok else "failed", "posts": len(rows), "chats": delivered}


async def remember_max_chat(
    tenant_id: str, *, chat_id: int, title: str | None, chat_type: str | None, is_channel: bool, added_by: int | None, active: bool
) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            insert into max_chats (tenant_id, chat_id, title, chat_type, is_channel, status, added_by_user_id)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, chat_id) do update set
              title = coalesce(excluded.title, max_chats.title), chat_type = coalesce(excluded.chat_type, max_chats.chat_type),
              is_channel = excluded.is_channel, status = excluded.status,
              added_by_user_id = coalesce(excluded.added_by_user_id, max_chats.added_by_user_id), updated_at = now()
            returning chat_id, title, status, crosspost
            """,
            (tenant_id, chat_id, title, chat_type, is_channel, "active" if active else "removed", added_by),
        )
