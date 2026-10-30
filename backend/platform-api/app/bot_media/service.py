"""Хранилище медиа бота: запросы к ``bot_media`` (V45) и отправка файла по ключу в Telegram и Max.

Файл загружает владелец в Telegram-боте WWC (app/telegram/bot_media_admin.py): в таблицу попадает ``telegram_file_id``
и вид (``kind``). Отправка:

* Telegram — по ``telegram_file_id``, без загрузки (``send_telegram_media``). Файл-id действует только у того бота,
  который его получил, поэтому отправлять нужно ботом WWC (текущая привязка или ``whieda-advisor-bot``).
* Max — по ``max_token``. Пока токена нет, файл один раз скачивается из Telegram (getFile), грузится в Max
  (``upload_max_media``: кружок → video, голосовое → audio, фото → image, документ → file), токен записывается
  в строку. Telegram отдаёт ботам файлы до 20 МБ: если файл больше, в Max он не уйдёт (``download_failed``) —
  вызывающий код отвечает текстом-заглушкой.

Функции записи не делают commit: его делает ``tenant_connection`` вызывающего. ``send_media`` принимает уже открытое
подключение (``conn``) или ``None`` — тогда подключения открываются коротко, только на чтение строки и запись токена
(на время сети — скачивание, загрузка, отправка — транзакция не держится).

Токен бота (он стоит в адресе скачивания) и токены вложений в журнал не попадают: пишутся ключ, статус и тип ошибки.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.max.client import max_inline_keyboard, send_max_attachment, upload_max_media
from app.telegram.bindings import current_bot_binding, resolve_bot_binding_context
from app.telegram.delivery import (
    TelegramFileUnavailable,
    download_telegram_file,
    send_telegram_media,
    send_telegram_text,
    telegram_caption_fits,
)

logger = logging.getLogger("whieda.bot_media")

KINDS = ("video", "voice", "video_note", "photo", "audio", "document")
KEY_RE = re.compile(r"[a-z0-9_-]{1,64}")
# Вид файла бота → тип вложения в Max (uploads?type=image|video|audio|file).
MAX_KIND = {
    "video": "video",
    "video_note": "video",
    "voice": "audio",
    "audio": "audio",
    "photo": "image",
    "document": "file",
}
# Бот WWC: file_id из Telegram действует только у него (по нему же скачивается файл для Max).
WWC_BINDING_ID = "whieda-advisor-bot"

_COLUMNS = (
    "media_id, tenant_id, key, kind, telegram_file_id, max_token, caption, source_note, created_by, created_at, updated_at"
)
_upload_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def _bot_binding(binding: Any) -> Any:
    """Привязка бота WWC: переданная, иначе текущая (внутри обработки апдейта), иначе ``whieda-advisor-bot``."""
    if binding is not None:
        return binding
    try:
        return current_bot_binding()
    except RuntimeError:
        return await resolve_bot_binding_context(WWC_BINDING_ID)


def normalize_key(raw: Any) -> str | None:
    """Ключ в нижнем регистре (латиница, цифры, «_» и «-», 1–64 знака) или ``None``."""
    key = str(raw or "").strip().lower()
    return key if KEY_RE.fullmatch(key) else None


# ---- запросы ---------------------------------------------------------------------------------------


async def get_media(conn: Any, tenant_id: str, key: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn, f"select {_COLUMNS} from bot_media where tenant_id = %s and key = %s", (tenant_id, key)
    )


async def list_media(conn: Any, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn, f"select {_COLUMNS} from bot_media where tenant_id = %s order by key", (tenant_id,)
    )


async def save_media(
    conn: Any,
    tenant_id: str,
    key: str,
    kind: str,
    telegram_file_id: str,
    *,
    caption: str | None = None,
    source_note: str | None = None,
    created_by: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Записать файл под ключом: новый ключ — вставка, существующий — замена файла (токен Max сбрасывается: он
    относился к старому файлу). Подпись по умолчанию при замене остаётся прежней, если новая не передана.
    Возвращает (строка, replaced)."""
    key_norm = normalize_key(key)
    if key_norm is None:
        raise ValueError("bot_media_key_invalid")
    if kind not in KINDS:
        raise ValueError(f"bot_media_kind_invalid:{kind}")
    file_id = str(telegram_file_id or "").strip()
    if not file_id:
        raise ValueError("bot_media_file_id_required")
    row = await fetch_one(
        conn,
        f"""
        insert into bot_media (tenant_id, key, kind, telegram_file_id, caption, source_note, created_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (tenant_id, key) do update
           set kind = excluded.kind,
               telegram_file_id = excluded.telegram_file_id,
               max_token = null,
               caption = coalesce(excluded.caption, bot_media.caption),
               source_note = excluded.source_note,
               updated_at = now()
        returning {_COLUMNS}, (xmax <> 0) as replaced
        """,
        (tenant_id, key_norm, kind, file_id, caption, source_note, created_by),
    )
    if row is None:  # insert ... returning всегда возвращает строку; пусто — значит политика RLS отказала
        raise RuntimeError("bot_media_save_failed")
    replaced = bool(row.pop("replaced"))
    return row, replaced


