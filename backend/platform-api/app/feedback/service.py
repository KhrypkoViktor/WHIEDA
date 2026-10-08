"""Пожелания (V25, 08.10.2026): все — партнёры и покупатели, Telegram и Max.

Ожидание («awaiting») — человек нажал «Пожелание», бот ждёт следующее сообщение
не дольше AWAIT_MINUTES; дальше — new → in_progress → done / declined.
"""

from __future__ import annotations

from typing import Any

from app.db import fetch_one, tenant_connection

AWAIT_MINUTES = 30
STATUS_WORDS = {"new": "новое", "in_progress": "в работе", "done": "сделано", "declined": "не будем"}
DECISIONS = {"prog": "in_progress", "done": "done", "no": "declined"}

_COLUMNS = """
feedback_id::text as feedback_id, feedback_no, channel, user_id, chat_id, user_display, ref_code, status,
text, file_id, media_kind, source, forum_chat_id, forum_message_id, created_at, decided_at, notified_at
"""


async def partner_ref(tenant_id: str, telegram_user_id: int) -> str | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select rp.ref_code from referral_profiles rp
            join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
            where rp.tenant_id = %s and rp.enabled = true
              and (la.telegram_user_id = %s or la.telegram_chat_id = %s)
            order by rp.created_at limit 1
            """,
            (tenant_id, int(telegram_user_id), str(telegram_user_id)),
        )
    return str(row["ref_code"]) if row else None


async def start_wish(
    tenant_id: str, *, channel: str, user_id: int, chat_id: int, display: str | None, ref_code: str | None
) -> dict[str, Any]:
    """«Пожелание»: ждём следующее сообщение. Повторное нажатие — то же ожидание, заново."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            insert into partner_feedback (tenant_id, channel, user_id, chat_id, user_display, ref_code)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, channel, user_id) where status = 'awaiting'
            do update set chat_id = excluded.chat_id, user_display = excluded.user_display,
                          ref_code = excluded.ref_code, created_at = now(), updated_at = now()
            returning {_COLUMNS}
            """,
            (tenant_id, channel, int(user_id), int(chat_id), (display or "")[:240] or None, ref_code),
        )


async def waiting_wish(tenant_id: str, *, channel: str, user_id: int) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            select {_COLUMNS} from partner_feedback
            where tenant_id = %s and channel = %s and user_id = %s and status = 'awaiting'
              and created_at > now() - make_interval(mins => %s)
            """,
            (tenant_id, channel, int(user_id), AWAIT_MINUTES),
        )


async def submit_wish(
    tenant_id: str, feedback_id: str, *, text: str | None, file_id: str | None, media_kind: str | None,
    display: str | None = None,
) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            update partner_feedback
               set status = 'new', text = %s, file_id = %s, media_kind = %s,
                   user_display = coalesce(%s, user_display), created_at = now(), updated_at = now()
             where tenant_id = %s and feedback_id = %s::uuid and status = 'awaiting'
            returning {_COLUMNS}
            """,
            ((text or "").strip()[:4000] or None, file_id, media_kind, (display or "")[:240] or None, tenant_id, feedback_id),
        )


async def add_wish(
    tenant_id: str, *, channel: str, user_id: int, chat_id: int, display: str | None, ref_code: str | None,
    text: str, source: str = "support",
) -> dict[str, Any]:
    """Пожелание, пришедшее не кнопкой (из обращения в поддержку) — сразу «новое»."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            insert into partner_feedback (tenant_id, channel, user_id, chat_id, user_display, ref_code, status, text, source)
            values (%s, %s, %s, %s, %s, %s, 'new', %s, %s)
            returning {_COLUMNS}
            """,
            (tenant_id, channel, int(user_id), int(chat_id), display, ref_code, text.strip()[:4000], source),
        )


async def set_forum_message(tenant_id: str, feedback_id: str, *, chat_id: int, message_id: int | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update partner_feedback set forum_chat_id = %s, forum_message_id = %s, updated_at = now()
            where tenant_id = %s and feedback_id = %s::uuid returning feedback_id
            """,
            (int(chat_id), message_id, tenant_id, feedback_id),
        )


async def decide(tenant_id: str, feedback_id: str, *, status: str, by: int) -> tuple[dict[str, Any] | None, bool]:
    """Решение владельца. Возвращает (пожелание, изменилось ли)."""
    async with tenant_connection(tenant_id) as conn:
        changed = await fetch_one(
            conn,
            f"""
            update partner_feedback set status = %s, decided_by = %s, decided_at = now(), updated_at = now()
            where tenant_id = %s and feedback_id = %s::uuid and status in ('new', 'in_progress', 'done', 'declined')
              and status <> %s
            returning {_COLUMNS}
            """,
            (status, int(by), tenant_id, feedback_id, status),
        )
        if changed:
            return changed, True
        current = await fetch_one(
            conn, f"select {_COLUMNS} from partner_feedback where tenant_id = %s and feedback_id = %s::uuid", (tenant_id, feedback_id)
        )
    return current, False


async def mark_notified(tenant_id: str, feedback_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            "update partner_feedback set notified_at = now() where tenant_id = %s and feedback_id = %s::uuid returning feedback_id",
            (tenant_id, feedback_id),
        )


async def find_by_forum_message(tenant_id: str, *, chat_id: int, message_id: int) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"select {_COLUMNS} from partner_feedback where tenant_id = %s and forum_chat_id = %s and forum_message_id = %s",
            (tenant_id, int(chat_id), int(message_id)),
        )


async def set_wishes_thread(tenant_id: str, *, binding_id: str, kind: str, thread_id: int | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update support_forums set wishes_thread_id = %s, updated_at = now()
            where tenant_id = %s and binding_id = %s and kind = %s returning binding_id
            """,
            (thread_id, tenant_id, binding_id, kind),
        )


async def wishes_thread(tenant_id: str, *, binding_id: str, kind: str) -> tuple[int, int | None] | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select chat_id, wishes_thread_id from support_forums where tenant_id = %s and binding_id = %s and kind = %s",
            (tenant_id, binding_id, kind),
        )
    if not row:
        return None
    return int(row["chat_id"]), (int(row["wishes_thread_id"]) if row.get("wishes_thread_id") else None)
