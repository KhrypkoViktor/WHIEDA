"""Parse Telegram updates: /start token, onboarding, advisor routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

START_TOKEN_RE = re.compile(r"^/start(?:@\w+)?(?:\s+(.+))?$", re.I)


@dataclass
class TelegramMessage:
    chat_id: int
    user_id: int
    message_id: int
    text: str
    chat_type: str
    file_id: str | None
    raw: dict[str, Any]
    username: str | None = None
    # Forum groups: the topic the message was posted in (None outside topics).
    thread_id: int | None = None
    is_forum: bool = False
    from_bot: bool = False
    # Voice, video, video note, audio, sticker: only support relays them; file_id
    # stays photo/document, because receipts and the site photo step rely on it.
    media_file_id: str | None = None
    media_kind: str | None = None
    # Only parse_shared_message fills these: people chosen with a «request_users» button and a forwarded
    # contact have no text, and parse_telegram_message keeps dropping them for every other flow.
    users_shared: "SharedUsers | None" = None
    shared_contact: "SharedContact | None" = None

    @property
    def attachment_id(self) -> str | None:
        return self.file_id or self.media_file_id


@dataclass(frozen=True)
class SharedUser:
    """One person chosen in Telegram's own picker (``message.users_shared.users``)."""

    user_id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None


@dataclass(frozen=True)
class SharedUsers:
    request_id: int
    users: tuple[SharedUser, ...]


@dataclass(frozen=True)
class SharedContact:
    """A contact card sent to the bot (``message.contact``); ``user_id`` is None when the person has no Telegram."""

    phone_number: str
    first_name: str = ""
    last_name: str = ""
    user_id: int | None = None


# Order matters: a GIF arrives as both «animation» and «document» — the document wins above.
_RELAY_ONLY_MEDIA = ("voice", "video_note", "video", "audio", "animation", "sticker")


@dataclass
class TelegramCallbackQuery:
    chat_id: int
    user_id: int
    callback_query_id: str
    data: str
    chat_type: str
    raw: dict[str, Any]
    thread_id: int | None = None


def parse_telegram_callback(update: dict[str, Any]) -> TelegramCallbackQuery | None:
    callback = (update or {}).get("callback_query") or {}
    data = str(callback.get("data") or "").strip()
    callback_id = str(callback.get("id") or "").strip()
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user = callback.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not data or not callback_id or chat_id is None or user_id is None:
        return None
    return TelegramCallbackQuery(
        chat_id=int(chat_id),
        user_id=int(user_id),
        callback_query_id=callback_id,
        data=data,
        chat_type=str(chat.get("type") or "private"),
        raw=update,
        thread_id=_thread_id(message),
    )


def _thread_id(message: dict[str, Any]) -> int | None:
    """Topic id of a forum message; Telegram sets it only for topic messages."""
    if not message.get("is_topic_message"):
        return None
    value = message.get("message_thread_id")
    return int(value) if value is not None else None


