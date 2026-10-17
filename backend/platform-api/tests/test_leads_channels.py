"""Каналы связи (app/leads/channels.py): слияние человека с партнёром по @username — только в Telegram.

17.10.2026: username в Max и в Telegram — разные пространства имён. Чужой человек ставил в Max ник партнёра,
писал боту — и его Max-id прилипал к actor партнёра. Теперь Max (и любой не-telegram канал) получает свой
actor ``<channel>:<tenant>:<id>``; к партнёру он привязывается только подтверждёнными путями.
База — подделка: проверяем решение модуля (ходил ли он искать партнёра по нику), не SQL. SQL — в
tests/test_max_channel_postgres.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.leads import channels


class FakeDb:
    def __init__(self) -> None:
        self.partners_by_username = {"partner_handle": "owner-made"}
        self.actors: set[str] = set()
        self.channel_rows: dict[tuple[str, str], str] = {}
        self.username_lookups: list[str] = []

    async def fetch_one(self, conn, query: str, params: tuple = ()):  # noqa: ARG002
        q = " ".join(query.split())
        if q.startswith("select c.actor_id"):  # find_actor_by_channel
            actor = self.channel_rows.get((params[1], params[2]))
            return {"actor_id": actor, "channel_chat_id": None, "display_name": ""} if actor else None
        if q.startswith("select la.actor_id from lead_actors la"):  # слияние по нику
            self.username_lookups.append(params[1])
            actor = self.partners_by_username.get(params[1])
            return {"actor_id": actor} if actor else None
        if q.startswith("insert into lead_actors"):
            self.actors.add(params[0])
            return {"actor_id": params[0]}
        if q.startswith("insert into lead_actor_channels"):
            self.channel_rows[(params[2], params[3])] = params[1]
            return {"actor_id": params[1]}
        raise AssertionError(f"unexpected query: {q[:80]}")


@pytest.fixture
def db():
    fake = FakeDb()

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    with patch.object(channels, "tenant_connection", connection), patch.object(channels, "fetch_one", fake.fetch_one):
        yield fake


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["partner_handle", "Partner_Handle", "@partner_handle"])
async def test_max_user_with_the_partners_username_gets_a_separate_actor(db, username):
    actor_id = await channels.ensure_channel_actor(
        "whieda", channel="max", channel_user_id=9002, channel_chat_id=9102, username=username, display_name=username
    )
    assert actor_id == "max:whieda:9002" and actor_id != "owner-made"
    assert db.username_lookups == [], "the username is never used to find a partner for Max"
    assert db.actors == {"max:whieda:9002"}
    assert db.channel_rows == {("max", "9002"): "max:whieda:9002"}, "nothing sticks to the partner's actor"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["max", "vk", "whatsapp"])
async def test_no_username_fold_outside_telegram(db, channel):
    actor_id = await channels._fold_or_create(
        object(), "whieda", channel=channel, channel_user_id=7, username="partner_handle", display_name="X"
    )
    assert actor_id == f"{channel}:whieda:7" and db.username_lookups == []


@pytest.mark.asyncio
async def test_telegram_username_still_folds_into_the_partner_made_by_the_owner(db):
    actor_id = await channels._fold_or_create(
        object(), "whieda", channel="telegram", channel_user_id=7, username="Partner_Handle", display_name="X"
    )
    assert actor_id == "owner-made" and db.username_lookups == ["partner_handle"]
    assert db.actors == set(), "folded into the existing actor, no new one"


@pytest.mark.asyncio
async def test_max_referral_start_with_the_inviters_username_is_not_a_self_referral(db):
    """Нельзя прикинуться пригласившим, поставив его ник, — и нельзя им стать."""
    invite = {"invite_code": "proofinvite_code1", "inviter_actor_id": "owner-made"}
    real_fetch = db.fetch_one

    async def with_invite(conn, query, params=()):
        if " ".join(query.split()).startswith("select invite_code, inviter_actor_id"):
            return invite
        if " ".join(query.split()).startswith("insert into partner_referral_attribution"):
            return {"invitee_actor_id": params[1], "audit_id": 1}
        return await real_fetch(conn, query, params)

    with patch.object(channels, "fetch_one", with_invite):
        result = await channels.accept_channel_referral_start(
            "whieda", channel="max", channel_user_id=50001, channel_chat_id=1, username="partner_handle",
            display_name="@partner_handle", invite_code="proofinvite_code1",
        )
    assert result.status == "attributed" and result.actor_id == "max:whieda:50001"
