"""Срез 4 бот-воронки: старт из заявки (lead_<id>), режим гостя, запись на встречу, перенос, отмена (V46, 30.10.2026).

Без базы и сети: база подменена фейками на границах модулей, Telegram и Max — моками. Настоящий SQL, миграция,
RLS и сквозные сценарии на живых строках — в test_meeting_registrations_postgres.py.
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.funnel import guest, guest_bot, lead_start, registration, system_word, texts
from app.funnel.guest import GuestContext, Reply, guest_menu_reply, is_guest_menu_word, rows_to_max, rows_to_telegram
from app.funnel.guest_bot import GUEST_MAX_PREFIXES, GuestAction, parse_guest_callback
from app.funnel.lead_start import parse_lead_start_token, start_from_lead
from app.leads import meeting_leads
from app.leads.meeting_leads import MeetingLead, registered_tag, registration_card_change
from app.max import processor as max_processor
from app.max.client import max_button_callback
from app.max.update_parser import MaxCallbackEvent, MaxEvent
from app.meetings import registrations
from app.meetings.registrations import ChangeOutcome, RegisterOutcome, Registration, changeable
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext
from tests.test_bot_media import _through_processor, _update

TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
OTHER_TENANT = TenantContext(tenant_id="nsp", status="active", display_name="NSP", entitlements={})
BOT_TOKEN = "whieda-test-token"
USER = 4242_0007
UTC = timezone.utc
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)  # воскресенье, 12:00 МСК


def meeting(meeting_id: int, starts_at: datetime, *, status: str = "planned", kind: str = "live") -> dict[str, Any]:
    return {"meeting_id": meeting_id, "kind": kind, "title": "Живая встреча", "starts_at": starts_at, "duration_min": 60, "status": status}


TUE = meeting(11, datetime(2026, 11, 3, 16, 30, tzinfo=UTC))  # вт 03.11 19:30 МСК
THU = meeting(12, datetime(2026, 11, 5, 16, 30, tzinfo=UTC))  # чт 05.11 19:30 МСК
NEXT_TUE = meeting(13, datetime(2026, 11, 10, 16, 30, tzinfo=UTC))  # вт 10.11 19:30 МСК


def make_lead(**changes: Any) -> MeetingLead:
    base = dict(
        lead_id="00000000-0000-0000-0000-00000000aaaa", public_id="L-20261030-AB12CD3", name="Анна Петрова",
        phone_e164="+79991112233", messenger_pref="telegram", meeting_id=12, assigned_owner_id="igor-actor",
        first_ref_code="igor", active_ref_code="igor", utm={}, source_tag=None, telegram_user_id=None, max_user_id=None,
        bot_opened_at=None, crm_card="card-1",
    )
    base.update(changes)
    return MeetingLead(**base)


def make_registration(registration_id: int = 7, meeting_id: int = 12, **changes: Any) -> Registration:
    base = dict(registration_id=registration_id, meeting_id=meeting_id, lead_id="00000000-0000-0000-0000-00000000aaaa",
                channel="telegram", channel_user_id=USER, status="registered")
    base.update(changes)
    return Registration(**base)


def callback_data(rows: list[list[tuple[str, str]]]) -> list[str]:
    return [data for row in rows for _, data in row]


def labels(rows: list[list[tuple[str, str]]]) -> list[str]:
    return [label for row in rows for label, _ in row]


MENU_LABELS = ["Показ системы", "Личный разбор", "Задать вопрос"]
MENU_DATA = ["funnel:tour", "funnel:consult", "funnel:ask"]


@asynccontextmanager
async def fake_connection(tenant_id: str):
    yield SimpleNamespace(tenant=tenant_id)


# ---- разбор параметров и кнопок --------------------------------------------------------------------


@pytest.mark.parametrize(("token", "expected"), [
    ("lead_L-20261030-AB12CD3", "L-20261030-AB12CD3"),
    ("LEAD_L-20261030-AB12CD3", "L-20261030-AB12CD3"),   # префикс без учёта регистра, сам id — как есть
    ("  lead_abc  ", "abc"),
    ("lead_", ""), ("lead_ab", ""), ("lead_bad id", ""), ("lead_" + "a" * 60, ""), ("lead_-abc", ""), ("lead_a/b", ""),
    ("ref_abcdefgh", None), ("src_vk", None), ("site_abcdefgh", None), ("shop_abc", None), ("leader", None),
    ("", None), (None, None),
])
def test_the_lead_start_token_is_parsed(token, expected):
    assert parse_lead_start_token(token) == expected


@pytest.mark.parametrize(("data", "expected"), [
    ("mreg:12", GuestAction("register", meeting_id=12)),
    ("mmove:7", GuestAction("move_list", registration_id=7)),
    ("mmoveto:7:13", GuestAction("move_to", registration_id=7, meeting_id=13)),
    ("mcancel:7", GuestAction("cancel", registration_id=7)),
    ("funnel:meet", GuestAction("meet")), ("funnel:tour", GuestAction("tour")), ("funnel:ask", GuestAction("ask")),
    (" mreg:12 ", GuestAction("register", meeting_id=12)),
])
def test_guest_buttons_are_parsed(data, expected):
    assert parse_guest_callback(data) == expected


@pytest.mark.parametrize("data", [
    "funnel:consult", "funnel:", "funnel:other", "mreg:", "mreg:abc", "mreg:-1", "mreg:12:3", "mmoveto:7", "mmoveto:7:", "mmoveto:7:13:1",
    "mmove:7:8", "mcancel:7x", "mreg:" + "1" * 19, "xmreg:1", "consent:news:yes", "", None,
])
def test_other_buttons_are_not_ours(data):
    assert parse_guest_callback(data) is None


def test_every_max_prefix_of_the_guest_resolves_to_the_guest_handler_and_consult_stays_with_the_word():
    for prefix in GUEST_MAX_PREFIXES:
        found = max_processor.resolve_max_callback(prefix + "1")
        assert found is not None and found[1] is guest_bot.max_guest_callback, prefix
    assert max_processor.resolve_max_callback("funnel:consult")[1] is system_word.max_funnel_callback
    assert set(GUEST_MAX_PREFIXES) == {"mreg:", "mmove:", "mmoveto:", "mcancel:", "funnel:meet", "funnel:tour", "funnel:ask"}


@pytest.mark.parametrize("text", ["меню", "Меню", "МЕНЮ!", " меню ", "/menu", "/menu@WHIEDA_Advisor_bot", "меню."])
def test_the_menu_word_is_recognised(text):
    assert is_guest_menu_word(text)


@pytest.mark.parametrize("text", ["", None, "меню бота", "покажи меню", "menu", "/start", "меню меню", "/menus"])
def test_other_text_is_not_the_menu_word(text):
    assert not is_guest_menu_word(text)


# ---- экраны ----------------------------------------------------------------------------------------


def test_the_guest_menu_names_the_partner_and_lists_two_meetings_with_the_three_buttons():
    reply = guest_menu_reply("Ольга Смирнова", registration=None, open_meetings=[TUE, THU], now=NOW)
    assert reply.text == "Вы пришли от Ольга Смирнова.\n\nВыберите ближайшую живую встречу."
    assert labels(reply.rows) == ["Записаться: вт 03.11 19:30", "Записаться: чт 05.11 19:30", *MENU_LABELS]
    assert callback_data(reply.rows) == ["mreg:11", "mreg:12", *MENU_DATA]
    assert all(len(row) == 1 for row in reply.rows)


def test_without_a_partner_the_guest_is_simply_in_wwc():
    for name in (None, ""):
        reply = guest_menu_reply(name, registration=None, open_meetings=[TUE], now=NOW)
        assert reply.text.startswith("Вы в WWC.\n\n")


def test_an_active_registration_replaces_the_meeting_list_with_move_and_cancel():
    reply = guest_menu_reply("Ольга", registration=(make_registration(7, 12), THU), open_meetings=[], now=NOW)
    assert reply.text == (
        "Вы пришли от Ольга.\n\nВы записаны на Живую встречу: чт 05.11 19:30 МСК.\nНапоминание о встрече придёт сюда."
    )
    assert reply.rows[0] == [("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]
    assert callback_data(reply.rows[1:]) == MENU_DATA
    assert "mreg:" not in " ".join(callback_data(reply.rows))


def test_no_meetings_at_all_is_said_plainly_and_the_menu_stays():
    reply = guest_menu_reply(None, registration=None, open_meetings=[], now=NOW)
    assert reply.text == "Вы в WWC.\n\nБлижайших живых встреч пока нет. Загляните сюда чуть позже."
    assert callback_data(reply.rows) == MENU_DATA


def test_a_note_goes_under_the_greeting():
    reply = guest_menu_reply("Ольга", registration=None, open_meetings=[TUE], now=NOW, note=texts.GUEST_MEETING_GONE)
    assert reply.text.split("\n\n")[:2] == ["Вы пришли от Ольга.", "Встреча, на которую вы записывались, уже прошла или отменена."]


def test_register_buttons_mark_the_meeting_the_person_is_already_on():
    rows = guest.register_rows([TUE, THU], frozenset({12}), now=NOW)
    assert labels(rows) == ["Записаться: вт 03.11 19:30", "Вы записаны: чт 05.11 19:30"]
    assert callback_data(rows) == ["mreg:11", "mreg:12"]  # обе кнопки — mreg: повтор отвечает «уже записаны»


def test_the_short_date_adds_the_year_only_when_it_is_not_the_current_one():
    later = datetime(2027, 1, 12, 16, 30, tzinfo=UTC)
    assert guest.short_when(later, now=NOW) == "вт 12.01.2027 19:30"
    assert guest.short_when(TUE["starts_at"], now=NOW) == "вт 03.11 19:30"


def test_rows_become_a_telegram_keyboard_and_max_buttons():
    reply = guest_menu_reply("Ольга", registration=(make_registration(7, 12), THU), open_meetings=[], now=NOW)
    markup = rows_to_telegram(reply.rows)
    assert markup["inline_keyboard"][0] == [
        {"text": "Перенести", "callback_data": "mmove:7"}, {"text": "Отменить запись", "callback_data": "mcancel:7"},
    ]
    assert rows_to_max(reply.rows)[0] == [max_button_callback("Перенести", "mmove:7"), max_button_callback("Отменить запись", "mcancel:7")]
    assert rows_to_telegram([]) is None and rows_to_max([]) is None


def test_every_guest_text_is_marked_for_the_funnel_owner():
    source = open(texts.__file__, encoding="utf-8").read()
    for name in ("GUEST_FROM_PARTNER", "GUEST_IN_WWC", "REGISTRATION_CONFIRMED", "MOVE_DONE", "CANCEL_DONE", "ASK_PROMPT", "GUEST_ERROR"):
        before = source.split(f"\n{name} = ")[0].rsplit("\n", 3)[-3:]
        assert any("# текст: воронка" in line for line in before), name


# ---- заметка и метки карточки ----------------------------------------------------------------------


def test_the_card_tag_is_the_same_as_the_one_the_lead_form_sets():
    assert registered_tag(THU["starts_at"]) == "записан 05.11"
    assert meeting_leads.card_tags(THU["starts_at"]) == ["записан 05.11", "бот не открыл"]


def test_a_registration_adds_the_tag_and_a_note():
    note, add, remove = registration_card_change("registered", starts_at=THU["starts_at"], now=NOW)
    assert (add, remove) == (["записан 05.11"], [])
    assert note == "Записался(ась) на Живую встречу: чт 05.11 19:30 МСК."


def test_a_move_swaps_the_tags_and_names_both_dates():
    note, add, remove = registration_card_change("moved", starts_at=NEXT_TUE["starts_at"], old_starts_at=THU["starts_at"], now=NOW)
    assert (add, remove) == (["записан 10.11"], ["записан 05.11"])
    assert note == "Перенёс(ла) запись на Живую встречу: чт 05.11 19:30 МСК → вт 10.11 19:30 МСК."


def test_a_cancellation_removes_the_tag():
    note, add, remove = registration_card_change("cancelled", starts_at=THU["starts_at"], now=NOW)
    assert (add, remove) == ([], ["записан 05.11"])
    assert note == "Отменил(а) запись на Живую встречу: чт 05.11 19:30 МСК."


def test_a_move_without_the_old_date_and_an_unknown_kind_are_rejected():
    with pytest.raises(ValueError):
        registration_card_change("moved", starts_at=THU["starts_at"])
    with pytest.raises(ValueError):
        registration_card_change("joined", starts_at=THU["starts_at"])  # type: ignore[arg-type]


async def test_a_card_event_without_a_card_does_nothing_and_a_failure_does_not_break_the_registration(monkeypatch):
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(meeting_leads, "record_card_event", record)

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

    assert await meeting_leads.card_registration_event(Conn(), "whieda", None, "registered", starts_at=THU["starts_at"]) is False
    record.assert_not_awaited()
    assert await meeting_leads.card_registration_event(Conn(), "whieda", "card-1", "registered", starts_at=THU["starts_at"], now=NOW) is True
    kwargs = record.await_args.kwargs
    assert (kwargs["contact_id"], kwargs["add_tags"], kwargs["remove_tags"]) == ("card-1", ["записан 05.11"], [])
    record.side_effect = RuntimeError("db down")
    assert await meeting_leads.card_registration_event(Conn(), "whieda", "card-1", "cancelled", starts_at=THU["starts_at"]) is False


# ---- правила записи (без базы) ---------------------------------------------------------------------


def test_a_registration_can_change_until_the_meeting_starts_or_when_it_was_cancelled():
    assert changeable(TUE, NOW) is True
    assert changeable(meeting(1, NOW - timedelta(minutes=1)), NOW) is False         # началась
    assert changeable(meeting(1, NOW, status="planned"), NOW) is False              # ровно сейчас — уже поздно
    assert changeable(meeting(1, NOW + timedelta(days=1), status="done"), NOW) is False
    assert changeable(meeting(1, NOW - timedelta(days=3), status="cancelled"), NOW) is True  # отменена владельцем: можно уйти
    assert changeable(None, NOW) is True


# ---- start_from_lead: логика старта ----------------------------------------------------------------


class Start:
    """Фейки вокруг ``start_from_lead``: база — на границах модулей, каждый вызов виден."""

    def __init__(self, monkeypatch, *, lead: MeetingLead | None = None):
        self.lead = lead
        self.opened_as = lead
        self.card = SimpleNamespace(display_name="Игорь Волков", site_url="https://igor.wwc.best", ref_code="igor")
        self.register_outcome = RegisterOutcome("registered", make_registration(7, 12), THU)
        self.view = (None, [])
        self.load = AsyncMock(side_effect=lambda conn, tenant, public_id: self.lead)
        self.opened = AsyncMock(side_effect=lambda conn, tenant, public_id, **kw: self.opened_as)
        self.owner = AsyncMock(side_effect=lambda tenant, owner: self.card if owner else None)
        self.invite = AsyncMock(return_value="invite-igor-0001")
        self.register = AsyncMock(side_effect=lambda *a, **kw: self.register_outcome)
        self.event = AsyncMock(return_value=True)
        self.guest_view = AsyncMock(side_effect=lambda *a, **kw: self.view)
        self.accept = AsyncMock(return_value=SimpleNamespace(status="attributed"))
        monkeypatch.setattr(lead_start, "tenant_connection", fake_connection)
        monkeypatch.setattr(lead_start, "load_lead_by_public_id", self.load)
        monkeypatch.setattr(lead_start, "mark_bot_opened", self.opened)
        monkeypatch.setattr(lead_start, "owner_card", self.owner)
        monkeypatch.setattr(lead_start, "get_or_create_invite_code", self.invite)
        monkeypatch.setattr(lead_start.registrations, "register", self.register)
        monkeypatch.setattr(lead_start, "card_registration_event", self.event)
        monkeypatch.setattr(lead_start, "load_guest_view", self.guest_view)

    async def run(self, *, channel="telegram", user=USER, public_id="L-20261030-AB12CD3"):
        return await start_from_lead(
            "whieda", channel=channel, channel_user_id=user, public_id=public_id, accept_partner=self.accept, now=NOW
        )


@pytest.fixture
def start(monkeypatch):
    return Start(monkeypatch, lead=make_lead())


async def test_an_unknown_lead_gives_nothing_and_touches_nothing(monkeypatch):
    world = Start(monkeypatch, lead=None)
    assert await world.run() is None
    world.opened.assert_not_awaited()
    world.accept.assert_not_awaited()
    world.register.assert_not_awaited()


async def test_a_lead_with_a_partner_and_a_meeting_attributes_registers_and_greets(start):
    start.opened_as = make_lead(telegram_user_id=USER, bot_opened_at=NOW)
    start.view = ((make_registration(7, 12), THU), [])
    result = await start.run()
    assert result.status == "registered" and result.attribution == "attributed" and result.inviter_name == "Игорь Волков"
    # партнёр закреплён кодом приглашения владельца заявки — тем же путём, что у ref_<код>
    start.invite.assert_awaited_once_with("whieda", "igor-actor")
    start.accept.assert_awaited_once_with("invite-igor-0001")
    start.opened.assert_awaited_once()
    assert start.opened.await_args.kwargs == {"channel": "telegram", "channel_user_id": USER}
    # запись на встречу из заявки, привязанная к заявке; метка и заметка — в карточке
    assert start.register.await_args.kwargs["meeting_id"] == 12
    assert start.register.await_args.kwargs["lead_id"] == "00000000-0000-0000-0000-00000000aaaa"
    start.event.assert_awaited_once()
    assert start.event.await_args.args[2:4] == ("card-1", "registered")
    assert start.event.await_args.kwargs["starts_at"] == THU["starts_at"]
    assert result.reply.text == (
        "Вы пришли от Игорь Волков.\n\nВы записаны на Живую встречу: чт 05.11 19:30 МСК.\nНапоминание о встрече придёт сюда."
    )
    assert result.reply.rows[0] == [("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]
    assert callback_data(result.reply.rows[1:]) == MENU_DATA


async def test_a_repeated_start_is_already_registered_and_the_card_is_not_touched_twice(start):
    start.opened_as = make_lead(telegram_user_id=USER)
    start.register_outcome = RegisterOutcome("already", make_registration(7, 12), THU)
    start.view = ((make_registration(7, 12), THU), [])
    result = await start.run()
    assert result.status == "already"
    start.event.assert_not_awaited()


async def test_the_organic_owner_is_not_a_partner_and_nobody_is_attributed(start):
    start.lead = make_lead(assigned_owner_id="viktor")
    start.opened_as = start.lead
    start.card = SimpleNamespace(display_name="Виктор", site_url="", ref_code="viktor")
    with patch.object(guest, "get_settings", return_value=SimpleNamespace(platform_organic_owner_id="viktor")), \
            patch.object(lead_start, "owner_card", guest.owner_card), patch.object(guest, "inviter_card", AsyncMock()) as card:
        result = await start.run()
    assert result.attribution is None and result.inviter_name is None
    card.assert_not_awaited()                  # профиль organic-владельца даже не читается
    start.accept.assert_not_awaited()
    start.invite.assert_not_awaited()
    assert result.reply.text.startswith("Вы в WWC.")


async def test_an_owner_without_a_partner_profile_means_in_wwc(start):
    start.card = None
    start.owner.side_effect = lambda tenant, owner: None
    result = await start.run()
    assert result.inviter_name is None and result.attribution is None
    start.accept.assert_not_awaited()
    assert result.reply.text.startswith("Вы в WWC.")


async def test_a_lead_without_an_owner_is_in_wwc(start):
    start.lead = make_lead(assigned_owner_id=None)
    start.opened_as = start.lead
    result = await start.run()
    assert result.inviter_name is None
    start.accept.assert_not_awaited()


@pytest.mark.parametrize("status", ["already_registered", "self_referral", "invalid"])
async def test_other_attribution_outcomes_are_reported_and_the_guest_is_still_welcomed(start, status):
    start.accept.return_value = SimpleNamespace(status=status)
    result = await start.run()
    assert result.attribution == status and result.inviter_name == "Игорь Волков"
    assert result.status == "registered"


async def test_a_failed_attribution_does_not_stop_the_registration_and_the_greeting(start):
    start.accept.side_effect = RuntimeError("db down")
    result = await start.run()
    assert result.attribution == "error" and result.status == "registered"
    start.register.assert_awaited_once()
    assert result.reply.text.startswith("Вы пришли от Игорь Волков.")


async def test_a_lead_without_a_meeting_shows_the_two_nearest_meetings(start):
    start.lead = make_lead(meeting_id=None)
    start.opened_as = start.lead
    start.view = (None, [TUE, THU])
    result = await start.run()
    assert result.status == "menu"
    start.register.assert_not_awaited()
    start.event.assert_not_awaited()
    assert callback_data(result.reply.rows) == ["mreg:11", "mreg:12", *MENU_DATA]


@pytest.mark.parametrize("problem", ["closed", "not_found"])
async def test_a_meeting_that_is_gone_is_said_politely_and_other_meetings_are_offered(start, problem):
    start.register_outcome = RegisterOutcome(problem, None, meeting(12, NOW - timedelta(days=1)))
    start.view = (None, [TUE, NEXT_TUE])
    result = await start.run()
    assert result.status == "closed"
    start.event.assert_not_awaited()
    assert "Встреча, на которую вы записывались, уже прошла или отменена." in result.reply.text
    assert callback_data(result.reply.rows) == ["mreg:11", "mreg:13", *MENU_DATA]


async def test_a_forwarded_link_registers_the_new_person_without_the_lead_and_without_touching_the_card(start):
    start.opened_as = make_lead(telegram_user_id=999)   # заявку первым открыл другой человек
    start.view = ((make_registration(8, 12, lead_id=None), THU), [])
    result = await start.run()
    assert result.status == "registered"
    assert start.register.await_args.kwargs["lead_id"] is None
    start.event.assert_not_awaited()


async def test_the_max_channel_compares_the_max_id(start):
    start.opened_as = make_lead(max_user_id=USER)
    await start.run(channel="max")
    assert start.opened.await_args.kwargs == {"channel": "max", "channel_user_id": USER}
    assert start.register.await_args.kwargs["channel"] == "max"
    assert start.register.await_args.kwargs["lead_id"] == "00000000-0000-0000-0000-00000000aaaa"
    start.event.assert_awaited_once()


async def test_a_lead_without_a_card_still_registers_but_has_nothing_to_tag(start):
    start.lead = make_lead(crm_card=None)
    start.opened_as = make_lead(crm_card=None, telegram_user_id=USER)
    await start.run()
    start.event.assert_awaited_once()                     # card_registration_event сам ничего не делает без карточки
    assert start.event.await_args.args[2] is None


# ---- действия записи: логика -----------------------------------------------------------------------


class Reg:
    """Фейки вокруг ``app.funnel.registration``."""

    def __init__(self, monkeypatch):
        self.lead = make_lead(telegram_user_id=USER)
        self.register_outcome = RegisterOutcome("registered", make_registration(7, 12), THU)
        self.open = [TUE, THU, NEXT_TUE]
        self.active: list[tuple[Registration, dict]] = []
        self.registration: Registration | None = make_registration(7, 12)
        self.meeting_row: dict | None = THU
        self.move_outcome = ChangeOutcome("moved", make_registration(8, 13, moved_from=7), make_registration(7, 12, status="moved"), NEXT_TUE, THU)
        self.cancel_outcome = ChangeOutcome("cancelled", make_registration(7, 12, status="cancelled"), make_registration(7, 12, status="cancelled"), THU, THU)
        self.card_id: str | None = "card-1"
        self.find_lead = AsyncMock(side_effect=lambda *a, **kw: self.lead)
        self.register_mock = AsyncMock(side_effect=lambda *a, **kw: self.register_outcome)
        self.open_mock = AsyncMock(side_effect=lambda *a, **kw: [m for m in self.open if m["meeting_id"] != kw.get("exclude_meeting_id")])
        self.active_mock = AsyncMock(side_effect=lambda *a, **kw: self.active)
        self.get_registration = AsyncMock(side_effect=lambda *a, **kw: self.registration)
        self.get_meeting = AsyncMock(side_effect=lambda *a, **kw: self.meeting_row)
        self.move_mock = AsyncMock(side_effect=lambda *a, **kw: self.move_outcome)
        self.cancel_mock = AsyncMock(side_effect=lambda *a, **kw: self.cancel_outcome)
        self.card_lookup = AsyncMock(side_effect=lambda *a, **kw: self.card_id)
        self.event = AsyncMock(return_value=True)
        monkeypatch.setattr(registration, "tenant_connection", fake_connection)
        monkeypatch.setattr(registration, "load_lead_by_channel_user", self.find_lead)
        monkeypatch.setattr(registration, "load_lead_card_id", self.card_lookup)
        monkeypatch.setattr(registration, "card_registration_event", self.event)
        for name, mock in (
            ("register", self.register_mock), ("open_meetings", self.open_mock), ("active_registrations", self.active_mock),
            ("get_registration", self.get_registration), ("get_meeting_row", self.get_meeting), ("move", self.move_mock),
            ("cancel", self.cancel_mock),
        ):
            monkeypatch.setattr(registration.registrations, name, mock)


@pytest.fixture
def reg(monkeypatch):
    return Reg(monkeypatch)


async def test_the_meeting_list_has_three_buttons_and_marks_the_registered_one(reg):
    reg.active = [(make_registration(7, 12), THU)]
    result = await registration.list_meetings("whieda", "telegram", USER, now=NOW)
    assert result.status == "listed"
    assert result.reply.text == "Выберите ближайшую живую встречу."
    assert labels(result.reply.rows) == ["Записаться: вт 03.11 19:30", "Вы записаны: чт 05.11 19:30", "Записаться: вт 10.11 19:30"]
    assert callback_data(result.reply.rows) == ["mreg:11", "mreg:12", "mreg:13"]
    assert reg.open_mock.await_args.kwargs["limit"] == 3


async def test_the_meeting_list_without_meetings_says_so_and_skips_the_second_query(reg):
    reg.open = []
    result = await registration.list_meetings("whieda", "telegram", USER, now=NOW)
    assert result.status == "no_meetings" and result.reply.rows == []
    assert result.reply.text == texts.GUEST_NO_MEETINGS
    reg.active_mock.assert_not_awaited()


async def test_registering_confirms_with_the_date_and_offers_move_and_cancel(reg):
    result = await registration.register("whieda", "telegram", USER, 12, now=NOW)
    assert result.status == "registered" and result.clear_keyboard is False
    assert result.reply.text == "Вы записаны на Живую встречу: чт 05.11 19:30 МСК.\nНапоминание о встрече придёт сюда."
    assert result.reply.rows == [[("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]]
    assert reg.register_mock.await_args.kwargs["lead_id"] == "00000000-0000-0000-0000-00000000aaaa"
    # карточка CRM: метка и заметка
    assert reg.event.await_args.args[2:4] == ("card-1", "registered")


async def test_registering_a_person_without_a_lead_has_no_lead_and_no_card(reg):
    reg.lead = None
    result = await registration.register("whieda", "max", USER, 12, now=NOW)
    assert result.status == "registered"
    assert reg.register_mock.await_args.kwargs["lead_id"] is None
    assert reg.register_mock.await_args.kwargs["channel"] == "max"
    reg.event.assert_not_awaited()


async def test_registering_again_says_already_and_does_not_touch_the_card(reg):
    reg.register_outcome = RegisterOutcome("already", make_registration(7, 12), THU)
    result = await registration.register("whieda", "telegram", USER, 12, now=NOW)
    assert result.status == "already"
    assert result.reply.text == "Вы уже записаны на эту встречу: чт 05.11 19:30 МСК.\nНапоминание о встрече придёт сюда."
    assert result.reply.rows[0][0] == ("Перенести", "mmove:7")
    reg.event.assert_not_awaited()


async def test_an_already_joined_person_gets_no_move_buttons(reg):
    reg.register_outcome = RegisterOutcome("already", make_registration(7, 12, status="joined"), THU)
    result = await registration.register("whieda", "telegram", USER, 12, now=NOW)
    assert result.reply.rows == []


@pytest.mark.parametrize("problem", ["closed", "not_found"])
async def test_a_gone_meeting_is_a_polite_refusal_with_other_meetings(reg, problem):
    reg.register_outcome = RegisterOutcome(problem, None, None)
    result = await registration.register("whieda", "telegram", USER, 99, now=NOW)
    assert result.status == "closed"
    assert result.reply.text == "Эта встреча уже прошла или отменена.\nВыберите другую:"
    assert callback_data(result.reply.rows) == ["mreg:11", "mreg:12", "mreg:13"]
    reg.event.assert_not_awaited()


async def test_a_gone_meeting_with_no_others_says_so(reg):
    reg.register_outcome = RegisterOutcome("closed", None, None)
    reg.open = []
    result = await registration.register("whieda", "telegram", USER, 99, now=NOW)
    assert result.reply.text == "Эта встреча уже прошла или отменена.\nБлижайших живых встреч пока нет. Загляните сюда чуть позже."
    assert result.reply.rows == []


async def test_the_move_list_offers_the_other_meetings_and_not_the_current_one(reg):
    result = await registration.move_list("whieda", "telegram", USER, 7, now=NOW)
    assert result.status == "listed" and result.reply.text == "На какую встречу перенести запись?"
    assert result.reply.rows == [[("вт 03.11 19:30", "mmoveto:7:11")], [("вт 10.11 19:30", "mmoveto:7:13")]]
    assert reg.open_mock.await_args.kwargs["exclude_meeting_id"] == 12


async def test_the_move_list_with_no_other_meetings_keeps_the_registration(reg):
    reg.open = [THU]
    result = await registration.move_list("whieda", "telegram", USER, 7, now=NOW)
    assert result.status == "no_others"
    assert result.reply.text == "Других ближайших встреч пока нет. Ваша запись остаётся: чт 05.11 19:30 МСК."
    assert result.reply.rows == [[("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]]


@pytest.mark.parametrize("record", [None, make_registration(7, 12, status="moved"), make_registration(7, 12, status="cancelled")])
async def test_the_move_list_of_a_stale_or_foreign_registration_is_a_polite_text(reg, record):
    reg.registration = record
    result = await registration.move_list("whieda", "telegram", USER, 7, now=NOW)
    assert result.status == "stale" and result.reply.text == texts.REGISTRATION_STALE and result.reply.rows == []


async def test_the_move_list_after_the_start_is_too_late(reg):
    reg.meeting_row = meeting(12, NOW - timedelta(minutes=5))
    result = await registration.move_list("whieda", "telegram", USER, 7, now=NOW)
    assert result.status == "too_late" and result.reply.text == texts.REGISTRATION_TOO_LATE


async def test_a_move_makes_the_new_registration_swaps_the_card_tags_and_clears_the_old_keyboard(reg):
    result = await registration.move_to("whieda", "telegram", USER, 7, 13, now=NOW)
    assert result.status == "moved" and result.clear_keyboard is True
    assert result.reply.text == "Запись перенесена: вт 10.11 19:30 МСК.\nНапоминание о встрече придёт сюда."
    assert result.reply.rows == [[("Перенести", "mmove:8"), ("Отменить запись", "mcancel:8")]]   # кнопки новой записи
    args = reg.event.await_args
    assert args.args[2:4] == ("card-1", "moved")
    assert (args.kwargs["starts_at"], args.kwargs["old_starts_at"]) == (NEXT_TUE["starts_at"], THU["starts_at"])
    assert reg.move_mock.await_args.kwargs["registration_id"] == 7 and reg.move_mock.await_args.kwargs["to_meeting_id"] == 13


async def test_a_move_of_a_registration_without_a_card_skips_the_card(reg):
    reg.card_id = None
    result = await registration.move_to("whieda", "telegram", USER, 7, 13, now=NOW)
    assert result.status == "moved"
    reg.event.assert_not_awaited()


@pytest.mark.parametrize("status", ["not_found", "not_active"])
async def test_a_move_by_an_old_button_is_a_polite_text_and_the_keyboard_goes(reg, status):
    reg.move_outcome = ChangeOutcome(status)
    result = await registration.move_to("whieda", "telegram", USER, 7, 13, now=NOW)
    assert result.status == "stale" and result.clear_keyboard is True
    reg.event.assert_not_awaited()


async def test_a_move_after_the_start_is_too_late(reg):
    reg.move_outcome = ChangeOutcome("too_late")
    result = await registration.move_to("whieda", "telegram", USER, 7, 13, now=NOW)
    assert (result.status, result.reply.text) == ("too_late", texts.REGISTRATION_TOO_LATE)


@pytest.mark.parametrize("status", ["same", "already"])
async def test_a_move_to_a_meeting_the_person_is_on_leaves_the_old_registration(reg, status):
    reg.move_outcome = ChangeOutcome(status, old_registration=make_registration(7, 12))
    result = await registration.move_to("whieda", "telegram", USER, 7, 13, now=NOW)
    assert result.status == status and result.reply.text == texts.MOVE_ALREADY
    reg.event.assert_not_awaited()


async def test_a_move_to_a_gone_meeting_refuses_and_offers_the_others(reg):
    reg.move_outcome = ChangeOutcome("closed", old_registration=make_registration(7, 12), old_meeting=THU)
    result = await registration.move_to("whieda", "telegram", USER, 7, 99, now=NOW)
    assert result.status == "closed"
    assert result.reply.text == "Эта встреча уже прошла или отменена.\nНа какую встречу перенести запись?"
    assert callback_data(result.reply.rows) == ["mmoveto:7:11", "mmoveto:7:13"]
    reg.event.assert_not_awaited()


async def test_a_cancellation_removes_the_tag_and_clears_the_keyboard(reg):
    result = await registration.cancel("whieda", "telegram", USER, 7, now=NOW)
    assert result.status == "cancelled" and result.clear_keyboard is True
    assert result.reply.text == "Запись на чт 05.11 19:30 МСК отменена. Захотите вернуться — напишите «меню»."
    assert result.reply.rows == []
    assert reg.event.await_args.args[2:4] == ("card-1", "cancelled")
    assert reg.event.await_args.kwargs["starts_at"] == THU["starts_at"]


@pytest.mark.parametrize(("status", "text"), [
    ("not_found", texts.REGISTRATION_STALE), ("not_active", texts.REGISTRATION_STALE), ("too_late", texts.REGISTRATION_TOO_LATE),
])
async def test_a_cancel_that_cannot_be_done_is_a_polite_text(reg, status, text):
    reg.cancel_outcome = ChangeOutcome(status)
    result = await registration.cancel("whieda", "telegram", USER, 7, now=NOW)
    assert result.reply.text == text
    reg.event.assert_not_awaited()


async def test_the_registration_log_has_ids_but_no_names_or_phones(reg, caplog):
    with caplog.at_level(logging.INFO, logger="whieda.funnel.registration"):
        await registration.register("whieda", "telegram", USER, 12, now=NOW)
        await registration.cancel("whieda", "telegram", USER, 7, now=NOW)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "funnel_registration" in text and "user_ref" in text
    for private in ("Анна", "Петрова", "+7999", "79991112233", str(USER)):
        assert private not in text


# ---- Telegram: плюмбинг ----------------------------------------------------------------------------


def tg_msg(text: str, chat_type: str = "private", user: int = USER) -> TelegramMessage:
    return TelegramMessage(chat_id=user, user_id=user, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={})


def tg_cb(data: str, chat_type: str = "private", message_id: int | None = 55) -> TelegramCallbackQuery:
    raw = {"callback_query": {"message": {"message_id": message_id}}} if message_id else {}
    return TelegramCallbackQuery(chat_id=USER, user_id=USER, callback_query_id="cq-1", data=data, chat_type=chat_type, raw=raw)


@pytest.fixture
def tg(whieda_bot_binding):
    """Бот WWC в Telegram: отправка, ответ на нажатие, прямой вызов API и тур подделаны."""
    state = SimpleNamespace(
        text=AsyncMock(return_value={"ok": True}),
        answer=AsyncMock(return_value={"ok": True}),
        call=AsyncMock(return_value={"ok": True}),
        tour=AsyncMock(return_value={"ok": True, "route": "funnel_system", "status": "tour"}),
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(guest_bot, "send_telegram_text", state.text))
        stack.enter_context(patch.object(guest_bot, "answer_callback_query", state.answer))
        stack.enter_context(patch.object(guest_bot, "_call_telegram", state.call))
        stack.enter_context(patch.object(guest_bot, "send_tour_telegram", state.tour))
        stack.enter_context(binding_context_scope(whieda_bot_binding))
        yield state


def lead_result(status="registered", **changes) -> lead_start.LeadStartResult:
    reply = Reply("Вы пришли от Игорь.", [[("Перенести", "mmove:7")], [("Показ системы", "funnel:tour")]])
    return lead_start.LeadStartResult(reply=reply, status=status, **{"attribution": "attributed", "inviter_name": "Игорь", **changes})


async def test_the_telegram_lead_start_sends_the_greeting_with_the_keyboard(tg):
    with patch.object(guest_bot, "start_from_lead", AsyncMock(return_value=lead_result())) as start:
        result = await guest_bot.handle_lead_start_telegram(TENANT, tg_msg("/start lead_L-1-ABC"), "L-1-ABC", trace_id="t1")
    assert result == {"ok": True, "route": "lead_start", "status": "registered", "attribution": "attributed", "trace_id": "t1"}
    assert start.await_args.kwargs["channel"] == "telegram" and start.await_args.kwargs["public_id"] == "L-1-ABC"
    tg.text.assert_awaited_once_with(
        chat_id=str(USER), text="Вы пришли от Игорь.", bot_token=BOT_TOKEN,
        reply_markup={"inline_keyboard": [
            [{"text": "Перенести", "callback_data": "mmove:7"}], [{"text": "Показ системы", "callback_data": "funnel:tour"}],
        ]},
    )


async def test_the_telegram_partner_is_accepted_through_the_same_first_touch_function_as_ref(tg):
    captured = {}

    async def fake_start(tenant_id, **kwargs):
        captured["outcome"] = await kwargs["accept_partner"]("invite-code-1")
        return lead_result()

    msg = dataclasses.replace(tg_msg("/start lead_L-1-ABC"), raw={"message": {"from": {"id": USER}}})
    with patch.object(guest_bot, "start_from_lead", fake_start), \
            patch.object(guest_bot, "accept_referral_start", AsyncMock(return_value=SimpleNamespace(status="attributed"))) as accept:
        await guest_bot.handle_lead_start_telegram(TENANT, msg, "L-1-ABC", trace_id="t1")
    accept.assert_awaited_once_with(
        "whieda", telegram_user_id=USER, telegram_chat_id=USER, invite_code="invite-code-1", raw_update=msg.raw
    )
    assert captured["outcome"].status == "attributed"


async def test_the_telegram_lead_start_steps_aside_when_there_is_no_lead_or_it_fails(tg):
    for outcome in (None, RuntimeError("db down")):
        mock = AsyncMock(return_value=outcome) if outcome is None else AsyncMock(side_effect=outcome)
        with patch.object(guest_bot, "start_from_lead", mock):
            assert await guest_bot.handle_lead_start_telegram(TENANT, tg_msg("/start lead_X"), "L-1-ABC", trace_id="t1") is None
    tg.text.assert_not_awaited()


@pytest.mark.parametrize(("tenant", "chat_type", "public_id"), [(OTHER_TENANT, "private", "L-1-ABC"), (TENANT, "private", "")])
async def test_the_telegram_lead_start_ignores_other_tenants_and_a_broken_id(tg, tenant, chat_type, public_id):
    with patch.object(guest_bot, "start_from_lead", AsyncMock()) as start:
        assert await guest_bot.handle_lead_start_telegram(tenant, tg_msg("/start x", chat_type), public_id, trace_id="t1") is None
    start.assert_not_awaited()


async def test_the_telegram_lead_start_in_a_group_asks_for_the_private_chat(tg):
    with patch.object(guest_bot, "start_from_lead", AsyncMock()) as start:
        result = await guest_bot.handle_lead_start_telegram(TENANT, tg_msg("/start lead_X", "group"), "L-1-ABC", trace_id="t1")
    assert result["status"] == "private_chat_required"
    start.assert_not_awaited()
    tg.text.assert_not_awaited()


async def test_the_lead_start_log_has_no_name_phone_or_the_full_id(tg, caplog):
    with caplog.at_level(logging.INFO, logger="whieda.funnel.guest"), \
            patch.object(guest_bot, "start_from_lead", AsyncMock(return_value=lead_result())):
        await guest_bot.handle_lead_start_telegram(TENANT, tg_msg("/start lead_L-1-ABC"), "L-1-ABC", trace_id="t1")
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "lead_start" in text and "user_ref" in text
    for private in ("Игорь", "Анна", "+7999", str(USER), "L-1-ABC"):
        assert private not in text


def guest_world(monkeypatch, *, guest_context: GuestContext | None, view=(None, []), card=None, fail: Exception | None = None):
    async def resolve(conn, tenant_id, channel, user_id):
        if fail:
            raise fail
        return guest_context

    monkeypatch.setattr(guest_bot, "tenant_connection", fake_connection)
    monkeypatch.setattr(guest_bot, "resolve_guest", resolve)
    monkeypatch.setattr(guest_bot, "load_guest_view", AsyncMock(return_value=view))
    monkeypatch.setattr(guest_bot, "owner_card", AsyncMock(return_value=card))


GUEST = GuestContext(channel="telegram", channel_user_id=USER, lead=make_lead(telegram_user_id=USER))


@pytest.mark.parametrize("text", ["/start", "/start@WHIEDA_Advisor_bot", "с чего начать", "меню", "Меню!", "/menu", "привет", "Добрый день"])
async def test_a_guest_gets_the_guest_menu_instead_of_the_cabinet(tg, monkeypatch, text):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, [TUE, THU]), card=SimpleNamespace(display_name="Игорь Волков"))
    result = await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg(text), trace_id="t1")
    assert result == {"ok": True, "route": "guest_menu", "status": "shown", "trace_id": "t1"}
    sent = tg.text.await_args.kwargs
    assert sent["text"].startswith("Вы пришли от Игорь Волков.\n\nВыберите ближайшую живую встречу.")
    assert callback_data([[(b["text"], b["callback_data"])] for row in sent["reply_markup"]["inline_keyboard"] for b in row]) == [
        "mreg:11", "mreg:12", *MENU_DATA,
    ]


@pytest.mark.parametrize("text", ["товары", "как дела", "сколько стоит ref", "/cabinet", "/start ref_abcdefgh", "система", "меню бота"])
async def test_other_words_do_not_open_the_guest_menu(tg, monkeypatch, text):
    guest_world(monkeypatch, guest_context=GUEST)
    assert await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg(text), trace_id="t1") is None
    tg.text.assert_not_awaited()


async def test_a_partner_and_everybody_else_still_get_what_they_got_before(tg, monkeypatch):
    guest_world(monkeypatch, guest_context=None)   # resolve_guest: нет заявки/записи или это партнёр
    for text in ("/start", "меню", "привет"):
        assert await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg(text), trace_id="t1") is None
    tg.text.assert_not_awaited()


@pytest.mark.parametrize(("tenant", "chat_type"), [(OTHER_TENANT, "private"), (TENANT, "group"), (TENANT, "supergroup")])
async def test_the_guest_menu_is_only_for_the_private_chat_of_the_wwc_bot(tg, monkeypatch, tenant, chat_type):
    guest_world(monkeypatch, guest_context=GUEST)
    resolve = AsyncMock(return_value=GUEST)
    monkeypatch.setattr(guest_bot, "resolve_guest", resolve)
    assert await guest_bot.try_handle_guest_menu_text(tenant, tg_msg("/start", chat_type), trace_id="t1") is None
    resolve.assert_not_awaited()


async def test_a_database_failure_leaves_the_old_behaviour_in_place(tg, monkeypatch, caplog):
    guest_world(monkeypatch, guest_context=GUEST, fail=RuntimeError("password=secret Анна +79991112233"))
    with caplog.at_level(logging.WARNING, logger="whieda.funnel.guest"):
        assert await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg("/start"), trace_id="t1") is None
    tg.text.assert_not_awaited()
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "guest_menu_failed" in text and "secret" not in text and "Анна" not in text and "79991112233" not in text


def run_action(monkeypatch, result: registration.ActionResult | Exception, name: str):
    mock = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    monkeypatch.setattr(registration, name, mock)
    return mock


async def test_the_register_button_acknowledges_and_sends_the_confirmation(tg, monkeypatch):
    reply = Reply("Вы записаны.", [[("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]])
    action = run_action(monkeypatch, registration.ActionResult(reply, "registered"), "register")
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mreg:12"), trace_id="t1")
    assert result == {"ok": True, "route": "guest_callback", "trace_id": "t1", "status": "registered"}
    tg.answer.assert_awaited_once_with(callback_query_id="cq-1", bot_token=BOT_TOKEN)
    action.assert_awaited_once_with("whieda", "telegram", USER, 12)
    assert tg.text.await_args.kwargs["text"] == "Вы записаны."
    assert tg.text.await_args.kwargs["reply_markup"]["inline_keyboard"][0][1] == {"text": "Отменить запись", "callback_data": "mcancel:7"}
    tg.call.assert_not_awaited()      # список остаётся как был: повтор нажатия безопасен


@pytest.mark.parametrize(("data", "name", "args"), [
    ("mmove:7", "move_list", ("whieda", "telegram", USER, 7)),
    ("mmoveto:7:13", "move_to", ("whieda", "telegram", USER, 7, 13)),
    ("mcancel:7", "cancel", ("whieda", "telegram", USER, 7)),
    ("funnel:meet", "list_meetings", ("whieda", "telegram", USER)),
])
async def test_each_button_calls_its_action(tg, monkeypatch, data, name, args):
    action = run_action(monkeypatch, registration.ActionResult(Reply("ok"), "done"), name)
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb(data), trace_id="t1")
    assert result["status"] == "done"
    action.assert_awaited_once_with(*args)


async def test_after_a_move_or_a_cancel_the_old_keyboard_is_removed(tg, monkeypatch):
    run_action(monkeypatch, registration.ActionResult(Reply("Перенесено."), "moved", clear_keyboard=True), "move_to")
    await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mmoveto:7:13", message_id=55), trace_id="t1")
    tg.call.assert_awaited_once_with(
        "editMessageReplyMarkup",
        {"chat_id": USER, "message_id": 55, "reply_markup": {"inline_keyboard": []}},
        bot_token=BOT_TOKEN,
    )
    tg.text.assert_awaited_once()


async def test_a_missing_message_id_or_a_failed_edit_does_not_break_the_answer(tg, monkeypatch):
    run_action(monkeypatch, registration.ActionResult(Reply("Отменено."), "cancelled", clear_keyboard=True), "cancel")
    await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mcancel:7", message_id=None), trace_id="t1")
    tg.call.assert_not_awaited()
    tg.call.side_effect = RuntimeError("network")
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mcancel:7"), trace_id="t1")
    assert result["ok"] is True and result["status"] == "cancelled"


async def test_a_failed_action_answers_with_an_apology_and_does_not_raise(tg, monkeypatch):
    run_action(monkeypatch, RuntimeError("db down"), "register")
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mreg:12"), trace_id="t1")
    assert result["ok"] is False and result["status"] == "error"
    assert tg.text.await_args.kwargs["text"] == "Не получилось. Попробуйте ещё раз чуть позже."


async def test_the_tour_button_sends_the_tour_the_same_way_as_the_word(tg):
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("funnel:tour"), trace_id="t1")
    assert result["status"] == "tour" and result["tour"] == "tour"
    tg.tour.assert_awaited_once_with(TENANT, USER, trace_id="t1")


ASK_TEXT = "Напишите вопрос одним сообщением — передам Виктору"


async def test_the_ask_button_opens_the_owner_support_ticket_with_the_funnel_invite(tg):
    support = AsyncMock(return_value={"ok": True, "route": "support", "status": "ticket_opened"})
    callback = tg_cb("funnel:ask")
    with patch.object(guest_bot, "open_site_support", support):
        result = await guest_bot.try_handle_guest_callback(TENANT, callback, trace_id="t1")
    assert result == {"ok": True, "route": "guest_callback", "trace_id": "t1", "status": "ask", "support": "ticket_opened"}
    # то же обращение, что у кнопки «Поддержка»; приглашение человеку — текст воронки, а не стандартный
    support.assert_awaited_once_with(TENANT, callback, trace_id="t1", user_text=ASK_TEXT)
    assert texts.ASK_PROMPT == ASK_TEXT
    tg.text.assert_not_awaited()          # приглашение отправляет сама поддержка, второго сообщения нет
    tg.answer.assert_awaited_once_with(callback_query_id="cq-1", bot_token=BOT_TOKEN)


async def test_the_ask_button_reports_a_support_that_is_not_connected(tg):
    support = AsyncMock(return_value={"ok": False, "route": "support", "status": "no_admin"})
    with patch.object(guest_bot, "open_site_support", support):
        result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("funnel:ask"), trace_id="t1")
    assert result["ok"] is False and result["status"] == "ask" and result["support"] == "no_admin"


async def test_the_ask_button_survives_a_support_failure(tg):
    with patch.object(guest_bot, "open_site_support", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("funnel:ask"), trace_id="t1")
    assert result["ok"] is False and result["status"] == "error"
    assert tg.text.await_args.kwargs["text"] == "Не получилось. Попробуйте ещё раз чуть позже."


class TicketWorld:
    """Фейки вокруг ``open_site_support``: тикет в памяти, отправка — моки."""

    def __init__(self, monkeypatch, *, created: bool = True):
        from app.telegram import support

        self.ticket = {
            "ticket_id": "t-1", "ticket_no": 12, "created": created, "channel_code": "site", "user_telegram_user_id": USER,
            "user_chat_id": USER, "user_display": "Анна (@anna)", "admin_telegram_user_id": 7001, "forum_chat_id": None,
            "forum_thread_id": None, "offer_title": None,
        }
        self.sent = AsyncMock(return_value={"ok": True, "message_id": 1})
        self.to_admin = AsyncMock(return_value={"ok": True, "message_id": 2})
        self.header = AsyncMock(return_value={"ok": True, "message_id": 3})
        self.opened = AsyncMock(side_effect=lambda *a, **kw: self.ticket)
        monkeypatch.setattr(support, "owner_id", lambda: 7001)
        monkeypatch.setattr(support, "open_or_reuse_ticket", self.opened)
        monkeypatch.setattr(support, "_site_card", AsyncMock(return_value=None))
        monkeypatch.setattr(support, "_open_forum_topic", AsyncMock(side_effect=lambda tenant, ticket, **kw: ticket))
        monkeypatch.setattr(support, "_send", self.sent)
        monkeypatch.setattr(support, "_send_to_admin", self.to_admin)
        monkeypatch.setattr(support, "_send_site_header", self.header)
        monkeypatch.setattr(support, "record_relayed_message", AsyncMock(return_value={"duplicate": False}))
        self.support = support


async def test_open_site_support_sends_the_custom_invite_instead_of_the_standard_one(monkeypatch):
    world = TicketWorld(monkeypatch)
    TENANT_SUPPORT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"site_support": True})
    result = await world.support.open_site_support(TENANT_SUPPORT, tg_cb("funnel:ask"), trace_id="t1", user_text=ASK_TEXT)
    assert result["status"] == "ticket_opened" and result["kind"] == "site" and result["ticket"] == "#S-12"
    world.sent.assert_awaited_once_with(USER, ASK_TEXT)
    world.header.assert_awaited_once()                       # владельцу — обычная шапка обращения
    assert "Анна" in world.header.await_args.args[1]


async def test_open_site_support_without_a_custom_invite_is_unchanged(monkeypatch):
    world = TicketWorld(monkeypatch)
    TENANT_SUPPORT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"site_support": True})
    await world.support.open_site_support(TENANT_SUPPORT, tg_cb("support"), trace_id="t1")
    assert world.sent.await_args.args == (USER, f"Обращение #S-12 открыто.\n\n{world.support.SUPPORT_INVITE_TEXT}")


async def test_a_second_ask_reuses_the_ticket_and_still_gets_the_invite(monkeypatch):
    world = TicketWorld(monkeypatch, created=False)
    TENANT_SUPPORT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"site_support": True})
    await world.support.open_site_support(TENANT_SUPPORT, tg_cb("funnel:ask"), trace_id="t1", user_text=ASK_TEXT)
    world.sent.assert_awaited_once_with(USER, ASK_TEXT)
    world.header.assert_not_awaited()                         # шапка не дублируется
    assert "снова нажал" in world.to_admin.await_args.args[1]


async def test_a_guest_text_after_ask_is_relayed_to_the_owner_by_the_support_chain(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    guest_world(monkeypatch, guest_context=GUEST)
    relayed = {"ok": True, "route": "support_relay", "direction": "user_to_admin", "ticket": "#S-12"}
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        mocks["try_relay_user_message"].return_value = relayed
        result = await process_core_telegram_update(
            whieda_tenant, _update({"text": "Как попасть на встречу, если я в другом городе?"}, user_id=USER), "t1", binding=whieda_bot_binding
        )
    assert result == relayed
    mocks["try_relay_user_message"].assert_awaited_once()
    tg.text.assert_not_awaited()


@pytest.mark.parametrize("text", ["Привет, как перенести встречу?", "Добрый день! Я не смогу прийти", "hello, question"])
async def test_a_question_that_starts_with_a_greeting_goes_to_the_owner_while_the_ticket_is_open(tg, monkeypatch, text):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, [TUE]))
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay"})
    monkeypatch.setattr(guest_bot, "try_relay_user_message", relay)
    result = await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg(text), trace_id="t1")
    assert result == {"ok": True, "route": "support_relay"}
    relay.assert_awaited_once()
    tg.text.assert_not_awaited()                              # меню не показано


async def test_a_greeting_without_a_ticket_still_shows_the_guest_menu(tg, monkeypatch):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, [TUE]))
    monkeypatch.setattr(guest_bot, "try_relay_user_message", AsyncMock(return_value=None))
    result = await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg("привет"), trace_id="t1")
    assert result["route"] == "guest_menu"
    tg.text.assert_awaited_once()


@pytest.mark.parametrize("text", ["/start", "меню", "Меню!", "/menu", "с чего начать"])
async def test_the_menu_words_show_the_menu_even_while_the_ticket_is_open(tg, monkeypatch, text):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, [TUE]))
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay"})
    monkeypatch.setattr(guest_bot, "try_relay_user_message", relay)
    result = await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg(text), trace_id="t1")
    assert result["route"] == "guest_menu"
    relay.assert_not_awaited()


async def test_the_relay_is_never_tried_for_somebody_who_is_not_a_guest(tg, monkeypatch):
    guest_world(monkeypatch, guest_context=None)
    relay = AsyncMock()
    monkeypatch.setattr(guest_bot, "try_relay_user_message", relay)
    assert await guest_bot.try_handle_guest_menu_text(TENANT, tg_msg("привет, как дела"), trace_id="t1") is None
    relay.assert_not_awaited()


@pytest.mark.parametrize(("tenant", "data"), [
    (TENANT, "funnel:consult"), (TENANT, "consent:news:yes"), (OTHER_TENANT, "mreg:12"), (OTHER_TENANT, "funnel:meet"),
])
async def test_other_telegram_buttons_are_left_to_the_next_handler(tg, tenant, data):
    assert await guest_bot.try_handle_guest_callback(tenant, tg_cb(data), trace_id="t1") is None
    tg.answer.assert_not_awaited()
    tg.text.assert_not_awaited()


async def test_a_button_in_a_group_is_acknowledged_and_nothing_is_done(tg, monkeypatch):
    action = run_action(monkeypatch, registration.ActionResult(Reply("ok"), "x"), "register")
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mreg:12", "group"), trace_id="t1")
    assert result["status"] == "private_chat_required"
    action.assert_not_awaited()
    tg.text.assert_not_awaited()


# ---- Telegram: через процессор ---------------------------------------------------------------------

_START_EXTRAS = ("first_start_consent_notice", "offer_marketing_consent")


def _processor_stack(stack: ExitStack) -> dict[str, AsyncMock]:
    mocks = _through_processor(stack)
    mocks["consent_notice"] = stack.enter_context(patch("app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)))
    mocks["consent_offer"] = stack.enter_context(patch("app.telegram.processor.offer_marketing_consent", AsyncMock(return_value=None)))
    mocks["dashboard"] = stack.enter_context(
        patch("app.telegram.processor.show_referral_dashboard", AsyncMock(return_value={"ok": True, "route": "referral"}))
    )
    mocks["legacy_keyboard"] = stack.enter_context(patch("app.telegram.processor._remove_legacy_reply_keyboard", AsyncMock()))
    mocks["source_start"] = stack.enter_context(patch("app.telegram.processor.record_source_start", AsyncMock()))
    return mocks


async def test_the_start_with_a_lead_token_goes_to_the_lead_start_before_everything_else(tg, whieda_tenant, whieda_bot_binding):
    lead_ok = {"ok": True, "route": "lead_start", "status": "registered"}
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        handler = stack.enter_context(patch("app.telegram.processor.handle_lead_start_telegram", AsyncMock(return_value=lead_ok)))
        start_token = stack.enter_context(patch("app.telegram.processor.handle_start_token", AsyncMock()))
        result = await process_core_telegram_update(
            whieda_tenant, _update({"text": "/start lead_L-20261030-AB12CD3"}, user_id=USER), "t1", binding=whieda_bot_binding
        )
    assert result == lead_ok
    assert handler.await_args.args[2] == "L-20261030-AB12CD3"
    start_token.assert_not_awaited()
    mocks["dashboard"].assert_not_awaited()
    mocks["consent_notice"].assert_awaited_once()        # согласие на политику — как у любого /start


async def test_a_lead_token_without_a_lead_becomes_the_plain_start_like_any_unknown_parameter(tg, whieda_tenant, whieda_bot_binding):
    unknown = {"ok": True, "route": "start_unknown", "status": "token_not_found"}
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        stack.enter_context(patch("app.telegram.processor.handle_lead_start_telegram", AsyncMock(return_value=None)))
        stack.enter_context(patch("app.telegram.processor.try_handle_guest_menu_text", AsyncMock(return_value=None)))
        start_token = stack.enter_context(patch("app.telegram.processor.handle_start_token", AsyncMock(return_value=unknown)))
        await process_core_telegram_update(
            whieda_tenant, _update({"text": "/start lead_L-NOPE-000"}, user_id=USER), "t1", binding=whieda_bot_binding
        )
    start_token.assert_awaited_once()                    # дальше — прежний путь незнакомого параметра
    mocks["dashboard"].assert_awaited_once()             # и обычный /start: кабинет, как раньше


@pytest.mark.parametrize("text", ["/start ref_abcdefgh", "/start src_vk", "/start shop_abc", "/start site_abcdefgh", "/start"])
async def test_the_other_starts_never_reach_the_lead_start(tg, whieda_tenant, whieda_bot_binding, text):
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        handler = stack.enter_context(patch("app.telegram.processor.handle_lead_start_telegram", AsyncMock()))
        stack.enter_context(patch("app.telegram.processor.try_handle_guest_menu_text", AsyncMock(return_value=None)))
        stack.enter_context(patch("app.telegram.processor.handle_referral_start_token", AsyncMock(return_value={"ok": True})))
        stack.enter_context(patch("app.telegram.processor.handle_site_start_token", AsyncMock(return_value={"ok": True})))
        stack.enter_context(patch("app.telegram.processor.handle_shop_start", AsyncMock(return_value={"ok": True})))
        await process_core_telegram_update(whieda_tenant, _update({"text": text}, user_id=USER), "t1", binding=whieda_bot_binding)
    handler.assert_not_awaited()


async def test_the_bare_start_of_a_guest_opens_the_guest_menu_and_not_the_cabinet(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, [TUE]), card=None)
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        result = await process_core_telegram_update(whieda_tenant, _update({"text": "/start"}, user_id=USER), "t1", binding=whieda_bot_binding)
    assert result["route"] == "guest_menu"
    mocks["dashboard"].assert_not_awaited()
    mocks["legacy_keyboard"].assert_not_awaited()
    assert tg.text.await_args.kwargs["text"].startswith("Вы в WWC.")


async def test_the_bare_start_of_a_partner_or_a_stranger_opens_the_cabinet_as_before(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    guest_world(monkeypatch, guest_context=None)
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        result = await process_core_telegram_update(whieda_tenant, _update({"text": "/start"}, user_id=USER), "t1", binding=whieda_bot_binding)
    assert result == {"ok": True, "route": "referral"}
    mocks["dashboard"].assert_awaited_once()
    tg.text.assert_not_awaited()


async def test_the_word_menu_of_a_guest_opens_the_guest_menu_and_of_everybody_else_goes_on_to_the_advisor(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    guest_world(monkeypatch, guest_context=GUEST, view=(None, []))
    with ExitStack() as stack:
        _processor_stack(stack)
        result = await process_core_telegram_update(whieda_tenant, _update({"text": "меню"}, user_id=USER), "t1", binding=whieda_bot_binding)
    assert result["route"] == "guest_menu"
    guest_world(monkeypatch, guest_context=None)
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        stack.enter_context(patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"ok": True, "route": "advisor"})))
        other = await process_core_telegram_update(whieda_tenant, _update({"text": "меню"}, user_id=USER), "t1", binding=whieda_bot_binding)
    assert other["route"] in {"advisor", "next_handler"} and other["route"] != "guest_menu"
    mocks["dashboard"].assert_not_awaited()


async def test_the_question_text_after_ask_goes_on_to_the_usual_chain_and_not_to_the_guest_menu(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    guest_world(monkeypatch, guest_context=GUEST)
    with ExitStack() as stack:
        mocks = _processor_stack(stack)
        result = await process_core_telegram_update(
            whieda_tenant, _update({"text": "Как попасть на встречу, если я в другом городе?"}, user_id=USER), "t1", binding=whieda_bot_binding
        )
    assert result["route"] != "guest_menu"
    tg.text.assert_not_awaited()


async def test_the_guest_buttons_go_through_the_processor_chain(tg, whieda_tenant, whieda_bot_binding, monkeypatch):
    run_action(monkeypatch, registration.ActionResult(Reply("Вы записаны."), "registered"), "register")
    update = {"update_id": 5, "callback_query": {"id": "cq-9", "data": "mreg:12", "from": {"id": USER},
                                                  "message": {"message_id": 3, "chat": {"id": USER, "type": "private"}}}}
    with patch("app.telegram.processor.try_handle_marketing_consent_callback", AsyncMock(return_value=None)):
        result = await process_core_telegram_update(whieda_tenant, update, "t1", binding=whieda_bot_binding)
    assert result["route"] == "guest_callback" and result["status"] == "registered"


async def test_the_consult_button_still_gets_the_old_placeholder_through_the_processor(whieda_tenant, whieda_bot_binding):
    state = SimpleNamespace(text=AsyncMock(return_value={"ok": True}), answer=AsyncMock(return_value={"ok": True}))
    update = {"update_id": 5, "callback_query": {"id": "cq-9", "data": "funnel:consult", "from": {"id": USER},
                                                  "message": {"message_id": 3, "chat": {"id": USER, "type": "private"}}}}
    with patch.object(system_word, "send_telegram_text", state.text), patch.object(system_word, "answer_callback_query", state.answer), \
            patch("app.telegram.processor.try_handle_marketing_consent_callback", AsyncMock(return_value=None)), \
            binding_context_scope(whieda_bot_binding):
        result = await process_core_telegram_update(whieda_tenant, update, "t1", binding=whieda_bot_binding)
    assert result["route"] == "funnel_callback" and result["status"] == "consult"
    assert state.text.await_args.kwargs["text"] == texts.FUNNEL_CONSULT_PLACEHOLDER


# ---- Max: плюмбинг ---------------------------------------------------------------------------------


def max_event(kind: str = "start", text: str = "", chat_id: int | None = 555, user: int = USER) -> MaxEvent:
    return MaxEvent(kind=kind, user_id=user, chat_id=chat_id, text=text, username=None, display_name="Инна", raw={})


def max_callback(payload: str, chat_type: str = "dialog") -> MaxCallbackEvent:
    return MaxCallbackEvent(
        callback_id="cb-1", payload=payload, user_id=USER, chat_id=555, message_id="mid-1", chat_type=chat_type,
        username=None, display_name="Инна", raw={},
    )


@pytest.fixture
def mx(monkeypatch):
    state = SimpleNamespace(
        text=AsyncMock(return_value={"ok": True}),
        tour=AsyncMock(return_value={"ok": True, "route": "max_funnel_system", "status": "tour"}),
        answered=AsyncMock(return_value={"ok": True}),
        actor=AsyncMock(),
        advisor=AsyncMock(return_value={"answer_text": "Ответ советника", "answer_mode": "structured_business"}),
        source_start=AsyncMock(return_value={"ok": True, "route": "max_start", "status": "source"}),
    )
    monkeypatch.setattr(guest_bot, "send_max_text", state.text)
    monkeypatch.setattr(guest_bot, "send_tour_max", state.tour)
    monkeypatch.setattr(max_processor, "send_max_text", state.text)
    monkeypatch.setattr(max_processor, "answer_max_callback", state.answered)
    monkeypatch.setattr(max_processor, "ensure_channel_actor", state.actor)
    monkeypatch.setattr(max_processor, "handle_structured_query", state.advisor)
    monkeypatch.setattr(max_processor, "should_deliver_telegram_response", lambda _r: True)
    monkeypatch.setattr(max_processor, "_handle_max_source_start", state.source_start)
    return state


async def test_a_max_start_with_a_lead_token_sends_the_greeting_with_the_same_buttons(mx):
    with patch.object(guest_bot, "start_from_lead", AsyncMock(return_value=lead_result())) as start:
        result = await max_processor.handle_max_start(TENANT, max_event(text="lead_L-20261030-AB12CD3"), "t1")
    assert result == {"ok": True, "route": "max_lead_start", "status": "registered", "attribution": "attributed", "trace_id": "t1"}
    assert start.await_args.kwargs["channel"] == "max" and start.await_args.kwargs["public_id"] == "L-20261030-AB12CD3"
    mx.text.assert_awaited_once_with(
        chat_id=555, user_id=USER, text="Вы пришли от Игорь.",
        buttons=[[max_button_callback("Перенести", "mmove:7")], [max_button_callback("Показ системы", "funnel:tour")]],
    )
    mx.source_start.assert_not_awaited()


async def test_the_max_partner_is_accepted_through_the_channel_first_touch_function(mx):
    async def fake_start(tenant_id, **kwargs):
        await kwargs["accept_partner"]("invite-code-1")
        return lead_result()

    event = max_event(text="lead_L-1-ABC")
    with patch.object(guest_bot, "start_from_lead", fake_start), \
            patch.object(guest_bot, "accept_channel_referral_start", AsyncMock(return_value=SimpleNamespace(status="attributed"))) as accept:
        await max_processor.handle_max_start(TENANT, event, "t1")
    accept.assert_awaited_once_with(
        "whieda", channel="max", channel_user_id=USER, channel_chat_id=555, username=None, display_name="Инна",
        invite_code="invite-code-1",
    )


@pytest.mark.parametrize("outcome", [None, RuntimeError("db down")])
async def test_a_max_lead_token_without_a_lead_becomes_the_plain_welcome(mx, outcome):
    mock = AsyncMock(return_value=outcome) if outcome is None else AsyncMock(side_effect=outcome)
    with patch.object(guest_bot, "start_from_lead", mock):
        result = await max_processor.handle_max_start(TENANT, max_event(text="lead_L-NOPE-000"), "t1")
    assert result["status"] == "source"
    assert mx.source_start.await_args.args[2].tag == "unknown"
    mx.text.assert_not_awaited()


async def test_a_max_malformed_lead_token_is_also_the_plain_welcome(mx):
    result = await max_processor.handle_max_start(TENANT, max_event(text="lead_"), "t1")
    assert result["status"] == "source" and mx.source_start.await_args.args[2].tag == "unknown"


async def test_the_other_max_starts_do_not_touch_the_lead_start(mx):
    with patch.object(guest_bot, "start_from_lead", AsyncMock()) as start, \
            patch.object(max_processor, "accept_channel_referral_start", AsyncMock(return_value=SimpleNamespace(status="invalid", inviter_actor_id=None))):
        await max_processor.handle_max_start(TENANT, max_event(text="ref_abcdefgh"), "t1")
        await max_processor.handle_max_start(TENANT, max_event(text="src_vk"), "t1")
    start.assert_not_awaited()


async def test_the_bare_max_start_of_a_guest_shows_the_guest_menu(mx, monkeypatch):
    guest_world(monkeypatch, guest_context=dataclasses.replace(GUEST, channel="max"), view=(None, [TUE]), card=None)
    result = await max_processor.handle_max_start(TENANT, max_event(text=""), "t1")
    assert result == {"ok": True, "route": "max_guest_menu", "status": "shown", "trace_id": "t1"}
    assert mx.text.await_args.kwargs["text"].startswith("Вы в WWC.")
    assert mx.text.await_args.kwargs["buttons"][0] == [max_button_callback("Записаться: вт 03.11 19:30", "mreg:11")]


async def test_the_bare_max_start_of_a_stranger_is_the_old_welcome(mx, monkeypatch):
    guest_world(monkeypatch, guest_context=None)
    result = await max_processor.handle_max_start(TENANT, max_event(text=""), "t1")
    assert result["status"] == "plain"
    assert mx.text.await_args.args == () and mx.text.await_args.kwargs["text"] == max_processor.WELCOME_PLAIN


async def test_the_menu_word_in_max_is_the_guest_menu_for_a_guest_and_the_advisor_for_others(mx, monkeypatch):
    guest_world(monkeypatch, guest_context=dataclasses.replace(GUEST, channel="max"), view=(None, []))
    shown = await max_processor.handle_max_message(TENANT, max_event("message", "меню"), "t1")
    assert shown["route"] == "max_guest_menu"
    mx.advisor.assert_not_awaited()
    guest_world(monkeypatch, guest_context=None)
    other = await max_processor.handle_max_message(TENANT, max_event("message", "меню"), "t1")
    assert other["route"] == "max_message"
    mx.advisor.assert_awaited_once()


async def test_other_max_words_are_not_hijacked(mx, monkeypatch):
    guest_world(monkeypatch, guest_context=dataclasses.replace(GUEST, channel="max"))
    await max_processor.handle_max_message(TENANT, max_event("message", "как попасть на встречу?"), "t1")
    mx.advisor.assert_awaited_once()


async def test_a_max_register_button_sends_the_confirmation_as_a_new_message_and_answers_quietly(mx, monkeypatch):
    reply = Reply("Вы записаны.", [[("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]])
    action = run_action(monkeypatch, registration.ActionResult(reply, "registered"), "register")
    result = await max_processor.process_max_event(TENANT, max_callback("mreg:12"), "t1")
    assert result["status"] == "handled" and result["prefix"] == "mreg:" and result["answered"] is True
    action.assert_awaited_once_with("whieda", "max", USER, 12)
    mx.text.assert_awaited_once_with(
        chat_id=555, user_id=USER, text="Вы записаны.",
        buttons=[[max_button_callback("Перенести", "mmove:7"), max_button_callback("Отменить запись", "mcancel:7")]],
    )
    mx.answered.assert_awaited_once_with("cb-1", notification=None, text=None, buttons=None)


@pytest.mark.parametrize(("payload", "name", "args"), [
    ("mmove:7", "move_list", ("whieda", "max", USER, 7)),
    ("mmoveto:7:13", "move_to", ("whieda", "max", USER, 7, 13)),
    ("mcancel:7", "cancel", ("whieda", "max", USER, 7)),
    ("funnel:meet", "list_meetings", ("whieda", "max", USER)),
])
async def test_each_max_button_calls_its_action(mx, monkeypatch, payload, name, args):
    action = run_action(monkeypatch, registration.ActionResult(Reply("ok"), "done"), name)
    await max_processor.process_max_event(TENANT, max_callback(payload), "t1")
    action.assert_awaited_once_with(*args)
    mx.text.assert_awaited_once()


async def test_the_max_tour_button(mx):
    await max_processor.process_max_event(TENANT, max_callback("funnel:tour"), "t1")
    mx.tour.assert_awaited_once_with(TENANT, chat_id=555, user_id=USER, trace_id="t1")
    mx.text.assert_not_awaited()


@pytest.mark.parametrize(("username", "url"), [
    (None, "https://t.me/WHIEDA_Advisor_bot"), ("", "https://t.me/WHIEDA_Advisor_bot"),
    ("@wwc_stage_bot", "https://t.me/wwc_stage_bot"), ("wwc_stage_bot", "https://t.me/wwc_stage_bot"),
])
async def test_the_max_ask_button_points_to_the_telegram_bot_because_max_has_no_support_tickets(mx, username, url):
    with patch.object(guest_bot, "get_settings", return_value=SimpleNamespace(telegram_bot_username=username)):
        result = await max_processor.process_max_event(TENANT, max_callback("funnel:ask"), "t1")
    assert result["status"] == "handled" and result["answered"] is True
    text = mx.text.await_args.kwargs["text"]
    assert mx.text.await_args.kwargs["chat_id"] == 555 and mx.text.await_args.kwargs["user_id"] == USER
    assert text == (
        f"Вопрос Виктору удобнее задать в Telegram-боте WWC: {url}\n"
        "Нажмите «Старт», затем напишите «Поддержка» — обращение сразу попадёт к нему."
    )
    mx.text.assert_awaited_once()
    assert "buttons" not in mx.text.await_args.kwargs


async def test_the_max_consult_button_keeps_the_old_placeholder(mx, monkeypatch):
    monkeypatch.setattr(system_word, "send_max_text", mx.text)
    await max_processor.process_max_event(TENANT, max_callback("funnel:consult"), "t1")
    mx.text.assert_awaited_once_with(chat_id=555, user_id=USER, text=texts.FUNNEL_CONSULT_PLACEHOLDER)


async def test_a_failed_max_action_apologises_and_the_webhook_still_answers(mx, monkeypatch):
    run_action(monkeypatch, RuntimeError("db down"), "register")
    result = await max_processor.process_max_event(TENANT, max_callback("mreg:12"), "t1")
    assert result["ok"] is True and result["answered"] is True
    mx.text.assert_awaited_once_with(chat_id=555, user_id=USER, text="Не получилось. Попробуйте ещё раз чуть позже.")


async def test_a_max_button_in_a_group_or_for_another_tenant_does_nothing(mx, monkeypatch):
    action = run_action(monkeypatch, registration.ActionResult(Reply("ok"), "x"), "register")
    await max_processor.process_max_event(TENANT, max_callback("mreg:12", chat_type="chat"), "t1")
    await max_processor.process_max_event(OTHER_TENANT, max_callback("mreg:12"), "t1")
    action.assert_not_awaited()
    mx.text.assert_not_awaited()


async def test_the_ids_in_max_buttons_must_be_numbers(mx, monkeypatch):
    action = run_action(monkeypatch, registration.ActionResult(Reply("ok"), "x"), "register")
    result = await max_processor.process_max_event(TENANT, max_callback("mreg:abc"), "t1")
    assert result["status"] == "handled"
    action.assert_not_awaited()
    mx.text.assert_not_awaited()
