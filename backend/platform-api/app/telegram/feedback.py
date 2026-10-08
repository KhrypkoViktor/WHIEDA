"""«💡 Пожелание» в боте (V25, 08.10.2026) — для всех, в Telegram и в Max.

Человек жмёт кнопку или пишет «пожелание» → следующим сообщением (текст, голос,
скриншот) присылает пожелание → оно уходит в тему «💡 Пожелания» группы WWC
Support с кнопками «В работу» / «Сделано» / «Не будем». Reply владельца на пост
пожелания уходит человеку; «Сделано» — человек получает «сделали».
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.feedback.service import (
    DECISIONS,
    STATUS_WORDS,
    decide,
    find_by_forum_message,
    mark_notified,
    partner_ref,
    set_forum_message,
    set_wishes_thread,
    start_wish,
    submit_wish,
    waiting_wish,
    wishes_thread,
)
from app.settings import get_settings
from app.support.service import FORUM_KIND_SITE
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import (
    answer_callback_query,
    copy_telegram_message,
    create_forum_topic,
    send_telegram_text,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

WISH_CALLBACK = "wish:start"
WISH_BUTTON = {"text": "💡 Пожелание", "callback_data": WISH_CALLBACK}
WISHES_TOPIC = "💡 Пожелания"
_WISH_RE = re.compile(r"^(?:/wish(?:@\w+)?|💡?\s*(?:пожелани[еяй]|предложени[ея]|идея))\s*[.!]?$", re.I)
_DECISION_RE = re.compile(r"^wish:(prog|done|no):([0-9a-f]{32})$")

WISH_PROMPT = (
    "Что улучшить в WWC — в боте, на сайте, в курсах или в клубе?\n\n"
    "Напишите одним сообщением. Можно голосовым или со скриншотом. Мы читаем каждое пожелание."
)


def wishes_enabled(tenant: TenantContext) -> bool:
    """Как «Поддержка»: только у тенанта с site_support — пожелания читает владелец WWC."""
    return bool(tenant.entitlements.get("site_support", False))


def is_wish_request(text: str) -> bool:
    return bool(_WISH_RE.fullmatch(str(text or "").strip()))


def thanks_text(no: int) -> str:
    return f"Спасибо! Пожелание №{no} записали и передали команде WWC. Сообщим, когда сделаем."


def done_text(row: dict[str, Any]) -> str:
    quote = str(row.get("text") or "").strip()
    quote = f" «{quote[:200]}{'…' if len(quote) > 200 else ''}»" if quote else ""
    return f"Ваше пожелание №{row['feedback_no']}{quote} — сделали. Спасибо, что помогаете делать WWC лучше!"


def _token(feedback_id: str) -> str:
    return str(feedback_id).replace("-", "")


def _uuid(token: str) -> str:
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def decision_keyboard(feedback_id: str) -> dict[str, Any]:
    t = _token(feedback_id)
    return {"inline_keyboard": [[
        {"text": "В работу", "callback_data": f"wish:prog:{t}"},
        {"text": "Сделано", "callback_data": f"wish:done:{t}"},
        {"text": "Не будем", "callback_data": f"wish:no:{t}"},
    ]]}


def _owner_id() -> int | None:
    value = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    return int(value) if value.isdigit() else None


def _display(msg: TelegramMessage) -> str:
    sender = ((msg.raw or {}).get("message") or {}).get("from") or {}
    name = " ".join(p for p in (str(sender.get("first_name") or "").strip(), str(sender.get("last_name") or "").strip()) if p)
    return f"{name or 'Без имени'}" + (f" (@{msg.username})" if msg.username else "")


def owner_post_text(row: dict[str, Any], *, attachment: bool) -> str:
    who = str(row.get("user_display") or f"id {row['user_id']}")
    where = " · Max" if row.get("channel") == "max" else ""
    ref = f" · {row['ref_code']}" if row.get("ref_code") else ""
    lines = [f"💡 Пожелание №{row['feedback_no']} — {who}{ref}{where}"]
    if row.get("text"):
        lines += ["", str(row["text"])]
    if attachment:
        lines += ["", "(вложение выше)"]
    lines += ["", "Ответить человеку — Reply на это сообщение."]
    return "\n".join(lines)


async def _wishes_topic(tenant: TenantContext) -> tuple[int, int | None] | None:
    """Тема «💡 Пожелания» в группе WWC Support; нет группы — None (тогда в личку владельцу)."""
    binding = current_bot_binding()
    found = await wishes_thread(tenant.tenant_id, binding_id=binding.binding_id, kind=FORUM_KIND_SITE)
    if not found:
        return None
    chat_id, thread = found
    if thread:
        return chat_id, thread
    created = await create_forum_topic(chat_id=str(chat_id), name=WISHES_TOPIC, bot_token=binding.bot_token)
    if not created.get("ok"):
        logger.warning("wishes_topic_create_failed")
        return chat_id, None
    thread = int(created["message_thread_id"])
    await set_wishes_thread(tenant.tenant_id, binding_id=binding.binding_id, kind=FORUM_KIND_SITE, thread_id=thread)
    return chat_id, thread


async def deliver_wish(tenant: TenantContext, row: dict[str, Any], *, source_chat_id: int | None = None, source_message_id: int | None = None) -> None:
    """Пост пожелания владельцу: тема «Пожелания» или личка; вложение — копией выше."""
    token = current_bot_binding().bot_token
    topic = await _wishes_topic(tenant)
    if topic and topic[1]:
        chat_id, thread = topic
    else:
        chat_id, thread = _owner_id(), None
    if chat_id is None:
        return
    attachment = bool(source_chat_id and source_message_id and row.get("file_id"))
    if attachment:
        await copy_telegram_message(
            chat_id=str(chat_id), from_chat_id=str(source_chat_id), message_id=int(source_message_id),
            bot_token=token, message_thread_id=thread,
        )
    sent = await send_telegram_text(
        chat_id=str(chat_id), text=owner_post_text(row, attachment=attachment), bot_token=token,
        message_thread_id=thread, reply_markup=decision_keyboard(row["feedback_id"]),
    )
    await set_forum_message(tenant.tenant_id, row["feedback_id"], chat_id=int(chat_id), message_id=sent.get("message_id"))


async def try_handle_wish_message(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Личка: «пожелание» начинает, следующее сообщение — само пожелание."""
    if msg.chat_type != "private" or not wishes_enabled(tenant):
        return None
    if is_wish_request(msg.text):
        await start_wish(
            tenant.tenant_id, channel="telegram", user_id=msg.user_id, chat_id=msg.chat_id,
            display=_display(msg), ref_code=await partner_ref(tenant.tenant_id, msg.user_id),
        )
        await send_telegram_text(chat_id=str(msg.chat_id), text=WISH_PROMPT, bot_token=current_bot_binding().bot_token)
        return {"ok": True, "route": "wish", "status": "awaiting", "trace_id": trace_id}
    if msg.text.startswith("/"):
        return None
    try:
        waiting = await waiting_wish(tenant.tenant_id, channel="telegram", user_id=msg.user_id)
    except RuntimeError as exc:
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if not waiting or not (msg.text or msg.attachment_id):
        return None
    row = await submit_wish(
        tenant.tenant_id, waiting["feedback_id"], text=msg.text, file_id=msg.attachment_id,
        media_kind=msg.media_kind or ("photo" if msg.file_id else None), display=_display(msg),
    )
    if not row:
        return None
    await deliver_wish(tenant, row, source_chat_id=msg.chat_id, source_message_id=msg.message_id)
    await send_telegram_text(chat_id=str(msg.chat_id), text=thanks_text(int(row["feedback_no"])), bot_token=current_bot_binding().bot_token)
    logger.info("wish_received", extra={"trace_id": trace_id, "no": row["feedback_no"]})
    return {"ok": True, "route": "wish", "status": "new", "no": int(row["feedback_no"]), "trace_id": trace_id}