async def delete_media(conn: Any, tenant_id: str, key: str) -> bool:
    row = await fetch_one(
        conn, "delete from bot_media where tenant_id = %s and key = %s returning media_id", (tenant_id, key)
    )
    return row is not None


async def set_max_token(conn: Any, tenant_id: str, key: str, token: str, *, telegram_file_id: str) -> bool:
    """Записать токен Max, если файл с тех пор не заменили (тот же telegram_file_id) и токена ещё нет.
    Дата строки (``updated_at``) не меняется: это не новый файл."""
    row = await fetch_one(
        conn,
        """
        update bot_media set max_token = %s
         where tenant_id = %s and key = %s and telegram_file_id = %s and max_token is null
        returning media_id
        """,
        (token, tenant_id, key, telegram_file_id),
    )
    return row is not None


# ---- отправка --------------------------------------------------------------------------------------


async def _read(conn: Any, tenant_id: str, key: str) -> dict[str, Any] | None:
    if conn is not None:
        return await get_media(conn, tenant_id, key)
    async with tenant_connection(tenant_id) as short:
        return await get_media(short, tenant_id, key)


async def _store_token(conn: Any, tenant_id: str, key: str, token: str, telegram_file_id: str) -> None:
    try:
        if conn is not None:
            await set_max_token(conn, tenant_id, key, token, telegram_file_id=telegram_file_id)
        else:
            async with tenant_connection(tenant_id) as short:
                await set_max_token(short, tenant_id, key, token, telegram_file_id=telegram_file_id)
    except Exception as exc:  # noqa: BLE001 — сообщение всё равно уйдёт; токен запишется при следующей отправке
        logger.warning("bot_media_token_store_failed", extra={"key": key, "error": type(exc).__name__})


