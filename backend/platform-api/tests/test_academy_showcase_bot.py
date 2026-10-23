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


def _ad_patches(sent, *, admin: int | None = 5035800725, pending: bool = False):
    return (
        patch("app.telegram.showcase_bot.record_open", AsyncMock()),
        patch("app.telegram.showcase_bot.alert_pending", AsyncMock(return_value=pending)),
        patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)),
        patch("app.telegram.showcase_bot.send_telegram_text", sent),
        patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()),
        patch("app.telegram.showcase_bot.record_telegram_consent", AsyncMock(return_value=True)),
        patch("app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"author": 739736805, "admin": admin})),
        patch("app.telegram.showcase_bot.show_academy_home", AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["ig_web24", "ig_web31"])
async def test_ad_lead_webinar_greets_without_date_buttons_and_notifies_admin(whieda_tenant, almira_binding, tag):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    with p[0] as opened, p[1], p[2], p[3], p[4], p[5], p[6], p[7] as home:
        result = await process_core_telegram_update(whieda_tenant, _start(f"/start {tag}"), "t", binding=almira_binding)
    assert opened.await_args.kwargs == {
        "telegram_user_id": 301, "username": "anna_p", "name": "Анна Петрова", "tag": tag, "lead_id": None, "series": "",
    }
    assert (result["route"], result["tag"], result["admin_notified"]) == ("showcase_ad_lead", tag, True)
    home.assert_not_called()
    lead, admin = sent.await_args_list
    assert lead.kwargs["chat_id"] == "301"
    # Дату не пишем: кнопка формы у всех ведёт на ig_web24, дату подтверждает отдельное сообщение.
    assert "бесплатный вебинар Александры Пармон по композиции.\n\nСсылку на эфир пришлём сюда" in lead.kwargs["text"]
    assert "октября" not in lead.kwargs["text"]
    assert "https://almira.by/privacy-policy/" in lead.kwargs["text"]
    rows = lead.kwargs["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in rows] == ["shs:open:author", "shs:open:admin"]
    assert admin.kwargs["chat_id"] == "5035800725"
    # Таблицы в тесте нет — строка не нашлась, администратору подсказка про личную ссылку (19.10.2026).
    assert admin.kwargs["text"] == (
        '✅ <b><a href="tg://user?id=301">Анна Петрова</a></b> (@anna_p) — в боте, ссылку на эфир получит здесь.'
        " В таблице заявок не найдена — отправьте человеку личную ссылку из списка «не в боте»."
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("ig_web24_1817700742504331", ("ig_web24", "1817700742504331")),
        ("ig_web31_701234", ("ig_web31", "701234")),
        ("ig_web24", ("ig_web24", None)),
        ("ig_kurs_123456", ("ig_kurs_123456", None)),  # база — не вебинар
        ("ig_web24_12", ("ig_web24_12", None)),  # id короче 6 цифр
        ("ig_web24_" + "1" * 26, ("ig_web24_" + "1" * 26, None)),  # длиннее 25
        ("ig_web24_12ab56", ("ig_web24_12ab56", None)),  # не только цифры
        ("ig_web24_701234_5", ("ig_web24_701234_5", None)),  # лишний хвост
    ],
)
def test_split_ad_tag(token, expected):
    from app.telegram.showcase_bot import split_ad_tag

    assert split_ad_tag(token) == expected


def _lead(lead_id: str, name: str, username: str | None):
    from app.academy.ad_leads import Lead

    return Lead(lead_id, None, name, "+375291112233", username, f"@{username}" if username else "", None, "", "ig", "Вебинар")


def _sheet_patches(leads):
    fetch = AsyncMock(return_value=leads)
    return fetch, (
        patch("app.telegram.showcase_bot.sheet_config", return_value={"id": "sheet", "gid": "0", "since": ""}),
        patch("app.telegram.showcase_bot.fetch_leads", fetch),
    )


@pytest.mark.asyncio
async def test_personal_link_finds_the_row_by_id_with_one_sheet_fetch(whieda_tenant, almira_binding):
    """Кирилл шлёт ig_web24_<id> тем, кто бота не открывал (19.10.2026): имя и ник в Telegram не совпадают с формой,
    строку находит только id из ссылки."""
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    row = _lead("701234567", "Мария Иванова", "masha_i")
    fetch, sheet = _sheet_patches([_lead("700000001", "Дарья Звонкова", None), row])
    p = _ad_patches(sent)
    with p[0] as opened, p[1] as pending, p[2], p[3], p[4], p[5], p[6], p[7], sheet[0], sheet[1]:
        result = await process_core_telegram_update(
            whieda_tenant, _start("/start ig_web24_701234567"), "t", binding=almira_binding
        )
    assert opened.await_args.kwargs == {
        "telegram_user_id": 301, "username": "anna_p", "name": "Анна Петрова", "tag": "ig_web24", "lead_id": "701234567", "series": "",
    }
    assert (result["route"], result["tag"], result["admin_notified"]) == ("showcase_ad_lead", "ig_web24", True)
    fetch.assert_awaited_once()
    assert pending.await_args.args[2] is row
    greeting, admin = sent.await_args_list
    assert greeting.kwargs["chat_id"] == "301"
    assert "бесплатный вебинар Александры Пармон по композиции." in greeting.kwargs["text"]
    assert "октября" not in greeting.kwargs["text"]
    assert admin.kwargs["chat_id"] == "5035800725"
    assert "Мария Иванова" in admin.kwargs["text"] and "не найдена" not in admin.kwargs["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("sheet_nick", "lead_id", "hint"), [("masha_i", None, True), ("anna_p", "700000009", False)])
async def test_personal_link_with_id_missing_in_sheet_falls_back_to_the_usual_match(
    whieda_tenant, almira_binding, sheet_nick, lead_id, hint,
):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    fetch, sheet = _sheet_patches([_lead("700000009", "Мария Иванова", sheet_nick)])
    p = _ad_patches(sent)
    with p[0] as opened, p[1], p[2], p[3], p[4], p[5], p[6], p[7], sheet[0], sheet[1]:
        await process_core_telegram_update(
            whieda_tenant, _start("/start ig_web24_999999999"), "t", binding=almira_binding
        )
    fetch.assert_awaited_once()
    assert opened.await_args.kwargs["tag"] == "ig_web24" and opened.await_args.kwargs["lead_id"] == lead_id
    admin = sent.await_args_list[-1]
    assert admin.kwargs["chat_id"] == "5035800725"
    assert ("В таблице заявок не найдена — отправьте человеку личную ссылку" in admin.kwargs["text"]) is hint


@pytest.mark.asyncio
async def test_ad_lead_other_tag_without_date_and_without_admin(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent, admin=None)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        result = await process_core_telegram_update(
            whieda_tenant, _start("/start ig_kurs", username=None), "t", binding=almira_binding
        )
    assert (result["route"], result["admin_notified"]) == ("showcase_ad_lead", False)
    [lead] = sent.await_args_list
    assert "оставили заявку. Мы скоро свяжемся с вами здесь" in lead.kwargs["text"]
    assert "вебинар" not in lead.kwargs["text"]


@pytest.mark.asyncio
async def test_ad_lead_skips_admin_line_when_the_row_alert_will_carry_the_flag(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent, pending=True)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        result = await process_core_telegram_update(whieda_tenant, _start("/start ig_web24"), "t", binding=almira_binding)
    assert (result["route"], result["admin_notified"]) == ("showcase_ad_lead", False)
    [lead] = sent.await_args_list
    assert lead.kwargs["chat_id"] == "301"


@pytest.mark.asyncio
async def test_bad_ad_tag_falls_back_to_welcome(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7] as home, patch(
        "app.telegram.showcase_bot.with_site_login", AsyncMock(side_effect=lambda url, **_: url)
    ):
        result = await process_core_telegram_update(whieda_tenant, _start("/start ig_<b>"), "t", binding=almira_binding)
    assert result["route"] == "showcase_welcome"
    home.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_button_moves_date_confirms_and_tells_admin(whieda_tenant, almira_binding):
    from datetime import datetime, timedelta, timezone

    from app.academy.ad_leads import Opened
    from app.academy.webinars import Webinar

    w24 = Webinar("ig_web24", datetime(2099, 10, 24, 17, 30, tzinfo=timezone(timedelta(hours=3))), "24 октября", "")
    opened = Opened(301, "anna_p", "Анна Петрова", "702", None, "ig_web24", "ig_web24")
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    answered = AsyncMock(return_value={"ok": True})
    update = {"callback_query": {"id": "cb1", "data": "wbn:ig_web24", "from": {"id": 301},
                                 "message": {"message_id": 9, "chat": {"id": 301, "type": "private"}}}}
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.move_webinar", AsyncMock(return_value=(w24, opened))
    ) as moved, patch("app.telegram.showcase_bot.send_telegram_text", sent), patch(
        "app.telegram.showcase_bot.answer_callback_query", answered
    ), patch("app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"admin": 5035800725})):
        result = await process_core_telegram_update(whieda_tenant, update, "t", binding=almira_binding)
    assert (result["route"], result["moved"], result["webinar"]) == ("showcase_webinar_move", True, "ig_web24")
    assert moved.await_args.args[2:] == (301, "ig_web24")
    person, admin = sent.await_args_list
    assert person.kwargs["chat_id"] == "301" and person.kwargs["text"] == "Готово: вы записаны на 24 октября, начало в 17:30 по Минску."
    assert admin.kwargs["chat_id"] == "5035800725"
    assert admin.kwargs["text"] == '🔁 <b><a href="tg://user?id=301">Анна Петрова</a></b>: запись перенесена на 24 октября'


def _text(text: str, user_id: int) -> dict:
    return {"message": {"message_id": 8, "text": text, "chat": {"id": user_id, "type": "private"},
                        "from": {"id": user_id, "first_name": "Кирилл"}}}


@pytest.mark.asyncio
async def test_admin_asks_who_is_not_in_bot(whieda_tenant, almira_binding):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    texts = AsyncMock(return_value=["Вебинар 24 октября: в боте 18 из 28 заявок. Не в боте — 10 чел.:\n\n1. <b>Дарья</b>"])
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()), patch(
        "app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"author": 739736805, "admin": 5035800725})
    ), patch("app.telegram.showcase_bot.not_in_bot_texts", texts):
        result = await process_core_telegram_update(whieda_tenant, _text("Не в боте?", 5035800725), "t", binding=almira_binding)
    assert (result["route"], result["messages"]) == ("showcase_not_in_bot", 1)
    assert sent.await_args.kwargs["chat_id"] == "5035800725" and "Не в боте — 10 чел." in sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_plain_start_and_site_login_count_as_in_bot(whieda_tenant, almira_binding):
    """Латыпова (18.10.2026) вошла на сайт академии через Telegram — это тоже «в боте», без стирания метки рекламы."""
    from app.academy.ad_leads import BOT_TAG

    recorded = AsyncMock()
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.record_open", recorded
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()), patch(
        "app.telegram.showcase_bot.show_welcome", AsyncMock(return_value={"ok": True, "route": "showcase_welcome"})
    ):
        await process_core_telegram_update(whieda_tenant, _start("/start", username="logoped_latypova"), "t", binding=almira_binding)
    kwargs = recorded.await_args.kwargs
    assert (kwargs["tag"], kwargs["overwrite"], kwargs["username"], kwargs["telegram_user_id"]) == (BOT_TAG, False, "logoped_latypova", 301)

    login = AsyncMock()
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA)), patch(
        "app.telegram.showcase_bot.try_handle_content_access", AsyncMock(return_value={"ok": True, "route": "content_access"})
    ), patch("app.telegram.showcase_bot._first_policy_notice", AsyncMock(return_value=None)), patch(
        "app.telegram.showcase_bot.record_open", login
    ):
        await process_core_telegram_update(whieda_tenant, _start("/start ca_abc", username="logoped_latypova"), "t", binding=almira_binding)
    assert login.await_args.kwargs["tag"] == BOT_TAG and login.await_args.kwargs["overwrite"] is False


