"""Гость встречи: кто он, что ему показать (срез 4 бот-воронки, V46, 30.10.2026).

Гость — человек, который записался на Живую встречу (есть заявка ``kind = meeting``, открытая им в боте, или
запись в ``meeting_registrations``) и не партнёр (нет ``referral_profiles``). Голый «/start» и слово «меню»
показывают ему меню гостя вместо кабинета партнёра: от кого он пришёл, ближайшие встречи или его запись,
«Показ системы», «Личный разбор», «Задать вопрос».

Здесь только то, что не зависит от мессенджера:
  * ``resolve_guest`` — определить гостя;
  * ``Reply`` и сборка экранов (текст и ряды кнопок ``(подпись, callback_data)``), ``rows_to_telegram`` и
    ``rows_to_max`` превращают ряды в клавиатуру канала;
  * ``owner_card`` — партнёр владельца заявки для строки «Вы пришли от …».

Telegram и Max подключают это в ``app/funnel/guest_bot.py``. Тексты — ``app/funnel/texts.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db import fetch_one
from app.funnel import texts
from app.leads.meeting_leads import MeetingLead, load_lead_by_channel_user
from app.max.client import max_button_callback
from app.meetings import registrations
from app.meetings.registrations import Registration
from app.meetings.service import format_msk, utcnow
from app.referral_bonus.service import InviterCard, inviter_card
from app.settings import get_settings

Button = tuple[str, str]  # (подпись, callback_data)
Rows = list[list[Button]]
ActiveRegistration = tuple[Registration, dict[str, Any]]  # (запись, её встреча)

# «меню» и /menu: гостю — меню гостя, остальным слово идёт дальше по цепочке как раньше.
GUEST_MENU_WORD_RE = re.compile(r"^\s*(?:/menu(?:@\w+)?|меню)\s*[!.]?\s*$", re.IGNORECASE)

# Сколько встреч показывать кнопками: в меню гостя и на старте по ссылке заявки — две, в списке под туром — три.
MENU_MEETINGS = 2
LIST_MEETINGS = 3


def is_guest_menu_word(text: str | None) -> bool:
    return bool(GUEST_MENU_WORD_RE.match(str(text or "")))


@dataclass(frozen=True)
class Reply:
    """Экран бота: текст и ряды кнопок. Канал сам решает, как их отправить."""

    text: str
    rows: Rows = field(default_factory=list)


def rows_to_telegram(rows: Rows) -> dict[str, Any] | None:
    if not rows:
        return None
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in rows]}


def rows_to_max(rows: Rows) -> list[list[dict[str, Any]]] | None:
    if not rows:
        return None
    return [[max_button_callback(text, data) for text, data in row] for row in rows]


# ---- кнопки ----------------------------------------------------------------------------------------


def short_when(value: datetime, *, now: datetime | None = None) -> str:
    """«вт 03.11 19:30» — для подписи кнопки (московское время, «МСК» не пишем: кнопка короткая)."""
    return format_msk(value, now=now).removesuffix(" МСК")


def register_rows(meetings: list[dict[str, Any]], registered_ids: frozenset[int] = frozenset(), *, now: datetime | None = None) -> Rows:
    """По кнопке на встречу: «Записаться: вт 03.11 19:30» (``mreg:<встреча>``); на ту, куда человек уже записан,
    — «Вы записаны: …» с тем же callback (ответ — «уже записаны» с кнопками переноса и отмены)."""
    rows: Rows = []
    for meeting in meetings:
        meeting_id = int(meeting["meeting_id"])
        label = texts.GUEST_REGISTERED_BUTTON if meeting_id in registered_ids else texts.GUEST_REGISTER_BUTTON
        rows.append([(label.format(when=short_when(meeting["starts_at"], now=now)), f"{texts.REGISTER_CALLBACK_PREFIX}{meeting_id}")])
    return rows


def control_rows(registration_id: int) -> Rows:
    """«Перенести» и «Отменить запись» под записью."""
    return [
        [
            (texts.GUEST_MOVE_BUTTON, f"{texts.MOVE_CALLBACK_PREFIX}{int(registration_id)}"),
            (texts.GUEST_CANCEL_BUTTON, f"{texts.CANCEL_CALLBACK_PREFIX}{int(registration_id)}"),
        ]
    ]


def menu_rows() -> Rows:
    """«Показ системы», «Личный разбор», «Задать вопрос» — каждая в своём ряду."""
    return [
        [(texts.GUEST_TOUR_BUTTON, texts.FUNNEL_TOUR_CALLBACK)],
        [(texts.FUNNEL_CONSULT_BUTTON, texts.FUNNEL_CONSULT_CALLBACK)],
        [(texts.GUEST_ASK_BUTTON, texts.FUNNEL_ASK_CALLBACK)],
    ]


# ---- экраны ----------------------------------------------------------------------------------------


def guest_header(inviter_name: str | None) -> str:
    return texts.GUEST_FROM_PARTNER.format(name=inviter_name) if inviter_name else texts.GUEST_IN_WWC


def guest_menu_reply(
    inviter_name: str | None,
    *,
    registration: ActiveRegistration | None,
    open_meetings: list[dict[str, Any]],
    now: datetime | None = None,
    note: str | None = None,
) -> Reply:
    """Меню гостя. Есть активная запись — её дата и кнопки «Перенести» / «Отменить запись»; нет — ближайшие
    встречи кнопками «Записаться: …» (или строка, что встреч пока нет). Внизу всегда три кнопки меню.
    ``note`` — строка под приветствием (например, что встреча из заявки уже прошла)."""
    parts = [guest_header(inviter_name)]
    if note:
        parts.append(note)
    rows: Rows = []
    if registration is not None:
        reg, meeting = registration
        parts.append(texts.REGISTRATION_CONFIRMED.format(when=format_msk(meeting["starts_at"], now=now)))
        rows.extend(control_rows(reg.registration_id))
    elif open_meetings:
        parts.append(texts.GUEST_PICK_MEETING)
        rows.extend(register_rows(open_meetings, now=now))
    else:
        parts.append(texts.GUEST_NO_MEETINGS)
    rows.extend(menu_rows())
    return Reply("\n\n".join(parts), rows)


async def load_guest_view(
    conn: Any, tenant_id: str, channel: str, channel_user_id: int, *, now: datetime | None = None
) -> tuple[ActiveRegistration | None, list[dict[str, Any]]]:
    """Что показать гостю: ближайшая активная запись (тогда список встреч не нужен) либо две ближайшие встречи."""
    current = now or utcnow()
    active = await registrations.active_registrations(
        conn, tenant_id, channel=channel, channel_user_id=channel_user_id, limit=1, now=current
    )
    if active:
        return active[0], []
    return None, await registrations.open_meetings(conn, tenant_id, limit=MENU_MEETINGS, now=current)


async def owner_card(tenant_id: str, owner_actor_id: str | None) -> InviterCard | None:
    """Партнёр владельца заявки: имя из профиля. Нет владельца, владелец «по умолчанию» (organic: реклама
    без партнёра) или у владельца нет включённого профиля партнёра — None, и гость видит «Вы в WWC»."""
    if not owner_actor_id or owner_actor_id == get_settings().platform_organic_owner_id:
        return None
    return await inviter_card(tenant_id, owner_actor_id)


# ---- кто гость -------------------------------------------------------------------------------------

# Партнёр человека: включённый профиль у его actor — по id в Telegram, по каналу (Max) или по привязанной
# учётке (account_identities: Max или Google, привязанные к Telegram партнёра).
_PARTNER_SQL = """
select 1 as partner
from referral_profiles rp
where rp.tenant_id = %(tenant)s and rp.enabled
  and rp.owner_id in (
    select actor_id from lead_actors
    where tenant_id = %(tenant)s and %(channel)s = 'telegram' and telegram_user_id = %(user)s
    union
    select actor_id from lead_actor_channels
    where tenant_id = %(tenant)s and channel = %(channel)s and channel_user_id = %(user_text)s
    union
    select la.actor_id from account_identities ai
    join lead_actors la on la.tenant_id = ai.tenant_id and la.telegram_user_id = ai.telegram_user_id
    where ai.tenant_id = %(tenant)s and ai.provider = %(channel)s and ai.external_id = %(user_text)s
      and ai.revoked_at is null
  )
