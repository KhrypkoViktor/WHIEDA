"""Bot messages of WWC CRM: the 09:00 digest with names and the meeting reminder.

Pure: rows in, ``{"text", "reply_markup"}`` out. The texts carry names and phones,
so they live only in platform_outbox.payload and in Telegram — never in logs.
Buttons hold plain site links; the worker turns them into sign-in links
(``with_site_login``) at the moment of sending, so no login token is stored.

Names come from the partner and from site leads (anyone can type one), and the
bot sends HTML: ``<`` and ``>`` become ‹ › here, or a name like «<b>» would make
Telegram refuse the whole message.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.crm.rules import STEP_TITLES, group_step

DIGEST_MAX_LINES = 10
DIGEST_BUTTON_CONTACTS = 3
NAME_MAX = 60
BUTTON_NAME_MAX = 40

OPEN_TODAY_LABEL = "📋 Открыть «Сегодня»"
OPEN_CARD_LABEL = "👤 Открыть карточку"
DIGEST_TITLE = "Доброе утро! Сегодня в WWC CRM:"
GROUP_HEADERS: dict[str, str] = {
    "meetings": "📅 Встречи сегодня",
    "call": "📞 Позвонить",
    "remind": "🔔 Напомнить",
}


def plain(value: Any, limit: int = NAME_MAX) -> str:
    """One line, no HTML brackets, at most ``limit`` characters."""
    text = " ".join(str(value or "").split()).replace("<", "‹").replace(">", "›")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _overdue_suffix(row: dict[str, Any], today: date) -> str:
    next_at = row.get("next_at")
    if isinstance(next_at, date) and next_at < today:
        return f" (с {next_at.strftime('%d.%m')})"
    return ""


def _line(key: str, row: dict[str, Any], today: date) -> str:
    name = plain(row.get("name"))
    if key == "meetings":
        when = str(row.get("meeting_local") or "").strip()
        return f"• {when} {name}" if when else f"• {name}"
    if key == "call":
        title = STEP_TITLES[group_step(row.get("next_step"))]
        return f"• {name} — {title[0].lower()}{title[1:]}{_overdue_suffix(row, today)}"
    return f"• {name}{_overdue_suffix(row, today)}"


def digest_message(
    sections: list[dict[str, Any]],
    today: date,
    *,
    today_url: str,
    contact_url: Callable[[str], str],
) -> dict[str, Any] | None:
    """Sections from ``rules.today_sections(..., split_overdue=False)``.

    At most DIGEST_MAX_LINES names across the groups, the rest is counted;
    buttons: «Открыть Сегодня» and the first three people of the message.
    None — nothing today, no message.
    """
    blocks: list[str] = []
    shown: list[dict[str, Any]] = []
    hidden = 0
    for section in sections:
        key = section["key"]
        if key not in GROUP_HEADERS:
            continue
        members = list(section.get("contacts") or [])
        room = DIGEST_MAX_LINES - len(shown)
        visible, rest = members[:room], members[room:]
        hidden += len(rest)
        if visible:
            blocks.append("\n".join([GROUP_HEADERS[key], *(_line(key, row, today) for row in visible)]))
            shown.extend(visible)
    if not shown and not hidden:
        return None
    text = DIGEST_TITLE + "\n\n" + "\n\n".join(blocks)
    if hidden:
        text += f"\n\n…и ещё {hidden} — в приложении."
    keyboard = [[{"text": OPEN_TODAY_LABEL, "url": today_url}]]
    for row in shown[:DIGEST_BUTTON_CONTACTS]:
        keyboard.append([{"text": "👤 " + plain(row.get("name"), BUTTON_NAME_MAX), "url": contact_url(str(row["contact_id"]))}])
    return {"text": text, "reply_markup": {"inline_keyboard": keyboard}}


def meeting_reminder_message(*, name: Any, phone: Any, local_time: str, card_url: str) -> dict[str, Any]:
    """«Через час встреча: <имя>, <телефон>» + «Открыть карточку»."""
    who = plain(name)
    phone_text = plain(phone, 64)
    if phone_text:
        who += f", {phone_text}"
    text = f"⏰ Через час встреча: {who}\nНачало в {local_time} по вашему времени."
    return {"text": text, "reply_markup": {"inline_keyboard": [[{"text": OPEN_CARD_LABEL, "url": card_url}]]}}
