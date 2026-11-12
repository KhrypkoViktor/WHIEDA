"""Тексты воронки, первая очередь (ТЗ 28, 11.11.2026): новая логика рядом с текстами.

Без базы и сети: всё, что снаружи модулей воронки, подменено на границах. Сами тексты и кнопки проверены рядом с прежними
тестами (test_lead_start_guest, test_meeting_reminders, test_meeting_after, test_bot_stages). Здесь:
  * кнопка «Я уже партнёр WHIEDA» и ответ на неё (раздел 8) в Telegram и Max;
  * метка источника → приветствие (1.3) и чтение метки из базы;
  * партнёру: «ваш человек записался» (6.1);
  * имя и статус для подписи в Zoom (4.4);
  * запреты на слова во всех новых текстах.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.funnel import guest, guest_bot, partner_answer, partner_notice, people, registration, texts
from app.funnel.guest import guest_menu_reply
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramCallbackQuery
from app.tenancy import TenantContext
from tests.test_lead_start_guest import NOW, THU, TUE, USER, guest_world, make_lead, make_registration, max_callback, tg_cb

TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})


class FakeConn:
    """Подключение, у которого есть ``transaction()`` (точка отката): исключение внутри считается откатом."""

    def __init__(self) -> None:
        self.rollbacks = 0

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        except BaseException:
            self.rollbacks += 1
            raise


# ---- запреты на слова во всех новых текстах -----------------------------------------------------------

NEW_TEXTS = [
    texts.SYSTEM_TOUR_CAPTION_GUEST, texts.SYSTEM_TOUR_CAPTION_GUEST_PLAIN,
    texts.GREETING_PARTNER, texts.GREETING_PARTNER_PLAIN, texts.GREETING_AD, texts.GREETING_ASK, texts.GREETING_ASK_PLAIN,
    texts.GUEST_PARTNER_WILL_CONTACT, texts.GUEST_NO_MEETINGS, texts.GUEST_NO_MEETINGS_WAITING, texts.GUEST_MEETING_APPEARED,
    texts.REGISTRATION_CONFIRMED, texts.REMINDER_DAY, texts.REMINDER_TWO_HOURS, texts.REMINDER_HOUR, texts.REMINDER_FIVE,
    texts.JOIN_NO_LINK, texts.AFTER_THANKS, texts.AFTER_CARD_PARTNER, texts.AFTER_CARD_CONSULT, texts.AFTER_AD_TEXT,
    texts.AFTER_NO_SHOW, texts.AFTER_NO_SHOW_RECORDING, texts.AFTER_NO_SHOW_NEXT, texts.AFTER_NO_SHOW_WAITING,
    texts.SUMMARY_HEAD, texts.SUMMARY_TAIL, texts.PARTNER_GUEST_REGISTERED, texts.PARTNER_ANSWER_TEXT, texts.PARTNER_ANSWER_VIDEO,
]


@pytest.mark.parametrize("text", NEW_TEXTS)
def test_new_texts_have_no_forbidden_words_and_fit_the_limit(text):
    lowered = text.lower()
    for word in ("млм", "сетев", "бот whieda", "карта системы whieda", "доход", "здоров", "гарант"):
        assert word not in lowered, word
    assert len(text) <= 350
    assert not re.search(r"\d+\s*(партн|сайт)", lowered)      # чисел партнёров и сайтов нет


def test_no_emoji_in_the_new_texts():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    assert not any(emoji.search(text) for text in NEW_TEXTS)


def test_the_old_promise_to_send_the_link_is_gone():
    for text in (texts.REMINDER_DAY, texts.REMINDER_TWO_HOURS, texts.REMINDER_HOUR, texts.REGISTRATION_CONFIRMED, texts.JOIN_NO_LINK):
        assert "пришлём" not in text


# ---- «Я уже партнёр WHIEDA»: разбор кнопки ---------------------------------------------------------------


def test_the_partner_button_is_parsed_and_registered_for_max():
    assert guest_bot.parse_guest_callback("funnel:partner") == guest_bot.GuestAction("partner")
    assert texts.FUNNEL_PARTNER_CALLBACK in guest_bot.GUEST_MAX_PREFIXES
    assert guest_bot.parse_guest_callback("funnel:partner:1") is None


# ---- раздел 8: ответ партнёру -------------------------------------------------------------------------------


@pytest.fixture
def answer_world(whieda_bot_binding, monkeypatch):
    state = SimpleNamespace(
        order=[],
        promote=AsyncMock(return_value="changed"),
        tg_text=AsyncMock(return_value={"ok": True}),
        tg_tour=AsyncMock(return_value={"ok": True, "status": "tour"}),
        max_text=AsyncMock(return_value={"ok": True}),
        max_tour=AsyncMock(return_value={"ok": True, "status": "tour"}),
        blocked=AsyncMock(return_value=False),
    )
    state.promote.side_effect = _record(state, "promote", "changed")
    state.tg_text.side_effect = _record(state, "text", {"ok": True})
    state.tg_tour.side_effect = _record(state, "tour", {"ok": True, "status": "tour"})
    state.max_text.side_effect = _record(state, "text", {"ok": True})
    state.max_tour.side_effect = _record(state, "tour", {"ok": True, "status": "tour"})
    monkeypatch.setattr(partner_answer, "promote", state.promote)
    monkeypatch.setattr(partner_answer, "guest_offers_blocked", state.blocked)
    monkeypatch.setattr("app.telegram.delivery.send_telegram_text", state.tg_text)
    monkeypatch.setattr("app.funnel.system_word.send_tour_telegram", state.tg_tour)
    monkeypatch.setattr("app.max.client.send_max_text", state.max_text)
    monkeypatch.setattr("app.funnel.system_word.send_tour_max", state.max_tour)
    with binding_context_scope(whieda_bot_binding):
        yield state


def _record(state, name, result):
    async def call(*args, **kwargs):
        state.order.append(name)
        return result

    return call


async def test_the_partner_answer_in_telegram_marks_the_person_and_sends_text_map_and_video(answer_world):
    callback = tg_cb("funnel:partner")
    with patch.object(guest_bot, "answer_callback_query", AsyncMock(return_value={"ok": True})):
        result = await guest_bot.try_handle_guest_callback(TENANT, callback, trace_id="t1")
    assert result["status"] == "partner_path" and result["stage"] == "changed"
    answer_world.promote.assert_awaited_once_with("whieda", "telegram", USER, "partner_whieda")
    assert answer_world.order == ["promote", "text", "tour", "text"]          # статус, текст, карта, ролик и цены
    first = answer_world.tg_text.await_args_list[0].kwargs
    assert first["text"] == texts.PARTNER_ANSWER_TEXT and first["chat_id"] == str(USER)
    # карта — с прежней подписью: partner=True; предложения (встреча, разбор) идут по признаку сети
    assert answer_world.tg_tour.await_args.kwargs == {"trace_id": "t1", "offers": True, "partner": True}
    video = answer_world.tg_text.await_args_list[1].kwargs
    assert video["text"] == texts.PARTNER_ANSWER_VIDEO
    assert video["reply_markup"] == {"inline_keyboard": [
        [{"text": "Ролик о системе", "url": texts.SYSTEM_TOUR_FALLBACK_URL}],
        [{"text": "Цены и подключение", "url": "https://wwc.best/start"}],
    ]}


async def test_the_partner_answer_goes_on_when_the_status_cannot_be_written(answer_world):
    answer_world.promote.side_effect = RuntimeError("db down")
    with patch.object(guest_bot, "answer_callback_query", AsyncMock(return_value={"ok": True})):
        result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("funnel:partner"), trace_id="t1")
    assert result["status"] == "partner_path" and result["stage"] == "error"
    assert answer_world.tg_tour.await_count == 1 and answer_world.tg_text.await_count == 2   # человек всё равно получил ответ


async def test_the_partner_answer_of_a_foreign_network_has_no_offers_under_the_map(answer_world):
    answer_world.blocked.return_value = True
    with patch.object(guest_bot, "answer_callback_query", AsyncMock(return_value={"ok": True})):
        await guest_bot.try_handle_guest_callback(TENANT, tg_cb("funnel:partner"), trace_id="t1")
    assert answer_world.tg_tour.await_args.kwargs["offers"] is False and answer_world.tg_tour.await_args.kwargs["partner"] is True


async def test_the_partner_answer_in_a_group_is_not_sent(answer_world):
    callback = TelegramCallbackQuery(chat_id=-5, user_id=USER, callback_query_id="cq", data="funnel:partner", chat_type="group", raw={})
    with patch.object(guest_bot, "answer_callback_query", AsyncMock(return_value={"ok": True})):
        result = await guest_bot.try_handle_guest_callback(TENANT, callback, trace_id="t1")
    assert result["status"] == "private_chat_required"
    answer_world.promote.assert_not_awaited()


async def test_the_partner_answer_in_max_takes_the_same_path(answer_world):
    from app.max import processor as max_processor

    await max_processor.process_max_event(TENANT, max_callback("funnel:partner"), "t1")
    answer_world.promote.assert_awaited_once_with("whieda", "max", USER, "partner_whieda")
    assert answer_world.order == ["promote", "text", "tour", "text"]
    assert answer_world.max_text.await_args_list[0].kwargs["text"] == texts.PARTNER_ANSWER_TEXT
    assert answer_world.max_tour.await_args.kwargs["partner"] is True
    video = answer_world.max_text.await_args_list[1].kwargs
    assert [b["text"] for row in video["buttons"] for b in row] == ["Ролик о системе", "Цены и подключение"]
    assert all(b["type"] == "link" for row in video["buttons"] for b in row)


# ---- метка источника → приветствие --------------------------------------------------------------------------


async def test_the_tag_is_read_from_the_last_start_source_row():
    conn = FakeConn()
    fetch = AsyncMock(return_value={"tag": "art-ashwagandha"})
    with patch.object(guest, "fetch_one", fetch):
        assert await guest.load_start_tag(conn, "whieda", "telegram", USER) == "art-ashwagandha"
    sql, params = fetch.await_args.args[1], fetch.await_args.args[2]
    assert "bot_start_sources" in sql and "order by start_id desc" in sql and params == ("whieda", "telegram", str(USER))
    with patch.object(guest, "fetch_one", AsyncMock(return_value=None)):
        assert await guest.load_start_tag(conn, "whieda", "telegram", USER) is None


async def test_a_failing_tag_read_gives_the_ordinary_greeting_and_does_not_break_the_transaction():
    conn = FakeConn()
    with patch.object(guest, "fetch_one", AsyncMock(side_effect=RuntimeError("no table"))):
        assert await guest.load_start_tag(conn, "whieda", "telegram", USER) is None
    assert conn.rollbacks == 1                                       # сбой остался в своей точке отката


async def test_the_search_tag_opens_the_question_greeting_in_telegram_and_max(monkeypatch):
    guest_world(monkeypatch, guest_context=guest_bot_context(), view=(None, [TUE]), card=SimpleNamespace(display_name="Игорь Волков"))
    for channel in ("telegram", "max"):
        monkeypatch.setattr(guest_bot, "load_start_tag", AsyncMock(return_value="site-club"))
        reply = await guest_bot._guest_menu("whieda", channel, USER)
        assert reply.text == "Вас пригласил Игорь Волков. " + texts.GREETING_ASK
        assert [data for row in reply.rows for _, data in row][:3] == ["funnel:ask", "funnel:tour", "funnel:meet"]
    monkeypatch.setattr(guest_bot, "load_start_tag", AsyncMock(return_value="vk"))
    assert (await guest_bot._guest_menu("whieda", "telegram", USER)).text.startswith("Вас пригласил Игорь Волков. Дарю карту системы")


def guest_bot_context():
    return guest.GuestContext(channel="telegram", channel_user_id=USER, lead=make_lead(telegram_user_id=USER))


# ---- 6.1: партнёру «ваш человек записался» -------------------------------------------------------------------


@pytest.fixture
def notice(monkeypatch):
    state = SimpleNamespace(
        owner=AsyncMock(return_value="igor-actor"),
        chat=AsyncMock(return_value=("7001", 7001)),
        binding=AsyncMock(return_value="whieda-advisor-bot"),
        name=AsyncMock(return_value="Анна Петрова"),
        card=AsyncMock(return_value="https://igor.wwc.best/crm/?contact=card-1"),
        outbox=AsyncMock(return_value={"created": True}),
    )
    monkeypatch.setattr(partner_notice, "guest_inviter_owner", state.owner)
    monkeypatch.setattr(partner_notice, "owner_chat", state.chat)
    monkeypatch.setattr(partner_notice, "notify_binding_id", state.binding)
    monkeypatch.setattr(partner_notice, "person_name", state.name)
    monkeypatch.setattr(partner_notice, "card_button_url", state.card)
    monkeypatch.setattr(partner_notice, "enqueue_outbox_event", state.outbox)
    monkeypatch.setattr(partner_notice, "get_settings", lambda: SimpleNamespace(platform_organic_owner_id="viktor"))
    return state


async def notify(**changes):
    kwargs = dict(
        channel="telegram", channel_user_id=USER, registration_id=7, starts_at=THU["starts_at"], lead=make_lead(), now=NOW,
    )
    kwargs.update(changes)
    return await partner_notice.notify_partner_registered(FakeConn(), "whieda", **kwargs)


async def test_the_partner_is_told_who_registered_with_the_card_button(notice):
    assert await notify() is True
    kwargs = notice.outbox.await_args.kwargs
    assert kwargs["event_type"] == "meeting_guest_registered" and kwargs["idempotency_key"] == "mnew:7"
    payload = kwargs["payload"]
    assert payload["text"] == (
        "Анна Петрова записался(ась) на Живую встречу: чт 05.11 19:30 МСК. Делать ничего не нужно: напоминания пришлёт бот. "
        "После встречи придёт список с вашими людьми."
    )
    assert len(payload["text"]) <= 350
    assert payload["chat_id"] == "7001" and payload["binding_id"] == "whieda-advisor-bot"
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "Открыть карточку", "url": "https://igor.wwc.best/crm/?contact=card-1"}]]}
    assert payload["site_login_user_id"] == 7001
    notice.owner.assert_awaited_once()
    assert notice.owner.await_args.args[-1] == "igor-actor"                 # владелец заявки; иначе — пригласивший по ссылке


async def test_without_a_card_or_open_crm_there_is_no_button_and_without_a_name_a_plain_noun(notice):
    notice.card.return_value = None
    notice.name.return_value = ""
    assert await notify(lead=None) is True
    payload = notice.outbox.await_args.kwargs["payload"]
    assert "reply_markup" not in payload and payload["text"].startswith("Ваш человек записался(ась)")


@pytest.mark.parametrize("what", ["no_owner", "organic_owner", "no_chat", "no_bot", "failure"])
async def test_nobody_is_told_when_there_is_no_partner_chat_or_bot_and_a_failure_is_silent(notice, what):
    if what == "no_owner":
        notice.owner.return_value = None
    elif what == "organic_owner":
        notice.owner.return_value = "viktor"
    elif what == "no_chat":
        notice.chat.return_value = None
    elif what == "no_bot":
        notice.binding.return_value = None
    else:
        notice.outbox.side_effect = RuntimeError("boom")
    assert await notify() is False
    if what != "failure":
        notice.outbox.assert_not_awaited()


async def test_a_repeated_registration_does_not_tell_the_partner_twice(notice):
    notice.outbox.return_value = {"created": False}
    assert await notify() is False


class RegistrationDb:
    def __init__(self, monkeypatch, *, status="registered"):
        self.notify = AsyncMock(return_value=True)
        self.outcome = SimpleNamespace(
            status=status, registration=make_registration(7, 12), meeting=THU, old_meeting=None
        )

        @asynccontextmanager
        async def connection(tenant_id):
            yield SimpleNamespace()

        monkeypatch.setattr(registration, "tenant_connection", connection)
        monkeypatch.setattr(registration, "load_lead_by_channel_user", AsyncMock(return_value=make_lead()))
        monkeypatch.setattr(registration.registrations, "register", AsyncMock(return_value=self.outcome))
        monkeypatch.setattr(registration, "card_registration_event", AsyncMock(return_value=True))
        monkeypatch.setattr(registration.partner_notice, "notify_partner_registered", self.notify)


async def test_a_registration_in_the_bot_tells_the_partner_once(monkeypatch):
    db = RegistrationDb(monkeypatch)
    result = await registration.register("whieda", "telegram", USER, 12, now=NOW)
    assert result.status == "registered"
    db.notify.assert_awaited_once()
    kwargs = db.notify.await_args.kwargs
    assert kwargs["registration_id"] == 7 and kwargs["starts_at"] == THU["starts_at"] and kwargs["channel"] == "telegram"


async def test_a_repeated_press_does_not_tell_the_partner(monkeypatch):
    db = RegistrationDb(monkeypatch, status="already")
    await registration.register("whieda", "telegram", USER, 12, now=NOW)
    db.notify.assert_not_awaited()


# ---- имя и статус человека ---------------------------------------------------------------------------------


async def test_the_name_comes_from_the_lead_then_from_the_actor_and_is_cleaned():
    conn = FakeConn()
    assert await people.person_name(conn, "whieda", "telegram", USER, lead_name="Анна @spam") == "Анна …"
    fetch = AsyncMock(side_effect=[{"name": ""}, {"display_name": "Олег"}])
    with patch.object(people, "fetch_one", fetch):
        assert await people.person_name(conn, "whieda", "max", USER, lead_id="lead-1") == "Олег"
    assert fetch.await_count == 2
    with patch.object(people, "fetch_one", AsyncMock(side_effect=RuntimeError("no table"))):
        assert await people.person_name(conn, "whieda", "telegram", USER) == ""
    assert conn.rollbacks == 1


@pytest.mark.parametrize(("stage", "expected"), [("guest", False), ("partner_whieda", True), ("partner_wwc", True)])
async def test_the_zoom_signature_follows_the_stage(stage, expected):
    with patch.object(people, "resolve_stage", AsyncMock(return_value=stage)):
        assert await people.is_partner_person(FakeConn(), "whieda", "telegram", USER) is expected
    with patch.object(people, "resolve_stage", AsyncMock(side_effect=RuntimeError("down"))):
        assert await people.is_partner_person(FakeConn(), "whieda", "telegram", USER) is False


# ---- приветствие: размеры и кнопки ----------------------------------------------------------------------------


def test_every_greeting_variant_fits_the_limit_with_every_extra_line():
    for name in (None, "Ольга Самцова"):
        for tag in (None, "vk", "art-ashwagandha", "site-club"):
            for own in (True, False):
                for meetings in ([], [TUE]):
                    reply = guest_menu_reply(name, registration=None, open_meetings=meetings, now=NOW, own_network=own, start_tag=tag)
                    assert len(reply.text) <= 350, (name, tag, own, meetings, len(reply.text))
                    assert reply.rows[-1] == [("Я уже партнёр WHIEDA", "funnel:partner")]
                    assert ("funnel:consult" in [d for r in reply.rows for _, d in r]) == (own and tag not in ("art-ashwagandha", "site-club"))


def test_the_registered_guest_text_fits_the_limit():
    reply = guest_menu_reply("Ольга Самцова", registration=(make_registration(7, 12), THU), open_meetings=[], now=NOW)
    assert len(reply.text) <= 350 and "Вас пригласил Ольга Самцова." in reply.text
