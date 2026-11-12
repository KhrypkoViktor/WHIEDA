"""Партнёру: «ваш человек записался» после записи гостя в боте (ТЗ 28, 6.1).

Адресат — партнёр, чья ссылка закреплена за человеком (владелец его заявки, иначе пригласивший по первому касанию;
``app/funnel/network.py: guest_inviter_owner``). Гость без партнёра (реклама) никому не пишет. Сообщение ставится в
``platform_outbox`` (отправляет боевой воркер, как «Новая запись на Живую встречу» с сайта) с кнопкой «Открыть карточку»,
если у человека есть карточка CRM и CRM у партнёра открыта. Ключ ``mnew:<запись>``: повторное нажатие второго сообщения
не даёт. Любой сбой — в журнал, запись уже сохранена. Имён и телефонов в логах нет.

Запись с сайта (анкета на странице «Живая встреча») по-прежнему идёт старым сообщением из ``app/leads/meeting_leads.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.crm.service import safe_error
from app.funnel import texts
from app.funnel.network import guest_inviter_owner
from app.funnel.people import person_name
from app.jobs.outbox import enqueue_outbox_event
from app.leads.meeting_leads import card_button_url, notify_binding_id, owner_chat
from app.meetings.service import format_msk
from app.settings import get_settings

logger = logging.getLogger("whieda.funnel.partner_notice")

EVENT_TYPE = "meeting_guest_registered"


def notice_key(registration_id: int) -> str:
    return f"mnew:{int(registration_id)}"


def notice_text(name: str, starts_at: datetime, *, now: datetime | None = None) -> str:
    return texts.PARTNER_GUEST_REGISTERED.format(
        name=name or texts.PARTNER_GUEST_NO_NAME, when=format_msk(starts_at, now=now)
    )


async def notify_partner_registered(
    conn: Any,
    tenant_id: str,
    *,
    channel: str,
    channel_user_id: int,
    registration_id: int,
    starts_at: datetime,
    lead: Any = None,
    now: datetime | None = None,
) -> bool:
    """Поставить партнёру сообщение о записи его человека. True — поставлено; нет партнёра, чата, бота процесса или сбой — False."""
    try:
        async with conn.transaction():
            owner = await guest_inviter_owner(
                conn, tenant_id, channel, int(channel_user_id), getattr(lead, "assigned_owner_id", None)
            )
            if not owner or owner == str(get_settings().platform_organic_owner_id or ""):
                return False
            chat = await owner_chat(conn, tenant_id, owner)
            binding_id = await notify_binding_id(conn, tenant_id) if chat else None
            if chat is None or not binding_id:
                return False
            chat_id, telegram_user_id = chat
            name = await person_name(
                conn, tenant_id, channel, int(channel_user_id),
                lead_id=getattr(lead, "lead_id", None), lead_name=getattr(lead, "name", None),
            )
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": notice_text(name, starts_at, now=now),
                "binding_id": binding_id,
            }
            card_url = await card_button_url(conn, tenant_id, telegram_user_id, getattr(lead, "crm_card", None))
            if card_url:
                payload["reply_markup"] = {"inline_keyboard": [[{"text": texts.OPEN_CARD_BUTTON, "url": card_url}]]}
                payload["site_login_user_id"] = telegram_user_id
            row = await enqueue_outbox_event(
                conn,
                tenant_id=tenant_id,
                event_type=EVENT_TYPE,
                idempotency_key=notice_key(registration_id),
                payload=payload,
                due_at=now or datetime.now(timezone.utc),
            )
            return bool(row and row.get("created"))
    except Exception as exc:  # noqa: BLE001 — запись гостя важнее уведомления
        logger.warning("partner_notice_failed", extra={"channel": channel, **safe_error(exc)})
        return False