def parse_telegram_message(update: dict[str, Any]) -> TelegramMessage | None:
    message = (update or {}).get("message") or {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    message_id = message.get("message_id")
    photos = message.get("photo") or []
    document = message.get("document") or {}
    file_id = ""
    if isinstance(photos, list) and photos:
        file_id = str((photos[-1] or {}).get("file_id") or "").strip()
    if not file_id and isinstance(document, dict):
        file_id = str(document.get("file_id") or "").strip()
    media_file_id, media_kind = "", None
    if not file_id:
        for kind in _RELAY_ONLY_MEDIA:
            item = message.get(kind)
            if isinstance(item, dict) and str(item.get("file_id") or "").strip():
                media_file_id, media_kind = str(item["file_id"]).strip(), kind
                break
    if (not text and not file_id and not media_file_id) or chat_id is None or user_id is None:
        return None
    username = str(user.get("username") or "").strip() or None
    return TelegramMessage(
        chat_id=int(chat_id),
        user_id=int(user_id),
        message_id=int(message_id or 0),
        text=text,
        chat_type=str(chat.get("type") or "private"),
        file_id=file_id or None,
        raw=update,
        username=username,
        thread_id=_thread_id(message),
        is_forum=bool(chat.get("is_forum")),
        from_bot=bool(user.get("is_bot")),
        media_file_id=media_file_id or None,
        media_kind=media_kind,
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _parse_users_shared(raw: Any) -> SharedUsers | None:
    """``users`` (Bot API 7.2+: id, name, @name) or the older ``user_ids`` (ids only). None — nobody usable."""
    if not isinstance(raw, dict):
        return None
    request_id = _int_or_none(raw.get("request_id"))
    if request_id is None:
        return None
    users: list[SharedUser] = []
    items = raw.get("users")
    if isinstance(items, list):
        for item in items:
            user_id = _int_or_none(item.get("user_id")) if isinstance(item, dict) else None
            if user_id is not None:
                users.append(SharedUser(
                    user_id=user_id,
                    first_name=_text(item.get("first_name")),
                    last_name=_text(item.get("last_name")),
                    username=_text(item.get("username")) or None,
                ))
    elif isinstance(raw.get("user_ids"), list):
        users = [SharedUser(user_id=user_id) for user_id in map(_int_or_none, raw["user_ids"]) if user_id is not None]
    return SharedUsers(request_id=request_id, users=tuple(users)) if users else None


def _parse_contact(raw: Any) -> SharedContact | None:
    if not isinstance(raw, dict):
        return None
    phone = _text(raw.get("phone_number"))
    if not phone:
        return None
    return SharedContact(
        phone_number=phone,
        first_name=_text(raw.get("first_name")),
        last_name=_text(raw.get("last_name")),
        user_id=_int_or_none(raw.get("user_id")),
    )


def parse_shared_message(update: dict[str, Any]) -> TelegramMessage | None:
    """A message with no text that carries people for «Добавить из Telegram»: ``users_shared`` (the picker
    behind the «👥 Выбрать людей» button) or ``contact`` (a contact forwarded to the bot).

    ``parse_telegram_message`` returns None for these, and every flow that uses it still gets None: only the
    CRM handler asks this function. Text, a caption or a file make it None — those messages go the usual way.
    """
    message = (update or {}).get("message") or {}
    if not isinstance(message, dict) or str(message.get("text") or message.get("caption") or "").strip():
        return None
    if message.get("photo") or message.get("document"):
        return None
    shared = _parse_users_shared(message.get("users_shared"))
    contact = _parse_contact(message.get("contact"))
    if shared is None and contact is None:
        return None
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id, user_id = chat.get("id"), user.get("id")
    if chat_id is None or user_id is None:
        return None
    return TelegramMessage(
        chat_id=int(chat_id),
        user_id=int(user_id),
        message_id=int(message.get("message_id") or 0),
        text="",
        chat_type=str(chat.get("type") or "private"),
        file_id=None,
        raw=update,
        username=str(user.get("username") or "").strip() or None,
        thread_id=_thread_id(message),
        is_forum=bool(chat.get("is_forum")),
        from_bot=bool(user.get("is_bot")),
        users_shared=shared,
        shared_contact=contact,
    )


def should_process_telegram_message(msg: TelegramMessage, bot_username: str | None) -> bool:
    """Keep group chats quiet unless the user explicitly addresses this bot."""
    if msg.chat_type == "private":
        return True
    if msg.chat_type not in {"group", "supergroup"}:
        return False

    username = (bot_username or "").lstrip("@").strip().lower()
    if not username:
        return False

    if re.search(rf"(?<!\w)@{re.escape(username)}\b", msg.text, re.I):
        return True

    reply_from = ((msg.raw.get("message") or {}).get("reply_to_message") or {}).get("from") or {}
    return str(reply_from.get("username") or "").lstrip("@").lower() == username


def is_start_command(text: str) -> bool:
    return bool(START_TOKEN_RE.match(str(text or "").strip()))


def parse_start_token(text: str) -> str | None:
    match = START_TOKEN_RE.match(text.strip())
    if not match:
        return None
    token = (match.group(1) or "").strip()
    return token or None
