"""A partner's photo, voice and video reach the team — step by step (06.10.2026).

Real database and the real handler chain; only Telegram is mocked. Before the
fix a photo sent before «Поддержка», any voice or video, and the owner's voice
answer were dropped without a word (Лебедевич, #S-17). Run with ``-s`` to read
the step log.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
BINDING = "whieda-advisor-bot"
OWNER = 688931415
NATA = 70001
IRA = 70002
SITE_FORUM = -1004000000001

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id, telegram_username) values
  ('nata', 'whieda', 'Наталья', 70001, '70001', 'nata'),
  ('ira', 'whieda', 'Ирина', 70002, '70002', 'ira');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled, public_profile) values
  ('nata', 'whieda', 'nata', 'named', true, '{"public_site_url": "https://nata.wwc.best/"}');
"""

OPENED_TEXT = (
    "Получили фото и передали команде WWC — обращение #S-1.\n\n"
    "Напишите сюда, что с ним сделать (например, поставить фото на сайт), — ответ придёт в этот чат."
)


def _sender(user: int) -> dict:
    name, login = {NATA: ("Наталья", "nata"), IRA: ("Ирина", "ira")}[user]
    return {"id": user, "first_name": name, "username": login}


def _private(user: int, message_id: int, **body) -> dict:
    return {"message": {"message_id": message_id, "chat": {"id": user, "type": "private"}, "from": _sender(user), **body}}


def _photo(fid: str) -> list[dict]:
    return [{"file_id": f"{fid}-small", "width": 90, "height": 90}, {"file_id": fid, "width": 1280, "height": 1280}]


def _group(chat: int, user: int, thread_id: int | None, message_id: int, **body) -> dict:
    message = {
        "message_id": message_id,
        "chat": {"id": chat, "type": "supergroup", "title": "WWC Support", "is_forum": True},
        "from": {"id": user, "first_name": "Виктор", "is_bot": False},
        **body,
    }
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"message": message}