async def notify_wish_author(row: dict[str, Any], text: str) -> bool:
    if row.get("channel") == "max":
        from app.max.client import send_max_text

        sent = await send_max_text(user_id=int(row["user_id"]), text=text)
    else:
        sent = await send_telegram_text(chat_id=str(row["chat_id"]), text=text, bot_token=current_bot_binding().bot_token)
    return bool(sent.get("ok"))


async def try_handle_wish_callback(tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str) -> dict[str, Any] | None:
    if not callback.data.startswith("wish:") or not wishes_enabled(tenant):
        return None
    token = current_bot_binding().bot_token
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=token)
    if callback.data == WISH_CALLBACK:
        if callback.chat_type != "private":
            return {"ok": True, "route": "wish", "status": "private_chat_required", "trace_id": trace_id}
        sender = ((callback.raw or {}).get("callback_query") or {}).get("from") or {}
        name = " ".join(p for p in (str(sender.get("first_name") or ""), str(sender.get("last_name") or "")) if p.strip()).strip()
        username = str(sender.get("username") or "").strip()
        await start_wish(
            tenant.tenant_id, channel="telegram", user_id=callback.user_id, chat_id=callback.chat_id,
            display=(name or "Без имени") + (f" (@{username})" if username else ""),
            ref_code=await partner_ref(tenant.tenant_id, callback.user_id),
        )
        await send_telegram_text(chat_id=str(callback.chat_id), text=WISH_PROMPT, bot_token=token)
        return {"ok": True, "route": "wish", "status": "awaiting", "trace_id": trace_id}
    match = _DECISION_RE.fullmatch(callback.data)
    if not match:
        return {"ok": False, "route": "wish", "status": "unknown", "trace_id": trace_id}
    if callback.user_id != _owner_id():
        return {"ok": False, "route": "wish", "status": "forbidden", "trace_id": trace_id}
    status = DECISIONS[match.group(1)]
    row, changed = await decide(tenant.tenant_id, _uuid(match.group(2)), status=status, by=callback.user_id)
    if not row:
        return {"ok": False, "route": "wish", "status": "not_found", "trace_id": trace_id}
    if changed:
        await send_telegram_text(
            chat_id=str(callback.chat_id), text=f"Пожелание №{row['feedback_no']} → {STATUS_WORDS[status]}.",
            bot_token=token, message_thread_id=callback.thread_id,
        )
        if status == "done" and not row.get("notified_at") and await notify_wish_author(row, done_text(row)):
            await mark_notified(tenant.tenant_id, row["feedback_id"])
    return {"ok": True, "route": "wish", "status": status, "changed": changed, "trace_id": trace_id}


async def try_handle_wish_reply(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Reply владельца на пост пожелания в группе — человеку, тем же каналом."""
    if msg.chat_type != "supergroup" or msg.from_bot or msg.user_id != _owner_id() or not wishes_enabled(tenant):
        return None
    reply = ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}
    if not reply.get("message_id") or reply.get("message_id") == msg.thread_id or not (msg.text or "").strip():
        return None
    try:
        row = await find_by_forum_message(tenant.tenant_id, chat_id=msg.chat_id, message_id=int(reply["message_id"]))
    except RuntimeError as exc:
        # Проверки маршрутов идут без пула базы (как в support.py).
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if not row:
        return None
    ok = await notify_wish_author(row, f"Ответ команды WWC на ваше пожелание №{row['feedback_no']}:\n{msg.text.strip()}")
    await send_telegram_text(
        chat_id=str(msg.chat_id), text="→ ответ отправлен" if ok else "Ответ не доставлен: человек закрыл бота.",
        bot_token=current_bot_binding().bot_token, message_thread_id=msg.thread_id,
    )
    return {"ok": ok, "route": "wish_reply", "no": int(row["feedback_no"]), "trace_id": trace_id}
