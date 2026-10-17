"""Max deep link на живой схеме: человек из Max закрепляется за партнёром через
lead_actor_channels, второй клик не меняет пригласившего. Партнёр с тем же @username с уже
заведённой владельцем записью НЕ сливается (17.10.2026): username в Max и Telegram — разные
пространства имён, чужой ник в Max не даёт доступа к actor партнёра."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_max_start_attributes_and_does_not_fold_on_live_schema():
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
            from app.max import webapp

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

            # Кто-то ставит в Max ник партнёра, заведённого владельцем (@same_handle), и пишет боту:
            # это ДРУГОЙ человек — свой actor, к партнёру ничего не прилипает.
            separate = await ensure_channel_actor(
                "whieda", channel="max", channel_user_id=9002, channel_chat_id=9102, username="Same_Handle", display_name="@Same_Handle"
            )
            assert separate == "max:whieda:9002"
            assert await value("select count(*) as n from lead_actor_channels where actor_id = 'owner-made'") == {"n": 0}
            assert await value(
                "select actor_id from lead_actor_channels where channel = 'max' and channel_user_id = '9002'"
            ) == {"actor_id": "max:whieda:9002"}
            assert await value("select telegram_user_id from lead_actors where actor_id = 'max:whieda:9002'") == {"telegram_user_id": None}
            # Ник пригласившего в Max его не «подменяет»: это не самоприглашение, а отдельный человек.
            impostor = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=50001, channel_chat_id=1, username="proof_inviter",
                display_name="@proof_inviter", invite_code="proofinvite_code1",
            )
            assert impostor.status == "attributed" and impostor.actor_id == "max:whieda:50001"
            assert impostor.actor_id != "proof-inviter"

            # Вход через Max читает только account_identities: ни эти Max-id, ни строка канала, которая
            # (как в старых данных) указывает прямо на actor партнёра, входом не являются, и ничего не дописывается.
            await ensure_channel_actor(
                "whieda", channel="max", channel_user_id=9100, channel_chat_id=9100, username=None, display_name="Лишний"
            )
            async with tenant_connection("whieda") as conn:
                await fetch_one(conn, "update lead_actor_channels set actor_id = 'proof-inviter' where channel_user_id = '9100' returning actor_id")
                resolved = {
                    max_id: await webapp._telegram_id_for_max(conn, "whieda", max_id) for max_id in (9001, 9002, 9100, 50001)
                }
            assert resolved == {9001: None, 9002: None, 9100: None, 50001: None}
            assert await value("select count(*) as n from account_identities") == {"n": 0}
            bad = await accept_channel_referral_start(
                "whieda", channel="max", channel_user_id=9003, channel_chat_id=None, username=None,
                display_name="Кто-то", invite_code="nope",
            )
            assert bad.status == "invalid"

        db.run_with_app(proof)
