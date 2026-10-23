"""Обращения в боте витрины Академии (14.10.2026): перенос туннеля WWC Support для @ALMIRA_I_bot.

Две кнопки в боте — два адресата:

* «Написать художнику» — вопросы по курсу и работам: автору витрины (у ALMIRA — Саше).
  Саша не хочет видеть всю переписку студии, поэтому её обращения идут ей в личку с ботом
  (режим Reply, как у администратора Gemini); группу с темами ей подключать не обязательно
  (``/forum site`` в группе — если передумает).
* «Написать администратору» — оплата, доступ, расписание и всё остальное: администратору
  витрины (у ALMIRA — Кириллу). Его группа с темами подключается командой ``/forum``:
  каждое обращение — своя тема «#A-12 · Имя (@ник) · администратору», ответ пишут прямо
  в теме, «закрыть» закрывает обращение и тему.

Хранилище — то же, что у WWC Support (support_tickets / support_messages / support_forums,
app/support/service.py): канал ``academy:author`` или ``academy:admin``, форум — по
binding бота витрины. Адресаты — в ``academy_showcases.support`` (V33):
``{"admin_telegram_user_id": …, "author_telegram_user_id": …}``; автор по умолчанию —
Telegram владельца витрины из lead_actors. Бот WHIEDA обращения Академии не трогает:
его запросы исключают каналы ``academy:%``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.academy.showcases import Showcase
from app.db import fetch_all, fetch_one, tenant_connection
from app.support.service import (
    FORUM_KIND_SERVICES,
    FORUM_KIND_SITE,
    attach_forum_topic,
    close_ticket,
    find_ticket_by_admin_message,
    find_ticket_by_forum_thread,
    get_forum,
    get_open_ticket_for_user,
    get_ticket,
    list_open_tickets_for_admin,
    open_or_reuse_ticket,
    record_relayed_message,
    register_forum,
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
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

KIND_AUTHOR = "author"
KIND_ADMIN = "admin"
CHANNEL_PREFIX = "academy:"
CHANNELS = {KIND_AUTHOR: f"{CHANNEL_PREFIX}{KIND_AUTHOR}", KIND_ADMIN: f"{CHANNEL_PREFIX}{KIND_ADMIN}"}
# Форум администратора — «/forum» (kind services), форум автора — «/forum site».
FORUM_KINDS = {KIND_ADMIN: FORUM_KIND_SERVICES, KIND_AUTHOR: FORUM_KIND_SITE}
BUTTON_AUTHOR = "🎨 Написать художнику"
BUTTON_ADMIN = "🛠 Написать администратору"
WHO = {KIND_AUTHOR: "художнику", KIND_ADMIN: "администратору"}
ANSWER_FROM = {KIND_AUTHOR: "Ответ художника", KIND_ADMIN: "Ответ администратора"}

_OPEN_RE = {
    KIND_AUTHOR: re.compile(r"^(?:🎨\s*)?(?:написать\s+)?художник[уа]?$|^/author$", re.IGNORECASE),
    KIND_ADMIN: re.compile(r"^(?:🛠\s*)?(?:написать\s+)?администратор[уа]?$|^/admin$", re.IGNORECASE),
}
_CALLBACK_RE = re.compile(r"^shs:open:(author|admin)$")
_CLOSE_CALLBACK_RE = re.compile(r"^svc:close:([A-Za-z0-9_-]+)$")
_FORUM_REGISTER_RE = re.compile(r"^/forum(?:@\w+)?(?:\s+(site|services))?$", re.IGNORECASE)
_CLOSE_WORDS = {"закрыть", "/close"}
_ATTACHMENT_WORDS = {
    "voice": "голосовое", "video_note": "видеокружок", "video": "видео", "audio": "аудио",
    "animation": "GIF", "sticker": "стикер",
}


# ---- адресаты ---------------------------------------------------------------------------------


async def support_targets(tenant_id: str, showcase: Showcase) -> dict[str, int | None]:
    """Кому уходят обращения каждого рода: из ``showcase.support``; автор по умолчанию —
    Telegram владельца витрины (lead_actors)."""
    support = showcase.support or {}
    author = _int_or_none(support.get("author_telegram_user_id"))
    if author is None and showcase.owner_actor_id:
        async with tenant_connection(tenant_id) as conn:
            row = await fetch_one(
                conn,
                "select telegram_user_id from lead_actors where tenant_id = %s and actor_id = %s and active = true",
                (tenant_id, showcase.owner_actor_id),
            )
        author = _int_or_none((row or {}).get("telegram_user_id"))
    return {KIND_AUTHOR: author, KIND_ADMIN: _int_or_none(support.get("admin_telegram_user_id"))}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _managers(tenant_id: str, showcase: Showcase) -> set[int]:
    """Кто может подключить группу командой /forum: адресаты обращений и владелец платформы."""
    from app.academy.service import preview_admin_ids

    targets = await support_targets(tenant_id, showcase)
    ids = {int(v) for v in targets.values() if v is not None}
    ids |= set(preview_admin_ids())
    return ids


def support_buttons() -> list[list[dict[str, Any]]]:
    """Ряды кнопок для приветствия бота витрины."""
    return [
        [{"text": BUTTON_AUTHOR, "callback_data": "shs:open:author"}],
        [{"text": BUTTON_ADMIN, "callback_data": "shs:open:admin"}],
    ]


# ---- служебное --------------------------------------------------------------------------------


def _label(ticket: dict[str, Any]) -> str:
    return f"#A-{int(ticket['ticket_no'])}"


def _kind(ticket: dict[str, Any]) -> str:
    return KIND_ADMIN if str(ticket.get("channel_code") or "") == CHANNELS[KIND_ADMIN] else KIND_AUTHOR


def _in_forum(ticket: dict[str, Any]) -> bool:
    return ticket.get("forum_thread_id") is not None and ticket.get("forum_chat_id") is not None


def _display(source: TelegramMessage | TelegramCallbackQuery) -> str:
    raw = source.raw or {}
    user = (raw.get("message") or raw.get("callback_query") or {}).get("from") or {}
    name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    username = str(user.get("username") or "").strip()
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else f"Telegram {source.user_id}")


def _topic_name(ticket: dict[str, Any], *, closed: bool = False) -> str:
    display = str(ticket.get("user_display") or "").strip() or f"Telegram {ticket['user_telegram_user_id']}"
    return (("✅ " if closed else "") + f"{_label(ticket)} · {display} · {WHO[_kind(ticket)]}")[:128]


def _header(ticket: dict[str, Any], card: str = "") -> str:
    display = str(ticket.get("user_display") or "").strip() or f"Telegram {ticket['user_telegram_user_id']}"
    lines = [f"{_label(ticket)} · {display} · {WHO[_kind(ticket)]}", f"tg://user?id={int(ticket['user_telegram_user_id'])}"]
    if card:
        lines += ["", card]
    return "\n".join(lines)


async def _card(tenant_id: str, showcase: Showcase, source: TelegramMessage | TelegramCallbackQuery) -> str:
    """Строка таблицы заявок человека — в шапку темы: кто это, прямые контакты, вебинар (22.10.2026)."""
    from app.academy.webinars import lead_card

    raw = source.raw or {}
    user = (raw.get("message") or raw.get("callback_query") or {}).get("from") or {}
    name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    try:
        return await lead_card(
            tenant_id, showcase, telegram_user_id=int(source.user_id),
            username=str(user.get("username") or "").strip() or None, name=name,
        )
    except Exception:
        logger.warning("showcase_support_card_failed", exc_info=True)
        return ""


def _close_keyboard(ticket: dict[str, Any]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Закрыть обращение", "callback_data": f"svc:close:{ticket['ticket_id']}"}]]}


async def _send(chat_id: int, text: str, *, reply_markup: dict | None = None, thread_id: int | None = None) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup, message_thread_id=thread_id,
    )


async def _send_to_admin(ticket: dict[str, Any], text: str, *, reply_markup: dict | None = None) -> dict[str, Any]:
    if _in_forum(ticket):
        return await _send(int(ticket["forum_chat_id"]), text, reply_markup=reply_markup, thread_id=int(ticket["forum_thread_id"]))
    return await _send(int(ticket["admin_telegram_user_id"]), text, reply_markup=reply_markup)


def _reply_to_message_id(msg: TelegramMessage) -> int | None:
    reply = ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}
    value = reply.get("message_id")
    return int(value) if value is not None else None


async def _open_academy_ticket(tenant_id: str, user_id: int) -> dict[str, Any] | None:
    """Открыты оба обращения — сообщение уходит в то, что человек открыл (или писал) последним."""
    found = []
    for kind in (KIND_AUTHOR, KIND_ADMIN):
        ticket = await get_open_ticket_for_user(tenant_id, user_telegram_user_id=user_id, channel_code=CHANNELS[kind])
        if ticket:
            found.append(ticket)
    return max(found, key=lambda t: t.get("last_message_at") or t.get("created_at")) if found else None


async def _touch(tenant_id: str, ticket: dict[str, Any]) -> None:
    """Кнопка уже открытого обращения делает его активным: следующие сообщения — туда."""
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "update support_tickets set last_message_at = now() where tenant_id = %s and ticket_id = %s::uuid",
                (tenant_id, str(ticket["ticket_id"])),
            )


# ---- открыть обращение ------------------------------------------------------------------------


async def open_ticket(
    tenant: TenantContext, source: TelegramMessage | TelegramCallbackQuery, showcase: Showcase, kind: str, *, trace_id: str
) -> dict[str, Any]:
    tenant_id = tenant.tenant_id
    targets = await support_targets(tenant_id, showcase)
    admin = targets.get(kind)
    if admin is None:
        contact = showcase.links.get("contact_url") or showcase.links.get("home_url") or ""
        await _send(source.chat_id, f"Этот адресат пока не подключён. Напишите в студию: {contact}".strip())
        return {"ok": False, "route": "showcase_support", "status": "no_target", "kind": kind, "trace_id": trace_id}
    ticket = await open_or_reuse_ticket(
        tenant_id,
        channel_code=CHANNELS[kind],
        offer_code=None,
        offer_title=None,
        user_telegram_user_id=source.user_id,
        user_chat_id=source.chat_id,
        user_display=_display(source),
        admin_telegram_user_id=admin,
    )
    label = _label(ticket)
    if ticket["created"]:
        ticket = await _open_forum_topic(tenant_id, ticket)
        hint = (
            f"Пишите в эту тему — ответ уйдёт человеку. «Закрыть» или кнопка — закрыть обращение."
            if _in_forum(ticket)
            else "Ответьте на это сообщение (Reply) — ответ уйдёт человеку. Кнопка ниже закрывает обращение."
        )
        header = _header(ticket, await _card(tenant_id, showcase, source))
        delivered = await _send_to_admin(ticket, header + "\n\n" + hint, reply_markup=_close_keyboard(ticket))
        if not delivered.get("ok"):
            logger.warning("showcase_support_target_unreachable", extra={"ticket": label, "kind": kind})
        await record_relayed_message(
            tenant_id, ticket_id=str(ticket["ticket_id"]), direction="system", text=header,
            delivered_chat_id=int(ticket["forum_chat_id"]) if _in_forum(ticket) else admin,
            delivered_message_id=delivered.get("message_id"),
        )
        await _send(
            source.chat_id,
            f"Обращение {label} открыто. Напишите вопрос {WHO[kind]} — текст, фото или голосовое; ответ придёт сюда.",
        )
    else:
        await _touch(tenant_id, ticket)
        await _send(source.chat_id, f"Обращение {label} уже открыто — просто напишите сюда, {WHO[kind]} всё уйдёт.")
    logger.info("showcase_support_opened", extra={"trace_id": trace_id, "ticket": label, "kind": kind, "ticket_created": ticket["created"]})
    return {"ok": True, "route": "showcase_support", "status": "ticket_opened" if ticket["created"] else "ticket_reused", "kind": kind, "ticket": label, "trace_id": trace_id}


async def _open_forum_topic(tenant_id: str, ticket: dict[str, Any]) -> dict[str, Any]:
    forum = await get_forum(tenant_id, binding_id=current_bot_binding().binding_id, kind=FORUM_KINDS[_kind(ticket)])
    if not forum:
        return ticket
    created = await create_forum_topic(chat_id=str(forum["chat_id"]), name=_topic_name(ticket), bot_token=current_bot_binding().bot_token)
    thread_id = created.get("message_thread_id") if created.get("ok") else None
    if not thread_id:
        logger.warning("showcase_support_topic_failed", extra={"ticket": _label(ticket)})
        # Подсказка в группу: чаще всего у бота нет права «Управление темами».
        await _send(
            int(forum["chat_id"]),
            f"Не получилось открыть тему для обращения {_label(ticket)}: дайте боту право «Управление темами» "
            "в правах администратора группы. Пока обращение пришло адресату в личку с ботом.",
        )
        return ticket
    attached = await attach_forum_topic(
        tenant_id, ticket_id=str(ticket["ticket_id"]), forum_chat_id=int(forum["chat_id"]), forum_thread_id=int(thread_id)
    )
    return {**ticket, **(attached or {})}


async def _move_open_to_forum(tenant_id: str, kind: str) -> int:
    """Открытые обращения рода ``kind`` без темы — в темы только что подключённой группы."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select ticket_id::text as ticket_id from support_tickets
            where tenant_id = %s and channel_code = %s and status = 'open' and forum_thread_id is null
            order by created_at
            """,
            (tenant_id, CHANNELS[kind]),
        )
    moved = 0
    for row in rows:
        ticket = await get_ticket(tenant_id, ticket_id=row["ticket_id"])
        if not ticket:
            continue
        ticket = await _open_forum_topic(tenant_id, ticket)
        if _in_forum(ticket):
            await _send_to_admin(
                ticket, _header(ticket) + "\n\nОбращение перенесено в тему. Пишите здесь — ответ уйдёт человеку.",
                reply_markup=_close_keyboard(ticket),
            )
            moved += 1
    return moved


# ---- переписка --------------------------------------------------------------------------------


async def _relay_user_to_admin(tenant_id: str, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    target_chat = int(ticket["forum_chat_id"]) if _in_forum(ticket) else int(ticket["admin_telegram_user_id"])
    header = f"{_label(ticket)} · {ticket.get('user_display') or ''}".strip(" ·")
    attachment = msg.attachment_id
    if attachment:
        await copy_telegram_message(
            chat_id=str(target_chat), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
            message_thread_id=int(ticket["forum_thread_id"]) if _in_forum(ticket) else None,
        )
        what = "вложение" if msg.file_id else _ATTACHMENT_WORDS.get(str(msg.media_kind or ""), "вложение")
        delivered = await _send_to_admin(ticket, f"{header}\n({what} выше)" + (f"\n{msg.text}" if msg.text else ""))
    else:
        delivered = await _send_to_admin(ticket, f"{header}\n{msg.text}")
    result = await record_relayed_message(
        tenant_id, ticket_id=str(ticket["ticket_id"]), direction="user_to_admin", text=msg.text,
        telegram_file_id=attachment, source_chat_id=msg.chat_id, source_message_id=msg.message_id,
        delivered_chat_id=target_chat, delivered_message_id=delivered.get("message_id"),
    )
    return {"ok": True, "route": "showcase_support_relay", "direction": "user_to_admin", "ticket": _label(ticket), "duplicate": result["duplicate"], "trace_id": trace_id}


async def _relay_admin_to_user(tenant_id: str, msg: TelegramMessage, ticket: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
    user_chat = int(ticket["user_chat_id"])
    prefix = f"{ANSWER_FROM[_kind(ticket)]} по обращению {_label(ticket)}"
    attachment = msg.attachment_id
    if attachment:
        await copy_telegram_message(
            chat_id=str(user_chat), from_chat_id=str(msg.chat_id), message_id=msg.message_id, bot_token=current_bot_binding().bot_token,
        )
        delivered = await _send(user_chat, f"{prefix}: вложение выше." + (f"\n{msg.text}" if msg.text else ""))
    else:
        delivered = await _send(user_chat, f"{prefix}:\n{msg.text}")
    await record_relayed_message(
        tenant_id, ticket_id=str(ticket["ticket_id"]), direction="admin_to_user", text=msg.text,
        telegram_file_id=attachment, source_chat_id=msg.chat_id, source_message_id=msg.message_id,
        delivered_chat_id=user_chat, delivered_message_id=delivered.get("message_id"),
    )
    if _in_forum(ticket) and msg.chat_id == int(ticket["forum_chat_id"]):
        receipt = await set_message_reaction(chat_id=str(msg.chat_id), message_id=msg.message_id, emoji="👍", bot_token=current_bot_binding().bot_token)
        if not receipt.get("ok"):
            await _send(msg.chat_id, f"→ отправлено: {_label(ticket)}", thread_id=int(ticket["forum_thread_id"]))
    else:
        await _send(msg.chat_id, f"→ отправлено: {_label(ticket)}")
    return {"ok": True, "route": "showcase_support_relay", "direction": "admin_to_user", "ticket": _label(ticket), "trace_id": trace_id}


async def _close(tenant_id: str, ticket: dict[str, Any], *, closed_by: str, reply_chat: int, reply_thread: int | None, trace_id: str) -> dict[str, Any]:
    label = _label(ticket)
    if ticket["status"] == "open":
        await close_ticket(tenant_id, ticket_id=str(ticket["ticket_id"]), closed_by=closed_by)
        await _send(int(ticket["user_chat_id"]), f"Обращение {label} закрыто. Появится вопрос — нажмите кнопку в боте ещё раз.")
    if _in_forum(ticket):
        token = current_bot_binding().bot_token
        await edit_forum_topic(chat_id=str(ticket["forum_chat_id"]), message_thread_id=int(ticket["forum_thread_id"]), name=_topic_name(ticket, closed=True), bot_token=token)
        await close_forum_topic(chat_id=str(ticket["forum_chat_id"]), message_thread_id=int(ticket["forum_thread_id"]), bot_token=token)
    else:
        await _send(reply_chat, f"Обращение {label} закрыто.", thread_id=reply_thread)
    logger.info("showcase_support_closed", extra={"trace_id": trace_id, "ticket": label})
    return {"ok": True, "route": "showcase_support", "status": "closed", "ticket": label, "trace_id": trace_id}


# ---- точки входа ------------------------------------------------------------------------------


async def try_handle_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, showcase: Showcase, *, trace_id: str
) -> dict[str, Any] | None:
    """Кнопки «Написать …» (личка) и «Закрыть обращение» (личка адресата или его тема)."""
    opened = _CALLBACK_RE.fullmatch(callback.data)
    if opened:
        await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
        if callback.chat_type != "private":
            return {"ok": False, "route": "showcase_support", "status": "private_chat_required", "trace_id": trace_id}
        return await open_ticket(tenant, callback, showcase, opened.group(1), trace_id=trace_id)
    closing = _CLOSE_CALLBACK_RE.fullmatch(callback.data)
    if not closing:
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    ticket = await get_ticket(tenant.tenant_id, ticket_id=closing.group(1))
    if ticket is None or not str(ticket.get("channel_code") or "").startswith(CHANNEL_PREFIX):
        return {"ok": False, "route": "showcase_support", "status": "unknown_ticket", "trace_id": trace_id}
    in_topic = _in_forum(ticket) and callback.chat_id == int(ticket["forum_chat_id"])
    allowed = in_topic or int(callback.user_id) == int(ticket["admin_telegram_user_id"]) or int(callback.user_id) in await _managers(tenant.tenant_id, showcase)
    if not allowed:
        return {"ok": False, "route": "showcase_support", "status": "forbidden", "trace_id": trace_id}
    return await _close(tenant.tenant_id, ticket, closed_by="admin", reply_chat=callback.chat_id, reply_thread=callback.thread_id, trace_id=trace_id)


async def try_handle_forum_message(
    tenant: TenantContext, msg: TelegramMessage, showcase: Showcase, *, trace_id: str
) -> dict[str, Any] | None:
    """Группа с темами: «/forum» подключает группу администратора («/forum site» — автора),
    текст в теме обращения уходит человеку, «закрыть» закрывает."""
    if msg.chat_type != "supergroup" or msg.from_bot:
        return None
    tenant_id = tenant.tenant_id
    register = _FORUM_REGISTER_RE.fullmatch(msg.text.strip())
    if register:
        kind = (register.group(1) or FORUM_KIND_SERVICES).lower()
        if int(msg.user_id) not in await _managers(tenant_id, showcase):
            return {"ok": False, "route": "showcase_support_forum", "status": "forbidden", "trace_id": trace_id}
        if not msg.is_forum:
            await _send(msg.chat_id, "В этой группе не включены темы. Включите «Темы» в настройках группы и повторите /forum.")
            return {"ok": False, "route": "showcase_support_forum", "status": "not_a_forum", "trace_id": trace_id}
        title = str(((msg.raw.get("message") or {}).get("chat") or {}).get("title") or "")
        await register_forum(tenant_id, binding_id=current_bot_binding().binding_id, chat_id=msg.chat_id, title=title, registered_by=msg.user_id, kind=kind)
        who = "автору («Написать художнику»)" if kind == FORUM_KIND_SITE else "администратору («Написать администратору»)"
        moved = await _move_open_to_forum(tenant_id, KIND_AUTHOR if kind == FORUM_KIND_SITE else KIND_ADMIN)
        note = f" Открытые обращения перенесены в темы: {moved}." if moved else ""
        await _send(msg.chat_id, f"Группа подключена: каждое обращение {who} будет открываться отдельной темой. Отвечайте прямо в теме; «закрыть» закрывает обращение.{note}", thread_id=msg.thread_id)
        logger.info("showcase_support_forum_registered", extra={"trace_id": trace_id, "kind": kind})
        return {"ok": True, "route": "showcase_support_forum", "status": "registered", "kind": kind, "trace_id": trace_id}
    if msg.thread_id is None:
        return None
    ticket = await find_ticket_by_forum_thread(tenant_id, forum_chat_id=msg.chat_id, forum_thread_id=msg.thread_id)
    if ticket is None or not str(ticket.get("channel_code") or "").startswith(CHANNEL_PREFIX):
        return None
    if ticket["status"] != "open":
        await _send(msg.chat_id, f"Обращение {_label(ticket)} закрыто. Если человек напишет снова — откроется новое.", thread_id=msg.thread_id)
        return {"ok": False, "route": "showcase_support_relay", "status": "closed", "trace_id": trace_id}
    if msg.text.strip().lower() in _CLOSE_WORDS:
        return await _close(tenant_id, ticket, closed_by="admin", reply_chat=msg.chat_id, reply_thread=msg.thread_id, trace_id=trace_id)
    return await _relay_admin_to_user(tenant_id, msg, ticket, trace_id=trace_id)


def is_open_request(text: str) -> str | None:
    """Текст кнопки или команда → род обращения."""
    clean = str(text or "").strip()
    for kind, pattern in _OPEN_RE.items():
        if pattern.fullmatch(clean):
            return kind
    return None


async def try_handle_private_message(
    tenant: TenantContext, msg: TelegramMessage, showcase: Showcase, *, trace_id: str, early: bool
) -> dict[str, Any] | None:
    """Личка. ``early`` — до команд Академии: кнопки «Написать …», ответы адресата через
    Reply. Поздний проход (``early=False``) — после команд Академии: текст и вложения
    человека с открытым обращением уходят адресату; плоский ответ адресата с единственным
    открытым обращением — человеку."""
    if msg.chat_type != "private":
        return None
    tenant_id = tenant.tenant_id
    if early:
        kind = is_open_request(msg.text)
        if kind:
            return await open_ticket(tenant, msg, showcase, kind, trace_id=trace_id)
        reply_to = _reply_to_message_id(msg)
        if reply_to is not None:
            ticket = await find_ticket_by_admin_message(tenant_id, admin_chat_id=msg.chat_id, message_id=reply_to)
            if ticket is not None and str(ticket.get("channel_code") or "").startswith(CHANNEL_PREFIX):
                if ticket["status"] != "open":
                    await _send(msg.chat_id, f"Обращение {_label(ticket)} закрыто.")
                    return {"ok": False, "route": "showcase_support_relay", "status": "closed", "trace_id": trace_id}
                return await _relay_admin_to_user(tenant_id, msg, ticket, trace_id=trace_id)
        return None
    if msg.text.startswith("/"):
        return None
    ticket = await _open_academy_ticket(tenant_id, msg.user_id)
    if ticket is not None:
        return await _relay_user_to_admin(tenant_id, msg, ticket, trace_id=trace_id)
    # Адресат пишет без Reply: единственное открытое обращение в режиме лички — ему.
    mine = [
        t for t in await list_open_tickets_for_admin(tenant_id, admin_telegram_user_id=msg.user_id, academy=True)
        if str(t.get("channel_code") or "").startswith(CHANNEL_PREFIX)
    ]
    if len(mine) == 1 and (msg.text or msg.attachment_id):
        return await _relay_admin_to_user(tenant_id, msg, mine[0], trace_id=trace_id)
    if len(mine) > 1 and msg.text:
        await _send(msg.chat_id, "Открыто несколько обращений — ответьте через Reply на сообщение нужного:\n" + "\n".join(_header(t).splitlines()[0] for t in mine))
        return {"ok": False, "route": "showcase_support_relay", "status": "ambiguous", "trace_id": trace_id}
    return None
