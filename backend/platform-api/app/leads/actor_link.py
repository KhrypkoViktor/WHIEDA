"""Link a partner's Telegram chat to their lead_actors row by username.

The owner enters only ``@username`` into Partners_Ref (the runtime sync copies it
into ``lead_actors.telegram_username``). The numeric chat id — the thing lead
delivery actually needs — appears the first time the partner writes anything to
the bot. Nobody has to look it up or send it by hand.

Only an empty chat id is filled. An existing link is never overwritten, and a
chat that is already bound to another actor is left alone so the unique
``(tenant_id, telegram_chat_id)`` constraint can never fire.

The numeric user id is completed the same way: a row that already carries this
chat id but no ``telegram_user_id`` gets it from the first message the partner
sends, unless another row already owns that user id — then nothing is merged
and the conflict is logged for an operator.
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


_FILL_USER_ID_SQL = """
update lead_actors
   set telegram_user_id = %(user_id)s,
       updated_at = now()
 where tenant_id = %(tenant_id)s
   and active
   and telegram_chat_id = %(chat_id)s
   and telegram_user_id is null
   and not exists (
       select 1 from lead_actors other
        where other.tenant_id = %(tenant_id)s
          and other.telegram_user_id = %(user_id)s
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


async def fill_lead_actor_user_id(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
) -> str | None:
    """Return the actor whose empty telegram_user_id was just filled, else None."""
    params = {
        "tenant_id": tenant_id,
        "chat_id": str(telegram_chat_id),
        "user_id": int(telegram_user_id),
    }
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _FILL_USER_ID_SQL, params)
        if row:
            actor_id = str(row.get("actor_id") or "")
            logger.info(
                "lead_actor_telegram_user_id_filled",
                extra={"tenant_id": tenant_id, "actor_id": actor_id, "chat_id": chat_ref(telegram_chat_id)},
            )
            return actor_id or None
        pending = await fetch_one(
            conn,
            """
            select actor_id from lead_actors
             where tenant_id = %(tenant_id)s and active
               and telegram_chat_id = %(chat_id)s and telegram_user_id is null
            """,
            params,
        )
    if pending:
        # The chat matches this row but the user id is owned by another actor:
        # one person, two rows. Never merged here; an operator has to decide.
        logger.error(
            "lead_actor_telegram_user_id_conflict",
            extra={
                "tenant_id": tenant_id,
                "actor_id": str(pending.get("actor_id") or ""),
                "chat_id": chat_ref(telegram_chat_id),
            },
        )
    return None


async def merge_anonymous_actor_into_partner(
    tenant_id: str,
    *,
    username: str | None,
    telegram_user_id: int,
    telegram_chat_id: int,
) -> dict[str, str] | None:
    """Fold a bot-created ``telegram:<tenant>:<id>`` row into the partner row with
    the same @username (a person who wrote to the bot before being registered).

    Moves what the person already owns — invite code, attribution (unless the
    partner already has one), bonus ledger, open requests — onto the partner
    row, gives the partner the Telegram ids, and switches the anonymous row off.
    Returns None when there is nothing to merge.
    """
    handle = normalize_username(username)
    if not handle:
        return None
    anonymous_id = f"telegram:{tenant_id}:{int(telegram_user_id)}"
    params = {
        "tenant_id": tenant_id,
        "username": handle,
        "chat_id": str(telegram_chat_id),
        "user_id": int(telegram_user_id),
        "anonymous": anonymous_id,
    }
    async with tenant_connection(tenant_id) as conn:
        pair = await fetch_one(
            conn,
            """
            select p.actor_id as partner_id, a.actor_id as anonymous_id
              from lead_actors p
              join lead_actors a
                on a.tenant_id = p.tenant_id
               and a.actor_id = %(anonymous)s
               and a.active
               and a.telegram_chat_id = %(chat_id)s
             where p.tenant_id = %(tenant_id)s
               and p.active
               and p.actor_id <> a.actor_id
               and p.actor_id not like 'telegram:%%'
               and lower(ltrim(coalesce(p.telegram_username, ''), '@')) = %(username)s
               and coalesce(p.telegram_chat_id, '') = ''
             limit 1
             for update of p, a
            """,
            params,
        )
        if not pair:
            return None
        partner_id = str(pair["partner_id"])
        moves = {"partner": partner_id, "anonymous": anonymous_id, "tenant_id": tenant_id}
        # Attribution: the partner keeps an existing one; otherwise the deep-link one moves.
        await fetch_one(
            conn,
            """
            update partner_referral_attributions set invitee_actor_id = %(partner)s
             where tenant_id = %(tenant_id)s and invitee_actor_id = %(anonymous)s
               and not exists (select 1 from partner_referral_attributions x
                                where x.tenant_id = %(tenant_id)s and x.invitee_actor_id = %(partner)s)
            returning invitee_actor_id
            """,
            moves,
        )
        await fetch_one(
            conn,
            "delete from partner_referral_attributions where tenant_id = %(tenant_id)s and invitee_actor_id = %(anonymous)s returning invitee_actor_id",
            moves,
        )
        # Invite code: only if the partner has no active code of their own.
        await fetch_one(
            conn,
            """
            update referral_invite_codes set inviter_actor_id = %(partner)s
             where tenant_id = %(tenant_id)s and inviter_actor_id = %(anonymous)s and active = true
               and not exists (select 1 from referral_invite_codes x
                                where x.tenant_id = %(tenant_id)s and x.inviter_actor_id = %(partner)s and x.active = true)
            returning invite_code
            """,
            moves,
        )
        await fetch_one(
            conn,
            "update referral_invite_codes set active = false where tenant_id = %(tenant_id)s and inviter_actor_id = %(anonymous)s returning invite_code",
            moves,
        )
        for table in ("partner_bonus_ledger", "partner_bonus_redemption_intents", "partner_site_requests", "partner_renewal_requests"):
            await fetch_one(
                conn,
                f"update {table} set actor_id = %(partner)s where tenant_id = %(tenant_id)s and actor_id = %(anonymous)s returning actor_id",
                moves,
            )
        await fetch_one(
            conn,
            """
            update lead_actors
               set telegram_chat_id = null, telegram_user_id = null, active = false,
                   display_name = display_name || ' (merged into ' || %(partner)s || ')', updated_at = now()
             where tenant_id = %(tenant_id)s and actor_id = %(anonymous)s
            returning actor_id
            """,
            moves,
        )
        await fetch_one(
            conn,
            """
            update lead_actors set telegram_chat_id = %(chat_id)s, telegram_user_id = %(user_id)s, updated_at = now()
             where tenant_id = %(tenant_id)s and actor_id = %(partner)s
            returning actor_id
            """,
            {**params, "partner": partner_id},
        )
    logger.info(
        "lead_actor_anonymous_merged",
        extra={"tenant_id": tenant_id, "actor_id": partner_id, "merged_from": anonymous_id, "chat_id": chat_ref(telegram_chat_id)},
    )
    return {"partner_actor_id": partner_id, "anonymous_actor_id": anonymous_id}
