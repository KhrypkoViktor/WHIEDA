"""Имя человека и его статус для сообщений воронки (ТЗ 28: подпись в Zoom, имена в итоге партнёру).

Имя — то, что человек написал в заявке (``website_leads.name``), иначе имя его актёра в боте (``lead_actors.display_name``,
для Max — через ``lead_actor_channels``). Имя очищается от ссылок и ограничивается по длине (``plain_no_links``).
Запросы идут под своей точкой отката: сбой одного из них (нет колонки, нет строки) не обрывает транзакцию вызывающего.
"""

from __future__ import annotations

import logging
from typing import Any

from app.bot_stages.service import STAGE_PARTNER_WHIEDA, STAGE_PARTNER_WWC, resolve_stage
from app.db import fetch_one
from app.leads.meeting_leads import plain_no_links

logger = logging.getLogger("whieda.funnel.people")

NAME_MAX = 40

_LEAD_NAME_SQL = "select name from website_leads where tenant_id = %s and lead_id = %s::uuid limit 1"

# Тот же поиск актёра, что у ``guest._PARTNER_SQL``: по id в Telegram или по каналу (Max).
_ACTOR_NAME_SQL = """
select display_name from lead_actors
where tenant_id = %(tenant)s and actor_id in (
    select actor_id from lead_actors
    where tenant_id = %(tenant)s and %(channel)s = 'telegram' and telegram_user_id = %(user)s
    union
    select actor_id from lead_actor_channels
    where tenant_id = %(tenant)s and channel = %(channel)s and channel_user_id = %(user_text)s
)
order by (display_name is null), actor_id
limit 1
"""


async def person_name(
    conn: Any, tenant_id: str, channel: str, channel_user_id: int, *, lead_id: str | None = None, lead_name: str | None = None
) -> str:
    """Имя человека или пустая строка. ``lead_name`` — имя из заявки, если вызывающий его уже прочитал."""
    name = plain_no_links(lead_name, NAME_MAX) if lead_name else ""
    if name:
        return name
    try:
        if lead_id:
            async with conn.transaction():
                row = await fetch_one(conn, _LEAD_NAME_SQL, (tenant_id, str(lead_id)))
            name = plain_no_links((row or {}).get("name"), NAME_MAX)
        if not name:
            async with conn.transaction():
                row = await fetch_one(
                    conn,
                    _ACTOR_NAME_SQL,
                    {"tenant": tenant_id, "channel": channel, "user": int(channel_user_id), "user_text": str(int(channel_user_id))},
                )
            name = plain_no_links((row or {}).get("display_name"), NAME_MAX)
    except Exception as exc:  # noqa: BLE001 — имя нужно для красоты, не для доставки
        logger.warning("person_name_failed", extra={"channel": channel, "error_class": type(exc).__name__})
        return ""
    return name


async def is_partner_person(conn: Any, tenant_id: str, channel: str, channel_user_id: int) -> bool:
    """Партнёр (WHIEDA или WWC) по статусу бота; сбой чтения — не партнёр (подпись «Гость …» безопаснее)."""
    try:
        async with conn.transaction():
            stage = await resolve_stage(conn, tenant_id, channel, int(channel_user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("person_stage_failed", extra={"channel": channel, "error_class": type(exc).__name__})
        return False
    return stage in (STAGE_PARTNER_WHIEDA, STAGE_PARTNER_WWC)
