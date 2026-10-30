"""Команды владельца для встреч в Telegram-боте WWC (V43, 30.10.2026). Только владелец, только личный чат.

  встречи                                       — ближайшие 10: ``#id · дата время МСК · название · ссылка есть/нет``;
  встреча 05.11 19:30 [https://ссылка] [название] — создать живую встречу (год — ближайший будущий;
                                                  название по умолчанию «Живая встреча»);
  встречи создать 4                             — 4 ближайших слота расписания (вторник и четверг 19:30 МСК),
                                                  уже созданные пропускаются;
  ссылка встречи 12 https://…                   — задать или заменить ссылку;
  отменить встречу 12                           — отменить;
  встреча прошла 12                             — отметить прошедшей.

Всё остальное — не команда: у не-владельца, в группе и у владельца с обычной фразой («встречи в клубе
по вторникам?») обработчик молча возвращает None, текст идёт дальше по цепочке (помощник «события»).
Опознанная команда с неверным форматом получает подсказку формата.

Ссылку входа бот никому не повторяет: ни в ответе, ни в журнале (пишутся только id встречи и действие).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.db import tenant_connection
from app.meetings import service
from app.meetings.service import MeetingError
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.telegram.meetings")

LIST_LIMIT = 10
SLOTS_MAX = 10

USAGE_CREATE = "Не понял. Формат: встреча 05.11 19:30 [https://ссылка] [название]"
USAGE_TIME = "Такой даты или времени нет. Формат: встреча 05.11 19:30 [https://ссылка] [название]"
USAGE_LINK = "Не похоже на ссылку: нужна https://… без пробелов."
USAGE_SCHEME = "Ссылка должна начинаться с https://, например: встреча 05.11 19:30 https://zoom.us/j/1 Название"
USAGE_SLOTS = f"Формат: встречи создать 4 (число от 1 до {SLOTS_MAX})"
USAGE_URL = "Формат: ссылка встречи 12 https://…"
USAGE_CANCEL = "Формат: отменить встречу 12"
USAGE_DONE = "Формат: встреча прошла 12"
USAGE_ALL = (
    "Команды для встреч:\n"
    "встречи\n"
    "встреча 05.11 19:30 [https://ссылка] [название]\n"
    "встречи создать 4\n"
    "ссылка встречи 12 https://…\n"
    "отменить встречу 12\n"
    "встреча прошла 12"
)

_I = re.IGNORECASE
_LIST_RE = re.compile(r"встречи", _I)
_SLOTS_RE = re.compile(r"встречи\s+создать(?:\s+(?P<rest>.*))?", _I)
_URL_CMD_RE = re.compile(r"ссылка\s+встречи(?:\s+(?P<rest>.*))?", _I)
_CANCEL_RE = re.compile(r"отменить\s+встречу(?:\s+(?P<rest>.*))?", _I)
_DONE_RE = re.compile(r"встреча\s+прошла(?:\s+(?P<rest>.*))?", _I)
_CREATE_RE = re.compile(r"встреча\s+(?P<rest>\d.*)", _I)
_BARE_RE = re.compile(r"встреча", _I)
_ID_RE = re.compile(r"#?(\d{1,9})")
_ID_PREFIX_RE = re.compile(r"#?\d")
_WHEN_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})(?:\s+(?P<tail>.*))?")
_COUNT_RE = re.compile(r"\d{1,3}")
# Слово без схемы, похожее на адрес («zoom.us/j/1»): со «/» или с точкой между двумя буквенными частями.
# Названием оно не становится: название встречи публично, а владелец хотел дать ссылку.
_ADDRESS_LIKE_RE = re.compile(r"[^\W\d_]{2,}\.[^\W\d_]{2,}")


@dataclass(frozen=True)
class MeetingCommand:
    action: str  # list | create | slots | set_url | cancel | done | usage
    meeting_id: int | None = None
    day: int = 0
    month: int = 0
    hour: int = 0
    minute: int = 0
    join_url: str | None = None
    title: str | None = None
    count: int = 0
    hint: str = ""


def _usage(hint: str) -> MeetingCommand:
    return MeetingCommand("usage", hint=hint)


def _starts_with_id(rest: str | None) -> bool:
    """Фраза «отменить встречу …» — команда, только если дальше номер (или ничего): иначе это обычный текст."""
    return rest is None or bool(_ID_PREFIX_RE.match(rest))


def _parse_create(rest: str) -> MeetingCommand:
    when = _WHEN_RE.fullmatch(rest)
    if not when:
        return _usage(USAGE_CREATE)
    day, month, hour, minute = (int(when.group(i)) for i in range(1, 5))
    url: str | None = None
    words: list[str] = []
    for word in (when.group("tail") or "").split():
        if word.lower().startswith(("http://", "https://")):
            normalized = service.normalize_join_url(word) if url is None else None
            if normalized is None:
                return _usage(USAGE_LINK)
            url = normalized
        elif "/" in word or _ADDRESS_LIKE_RE.search(word):
            return _usage(USAGE_SCHEME)
        else:
            words.append(word)
    return MeetingCommand(
        "create", day=day, month=month, hour=hour, minute=minute, join_url=url, title=" ".join(words) or None
    )


def parse_command(text: str | None) -> MeetingCommand | None:
    """Слово владельца → команда; ``None`` — это не про встречи (текст идёт дальше по цепочке)."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if _LIST_RE.fullmatch(raw):
        return MeetingCommand("list")
    if _BARE_RE.fullmatch(raw):
        return _usage(USAGE_ALL)

    match = _SLOTS_RE.fullmatch(raw)
    if match:
        rest = match.group("rest")
        if rest is None or not _COUNT_RE.fullmatch(rest) or not 1 <= int(rest) <= SLOTS_MAX:
            return _usage(USAGE_SLOTS)
        return MeetingCommand("slots", count=int(rest))

    match = _URL_CMD_RE.fullmatch(raw)
    if match and _starts_with_id(match.group("rest")):
        parts = (match.group("rest") or "").split()
        id_match = _ID_RE.fullmatch(parts[0]) if parts else None
        if id_match is None or len(parts) != 2:
            return _usage(USAGE_URL)
        url = service.normalize_join_url(parts[1])
        if url is None:
            return _usage(USAGE_LINK)
        return MeetingCommand("set_url", meeting_id=int(id_match.group(1)), join_url=url)

    for pattern, action, hint in ((_CANCEL_RE, "cancel", USAGE_CANCEL), (_DONE_RE, "done", USAGE_DONE)):
        match = pattern.fullmatch(raw)
        if match and _starts_with_id(match.group("rest")):
            id_match = _ID_RE.fullmatch(match.group("rest") or "")
            if id_match is None:
                return _usage(hint)
            return MeetingCommand(action, meeting_id=int(id_match.group(1)))

    match = _CREATE_RE.fullmatch(raw)
    if match:
        return _parse_create(match.group("rest"))
    return None


