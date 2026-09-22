"""Max deep link на живой схеме: человек из Max закрепляется за партнёром через
lead_actor_channels, второй клик не меняет пригласившего, партнёр с тем же
@username сливается с уже заведённой владельцем записью."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_max_start_attributes_and_folds_on_live_schema():
    with temporary_database("whieda_max_channel") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_username, telegram_chat_id, telegram_user_id)
                values ('proof-inviter', 'whieda', 'Proof inviter', 'proof_inviter', '50001', 50001),
                       ('owner-made', 'whieda', 'Партнёр без чата', 'same_handle', null, null);
                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values ('whieda', 'proofinvite_code1', 'proof-inviter');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_one, tenant_connection
            from app.leads.channels import accept_channel_referral_start, ensure_channel_actor

            async def value(query: str, params: tuple = ()) -> object:
                async with tenant_connection("whieda") as conn:
                    row = await fetch_one(conn, query, params)
                return dict(row) if row else None

            first = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=9001, channel_chat_id=9101,
                username="inna_max", display_name="@inna_max", invite_code="proofinvite_code1",
            )
            assert first.status == "attributed" and first.actor_id == "max:whieda:9001"
            channel = await value(
                "select actor_id, channel_chat_id, username from lead_actor_channels where channel = 'max' and channel_user_id = '9001'"
            )
            assert channel == {"actor_id": "max:whieda:9001", "channel_chat_id": "9101", "username": "inna_max"}
            attribution = await value(
                "select inviter_actor_id, source from partner_referral_attributions where invitee_actor_id = 'max:whieda:9001'"
            )
            assert attribution == {"inviter_actor_id": "proof-inviter", "source": "max_deeplink"}

            again = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=9001, channel_chat_id=9101,
                username="inna_max", display_name="@inna_max", invite_code="proofinvite_code1",
            )
            assert again.status == "already_registered"

            # Партнёр, заведённый владельцем с @same_handle, пишет из Max — это тот же человек.
            folded = await ensure_channel_actor(
                "whieda", channel="max", channel_user_id=9002, channel_chat_id=9102, username="Same_Handle", display_name="@Same_Handle"
            )
            assert folded == "owner-made"
            self_ref = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=50001, channel_chat_id=1, username="proof_inviter",
                display_name="@proof_inviter", invite_code="proofinvite_code1",
            )
            assert self_ref.status == "self_referral"
            bad = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=9003, channel_chat_id=None, username=None,
                display_name="Кто-то", invite_code="nope",
            )
            assert bad.status == "invalid"

        db.run_with_app(proof)
