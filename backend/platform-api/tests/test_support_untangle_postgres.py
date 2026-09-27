"""«Распутать чаты» на настоящей базе (27.09.2026).

Вопрос по сайту ушёл в открытую заявку Gemini (пачка: текст и два скриншота
в пределах минуты, плюс старое сообщение). Перенос кладёт пачку в обращение
по сайту целиком — записи переезжают, копий с тем же источником нет, — а
старое сообщение остаётся у Карины. Обращение по сайту становится самым
свежим, и следующее сообщение человека найдёт именно его. Номер темы
«Заявки на сайты» хранится у site-форума и не трогает «Отчёты» Карины.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
BINDING = "whieda-advisor-bot"
OWNER = 688931415
KARINA = 2101187096
ELENA = 1430839193

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('proof-elena', 'whieda', 'Елена', 1430839193, '1430839193');
"""


@pytest.mark.integration
def test_misrouted_burst_moves_to_the_site_ticket_and_orders_topic_is_kept_apart():
    with temporary_database("whieda_support_untangle") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS, twice=True)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_one, tenant_connection
            from app.support.service import (
                get_forum,
                get_message_by_source,
                get_open_ticket_for_user,
                list_user_burst,
                move_message_to_ticket,
                open_or_reuse_ticket,
                record_relayed_message,
                register_forum,
                set_forum_service_threads,
                set_site_orders_thread,
            )

            def ticket(channel: str, admin: int):
                return open_or_reuse_ticket(
                    "whieda", channel_code=channel, offer_code=None, offer_title=None,
                    user_telegram_user_id=ELENA, user_chat_id=ELENA, user_display="Елена Антонова (@lite77777)",
                    admin_telegram_user_id=admin,
                )

            gemini = await ticket("gemini", KARINA)
            for source, text in ((100, "Оплачено"), (200, "В Одноклассниках ссылка не кликабельна"), (201, None), (202, None)):
                await record_relayed_message(
                    "whieda", ticket_id=str(gemini["ticket_id"]), direction="user_to_admin", text=text,
                    telegram_file_id=None if text else "photo", source_chat_id=ELENA, source_message_id=source,
                    delivered_chat_id=-1004392604070, delivered_message_id=5000 + source,
                )
            # «Оплачено» было за час до вопроса про сайт.
            async with tenant_connection("whieda") as conn:
                await fetch_one(
                    conn,
                    "update support_messages set created_at = created_at - interval '1 hour' where source_message_id = 100 returning message_id",
                    (),
                )

            pressed = await get_message_by_source("whieda", source_chat_id=ELENA, source_message_id=200)
            burst = await list_user_burst("whieda", ticket_id=str(gemini["ticket_id"]), around=pressed["created_at"])
            assert [m["source_message_id"] for m in burst] == [200, 201, 202]

            site = await ticket("site", OWNER)
            assert site["created"] is True
            for index, item in enumerate(burst):
                await move_message_to_ticket(
                    "whieda", message_id=str(item["message_id"]), ticket_id=str(site["ticket_id"]),
                    delivered_chat_id=-1004367523131, delivered_message_id=900 + index,
                )

            moved = await get_message_by_source("whieda", source_chat_id=ELENA, source_message_id=202)
            assert str(moved["ticket_id"]) == str(site["ticket_id"]) and int(moved["delivered_message_id"]) == 902
            stays = await get_message_by_source("whieda", source_chat_id=ELENA, source_message_id=100)
            assert str(stays["ticket_id"]) == str(gemini["ticket_id"])
            # Следующее сообщение Елены найдёт обращение по сайту — оно самое свежее.
            latest = await get_open_ticket_for_user("whieda", user_telegram_user_id=ELENA)
            assert str(latest["ticket_id"]) == str(site["ticket_id"])

            # Тема «Заявки на сайты» — у site-форума; «Отчёты» Карины не тронуты.
            await register_forum("whieda", binding_id=BINDING, chat_id=-1004392604070, title="WWC_Карина", registered_by=OWNER, kind="services")
            await register_forum("whieda", binding_id=BINDING, chat_id=-1004367523131, title="WWC Support", registered_by=OWNER, kind="site")
            await set_forum_service_threads("whieda", binding_id=BINDING, bonuses_thread_id=24, reports_thread_id=25)
            await set_site_orders_thread("whieda", binding_id=BINDING, thread_id=5)
            assert (await get_forum("whieda", binding_id=BINDING, kind="site"))["reports_thread_id"] == 5
            assert (await get_forum("whieda", binding_id=BINDING))["reports_thread_id"] == 25
            await set_site_orders_thread("whieda", binding_id=BINDING, thread_id=None)
            assert (await get_forum("whieda", binding_id=BINDING, kind="site"))["reports_thread_id"] is None

        db.run_with_app(proof)
