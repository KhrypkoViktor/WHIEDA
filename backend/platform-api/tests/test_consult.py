"""Срез 7 бот-воронки: «Личный разбор» — два вопроса кнопками → Виктор или Елена → заявка ведущему (V47, 31.10.2026).

Без базы и сети: база подменена фейками на границах модуля app/funnel/consult.py, Telegram и Max — моками. Настоящий SQL,
миграция V47, RLS и сквозной сценарий на живых строках — в test_consult_postgres.py.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.funnel import consult, guest_bot, system_word, texts
from app.funnel.consult import ConsultAction, Requester, parse_consult_callback
from app.funnel.guest import Reply, rows_to_max, rows_to_telegram
from app.leads.meeting_leads import MeetingLead
from app.max import processor as max_processor
from app.max.update_parser import MaxCallbackEvent
from app.settings import get_settings
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramCallbackQuery
from app.tenancy import TenantContext

TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
OTHER_TENANT = TenantContext(tenant_id="nsp", status="active", display_name="NSP", entitlements={})
BOT_TOKEN = "whieda-test-token"
USER = 4242_0011
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def data_of(rows: list[list[tuple[str, str]]]) -> list[str]:
    return [data for row in rows for _, data in row]


def labels_of(rows: list[list[tuple[str, str]]]) -> list[str]:
    return [label for row in rows for label, _ in row]


# ---- разбор кнопок ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "expected"), [
    ("funnel:consult", ConsultAction("ask_who")),
    ("cq1:net", ConsultAction("ask_goal", who="net")),
    ("cq1:look", ConsultAction("ask_goal", who="look")),
    ("cq2:sell:team", ConsultAction("ask_host", who="sell", goal="team")),
    ("chost:biz:system:elena", ConsultAction("request", who="biz", goal="system", host="elena")),
    ("chost:net:money:viktor", ConsultAction("request", who="net", goal="money", host="viktor")),
    ("  cq1:net  ", ConsultAction("ask_goal", who="net")),
])
def test_consult_buttons_are_parsed(data, expected):
    assert parse_consult_callback(data) == expected


@pytest.mark.parametrize("data", [
    "funnel:meet", "funnel:tour", "funnel:ask", "funnel:", "funnel:consult:x", "cq1:", "cq1:bogus", "cq1:net:money", "cq2:net", "cq2:net:",
    "cq2:net:bogus", "cq2:bogus:money", "chost:net:money", "chost:net:money:", "chost:net:money:anna", "chost:net:money:viktor:1",
    "xcq1:net", "mreg:12", "consent:news:yes", "", None,
])
def test_other_buttons_are_not_ours(data):
    assert parse_consult_callback(data) is None


def test_every_button_of_the_dialog_fits_a_telegram_callback():
    seen: list[str] = []
    seen += data_of(consult.who_reply().rows)
    for who, _ in texts.CONSULT_WHO_LABELS:
        seen += data_of(consult.goal_reply(who).rows)
        for goal, _ in texts.CONSULT_GOAL_LABELS:
            seen += data_of(consult.host_reply(who, goal).rows)
    assert len(seen) == 4 + 16 + 32
    assert all(len(item.encode("utf-8")) <= 64 for item in seen)
    assert all(parse_consult_callback(item) is not None for item in seen)   # диалог не выдаёт кнопок, которых сам не разберёт
    assert len(set(seen)) == len(seen)


def test_the_questions_and_their_buttons():
    who = consult.who_reply()
    assert who.text == "Кто вы сейчас?"
    assert labels_of(who.rows) == ["Сетевик", "Продаю продукт", "Своё дело", "Пока смотрю"]
    assert data_of(who.rows) == ["cq1:net", "cq1:sell", "cq1:biz", "cq1:look"]
    goal = consult.goal_reply("sell")
    assert goal.text == "Чего ждёте от разбора?"
    assert labels_of(goal.rows) == ["Доход", "Команда", "Продукт для себя", "Понять систему"]
    assert data_of(goal.rows) == ["cq2:sell:money", "cq2:sell:team", "cq2:sell:product", "cq2:sell:system"]
    host = consult.host_reply("sell", "team")
    assert host.text == "С кем удобнее?"
    assert labels_of(host.rows) == ["Виктор", "Елена"]
    assert data_of(host.rows) == ["chost:sell:team:viktor", "chost:sell:team:elena"]
    assert all(len(row) == 1 for reply in (who, goal, host) for row in reply.rows)   # по кнопке в ряду, как в остальных меню


def test_host_buttons_take_the_names_from_the_setting(monkeypatch):
    monkeypatch.setenv("PLATFORM_CONSULT_HOST_NAMES", '{"viktor":"Виктор Х.","elena":"Лена"}')
    get_settings.cache_clear()
    try:
        assert labels_of(consult.host_reply("net", "money").rows) == ["Виктор Х.", "Лена"]
    finally:
        monkeypatch.delenv("PLATFORM_CONSULT_HOST_NAMES")
        get_settings.cache_clear()


@pytest.mark.parametrize("raw", [None, "{broken", '["list"]', '{"elena": "  ", "viktor": 5}', '{"extra": "x"}'])
def test_the_host_settings_fall_back_to_the_defaults(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("PLATFORM_CONSULT_HOSTS", raising=False)
        monkeypatch.delenv("PLATFORM_CONSULT_HOST_NAMES", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_CONSULT_HOSTS", raw)
        monkeypatch.setenv("PLATFORM_CONSULT_HOST_NAMES", raw)
    get_settings.cache_clear()
    try:
        assert get_settings().parsed_consult_hosts() == {"viktor": "viktor", "elena": "onlineelena"}
        assert get_settings().parsed_consult_host_names() == {"viktor": "Виктор", "elena": "Елена"}
    finally:
        monkeypatch.delenv("PLATFORM_CONSULT_HOSTS", raising=False)
        monkeypatch.delenv("PLATFORM_CONSULT_HOST_NAMES", raising=False)
        get_settings.cache_clear()


def test_a_valid_part_of_the_setting_is_taken_and_the_rest_stays_default(monkeypatch):
    monkeypatch.setenv("PLATFORM_CONSULT_HOSTS", '{"elena": "lena-actor", "extra": "x"}')
    monkeypatch.setenv("PLATFORM_CONSULT_HOST_NAMES", '{"elena": "Лена"}')
    get_settings.cache_clear()
    try:
        assert get_settings().parsed_consult_hosts() == {"viktor": "viktor", "elena": "lena-actor"}
        assert get_settings().parsed_consult_host_names() == {"viktor": "Виктор", "elena": "Лена"}
    finally:
        monkeypatch.delenv("PLATFORM_CONSULT_HOSTS", raising=False)
        monkeypatch.delenv("PLATFORM_CONSULT_HOST_NAMES", raising=False)
        get_settings.cache_clear()


# ---- кто нажал -------------------------------------------------------------------------------------


def tg_callback(data: str, *, user: int = USER, chat_type: str = "private", profile: dict[str, Any] | None = None) -> TelegramCallbackQuery:
    from_user = {"id": user, "first_name": "Анна", "last_name": "Петрова", "username": "anna_petrova"}
    from_user.update(profile if profile is not None else {})
    return TelegramCallbackQuery(
        chat_id=user, user_id=user, callback_query_id="cq-1", data=data, chat_type=chat_type,
        raw={"callback_query": {"from": from_user, "message": {"message_id": 3}}},
    )


def max_callback(payload: str, *, chat_type: str = "dialog", username: str | None = None, user_raw: dict[str, Any] | None = None) -> MaxCallbackEvent:
    return MaxCallbackEvent(
        callback_id="cb-1", payload=payload, user_id=USER, chat_id=555, message_id="mid-1", chat_type=chat_type,
        username=username, display_name=f"@{username}" if username else "Инна Орлова",
        raw={"callback": {"user": {"user_id": USER, **(user_raw or {})}}},
    )


def test_a_telegram_requester_takes_the_profile_name_and_a_valid_nick():
    assert consult.telegram_requester(tg_callback("x")) == Requester("telegram", USER, "Анна Петрова", "anna_petrova")
    only_first = consult.telegram_requester(tg_callback("x", profile={"last_name": "", "username": "@Anna_K1"}))
    assert (only_first.display_name, only_first.username) == ("Анна", "Anna_K1")
    # ник, из которого нельзя построить ссылку (короткий, с точкой, с адресом), не берём; пустой профиль — пустое имя
    for bad in ("ann", "anna.k", "a" * 33, "anna k", "http://x.y/z", ""):
        assert consult.telegram_requester(tg_callback("x", profile={"username": bad})).username is None, bad
    bare = TelegramCallbackQuery(chat_id=1, user_id=1, callback_query_id="c", data="x", chat_type="private", raw={})
    assert consult.telegram_requester(bare) == Requester("telegram", 1, "", None)


def test_a_max_requester_takes_the_name_from_the_user_object():
    event = max_callback("x", user_raw={"first_name": "Инна", "last_name": "Орлова"})
    assert consult.max_requester(event) == Requester("max", USER, "Инна Орлова", None)
    named = max_callback("x", username="inna_o", user_raw={"name": "Инна", "username": "inna_o"})
    assert consult.max_requester(named) == Requester("max", USER, "Инна", "inna_o")
    assert consult.max_requester(max_callback("x", username="bad name")).username is None
    bare = SimpleNamespace(user_id=USER)   # без raw и username вообще
    assert consult.max_requester(bare) == Requester("max", USER, "", None)


# ---- сообщение ведущему и кнопка «Написать» --------------------------------------------------------


def message(**changes: Any) -> str:
    base = dict(name="Анна Петрова", channel="telegram", username="anna_petrova", phone_e164="+79991112233",
                who="net", goal="money", partner_code="igor")
    base.update(changes)
    return consult.host_message_text(**base)


def test_the_host_message_has_every_known_fact():
    assert message().split("\n") == [
        "Личный разбор: Анна Петрова",
        "Мессенджер: Telegram @anna_petrova",
        "Телефон: +79991112233",
        "Кто: Сетевик",
        "Цель: Доход",
        "От партнёра: igor",
    ]


def test_the_host_message_skips_what_is_unknown():
    lines = message(phone_e164=None, partner_code="", username=None, name="", who="look", goal="system").split("\n")
    assert lines == ["Личный разбор: Без имени", "Мессенджер: Telegram, без ника", "Кто: Пока смотрю", "Цель: Понять систему"]


def test_the_host_message_for_a_person_in_max_asks_to_reply_in_max():
    lines = message(channel="max", username=None).split("\n")
    assert "Мессенджер: Max" in lines and lines[-1] == "Ответьте человеку в Max." and "Telegram" not in " ".join(lines)
    # ник Max в текст попадает без @: Telegram сделал бы из него ссылку на чужого человека
    with_nick = message(channel="max", username="inna_o").split("\n")
    assert "Мессенджер: Max (ник inna_o)" in with_nick and "@" not in " ".join(with_nick)


def test_the_name_and_the_partner_code_cannot_carry_links_mentions_or_markup():
    text = message(name="<b>Анна</b> https://evil.example/x @boss t.me/boss www.evil.example", partner_code="@admin http://x.y")
    assert "<" not in text and ">" not in text and "evil" not in text and "boss" not in text and "admin" not in text
    assert "‹b›Анна‹/b›" in text and "От партнёра: … …" in text
    # единственный @ в тексте — проверенный ник человека
    assert [part for part in text.split() if part.startswith("@")] == ["@anna_petrova"]


def test_the_owner_copy_names_the_host_and_tells_when_the_host_got_nothing():
    copy = message(host_name="Елена").split("\n")
    assert copy[:2] == ["Личный разбор: Анна Петрова", "Ведущий: Елена"]
    assert texts.CONSULT_HOST_UNREACHABLE not in copy
    assert message(host_name="Елена", unreachable=True).split("\n")[-1] == texts.CONSULT_HOST_UNREACHABLE


def test_the_write_button():
    assert consult.host_markup("telegram", USER, "anna_petrova") == {"inline_keyboard": [[{"text": "Написать", "url": "https://t.me/anna_petrova"}]]}
    assert consult.host_markup("telegram", USER, None) == {"inline_keyboard": [[{"text": "Написать", "url": f"tg://user?id={USER}"}]]}
    assert consult.host_markup("max", USER, "inna_o") is None and consult.host_markup("max", USER, None) is None


def test_the_card_note_names_the_host_and_the_answers():
    assert consult.card_note(host_name="Елена", who="biz", goal="team") == "Заявка на личный разбор. Ведущий: Елена. Кто: Своё дело. Цель: Команда."


# ---- Telegram: цепочка кнопок ----------------------------------------------------------------------


@pytest.fixture
def tg(whieda_bot_binding):
    state = SimpleNamespace(text=AsyncMock(return_value={"ok": True}), answer=AsyncMock(return_value={"ok": True}))
    with ExitStack() as stack:
        stack.enter_context(patch.object(system_word, "send_telegram_text", state.text))
        stack.enter_context(patch.object(system_word, "answer_callback_query", state.answer))
        stack.enter_context(binding_context_scope(whieda_bot_binding))
        yield state


def sent_buttons(mock: AsyncMock) -> list[tuple[str, str]]:
    markup = mock.await_args.kwargs["reply_markup"]
    return [(b["text"], b["callback_data"]) for row in markup["inline_keyboard"] for b in row] if markup else []


async def test_the_telegram_chain_asks_two_questions_then_the_host(tg):
    first = await system_word.try_handle_funnel_callback(TENANT, tg_callback("funnel:consult"), trace_id="t1")
    assert first == {"ok": True, "route": "funnel_callback", "status": "consult", "trace_id": "t1"}
    assert tg.text.await_args.kwargs["text"] == "Кто вы сейчас?"
    assert tg.text.await_args.kwargs["chat_id"] == str(USER) and tg.text.await_args.kwargs["bot_token"] == BOT_TOKEN
    assert [d for _, d in sent_buttons(tg.text)] == ["cq1:net", "cq1:sell", "cq1:biz", "cq1:look"]

    second = await system_word.try_handle_funnel_callback(TENANT, tg_callback("cq1:sell"), trace_id="t1")
    assert second["status"] == "consult_goal" and tg.text.await_args.kwargs["text"] == "Чего ждёте от разбора?"
    assert [d for _, d in sent_buttons(tg.text)] == ["cq2:sell:money", "cq2:sell:team", "cq2:sell:product", "cq2:sell:system"]

    third = await system_word.try_handle_funnel_callback(TENANT, tg_callback("cq2:sell:product"), trace_id="t1")
    assert third["status"] == "consult_host" and tg.text.await_args.kwargs["text"] == "С кем удобнее?"
    assert sent_buttons(tg.text) == [("Виктор", "chost:sell:product:viktor"), ("Елена", "chost:sell:product:elena")]
    assert tg.answer.await_count == 3   # на каждое нажатие Telegram получил ответ (часики на кнопке гаснут)


async def test_the_telegram_request_answers_with_the_host_name_and_the_messenger(tg):
    fake = AsyncMock(return_value=consult.SubmitResult("created", Reply(texts.CONSULT_ACCEPTED.format(host="Елена", messenger="Telegram")), 41))
    with patch.object(consult, "submit_request", fake):
        result = await system_word.try_handle_funnel_callback(TENANT, tg_callback("chost:net:money:elena"), trace_id="t1")
    assert result == {"ok": True, "route": "funnel_callback", "status": "consult_created", "trace_id": "t1"}
    assert tg.text.await_args.kwargs["text"] == "Заявка принята. Елена напишет вам в Telegram и предложит время. Напоминание за час пришлём."
    assert tg.text.await_args.kwargs["reply_markup"] is None
    fake.assert_awaited_once_with("whieda", Requester("telegram", USER, "Анна Петрова", "anna_petrova"), "net", "money", "elena")


async def test_a_failing_request_apologises_and_is_not_a_crash(tg, caplog):
    with patch.object(consult, "submit_request", AsyncMock(side_effect=RuntimeError("db down: Анна +79991112233"))), caplog.at_level(logging.INFO):
        result = await system_word.try_handle_funnel_callback(TENANT, tg_callback("chost:net:money:viktor"), trace_id="t1")
    assert result["ok"] is False and result["status"] == "consult_error"
    assert tg.text.await_args.kwargs["text"] == texts.GUEST_ERROR
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "Анна" not in logged and "79991112233" not in logged   # в журнал — класс ошибки, не её текст


@pytest.mark.parametrize(("tenant", "data"), [
    (OTHER_TENANT, "funnel:consult"), (OTHER_TENANT, "chost:net:money:viktor"), (TENANT, "cq1:bogus"), (TENANT, "chost:net:money:anna"),
    (TENANT, "funnel:meet"), (TENANT, "consent:news:yes"),
])
async def test_not_our_buttons_and_other_tenants_are_left_to_the_rest_of_the_chain(tg, tenant, data):
    assert await system_word.try_handle_funnel_callback(tenant, tg_callback(data), trace_id="t1") is None
    tg.answer.assert_not_awaited()
    tg.text.assert_not_awaited()


async def test_a_consult_button_in_a_group_is_acknowledged_but_sends_nothing(tg):
    fake = AsyncMock()
    with patch.object(consult, "submit_request", fake):
        result = await system_word.try_handle_funnel_callback(TENANT, tg_callback("chost:net:money:viktor", chat_type="supergroup"), trace_id="t1")
    assert result["status"] == "private_chat_required"
    tg.answer.assert_awaited_once()
    tg.text.assert_not_awaited()
    fake.assert_not_awaited()


async def test_the_dialog_goes_through_the_processor_chain(tg, whieda_tenant, whieda_bot_binding):
    for data, expected in (("cq1:net", "consult_goal"), ("cq2:net:team", "consult_host")):
        update = {"update_id": 5, "callback_query": {"id": "cq-9", "data": data, "from": {"id": USER},
                                                      "message": {"message_id": 3, "chat": {"id": USER, "type": "private"}}}}
        with patch("app.telegram.processor.try_handle_marketing_consent_callback", AsyncMock(return_value=None)):
            result = await process_core_telegram_update(whieda_tenant, update, "t1", binding=whieda_bot_binding)
        assert result["route"] == "funnel_callback" and result["status"] == expected


# ---- Max: цепочка кнопок ---------------------------------------------------------------------------


@pytest.fixture
def mx(monkeypatch):
    state = SimpleNamespace(text=AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(system_word, "send_max_text", state.text)
    monkeypatch.setattr(max_processor, "answer_max_callback", AsyncMock(return_value={"ok": True}))
    return state


def max_buttons_of(mock: AsyncMock) -> list[tuple[str, str]]:
    return [(b["text"], b["payload"]) for row in mock.await_args.kwargs["buttons"] for b in row]


@pytest.mark.parametrize("prefix", list(consult.CONSULT_MAX_PREFIXES))
def test_every_consult_prefix_resolves_to_the_funnel_handler(prefix):
    found = max_processor.resolve_max_callback(prefix + "net")
    assert found is not None and found[0] == prefix and found[1] is system_word.max_funnel_callback
    assert set(consult.CONSULT_MAX_PREFIXES) == {"cq1:", "cq2:", "chost:"}


async def test_the_max_chain_asks_the_same_questions_with_the_same_buttons(mx):
    for payload, status in (("funnel:consult", "handled"), ("cq1:biz", "handled"), ("cq2:biz:system", "handled")):
        result = await max_processor.process_max_event(TENANT, max_callback(payload), "t1")
        assert result["status"] == status and result["answered"] is True
    texts_sent = [call.kwargs["text"] for call in mx.text.await_args_list]
    assert texts_sent == ["Кто вы сейчас?", "Чего ждёте от разбора?", "С кем удобнее?"]
    assert max_buttons_of(mx.text) == [("Виктор", "chost:biz:system:viktor"), ("Елена", "chost:biz:system:elena")]
    assert mx.text.await_args_list[0].kwargs["buttons"] == rows_to_max(consult.who_reply().rows)
    assert mx.text.await_args_list[1].kwargs["buttons"] == rows_to_max(consult.goal_reply("biz").rows)
    assert all(call.kwargs["chat_id"] == 555 and call.kwargs["user_id"] == USER for call in mx.text.await_args_list)


async def test_the_max_request_answers_with_the_messenger_max(mx):
    fake = AsyncMock(return_value=consult.SubmitResult("created", Reply(texts.CONSULT_ACCEPTED.format(host="Виктор", messenger="Max")), 41))
    with patch.object(consult, "submit_request", fake):
        await max_processor.process_max_event(TENANT, max_callback("chost:look:team:viktor", user_raw={"first_name": "Инна"}), "t1")
    assert mx.text.await_args.kwargs["text"] == "Заявка принята. Виктор напишет вам в Max и предложит время. Напоминание за час пришлём."
    assert "buttons" in mx.text.await_args.kwargs and mx.text.await_args.kwargs["buttons"] is None
    fake.assert_awaited_once_with("whieda", Requester("max", USER, "Инна", None), "look", "team", "viktor")


async def test_max_consult_buttons_answer_only_in_the_wwc_bot_and_a_private_chat(mx):
    assert await system_word.max_funnel_callback(OTHER_TENANT, max_callback("cq1:net"), "t1") is None
    for chat_type in ("chat", "channel"):
        assert await system_word.max_funnel_callback(TENANT, max_callback("chost:net:money:viktor", chat_type=chat_type), "t1") is None
    assert await system_word.max_funnel_callback(TENANT, max_callback("cq1:bogus"), "t1") is None
    mx.text.assert_not_awaited()


# ---- заявка: база подменена -------------------------------------------------------------------------


class FakeConn:
    """Подключение: точки отката и курсор — заглушки, запросы идут через подменённый ``consult.fetch_one``."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        yield self

    @asynccontextmanager
    async def cursor(self):
        conn = self

        class Cursor:
            async def execute(self, sql: str, params: tuple = ()) -> None:
                conn.executed.append((" ".join(sql.split()), params))

        yield Cursor()


