"""Команды владельца для хранилища медиа бота в Telegram-боте WWC (V45, 30.10.2026). Только владелец, только личный чат.

  файл с подписью «медиа <ключ>» — сохранить: видео, голосовое, кружок, фото, аудио или файл. Вид берётся
                                    из вложения (кружок = video_note, голосовое = voice, сжатое фото = photo,
                                    файл без сжатия = document). Тот же ключ — замена файла;
  медиа                          — список ключей: вид, дата загрузки (МСК), есть ли файл для Max;
  медиа удалить <ключ>           — удалить.

Ключ — латиница, цифры, «_» и «-», до 64 знаков; регистр не важен (хранится строчными).

Всё остальное — не команда: у не-владельца, в группе и у владельца с обычной фразой («медиа план на неделю»)
обработчик молча возвращает None, текст идёт дальше по цепочке. Подпись «медиа …» с файлом, но неверного формата,
получает подсказку. File_id файла в ответах и журнале не показывается.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.bot_media import service
from app.db import tenant_connection
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.telegram.bot_media")

MSK = ZoneInfo("Europe/Moscow")

USAGE_ALL = (
    "Команды для медиа:\n"
    "файл с подписью «медиа ключ» — сохранить (видео, голосовое, кружок, фото)\n"
    "медиа — список\n"
    "медиа удалить ключ\n"
    "Ключ — латиница, цифры, _ и -, до 64 знаков."
)
USAGE_KEY = "Ключ — латиница, цифры, _ и -, до 64 знаков. Пример подписи: медиа system_tour"
USAGE_DELETE = "Формат: медиа удалить ключ"
NO_FILE = "Файла нет. Пришлите видео, голосовое, кружок или фото с подписью «медиа ключ»."
NO_KEY_FOR_FILE = "Нужен ключ. Подпись к файлу: медиа ключ (например, медиа system_tour)."
UNSUPPORTED = "Этот вид вложения не сохраняю. Пришлите видео, голосовое, кружок, фото, аудио или файл."
EMPTY_LIST = "Медиа пока нет. Загрузить: пришлите файл с подписью «медиа ключ»."

# kind, который Telegram присылает полем сообщения; всё остальное (стикер, GIF-анимация) не сохраняем.
_MEDIA_FIELDS = frozenset({"voice", "video_note", "video", "audio"})


@dataclass(frozen=True)
class BotMediaCommand:
    action: str  # list | save | delete | usage
    key: str | None = None
    hint: str = ""


def parse_command(text: str | None, *, has_file: bool = False) -> BotMediaCommand | None:
    """Слово владельца → команда; ``None`` — это не про медиа (текст идёт дальше по цепочке).

    has_file — к сообщению приложен файл: тогда подпись «медиа …» неверного формата получает подсказку,
    а не уходит дальше (без файла такая фраза — обычный текст)."""
    parts = " ".join(str(text or "").split()).split(" ")
    if not parts or parts[0].lower() != "медиа":
        return None
    rest = parts[1:]
    if not rest:
        return BotMediaCommand("usage", hint=NO_KEY_FOR_FILE) if has_file else BotMediaCommand("list")
    if rest[0].lower() == "удалить":
        key = service.normalize_key(rest[1]) if len(rest) == 2 else None
        return BotMediaCommand("delete", key=key) if key else BotMediaCommand("usage", hint=USAGE_DELETE)
    key = service.normalize_key(rest[0]) if len(rest) == 1 else None
    if key:
        return BotMediaCommand("save", key=key)
    return BotMediaCommand("usage", hint=USAGE_KEY) if has_file else None


def attachment_kind(msg: TelegramMessage) -> str | None:
    """Вид вложения для bot_media или None (нет вложения, стикер, GIF-анимация)."""
    if msg.media_kind:
        return msg.media_kind if msg.media_kind in _MEDIA_FIELDS else None
    if msg.file_id:
        photos = ((msg.raw or {}).get("message") or {}).get("photo")
        return "photo" if isinstance(photos, list) and photos else "document"
    return None


def owner_allowed(user_id: int) -> bool:
    """Владелец — PLATFORM_BILLING_OWNER_TELEGRAM_ID (как в referral_admin и meetings_admin)."""
    owner_id = get_settings().platform_billing_owner_telegram_id
    return owner_id is not None and int(user_id) == int(owner_id)


def _bot_id() -> str:
    """Какой бот получил файл: file_id действует только у него (в журнале строки — для разбора, если отправка не пройдёт)."""
    try:
        return current_bot_binding().binding_id
    except RuntimeError:
        return "unknown"


async def _deliver(chat_id: int, text: str) -> None:
    await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token)


# ---- тексты ----------------------------------------------------------------------------------------


def _stamp(value: Any) -> str:
    if not isinstance(value, dt.datetime):
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def list_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return EMPTY_LIST
    lines = ["Медиа бота (ключ · вид · загружено МСК · файл для Max):"]
    for row in rows:
        lines.append(
            f"{row['key']} · {row['kind']} · {_stamp(row.get('updated_at'))} · "
            f"{'есть' if row.get('max_token') else 'нет, загрузится при первой отправке'}"
        )
    return "\n".join(lines)


def saved_text(row: dict[str, Any], replaced: bool) -> str:
    text = f"Сохранено: {row['key']} ({row['kind']})"
    return text + "\nПрежний файл заменён." if replaced else text


# ---- обработчик ------------------------------------------------------------------------------------


async def try_handle_bot_media_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    # Сначала — кто пишет и где: у не-владельца и в группе такого слова нет, сообщение идёт дальше без ответа.
    if msg.chat_type != "private" or not msg.text or not owner_allowed(msg.user_id):
        return None
    command = parse_command(msg.text, has_file=bool(msg.attachment_id))
    if command is None:
        return None
    base: dict[str, Any] = {"ok": True, "route": "bot_media_admin", "action": command.action, "trace_id": trace_id}
    if command.action == "usage":
        await _deliver(msg.chat_id, command.hint or USAGE_ALL)
        return {**base, "ok": False, "status": "invalid_command"}

    tenant_id = tenant.tenant_id
    if command.action == "list":
        async with tenant_connection(tenant_id) as conn:
            rows = await service.list_media(conn, tenant_id)
        await _deliver(msg.chat_id, list_text(rows))
        return {**base, "status": "listed", "count": len(rows)}

    key = str(command.key)
    if command.action == "delete":
        async with tenant_connection(tenant_id) as conn:
            deleted = await service.delete_media(conn, tenant_id, key)
        logger.info("bot media admin: delete key=%s deleted=%s", key, deleted)
        await _deliver(msg.chat_id, f"Удалено: {key}" if deleted else f"Такого ключа нет: {key}. Список: медиа")
        return {**base, "status": "deleted" if deleted else "not_found", "key": key}

    # save
    if not msg.attachment_id:
        await _deliver(msg.chat_id, NO_FILE)
        return {**base, "ok": False, "status": "no_file", "key": key}
    kind = attachment_kind(msg)
    if kind is None:
        await _deliver(msg.chat_id, UNSUPPORTED)
        return {**base, "ok": False, "status": "unsupported", "key": key}
    async with tenant_connection(tenant_id) as conn:
        row, replaced = await service.save_media(
            conn, tenant_id, key, kind, str(msg.attachment_id),
            source_note=f"bot={_bot_id()} telegram:{msg.chat_id}:{msg.message_id}", created_by=f"telegram:{msg.user_id}",
        )
    logger.info("bot media admin: save key=%s kind=%s replaced=%s", key, kind, replaced)
    await _deliver(msg.chat_id, saved_text(row, replaced))
    return {**base, "status": "replaced" if replaced else "saved", "key": key, "kind": kind}