@pytest.mark.asyncio
async def test_admin_short_link_kvk_works_as_the_webinar_link(whieda_tenant, almira_binding):
    """?start=kvk (ссылка Кирилла, 20.10.2026) — как ig_web24: приветствие к эфиру и запись с меткой вебинара."""
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    kvk = replace(ALMIRA, support={"start_aliases": {"kvk": "ig_web24"}})
    with p[0] as opened, p[1], patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=kvk)),             p[3], p[4], p[5], p[6], p[7] as home:
        result = await process_core_telegram_update(whieda_tenant, _start("/start kvk"), "t", binding=almira_binding)
    assert (result["route"], result["tag"]) == ("showcase_ad_lead", "ig_web24")
    assert opened.await_args.kwargs["tag"] == "ig_web24"
    home.assert_not_called()


def test_start_alias_only_to_an_ad_tag():
    from app.telegram.showcase_bot import start_alias

    shop = replace(ALMIRA, support={"start_aliases": {"kvk": "ig_web24", "bad": "course_123"}})
    assert start_alias(shop, "kvk") == "ig_web24"
    assert start_alias(shop, "KVK") == "ig_web24"
    assert start_alias(shop, "bad") is None
    assert start_alias(shop, "other") is None
    assert start_alias(ALMIRA, "kvk") is None