def make_lead(**changes: Any) -> MeetingLead:
    base = dict(
        lead_id="00000000-0000-0000-0000-00000000aaaa", public_id="L-20261030-AB12CD3", name="Анна Петрова",
        phone_e164="+79991112233", messenger_pref="telegram", meeting_id=12, assigned_owner_id="igor-actor",
        first_ref_code="igor", active_ref_code="igor", utm={}, source_tag=None, telegram_user_id=USER, max_user_id=None,
        bot_opened_at=None, crm_card="card-1",
    )
    base.update(changes)
    return MeetingLead(**base)


@pytest.fixture
def world(monkeypatch):
    """Подменённый мир заявки: что есть в базе (открытая заявка, заявка человека, чаты ведущих) и что с ним сделали."""
    conn = FakeConn()
    w = SimpleNamespace(
        conn=conn, existing=None, lead=None, binding="whieda-advisor-bot", attribution=None, next_id=41,
        chats={"viktor": ("1001", 1001), "onlineelena": ("2002", 2002)},
        inserts=[], outbox=[], card_calls=[], queries=[],
        outbox_created=True, outbox_error=None, card_error=None,
    )

    @asynccontextmanager
    async def fake_tenant_connection(tenant_id: str):
        w.tenant = tenant_id
        yield conn

    async def fake_fetch_one(_conn, sql: str, params: tuple = ()):
        text = " ".join(sql.split())
        w.queries.append((text, params))
        if "pg_advisory_xact_lock" in text:
            return {"locked": ""}
        if text.startswith("select request_id from consult_requests"):
            return {"request_id": w.existing} if w.existing else None
        if text.startswith("insert into consult_requests"):
            w.inserts.append(params)
            w.next_id += 1
            return {"request_id": w.next_id}
        if "partner_referral_attributions" in text:
            return {"ref_code": w.attribution} if w.attribution else None
        raise AssertionError(f"unexpected query: {text[:80]}")

    async def fake_owner_chat(_conn, _tenant, actor_id):
        return w.chats.get(actor_id)

    async def fake_enqueue(_conn, **kwargs):
        if w.outbox_error:
            raise w.outbox_error
        w.outbox.append(kwargs)
        return {"outbox_id": len(w.outbox), "created": w.outbox_created}

    async def fake_step(_conn, **kwargs):
        w.card_calls.append(("step", kwargs))
        if w.card_error:
            raise w.card_error
        return None

    async def fake_event(_conn, **kwargs):
        w.card_calls.append(("event", kwargs))
        return True

    monkeypatch.setattr(consult, "tenant_connection", fake_tenant_connection)
    monkeypatch.setattr(consult, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(consult, "load_lead_by_channel_user", AsyncMock(side_effect=lambda *_a, **_k: w.lead))
    monkeypatch.setattr(consult, "notify_binding_id", AsyncMock(side_effect=lambda *_a, **_k: w.binding))
    monkeypatch.setattr(consult, "owner_chat", fake_owner_chat)
    monkeypatch.setattr(consult, "enqueue_outbox_event", fake_enqueue)
    monkeypatch.setattr(consult, "move_card_step_today", fake_step)
    monkeypatch.setattr(consult, "record_card_event", fake_event)
    return w


TG_PERSON = Requester("telegram", USER, "Анна Петрова", "anna_petrova")
MAX_PERSON = Requester("max", USER, "Инна Орлова", None)


async def test_a_request_to_viktor_is_stored_and_sent_to_him_only(world):
    world.lead = make_lead()
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert result.status == "created" and result.request_id == 42 and result.notified is True
    assert result.reply.text == "Заявка принята. Виктор напишет вам в Telegram и предложит время. Напоминание за час пришлём."
    # строка заявки: арендатор, заявка человека, канал, id, имя из профиля, ведущий, ответы
    (insert,) = world.inserts
    assert insert[:4] == ("whieda", "00000000-0000-0000-0000-00000000aaaa", "telegram", USER)
    assert insert[4:6] == ("Анна Петрова", "viktor") and insert[6] == '{"who": "net", "goal": "money"}'
    # одно сообщение — чат Виктора; копии нет (он сам и есть владелец)
    (queued,) = world.outbox
    assert queued["tenant_id"] == "whieda" and queued["event_type"] == "consult_request_created"
    assert queued["idempotency_key"] == "consult:42" and queued["due_at"] is not None
    payload = queued["payload"]
    assert payload["chat_id"] == "1001" and payload["binding_id"] == "whieda-advisor-bot"
    assert payload["text"].split("\n") == [
        "Личный разбор: Анна Петрова", "Мессенджер: Telegram @anna_petrova", "Телефон: +79991112233",
        "Кто: Сетевик", "Цель: Доход", "От партнёра: igor",
    ]
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "Написать", "url": "https://t.me/anna_petrova"}]]}
    # notified_at проставлен именно по этой заявке
    assert [(sql, params) for sql, params in world.conn.executed if "notified_at" in sql] == [
        ("update consult_requests set notified_at = now(), updated_at = now() where tenant_id = %s and request_id = %s", ("whieda", 42))
    ]


