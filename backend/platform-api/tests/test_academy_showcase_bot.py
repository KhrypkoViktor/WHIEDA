"""Бот витрины автора Академии (V29): только Академия, только личка, политика витрины."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from app.academy.showcases import Showcase
from app.telegram.processor import process_core_telegram_update

ALMIRA = Showcase(
    tenant_id="whieda",
    showcase_id="almira",
    is_default=False,
    hosts=("academy.almira.by",),
    owner_actor_id="almira",
    brand_name="ALMIRA",
    academy_title="Онлайн-академия ALMIRA",
    links={
        "privacy_url": "https://almira.by/privacy-policy/",
        "contact_url": "https://t.me/alm_school",
        "contact_label": "Написать студии",
    },
    telegram_binding_id="almira-academy-bot",
)


@pytest.fixture
def almira_binding(whieda_bot_binding):
    return replace(
        whieda_bot_binding, binding_id="almira-academy-bot", bot_username="ALMIRA_I_bot", showcase_id="almira"
    )


def _private(text: str) -> dict:
    return {"message": {"message_id": 5, "text": text, "chat": {"id": 300, "type": "private"}, "from": {"id": 300}}}


@pytest.mark.asyncio
async def test_start_in_showcase_bot_greets_with_policy_and_courses(whieda_tenant, almira_binding):
    sent = AsyncMock()
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()) as actor, patch(
        "app.telegram.showcase_bot.record_telegram_consent", AsyncMock(return_value=True)
    ) as consent, patch(
        "app.telegram.showcase_bot.with_site_login", AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=x")
    ), patch(
        "app.telegram.showcase_bot.show_academy_home", AsyncMock(return_value={"status": "courses"})
    ) as home, patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor, patch(
        "app.telegram.processor.show_referral_dashboard", AsyncMock()
    ) as dashboard:
        result = await process_core_telegram_update(whieda_tenant, _private("/start"), "t", binding=almira_binding)
    assert result["route"] == "showcase_welcome" and result["showcase"] == "almira"
    text = sent.await_args.kwargs["text"]
    assert "Онлайн-академия ALMIRA" in text and "https://almira.by/privacy-policy/" in text
    rows = sent.await_args.kwargs["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["url"] == "https://academy.almira.by/academy/#wwc-login=x"
    assert [row[0].get("callback_data") for row in rows[1:]] == ["shs:open:author", "shs:open:admin"]
    assert consent.await_args.kwargs["policy_version"].startswith("showcase:almira:")
    actor.assert_awaited_once()
    assert home.await_args.args[3] is ALMIRA
    advisor.assert_not_called()
    dashboard.assert_not_called()


@pytest.mark.asyncio
async def test_policy_is_shown_once(whieda_tenant, almira_binding):
    sent = AsyncMock()
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()), patch(
        "app.telegram.showcase_bot.record_telegram_consent", AsyncMock(return_value=False)
    ), patch("app.telegram.showcase_bot.with_site_login", AsyncMock(side_effect=lambda url, **_: url)), patch(
        "app.telegram.showcase_bot.show_academy_home", AsyncMock(return_value={})
    ), patch("app.telegram.showcase_bot.try_handle_private_message", AsyncMock(return_value=None)):
        await process_core_telegram_update(whieda_tenant, _private("привет"), "t", binding=almira_binding)
    assert "privacy-policy" not in sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_showcase_bot_is_silent_in_groups(whieda_tenant, almira_binding):
    update = {"message": {"text": "Новая заявка", "chat": {"id": -100, "type": "supergroup"}, "from": {"id": 1}}}
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.send_telegram_text", AsyncMock()
    ) as sent:
        result = await process_core_telegram_update(whieda_tenant, update, "t", binding=almira_binding)
    assert result["route"] == "showcase_ignored"
    sent.assert_not_called()


@pytest.mark.asyncio
async def test_course_key_and_author_commands_go_to_the_academy(whieda_tenant, almira_binding):
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()
    ), patch(
        "app.telegram.showcase_bot.handle_course_start_token", AsyncMock(return_value={"route": "academy_key"})
    ) as key, patch(
        "app.telegram.showcase_bot.try_handle_academy_text", AsyncMock(return_value={"route": "academy_keys"})
    ) as text:
        assert (await process_core_telegram_update(
            whieda_tenant, _private("/start course_abcdefghjkmn"), "t", binding=almira_binding
        ))["route"] == "academy_key"
        assert (await process_core_telegram_update(
            whieda_tenant, _private("ключи kompoziciya 3"), "t", binding=almira_binding
        ))["route"] == "academy_keys"
    key.assert_awaited_once()
    text.assert_awaited_once()


@pytest.mark.asyncio
async def test_showcase_lookup_failure_keeps_second_bot_quiet(whieda_tenant, almira_binding):
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})
    ) as advisor:
        quiet = await process_core_telegram_update(whieda_tenant, _private("вопрос"), "t", binding=almira_binding)
    assert quiet["route"] == "showcase_unavailable"
    advisor.assert_not_called()


@pytest.mark.asyncio
async def test_main_bot_without_showcase_keeps_its_processor(whieda_tenant, whieda_bot_binding):
    lookup = AsyncMock(return_value=None)
    with patch("app.telegram.processor.showcase_for_binding", lookup), patch(
        "app.telegram.processor.handle_start_token", AsyncMock(return_value={"ok": True, "route": "start_token"})
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _private("/start opaque-token"), "t", binding=whieda_bot_binding
        )
    assert result["route"] == "start_token"
    lookup.assert_not_called()  # бот процесса витрины не ищет


@pytest.mark.asyncio
async def test_site_login_on_showcase_uses_its_bot(client):
    created = AsyncMock(return_value={"ok": True, "deep_link": "https://t.me/ALMIRA_I_bot?start=content_access_x"})
    with patch("app.content_access.routes.showcase_for_request", AsyncMock(return_value=ALMIRA)), patch(
        "app.content_access.routes.login_bot_username", AsyncMock(return_value="ALMIRA_I_bot")
    ), patch("app.content_access.routes.create_content_challenge", created):
        response = await client.post(
            "/api/v1/content-access/challenges",
            headers={"x-forwarded-host": "wwc.best"},
            json={"browser_nonce": "n" * 20, "return_to": "/academy/"},
        )
    assert response.status_code == 200
    assert created.await_args.kwargs["bot_username"] == "ALMIRA_I_bot"


@pytest.mark.asyncio
async def test_public_showcase_config_needs_no_session(client):
    with patch("app.academy.routes.showcase_for_request", AsyncMock(return_value=ALMIRA)), patch(
        "app.academy.showcases.binding_username", AsyncMock(return_value="ALMIRA_I_bot")
    ):
        response = await client.get("/api/v1/content-access/academy/showcase", headers={"x-forwarded-host": "wwc.best"})
    assert response.status_code == 200
    body = response.json()["showcase"]
    assert body["id"] == "almira" and body["bot_url"] == "https://t.me/ALMIRA_I_bot"
    assert body["academy_title"] == "Онлайн-академия ALMIRA"
    assert body["links"]["privacy_url"] == "https://almira.by/privacy-policy/"


def _start(text: str, *, username: str | None = "anna_p") -> dict:
    user = {"id": 301, "first_name": "Анна", "last_name": "Петрова", **({"username": username} if username else {})}
    return {"message": {"message_id": 7, "text": text, "chat": {"id": 301, "type": "private"}, "from": user}}


def _ad_patches(sent, *, admin: int | None = 5035800725):
    return (
        patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)),
        patch("app.telegram.showcase_bot.send_telegram_text", sent),
        patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()),
        patch("app.telegram.showcase_bot.record_telegram_consent", AsyncMock(return_value=True)),
        patch("app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"author": 739736805, "admin": admin})),
        patch("app.telegram.showcase_bot.show_academy_home", AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tag, date", [("ig_web24", "24 октября"), ("ig_web31", "31 октября")])
async def test_ad_lead_webinar_greets_with_date_buttons_and_notifies_admin(whieda_tenant, almira_binding, tag, date):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    with p[0], p[1], p[2], p[3], p[4], p[5] as home:
        result = await process_core_telegram_update(whieda_tenant, _start(f"/start {tag}"), "t", binding=almira_binding)
    assert (result["route"], result["tag"], result["admin_notified"]) == ("showcase_ad_lead", tag, True)
    home.assert_not_called()
    lead, admin = sent.await_args_list
    assert lead.kwargs["chat_id"] == "301"
    assert f"бесплатный вебинар Александры Пармон по композиции — {date}" in lead.kwargs["text"]
    assert "https://almira.by/privacy-policy/" in lead.kwargs["text"]
    rows = lead.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in rows] == ["shs:open:author", "shs:open:admin"]
    assert admin.kwargs["chat_id"] == "5035800725"
    assert admin.kwargs["text"] == (
        f"Заявка с рекламы открыла бота: Анна Петрова (@anna_p), id 301. Метка: {tag} — вебинар {date}. "
        "Телефон и ответы формы — в таблице «ALMIRA · Заявки»."
    )


@pytest.mark.asyncio
async def test_ad_lead_other_tag_without_date_and_without_admin(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent, admin=None)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        result = await process_core_telegram_update(
            whieda_tenant, _start("/start ig_kurs", username=None), "t", binding=almira_binding
        )
    assert (result["route"], result["admin_notified"]) == ("showcase_ad_lead", False)
    [lead] = sent.await_args_list
    assert "оставили заявку. Мы скоро свяжемся с вами здесь" in lead.kwargs["text"]
    assert "вебинар" not in lead.kwargs["text"]


@pytest.mark.asyncio
async def test_bad_ad_tag_falls_back_to_welcome(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    with p[0], p[1], p[2], p[3], p[4], p[5] as home, patch(
        "app.telegram.showcase_bot.with_site_login", AsyncMock(side_effect=lambda url, **_: url)
    ):
        result = await process_core_telegram_update(whieda_tenant, _start("/start ig_<b>"), "t", binding=almira_binding)
    assert result["route"] == "showcase_welcome"
    home.assert_awaited_once()
