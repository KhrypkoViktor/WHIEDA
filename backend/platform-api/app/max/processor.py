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


async def process_max_event(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    if event.kind == "start":
        return await handle_max_start(tenant, event, trace_id)
    return await handle_max_message(tenant, event, trace_id)
