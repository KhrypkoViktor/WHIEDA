"""Бот и кабинет /me/: кнопка «Открыть кабинет», короткий текст, модерация профиля,
«Продлить» и «Поддержка» по ссылке с сайта."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.settings import get_settings
from app.telegram import cabinet_profile as moderation
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update
from app.telegram.referral_bonus import _dashboard_keyboard, cabinet_page_url, cabinet_text, show_referral_dashboard
from app.telegram.update_parser import parse_telegram_callback, parse_telegram_message

OWNER = 688931415
PARTNER = 525317405
REQUEST_ID = "1a2b3c4d-1111-2222-3333-444455556666"
TOKEN = REQUEST_ID.replace("-", "")
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _callback(data: str, *, user_id: int = OWNER, message_id: int = 77):
    parsed = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-1",
                "data": data,
                "from": {"id": user_id},
                "message": {"message_id": message_id, "chat": {"id": user_id, "type": "private"}},
            }
        }
    )
    assert parsed is not None
    return parsed


def _message(text: str, *, user_id: int = OWNER, reply_to: dict | None = None):
    message = {"message_id": 5, "text": text, "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id}}
    if reply_to is not None:
        message["reply_to_message"] = reply_to
    return {"message": message}


CARD = {
    "request_id": REQUEST_ID,
    "ref_code": "olga-samtsova",
    "telegram_user_id": PARTNER,
    "telegram_chat_id": str(PARTNER),
    "status": "pending",
    "public_profile": {"display_name": "Ольга Самцова", "subdomain": "samtsova"},
    "actor_name": "@olga",
    "changes": {
        "display_name": "Ольга С.",
        "bio": "Новый текст о себе",
        "contacts": {"phone": "+79991112233"},
        "socials": {"vk_url": None},
    },
    "previous": {
        "display_name": "Ольга Самцова",
        "bio": None,
        "contacts": {"phone": None},
        "socials": {"vk_url": "https://vk.com/olga"},
    },
}


# ---- кабинет в боте ----------------------------------------------------------------------


def test_cabinet_button_is_first_and_keeps_the_rest_of_the_keyboard():
    plain = _dashboard_keyboard(
        bot_username="WHIEDA_bot", invite_code="c0de1234", site_url="https://dev.wwc.best/", has_site=True, minimal=True
    )
    with_cabinet = _dashboard_keyboard(
        bot_username="WHIEDA_bot", invite_code="c0de1234", site_url="https://dev.wwc.best/", has_site=True, minimal=True,
        cabinet_url="https://dev.wwc.best/me/#wwc-login=x.y",
    )
    rows = with_cabinet["inline_keyboard"]
    assert rows[0] == [{"text": "Открыть кабинет", "url": "https://dev.wwc.best/me/#wwc-login=x.y"}]
    assert rows[1:] == plain["inline_keyboard"]
    assert cabinet_page_url("https://dev.wwc.best/") == "https://dev.wwc.best/me/"
    assert cabinet_page_url(None) == "https://wwc.best/me/"


def test_cabinet_text_is_status_balance_and_link_only():
    link = "https://t.me/WHIEDA_bot?start=ref_c0de1234"
    site = {"url": "https://samtsova.wwc.best/", "subscription_status": "active", "paid_until": NOW + timedelta(days=84),
            "days_remaining": 84}
    text = cabinet_text(site, balance_minor=1250, link=link)
    assert text.splitlines()[:4] == ["Личный кабинет", "", "Сайт samtsova.wwc.best: активен до 25.12.2026, ещё 84 дн.",
                                     "Баланс: 12,50 WWC$"]
    assert link in text and "в кабинете на сайте" in text
    for gone in ("Приглашено", "Оплатили", "20%", "вывести"):
        assert gone not in text
    assert "Сайт: пока нет." in cabinet_text(None, balance_minor=0, link=link)
    assert "ждёт продления" in cabinet_text({**site, "subscription_status": "suspended"}, balance_minor=0, link=link)
    assert len(text.splitlines()) <= 9


@pytest.mark.asyncio
async def test_bot_cabinet_signs_the_person_in_on_the_me_page(whieda_tenant, whieda_bot_binding):
    dashboard = {"balance_wusd_minor": 600, "invited_count": 2, "paid_count": 1, "history": [], "plans": [],
                 "site": {"url": "https://samtsova.wwc.best/", "subscription_status": "active", "paid_until": NOW,
                          "days_remaining": 3}, "referrals": []}
    logins = []

    async def fake_login(url, *, tenant_id, telegram_user_id):
        logins.append(url)
        return f"{url}#wwc-login=t{len(logins)}"

    with patch("app.telegram.referral_bonus._actor_for_telegram", AsyncMock(return_value="actor")), patch(
        "app.telegram.referral_bonus.get_or_create_invite_code", AsyncMock(return_value="c0de1234")
    ), patch("app.telegram.referral_bonus.referral_dashboard", AsyncMock(return_value=dashboard)), patch(
        "app.telegram.referral_bonus.academy_button_rows", AsyncMock(return_value=[])
    ), patch("app.telegram.referral_bonus.crm_button_rows", AsyncMock(return_value=[])), patch(
        "app.telegram.referral_bonus.with_site_login", fake_login
    ), patch("app.telegram.referral_bonus._deliver", AsyncMock()) as deliver:
        with binding_context_scope(whieda_bot_binding):
            await show_referral_dashboard(
                whieda_tenant, telegram_user_id=PARTNER, telegram_chat_id=PARTNER, raw_update={}, trace_id="t"
            )
    markup = deliver.await_args.kwargs["reply_markup"]
    assert markup["inline_keyboard"][0][0] == {"text": "Открыть кабинет", "url": "https://samtsova.wwc.best/me/#wwc-login=t2"}
    assert logins == ["https://samtsova.wwc.best/", "https://samtsova.wwc.best/me/"]
    assert "Баланс: 6 WWC$" in deliver.await_args.args[1]


# ---- карточка владельцу ---------------------------------------------------------------------


def test_moderation_card_shows_was_and_becomes_per_field():
    text = moderation.moderation_text(CARD, photo_sent=False)
    lines = text.splitlines()
    assert lines[0] == "Заявка на изменение сайта — Ольга Самцова (olga-samtsova)"
    assert lines[1] == "samtsova.wwc.best · заявка №1a2b3c4d"
    assert "Имя: Ольга Самцова → Ольга С." in lines
    assert ["О себе:", "было: —", "стало: Новый текст о себе"] == lines[lines.index("О себе:"):lines.index("О себе:") + 3]
    assert "Телефон: — → +79991112233" in lines
    assert "ВКонтакте: https://vk.com/olga → —" in lines
    photo = moderation.moderation_text({**CARD, "changes": {"photo_url": "https://wwc.best/p.jpg"}, "previous": {}}, photo_sent=True)
    assert photo.splitlines()[-1] == "Фото: новое — выше."
    assert moderation.moderation_keyboard(REQUEST_ID) == {
        "inline_keyboard": [[
            {"text": "Применить", "callback_data": f"prof:apply:{TOKEN}"},
            {"text": "Отклонить", "callback_data": f"prof:reject:{TOKEN}"},
        ]]
    }
    assert len(f"prof:reject:{TOKEN}".encode()) <= 64


def test_partner_texts_are_short_and_promise_nothing():
    applied = moderation.applied_text(CARD["changes"])
    assert applied == "Изменения на сайте применены: имя, «о себе», телефон, ВКонтакте."
    assert "минут" not in applied  # без обещаний, когда именно обновится сайт
    rejected = moderation.rejected_text("Фото размытое")
    assert rejected.splitlines() == [
        "Отклонено: Фото размытое.",
        "Изменения на сайте не применены — поправьте в кабинете и отправьте ещё раз.",
    ]
    assert moderation.rejected_text(None).splitlines()[0] == "Отклонено."


# ---- кнопки и причина -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_owner_can_apply(whieda_tenant, whieda_bot_binding, owner_env):
    with patch.object(moderation, "answer_callback_query", AsyncMock()), patch.object(
        moderation, "_deliver", AsyncMock(return_value={"ok": True})
    ) as deliver, patch.object(moderation, "apply_profile_request", AsyncMock()) as apply:
        with binding_context_scope(whieda_bot_binding):
            result = await moderation.try_handle_profile_callback(
                whieda_tenant, _callback(f"prof:apply:{TOKEN}", user_id=PARTNER), trace_id="t"
            )
    assert result["status"] == "forbidden"
    apply.assert_not_awaited()
    assert deliver.await_args.args[1] == "Команда недоступна."


@pytest.mark.asyncio
async def test_apply_updates_site_notifies_partner_and_drops_buttons(whieda_tenant, whieda_bot_binding, owner_env):
    applied = {"applied": True, "status": "applied", "request": {"ref_code": "olga-samtsova", "changes": CARD["changes"]},
               "profile_version": 8}
    with patch.object(moderation, "answer_callback_query", AsyncMock()), patch.object(
        moderation, "apply_profile_request", AsyncMock(return_value=applied)
    ) as apply, patch.object(moderation, "load_request_card", AsyncMock(return_value={**CARD, "status": "applied"})), patch.object(
        moderation, "with_site_login", AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=t")
    ), patch.object(moderation, "_call_telegram", AsyncMock(return_value={"ok": True})) as edit, patch.object(
        moderation, "_deliver", AsyncMock(return_value={"ok": True})
    ) as deliver:
        with binding_context_scope(whieda_bot_binding):
            result = await moderation.try_handle_profile_callback(whieda_tenant, _callback(f"prof:apply:{TOKEN}"), trace_id="t")
    assert result["status"] == "applied"
    assert apply.await_args.args == ("whieda", REQUEST_ID) and apply.await_args.kwargs == {"reviewer_id": OWNER}
    partner_call, owner_call = deliver.await_args_list
    assert partner_call.args[0] == PARTNER and partner_call.args[1].startswith("Изменения на сайте применены")
    assert partner_call.kwargs["reply_markup"] == {
        "inline_keyboard": [[{"text": "Открыть кабинет", "url": "https://samtsova.wwc.best/me/#wwc-login=t"}]]
    }
    assert owner_call.args == (OWNER, "Применено: olga-samtsova.")
    assert edit.await_args.args[0] == "editMessageReplyMarkup"
    assert edit.await_args.args[1]["message_id"] == 77


@pytest.mark.asyncio
async def test_second_press_and_old_cards_change_nothing(whieda_tenant, whieda_bot_binding, owner_env):
    for status, word in (("applied", "применена"), ("replaced", "заменена новой"), ("cancelled", "отозвана партнёром")):
        with patch.object(moderation, "answer_callback_query", AsyncMock()), patch.object(
            moderation, "apply_profile_request", AsyncMock(return_value={"applied": False, "status": status, "request": {}})
        ), patch.object(moderation, "_deliver", AsyncMock(return_value={"ok": True})) as deliver:
            with binding_context_scope(whieda_bot_binding):
                await moderation.try_handle_profile_callback(whieda_tenant, _callback(f"prof:apply:{TOKEN}"), trace_id="t")
        assert deliver.await_args.args[1] == f"Заявка уже {word}."


@pytest.mark.asyncio
async def test_reject_asks_for_a_reason_with_force_reply(whieda_tenant, whieda_bot_binding, owner_env):
    with patch.object(moderation, "answer_callback_query", AsyncMock()), patch.object(
        moderation, "request_status", AsyncMock(return_value={"request_id": REQUEST_ID, "ref_code": "olga-samtsova", "status": "pending"})
    ), patch.object(moderation, "mark_reason_asked", AsyncMock()) as asked, patch.object(
        moderation, "reject_profile_request", AsyncMock()
    ) as reject, patch.object(moderation, "_deliver", AsyncMock(return_value={"ok": True, "message_id": 901})) as deliver:
        with binding_context_scope(whieda_bot_binding):
            result = await moderation.try_handle_profile_callback(whieda_tenant, _callback(f"prof:reject:{TOKEN}"), trace_id="t")
    assert result["status"] == "reason_asked"
    reject.assert_not_awaited()  # отклоняет только ответ с причиной
    text = deliver.await_args.args[1]
    assert text.startswith("Причина отказа — заявка №1a2b3c4d (olga-samtsova).")
    assert deliver.await_args.kwargs["reply_markup"]["force_reply"] is True
    assert asked.await_args.kwargs == {"prompt_message_id": 901}


@pytest.mark.asyncio
async def test_owner_reply_is_the_reason_and_reaches_the_partner(whieda_tenant, whieda_bot_binding, owner_env):
    prompt = {"message_id": 901, "from": {"id": 1, "is_bot": True},
              "text": "Причина отказа — заявка №1a2b3c4d (olga-samtsova). Ответьте на это сообщение одной строкой; без причины — «-»."}
    found = {"request_id": REQUEST_ID, "ref_code": "olga-samtsova", "status": "pending"}
    with patch.object(moderation, "find_pending_by_short_id", AsyncMock(return_value=found)) as lookup, patch.object(
        moderation, "reject_profile_request", AsyncMock(return_value={"rejected": True, "status": "rejected"})
    ) as reject, patch.object(moderation, "load_request_card", AsyncMock(return_value=CARD)), patch.object(
        moderation, "with_site_login", AsyncMock(side_effect=lambda url, **_: url)
    ), patch.object(moderation, "_retire_card", AsyncMock()) as retire, patch.object(
        moderation, "_deliver", AsyncMock(return_value={"ok": True})
    ) as deliver:
        with binding_context_scope(whieda_bot_binding):
            msg = parse_telegram_message(_message("  Фото   размытое ", reply_to=prompt))
            result = await moderation.try_handle_profile_reject_reason(whieda_tenant, msg, trace_id="t")
            minus = parse_telegram_message(_message("-", reply_to=prompt))
            await moderation.try_handle_profile_reject_reason(whieda_tenant, minus, trace_id="t")
    assert result["status"] == "rejected"
    assert lookup.await_args.args == ("whieda", "1a2b3c4d")
    assert reject.await_args_list[0].kwargs == {"reviewer_id": OWNER, "reason": "Фото размытое"}
    assert reject.await_args_list[1].kwargs["reason"] is None
    texts = [call.args[1] for call in deliver.await_args_list]
    assert texts[0].startswith("Отклонено: Фото размытое.")
    assert texts[1] == "Отклонено: olga-samtsova. Партнёр получил ответ."
    assert retire.await_args_list[0].args == ("whieda", REQUEST_ID)  # кнопки под карточкой сняты


@pytest.mark.asyncio
async def test_other_replies_are_not_taken_for_a_reason(whieda_tenant, owner_env):
    prompt = {"message_id": 901, "from": {"id": 1, "is_bot": True}, "text": "Причина отказа — заявка №1a2b3c4d (x)."}
    with patch.object(moderation, "find_pending_by_short_id", AsyncMock()) as lookup:
        for update in (
            _message("причина"),  # не ответ
            _message("причина", reply_to={**prompt, "from": {"id": 5, "is_bot": False}}),  # ответ человеку
            _message("причина", reply_to={**prompt, "text": "Чек получен."}),  # ответ на другое сообщение бота
            _message("причина", user_id=PARTNER, reply_to=prompt),  # не владелец
        ):
            assert await moderation.try_handle_profile_reject_reason(
                whieda_tenant, parse_telegram_message(update), trace_id="t"
            ) is None
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_profiles_command_resends_pending_cards(whieda_tenant, whieda_bot_binding, owner_env):
    with patch.object(
        moderation, "list_pending_requests", AsyncMock(return_value=[{"request_id": REQUEST_ID}])
    ) as pending, patch.object(moderation, "send_moderation_card", AsyncMock(return_value=True)) as card:
        with binding_context_scope(whieda_bot_binding):
            result = await moderation.try_handle_profile_requests_command(
                whieda_tenant, parse_telegram_message(_message("/profiles")), trace_id="t"
            )
            assert await moderation.try_handle_profile_requests_command(
                whieda_tenant, parse_telegram_message(_message("/profiles", user_id=PARTNER)), trace_id="t"
            ) is None
    assert result["count"] == 1
    card.assert_awaited_once_with("whieda", REQUEST_ID, chat_id=OWNER)
    # Только заявки этого бота (staging и бой делят базу) и ещё не разосланные.
    assert pending.await_args.kwargs == {"binding_id": whieda_bot_binding.binding_id}
    assert moderation.COMMAND_RE.fullmatch("правки сайтов")


# ---- уведомление владельцу с сайта --------------------------------------------------------------


@pytest.mark.asyncio
async def test_site_request_reaches_the_owner_through_this_process_bot(whieda_bot_binding, owner_env):
    with patch.object(moderation, "process_bot_binding", AsyncMock(return_value=whieda_bot_binding)), patch.object(
        moderation, "load_request_card", AsyncMock(return_value=CARD)
    ), patch.object(moderation, "record_owner_card", AsyncMock()) as record, patch.object(
        moderation, "send_telegram_text", AsyncMock(return_value={"ok": True, "message_id": 42})
    ) as send:
        assert await moderation.notify_owner_about_profile_request("whieda", REQUEST_ID) is True
    assert send.await_args.kwargs["chat_id"] == str(OWNER)
    assert send.await_args.kwargs["bot_token"] == whieda_bot_binding.bot_token
    assert send.await_args.kwargs["reply_markup"] == moderation.moderation_keyboard(REQUEST_ID)
    record.assert_awaited_once_with(
        "whieda", REQUEST_ID, chat_id=OWNER, message_id=42, binding_id=whieda_bot_binding.binding_id
    )


@pytest.mark.asyncio
async def test_photo_goes_to_the_owner_as_a_file_before_the_card(whieda_bot_binding, owner_env):
    card = {**CARD, "changes": {"photo_url": "https://wwc.best/api/v1/content-access/partner-media/3f1c2a9e-0b7d-4c55-9a1e-2d3f4b5c6d7e.jpg"},
            "previous": {"photo_url": None}}
    with patch.object(moderation, "load_request_card", AsyncMock(return_value=card)), patch.object(
        moderation, "load_media_body", AsyncMock(return_value=b"\xff\xd8jpeg")
    ) as body, patch.object(moderation, "_send_photo_upload", AsyncMock(return_value={"ok": True})) as photo, patch.object(
        moderation, "send_telegram_text", AsyncMock(return_value={"ok": True, "message_id": 1})
    ) as send, patch.object(moderation, "record_owner_card", AsyncMock()):
        with binding_context_scope(whieda_bot_binding):
            assert await moderation.send_moderation_card("whieda", REQUEST_ID, chat_id=OWNER) is True
    assert body.await_args.args == ("whieda", "3f1c2a9e-0b7d-4c55-9a1e-2d3f4b5c6d7e")
    assert photo.await_args.kwargs["body"] == b"\xff\xd8jpeg"
    assert "Фото: новое — выше." in send.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_owner_notice_fails_softly(monkeypatch):
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    get_settings.cache_clear()
    try:
        assert await moderation.notify_owner_about_profile_request("whieda", REQUEST_ID) is False
        monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
        get_settings.cache_clear()
        with patch.object(moderation, "process_bot_binding", AsyncMock(return_value=None)):
            assert await moderation.notify_owner_about_profile_request("whieda", REQUEST_ID) is False
        with patch.object(moderation, "process_bot_binding", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await moderation.notify_owner_about_profile_request("whieda", REQUEST_ID) is False
    finally:
        get_settings.cache_clear()


# ---- маршрутизация в processor ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_renew_link_opens_renewal_on_full_and_manual_notice_on_minimal(whieda_tenant, whieda_bot_binding, monkeypatch):
    update = _message("/start renew", user_id=PARTNER)
    try:
        monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "full")
        get_settings.cache_clear()
        with patch(
            "app.telegram.processor.start_renewal_from_link",
            AsyncMock(return_value={"ok": True, "route": "renewal", "status": "awaiting_period"}),
        ) as renew, patch("app.telegram.processor.handle_start_token", AsyncMock()) as other:
            result = await process_core_telegram_update(whieda_tenant, update, "r1", binding=whieda_bot_binding)
        assert result["route"] == "renewal"
        renew.assert_awaited_once()
        other.assert_not_called()

        monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "minimal")
        get_settings.cache_clear()
        with patch("app.telegram.processor.start_renewal_from_link", AsyncMock()) as renew, patch(
            "app.telegram.processor.deliver_text", AsyncMock()
        ) as notice:
            result = await process_core_telegram_update(whieda_tenant, update, "r2", binding=whieda_bot_binding)
        assert result["route"] == "manual_partner_operation"
        renew.assert_not_called()
        assert "вручную" in notice.await_args.args[1]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_support_link_opens_the_same_ticket_as_slash_support(whieda_tenant, whieda_bot_binding):
    with patch(
        "app.telegram.processor.open_site_support", AsyncMock(return_value={"ok": True, "route": "support"})
    ) as support, patch("app.telegram.processor.handle_start_token", AsyncMock()) as other:
        result = await process_core_telegram_update(
            whieda_tenant, _message("/start support", user_id=PARTNER), "s1", binding=whieda_bot_binding
        )
    assert result["route"] == "support"
    support.assert_awaited_once()
    other.assert_not_called()


@pytest.mark.asyncio
async def test_profile_buttons_work_on_the_production_profile(whieda_tenant, whieda_bot_binding, monkeypatch):
    monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "minimal")
    get_settings.cache_clear()
    update = {"callback_query": {"id": "cb", "data": f"prof:apply:{TOKEN}", "from": {"id": OWNER},
                                 "message": {"message_id": 3, "chat": {"id": OWNER, "type": "private"}}}}
    try:
        with patch(
            "app.telegram.processor.try_handle_profile_callback",
            AsyncMock(return_value={"ok": True, "route": "cabinet_profile", "status": "applied"}),
        ) as handled:
            result = await process_core_telegram_update(whieda_tenant, update, "p1", binding=whieda_bot_binding)
    finally:
        get_settings.cache_clear()
    assert result["route"] == "cabinet_profile"
    handled.assert_awaited_once()


@pytest.mark.asyncio
async def test_reason_reply_is_checked_before_support_relay(whieda_tenant, whieda_bot_binding):
    update = _message("Фото размытое", reply_to={"message_id": 9, "from": {"id": 1, "is_bot": True}, "text": "Причина отказа — заявка №1a2b3c4d (x)."})
    with patch(
        "app.telegram.processor.try_handle_profile_reject_reason",
        AsyncMock(return_value={"ok": True, "route": "cabinet_profile_reason", "status": "rejected"}),
    ) as reason, patch("app.telegram.processor.try_handle_support_message", AsyncMock()) as support, patch(
        "app.telegram.processor._link_partner_chat", AsyncMock()
    ):
        result = await process_core_telegram_update(whieda_tenant, update, "q1", binding=whieda_bot_binding)
    assert result["route"] == "cabinet_profile_reason"
    reason.assert_awaited_once()
    support.assert_not_called()


@pytest.mark.asyncio
async def test_replaced_and_withdrawn_requests_lose_their_buttons(whieda_bot_binding, owner_env):
    with patch.object(moderation, "process_bot_binding", AsyncMock(return_value=whieda_bot_binding)), patch.object(
        moderation, "send_moderation_card", AsyncMock(return_value=True)
    ), patch.object(moderation, "load_owner_card", AsyncMock(return_value={"chat_id": OWNER, "message_id": 55})) as where, patch.object(
        moderation, "_call_telegram", AsyncMock(return_value={"ok": True})
    ) as edit:
        assert await moderation.notify_owner_about_profile_request("whieda", REQUEST_ID, replaced_request_id="old-1") is True
        await moderation.retire_owner_card("whieda", "old-2")
    assert [call.args for call in where.await_args_list] == [("whieda", "old-1"), ("whieda", "old-2")]
    assert edit.await_args_list[0].args == (
        "editMessageReplyMarkup",
        {"chat_id": OWNER, "message_id": 55, "reply_markup": {"inline_keyboard": []}},
    )
    assert edit.await_args_list[0].kwargs == {"bot_token": whieda_bot_binding.bot_token}


@pytest.mark.asyncio
async def test_long_request_goes_in_parts_and_the_card_keeps_the_buttons(whieda_bot_binding, owner_env):
    url = "https://t.me/" + "c" * 280
    card = {
        **CARD,
        "changes": {
            "bio": "б" * 600,
            "contacts": {"max_url": "https://max.ru/" + "m" * 280, "email": "e" * 240 + "@mail.ru",
                         "address": "а" * 200},
            "socials": {key: url for key in ("telegram_channel_url", "vk_url", "instagram_url", "youtube_url", "tiktok_url")},
        },
        "previous": {
            "bio": "п" * 600,
            "contacts": {"max_url": "https://max.ru/" + "o" * 280, "email": "o" * 240 + "@mail.ru", "address": "о" * 200},
            "socials": {key: "https://t.me/" + "o" * 280 for key in ("telegram_channel_url", "vk_url", "instagram_url",
                                                                     "youtube_url", "tiktok_url")},
        },
    }
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)
        return {"ok": True, "message_id": len(sent)}

    with patch.object(moderation, "load_request_card", AsyncMock(return_value=card)), patch.object(
        moderation, "send_telegram_text", send
    ), patch.object(moderation, "record_owner_card", AsyncMock()) as record:
        with binding_context_scope(whieda_bot_binding):
            assert await moderation.send_moderation_card("whieda", REQUEST_ID, chat_id=OWNER) is True
    *parts, final = sent
    assert len(parts) >= 2
    assert all(len(moderation.format_telegram_html(item["text"])) <= 4096 for item in sent)
    joined = "\n".join(item["text"] for item in parts)
    assert "б" * 600 in joined and "п" * 600 in joined and url in joined  # ничего не обрезано
    assert final["reply_markup"] == moderation.moderation_keyboard(REQUEST_ID)
    assert final["text"].endswith("Изменения — в сообщениях выше.")
    assert record.await_args.kwargs["message_id"] == len(sent)
