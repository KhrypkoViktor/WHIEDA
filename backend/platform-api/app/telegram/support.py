"""Services catalogue and the support tunnel in the bot.

A subscriber opens «Сервисы», picks a Gemini offer (or «Поддержка»), confirms —
and a ticket opens between them and the service administrator. From then on
the bot relays messages both ways; neither side sees the other's contact.

How the administrator answers so the bot knows whom to send to — two modes:

* Forum group (owner, 15.09.2026): the owner registers a Telegram group with
  topics by sending «/forum» in it; the bot must be an administrator there with
  «Manage topics». Every ticket then gets its own topic «#S-7 · Gemini …»; the
  administrator simply writes inside the topic, no Reply needed. Closing the
  ticket closes the topic.
* Private chat (fallback when no forum is registered): every relayed message
  lands in the admin chat with a «Клиент WWC · Заявка #S-1042» header; the
  admin uses Telegram's *Reply* on it, and the bot maps the reply back to the
  ticket. A plain message without a Reply goes to the only open ticket.

The administrator never sees the person's name, username or a link — only the
ticket number.

The administrator is PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID: the owner on staging,
the real administrator in production (both environments share one database,
so the id must not live in a table).

Second kind of ticket — «site» (owner, 26.09.2026): the menu button «Поддержка»,
«/support» and the word «поддержка» open a ticket that goes to the owner
(PLATFORM_BILLING_OWNER_TELEGRAM_ID), not to the Gemini administrator. Its forum
is registered with «/forum site» (support_forums.kind); there the owner *does*
see who writes: the topic is «#S-12 · Имя Фамилия (@username) · ref», the first
message carries the name, a link to the person and the partner's site. Without
a «site» forum the fallback is the owner's private chat, header with the name.
Partners never see each other: one topic per ticket, answers only into it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.referral_bonus.service import ensure_telegram_actor
from app.renewal_requests.service import get_open_renewal_request
from app.settings import get_settings
from app.site_requests.service import get_open_site_request
from app.support.service import (
    CHANNEL_SITE,
    FORUM_KIND_SERVICES,
    FORUM_KIND_SITE,
    attach_forum_topic,
    close_ticket,
    find_ticket_by_admin_message,
    find_ticket_by_forum_thread,
    forum_kind_for_channel,
    get_forum,
    get_message_by_delivery,
    get_message_by_source,
    get_open_ticket_for_user,
    get_ticket,
    list_open_tickets_for_admin,
    list_ticket_messages,
    list_user_burst,
    move_message_to_ticket,
    open_or_reuse_ticket,
    partner_site_for_telegram_user,
    record_relayed_message,
    register_forum,
    ticket_label,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import (
    TelegramDeliveryError,
    answer_callback_query,
    close_forum_topic,
    copy_telegram_message,
    create_forum_topic,
    edit_forum_topic,
    send_telegram_text,
    set_message_reaction,
)
from app.telegram.log_safe import chat_ref
from app.telegram.service_sales import (
    SERVICE_COMMAND_TOKENS,
    ensure_service_topics,
    is_reports_topic,
    is_service_command,
    paid_button,
    try_handle_service_command,
    try_handle_service_sale_callback,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

CHANNEL_GEMINI = "gemini"
_SERVICES_RE = re.compile(r"^(?:сервисы|/services|gemini|джемини)$", re.IGNORECASE)
# The menu button / command «Поддержка» (app/telegram/navigation.py) and the bare word.
_SUPPORT_RE = re.compile(r"^(?:поддержка|/support(?:@\w+)?)$", re.IGNORECASE)
_CALLBACK_RE = re.compile(r"^svc:(order|confirm|cancel|support|close|card|how|move):([A-Za-z0-9_-]+)$")
_USERNAME_IN_DISPLAY = re.compile(r"@([A-Za-z0-9_]{4,32})")
# Ответ (Reply) на сообщение клиента в теме Gemini этими словами переносит его
# в поддержку WWC (владелец, 27.09.2026: кнопок под сообщениями не нужно).
_MOVE_WORDS = {"в поддержку", "поддержка", "в поддержку wwc", "сайт"}
# Owner (or the administrator) sends this inside the forum group once:
# «/forum» — the Gemini («services») group, «/forum site» — the owner's «site» group.
_FORUM_REGISTER_RE = re.compile(r"^/forum(?:@\w+)?(?:\s+(site|services))?$", re.IGNORECASE)

# The cabinet button «Поддержка» (app/telegram/referral_bonus.py) opens a «site» ticket.
SUPPORT_SITE_CALLBACK = f"svc:support:{CHANNEL_SITE}"

# Owner's wording, 26.09.2026 — verbatim.
SUPPORT_INVITE_TEXT = (
    "Напишите, что изменить на вашем сайте — контакты (WhatsApp, MAX, ВК, Одноклассники, Instagram), "
    "фото, текст о себе — или задайте вопрос. Всё, что пришлёте сюда, попадёт к команде WWC; "
    "ответ придёт в этот чат."
)
# Owner commands the support admin may also use on staging; never relayed.
_OWNER_COMMAND_TOKENS = {
    "оплата", "/pay", "статус", "/status", "/due", "бонусы", "/bonuses", "реферер", "/referrer",
    "корректировка-бонусов", "/bonus-adjust", "цена", "/price",
} | SERVICE_COMMAND_TOKENS


@dataclass(frozen=True)
class Offer:
    code: str
    button: str
    title: str
    price_text: str
    card: str


OFFERS: dict[str, Offer] = {
    "gemini_18m": Offer(
        code="gemini_18m",
        button="Gemini Pro 4 490 ₽",
        title="Gemini Pro, лицензия на 18 месяцев",
        price_text="4 490 ₽",
        card=(
            "Gemini тариф Pro\n"
            "Лицензия на 18 месяцев\n"
            "Гарантия 1 месяц. Если лицензия «слетит», производим переподключение за свой счёт.\n"
            "В целом, подобные лицензии работают спокойно без сбоев у наших клиентов с начала 2026 года.\n\n"
            "Стоимость подключения — 4 490 ₽"
        ),
    ),
    # Owner, 15.09.2026: the 1-year licence with a full-term guarantee is not
    # available now; the second offer is 6 months for 3 990 ₽.
    "gemini_6m": Offer(
        code="gemini_6m",
        button="Gemini Pro 3 990 ₽",
        title="Gemini Pro, лицензия на 6 месяцев",
        price_text="3 990 ₽",
        card=(
            "Gemini тариф Pro\n"
            "Лицензия на 6 месяцев\n"
            "Гарантия на весь срок подписки. Если лицензия «слетит», производим переподключение за свой счёт.\n"
            "В целом, подобные лицензии работают спокойно без сбоев у наших клиентов с начала 2026 года.\n\n"
            "Стоимость подключения — 3 990 ₽"
        ),
    ),
}

SERVICES_TEXT = (
    "Gemini Pro\n"
    "В подписку входит нейросеть Gemini Pro + Nanobanana (генерация картинок) + "
    "VEO3 (видео) + 2 TB Google Drive (облачное хранилище).\n\n"
    "Тариф Pro, лицензия на 18 месяцев — 4 490 ₽, гарантия 1 месяц.\n"
    "Тариф Pro, лицензия на 6 месяцев — 3 990 ₽, гарантия на весь срок подписки.\n\n"
    "Выберите вариант или напишите администратору."
)


HOW_IT_WORKS_TEXT = "\n".join(
    [
        "Как работает связь с администратором сервиса",
        "",
        "1. Выбираете вариант и нажимаете «Заказать» — или «Поддержка», если есть вопрос. Открывается заявка с номером, например #S-7.",
        "2. Пока заявка открыта, всё, что вы пишете боту обычным текстом (и фото), уходит администратору сервиса. Команды и кнопки меню работают как обычно.",
        "3. Ответы приходят сюда же: «Ответ администратора по заявке #S-7».",
        "4. Администратор не видит ваше имя и контакты — только номер заявки. Вы не видите его. Всё общение идёт через бота.",
        "5. Оплату и подключение администратор согласует с вами в заявке.",
        "6. Когда вопрос решён, администратор закрывает заявку — вам придёт сообщение. Обычный чат с ботом снова работает как раньше.",
        "7. Новый вопрос позже — «Сервисы» → «Поддержка»: откроется новая заявка.",
    ]
)


def services_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": OFFERS["gemini_18m"].button, "callback_data": "svc:order:gemini_18m"}],
            [{"text": OFFERS["gemini_6m"].button, "callback_data": "svc:order:gemini_6m"}],
            [{"text": "Поддержка", "callback_data": f"svc:support:{CHANNEL_GEMINI}"}],
            [{"text": "Как это работает?", "callback_data": f"svc:how:{CHANNEL_GEMINI}"}],
        ]
    }


# The cabinet button «Подключить Gemini Pro» (app/telegram/referral_bonus.py).
SERVICES_CARD_CALLBACK = f"svc:card:{CHANNEL_GEMINI}"


def services_enabled() -> bool:
    """Owner signed «сервисы» off for production on 15.09.2026 (AGENTS.md):
    the tunnel works on every profile as long as an administrator is configured."""
    return True


def is_services_request(text: str) -> bool:
    return services_enabled() and bool(_SERVICES_RE.fullmatch(str(text or "").strip()))


def is_services_start_token(token: str) -> bool:
    """`/start gemini` from the site button «Подключить Gemini Pro» opens the card."""
    return services_enabled() and str(token or "").strip().lower() in {"gemini", "services"}


def support_admin_id() -> int | None:
    value = get_settings().platform_support_admin_telegram_id
    return int(value) if value else None


def is_support_admin(user_id: int) -> bool:
    admin = support_admin_id()
    return admin is not None and int(user_id) == admin


def is_support_request(text: str) -> bool:
    """«Поддержка» from the menu, «/support», the bare word — a «site» ticket."""
    return bool(_SUPPORT_RE.fullmatch(str(text or "").strip()))


def owner_id() -> int | None:
    value = get_settings().platform_billing_owner_telegram_id
    return int(value) if value else None


def _is_owner(user_id: int) -> bool:
    owner = owner_id()
    return owner is not None and int(user_id) == owner


def _admin_for_kind(kind: str) -> int | None:
    """«site» tickets go to the owner; «services» (Gemini) to the administrator."""
    return owner_id() if kind == FORUM_KIND_SITE else support_admin_id()


def _ticket_kind(ticket: dict[str, Any]) -> str:
    return forum_kind_for_channel(ticket.get("channel_code"))


def _is_site(ticket: dict[str, Any]) -> bool:
    return _ticket_kind(ticket) == FORUM_KIND_SITE


def _is_ticket_admin(ticket: dict[str, Any] | None, user_id: int) -> bool:
    """The person a ticket is addressed to (owner for «site», administrator for
    Gemini); the configured administrator keeps the right on every ticket."""
    if ticket is not None and ticket.get("admin_telegram_user_id") is not None and int(user_id) == int(ticket["admin_telegram_user_id"]):
        return True
    return is_support_admin(user_id)


async def _site_card(tenant: TenantContext, ticket: dict[str, Any]) -> dict[str, Any] | None:
    """The partner's site for a «site» ticket header; None for Gemini tickets."""
    if not _is_site(ticket):
        return None
    return await partner_site_for_telegram_user(tenant.tenant_id, telegram_user_id=int(ticket["user_telegram_user_id"]))


