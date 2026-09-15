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
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.settings import get_settings
from app.support.service import (
    attach_forum_topic,
    close_ticket,
    find_ticket_by_admin_message,
    find_ticket_by_forum_thread,
    get_forum,
    get_open_ticket_for_user,
    get_ticket,
    list_open_tickets_for_admin,
    list_ticket_messages,
    open_or_reuse_ticket,
    record_relayed_message,
    register_forum,
    ticket_label,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import (
    answer_callback_query,
    close_forum_topic,
    copy_telegram_message,
    create_forum_topic,
    edit_forum_topic,
    send_telegram_text,
    set_message_reaction,
)
from app.telegram.log_safe import chat_ref
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

CHANNEL_GEMINI = "gemini"
_SERVICES_RE = re.compile(r"^(?:сервисы|/services|gemini|джемини)$", re.IGNORECASE)
_CALLBACK_RE = re.compile(r"^svc:(order|confirm|cancel|support|close):([A-Za-z0-9_-]+)$")
# Owner (or the administrator) sends this inside the forum group once.
_FORUM_REGISTER_RE = re.compile(r"^/forum(?:@\w+)?$", re.IGNORECASE)
# Owner commands the support admin may also use on staging; never relayed.
_OWNER_COMMAND_TOKENS = {
    "оплата", "/pay", "статус", "/status", "/due", "бонусы", "/bonuses", "реферер", "/referrer",
    "корректировка-бонусов", "/bonus-adjust", "цена", "/price",
}


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


def services_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": OFFERS["gemini_18m"].button, "callback_data": "svc:order:gemini_18m"}],
            [{"text": OFFERS["gemini_6m"].button, "callback_data": "svc:order:gemini_6m"}],
            [{"text": "Поддержка", "callback_data": f"svc:support:{CHANNEL_GEMINI}"}],
        ]
    }


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