async def _ensure_max_token(
    conn: Any, tenant_id: str, row: dict[str, Any], binding: Any
) -> tuple[str | None, str | None]:
    """(токен, None) или (None, причина): missing, no_file, no_telegram_binding, download_failed, upload_failed."""
    key = str(row["key"])
    lock = _upload_locks.setdefault((tenant_id, key), asyncio.Lock())
    async with lock:
        # Пока ждали очередь, другой вызов мог уже загрузить файл или файл заменили — читаем строку заново.
        fresh = await _read(conn, tenant_id, key)
        if fresh is None:
            return None, "missing"
        if fresh.get("max_token"):
            return str(fresh["max_token"]), None
        file_id = str(fresh.get("telegram_file_id") or "")
        if not file_id:
            return None, "no_file"
        bot = await _bot_binding(binding)
        if bot is None:
            logger.warning("bot_media_no_telegram_binding", extra={"key": key})
            return None, "no_telegram_binding"
        try:
            body, filename = await download_telegram_file(file_id, bot_token=bot.bot_token)
        except TelegramFileUnavailable as exc:
            logger.warning("bot_media_download_failed", extra={"key": key, "reason": str(exc)})
            return None, "download_failed"
        except Exception as exc:  # noqa: BLE001 — текст исключения httpx может нести адрес с токеном: пишем только тип
            logger.warning("bot_media_download_failed", extra={"key": key, "error": type(exc).__name__})
            return None, "download_failed"
        try:
            attachment = await upload_max_media(MAX_KIND[str(fresh["kind"])], body, filename=filename)
            token = str(attachment["payload"]["token"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("bot_media_max_upload_failed", extra={"key": key, "error": type(exc).__name__})
            return None, "upload_failed"
        await _store_token(conn, tenant_id, key, token, file_id)
        logger.info("bot_media_max_token_saved", extra={"key": key, "kind": fresh["kind"]})
        return token, None


async def _send_telegram(
    row: dict[str, Any], *, chat_id: int | str, caption: str | None, reply_markup: dict | None, binding: Any
) -> dict[str, Any]:
    key, kind = str(row["key"]), str(row["kind"])
    file_id = str(row.get("telegram_file_id") or "")
    if not file_id:
        return {"ok": False, "status": "no_file", "key": key, "kind": kind}
    bot = await _bot_binding(binding)
    if bot is None:
        logger.warning("bot_media_no_telegram_binding", extra={"key": key})
        return {"ok": False, "status": "no_telegram_binding", "key": key, "kind": kind}
    bot_token = bot.bot_token
    text = (caption or "").strip()
    # Подпись под файлом — если она у такого вида есть и влезает; иначе файл идёт без подписи, а подпись
    # с кнопками — отдельным сообщением следом (кружок подписи не имеет).
    inline = telegram_caption_fits(kind, text)
    follow_up = "" if inline else text
    try:
        result = await send_telegram_media(
            chat_id, kind, file_id, bot_token=bot_token,
            caption=text if inline else None,
            reply_markup=None if follow_up else reply_markup,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_media_telegram_failed", extra={"key": key, "error": type(exc).__name__})
        return {"ok": False, "status": "failed", "key": key, "kind": kind}
    if not result.get("ok"):
        logger.warning("bot_media_telegram_rejected", extra={"key": key, "status_code": result.get("status_code")})
        return {"ok": False, "status": "failed", "key": key, "kind": kind}
    sent = {"ok": True, "status": "sent", "channel": "telegram", "key": key, "kind": kind}
    if follow_up:
        try:
            await send_telegram_text(chat_id=str(chat_id), text=follow_up, bot_token=bot_token, reply_markup=reply_markup)
        except Exception as exc:  # noqa: BLE001 — файл уже у человека
            logger.warning("bot_media_telegram_text_failed", extra={"key": key, "error": type(exc).__name__})
            sent["text_sent"] = False
        else:
            sent["text_sent"] = True
    return sent


async def _send_max(
    conn: Any, tenant_id: str, row: dict[str, Any], *,
    chat_id: int | str | None, user_id: int | str | None, caption: str | None,
    buttons: list[list[dict[str, Any]]] | None, binding: Any,
) -> dict[str, Any]:
    key, kind = str(row["key"]), str(row["kind"])
    if buttons:
        max_inline_keyboard(buttons)  # ошибка кнопок — до загрузки файла, а не после неё
    token = str(row.get("max_token") or "") or None
    if token is None:
        token, why = await _ensure_max_token(conn, tenant_id, row, binding)
        if token is None:
            return {"ok": False, "status": why or "no_file", "key": key, "kind": kind}
    try:
        result = await send_max_attachment(
            user_id=user_id, chat_id=chat_id, kind=MAX_KIND[kind], token=token,
            text=(caption or "").strip(), buttons=buttons,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_media_max_send_failed", extra={"key": key, "error": type(exc).__name__})
        return {"ok": False, "status": "failed", "key": key, "kind": kind}
    if not result.get("ok"):
        logger.warning("bot_media_max_rejected", extra={"key": key, "status_code": result.get("status")})
        return {"ok": False, "status": "failed", "key": key, "kind": kind}
    return {"ok": True, "status": "sent", "channel": "max", "key": key, "kind": kind}


async def send_media(
    conn: Any,
    tenant_id: str,
    key: str,
    *,
    channel: str,
    chat_id: int | str | None = None,
    user_id: int | str | None = None,
    caption: str | None = None,
    buttons: list[list[dict[str, Any]]] | None = None,
    reply_markup: dict | None = None,
    binding: Any = None,
) -> dict[str, Any]:
    """Отправить файл ``key`` в Telegram (``chat_id``) или Max (``chat_id`` или ``user_id``).

    caption — подпись; не передана — берётся ``bot_media.caption``. Кнопки: ``reply_markup`` — inline-клавиатура
    Telegram, ``buttons`` — ряды кнопок Max (``max_button_callback`` / ``max_button_link``); у другого канала они
    игнорируются. ``binding`` — привязка бота (токен для Telegram и для скачивания файла в Max); не передана —
    текущая у Telegram и ``whieda-advisor-bot`` у Max. ``conn`` — открытое подключение или ``None``.

    Ожидаемые отказы не бросают исключение, а возвращают ``{"ok": False, "status": …}``: ``missing`` (ключа нет),
    ``no_file``, ``no_telegram_binding``, ``failed`` (канал отказал), для Max ещё ``download_failed`` и
    ``upload_failed``. Успех — ``{"ok": True, "status": "sent", …}``."""
    if channel not in ("telegram", "max"):
        raise ValueError(f"bot_media_channel_unknown:{channel}")
    key_norm = normalize_key(key)
    row = await _read(conn, tenant_id, key_norm) if key_norm else None
    if row is None:
        return {"ok": False, "status": "missing", "key": key_norm or str(key)}
    text = caption if caption is not None else row.get("caption")
    if channel == "telegram":
        if chat_id is None:
            raise ValueError("bot_media_chat_id_required")
        return await _send_telegram(row, chat_id=chat_id, caption=text, reply_markup=reply_markup, binding=binding)
    if not chat_id and not user_id:
        raise ValueError("bot_media_max_recipient_required")
    return await _send_max(
        conn, tenant_id, row, chat_id=chat_id, user_id=user_id, caption=text, buttons=buttons, binding=binding
    )