def _partner_line(ticket: dict[str, Any]) -> str:
    """Who writes, for the owner: «Имя (@username)», or the name as a tg://user
    link when the person has no username (delivery keeps such links)."""
    display = str(ticket.get("user_display") or "").strip() or f"Telegram {ticket['user_telegram_user_id']}"
    if "(@" in display or display.startswith("@"):
        return f"Партнёр: {display}"
    return f'Партнёр: <a href="tg://user?id={int(ticket["user_telegram_user_id"])}">{display}</a>'


def _direct_url(ticket: dict[str, Any]) -> str:
    """Прямой чат с партнёром (владелец, 27.09.2026: «хочу написать Ольге в
    Telegram напрямую»): t.me/<username>, а без username — tg://user?id=…"""
    match = _USERNAME_IN_DISPLAY.search(str(ticket.get("user_display") or ""))
    if match:
        return f"https://t.me/{match.group(1)}"
    return f"tg://user?id={int(ticket['user_telegram_user_id'])}"


def _site_keyboard(ticket: dict[str, Any], *, direct: bool = True) -> dict[str, Any]:
    """Шапка обращения по сайту: написать партнёру напрямую и закрыть. Без
    «Оплачено» — это кнопка продаж Gemini, в поддержке сайтов она путала."""
    close = {"text": f"Закрыть {ticket_label(ticket)}", "callback_data": f"svc:close:{ticket['ticket_id']}"}
    rows = [[{"text": "✉️ Написать в Telegram", "url": _direct_url(ticket)}]] if direct else []
    return {"inline_keyboard": [*rows, [close]]}


async def _send_site_header(ticket: dict[str, Any], text: str) -> dict[str, Any]:
    try:
        delivered = await _send_to_admin(ticket, text, reply_markup=_site_keyboard(ticket, direct=True))
    except TelegramDeliveryError:
        delivered = {"ok": False}
    if not delivered.get("ok"):
        # BUTTON_USER_PRIVACY_RESTRICTED: человек запретил ссылки на себя по id —
        # шапка уходит без кнопки, имя-ссылка в тексте останется.
        delivered = await _send_to_admin(ticket, text, reply_markup=_site_keyboard(ticket, direct=False))
    return delivered


