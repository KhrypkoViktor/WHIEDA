"""Link a partner's Telegram chat to their lead_actors row by username.

The owner enters only ``@username`` into Partners_Ref (the runtime sync copies it
into ``lead_actors.telegram_username``). The numeric chat id — the thing lead
delivery actually needs — appears the first time the partner writes anything to
the bot. Nobody has to look it up or send it by hand.

Only an empty chat id is filled. An existing link is never overwritten, and a
chat that is already bound to another actor is left alone so the unique
``(tenant_id, telegram_chat_id)`` constraint can never fire.
"""

from __future__ import annotations

import logging

from app.db import fetch_one, tenant_connection
from app.telegram.log_safe import chat_ref

logger = logging.getLogger(__name__)

_LINK_SQL = """
update lead_actors
   set telegram_chat_id = %(chat_id)s,
       telegram_user_id = case
           when exists (
               select 1 from lead_actors u
                where u.tenant_id = %(tenant_id)s
                  and u.telegram_user_id = %(user_id)s
                  and u.actor_id <> lead_actors.actor_id
           ) then lead_actors.telegram_user_id
           else %(user_id)s
       end,
       updated_at = now()
 where tenant_id = %(tenant_id)s
   and active
   and lower(ltrim(coalesce(telegram_username, ''), '@')) = %(username)s
   and coalesce(telegram_chat_id, '') = ''
   and not exists (
       select 1 from lead_actors c
        where c.tenant_id = %(tenant_id)s
          and c.telegram_chat_id = %(chat_id)s
   )
returning actor_id
"""


def normalize_username(username: str | None) -> str:
    return str(username or "").strip().lstrip("@").strip().lower()


async def link_lead_actor_by_username(
    tenant_id: str,
    *,
    username: str | None,
    telegram_user_id: int,
    telegram_chat_id: int,
) -> str | None:
    """Return the linked actor_id, or None when nothing needed linking."""
    handle = normalize_username(username)
    if not handle:
        return None
    params = {
        "tenant_id": tenant_id,
        "username": handle,
        "chat_id": str(telegram_chat_id),
        "user_id": int(telegram_user_id),
    }
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _LINK_SQL, params)
    if not row:
        return None
    actor_id = str(row.get("actor_id") or "")
    logger.info(
        "lead_actor_telegram_linked",
        extra={
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "chat_id": chat_ref(telegram_chat_id),
        },
    )
    return actor_id or None
