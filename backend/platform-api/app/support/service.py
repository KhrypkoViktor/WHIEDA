"""Support tunnel storage: tickets between a subscriber and a service administrator.

Neither side ever sees the other's Telegram contact; the bot is the only party
that knows both chat ids. Everything the bot delivers into the admin chat is
recorded with its delivered message id so that a Telegram *Reply* on that
message can be mapped back to the ticket (see app/telegram/support.py).
"""

from __future__ import annotations

from typing import Any, Literal

from app.db import fetch_all, fetch_one, tenant_connection

Direction = Literal["user_to_admin", "admin_to_user", "system"]

_TICKET_COLUMNS = """
ticket_id, ticket_no, tenant_id, channel_code, offer_code, offer_title,
user_telegram_user_id, user_chat_id, user_display, admin_telegram_user_id,
status, created_at, last_message_at, closed_at, closed_by,
forum_chat_id, forum_thread_id
"""


# Same columns, qualified for joins.
_TICKET_COLUMNS_T = ", ".join(f"t.{col.strip()}" for col in _TICKET_COLUMNS.split(","))


def ticket_label(ticket: dict[str, Any]) -> str:
    return f"#S-{int(ticket['ticket_no'])}"


async def open_or_reuse_ticket(
    tenant_id: str,
    *,
    channel_code: str,
    offer_code: str | None,
    offer_title: str | None,
    user_telegram_user_id: int,
    user_chat_id: int,
    user_display: str,
    admin_telegram_user_id: int,
) -> dict[str, Any]:
    """Return the person's open ticket for the channel, creating one if needed.

    ``created`` tells the caller whether this is a new tunnel (announce it to
    both sides) or a second order inside an existing one (just relay).
    """
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            f"""
            select {_TICKET_COLUMNS} from support_tickets
            where tenant_id = %s and channel_code = %s
              and user_telegram_user_id = %s and status = 'open'
            limit 1
            """,
            (tenant_id, channel_code, int(user_telegram_user_id)),
        )
        if existing:
            return {**existing, "created": False}
        created = await fetch_one(
            conn,
            f"""
            insert into support_tickets (
              tenant_id, channel_code, offer_code, offer_title,
              user_telegram_user_id, user_chat_id, user_display, admin_telegram_user_id
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, channel_code, user_telegram_user_id)
              where status = 'open'
            do nothing
            returning {_TICKET_COLUMNS}
            """,
            (
                tenant_id, channel_code, offer_code, offer_title,
                int(user_telegram_user_id), int(user_chat_id), user_display[:240], int(admin_telegram_user_id),
            ),
        )
        if created:
            return {**created, "created": True}
        # Lost a race with a concurrent update from the same person.
        winner = await fetch_one(
            conn,
            f"""
            select {_TICKET_COLUMNS} from support_tickets
            where tenant_id = %s and channel_code = %s
              and user_telegram_user_id = %s and status = 'open'
            limit 1
            """,
            (tenant_id, channel_code, int(user_telegram_user_id)),
        )
    if not winner:
        raise RuntimeError("could not open support ticket")
    return {**winner, "created": False}


async def get_open_ticket_for_user(
    tenant_id: str, *, user_telegram_user_id: int, channel_code: str | None = None
) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            select {_TICKET_COLUMNS} from support_tickets
            where tenant_id = %s and user_telegram_user_id = %s and status = 'open'
              and (%s::text is null or channel_code = %s)
            order by last_message_at desc
            limit 1
            """,
            (tenant_id, int(user_telegram_user_id), channel_code, channel_code),
        )


async def get_ticket(tenant_id: str, *, ticket_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"select {_TICKET_COLUMNS} from support_tickets where tenant_id = %s and ticket_id = %s::uuid",
            (tenant_id, str(ticket_id)),
        )


async def list_open_tickets_for_admin(
    tenant_id: str, *, admin_telegram_user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            select {_TICKET_COLUMNS} from support_tickets
            where tenant_id = %s and admin_telegram_user_id = %s and status = 'open'
              and forum_thread_id is null
            order by last_message_at desc
            limit %s
            """,
            (tenant_id, int(admin_telegram_user_id), max(1, min(limit, 50))),
        )


async def find_ticket_by_admin_message(
    tenant_id: str, *, admin_chat_id: int, message_id: int
) -> dict[str, Any] | None:
    """The ticket whose relayed message the admin just replied to."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            select {_TICKET_COLUMNS_T}
            from support_tickets t
            join support_messages m on m.tenant_id = t.tenant_id and m.ticket_id = t.ticket_id
            where t.tenant_id = %s and m.delivered_chat_id = %s and m.delivered_message_id = %s
            limit 1
            """,
            (tenant_id, int(admin_chat_id), int(message_id)),
        )