def _site_header_lines(ticket: dict[str, Any], site: dict[str, Any] | None) -> list[str]:
    return [_client_label(ticket), _partner_line(ticket), f"Сайт: {site['url']}" if site else "Сайт: не найден"]


def is_support_forum_traffic(update: dict[str, Any]) -> bool:
    """Group traffic the webhook must let through to the processor: «/forum»
    registration, any message inside a forum topic, and the «Закрыть» button.
    Everything else in groups stays ignored as before."""
    callback = (update or {}).get("callback_query") or {}
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        data = str(callback.get("data") or "")
        return chat.get("type") == "supergroup" and data.startswith(
            ("svc:close:", "svc:move:", "sale:", "dep:", "site:confirm:", "site:reject:")
        )
    message = (update or {}).get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "supergroup":
        return False
    text = str(message.get("text") or message.get("caption") or "").strip()
    # Operators' commands («перевёл 20000», «баланс», «отчёт», «тариф») count
    # even in the group's General topic, where Telegram sets no thread id.
    return bool(message.get("is_topic_message")) or bool(_FORUM_REGISTER_RE.fullmatch(text)) or is_service_command(text)


def _may_register_forum(user_id: int) -> bool:
    owner = get_settings().platform_billing_owner_telegram_id
    return is_support_admin(user_id) or (owner is not None and int(user_id) == int(owner))


def _display(msg: TelegramMessage | TelegramCallbackQuery) -> str:
    raw = msg.raw or {}
    user = ((raw.get("message") or {}).get("from") or (raw.get("callback_query") or {}).get("from") or {})
    name = " ".join(
        part for part in (str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()) if part
    )
    username = str(user.get("username") or "").strip()
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else f"Telegram {msg.user_id}")


def _client_label(ticket: dict[str, Any]) -> str:
    """What the administrator sees instead of the person: no name, no username,
    no link (owner, 15.09.2026). The real display stays in `support_tickets`.
    «Site» tickets are the owner's own: the label carries the name (26.09.2026)."""
    if _is_site(ticket):
        display = str(ticket.get("user_display") or "").strip() or f"Telegram {ticket['user_telegram_user_id']}"
        # Имя — ссылка в чат с партнёром (27.09.2026); format_telegram_html её сохраняет.
        return f'{ticket_label(ticket)} · <a href="{_direct_url(ticket)}">{display}</a>'
    return f"Клиент WWC · Заявка {ticket_label(ticket)}"


def _reply_to_message_id(msg: TelegramMessage) -> int | None:
    reply = ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}
    value = reply.get("message_id")
    return int(value) if value is not None else None


def _first_token(text: str) -> str:
    return (str(text or "").strip().split() or [""])[0].lower()


async def _send(
    chat_id: int, text: str, *, reply_markup: dict | None = None, thread_id: int | None = None
) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup, message_thread_id=thread_id,
    )


def _in_forum(ticket: dict[str, Any]) -> bool:
    return ticket.get("forum_thread_id") is not None and ticket.get("forum_chat_id") is not None


def _topic_name(ticket: dict[str, Any], *, closed: bool = False, site: dict[str, Any] | None = None) -> str:
    if _is_site(ticket):
        # «#S-12 · Имя Фамилия (@username) · ref» — the owner sees who it is at a glance.
        display = str(ticket.get("user_display") or "").strip() or f"Telegram {ticket['user_telegram_user_id']}"
        ref = str((site or {}).get("ref_code") or "").strip() or "без сайта"
        return ("✅ " if closed else "") + f"{ticket_label(ticket)} · {display} · {ref}"
    what = str(ticket.get("offer_title") or "Вопрос по Gemini")
    return ("✅ " if closed else "") + f"{ticket_label(ticket)} · {what}"


async def _send_to_admin(ticket: dict[str, Any], text: str, *, reply_markup: dict | None = None) -> dict[str, Any]:
    """Into the ticket's topic when the forum is on; otherwise the admin's private chat."""
    if _in_forum(ticket):
        return await _send(int(ticket["forum_chat_id"]), text, reply_markup=reply_markup, thread_id=int(ticket["forum_thread_id"]))
    return await _send(int(ticket["admin_telegram_user_id"]), text, reply_markup=reply_markup)