def owner_allowed(user_id: int) -> bool:
    """Владелец — PLATFORM_BILLING_OWNER_TELEGRAM_ID (как в referral_admin)."""
    owner_id = get_settings().platform_billing_owner_telegram_id
    return owner_id is not None and int(user_id) == int(owner_id)


async def _deliver(chat_id: int, text: str) -> None:
    await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token)


# ---- тексты ----------------------------------------------------------------------------------------


def _title(row: dict[str, Any]) -> str:
    return str(row.get("title") or "").strip() or service.DEFAULT_TITLES.get(str(row.get("kind")), "")


def _when(row: dict[str, Any], now: dt.datetime | None) -> str:
    return service.format_msk(row["starts_at"], now=now)


def _link_word(row: dict[str, Any]) -> str:
    return "ссылка есть" if service.has_link(row) else "ссылка нет"


def line_text(row: dict[str, Any], now: dt.datetime | None = None) -> str:
    return f"#{row['meeting_id']} · {_when(row, now)} · {_title(row)} · {_link_word(row)}"


def list_text(rows: list[dict[str, Any]], now: dt.datetime | None = None) -> str:
    if not rows:
        return "Ближайших встреч нет. Создать: встреча 05.11 19:30 или встречи создать 4"
    return "\n".join(["Ближайшие встречи:", *(line_text(row, now) for row in rows)])


def created_text(row: dict[str, Any], now: dt.datetime | None = None) -> str:
    text = f"Создана встреча {line_text(row, now)}"
    if not service.has_link(row):
        text += f"\nСсылку добавьте: ссылка встречи {row['meeting_id']} https://…"
    return text


def duplicate_text(row: dict[str, Any], now: dt.datetime | None = None) -> str:
    return f"На это время уже есть встреча {line_text(row, now)}. Новую не создавал."


def _short(row: dict[str, Any], now: dt.datetime | None) -> str:
    return f"#{row['meeting_id']} {service.format_msk(row['starts_at'], now=now).removesuffix(' МСК')}"


def slots_text(
    created: list[dict[str, Any]], skipped: list[dict[str, Any]], now: dt.datetime | None = None
) -> str:
    if not created:
        return "Новых нет: ближайшие слоты уже созданы — " + " · ".join(_short(r, now) for r in skipped) + " (МСК)."
    lines = [f"Создано {len(created)}: " + " · ".join(_short(r, now) for r in created) + " (МСК)."]
    if skipped:
        lines.append("Уже были, пропустил: " + " · ".join(_short(r, now) for r in skipped) + ".")
    else:
        lines.append("Ссылки добавьте: ссылка встречи <номер> https://…")
    return "\n".join(lines)