@pytest.mark.integration
def test_partner_attachments_reach_the_team_step_by_step(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    with temporary_database("whieda_support_media") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.telegram.bindings import BotBindingContext
            from app.telegram.processor import process_core_telegram_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                   entitlements={"structure_basic": True, "partner_leads": True, "site_support": True})
            binding = BotBindingContext(
                binding_id=BINDING, tenant=tenant, bot_token_ref="env:PROOF", webhook_secret_ref="env:PROOF",
                bot_username="WHIEDA_Advisor_bot", status="active", processing_mode="core",
                bot_token="proof-token", webhook_secret="proof-secret",
            )
            ids = itertools.count(1000)

            async def fake_send(**kwargs):
                return {"ok": True, "message_id": next(ids)}

            send = AsyncMock(side_effect=fake_send)
            copy = AsyncMock(side_effect=fake_send)
            react = AsyncMock(return_value={"ok": True})
            topics = AsyncMock(side_effect=[{"ok": True, "message_thread_id": 77}, {"ok": True, "message_thread_id": 78}])
            quiet = {
                "app.telegram.processor.link_lead_actor_by_username": AsyncMock(return_value=None),
                "app.telegram.processor.fill_lead_actor_user_id": AsyncMock(return_value=None),
                "app.telegram.processor.handle_onboarding": AsyncMock(return_value=None),
                "app.telegram.processor.handle_navigation_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_academy_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_crm_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_wwc_service_text": AsyncMock(return_value=None),
                "app.telegram.processor.handle_advisor_query": AsyncMock(return_value={"ok": True, "route": "advisor"}),
                "app.telegram.support.ensure_service_topics": AsyncMock(return_value=None),
                "app.telegram.support.set_message_reaction": react,
                "app.telegram.support.answer_callback_query": AsyncMock(return_value={"ok": True}),
                "app.telegram.support.create_forum_topic": topics,
                "app.telegram.support.send_telegram_text": send,
                "app.telegram.support.copy_telegram_message": copy,
            }
            patches = [patch(target, mock) for target, mock in quiet.items()]
            for item in patches:
                item.start()
            steps: list[str] = []

            async def step(name: str, update: dict, user: int = NATA) -> dict:
                send.reset_mock()
                copy.reset_mock()
                react.reset_mock()
                result = await process_core_telegram_update(tenant, update, name, binding=binding)
                out = {
                    "result": result or {},
                    "to_user": [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(user)],
                    "to_topic": [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)],
                    "copied": [(c.kwargs["chat_id"], c.kwargs["from_chat_id"]) for c in copy.await_args_list],
                    "reacted": [c.kwargs["chat_id"] for c in react.await_args_list],
                }
                steps.append(
                    f"{name}: route={out['result'].get('route')} status={out['result'].get('status')} "
                    f"| партнёру: {[t[:60] for t in out['to_user']] or '—'} | в тему: {[t[:60] for t in out['to_topic']] or '—'} "
                    f"| копия: {out['copied'] or '—'} | 👍: {out['reacted'] or '—'}"
                )
                return out

            try:
                await process_core_telegram_update(
                    tenant, _group(SITE_FORUM, OWNER, None, 1, text="/forum site"), "forum", binding=binding)
                forum, nata = str(SITE_FORUM), str(NATA)
                into_topic = [(forum, nata)]

                # 1. Photo before «Поддержка»: the bot opens the ticket itself, the photo lands in the topic.
                first = await step("1 фото ДО «Поддержки»", _private(NATA, 10, photo=_photo("ph-before")))
                assert first["result"]["route"] == "support_relay" and first["result"]["status"] == "ticket_opened"
                assert first["copied"] == into_topic and first["reacted"] == [nata]
                assert first["to_user"] == [OPENED_TEXT]
                assert "Бот открыл обращение сам: пришло фото без «Поддержки»." in first["to_topic"][0]
                assert first["to_topic"][1].endswith("(вложение выше)")

                again = await step("2 кнопка «Поддержка»", _private(NATA, 11, text="поддержка"))
                assert again["result"]["status"] == "ticket_opened" and "снова нажал" in again["to_topic"][0]

                text = await step("3 текст «Фото»", _private(NATA, 12, text="Фото"))
                assert text["result"]["route"] == "support_relay" and text["to_topic"][0].endswith("\nФото")
                assert not text["reacted"]

                for name, update in [
                    ("4 фото (сжатое)", _private(NATA, 13, photo=_photo("ph-1"))),
                    ("5 фото файлом", _private(NATA, 14, document={"file_id": "doc-1", "mime_type": "image/jpeg", "file_name": "me.jpg"})),
                    ("6 альбом 1/2", _private(NATA, 15, media_group_id="g1", photo=_photo("ph-a1"))),
                    ("6 альбом 2/2", _private(NATA, 16, media_group_id="g1", photo=_photo("ph-a2"))),
                    ("7 фото с подписью", _private(NATA, 17, photo=_photo("ph-c"), caption="Вот новое фото")),
                ]:
                    out = await step(name, update)
                    assert out["result"]["route"] == "support_relay", name
                    assert out["copied"] == into_topic and out["reacted"] == [nata] and not out["to_user"], name

                voice = await step("8 голосовое", _private(NATA, 18, voice={"file_id": "voice-1", "duration": 12}))
                assert voice["result"]["route"] == "support_relay" and voice["copied"] == into_topic
                assert voice["to_topic"][0].endswith("(голосовое выше)")
                video = await step("9 видео", _private(NATA, 19, video={"file_id": "video-1", "duration": 5}))
                assert video["copied"] == into_topic and video["to_topic"][0].endswith("(видео выше)")
                circle = await step("9b кружок", _private(NATA, 20, video_note={"file_id": "note-1", "length": 240, "duration": 4}))
                assert circle["copied"] == into_topic and circle["to_topic"][0].endswith("(видеокружок выше)")

                owner_photo = await step("10 владелец: фото в тему", _group(SITE_FORUM, OWNER, 77, 300, photo=_photo("ph-owner")))
                assert owner_photo["copied"] == [(nata, forum)]
                owner_voice = await step(
                    "11 владелец: голосовое в тему", _group(SITE_FORUM, OWNER, 77, 301, voice={"file_id": "voice-owner", "duration": 9}))
                assert owner_voice["result"]["direction"] == "admin_to_user" and owner_voice["copied"] == [(nata, forum)]
                assert owner_voice["to_user"] == ["Ответ команды WWC по обращению #S-1: вложение выше."]

                # Another person: a sticker opens nothing; an album opens one ticket, the second photo joins it.
                ira = str(IRA)
                sticker = await step("12 стикер без обращения", _private(IRA, 30, sticker={"file_id": "st-1"}), user=IRA)
                assert sticker["result"]["route"] == "ignored_media" and not sticker["to_user"] and not sticker["copied"]
                album1 = await step("13 альбом без обращения 1/2", _private(IRA, 31, media_group_id="g2", photo=_photo("ira-1")), user=IRA)
                album2 = await step("13 альбом без обращения 2/2", _private(IRA, 32, media_group_id="g2", photo=_photo("ira-2")), user=IRA)
                assert album1["result"]["status"] == "ticket_opened" and len(album1["to_user"]) == 1
                assert album2["result"]["route"] == "support_relay" and not album2["to_user"]
                assert album2["copied"] == [(forum, ira)]
            finally:
                for item in patches:
                    item.stop()

            async with tenant_connection("whieda") as conn:
                stored = await fetch_all(
                    conn,
                    "select direction, coalesce(text, '') as text, coalesce(telegram_file_id, '') as fid "
                    "from support_messages where direction <> 'system' order by created_at",
                )
            print("\n".join(["", *steps, "", "support_messages:",
                             *[f"  {r['direction']}: {r['text']!r} file={r['fid']!r}" for r in stored]]))
            assert [r["fid"] for r in stored if r["fid"]] == [
                "ph-before", "ph-1", "doc-1", "ph-a1", "ph-a2", "ph-c", "voice-1", "video-1", "note-1",
                "ph-owner", "voice-owner", "ira-1", "ira-2",
            ]

        db.run_with_app(proof)
