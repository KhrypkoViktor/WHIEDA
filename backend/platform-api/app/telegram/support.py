"""Services catalogue and the support tunnel in the bot.

A subscriber opens «Сервисы», picks a Gemini offer (or «Поддержка»), confirms —
and a ticket opens between them and the service administrator. From then on
the bot relays messages both ways; neither side sees the other's contact.

How the administrator answers so the bot knows whom to send to: every relayed
message lands in the admin chat with a «#S-1042 · Name» header. The admin
uses Telegram's *Reply* on that message; Telegram attaches the original
message id, and the bot maps it back to the ticket. A plain message without a
Reply goes to the only open ticket, or — when several are open — the bot asks
for a Reply instead of guessing.

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
    close_ticket,
    find_ticket_by_admin_message,
    get_open_ticket_for_user,
    get_ticket,
    list_open_tickets_for_admin,
    open_or_reuse_ticket,
    record_relayed_message,
    ticket_label,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, copy_telegram_message, send_telegram_text
from app.telegram.log_safe import chat_ref
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

CHANNEL_GEMINI = "gemini"
_SERVICES_RE = re.compile(r"^(?:сервисы|/services|gemini|джемини)$", re.IGNORECASE)
_CALLBACK_RE = re.compile(r"^svc:(order|confirm|cancel|support|close):([A-Za-z0-9_-]+)$")
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
    "gemini_12m": Offer(
        code="gemini_12m",
        button="Gemini Pro 6 900 ₽",
        title="Gemini Pro, лицензия на 1 год",
        price_text="6 900 ₽",
        card=(
            "Gemini тариф Pro\n"
            "Лицензия на 1 год\n"
            "Гарантия на весь срок подписки. Если лицензия «слетит», производим переподключение за свой счёт.\n"
            "В целом, подобные лицензии работают спокойно без сбоев у наших клиентов с начала 2026 года.\n\n"
            "Стоимость подключения — 6 900 ₽"
        ),
    ),
}

SERVICES_TEXT = (
    "Gemini Pro\n"
    "В подписку входит нейросеть Gemini Pro + Nanobanana (генерация картинок) + "
    "VEO3 (видео) + 2 TB Google Drive (облачное хранилище).\n\n"
    "Тариф Pro, лицензия на 18 месяцев — 4 490 ₽, гарантия 1 месяц.\n"
    "Тариф Pro, лицензия на 1 год — 6 900 ₽, гарантия на весь срок подписки.\n\n"
    "Выберите вариант или напишите администратору."
)


def services_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": OFFERS["gemini_18m"].button, "callback_data": "svc:order:gemini_18m"}],
            [{"text": OFFERS["gemini_12m"].button, "callback_data": "svc:order:gemini_12m"}],
            [{"text": "Поддержка", "callback_data": f"svc:support:{CHANNEL_GEMINI}"}],
        ]
    }


def services_enabled() -> bool:
    """Staging feature until the owner signs it off for the production `minimal`
    profile (AGENTS.md: production shows only ready partner actions)."""
    return get_settings().telegram_ui_profile != "minimal"


def is_services_request(text: str) -> bool:
    return services_enabled() and bool(_SERVICES_RE.fullmatch(str(text or "").strip()))


def support_admin_id() -> int | None:
    value = get_settings().platform_support_admin_telegram_id
    return int(value) if value else None


def is_support_admin(user_id: int) -> bool:
    admin = support_admin_id()
    return admin is not None and int(user_id) == admin


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


def _reply_to_message_id(msg: TelegramMessage) -> int | None:
    reply = ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}
    value = reply.get("message_id")
    return int(value) if value is not None else None


def _first_token(text: str) -> str:
    return (str(text or "").strip().split() or [""])[0].lower()


async def _send(chat_id: int, text: str, *, reply_markup: dict | None = None) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token, reply_markup=reply_markup
    )


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
    label = ticket_label(ticket)
    if offer:
        header = f"{label} · Заказ: {offer.title} — {offer.price_text}\n{_display(source)}"
        user_text = (
            f"Заявка {label} принята: {offer.title} — {offer.price_text}.\n"
            "Администратор ответит здесь же, в этом чате. Всё, что вы напишете сюда, уйдёт ему."
        )
    else:
        header = f"{label} · Вопрос по Gemini\n{_display(source)}"
        user_text = (
            f"Обращение {label} открыто. Напишите вопрос — он уйдёт администратору Gemini, "
            "ответ придёт сюда."
        )
    delivered = await _send(
        admin,
        header + "\n\nОтветьте на это сообщение (Reply) — ответ уйдёт человеку.",
        reply_markup=_close_keyboard(ticket),
    )
    await record_relayed_message(
        tenant.tenant_id,
        ticket_id=str(ticket["ticket_id"]),
        direction="system",
        text=header,
        delivered_chat_id=admin,
        delivered_message_id=delivered.get("message_id"),
    )
    await _send(source.chat_id, user_text)
    logger.info(
        "support_ticket_opened",
        extra={"trace_id": trace_id, "ticket": label, "offer": offer.code if offer else None, "created": ticket["created"]},
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
    if callback.chat_type != "private":
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
        if not is_support_admin(callback.user_id):
            return {"ok": False, "route": "services", "status": "forbidden", "trace_id": trace_id}
        ticket = await close_ticket(tenant.tenant_id, ticket_id=arg, closed_by="admin")
        if not ticket:
            existing = await get_ticket(tenant.tenant_id, ticket_id=arg)
            await _send(callback.chat_id, f"{ticket_label(existing) if existing else 'Обращение'} уже закрыто.")
            return {"ok": True, "route": "services", "status": "already_closed", "trace_id": trace_id}
        label = ticket_label(ticket)
        await _send(
            int(ticket["user_chat_id"]),
            f"Обращение {label} закрыто. Если появятся вопросы — откройте «Сервисы» и нажмите «Поддержка».",
        )
        await _send(callback.chat_id, f"{label} закрыто.")
        return {"ok": True, "route": "services", "status": "closed", "ticket": label, "trace_id": trace_id}
    return None


async def _relay_user_to_admin(tenant: TenantContext, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    admin = int(ticket["admin_telegram_user_id"])
    label = ticket_label(ticket)
    header = f"{label} · {ticket['user_display']}"
    if msg.file_id:
        # A photo or document: copy it (no forward header, no contact leak), then
        # a text line the admin can Reply to.
        await copy_telegram_message(
            chat_id=str(admin), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
        )
        delivered = await _send(admin, f"{header}\n(вложение выше)" + (f"\n{msg.text}" if msg.text else ""), reply_markup=_close_keyboard(ticket))
    else:
        delivered = await _send(admin, f"{header}\n{msg.text}", reply_markup=_close_keyboard(ticket))
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
    await _send(msg.chat_id, f"→ отправлено: {ticket['user_display']} ({label})")
    return {"ok": True, "route": "support_relay", "direction": "admin_to_user", "ticket": label, "trace_id": trace_id}


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
    lines += [f"{ticket_label(t)} · {t['user_display']}" for t in open_tickets]
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
