"""Мост группы потока Telegram ↔ Max и контроль состава на настоящей базе (V27, 10.10.2026).

Владелец: «из группы 1 потока перепощивать мои сообщения в Max, а оттуда от людей —
в нашу группу»; Самцова — в Max, только когда отвечает на сообщение бота; «пусть
следит за составом: участники не должны появляться рандомно без оплаты».
Telegram и Max подменены, база и вся цепочка обработчиков — настоящие.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

TG_GROUP = -1004497305833
MAX_GROUP = -79965253305729
OWNER = 688931415
SAMTSOVA = 525317405
BOT = 8159293641
TOKEN = f"{BOT}:proof"
PAID = 71001
STRANGER = 71002


def _tg(message_id: int, sender: int, **body) -> dict:
    return {"message_id": message_id, "chat": {"id": TG_GROUP, "type": "supergroup"}, "from": {"id": sender, "first_name": "X"}, **body}


def _max(mid: str, user_id: int, text: str = "", **message) -> dict:
    return {"update_type": "message_created", "message": {
        "sender": {"user_id": user_id, "first_name": "Ирина", "last_name": "Петрова"},
        "recipient": {"chat_id": MAX_GROUP, "chat_type": "chat"},
        "body": {"mid": mid, "text": text, **message.pop("body", {})}, **message}}


@pytest.mark.integration
def test_bridge_carries_owner_and_people_and_watches_who_joins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_MAX_BOT_TOKEN", "max-proof")
    with temporary_database("whieda_chat_bridge") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                "insert into shop_access (tenant_id, item_code, telegram_user_id) values ('whieda', 'kurs-online-start', %s)",
                (PAID,),
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.max import bridge as bridge_mod
            from app.max import crosspost
            from app.max.bridges import BRIDGES, bridge_for_telegram
            from app.max.processor import process_max_event
            from app.max.update_parser import parse_max_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
            bridge = bridge_for_telegram(TG_GROUP)
            assert bridge is BRIDGES[0]
            mids = itertools.count(1)
            tg_ids = itertools.count(500)
            send_max = AsyncMock(side_effect=lambda **kw: {"ok": True, "mid": f"mid.bot.{next(mids)}"})
            upload = AsyncMock(side_effect=lambda kind, body, **kw: {"type": kind, "payload": {"token": f"tok-{body.decode()}"}})
            download_tg = AsyncMock(side_effect=lambda token, file_id: file_id.encode())
            calls: list[tuple[str, dict, dict | None]] = []

            async def fake_tg(method, bot_token, data, files=None):
                calls.append((method, data, files))
                if method in {"sendMessage", "sendPhoto", "sendDocument", "sendVideo", "sendAudio"}:
                    return {"ok": True, "message_id": next(tg_ids)}
                return {"ok": True}

            member_status = AsyncMock(return_value="member")
            binding = type("B", (), {"bot_token": TOKEN})()
            with patch.object(bridge_mod, "send_max_message", send_max), patch.object(crosspost, "upload_max_media", upload), \
                 patch.object(crosspost, "download_telegram_file", download_tg), patch.object(bridge_mod, "_tg", side_effect=fake_tg), \
                 patch.object(bridge_mod, "_download", AsyncMock(side_effect=lambda url: url.encode())), \
                 patch.object(bridge_mod, "_tg_member_status", member_status), \
                 patch.object(bridge_mod, "remove_max_member", AsyncMock(return_value={"ok": True})) as remove_max, \
                 patch("app.telegram.bindings.resolve_bot_binding_context", AsyncMock(return_value=binding)):
                role = lambda m: bridge_mod.telegram_role(m, bridge, owner_id=OWNER, bot_id=BOT)  # noqa: E731

                # 1. Владелец пишет в Telegram-группе — в Max уходит без подписи; повтор Telegram не дублирует.
                post = _tg(10, OWNER, text="Эфир в 19:00", entities=[{"type": "bold", "offset": 0, "length": 4}])
                assert role(post) == "owner"
                first = await bridge_mod.bridge_telegram_message("whieda", bridge, post, role="owner", bot_token=TOKEN)
                again = await bridge_mod.bridge_telegram_message("whieda", bridge, post, role="owner", bot_token=TOKEN)
                assert first["status"] == "sent" and again["status"] == "duplicate" and send_max.await_count == 1
                assert send_max.await_args.kwargs == {
                    "chat_id": MAX_GROUP, "text": "<b>Эфир</b> в 19:00", "attachments": [], "html_format": True, "reply_to_mid": None,
                }

                # Файл владельца — тоже в Max (у канала файлы шли ссылкой, у моста — вложением).
                doc = _tg(11, OWNER, caption="Задание недели", document={"file_id": "d1", "file_size": 10, "file_name": "zadanie.pdf"})
                await bridge_mod.bridge_telegram_message("whieda", bridge, doc, role="owner", bot_token=TOKEN)
                assert send_max.await_args.kwargs["attachments"] == [{"type": "file", "payload": {"token": "tok-d1"}}]
                assert upload.await_args.kwargs == {"filename": "zadanie.pdf"}

                # Служебное «вступил», чужие участники и Самцова без ответа боту — в Max не идут.
                assert role(_tg(12, OWNER, new_chat_members=[{"id": PAID}])) is None
                assert role(_tg(13, PAID, text="привет")) is None
                assert role(_tg(14, SAMTSOVA, text="всем привет")) is None

                # 2. Участница пишет в Max, отвечая на сообщение владельца — в Telegram с подписью и ответом на его пост.
                result = await process_max_event(tenant, parse_max_update(_max(
                    "mid.ira.1", 900, "Можно запись?", link={"type": "reply", "message": {"mid": "mid.bot.1"}})), "t")
                assert result["status"] == "sent"
                method, data, files = calls[-1]
                assert method == "sendMessage" and files is None
                assert data["text"] == "<b>Ирина Петрова</b> · Max\nМожно запись?"
                assert data["reply_parameters"]["message_id"] == 10
                ira_tg = result["tg_message_ids"][0]
                # Повтор вебхука Max не дублирует.
                calls.clear()
                assert (await process_max_event(tenant, parse_max_update(_max("mid.ira.1", 900, "Можно запись?")), "t"))["status"] == "duplicate"
                assert calls == []

                # Фото из Max — фото в Telegram с подписью; стикер — пометкой.
                await process_max_event(tenant, parse_max_update(_max("mid.ira.2", 900, "", body={"attachments": [
                    {"type": "image", "payload": {"url": "https://i.max/p.jpg"}}, {"type": "sticker", "payload": {"code": "x"}}]})), "t")
                method, data, files = calls[-1]
                assert method == "sendPhoto" and files["photo"][1] == b"https://i.max/p.jpg"
                assert data["caption"].startswith("<b>Ирина Петрова</b> · Max") and "стикер" in data["caption"]

                # 3. Самцова отвечает на сообщение бота (пришедшее из Max) — в Max с подписью, ответом на Ирину.
                reply = _tg(20, SAMTSOVA, text="Запись будет завтра", reply_to_message={"message_id": ira_tg, "from": {"id": BOT, "is_bot": True}})
                assert role(reply) == "curator"
                await bridge_mod.bridge_telegram_message("whieda", bridge, reply, role="curator", bot_token=TOKEN)
                kwargs = send_max.await_args.kwargs
                assert kwargs["text"] == "<b>Ольга Самцова</b>:\nЗапись будет завтра" and kwargs["reply_to_mid"] == "mid.ira.1"

                # 4. Состав Telegram: оплатившего и добавленного админом не трогаем, чужого — владельцу кнопки.
                calls.clear()
                joined = await bridge_mod.watch_telegram_join(
                    "whieda", bridge, _tg(30, STRANGER, new_chat_members=[{"id": STRANGER, "first_name": "Чужой"}]), bot_token=TOKEN)
                paid = await bridge_mod.watch_telegram_join(
                    "whieda", bridge, _tg(31, PAID, new_chat_members=[{"id": PAID, "first_name": "Оплатил"}]), bot_token=TOKEN)
                member_status.return_value = "creator"
                by_admin = await bridge_mod.watch_telegram_join(
                    "whieda", bridge, _tg(32, OWNER, new_chat_members=[{"id": 71003, "first_name": "Новый"}]), bot_token=TOKEN)
                assert [r["status"] for r in joined + paid + by_admin] == ["alerted", "paid", "added_by_admin"]
                alerts = [c for c in calls if c[0] == "sendMessage"]
                assert len(alerts) == 1 and alerts[0][1]["chat_id"] == OWNER and "Чужой" in alerts[0][1]["text"]
                buttons = alerts[0][1]["reply_markup"]["inline_keyboard"][0]
                assert [b["callback_data"] for b in buttons] == [f"brg:tk:0:{STRANGER}", "brg:keep"]

                # Кнопка «Удалить»: только владелец; бан и сразу разбан — человек сможет вернуться после оплаты.
                calls.clear()
                await bridge_mod.handle_owner_callback({"id": "c1", "data": f"brg:tk:0:{STRANGER}", "from": {"id": PAID}}, bot_token=TOKEN)
                assert [c[0] for c in calls] == ["answerCallbackQuery"]
                calls.clear()
                await bridge_mod.handle_owner_callback({"id": "c2", "data": f"brg:tk:0:{STRANGER}", "from": {"id": OWNER},
                                                        "message": {"message_id": 5, "chat": {"id": OWNER}, "text": "⚠️ …"}}, bot_token=TOKEN)
                assert [c[0] for c in calls] == ["banChatMember", "unbanChatMember", "answerCallbackQuery", "editMessageText"]
                assert calls[0][1] == {"chat_id": TG_GROUP, "user_id": STRANGER}

                # 5. Состав Max: добавил владелец (его Max-аккаунт) — молчим; вошёл по ссылке — кнопки; «Удалить» убирает из Max.
                calls.clear()
                quiet = await process_max_event(tenant, parse_max_update(
                    {"update_type": "user_added", "chat_id": MAX_GROUP, "user": {"user_id": 801, "first_name": "Свой"}, "inviter_id": 482284673}), "t")
                loud = await process_max_event(tenant, parse_max_update(
                    {"update_type": "user_added", "chat_id": MAX_GROUP, "user": {"user_id": 802, "first_name": "Гость"}}), "t")
                assert quiet["status"] == "added_by_owner" and loud["status"] == "alerted"
                assert len(calls) == 1 and "по ссылке" in calls[0][1]["text"]
                await bridge_mod.handle_owner_callback({"id": "c3", "data": "brg:mk:0:802", "from": {"id": OWNER}}, bot_token=TOKEN)
                remove_max.assert_awaited_once_with(MAX_GROUP, 802)

                # Чужие группы Max мост не трогает.
                other = await process_max_event(tenant, parse_max_update(
                    {"update_type": "message_created", "message": {"sender": {"user_id": 5}, "recipient": {"chat_id": -1, "chat_type": "chat"},
                                                                    "body": {"mid": "m", "text": "x"}}}), "t")
                assert other["status"] == "not_bridged"

            # Группа потока никогда не получает посты канала, даже с флагом crosspost.
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "insert into max_chats (tenant_id, chat_id, title, crosspost) values ('whieda', %s, 'поток', true), ('whieda', -5, 'канал', true)",
                    (MAX_GROUP,),
                )
                links = await fetch_all(conn, "select tg_message_id, max_mid, direction from chat_bridge_links order by tg_message_id")
            assert await crosspost.target_chats("whieda") == [-5]
            assert [(int(r["tg_message_id"]), r["max_mid"], r["direction"]) for r in links][:3] == [
                (10, "mid.bot.1", "tg_to_max"), (11, "mid.bot.2", "tg_to_max"), (20, "mid.bot.3", "tg_to_max"),
            ]

        db.run_with_app(proof)


CLUB_TG = -1004338290116
CLUB_MAX = -79980845696385


@pytest.mark.integration
def test_club_bridge_carries_everyone_both_ways_and_checks_the_club(monkeypatch: pytest.MonkeyPatch):
    """Клуб (11.10.2026): «двустороннее общение» — все участники в обе стороны; состав — по активному клубу."""
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_MAX_BOT_TOKEN", "max-proof")
    with temporary_database("whieda_club_bridge") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.max import bridge as bridge_mod
            from app.max import crosspost
            from app.max.bridges import bridge_for_max, bridge_for_telegram
            from app.max.processor import process_max_event
            from app.max.update_parser import parse_max_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
            club = bridge_for_telegram(CLUB_TG)
            assert club is bridge_for_max(CLUB_MAX) and club.mode == "all" and club.access == "club"
            send_max = AsyncMock(return_value={"ok": True, "mid": "mid.club.1"})
            calls: list[tuple[str, dict]] = []

            async def fake_tg(method, bot_token, data, files=None):
                calls.append((method, data))
                return {"ok": True, "message_id": 900}

            binding = type("B", (), {"bot_token": TOKEN})()
            member = {"message_id": 1, "chat": {"id": CLUB_TG, "type": "supergroup"}, "from": {"id": PAID, "first_name": "Анна", "last_name": "Ким"}, "text": "Всем привет"}
            with patch.object(bridge_mod, "send_max_message", send_max), patch.object(bridge_mod, "_tg", side_effect=fake_tg), \
                 patch.object(crosspost, "upload_max_media", AsyncMock()), \
                 patch("app.shop.service.is_club_member", AsyncMock(side_effect=lambda tenant_id, uid: uid == PAID)), \
                 patch.object(bridge_mod, "_tg_member_status", AsyncMock(return_value="member")), \
                 patch("app.telegram.bindings.resolve_bot_binding_context", AsyncMock(return_value=binding)):
                # Любой участник клуба из Telegram — в Max с подписью «Имя · Telegram»; владелец — без подписи.
                role = bridge_mod.telegram_role(member, club, owner_id=OWNER, bot_id=BOT)
                assert role == "member"
                await bridge_mod.bridge_telegram_message("whieda", club, member, role=role, bot_token=TOKEN)
                assert send_max.await_args.kwargs["chat_id"] == CLUB_MAX
                assert send_max.await_args.kwargs["text"] == "<b>Анна Ким</b> · Telegram\nВсем привет"
                own = {**member, "message_id": 2, "from": {"id": OWNER}, "text": "Эфир клуба в 20:00"}
                assert bridge_mod.telegram_role(own, club, owner_id=OWNER, bot_id=BOT) == "owner"
                assert bridge_mod.telegram_role({**member, "message_id": 3, "text": None, "new_chat_members": [{"id": 5}]}, club, owner_id=OWNER, bot_id=BOT) is None

                # Из Max — в Telegram-группу клуба.
                result = await process_max_event(tenant, parse_max_update({"update_type": "message_created", "message": {
                    "sender": {"user_id": 5, "first_name": "Олег"}, "recipient": {"chat_id": CLUB_MAX, "chat_type": "chat"},
                    "body": {"mid": "mid.oleg", "text": "Привет из Max"}}}), "t")
                assert result["status"] == "sent" and calls[-1][1]["chat_id"] == CLUB_TG
                assert calls[-1][1]["text"] == "<b>Олег</b> · Max\nПривет из Max"

                # Состав: с активным клубом — тишина, без клуба — владельцу кнопки.
                calls.clear()
                joined = await bridge_mod.watch_telegram_join("whieda", club, {"chat": {"id": CLUB_TG}, "from": {"id": STRANGER},
                    "new_chat_members": [{"id": STRANGER, "first_name": "Гость"}, {"id": PAID, "first_name": "Анна"}]}, bot_token=TOKEN)
                assert [r["status"] for r in joined] == ["alerted", "paid"]
                assert "Активного клуба у него не вижу" in calls[0][1]["text"]
                assert calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"brg:tk:1:{STRANGER}"

        db.run_with_app(proof)
