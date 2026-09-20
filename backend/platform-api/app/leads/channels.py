"""Каналы связи с человеком (lead_actor_channels, V11): один actor — много мессенджеров.

Telegram пока живёт в колонках lead_actors.telegram_* (и зеркалится сюда бэкфиллом);
Max и следующие каналы — только здесь. actor_id для нового человека из канала:
``<channel>:<tenant>:<channel_user_id>`` — по образцу telegram:*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.db import fetch_one, tenant_connection
from app.referral_bonus.service import _INVITE_CODE_RE  # единый формат кода приглашения

logger = logging.getLogger("whieda.leads.channels")

Channel = Literal["telegram", "max", "vk", "whatsapp"]


@dataclass
class ChannelStartResult:
    status: Literal["attributed", "already_registered", "invalid", "self_referral"]
    actor_id: str | None = None
    inviter_actor_id: str | None = None


def channel_actor_id(tenant_id: str, channel: str, channel_user_id: int | str) -> str:
    return f"{channel}:{tenant_id}:{channel_user_id}"


async def find_actor_by_channel(conn: Any, tenant_id: str, channel: str, channel_user_id: int | str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select c.actor_id, c.channel_chat_id, la.display_name
        from lead_actor_channels c join lead_actors la on la.actor_id = c.actor_id
        where c.tenant_id = %s and c.channel = %s and c.channel_user_id = %s
        limit 1
        """,
        (tenant_id, channel, str(channel_user_id)),
    )


async def upsert_channel(
    conn: Any,
    *,
    tenant_id: str,
    actor_id: str,
    channel: str,
    channel_user_id: int | str,
    channel_chat_id: int | str | None,
    username: str | None,
    display_name: str,
) -> None:
    await fetch_one(
        conn,
        """
        insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, channel_chat_id, username, display_name)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (tenant_id, channel, channel_user_id) do update
          set channel_chat_id = coalesce(excluded.channel_chat_id, lead_actor_channels.channel_chat_id),
              username = coalesce(excluded.username, lead_actor_channels.username),
              display_name = excluded.display_name,
              updated_at = now()
        returning actor_id
        """,
        (
            tenant_id, actor_id, channel, str(channel_user_id),
            None if channel_chat_id is None else str(channel_chat_id), username, display_name,
        ),
    )


async def ensure_channel_actor(
    tenant_id: str,
    *,
    channel: str,
    channel_user_id: int | str,
    channel_chat_id: int | str | None,
    username: str | None,
    display_name: str,
) -> str:
    """Человек из канала без приглашения: actor есть — вернуть, нет — создать (без атрибуции)."""
    async with tenant_connection(tenant_id) as conn:
        found = await find_actor_by_channel(conn, tenant_id, channel, channel_user_id)
        if found:
            actor_id = str(found["actor_id"])
        else:
            actor_id = await _fold_or_create(
                conn, tenant_id, channel=channel, channel_user_id=channel_user_id, username=username, display_name=display_name
            )
        await upsert_channel(
            conn, tenant_id=tenant_id, actor_id=actor_id, channel=channel, channel_user_id=channel_user_id,
            channel_chat_id=channel_chat_id, username=username, display_name=display_name,
        )
        return actor_id


async def _fold_or_create(
    conn: Any, tenant_id: str, *, channel: str, channel_user_id: int | str, username: str | None, display_name: str
) -> str:
    """Партнёр, заведённый владельцем с тем же @username, — это тот же человек (как fix 55cf71d для Telegram)."""
    if username:
        partner = await fetch_one(
            conn,
            """
            select la.actor_id from lead_actors la
            where la.tenant_id = %s
              and lower(ltrim(coalesce(la.telegram_username, ''), '@')) = %s
              and not exists (
                select 1 from lead_actor_channels c
                where c.tenant_id = la.tenant_id and c.actor_id = la.actor_id and c.channel = %s
              )
            limit 1
            """,
            (tenant_id, username.lower().lstrip("@"), channel),
        )
        if partner:
            return str(partner["actor_id"])
    actor_id = channel_actor_id(tenant_id, channel, channel_user_id)
    await fetch_one(
        conn,
        """
        insert into lead_actors (actor_id, tenant_id, display_name)
        values (%s, %s, %s)
        on conflict (actor_id) do nothing
        returning actor_id
        """,
        (actor_id, tenant_id, display_name),
    )
    return actor_id


async def accept_channel_referral_start(
    tenant_id: str,
    *,
    channel: str,
    channel_user_id: int | str,
    channel_chat_id: int | str | None,
    username: str | None,
    display_name: str,
    invite_code: str,
) -> ChannelStartResult:
    """Первое касание из канала по deep link: как accept_referral_start, но через lead_actor_channels."""
    if not _INVITE_CODE_RE.fullmatch(str(invite_code or "")):
        return ChannelStartResult(status="invalid")
    async with tenant_connection(tenant_id) as conn:
        invite = await fetch_one(
            conn,
            """
            select invite_code, inviter_actor_id from referral_invite_codes
            where tenant_id = %s and invite_code = %s and active = true
            limit 1
            """,
            (tenant_id, invite_code),
        )
        if not invite:
            return ChannelStartResult(status="invalid")
        inviter_actor_id = str(invite["inviter_actor_id"])

        found = await find_actor_by_channel(conn, tenant_id, channel, channel_user_id)
        if found:
            await upsert_channel(
                conn, tenant_id=tenant_id, actor_id=found["actor_id"], channel=channel, channel_user_id=channel_user_id,
                channel_chat_id=channel_chat_id, username=username, display_name=display_name,
            )
            return ChannelStartResult(status="already_registered", actor_id=str(found["actor_id"]), inviter_actor_id=inviter_actor_id)

        actor_id = await _fold_or_create(
            conn, tenant_id, channel=channel, channel_user_id=channel_user_id, username=username, display_name=display_name
        )
        if actor_id == inviter_actor_id:
            return ChannelStartResult(status="self_referral", actor_id=actor_id)
        await upsert_channel(
            conn, tenant_id=tenant_id, actor_id=actor_id, channel=channel, channel_user_id=channel_user_id,
            channel_chat_id=channel_chat_id, username=username, display_name=display_name,
        )
        attribution = await fetch_one(
            conn,
            """
            insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
            values (%s, %s, %s, %s, %s)
            on conflict (tenant_id, invitee_actor_id) do nothing
            returning invitee_actor_id
            """,
            (tenant_id, actor_id, inviter_actor_id, invite_code, f"{channel}_deeplink"),
        )
        if not attribution:
            return ChannelStartResult(status="already_registered", actor_id=actor_id, inviter_actor_id=inviter_actor_id)
        await fetch_one(
            conn,
            """
            insert into partner_referral_attribution_audit (tenant_id, invitee_actor_id, new_inviter_actor_id, action, reason)
            values (%s, %s, %s, 'created', %s)
            returning audit_id
            """,
            (tenant_id, actor_id, inviter_actor_id, f"{channel}_deeplink"),
        )
        return ChannelStartResult(status="attributed", actor_id=actor_id, inviter_actor_id=inviter_actor_id)