async def test_a_request_to_elena_goes_to_her_and_a_copy_to_the_owner(world):
    result = await consult.submit_request("whieda", TG_PERSON, "biz", "team", "elena", now=NOW)
    assert result.reply.text.startswith("Заявка принята. Елена напишет вам в Telegram")
    assert [(m["idempotency_key"], m["payload"]["chat_id"]) for m in world.outbox] == [("consult:42", "2002"), ("consult:42:owner", "1001")]
    host_text, owner_text = (m["payload"]["text"].split("\n") for m in world.outbox)
    assert "Ведущий: Елена" not in host_text
    assert owner_text[:2] == ["Личный разбор: Анна Петрова", "Ведущий: Елена"]
    assert texts.CONSULT_HOST_UNREACHABLE not in owner_text
    # кнопка «Написать» у обоих
    assert all(m["payload"]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/anna_petrova" for m in world.outbox)
    # без заявки на встречу: ни телефона, ни партнёра, ни заявки в строке
    assert not any(line.startswith(("Телефон", "От партнёра")) for line in host_text)
    assert world.inserts[0][1] is None


async def test_the_person_without_a_nick_gets_the_user_id_link_and_the_one_in_max_gets_no_button(world):
    await consult.submit_request("whieda", Requester("telegram", USER, "Анна", None), "net", "money", "viktor", now=NOW)
    assert world.outbox[0]["payload"]["reply_markup"] == {"inline_keyboard": [[{"text": "Написать", "url": f"tg://user?id={USER}"}]]}
    assert "Telegram, без ника" in world.outbox[0]["payload"]["text"]
    world.outbox.clear()
    result = await consult.submit_request("whieda", MAX_PERSON, "sell", "product", "elena", now=NOW)
    assert result.reply.text.startswith("Заявка принята. Елена напишет вам в Max")
    for queued in world.outbox:
        assert "reply_markup" not in queued["payload"]
        lines = queued["payload"]["text"].split("\n")
        assert "Мессенджер: Max" in lines and lines[-1] == "Ответьте человеку в Max."
    assert world.inserts[-1][2:4] == ("max", USER)


async def test_the_second_request_to_the_same_host_in_a_week_is_refused_without_a_new_row(world):
    world.existing = 40
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert result.status == "exists" and result.request_id == 40
    assert result.reply.text == "Заявка уже есть, ведущий напишет."
    assert world.inserts == [] and world.outbox == [] and world.card_calls == []
    # поиск открытой заявки: тот человек, тот ведущий, статусы new/contacted, окно — 7 дней от «сейчас»
    find = next((sql, params) for sql, params in world.queries if sql.startswith("select request_id"))
    assert find[1] == ("whieda", "telegram", USER, "viktor", ["new", "contacted"], NOW, 7)
    assert "status = any(%s)" in find[0] and "make_interval(days => %s)" in find[0]


async def test_the_request_takes_the_advisory_lock_per_person_and_host_before_anything_else(world):
    await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert world.queries[0] == ("select pg_advisory_xact_lock(hashtext(%s)) as locked", (f"consult:whieda:telegram:{USER}:viktor",))
    assert world.tenant == "whieda"


async def test_no_bot_for_scheduled_messages_means_a_stored_request_and_nothing_queued(world):
    world.binding = None   # стенд: PLATFORM_SCHEDULED_NOTIFY_BINDINGS не задан
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    assert result.status == "created" and result.notified is False
    assert world.outbox == [] and not any("notified_at" in sql for sql, _ in world.conn.executed)


async def test_a_host_without_a_chat_leaves_the_request_unnotified_and_the_owner_is_told(world):
    world.chats.pop("onlineelena")
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    assert result.status == "created" and result.notified is False
    assert [m["idempotency_key"] for m in world.outbox] == ["consult:42:owner"]
    assert world.outbox[0]["payload"]["text"].split("\n")[-1] == texts.CONSULT_HOST_UNREACHABLE
    assert not any("notified_at" in sql for sql, _ in world.conn.executed)


async def test_a_failing_queue_does_not_undo_the_request(world):
    world.outbox_error = RuntimeError("queue down: Анна +79991112233")
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    assert result.status == "created" and result.notified is False and len(world.inserts) == 1


async def test_the_hosts_come_from_the_setting(world, monkeypatch):
    monkeypatch.setenv("PLATFORM_CONSULT_HOSTS", '{"viktor":"owner-actor","elena":"lena-actor"}')
    get_settings.cache_clear()
    world.chats = {"owner-actor": ("7", 7), "lena-actor": ("8", 8), "viktor": ("1001", 1001)}
    try:
        await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    finally:
        monkeypatch.delenv("PLATFORM_CONSULT_HOSTS")
        get_settings.cache_clear()
    assert [m["payload"]["chat_id"] for m in world.outbox] == ["8", "7"]


async def test_one_actor_for_both_hosts_gets_one_message(world, monkeypatch):
    monkeypatch.setenv("PLATFORM_CONSULT_HOSTS", '{"viktor":"viktor","elena":"viktor"}')
    get_settings.cache_clear()
    try:
        await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    finally:
        monkeypatch.delenv("PLATFORM_CONSULT_HOSTS")
        get_settings.cache_clear()
    assert [m["idempotency_key"] for m in world.outbox] == ["consult:42"]


async def test_the_partner_comes_from_the_lead_else_from_the_bot_attribution(world):
    world.lead = make_lead(active_ref_code=None, first_ref_code="petr")
    await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert "От партнёра: petr" in world.outbox[-1]["payload"]["text"]
    world.lead, world.attribution = None, "igor"
    await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert "От партнёра: igor" in world.outbox[-1]["payload"]["text"]
    attribution = next(params for sql, params in world.queries if "partner_referral_attributions" in sql)
    assert attribution == ("whieda", f"telegram:whieda:{USER}")
    world.attribution = None
    await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert "От партнёра" not in world.outbox[-1]["payload"]["text"]


async def test_the_card_gets_the_tag_the_note_and_the_step_when_the_lead_has_one(world):
    world.lead = make_lead()
    await consult.submit_request("whieda", TG_PERSON, "biz", "team", "elena", now=NOW)
    step, event = world.card_calls
    assert step[0] == "step" and step[1] == {
        "tenant_id": "whieda", "contact_id": "card-1", "status": "new", "from_steps": ("invite", None), "to_step": "ping",
    }
    assert event[0] == "event" and event[1]["contact_id"] == "card-1" and event[1]["add_tags"] == ["разбор"]
    assert event[1]["note"] == "Заявка на личный разбор. Ведущий: Елена. Кто: Своё дело. Цель: Команда."


@pytest.mark.parametrize("lead", [None, make_lead(crm_card=None)])
async def test_without_a_lead_or_a_card_only_the_table_changes(world, lead):
    world.lead = lead
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert result.status == "created" and world.card_calls == []


async def test_a_broken_card_does_not_undo_the_request(world):
    world.lead = make_lead()
    world.card_error = RuntimeError("card locked: Анна +79991112233")
    result = await consult.submit_request("whieda", TG_PERSON, "net", "money", "viktor", now=NOW)
    assert result.status == "created" and len(world.inserts) == 1 and len(world.outbox) == 1


async def test_the_stored_name_is_the_profile_name_else_the_lead_name_else_empty(world):
    world.lead = make_lead(name="Анна из заявки")
    await consult.submit_request("whieda", Requester("telegram", USER, "", None), "net", "money", "viktor", now=NOW)
    assert world.inserts[-1][4] == "Анна из заявки"
    world.lead = None
    await consult.submit_request("whieda", Requester("telegram", USER, "  ", None), "net", "money", "viktor", now=NOW)
    assert world.inserts[-1][4] is None
    assert "Личный разбор: Без имени" in world.outbox[-1]["payload"]["text"]
    await consult.submit_request("whieda", Requester("telegram", USER, "  Анна   Петрова  ", None), "net", "money", "viktor", now=NOW)
    assert world.inserts[-1][4] == "Анна Петрова"


@pytest.mark.parametrize(("who", "goal", "host"), [("x", "money", "viktor"), ("net", "x", "viktor"), ("net", "money", "anna"), ("", "", "")])
async def test_unknown_answers_never_reach_the_database(world, who, goal, host):
    with pytest.raises(ValueError):
        await consult.submit_request("whieda", TG_PERSON, who, goal, host, now=NOW)
    assert world.queries == [] and world.inserts == []


async def test_logs_carry_no_names_phones_or_nicks(world, caplog):
    world.lead = make_lead()
    with caplog.at_level(logging.DEBUG):
        await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
        world.existing = 40
        await consult.submit_request("whieda", TG_PERSON, "net", "money", "elena", now=NOW)
    logged = " ".join(f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}" for r in caplog.records)
    assert "funnel_consult" in logged
    for private in ("Анна", "Петрова", "79991112233", "anna_petrova", "igor", str(USER)):
        assert private not in logged, private


async def test_handle_action_asks_without_the_database_and_a_request_needs_a_requester():
    result = await consult.handle_action("whieda", ConsultAction("ask_goal", who="net"))
    assert result.status == "consult_goal" and result.ok is True and result.reply == consult.goal_reply("net")
    with pytest.raises(ValueError):
        await consult.handle_action("whieda", ConsultAction("request", who="net", goal="money", host="viktor"))