def error_text(exc: MeetingError, command: MeetingCommand) -> str:
    number = command.meeting_id
    if exc.code == "not_found":
        return f"Встречи #{number} нет. Список: встречи"
    if exc.code == "bad_url":
        return USAGE_LINK
    if exc.code == "bad_status" and exc.meeting is not None:
        status = str(exc.meeting.get("status"))
        if command.action == "set_url":
            return f"Встреча #{number} {'отменена' if status == 'cancelled' else 'уже прошла'} — ссылку не меняю."
        if command.action == "cancel":
            return f"Встреча #{number} уже отменена." if status == "cancelled" else (
                f"Встреча #{number} уже прошла — отменить нельзя."
            )
        if command.action == "done":
            return f"Встреча #{number} отменена — отметить прошедшей нельзя." if status == "cancelled" else (
                f"Встреча #{number} уже отмечена как прошедшая."
            )
    return "Не получилось выполнить команду. Список встреч: встречи"


# ---- обработчик ------------------------------------------------------------------------------------


async def try_handle_meetings_admin_message(
    tenant: TenantContext,
    msg: TelegramMessage,
    *,
    trace_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any] | None:
    # Сначала — кто пишет и где: у не-владельца и в группе такого слова нет, текст идёт дальше без ответа.
    if msg.chat_type != "private" or not owner_allowed(msg.user_id):
        return None
    command = parse_command(msg.text)
    if command is None:
        return None
    base: dict[str, Any] = {"ok": True, "route": "meetings_admin", "action": command.action, "trace_id": trace_id}
    if command.action == "usage":
        await _deliver(msg.chat_id, command.hint)
        return {**base, "ok": False, "status": "invalid_command"}

    moment = now or service.utcnow()
    wrote = False
    try:
        # Время разбираем до обращения к базе: дата 31.02 не должна открывать транзакцию.
        starts_at = None
        if command.action == "create":
            starts_at = service.resolve_msk_datetime(
                command.day, command.month, command.hour, command.minute, now=moment
            )
        tenant_id = tenant.tenant_id
        author = f"telegram:{msg.user_id}"
        async with tenant_connection(tenant_id) as conn:
            if command.action == "list":
                rows = await service.list_upcoming(conn, tenant_id, None, LIST_LIMIT, now=moment)
                text, status = list_text(rows, moment), "listed"
            elif command.action == "create":
                row = await service.create_meeting(
                    conn,
                    tenant_id,
                    starts_at=starts_at,
                    kind="live",
                    title=command.title,
                    join_url=command.join_url,
                    created_by=author,
                )
                wrote = True
                text, status = created_text(row, moment), "created"
                base["meeting_id"] = int(row["meeting_id"])
            elif command.action == "slots":
                created, skipped = await service.create_default_slots(
                    conn, tenant_id, command.count, now=moment, created_by=author
                )
                wrote = bool(created)
                text, status = slots_text(created, skipped, moment), "slots_created" if created else "slots_exist"
                base["created"] = [int(r["meeting_id"]) for r in created]
            elif command.action == "set_url":
                row = await service.set_join_url(conn, tenant_id, int(command.meeting_id or 0), str(command.join_url))
                wrote = True
                text = f"Ссылка встречи #{row['meeting_id']} сохранена ({_when(row, moment)})."
                status = "url_set"
            elif command.action == "cancel":
                row = await service.cancel_meeting(conn, tenant_id, int(command.meeting_id or 0))
                wrote = True
                text = f"Встреча #{row['meeting_id']} отменена ({_when(row, moment)})."
                status = "cancelled"
            else:  # done
                row = await service.mark_done(conn, tenant_id, int(command.meeting_id or 0))
                wrote = True
                text = f"Встреча #{row['meeting_id']} отмечена как прошедшая."
                status = "done"
            if command.meeting_id is not None:
                base["meeting_id"] = int(command.meeting_id)
    except MeetingError as exc:
        if exc.code == "duplicate" and exc.meeting is not None:
            await _deliver(msg.chat_id, duplicate_text(exc.meeting, moment))
            return {**base, "status": "duplicate", "meeting_id": int(exc.meeting["meeting_id"])}
        if exc.code == "bad_time":
            await _deliver(msg.chat_id, USAGE_TIME)
            return {**base, "ok": False, "status": "invalid_time"}
        await _deliver(msg.chat_id, error_text(exc, command))
        return {**base, "ok": False, "status": exc.code}
    if wrote:
        # Транзакция закрыта (commit): публичный список перечитает базу.
        service.forget_public_meetings()
    logger.info("meetings admin: action=%s status=%s meeting_id=%s", command.action, status, base.get("meeting_id"))
    await _deliver(msg.chat_id, text)
    return {**base, "status": status}
