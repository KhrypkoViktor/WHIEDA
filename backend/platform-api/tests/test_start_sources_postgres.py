"""Метка источника в ссылке в бота на настоящей схеме PostgreSQL (V42, 30.10.2026).

Проверяет то, чего юнит-тесты не видят: миграция (дважды), изоляция арендатора, запись строки
при старте из Telegram и Max, «первое касание» — человек новый один раз, код партнёра после метки
(как код приглашения и как ref_code) закрепляет пригласившего и не меняет уже стоящего,
и запрос «новые люди по метке по неделям» из SRC_TAGS.md.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

# Тот же запрос, что в PROCESS/wwc-analytics-20261030/SRC_TAGS.md.
WEEKLY_NEW_PEOPLE_SQL = """
select date_trunc('week', created_at at time zone 'Europe/Moscow')::date as week_start_msk,
       channel,
       tag,
       count(distinct channel_user_id) as new_people
from bot_start_sources
where tenant_id = 'whieda' and first_seen
group by 1, 2, 3
order by 1 desc, 2, 3
"""


@pytest.mark.integration
def test_start_sources_on_live_schema():
    with temporary_database("whieda_start_sources") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            # V42 безопасен при повторном прогоне.
            conn.execute((db_sql_path()).read_text(encoding="utf-8"))
            conn.execute((db_sql_path()).read_text(encoding="utf-8"))

            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_username, telegram_chat_id)
                values ('proof-inviter', 'whieda', 'Proof inviter', 'proof_inviter', '50001');

                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values ('whieda', 'proofinvite_code1', 'proof-inviter');

                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile)
                values ('proofref', 'whieda', 'proof-inviter', 'named', '{"display_name": "Proof inviter"}'::jsonb);
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, fetch_one, tenant_connection
            from app.leads.channels import accept_channel_referral_start, ensure_channel_actor
            from app.leads.start_sources import (
                SourceStart,
                parse_source_start_token,
                register_start_source,
                unknown_source_start,
            )
            from app.referral_bonus.service import accept_referral_start, ensure_telegram_actor
            from app.telegram.processor import record_source_start
            from app.telegram.update_parser import parse_telegram_message
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})

            async def rows(where: str = "true", params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return list(
                        await fetch_all(
                            conn,
                            "select channel, channel_user_id, actor_id, tag, ref_code, first_seen "
                            f"from bot_start_sources where tenant_id = 'whieda' and ({where}) order by start_id",
                            params,
                        )
                    )

            async def value(query: str, params: tuple = ()) -> dict | None:
                async with tenant_connection("whieda") as conn:
                    row = await fetch_one(conn, query, params)
                return dict(row) if row else None

            def telegram_msg(user_id: int, text: str):
                return parse_telegram_message(
                    {"message": {"text": text, "chat": {"id": user_id, "type": "private"},
                                 "from": {"id": user_id, "first_name": f"User{user_id}"}}}
                )

            # 1. Новый человек, метка без партнёра: строка есть, он новый, actor ещё нет.
            await record_source_start(
                tenant, telegram_msg(61001, "/start src_vk"), parse_source_start_token("src_vk"), trace_id="t1"
            )
            assert await rows("channel_user_id = '61001'") == [
                {"channel": "telegram", "channel_user_id": "61001", "actor_id": None,
                 "tag": "vk", "ref_code": None, "first_seen": True}
            ]
            assert await value("select count(*) as n from lead_actors where telegram_user_id = 61001") == {"n": 0}

            # 2. Тот же человек нажал другую ссылку: новая строка, но уже не «новый» — первое касание одно.
            await record_source_start(
                tenant, telegram_msg(61001, "/start src_ok"), parse_source_start_token("src_ok"), trace_id="t2"
            )
            assert [(r["tag"], r["first_seen"]) for r in await rows("channel_user_id = '61001'")] == [
                ("vk", True), ("ok", False)
            ]

            # 3. Человек, которого бот уже знал (есть actor), по новой метке — не новый, actor_id в строке.
            raw = {"message": {"from": {"id": 61002, "first_name": "Known"}}}
            known_actor = await ensure_telegram_actor(
                "whieda", telegram_user_id=61002, telegram_chat_id=61002, raw_update=raw
            )
            assert known_actor == "telegram:whieda:61002"
            await record_source_start(
                tenant, telegram_msg(61002, "/start src_dzen"), parse_source_start_token("src_dzen"), trace_id="t3"
            )
            assert await rows("channel_user_id = '61002'") == [
                {"channel": "telegram", "channel_user_id": "61002", "actor_id": "telegram:whieda:61002",
                 "tag": "dzen", "ref_code": None, "first_seen": False}
            ]

            # 4. Метка + код приглашения (как ref_<код>): первое касание партнёра, ref_code в строке.
            await record_source_start(
                tenant, telegram_msg(61003, "/start src_youtube_proofinvite_code1"),
                parse_source_start_token("src_youtube_proofinvite_code1"), trace_id="t4",
            )
            assert await rows("channel_user_id = '61003'") == [
                {"channel": "telegram", "channel_user_id": "61003", "actor_id": "telegram:whieda:61003",
                 "tag": "youtube", "ref_code": "proofref", "first_seen": True}
            ]
            assert await value(
                "select inviter_actor_id, source from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'telegram:whieda:61003'"
            ) == {"inviter_actor_id": "proof-inviter", "source": "telegram_deeplink"}

            # 5. Метка + ref_code партнёра (как shop_<код>_<ref> на сайте): тот же итог.
            await record_source_start(
                tenant, telegram_msg(61004, "/start src_instagram_proofref"),
                parse_source_start_token("src_instagram_proofref"), trace_id="t5",
            )
            assert await rows("channel_user_id = '61004'") == [
                {"channel": "telegram", "channel_user_id": "61004", "actor_id": "telegram:whieda:61004",
                 "tag": "instagram", "ref_code": "proofref", "first_seen": True}
            ]
            assert await value(
                "select inviter_actor_id from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'telegram:whieda:61004'"
            ) == {"inviter_actor_id": "proof-inviter"}
            # Код приглашения у партнёра остался один: ref_code не плодит новых.
            assert await value(
                "select count(*) as n from referral_invite_codes where tenant_id = 'whieda'"
            ) == {"n": 1}

            # 6. Уже стоящий пригласивший не меняется: знакомый человек с метка+код другого партнёра.
            await value(
                "insert into lead_actors (actor_id, tenant_id, display_name) "
                "values ('proof-second', 'whieda', 'Second') returning actor_id"
            )
            await value(
                "insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id) "
                "values ('whieda', 'secondinvite_code', 'proof-second') returning invite_code"
            )
            await record_source_start(
                tenant, telegram_msg(61004, "/start src_vk_secondinvite_code"),
                parse_source_start_token("src_vk_secondinvite_code"), trace_id="t6",
            )
            assert await value(
                "select inviter_actor_id from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'telegram:whieda:61004'"
            ) == {"inviter_actor_id": "proof-inviter"}
            assert (await rows("channel_user_id = '61004'"))[-1]["first_seen"] is False

            # 7. Негодный код партнёра: метка записана, партнёра нет, атрибуции нет, ошибки нет.
            await record_source_start(
                tenant, telegram_msg(61005, "/start src_ok_nosuchpartner"),
                parse_source_start_token("src_ok_nosuchpartner"), trace_id="t7",
            )
            assert await rows("channel_user_id = '61005'") == [
                {"channel": "telegram", "channel_user_id": "61005", "actor_id": None,
                 "tag": "ok", "ref_code": None, "first_seen": True}
            ]
            assert await value(
                "select count(*) as n from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'telegram:whieda:61005'"
            ) == {"n": 0}

            # 8. Незнакомый параметр: tag = 'unknown', значение не хранится.
            await record_source_start(tenant, telegram_msg(61006, "/start vk"), unknown_source_start(), trace_id="t8")
            assert await rows("channel_user_id = '61006'") == [
                {"channel": "telegram", "channel_user_id": "61006", "actor_id": None,
                 "tag": "unknown", "ref_code": None, "first_seen": True}
            ]

            # 9. Max: метка, затем та же метка снова; потом метка + код приглашения.
            async def max_start(user_id: int, payload: str):
                source = parse_source_start_token(payload) or unknown_source_start()

                async def accept_partner(code: str):
                    return await accept_channel_referral_start(
                        "whieda", channel="max", channel_user_id=user_id, channel_chat_id=user_id,
                        username=None, display_name=f"Max {user_id}", invite_code=code,
                    )

                async def ensure_actor():
                    return await ensure_channel_actor(
                        "whieda", channel="max", channel_user_id=user_id, channel_chat_id=user_id,
                        username=None, display_name=f"Max {user_id}",
                    )

                return await register_start_source(
                    "whieda", channel="max", channel_user_id=user_id, source=source,
                    accept_partner=accept_partner, ensure_actor=ensure_actor,
                )

            first = await max_start(62001, "src_max")
            again = await max_start(62001, "src_max")
            assert (first.first_seen, again.first_seen) == (True, False)
            assert [(r["channel"], r["actor_id"], r["tag"], r["first_seen"]) for r in await rows("channel_user_id = '62001'")] == [
                ("max", "max:whieda:62001", "max", True),
                ("max", "max:whieda:62001", "max", False),
            ]
            with_partner = await max_start(62002, "src_vk_proofinvite_code1")
            assert (with_partner.first_seen, with_partner.attribution, with_partner.ref_code) == (True, "attributed", "proofref")
            assert await value(
                "select inviter_actor_id, source from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'max:whieda:62002'"
            ) == {"inviter_actor_id": "proof-inviter", "source": "max_deeplink"}
            unknown = await max_start(62003, "whatever-param")
            assert unknown.tag == "unknown"
            # Незнакомый и нулевой по метке: Max-человек появился в lead_actor_channels через ensure_actor.
            assert await value(
                "select count(*) as n from lead_actor_channels where tenant_id = 'whieda' and channel = 'max'"
            ) == {"n": 3}

            # 10. Арендаторы не смешиваются: другой арендатор строк не видит и чужую строку не запишет.
            async with tenant_connection("other") as conn:
                assert await fetch_one(conn, "select count(*) as n from bot_start_sources") == {"n": 0}
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with tenant_connection("other") as conn:
                    await fetch_one(
                        conn,
                        "insert into bot_start_sources (tenant_id, channel, channel_user_id, tag) "
                        "values ('whieda', 'telegram', '1', 'vk') returning start_id",
                    )

        db.run_with_app(proof)

        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            # Запрос из отчёта: новые люди по метке по неделям (МСК); считаются только строки first_seen.
            conn.execute(
                "update bot_start_sources set created_at = timestamptz '2026-10-20 12:00:00+03' "
                "where channel_user_id = '61001'"
            )
            conn.execute(
                "update bot_start_sources set created_at = timestamptz '2026-10-26 12:00:00+03' "
                "where channel_user_id = '62001'"
            )
            # Воскресенье 25.10 22:30 UTC — это уже понедельник 26.10 01:30 по Москве: неделя считается по Москве.
            conn.execute(
                "update bot_start_sources set created_at = timestamptz '2026-10-25 22:30:00+00' "
                "where channel_user_id = '61003'"
            )
            report = [tuple(row) for row in conn.execute(WEEKLY_NEW_PEOPLE_SQL).fetchall()]
            weeks = {(str(week), channel, tag): n for week, channel, tag, n in report}
            assert weeks[("2026-10-19", "telegram", "vk")] == 1  # 61001; его вторая строка (ok) — не новая
            assert ("2026-10-19", "telegram", "ok") not in weeks
            assert weeks[("2026-10-26", "max", "max")] == 1  # 62001 один раз: второй старт не новый
            assert weeks[("2026-10-26", "telegram", "youtube")] == 1  # граница недели по Москве, не по UTC
            assert ("2026-10-19", "telegram", "youtube") not in weeks
            assert ("2026-10-26", "telegram", "dzen") not in weeks  # 61002 бот знал заранее
            assert report == sorted(report, key=lambda r: (-r[0].toordinal(), r[1], r[2]))  # свежая неделя сверху

            # Метка вне формата не пишется в таблицу вообще.
            for bad in ("VK", "", "a" * 33, "vk_ref", "вк"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute(
                        "insert into bot_start_sources (tenant_id, channel, channel_user_id, tag) "
                        "values ('whieda', 'telegram', 'x', %s)",
                        (bad,),
                    )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into bot_start_sources (tenant_id, channel, channel_user_id, tag) "
                    "values ('whieda', 'vk', 'x', 'vk')"
                )


def db_sql_path():
    from tests.postgres_testkit import SQL_DIR

    return SQL_DIR / "platform_bot_start_sources_v42.sql"
