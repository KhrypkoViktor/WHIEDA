"""Обработка событий Max: старт по deep link (ref_… / site_…), вопросы советнику.

Первая версия канала (20.09.2026): человек приходит по ссылке партнёра
https://max.ru/<bot>?start=ref_<код>, закрепляется за пригласившим, видит карточку
«вы пришли от …» с сайтом партнёра, дальше задаёт вопросы по продуктам — отвечает
тот же советник, что и в Telegram. Кабинет партнёра, оплаты и заказ сайта — только
в Telegram (следующие версии).
"""

from __future__ import annotations

import logging
from typing import Any

from app.advisor.service import handle_structured_query
from app.leads.channels import accept_channel_referral_start, ensure_channel_actor
from app.max.client import send_max_text
from app.max.update_parser import MaxEvent
from app.referral_bonus.service import inviter_card, parse_referral_start_token, parse_site_start_token
from app.telegram.modes import should_deliver_telegram_response
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.max")

CHANNEL = "max"

WELCOME_PLAIN = (
    "Здравствуйте! Это бот WWC — продукты и технологии WHIEDA.\n"
    "Напишите вопрос по товару (например, «стельки с анионами» или «активатор клеток») — отвечу по документам."
)


def _greeting(status: str, inviter: Any) -> str:
    who = ""
    if inviter and getattr(inviter, "display_name", ""):
        who = f"Вы пришли от партнёра: {inviter.display_name}."
        if getattr(inviter, "site_url", ""):
            who += f"\nСайт партнёра: {inviter.site_url}"
    lines = {
        "attributed": ["Приглашение сохранено.", who],
        "already_registered": ["Мы уже знакомы — пригласивший не меняется.", who],
        "invalid": ["Ссылка-приглашение недействительна или больше не активна."],
        "self_referral": ["Свою ссылку нельзя использовать для себя."],
    }[status]
    lines.append("")
    lines.append("Напишите вопрос по товару — отвечу по официальным документам WHIEDA.")
    return "\n".join(line for line in lines if line is not None)


async def _reply(event: MaxEvent, text: str) -> None:
    if event.chat_id:
        await send_max_text(chat_id=event.chat_id, text=text)
    else:
        await send_max_text(user_id=event.user_id, text=text)


async def handle_max_start(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    payload = event.text
    invite_code = parse_referral_start_token(payload)
    if invite_code is None:
        invite_code = parse_site_start_token(payload)
    if invite_code is None or not invite_code:
        # Старт без приглашения: человек есть, атрибуции нет.
        await ensure_channel_actor(
            tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
            username=event.username, display_name=event.display_name,
        )
        await _reply(event, WELCOME_PLAIN)
        return {"ok": True, "route": "max_start", "status": "plain", "trace_id": trace_id}
    result = await accept_channel_referral_start(
        tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
        username=event.username, display_name=event.display_name, invite_code=invite_code,
    )
    inviter = await inviter_card(tenant.tenant_id, result.inviter_actor_id) if result.inviter_actor_id else None
    await _reply(event, _greeting(result.status, inviter))
    logger.info("max_start", extra={"trace_id": trace_id, "status": result.status, "user_ref": str(event.user_id)[-4:]})
    return {"ok": result.status == "attributed", "route": "max_start", "status": result.status, "trace_id": trace_id}


async def handle_max_message(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    await ensure_channel_actor(
        tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
        username=event.username, display_name=event.display_name,
    )
    if not event.text:
        return {"ok": True, "route": "max_message", "status": "empty", "trace_id": trace_id}
    wish = await _try_max_wish(tenant, event, trace_id)
    if wish is not None:
        return wish
    body = {
        "session": f"max:{event.chat_id or event.user_id}",
        "question": event.text,
        "surface": "max",
        "country": "RU",
        "language": "ru",
    }
    core_response = await handle_structured_query(tenant, body, trace_id)
    if should_deliver_telegram_response(core_response):
        await _reply(event, str(core_response.get("answer_text") or ""))
    return {"ok": True, "route": "max_message", "answer_mode": core_response.get("answer_mode"), "trace_id": trace_id}


async def handle_max_chat_membership(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    """Бота добавили в чат Max (или убрали): запоминаем — туда пойдут посты из
    Telegram-канала; владельцу — строка в Telegram, чтобы видел, куда подключились."""
    from app.max.client import get_chat
    from app.max.crosspost import remember_max_chat
    from app.settings import get_settings
    from app.telegram.bindings import resolve_bot_binding_context
    from app.telegram.delivery import send_telegram_text

    added = event.kind == "chat_added"
    info = await get_chat(int(event.chat_id)) if added else {}
    title = str(info.get("title") or "").strip() or None
    row = await remember_max_chat(
        tenant.tenant_id, chat_id=int(event.chat_id), title=title, chat_type=str(info.get("type") or event.text),
        is_channel=event.text == "channel", added_by=event.user_id or None, active=added,
    )
    owner = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if owner.isdigit() and binding is not None:
        name = (row or {}).get("title") or title or str(event.chat_id)
        text = (
            f"Бот WWC добавлен в Max: «{name}». Посты из Telegram-канала теперь повторяются и здесь."
            if added else f"Бота WWC убрали из Max: «{name}». Посты туда больше не идут."
        )
        await send_telegram_text(chat_id=owner, text=text, bot_token=binding.bot_token)
    logger.info("max_chat_membership", extra={"trace_id": trace_id, "added": added, "chat_id": event.chat_id})
    return {"ok": True, "route": "max_chat", "added": added, "chat_id": event.chat_id}


async def _try_max_wish(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any] | None:
    """«Пожелание» в Max (V25): как в Telegram — слово, потом само пожелание текстом.
    Пост владельцу уходит в Telegram, в тему «💡 Пожелания»."""
    from app.feedback.service import start_wish, submit_wish, waiting_wish
    from app.telegram.bindings import binding_context_scope, resolve_bot_binding_context
    from app.telegram.feedback import WISH_PROMPT, deliver_wish, is_wish_request, thanks_text

    if not tenant.entitlements.get("site_support", False):
        return None
    if is_wish_request(event.text):
        await start_wish(
            tenant.tenant_id, channel=CHANNEL, user_id=event.user_id, chat_id=event.chat_id or event.user_id,
            display=event.display_name, ref_code=None,
        )
        await _reply(event, WISH_PROMPT)
        return {"ok": True, "route": "max_wish", "status": "awaiting", "trace_id": trace_id}
    waiting = await waiting_wish(tenant.tenant_id, channel=CHANNEL, user_id=event.user_id)
    if not waiting:
        return None
    row = await submit_wish(tenant.tenant_id, waiting["feedback_id"], text=event.text, file_id=None, media_kind=None)
    if not row:
        return None
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if binding is not None:
        with binding_context_scope(binding):
            await deliver_wish(tenant, row)
    await _reply(event, thanks_text(int(row["feedback_no"])))
    return {"ok": True, "route": "max_wish", "status": "new", "no": int(row["feedback_no"]), "trace_id": trace_id}


async def process_max_event(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    if event.kind in {"chat_added", "chat_removed"}:
        return await handle_max_chat_membership(tenant, event, trace_id)
    if event.kind == "start":
        return await handle_max_start(tenant, event, trace_id)
    return await handle_max_message(tenant, event, trace_id)