async def _open_forum_topic(tenant: TenantContext, ticket: dict[str, Any], *, site: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create the ticket's topic if a forum of the ticket's kind is registered for
    this bot. On any failure the ticket stays in private-chat mode, so support
    never stops."""
    if _in_forum(ticket):
        return ticket
    forum = await get_forum(tenant.tenant_id, binding_id=current_bot_binding().binding_id, kind=_ticket_kind(ticket))
    if not forum:
        return ticket
    created = await create_forum_topic(
        chat_id=str(forum["chat_id"]), name=_topic_name(ticket, site=site), bot_token=current_bot_binding().bot_token
    )
    thread_id = created.get("message_thread_id") if created.get("ok") else None
    if not thread_id:
        logger.warning("support_forum_topic_failed", extra={"ticket": ticket_label(ticket)})
        return ticket
    attached = await attach_forum_topic(
        tenant.tenant_id, ticket_id=str(ticket["ticket_id"]), forum_chat_id=int(forum["chat_id"]), forum_thread_id=int(thread_id)
    )
    return {**ticket, **(attached or {})}


def _close_keyboard(ticket: dict[str, Any]) -> dict[str, Any]:
    # «Оплачено» starts the sale record (Gemini, v10); «Закрыть» ends the tunnel.
    close = {"text": f"Закрыть {ticket_label(ticket)}", "callback_data": f"svc:close:{ticket['ticket_id']}"}
    if _is_site(ticket):
        return {"inline_keyboard": [[close]]}
    return {"inline_keyboard": [[paid_button(ticket), close]]}





# ----------------------------------------------------------------------------
# Subscriber side
# ----------------------------------------------------------------------------

async def show_services(chat_id: int, *, trace_id: str) -> dict[str, Any]:
    await _send(chat_id, SERVICES_TEXT, reply_markup=services_keyboard())
    return {"ok": True, "route": "services", "trace_id": trace_id}


async def open_site_support(
    tenant: TenantContext, source: TelegramMessage | TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any]:
    """«Поддержка» (menu, /support, the word, the cabinet button): a «site» ticket
    to the owner — site changes, contacts, photos, any question.

    Только у тенанта с правом ``site_support`` (seed в V16): иначе бот другой
    компании открывал бы обращение к владельцу WWC (ревью 26.09.2026)."""
    if not tenant.entitlements.get("site_support", False):
        await _send(source.chat_id, "Поддержка в этом боте не подключена.")
        return {"ok": False, "route": "support", "status": "feature_disabled", "trace_id": trace_id}
    return await _open_tunnel(tenant, source, offer=None, trace_id=trace_id, channel=CHANNEL_SITE)


def _closed_note(ticket: dict[str, Any]) -> str:
    if _is_site(ticket):
        return f"{ticket_label(ticket)} закрыто; партнёр откроет новое обращение кнопкой «Поддержка», если нужно."
    return f"{ticket_label(ticket)} закрыто; клиент откроет новое обращение через «Сервисы», если нужно."


async def _open_tunnel(
    tenant: TenantContext,
    source: TelegramMessage | TelegramCallbackQuery,
    *,
    offer: Offer | None,
    trace_id: str,
    channel: str = CHANNEL_GEMINI,
) -> dict[str, Any]:
    kind = forum_kind_for_channel(channel)
    route = "support" if kind == FORUM_KIND_SITE else "services"
    admin = _admin_for_kind(kind)
    if admin is None:
        what = "Поддержка" if kind == FORUM_KIND_SITE else "Поддержка сервисов"
        await _send(source.chat_id, f"{what} пока не подключена. Напишите Виктору: @sunraysword.")
        return {"ok": False, "route": route, "status": "no_admin", "trace_id": trace_id}
    ticket = await open_or_reuse_ticket(
        tenant.tenant_id,
        channel_code=channel,
        offer_code=offer.code if offer else None,
        offer_title=offer.title if offer else None,
        user_telegram_user_id=source.user_id,
        user_chat_id=source.chat_id,
        user_display=_display(source),
        admin_telegram_user_id=admin,
    )
    site = await _site_card(tenant, ticket)
    if ticket["created"]:
        ticket = await _open_forum_topic(tenant, ticket, site=site)
    label = ticket_label(ticket)
    client = _client_label(ticket)
    if kind == FORUM_KIND_SITE:
        # The owner sees who writes and which site it is about.
        header = "\n".join(_site_header_lines(ticket, site))
        user_text = f"Обращение {label} открыто.\n\n{SUPPORT_INVITE_TEXT}"
        who = "партнёру"
    elif offer:
        header = f"{client} · Заказ: {offer.title} — {offer.price_text}"
        user_text = (
            f"Заявка {label} принята: {offer.title} — {offer.price_text}.\n"
            "Администратор ответит здесь же, в этом чате. Всё, что вы напишете сюда, уйдёт ему."
        )
        who = "клиенту"
    else:
        header = f"{client} · Вопрос по Gemini"
        user_text = (
            f"Обращение {label} открыто. Напишите вопрос — он уйдёт администратору Gemini, "
            "ответ придёт сюда."
        )
        who = "клиенту"
    if _in_forum(ticket):
        hint = f"Пишите в эту тему — ответ уйдёт {who}."
        delivered_chat = int(ticket["forum_chat_id"])
    else:
        hint = f"Ответьте на это сообщение (Reply) — ответ уйдёт {who if kind == FORUM_KIND_SITE else 'человеку'}."
        delivered_chat = admin
    if kind != FORUM_KIND_SITE:
        hint += "\nВопрос не про Gemini (сайт, платформа)? Ответьте на сообщение клиента словами «в поддержку» — оно уйдёт команде WWC."
    if not ticket["created"]:
        # Заявка уже открыта: вторую шапку с кнопками не шлём — одна строка.
        again = "партнёр снова нажал «Поддержка»" if kind == FORUM_KIND_SITE else (
            f"клиент снова нажал «Заказать»: {offer.title} — {offer.price_text}" if offer else "клиент снова открыл вопрос"
        )
        delivered = await _send_to_admin(ticket, f"{client} · {again}")
    elif kind == FORUM_KIND_SITE:
        delivered = await _send_site_header(ticket, header + "\n\n" + hint)
    else:
        delivered = await _send_to_admin(ticket, header + "\n\n" + hint, reply_markup=_close_keyboard(ticket))
    await record_relayed_message(
        tenant.tenant_id,
        ticket_id=str(ticket["ticket_id"]),
        direction="system",
        text=header,
        delivered_chat_id=delivered_chat,
        delivered_message_id=delivered.get("message_id"),
    )
    await _send(source.chat_id, user_text)
    logger.info(
        "support_ticket_opened",
        extra={"trace_id": trace_id, "ticket": label, "kind": kind, "offer": offer.code if offer else None, "ticket_created": ticket["created"]},
    )
    return {"ok": True, "route": route, "status": "ticket_opened", "kind": kind, "ticket": label, "trace_id": trace_id}


async def try_handle_support_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    if callback.data.startswith(("sale:", "dep:")):
        return await try_handle_service_sale_callback(tenant, callback, trace_id=trace_id)
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    action, arg = match.group(1), match.group(2)
    if not services_enabled() and action in {"order", "confirm", "support"}:
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    if callback.chat_type != "private" and action not in {"close", "move"}:
        return {"ok": True, "route": "services", "status": "private_chat_required", "trace_id": trace_id}

    if action == "card":
        return await show_services(callback.chat_id, trace_id=trace_id)

    if action == "how":
        await _send(callback.chat_id, HOW_IT_WORKS_TEXT, reply_markup=services_keyboard())
        return {"ok": True, "route": "services", "status": "how_it_works", "trace_id": trace_id}

    if action == "order":
        offer = OFFERS.get(arg)
        if not offer:
            await _send(callback.chat_id, "Такого варианта больше нет. Откройте «Сервисы» ещё раз.")
            return {"ok": False, "route": "services", "status": "unknown_offer", "trace_id": trace_id}
        await _send(
            callback.chat_id,
            offer.card + "\n\nОформить заказ?",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Заказать", "callback_data": f"svc:confirm:{offer.code}"},
                    {"text": "Отменить", "callback_data": "svc:cancel:order"},
                ]]
            },
        )
        return {"ok": True, "route": "services", "status": "confirm_requested", "offer": offer.code, "trace_id": trace_id}

    if action == "confirm":
        offer = OFFERS.get(arg)
        if not offer:
            await _send(callback.chat_id, "Такого варианта больше нет. Откройте «Сервисы» ещё раз.")
            return {"ok": False, "route": "services", "status": "unknown_offer", "trace_id": trace_id}
        return await _open_tunnel(tenant, callback, offer=offer, trace_id=trace_id)

    if action == "cancel":
        await _send(callback.chat_id, "Заказ отменён.", reply_markup=services_keyboard())
        return {"ok": True, "route": "services", "status": "cancelled", "trace_id": trace_id}

    if action == "support":
        # «svc:support:site» — the cabinet button, owner's ticket; anything else — Gemini.
        channel = CHANNEL_SITE if arg == CHANNEL_SITE else CHANNEL_GEMINI
        return await _open_tunnel(tenant, callback, offer=None, trace_id=trace_id, channel=channel)

    if action == "move":
        return await _move_to_site_support(tenant, callback, arg, trace_id=trace_id)

    if action == "close":
        existing = await get_ticket(tenant.tenant_id, ticket_id=arg)
        # In the forum any human in the ticket's topic may close it; in private
        # chat only the person the ticket is addressed to (owner or administrator).
        in_topic = existing is not None and _in_forum(existing) and callback.chat_id == int(existing["forum_chat_id"])
        if not in_topic and not _is_ticket_admin(existing, callback.user_id):
            return {"ok": False, "route": "services", "status": "forbidden", "trace_id": trace_id}
        return await _close_ticket_everywhere(tenant, arg, existing, reply_chat=callback.chat_id, trace_id=trace_id)
    return None


async def _move_to_site_support(
    tenant: TenantContext, callback: TelegramCallbackQuery, arg: str, *, trace_id: str
) -> dict[str, Any]:
    """«↪ В поддержку WWC» под сообщением в заявке Gemini (27.09.2026).

    Пока у человека открыта заявка Gemini, всё, что он пишет боту, уходит
    Карине — и вопрос про сайт («в Одноклассниках ссылка не кликабельна»)
    оказался у неё. Кнопка переносит это сообщение и соседние (±3 минуты:
    текст и скриншоты одной пачки) в обращение по сайту к владельцу — новое
    или уже открытое — и сообщает человеку номер. Обращение по сайту
    становится самым свежим, и следующие сообщения человека идут туда же.
    """
    try:
        chat_part, message_part = arg.split("_", 1)
        source_chat, source_message = int(chat_part), int(message_part)
    except ValueError:
        return {"ok": False, "route": "support_move", "status": "bad_argument", "trace_id": trace_id}
    row = await get_message_by_source(tenant.tenant_id, source_chat_id=source_chat, source_message_id=source_message)
    return await _move_row_to_site_support(
        tenant, row, chat_id=callback.chat_id, user_id=callback.user_id, reply_thread=callback.thread_id, trace_id=trace_id
    )


async def _move_row_to_site_support(
    tenant: TenantContext, row: dict[str, Any] | None, *, chat_id: int, user_id: int, reply_thread: int | None, trace_id: str
) -> dict[str, Any]:
    origin = await get_ticket(tenant.tenant_id, ticket_id=str(row["ticket_id"])) if row else None
    if not row or not origin or row.get("direction") not in (None, "user_to_admin"):
        await _send(chat_id, "Не нашёл сообщение клиента. Ответьте (Reply) на строку «Клиент WWC · Заявка …» с его текстом.", thread_id=reply_thread)
        return {"ok": False, "route": "support_move", "status": "not_found", "trace_id": trace_id}
    if _is_site(origin):
        await _send(chat_id, f"Уже в поддержке WWC ({ticket_label(origin)}).", thread_id=reply_thread)
        return {"ok": True, "route": "support_move", "status": "already_site", "trace_id": trace_id}
    in_topic = _in_forum(origin) and chat_id == int(origin["forum_chat_id"])
    if not in_topic and not _is_ticket_admin(origin, user_id) and not _is_owner(user_id):
        return {"ok": False, "route": "support_move", "status": "forbidden", "trace_id": trace_id}
    owner = owner_id()
    if owner is None or not tenant.entitlements.get("site_support", False):
        await _send(chat_id, "Поддержка сайтов в этом боте не подключена.", thread_id=reply_thread)
        return {"ok": False, "route": "support_move", "status": "feature_disabled", "trace_id": trace_id}
    burst = await list_user_burst(tenant.tenant_id, ticket_id=str(origin["ticket_id"]), around=row["created_at"])
    site_ticket = await open_or_reuse_ticket(
        tenant.tenant_id,
        channel_code=CHANNEL_SITE,
        offer_code=None,
        offer_title=None,
        user_telegram_user_id=int(origin["user_telegram_user_id"]),
        user_chat_id=int(origin["user_chat_id"]),
        user_display=str(origin.get("user_display") or ""),
        admin_telegram_user_id=owner,
    )
    site = await _site_card(tenant, site_ticket)
    if site_ticket["created"]:
        site_ticket = await _open_forum_topic(tenant, site_ticket, site=site)
    label = ticket_label(site_ticket)
    in_forum = _in_forum(site_ticket)
    target_chat = int(site_ticket["forum_chat_id"]) if in_forum else int(site_ticket["admin_telegram_user_id"])
    thread = int(site_ticket["forum_thread_id"]) if in_forum else None
    hint = "Пишите в эту тему — ответ уйдёт партнёру." if in_forum else "Ответьте на это сообщение (Reply) — ответ уйдёт партнёру."
    lines = [*_site_header_lines(site_ticket, site), f"↪ Перенесено из заявки Gemini {ticket_label(origin)}.", "", hint]
    delivered = await _send_site_header(site_ticket, "\n".join(lines))
    await record_relayed_message(
        tenant.tenant_id, ticket_id=str(site_ticket["ticket_id"]), direction="system", text=lines[0],
        delivered_chat_id=target_chat, delivered_message_id=delivered.get("message_id"),
    )
    token = current_bot_binding().bot_token
    moved = 0
    for item in burst:
        copied = await copy_telegram_message(
            chat_id=str(target_chat), from_chat_id=str(item["source_chat_id"]),
            message_id=int(item["source_message_id"]), bot_token=token, message_thread_id=thread,
        )
        if not copied.get("ok"):
            body = str(item.get("text") or "").strip() or "(вложение — открыть не удалось)"
            copied = await _send(target_chat, f"{_client_label(site_ticket)}\n{body}", thread_id=thread)
        await move_message_to_ticket(
            tenant.tenant_id, message_id=str(item["message_id"]), ticket_id=str(site_ticket["ticket_id"]),
            delivered_chat_id=target_chat, delivered_message_id=copied.get("message_id"),
        )
        moved += 1
    await _send(
        chat_id,
        f"↪ Перенесено в поддержку WWC ({label}): сообщений — {moved}. Дальше этот вопрос ведёт команда WWC.",
        thread_id=reply_thread,
    )
    await _send(
        int(origin["user_chat_id"]),
        f"Ваш вопрос передан команде WWC — обращение {label}. Вопросы по сайту и платформе пишите сюда, ответ придёт в этот чат.",
    )
    logger.info("support_messages_moved", extra={"trace_id": trace_id, "from": ticket_label(origin), "to": label, "moved": moved})
    return {"ok": True, "route": "support_move", "status": "moved", "from": ticket_label(origin), "to": label, "moved": moved, "trace_id": trace_id}


async def _close_ticket_everywhere(
    tenant: TenantContext, ticket_id: str, existing: dict[str, Any] | None, *, reply_chat: int, trace_id: str
) -> dict[str, Any]:
    ticket = await close_ticket(tenant.tenant_id, ticket_id=ticket_id, closed_by="admin")
    thread = int(existing["forum_thread_id"]) if existing and _in_forum(existing) and reply_chat == int(existing["forum_chat_id"]) else None
    if not ticket:
        await _send(reply_chat, f"{ticket_label(existing) if existing else 'Обращение'} уже закрыто.", thread_id=thread)
        return {"ok": True, "route": "services", "status": "already_closed", "trace_id": trace_id}
    label = ticket_label(ticket)
    reopen = "нажмите «Поддержка» в меню" if _is_site(ticket) else "откройте «Сервисы» и нажмите «Поддержка»"
    await _send(int(ticket["user_chat_id"]), f"Обращение {label} закрыто. Если появятся вопросы — {reopen}.")
    await _send(reply_chat, f"{label} закрыто.", thread_id=thread)
    if _in_forum(ticket):
        token = current_bot_binding().bot_token
        chat, topic = str(ticket["forum_chat_id"]), int(ticket["forum_thread_id"])
        site = await _site_card(tenant, ticket)
        await edit_forum_topic(chat_id=chat, message_thread_id=topic, name=_topic_name(ticket, closed=True, site=site), bot_token=token)
        await close_forum_topic(chat_id=chat, message_thread_id=topic, bot_token=token)
    return {"ok": True, "route": "services", "status": "closed", "ticket": label, "trace_id": trace_id}


async def _relay_user_to_admin(tenant: TenantContext, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    admin = int(ticket["forum_chat_id"]) if _in_forum(ticket) else int(ticket["admin_telegram_user_id"])
    label = ticket_label(ticket)
    header = _client_label(ticket)
    # Кнопки — только в шапке темы, не под каждым сообщением клиента
    # (владелец, 27.09.2026: «очень много лишнего»).
    markup = None
    if msg.file_id:
        # A photo or document: copy it (no forward header, no contact leak), then
        # a text line the admin can Reply to.
        await copy_telegram_message(
            chat_id=str(admin), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
            message_thread_id=int(ticket["forum_thread_id"]) if _in_forum(ticket) else None,
        )
        delivered = await _send_to_admin(ticket, f"{header}\n(вложение выше)" + (f"\n{msg.text}" if msg.text else ""), reply_markup=markup)
    else:
        delivered = await _send_to_admin(ticket, f"{header}\n{msg.text}", reply_markup=markup)
    result = await record_relayed_message(
        tenant.tenant_id,
        ticket_id=str(ticket["ticket_id"]),
        direction="user_to_admin",
        text=msg.text,
        telegram_file_id=msg.file_id,
        source_chat_id=msg.chat_id,
        source_message_id=msg.message_id,
        delivered_chat_id=admin,
        delivered_message_id=delivered.get("message_id"),
    )
    return {"ok": True, "route": "support_relay", "direction": "user_to_admin", "ticket": label, "duplicate": result["duplicate"], "trace_id": trace_id}


async def try_relay_user_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """A subscriber with an open ticket: their message goes to the administrator."""
    if msg.chat_type != "private" or is_support_admin(msg.user_id):
        return None
    if msg.text.startswith("/"):
        return None
    try:
        ticket = await get_open_ticket_for_user(tenant.tenant_id, user_telegram_user_id=msg.user_id)
    except RuntimeError as exc:
        # Routing checks run without a database pool; a live Core always has one
        # before it accepts Telegram updates (same rule as site_requests.py).
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if not ticket:
        return None
    return await _relay_user_to_admin(tenant, msg, ticket, trace_id=trace_id)


# ----------------------------------------------------------------------------
# Administrator side
# ----------------------------------------------------------------------------

async def _relay_admin_to_user(tenant: TenantContext, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    label = ticket_label(ticket)
    user_chat = int(ticket["user_chat_id"])
    prefix = f"Ответ команды WWC по обращению {label}" if _is_site(ticket) else f"Ответ администратора по заявке {label}"
    if msg.file_id:
        await copy_telegram_message(
            chat_id=str(user_chat), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
        )
        delivered = await _send(user_chat, f"{prefix}: вложение выше." + (f"\n{msg.text}" if msg.text else ""))
    else:
        delivered = await _send(user_chat, f"{prefix}:\n{msg.text}")
    await record_relayed_message(
        tenant.tenant_id,
        ticket_id=str(ticket["ticket_id"]),
        direction="admin_to_user",
        text=msg.text,
        telegram_file_id=msg.file_id,
        source_chat_id=msg.chat_id,
        source_message_id=msg.message_id,
        delivered_chat_id=user_chat,
        delivered_message_id=delivered.get("message_id"),
    )
    if _in_forum(ticket) and msg.chat_id == int(ticket["forum_chat_id"]):
        # Inside the topic a reaction is the receipt; a text line would just add noise.
        receipt = await set_message_reaction(
            chat_id=str(msg.chat_id), message_id=msg.message_id, emoji="👍", bot_token=current_bot_binding().bot_token
        )
        if not receipt.get("ok"):
            await _send(msg.chat_id, f"→ отправлено: {_client_label(ticket)}", thread_id=int(ticket["forum_thread_id"]))
    else:
        await _send(msg.chat_id, f"→ отправлено: {_client_label(ticket)}")
    return {"ok": True, "route": "support_relay", "direction": "admin_to_user", "ticket": label, "trace_id": trace_id}


# ----------------------------------------------------------------------------
# Forum group (one topic per ticket)
# ----------------------------------------------------------------------------

async def _move_open_tickets_to_forum(tenant: TenantContext, *, kind: str = FORUM_KIND_SERVICES) -> tuple[int, int]:
    """Tickets of this ``kind`` opened before the group existed get their topics
    now, with the conversation so far replayed, so the administrator continues
    in one place. Only this environment's tickets (its admin id) — the database
    is shared. Returns (moved, failed)."""
    admin = _admin_for_kind(kind)
    if admin is None:
        return 0, 0
    moved = failed = 0
    for ticket in await list_open_tickets_for_admin(tenant.tenant_id, admin_telegram_user_id=admin, limit=50, forum_kind=kind):
        site = await _site_card(tenant, ticket)
        bound = await _open_forum_topic(tenant, ticket, site=site)
        if not _in_forum(bound):
            failed += 1
            continue
        if _is_site(bound):
            lines = [*_site_header_lines(bound, site), ""]
            client_word, who = "Партнёр", "партнёру"
        else:
            what = f"Заказ: {bound['offer_title']}" if bound.get("offer_title") else "Вопрос по Gemini"
            lines = [f"{_client_label(bound)} · {what}", ""]
            client_word, who = "Клиент", "клиенту"
        for item in await list_ticket_messages(tenant.tenant_id, ticket_id=str(bound["ticket_id"])):
            author = client_word if item["direction"] == "user_to_admin" else "Администратор"
            body = str(item.get("text") or "").strip() or ("(вложение)" if item.get("telegram_file_id") else "")
            if body:
                lines.append(f"{author}: {body}")
        lines += ["", f"Пишите в эту тему — ответ уйдёт {who}."]
        if _is_site(bound):
            delivered = await _send_site_header(bound, "\n".join(lines))
        else:
            delivered = await _send_to_admin(bound, "\n".join(lines), reply_markup=_close_keyboard(bound))
        await record_relayed_message(
            tenant.tenant_id, ticket_id=str(bound["ticket_id"]), direction="system", text=lines[0],
            delivered_chat_id=int(bound["forum_chat_id"]), delivered_message_id=delivered.get("message_id"),
        )
        moved += 1
    return moved, failed

async def try_handle_support_forum_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """Runs before the group-quiet filter: «/forum» registers the group, and any
    human text inside a ticket's topic goes to the client."""
    if msg.chat_type != "supergroup" or msg.from_bot:
        return None
    register = _FORUM_REGISTER_RE.fullmatch(msg.text.strip())
    if register:
        kind = (register.group(1) or FORUM_KIND_SERVICES).lower()
        # The «site» group carries partners' names and sites: only the owner binds it.
        allowed = _is_owner(msg.user_id) if kind == FORUM_KIND_SITE else _may_register_forum(msg.user_id)
        if not allowed:
            return {"ok": False, "route": "support_forum", "status": "forbidden", "trace_id": trace_id}
        if not msg.is_forum:
            await _send(msg.chat_id, "В этой группе не включены темы. Включите «Темы» в настройках группы и повторите /forum.")
            return {"ok": False, "route": "support_forum", "status": "not_a_forum", "trace_id": trace_id}
        title = str(((msg.raw.get("message") or {}).get("chat") or {}).get("title") or "")
        await register_forum(
            tenant.tenant_id, binding_id=current_bot_binding().binding_id, chat_id=msg.chat_id,
            title=title, registered_by=msg.user_id, kind=kind,
        )
        if kind == FORUM_KIND_SERVICES:
            await ensure_service_topics(tenant)  # «Бонусы» / «Отчёты» — Gemini sales only
        moved, failed = await _move_open_tickets_to_forum(tenant, kind=kind)
        note = f" Открытые заявки перенесены в темы: {moved}." if moved else ""
        if failed:
            note += (
                f" Не удалось создать темы для {failed} заявок: дайте боту право «Управление темами» "
                f"(Manage topics) в правах администратора и отправьте {msg.text.strip().split('@')[0] if kind == FORUM_KIND_SERVICES else '/forum site'} ещё раз."
            )
        if kind == FORUM_KIND_SITE:
            done = "Группа поддержки сайтов подключена: каждое обращение «Поддержка» будет открываться отдельной темой с именем партнёра."
        else:
            done = "Группа поддержки подключена: каждая новая заявка будет открываться отдельной темой."
        await _send(msg.chat_id, done + note, thread_id=msg.thread_id)
        logger.info("support_forum_registered", extra={"trace_id": trace_id, "chat_id": chat_ref(msg.chat_id), "moved": moved, "kind": kind})
        return {"ok": True, "route": "support_forum", "status": "registered", "kind": kind, "moved": moved, "trace_id": trace_id}
    # Operators' commands («отчёт», «баланс», «перевёл N», «тариф») work in any
    # topic of the group, General included; nothing of that is relayed to a client.
    command_result = await try_handle_service_command(tenant, msg, trace_id=trace_id)
    if command_result is not None:
        return command_result
    if msg.thread_id is None:
        return None
    ticket = await find_ticket_by_forum_thread(tenant.tenant_id, forum_chat_id=msg.chat_id, forum_thread_id=msg.thread_id)
    if ticket is None:
        forum = await get_forum(tenant.tenant_id, binding_id=current_bot_binding().binding_id)
        if is_reports_topic(forum, msg.chat_id, msg.thread_id):
            await _send(msg.chat_id, "Команды: «отчёт», «баланс», «перевёл 20000», «тариф».", thread_id=msg.thread_id)
            return {"ok": True, "route": "service_command", "status": "help", "trace_id": trace_id}
        return await _try_orders_topic_reply(tenant, msg, trace_id=trace_id)
    if ticket["status"] != "open":
        await _send(msg.chat_id, _closed_note(ticket), thread_id=msg.thread_id)
        return {"ok": False, "route": "support_relay", "status": "closed", "trace_id": trace_id}
    if msg.text.strip().lower() in {"закрыть", "/close"}:
        return await _close_ticket_everywhere(tenant, str(ticket["ticket_id"]), ticket, reply_chat=msg.chat_id, trace_id=trace_id)
    reply_to = _reply_to_message_id(msg)
    if not _is_site(ticket) and reply_to is not None and msg.text.strip().lower().rstrip(".!") in _MOVE_WORDS:
        # Вопрос не про Gemini: ответом «в поддержку» — к команде WWC, клиенту не пересылаем.
        row = await get_message_by_delivery(tenant.tenant_id, delivered_chat_id=msg.chat_id, delivered_message_id=reply_to)
        return await _move_row_to_site_support(
            tenant, row, chat_id=msg.chat_id, user_id=msg.user_id, reply_thread=msg.thread_id, trace_id=trace_id
        )
    return await _relay_admin_to_user(tenant, msg, ticket, trace_id=trace_id)


_PARTNER_ID_RE = re.compile(r"\bid (\d{5,15})\b")
_PARTNER_TAG_RE = re.compile(r"— (@[A-Za-z0-9_]{4,32}) · id \d+")


async def _try_orders_topic_reply(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Reply владельца на сообщение бота о заявке в теме «Заявки на сайты» —
    партнёру, через его обращение по сайту: там же вернётся ответ (30.09.2026:
    «Ждём оплату. Вы хотите в клуб?» молча не ушёл Татьяне). Своя заметка в
    теме без Reply партнёру не уходит."""
    forum = await get_forum(tenant.tenant_id, binding_id=current_bot_binding().binding_id, kind=FORUM_KIND_SITE)
    orders_thread = (forum or {}).get("reports_thread_id")
    if not forum or int(forum["chat_id"]) != msg.chat_id or not orders_thread or int(orders_thread) != msg.thread_id:
        return None
    reply = ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}
    # В темах каждое сообщение формально «отвечает» на создание темы — это не ответ.
    if not reply or reply.get("message_id") == msg.thread_id or not (reply.get("from") or {}).get("is_bot"):
        return None
    admin = _admin_for_kind(FORUM_KIND_SITE)
    if admin is None or msg.user_id != admin or not (msg.text.strip() or msg.file_id):
        return None
    source = str(reply.get("text") or reply.get("caption") or "")
    found = _PARTNER_ID_RE.search(source)
    if not found:
        await _send(
            msg.chat_id,
            "Этот ответ никуда не ушёл: в сообщении нет id партнёра. Ответьте на сообщение бота "
            "о шаге заявки (в нём есть id) или напишите в тему обращения партнёра.",
            thread_id=msg.thread_id,
        )
        return {"ok": False, "route": "site_orders_reply", "status": "no_partner", "trace_id": trace_id}
    partner = int(found.group(1))
    tag = _PARTNER_TAG_RE.search(source)
    ticket = await open_or_reuse_ticket(
        tenant.tenant_id,
        channel_code=CHANNEL_SITE,
        offer_code=None,
        offer_title=None,
        user_telegram_user_id=partner,
        user_chat_id=partner,
        user_display=tag.group(1) if tag else f"id {partner}",
        admin_telegram_user_id=admin,
    )
    if ticket["created"]:
        site = await _site_card(tenant, ticket)
        ticket = await _open_forum_topic(tenant, ticket, site=site)
        header = "\n".join(_site_header_lines(ticket, site))
        delivered = await _send_site_header(ticket, header + "\n\nОткрыто ответом из «Заявок на сайты». Пишите в эту тему — ответ уйдёт партнёру.")
        await record_relayed_message(
            tenant.tenant_id, ticket_id=str(ticket["ticket_id"]), direction="system", text=header,
            delivered_chat_id=int(ticket["forum_chat_id"]) if _in_forum(ticket) else admin,
            delivered_message_id=delivered.get("message_id"),
        )
    result = await _relay_admin_to_user(tenant, msg, ticket, trace_id=trace_id)
    label = ticket_label(ticket)
    if _in_forum(ticket):
        # Разговор целиком — в теме обращения: туда же придёт ответ партнёра.
        await _send(
            int(ticket["forum_chat_id"]),
            "Ответ из «Заявок на сайты»:\n" + (msg.text or "вложение"),
            thread_id=int(ticket["forum_thread_id"]),
        )
    await _send(msg.chat_id, f"→ ушло партнёру, обращение {label}: ответ придёт в его тему.", thread_id=msg.thread_id)
    return {**result, "route": "site_orders_reply"}


async def try_handle_support_admin_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """The administrator's message: Reply → that ticket; no Reply → the only open one.

    The owner (not the administrator) answers «site» tickets in fallback mode —
    only by Reply on the ticket's header: the owner's plain text is commands
    («оплата», «безлимит», …) and questions to the advisor, never an implicit
    answer to whichever ticket happens to be open."""
    admin = is_support_admin(msg.user_id)
    if msg.chat_type != "private" or not (admin or _is_owner(msg.user_id)):
        return None
    if msg.text.startswith("/") or _first_token(msg.text) in _OWNER_COMMAND_TOKENS or is_services_request(msg.text) or is_support_request(msg.text):
        return None
    reply_to = _reply_to_message_id(msg)
    if reply_to is not None:
        try:
            ticket = await find_ticket_by_admin_message(tenant.tenant_id, admin_chat_id=msg.chat_id, message_id=reply_to)
        except RuntimeError as exc:
            # Routing checks run without a database pool (same rule as try_relay_user_message).
            if not admin and "database pool is not initialized" in str(exc):
                return None
            raise
        if ticket is None:
            if not admin:
                return None  # the owner replies to all sorts of bot messages; not support traffic
            await _send(msg.chat_id, "Это сообщение не относится к обращению. Ответьте (Reply) на сообщение с номером #S-….")
            return {"ok": False, "route": "support_relay", "status": "unknown_reply", "trace_id": trace_id}
        if ticket["status"] != "open":
            await _send(msg.chat_id, _closed_note(ticket))
            return {"ok": False, "route": "support_relay", "status": "closed", "trace_id": trace_id}
        return await _relay_admin_to_user(tenant, msg, ticket, trace_id=trace_id)
    if not admin:
        return None

    open_tickets = await list_open_tickets_for_admin(tenant.tenant_id, admin_telegram_user_id=msg.user_id)
    if not open_tickets:
        return None  # not support traffic — let the rest of the bot handle it
    if len(open_tickets) == 1:
        return await _relay_admin_to_user(tenant, msg, open_tickets[0], trace_id=trace_id)
    lines = ["Открыто несколько обращений — ответьте через Reply на сообщение нужного:"]
    lines += [
        f"{_client_label(t)}" + (f" · {t['offer_title']}" if t.get("offer_title") else "") for t in open_tickets
    ]
    await _send(msg.chat_id, "\n".join(lines))
    return {"ok": False, "route": "support_relay", "status": "ambiguous", "trace_id": trace_id}


async def try_handle_support_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """Early hook: the services card, the admin's relay, and attachments from a
    subscriber with an open ticket. Text from subscribers is relayed late (see
    processor), so bot commands and menu buttons keep working inside a tunnel."""
    if msg.chat_type != "private":
        return None
    if is_services_request(msg.text):
        return await show_services(msg.chat_id, trace_id=trace_id)
    if is_support_request(msg.text):
        return await open_site_support(tenant, msg, trace_id=trace_id)
    command_result = await try_handle_service_command(tenant, msg, trace_id=trace_id)
    if command_result is not None:
        return command_result
    admin_result = await try_handle_support_admin_message(tenant, msg, trace_id=trace_id)
    if admin_result is not None:
        return admin_result
    if msg.file_id and not is_support_admin(msg.user_id):
        if await _request_waits_for_file(tenant, msg):
            return None  # чек или фото анкеты — заявке (renewal / site_request в processor)
        return await try_relay_user_message(tenant, msg, trace_id=trace_id)
    return None


# Шаги анкеты «Заказать сайт», которые ждут вложение (как SITE_FILE_STEPS).
_SITE_FILE_STEPS = frozenset({"awaiting_photo", "awaiting_payment"})


async def _request_waits_for_file(tenant: TenantContext, msg: TelegramMessage) -> bool:
    """Анкета ждёт фото, заявка на сайт или продление — чек: вложение идёт
    туда, даже если открыто обращение (30.09.2026: чек Татьяны ушёл в #S-11
    без «Подтвердить», оплату никто не увидел)."""
    try:
        actor_id = await ensure_telegram_actor(
            tenant.tenant_id, telegram_user_id=msg.user_id, telegram_chat_id=msg.chat_id, raw_update=msg.raw
        )
        renewal = await get_open_renewal_request(tenant.tenant_id, actor_id)
        if renewal and renewal["status"] == "awaiting_payment":
            return True
        site = await get_open_site_request(tenant.tenant_id, actor_id)
    except RuntimeError as exc:
        if "database pool is not initialized" in str(exc):
            return False
        raise
    return bool(site) and str(site["status"]) in _SITE_FILE_STEPS