@pytest.mark.asyncio
async def test_anatomy_link_greets_with_its_own_text_and_records_the_series(whieda_tenant, almira_binding):
    """/start ig_an28 (анатомия, 23.10.2026): своё приветствие из настройки, запись в серии anatomy; личная ссылка
    ig_an28_<id> тоже узнаётся."""
    from app.telegram.showcase_bot import split_ad_tag, webinar_tags

    support = {"webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_an28": {"at": "2026-10-28T19:30:00+03:00", "date": "28 октября", "url": "", "series": "anatomy",
                    "form": "анатом", "greeting": "Здравствуйте! Спасибо, что записались на вебинар по анатомии."},
    }}
    anatomy = replace(ALMIRA, support=support)
    assert split_ad_tag("ig_an28_123456789", webinar_tags(anatomy)) == ("ig_an28", "123456789")
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    p = _ad_patches(sent)
    with p[0] as opened, p[1], patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=anatomy)), \
            p[3], p[4], p[5], p[6], p[7]:
        result = await process_core_telegram_update(whieda_tenant, _start("/start ig_an28"), "t", binding=almira_binding)
    assert (result["route"], result["tag"]) == ("showcase_ad_lead", "ig_an28")
    assert opened.await_args.kwargs["series"] == "anatomy" and opened.await_args.kwargs["tag"] == "ig_an28"
    greeting = sent.await_args_list[0].kwargs["text"]
    assert greeting.startswith("Здравствуйте! Спасибо, что записались на вебинар по анатомии.")
    assert "по композиции" not in greeting