limit 1
"""


@dataclass(frozen=True)
class GuestContext:
    """Гость встречи. ``lead`` — его последняя заявка на встречу; ``None``, если он записался без заявки
    (например, из бота после «Показа системы»)."""

    channel: str
    channel_user_id: int
    lead: MeetingLead | None = None

    @property
    def owner_actor_id(self) -> str | None:
        return self.lead.assigned_owner_id if self.lead else None


async def is_partner(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> bool:
    row = await fetch_one(
        conn,
        _PARTNER_SQL,
        {"tenant": tenant_id, "channel": channel, "user": int(channel_user_id), "user_text": str(int(channel_user_id))},
    )
    return row is not None


async def resolve_guest(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> GuestContext | None:
    """Гость или None. Не гость: нет ни заявки на встречу, открытой этим человеком, ни записи на встречу — либо
    он партнёр (есть включённый ``referral_profiles``): партнёру и всем остальным бот отвечает как прежде.

    ``conn`` — открытое ``tenant_connection`` того же арендатора. Сбой запроса — исключение: вызывающий
    не должен ломать из-за него обычный ответ бота."""
    lead = await load_lead_by_channel_user(conn, tenant_id, channel, channel_user_id)
    if lead is None and not await registrations.person_has_registration(
        conn, tenant_id, channel=channel, channel_user_id=channel_user_id
    ):
        return None
    if await is_partner(conn, tenant_id, channel, channel_user_id):
        return None
    return GuestContext(channel=channel, channel_user_id=int(channel_user_id), lead=lead)