def is_support_forum_traffic(update: dict[str, Any]) -> bool:
    """Group traffic the webhook must let through to the processor: «/forum»
    registration, any message inside a forum topic, and the «Закрыть» button.
    Everything else in groups stays ignored as before."""
    callback = (update or {}).get("callback_query") or {}
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        return chat.get("type") == "supergroup" and str(callback.get("data") or "").startswith("svc:close:")
    message = (update or {}).get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "supergroup":
        return False
    text = str(message.get("text") or message.get("caption") or "").strip()
    return bool(message.get("is_topic_message")) or bool(_FORUM_REGISTER_RE.fullmatch(text))


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
    no link (owner, 15.09.2026). The real display stays in `support_tickets`."""
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


def _topic_name(ticket: dict[str, Any], *, closed: bool = False) -> str:
    what = str(ticket.get("offer_title") or "Вопрос по Gemini")
    return ("✅ " if closed else "") + f"{ticket_label(ticket)} · {what}"


async def _send_to_admin(ticket: dict[str, Any], text: str, *, reply_markup: dict | None = None) -> dict[str, Any]:
    """Into the ticket's topic when the forum is on; otherwise the admin's private chat."""
    if _in_forum(ticket):
        return await _send(int(ticket["forum_chat_id"]), text, reply_markup=reply_markup, thread_id=int(ticket["forum_thread_id"]))
    return await _send(int(ticket["admin_telegram_user_id"]), text, reply_markup=reply_markup)


async def _open_forum_topic(tenant: TenantContext, ticket: dict[str, Any]) -> dict[str, Any]:
    """Create the ticket's topic if a forum is registered for this bot. On any
    failure the ticket stays in private-chat mode, so support never stops."""
    if _in_forum(ticket):
        return ticket
    forum = await get_forum(tenant.tenant_id, binding_id=current_bot_binding().binding_id)
    if not forum:
        return ticket
    created = await create_forum_topic(
        chat_id=str(forum["chat_id"]), name=_topic_name(ticket), bot_token=current_bot_binding().bot_token
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
    return {"inline_keyboard": [[{"text": f"Закрыть {ticket_label(ticket)}", "callback_data": f"svc:close:{ticket['ticket_id']}"}]]}


# ----------------------------------------------------------------------------
# Subscriber side
# ----------------------------------------------------------------------------

async def show_services(chat_id: int, *, trace_id: str) -> dict[str, Any]:
    await _send(chat_id, SERVICES_TEXT, reply_markup=services_keyboard())
    return {"ok": True, "route": "services", "trace_id": trace_id}


async def _open_tunnel(
    tenant: TenantContext,
    source: TelegramMessage | TelegramCallbackQuery,
    *,
    offer: Offer | None,
    trace_id: str,
) -> dict[str, Any]:
    admin = support_admin_id()
    if admin is None:
        await _send(source.chat_id, "Поддержка сервисов пока не подключена. Напишите Виктору.")
        return {"ok": False, "route": "services", "status": "no_admin", "trace_id": trace_id}
    ticket = await open_or_reuse_ticket(
        tenant.tenant_id,
        channel_code=CHANNEL_GEMINI,
        offer_code=offer.code if offer else None,
        offer_title=offer.title if offer else None,
        user_telegram_user_id=source.user_id,
        user_chat_id=source.chat_id,
        user_display=_display(source),
        admin_telegram_user_id=admin,
    )
    if ticket["created"]:
        ticket = await _open_forum_topic(tenant, ticket)
    label = ticket_label(ticket)
    client = _client_label(ticket)
    if offer:
        header = f"{client} · Заказ: {offer.title} — {offer.price_text}"
        user_text = (
            f"Заявка {label} принята: {offer.title} — {offer.price_text}.\n"
            "Администратор ответит здесь же, в этом чате. Всё, что вы напишете сюда, уйдёт ему."
        )
    else:
        header = f"{client} · Вопрос по Gemini"
        user_text = (
            f"Обращение {label} открыто. Напишите вопрос — он уйдёт администратору Gemini, "
            "ответ придёт сюда."
        )
    if _in_forum(ticket):
        hint = "Пишите в эту тему — ответ уйдёт клиенту."
        delivered_chat = int(ticket["forum_chat_id"])
    else:
        hint = "Ответьте на это сообщение (Reply) — ответ уйдёт человеку."
        delivered_chat = admin
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
        extra={"trace_id": trace_id, "ticket": label, "offer": offer.code if offer else None, "ticket_created": ticket["created"]},
    )
    return {"ok": True, "route": "services", "status": "ticket_opened", "ticket": label, "trace_id": trace_id}


async def try_handle_support_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    action, arg = match.group(1), match.group(2)
    if not services_enabled() and action in {"order", "confirm", "support"}:
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    if callback.chat_type != "private" and action != "close":
        return {"ok": True, "route": "services", "status": "private_chat_required", "trace_id": trace_id}

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
        return await _open_tunnel(tenant, callback, offer=None, trace_id=trace_id)

    if action == "close":
        existing = await get_ticket(tenant.tenant_id, ticket_id=arg)
        # In the forum any human in the ticket's topic may close it; in private chat only the admin.
        in_topic = existing is not None and _in_forum(existing) and callback.chat_id == int(existing["forum_chat_id"])
        if not in_topic and not is_support_admin(callback.user_id):
            return {"ok": False, "route": "services", "status": "forbidden", "trace_id": trace_id}
        return await _close_ticket_everywhere(tenant, arg, existing, reply_chat=callback.chat_id, trace_id=trace_id)
    return None


async def _close_ticket_everywhere(
    tenant: TenantContext, ticket_id: str, existing: dict[str, Any] | None, *, reply_chat: int, trace_id: str
) -> dict[str, Any]:
    ticket = await close_ticket(tenant.tenant_id, ticket_id=ticket_id, closed_by="admin")
    thread = int(existing["forum_thread_id"]) if existing and _in_forum(existing) and reply_chat == int(existing["forum_chat_id"]) else None
    if not ticket:
        await _send(reply_chat, f"{ticket_label(existing) if existing else 'Обращение'} уже закрыто.", thread_id=thread)
        return {"ok": True, "route": "services", "status": "already_closed", "trace_id": trace_id}
    label = ticket_label(ticket)
    await _send(
        int(ticket["user_chat_id"]),
        f"Обращение {label} закрыто. Если появятся вопросы — откройте «Сервисы» и нажмите «Поддержка».",
    )
    await _send(reply_chat, f"{label} закрыто.", thread_id=thread)
    if _in_forum(ticket):
        token = current_bot_binding().bot_token
        chat, topic = str(ticket["forum_chat_id"]), int(ticket["forum_thread_id"])
        await edit_forum_topic(chat_id=chat, message_thread_id=topic, name=_topic_name(ticket, closed=True), bot_token=token)
        await close_forum_topic(chat_id=chat, message_thread_id=topic, bot_token=token)
    return {"ok": True, "route": "services", "status": "closed", "ticket": label, "trace_id": trace_id}


async def _relay_user_to_admin(tenant: TenantContext, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    admin = int(ticket["forum_chat_id"]) if _in_forum(ticket) else int(ticket["admin_telegram_user_id"])
    label = ticket_label(ticket)
    header = _client_label(ticket)
    if msg.file_id:
        # A photo or document: copy it (no forward header, no contact leak), then
        # a text line the admin can Reply to.
        await copy_telegram_message(
            chat_id=str(admin), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
            message_thread_id=int(ticket["forum_thread_id"]) if _in_forum(ticket) else None,
        )
        delivered = await _send_to_admin(ticket, f"{header}\n(вложение выше)" + (f"\n{msg.text}" if msg.text else ""), reply_markup=_close_keyboard(ticket))
    else:
        delivered = await _send_to_admin(ticket, f"{header}\n{msg.text}", reply_markup=_close_keyboard(ticket))
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
    ticket = await get_open_ticket_for_user(tenant.tenant_id, user_telegram_user_id=msg.user_id)
    if not ticket:
        return None
    return await _relay_user_to_admin(tenant, msg, ticket, trace_id=trace_id)


# ----------------------------------------------------------------------------
# Administrator side
# ----------------------------------------------------------------------------

async def _relay_admin_to_user(tenant: TenantContext, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    label = ticket_label(ticket)
    user_chat = int(ticket["user_chat_id"])
    if msg.file_id:
        await copy_telegram_message(
            chat_id=str(user_chat), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
        )
        delivered = await _send(user_chat, f"Ответ администратора по заявке {label}: вложение выше." + (f"\n{msg.text}" if msg.text else ""))
    else:
        delivered = await _send(user_chat, f"Ответ администратора по заявке {label}:\n{msg.text}")
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

async def _move_open_tickets_to_forum(tenant: TenantContext) -> tuple[int, int]:
    """Tickets opened before the group existed get their topics now, with the
    conversation so far replayed, so the administrator continues in one place.
    Only this environment's tickets (its admin id) — the database is shared.
    Returns (moved, failed)."""
    admin = support_admin_id()
    if admin is None:
        return 0, 0
    moved = failed = 0
    for ticket in await list_open_tickets_for_admin(tenant.tenant_id, admin_telegram_user_id=admin, limit=50):
        bound = await _open_forum_topic(tenant, ticket)
        if not _in_forum(bound):
            failed += 1
            continue
        what = f"Заказ: {bound['offer_title']}" if bound.get("offer_title") else "Вопрос по Gemini"
        lines = [f"{_client_label(bound)} · {what}", ""]
        for item in await list_ticket_messages(tenant.tenant_id, ticket_id=str(bound["ticket_id"])):
            who = "Клиент" if item["direction"] == "user_to_admin" else "Администратор"
            body = str(item.get("text") or "").strip() or ("(вложение)" if item.get("telegram_file_id") else "")
            if body:
                lines.append(f"{who}: {body}")
        lines += ["", "Пишите в эту тему — ответ уйдёт клиенту."]
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
    if _FORUM_REGISTER_RE.fullmatch(msg.text.strip()):
        if not _may_register_forum(msg.user_id):
            return {"ok": False, "route": "support_forum", "status": "forbidden", "trace_id": trace_id}
        if not msg.is_forum:
            await _send(msg.chat_id, "В этой группе не включены темы. Включите «Темы» в настройках группы и повторите /forum.")
            return {"ok": False, "route": "support_forum", "status": "not_a_forum", "trace_id": trace_id}
        title = str(((msg.raw.get("message") or {}).get("chat") or {}).get("title") or "")
        await register_forum(
            tenant.tenant_id, binding_id=current_bot_binding().binding_id, chat_id=msg.chat_id,
            title=title, registered_by=msg.user_id,
        )
        moved, failed = await _move_open_tickets_to_forum(tenant)
        note = f" Открытые заявки перенесены в темы: {moved}." if moved else ""
        if failed:
            note += (
                f" Не удалось создать темы для {failed} заявок: дайте боту право «Управление темами» "
                "(Manage topics) в правах администратора и отправьте /forum ещё раз."
            )
        await _send(msg.chat_id, "Группа поддержки подключена: каждая новая заявка будет открываться отдельной темой." + note, thread_id=msg.thread_id)
        logger.info("support_forum_registered", extra={"trace_id": trace_id, "chat_id": chat_ref(msg.chat_id), "moved": moved})
        return {"ok": True, "route": "support_forum", "status": "registered", "moved": moved, "trace_id": trace_id}
    if msg.thread_id is None:
        return None
    ticket = await find_ticket_by_forum_thread(tenant.tenant_id, forum_chat_id=msg.chat_id, forum_thread_id=msg.thread_id)
    if ticket is None:
        return None
    if ticket["status"] != "open":
        await _send(msg.chat_id, f"{ticket_label(ticket)} закрыто; клиент откроет новое обращение через «Сервисы», если нужно.", thread_id=msg.thread_id)
        return {"ok": False, "route": "support_relay", "status": "closed", "trace_id": trace_id}
    if msg.text.strip().lower() in {"закрыть", "/close"}:
        return await _close_ticket_everywhere(tenant, str(ticket["ticket_id"]), ticket, reply_chat=msg.chat_id, trace_id=trace_id)
    return await _relay_admin_to_user(tenant, msg, ticket, trace_id=trace_id)


async def try_handle_support_admin_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """The administrator's message: Reply → that ticket; no Reply → the only open one."""
    if msg.chat_type != "private" or not is_support_admin(msg.user_id):
        return None
    if msg.text.startswith("/") or _first_token(msg.text) in _OWNER_COMMAND_TOKENS or is_services_request(msg.text):
        return None
    reply_to = _reply_to_message_id(msg)
    if reply_to is not None:
        ticket = await find_ticket_by_admin_message(tenant.tenant_id, admin_chat_id=msg.chat_id, message_id=reply_to)
        if ticket is None:
            await _send(msg.chat_id, "Это сообщение не относится к обращению. Ответьте (Reply) на сообщение с номером #S-….")
            return {"ok": False, "route": "support_relay", "status": "unknown_reply", "trace_id": trace_id}
        if ticket["status"] != "open":
            await _send(msg.chat_id, f"{ticket_label(ticket)} закрыто; человек откроет новое обращение через «Сервисы», если нужно.")
            return {"ok": False, "route": "support_relay", "status": "closed", "trace_id": trace_id}
        return await _relay_admin_to_user(tenant, msg, ticket, trace_id=trace_id)

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
    admin_result = await try_handle_support_admin_message(tenant, msg, trace_id=trace_id)
    if admin_result is not None:
        return admin_result
    if msg.file_id and not is_support_admin(msg.user_id):
        return await try_relay_user_message(tenant, msg, trace_id=trace_id)
    return None