async def record_relayed_message(
    tenant_id: str,
    *,
    ticket_id: str,
    direction: Direction,
    text: str | None,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    delivered_chat_id: int | None = None,
    delivered_message_id: int | None = None,
    telegram_file_id: str | None = None,
) -> dict[str, Any]:
    """Store one relayed message; a repeated Telegram update is reported as duplicate."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into support_messages (
              tenant_id, ticket_id, direction, text, telegram_file_id,
              source_chat_id, source_message_id, delivered_chat_id, delivered_message_id
            ) values (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, source_chat_id, source_message_id)
              where source_message_id is not null
            do nothing
            returning message_id
            """,
            (
                tenant_id, str(ticket_id), direction, (text or "")[:4096] or None, telegram_file_id,
                source_chat_id, source_message_id, delivered_chat_id, delivered_message_id,
            ),
        )
        if row:
            await fetch_one(
                conn,
                "update support_tickets set last_message_at = now() where tenant_id = %s and ticket_id = %s::uuid returning ticket_id",
                (tenant_id, str(ticket_id)),
            )
    return {"duplicate": row is None, "message_id": str(row["message_id"]) if row else None}


async def is_duplicate_source(tenant_id: str, *, source_chat_id: int, source_message_id: int) -> bool:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select 1 as seen from support_messages where tenant_id = %s and source_chat_id = %s and source_message_id = %s limit 1",
            (tenant_id, int(source_chat_id), int(source_message_id)),
        )
    return bool(row)


async def close_ticket(
    tenant_id: str, *, ticket_id: str, closed_by: Literal["admin", "user", "system"]
) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            update support_tickets
               set status = 'closed', closed_at = now(), closed_by = %s
             where tenant_id = %s and ticket_id = %s::uuid and status = 'open'
            returning {_TICKET_COLUMNS}
            """,
            (closed_by, tenant_id, str(ticket_id)),
        )


# ----------------------------------------------------------------------------
# Forum group: one topic per ticket (owner, 15.09.2026)
# ----------------------------------------------------------------------------

async def register_forum(
    tenant_id: str, *, binding_id: str, chat_id: int, title: str | None, registered_by: int
) -> dict[str, Any]:
    """Remember the forum group for this bot; a later registration replaces it."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into support_forums (tenant_id, binding_id, chat_id, title, registered_by_telegram_user_id)
            values (%s, %s, %s, %s, %s)
            on conflict (tenant_id, binding_id) do update
              set chat_id = excluded.chat_id, title = excluded.title,
                  registered_by_telegram_user_id = excluded.registered_by_telegram_user_id,
                  updated_at = now()
            returning tenant_id, binding_id, chat_id, title
            """,
            (tenant_id, binding_id, int(chat_id), (title or "")[:200] or None, int(registered_by)),
        )
    return dict(row)


async def get_forum(tenant_id: str, *, binding_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            "select tenant_id, binding_id, chat_id, title, bonuses_thread_id, reports_thread_id from support_forums where tenant_id = %s and binding_id = %s",
            (tenant_id, binding_id),
        )


async def set_forum_service_threads(
    tenant_id: str, *, binding_id: str, bonuses_thread_id: int | None, reports_thread_id: int | None
) -> None:
    """Service topics «Бонусы» / «Отчёты» in the forum (Gemini sales, v10)."""
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update support_forums
               set bonuses_thread_id = coalesce(%s, bonuses_thread_id),
                   reports_thread_id = coalesce(%s, reports_thread_id),
                   updated_at = now()
             where tenant_id = %s and binding_id = %s
            returning binding_id
            """,
            (bonuses_thread_id, reports_thread_id, tenant_id, binding_id),
        )


async def attach_forum_topic(
    tenant_id: str, *, ticket_id: str, forum_chat_id: int, forum_thread_id: int
) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            update support_tickets set forum_chat_id = %s, forum_thread_id = %s
             where tenant_id = %s and ticket_id = %s::uuid
            returning {_TICKET_COLUMNS}
            """,
            (int(forum_chat_id), int(forum_thread_id), tenant_id, str(ticket_id)),
        )


async def list_ticket_messages(tenant_id: str, *, ticket_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The relayed conversation, oldest first (used to replay a ticket into its new topic)."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select direction, text, telegram_file_id, created_at from support_messages
            where tenant_id = %s and ticket_id = %s::uuid and direction in ('user_to_admin', 'admin_to_user')
            order by created_at
            limit %s
            """,
            (tenant_id, str(ticket_id), max(1, min(limit, 200))),
        )


async def find_ticket_by_forum_thread(
    tenant_id: str, *, forum_chat_id: int, forum_thread_id: int
) -> dict[str, Any] | None:
    """The ticket whose topic the administrator just wrote in (open or closed)."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            select {_TICKET_COLUMNS} from support_tickets
            where tenant_id = %s and forum_chat_id = %s and forum_thread_id = %s
            order by created_at desc
            limit 1
            """,
            (tenant_id, int(forum_chat_id), int(forum_thread_id)),
        )
