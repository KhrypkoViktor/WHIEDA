"""Гость встречи: кто он, что ему показать (срез 4 бот-воронки, V46, 30.10.2026).

Гость — человек, который записался на Живую встречу (есть заявка ``kind = meeting``, открытая им в боте, или
запись в ``meeting_registrations``) и не партнёр (нет ``referral_profiles``). Голый «/start» и слово «меню»
показывают ему меню гостя вместо кабинета партнёра: от кого он пришёл, ближайшие встречи или его запись,
«Карта системы», «Личный разбор», «Задать вопрос».

Здесь только то, что не зависит от мессенджера:
  * ``resolve_guest`` — определить гостя;
  * ``Reply`` и сборка экранов (текст и ряды кнопок ``(подпись, callback_data)``), ``rows_to_telegram`` и
    ``rows_to_max`` превращают ряды в клавиатуру канала;
  * ``owner_card`` — партнёр владельца заявки для строки «Вас пригласил партнёр: …».

Telegram и Max подключают это в ``app/funnel/guest_bot.py``. Тексты — ``app/funnel/texts.py``.

Бот под три типа людей (PLATFORM_BOT_STAGES, V49, 04.11.2026): гость — любой, у кого статус ``guest`` (``app/bot_stages``):
пришёл по метке ``src_``, по голому «/start», по ссылке партнёра или заявки. Заявка на встречу и запись больше не нужны;
экран тот же, подписи кнопок и первая строка — из раздела «три типа людей» в ``texts.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.bot_stages.service import STAGE_GUEST, has_academy_access, inviter_actor_id, resolve_stage, stages_enabled
from app.db import fetch_one
from app.funnel import texts
from app.leads.meeting_leads import MeetingLead, load_lead_by_channel_user
from app.max.client import max_button_callback, max_button_link
from app.meetings import registrations
from app.meetings.registrations import Registration
from app.meetings.service import format_msk, utcnow
from app.referral_bonus.service import InviterCard, inviter_card
from app.settings import get_settings

logger = logging.getLogger("whieda.funnel.guest")

Button = tuple[str, str]  # (подпись, callback_data) или (подпись, https://адрес) — кнопка-ссылка
Rows = list[list[Button]]
ActiveRegistration = tuple[Registration, dict[str, Any]]  # (запись, её встреча)

# «меню» и /menu: гостю — меню гостя, остальным слово идёт дальше по цепочке как раньше.
GUEST_MENU_WORD_RE = re.compile(r"^\s*(?:/menu(?:@\w+)?|меню)\s*[!.]?\s*$", re.IGNORECASE)

# Сколько встреч показывать кнопками: в меню гостя и на старте по ссылке заявки — две, в списке под туром — три.
MENU_MEETINGS = 2
LIST_MEETINGS = 3


def is_guest_menu_word(text: str | None) -> bool:
    return bool(GUEST_MENU_WORD_RE.match(str(text or "")))


def _stages() -> bool:
    """Три меню включены: подписи кнопок и первая строка гостя — новые (меню гостя бывает только в боте WWC)."""
    return stages_enabled()


@dataclass(frozen=True)
class Reply:
    """Экран бота: текст и ряды кнопок. Канал сам решает, как их отправить."""

    text: str
    rows: Rows = field(default_factory=list)


def is_link(data: str) -> bool:
    """Вторая часть кнопки — адрес (``https://…``), а не callback_data: кнопка-ссылка. Наши callback_data
    (``mreg:``, ``mjoin:``, ``funnel:`` …) с адреса никогда не начинаются."""
    return str(data).lower().startswith(("https://", "http://"))


def rows_to_telegram(rows: Rows) -> dict[str, Any] | None:
    if not rows:
        return None
    return {
        "inline_keyboard": [
            [{"text": text, "url": data} if is_link(data) else {"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def rows_to_max(rows: Rows) -> list[list[dict[str, Any]]] | None:
    if not rows:
        return None
    return [[max_button_link(text, data) if is_link(data) else max_button_callback(text, data) for text, data in row] for row in rows]


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
        if _stages():
            label = texts.STAGE_REGISTERED_BUTTON if meeting_id in registered_ids else texts.STAGE_REGISTER_BUTTON
        else:
            label = texts.GUEST_REGISTERED_BUTTON if meeting_id in registered_ids else texts.GUEST_REGISTER_BUTTON
        rows.append([(label.format(when=short_when(meeting["starts_at"], now=now)), f"{texts.REGISTER_CALLBACK_PREFIX}{meeting_id}")])
    return rows


def ask_rows() -> Rows:
    """Одна кнопка «Задать вопрос» (под «встреч пока нет», ТЗ 28, 3.2)."""
    label = texts.STAGE_ASK_BUTTON if _stages() else texts.GUEST_ASK_BUTTON
    return [[(label, texts.FUNNEL_ASK_CALLBACK)]]


def consult_rows() -> Rows:
    """Одна кнопка «Личный разбор» (только своей сети: вызывающий решает, показывать ли)."""
    label = texts.STAGE_CONSULT_BUTTON if _stages() else texts.FUNNEL_CONSULT_BUTTON
    return [[(label, texts.FUNNEL_CONSULT_CALLBACK)]]


def control_rows(registration_id: int) -> Rows:
    """«Перенести» и «Отменить запись» под записью."""
    return [
        [
            (texts.GUEST_MOVE_BUTTON, f"{texts.MOVE_CALLBACK_PREFIX}{int(registration_id)}"),
            (texts.GUEST_CANCEL_BUTTON, f"{texts.CANCEL_CALLBACK_PREFIX}{int(registration_id)}"),
        ]
    ]


# Метки источника /start (bot_start_sources.tag), с которых человек пришёл «задать вопрос» из статьи или со страницы сайта:
# src_art-<статья> и src_site-<страница> (ТЗ 28, 1.3). Метка длиннее 32 знаков в базу попадает как «unknown» (start_sources.py).
ASK_SOURCE_PREFIXES = ("art-", "site-")


def is_ask_source(tag: str | None) -> bool:
    return str(tag or "").strip().lower().startswith(ASK_SOURCE_PREFIXES)


def menu_rows(
    *, academy: Button | None = None, own_network: bool = True, can_book: bool = False, ask_first: bool = False
) -> Rows:
    """Кнопки приветствия гостя (ТЗ 28, 1.1–1.3), каждая в своём ряду: «Карта системы», «Записаться на встречу» (только
    когда есть даты, ``can_book``), «Личный разбор» (только своей сети), «Задать вопрос»; ``ask_first`` (приветствие из
    поиска, 1.3) — «Задать вопрос» первой и без «Личного разбора». Доступ в Академию — ещё «🎓 Академия» перед «Задать вопрос».
    Последний ряд — «Я уже партнёр WHIEDA» (``funnel:partner``). Значки — только те, что уже были у этих кнопок.
    Гость чужого партнёра (``own_network=False``, ``app/funnel/network.py``): ни записи, ни «Личного разбора»."""
    stages = _stages()
    tour = (texts.STAGE_TOUR_BUTTON if stages else texts.GUEST_TOUR_BUTTON, texts.FUNNEL_TOUR_CALLBACK)
    book = (texts.STAGE_BOOK_BUTTON if stages else texts.GUEST_BOOK_BUTTON, texts.FUNNEL_MEET_CALLBACK)
    consult = (texts.STAGE_CONSULT_BUTTON if stages else texts.FUNNEL_CONSULT_BUTTON, texts.FUNNEL_CONSULT_CALLBACK)
    ask = (texts.STAGE_ASK_BUTTON if stages else texts.GUEST_ASK_BUTTON, texts.FUNNEL_ASK_CALLBACK)
    rows: Rows = []
    if ask_first:
        rows.append([ask])
    rows.append([tour])
    if can_book and own_network:
        rows.append([book])
    if own_network and not ask_first:
        rows.append([consult])
    if academy is not None and stages:
        rows.append([academy])
    if not ask_first:
        rows.append([ask])
    rows.append([(texts.GUEST_ALREADY_PARTNER_BUTTON, texts.FUNNEL_PARTNER_CALLBACK)])
    return rows


# ---- экраны ----------------------------------------------------------------------------------------


def greeting_text(inviter_name: str | None, *, own_network: bool = True, start_tag: str | None = None) -> str:
    """Приветствие гостя по источнику (ТЗ 28, 1.1–1.3): из поиска (``src_art-…``, ``src_site-…``) — 1.3, с первой фразой
    1.1, если есть партнёр; иначе по ссылке партнёра — 1.1; иначе реклама и голый старт — 1.2. Гость чужого партнёра
    (``own_network=False``): без слов про Живую встречу — встреч ему не предлагают."""
    if is_ask_source(start_tag):
        body = texts.GREETING_ASK if own_network else texts.GREETING_ASK_PLAIN
        return f"{texts.GREETING_ASK_PARTNER_LEAD.format(name=inviter_name)} {body}" if inviter_name else body
    if inviter_name:
        return (texts.GREETING_PARTNER if own_network else texts.GREETING_PARTNER_PLAIN).format(name=inviter_name)
    return texts.GREETING_AD


def guest_menu_reply(
    inviter_name: str | None,
    *,
    registration: ActiveRegistration | None,
    open_meetings: list[dict[str, Any]],
    now: datetime | None = None,
    note: str | None = None,
    academy: Button | None = None,
    own_network: bool = True,
    start_tag: str | None = None,
) -> Reply:
    """Приветствие гостя и его кнопки. Есть активная запись — вместо приветствия её дата (с первой фразой «Вас пригласил …»,
    если есть партнёр) и кнопки «Перенести» / «Отменить запись»; нет — приветствие по источнику (``greeting_text``) и
    «Записаться на встречу», если есть даты (кнопка ведёт к списку дат); дат нет — строка, что встреч пока нет.
    ``note`` — строка под приветствием (например, что встреча из заявки уже прошла). Гость чужого партнёра
    (``own_network=False``): ни записи, ни «Личного разбора»; вместо них «Ваш партнёр на связи» и «Задать вопрос»;
    запись, сделанную раньше, показываем как есть."""
    parts: list[str] = []
    rows: Rows = []
    if registration is not None:
        reg, meeting = registration
        if inviter_name:
            parts.append(texts.GREETING_ASK_PARTNER_LEAD.format(name=inviter_name))
        parts.append(texts.REGISTRATION_CONFIRMED.format(when=format_msk(meeting["starts_at"], now=now)))
        if note:
            parts.append(note)
        rows.extend(control_rows(reg.registration_id))
    else:
        parts.append(greeting_text(inviter_name, own_network=own_network, start_tag=start_tag))
        if note:
            parts.append(note)
        if not own_network:
            parts.append(texts.GUEST_PARTNER_WILL_CONTACT)
        elif not open_meetings:
            parts.append(texts.GUEST_NO_MEETINGS)
    rows.extend(
        menu_rows(
            academy=academy,
            own_network=own_network,
            can_book=registration is None and bool(open_meetings),
            ask_first=registration is None and is_ask_source(start_tag),
        )
    )
    return Reply("\n\n".join(parts), rows)


async def load_start_tag(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> str | None:
    """Последняя метка источника человека (``bot_start_sources.tag``: ``vk``, ``art-<статья>``, ``unknown`` …); нет строки
    или сбой запроса — ``None`` (приветствие 1.2). Запрос идёт под своей точкой отката: сбой не обрывает транзакцию меню."""
    try:
        async with conn.transaction():
            row = await fetch_one(
                conn,
                "select tag from bot_start_sources where tenant_id = %s and channel = %s and channel_user_id = %s "
                "order by start_id desc limit 1",
                (tenant_id, channel, str(int(channel_user_id))),
            )
    except Exception as exc:  # noqa: BLE001 — приветствие важнее метки
        logger.warning("guest_start_tag_failed", extra={"channel": channel, "error_class": type(exc).__name__})
        return None
    return str(row["tag"]) if row and row.get("tag") else None


async def load_guest_view(
    conn: Any, tenant_id: str, channel: str, channel_user_id: int, *, now: datetime | None = None, own_network: bool = True
) -> tuple[ActiveRegistration | None, list[dict[str, Any]]]:
    """Что показать гостю: ближайшая активная запись (тогда список встреч не нужен) либо две ближайшие встречи.
    Гость чужого партнёра (``own_network=False``): ближайших встреч не читаем — показываем только его прежнюю запись."""
    current = now or utcnow()
    active = await registrations.active_registrations(
        conn, tenant_id, channel=channel, channel_user_id=channel_user_id, limit=1, now=current
    )
    if active:
        return active[0], []
    if not own_network:
        return None, []
    return None, await registrations.open_meetings(conn, tenant_id, limit=MENU_MEETINGS, now=current)


async def academy_button(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> Button | None:
    """Кнопка «🎓 Академия» для меню гостя — только если три меню включены, Академия открыта и у человека есть любой
    доступ в ней (``has_academy_access``, один запрос). В Telegram — вход в Академию бота, в Max — ссылка на Академию на
    сайте. Нет доступа, Академия закрыта или сбой запроса — ``None``: меню гостя без кнопки. Запрос идёт под своей
    точкой отката: сбой не обрывает общую транзакцию меню."""
    if not stages_enabled(tenant_id):
        return None
    from app.academy.service import academy_open

    if not academy_open():
        return None
    try:
        async with conn.transaction():
            has_access = await has_academy_access(conn, tenant_id, channel, channel_user_id)
    except Exception as exc:  # noqa: BLE001 — меню важнее кнопки
        logger.warning("guest_academy_check_failed", extra={"channel": channel, "error_class": type(exc).__name__})
        return None
    if not has_access:
        return None
    if channel == "max":
        from app.bot_stages.menus import academy_url

        return (texts.STAGE_ACADEMY_BUTTON, academy_url())
    from app.telegram.academy import ACADEMY_HOME_CALLBACK

    return (texts.STAGE_ACADEMY_BUTTON, ACADEMY_HOME_CALLBACK)


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
    # Партнёр, чья ссылка закрепила человека в боте (src_<метка>_<код>, ref_<код>), когда заявки на встречу нет.
    attributed_owner_id: str | None = None

    @property
    def owner_actor_id(self) -> str | None:
        owner = self.lead.assigned_owner_id if self.lead else None
        return owner or self.attributed_owner_id


async def is_partner(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> bool:
    row = await fetch_one(
        conn,
        _PARTNER_SQL,
        {"tenant": tenant_id, "channel": channel, "user": int(channel_user_id), "user_text": str(int(channel_user_id))},
    )
    return row is not None


async def resolve_guest(
    conn: Any, tenant_id: str, channel: str, channel_user_id: int, *, stage: str | None = None
) -> GuestContext | None:
    """Гость или None. Не гость: нет ни заявки на встречу, открытой этим человеком, ни записи на встречу — либо
    он партнёр (есть включённый ``referral_profiles``): партнёру и всем остальным бот отвечает как прежде.

    Три меню включены (PLATFORM_BOT_STAGES): гость — любой со статусом ``guest`` (``app/bot_stages``), заявка и запись
    не нужны; заявка, если она есть, даёт имя партнёра и телефон, иначе партнёр — тот, чья ссылка закрепила человека.
    ``stage`` — статус, уже прочитанный вызывающим (чтобы не читать его второй раз).

    ``conn`` — открытое ``tenant_connection`` того же арендатора. Сбой запроса — исключение: вызывающий
    не должен ломать из-за него обычный ответ бота."""
    if stages_enabled(tenant_id):
        if (stage or await resolve_stage(conn, tenant_id, channel, channel_user_id)) != STAGE_GUEST:
            return None
        lead = await load_lead_by_channel_user(conn, tenant_id, channel, channel_user_id)
        owner = lead.assigned_owner_id if lead else None
        attributed = None if owner else await inviter_actor_id(conn, tenant_id, channel, channel_user_id)
        return GuestContext(channel=channel, channel_user_id=int(channel_user_id), lead=lead, attributed_owner_id=attributed)
    lead = await load_lead_by_channel_user(conn, tenant_id, channel, channel_user_id)
    if lead is None and not await registrations.person_has_registration(
        conn, tenant_id, channel=channel, channel_user_id=channel_user_id
    ):
        return None
    if await is_partner(conn, tenant_id, channel, channel_user_id):
        return None
    return GuestContext(channel=channel, channel_user_id=int(channel_user_id), lead=lead)
