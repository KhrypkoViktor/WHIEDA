"""Срез 5 бот-воронки, часть 2: сообщения после Живой встречи, визитка партнёра, итог партнёру, карточка CRM,
фото визитки в воркере и картинка по адресу в Max (31.10.2026).

Без базы и сети: база подменена фейком, Telegram и Max — моками. Настоящий SQL и сквозной сценарий —
tests/test_meeting_reminders_postgres.py.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.funnel import after_meeting, partner_card, texts
from app.funnel.after_meeting import after_key, no_show_message, summary_key, summary_text
from app.funnel.partner_card import OwnerCard, build_card, card_intro, card_rows, card_text, guest_cards, safe_https_url
from app.leads import meeting_leads
from app.max import outbox as max_outbox
from app.settings import get_settings
from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown
from tests.test_meeting_reminders import FakeConn, fake_tenant_connection

UTC = timezone.utc
# 05.11.2026 16:30 UTC = чт 05.11 19:30 МСК; длительность 60 минут → конец 17:30 UTC
START = datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
AFTER_NOW = START + timedelta(minutes=61 + 30)  # через 30 минут после конца и минуту сверху
USER = 4242_0007
RECORDING = "https://rutube.example/video/abc"
NEXT_TUE = {"meeting_id": 13, "kind": "live", "title": "Живая встреча", "starts_at": datetime(2026, 11, 10, 16, 30, tzinfo=UTC), "duration_min": 60, "status": "planned"}


def meeting(**changes: Any) -> dict[str, Any]:
    base = {
        "meeting_id": 12, "title": "Живая встреча", "starts_at": START, "duration_min": 60, "status": "planned",
        "recording_url": RECORDING,
    }
    base.update(changes)
    return base


IGOR_PROFILE = {
    "display_name": "Игорь Волков", "photo_url": "https://wwc.best/media/igor.jpg", "public_site_url": "https://igor.wwc.best/",
    "subdomain": "igor", "contacts": {"phone": "+7 999 111-22-33", "max_url": "https://max.ru/u/igor", "email": "igor@example.com"},
}


def igor_row(**changes: Any) -> dict[str, Any]:
    base = {"owner_id": "igor-actor", "ref_code": "igor", "public_profile": IGOR_PROFILE, "actor_name": "Igor", "telegram_username": "@igor_v"}
    base.update(changes)
    return base


IGOR = build_card(igor_row())
VIKTOR = OwnerCard(owner_id="viktor", ref_code=None, name="Виктор Хрипко", telegram_username="khripko", site_url="https://wwc.best")
ELENA = OwnerCard(owner_id="elena", ref_code="onlineelena", name="Елена", phone="+375291112233", max_url="https://max.ru/u/elena")


# ---- визитка: очистка полей ------------------------------------------------------------------------


def test_the_card_takes_name_contacts_site_photo_and_the_personal_telegram_from_the_profile():
    assert IGOR == OwnerCard(
        owner_id="igor-actor", ref_code="igor", name="Игорь Волков", photo_url="https://wwc.best/media/igor.jpg",
        site_url="https://igor.wwc.best/", max_url="https://max.ru/u/igor", phone="+7 999 111-22-33",
        email="igor@example.com", telegram_username="igor_v",
    )


def test_the_site_is_the_public_url_or_the_chosen_subdomain_and_never_a_guess():
    no_url = {**IGOR_PROFILE, "public_site_url": None}
    assert partner_card.site_url(no_url) == "https://igor.wwc.best"
    assert partner_card.site_url({**no_url, "public_site_url": "http://igor.wwc.best"}) == "https://igor.wwc.best"  # не https
    assert partner_card.site_url({"display_name": "X"}) is None                    # поддомена нет — адрес не выдумываем
    assert partner_card.site_url({"subdomain": "Bad_Sub.evil"}) is None
    assert partner_card.site_url({"subdomain": "a" * 70}) is None


def test_unsafe_links_are_dropped_so_telegram_never_rejects_the_whole_message():
    assert safe_https_url("https://max.ru/u/abc", host_suffixes=("max.ru",)) == "https://max.ru/u/abc"
    assert safe_https_url("https://www.max.ru/u/abc", host_suffixes=("max.ru",)) == "https://www.max.ru/u/abc"
    for bad in (
        "http://max.ru/u/abc", "https://max.ru.evil.example/u", "https://evil.example/max.ru", "max.ru/u/abc",
        "https://max.ru/u/ab c", 'https://max.ru/"x', "https://user:pass@max.ru/u", "javascript:alert(1)", "https://max.ru/" + "a" * 300, "",
        None,
    ):
        assert safe_https_url(bad, host_suffixes=("max.ru",)) is None, bad
    card = build_card(igor_row(public_profile={**IGOR_PROFILE, "contacts": {"max_url": "https://t.me/evil", "email": "не почта"}}))
    assert card.max_url is None and card.email is None


@pytest.mark.parametrize(("raw", "expected"), [("@Igor_V", "Igor_V"), ("igor_v", "igor_v"), ("  @igor_v ", "igor_v"), ("ab", None),
                                               ("1igor", None), ("igor v", None), ("igor-v", None), ("", None), (None, None)])
def test_the_telegram_username_is_validated(raw, expected):
    assert partner_card.telegram_username(raw) == expected


def test_the_name_cannot_carry_markup_or_links_and_a_missing_profile_falls_back_to_the_actor():
    card = build_card(igor_row(public_profile={"display_name": '<b>Игорь</b> https://evil.example @spam'}))
    assert "<" not in card.name and ">" not in card.name and "evil.example" not in card.name and "@spam" not in card.name
    only_actor = build_card({"owner_id": "viktor", "ref_code": None, "actor_name": "Виктор Хрипко", "telegram_username": "khripko"})
    assert only_actor.name == "Виктор Хрипко" and only_actor.telegram_username == "khripko" and only_actor.site_url is None
    assert build_card({"owner_id": "x", "ref_code": None}) is None


def test_a_relative_photo_path_is_absolutized_with_the_media_base_and_must_be_https(monkeypatch):
    monkeypatch.setenv("PLATFORM_TENANT_MEDIA_BASE_URL", "https://media.example")
    get_settings.cache_clear()
    try:
        card = build_card(igor_row(public_profile={**IGOR_PROFILE, "photo_url": "/partners/igor.jpg"}))
        assert card.photo_url == "https://media.example/partners/igor.jpg"
        assert build_card(igor_row(public_profile={**IGOR_PROFILE, "photo_url": "http://wwc.best/a.jpg"})).photo_url is None
    finally:
        get_settings.cache_clear()


def test_the_card_text_buttons_and_intros():
    assert card_text(IGOR, "Привет") == "Привет\nТелефон: +7 999 111-22-33\nПочта: igor@example.com"
    assert card_text(VIKTOR, "Привет") == "Привет"
    assert card_rows(IGOR) == [
        [("Написать в Telegram", "https://t.me/igor_v")], [("Написать в Max", "https://max.ru/u/igor")], [("Сайт", "https://igor.wwc.best/")],
    ]
    assert card_rows(ELENA) == [[("Написать в Max", "https://max.ru/u/elena")]]
    assert card_rows(OwnerCard(None, "x", "Имя")) == []
    assert card_intro(IGOR, position=0, partner=True).startswith("Спасибо, что были на Живой встрече!\nВаш партнёр WWC — Игорь Волков.")
    assert card_intro(VIKTOR, position=0, partner=False) == "Спасибо, что были на Живой встрече!\nВиктор Хрипко — на связи по любым вопросам."
    assert card_intro(ELENA, position=1, partner=False) == "Елена — тоже на связи по любым вопросам."


# ---- какие визитки получает гость ------------------------------------------------------------------


def load_card_stub(cards: dict[str, OwnerCard | None]):
    async def _load(conn, tenant_id, *, owner_id=None, ref_code=None):
        return cards.get(f"ref:{ref_code}" if ref_code else f"owner:{owner_id}")

    return _load


@pytest.fixture
def settings_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "viktor")
    monkeypatch.delenv("PLATFORM_MEETING_AD_CARD_REFS", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


async def test_a_guest_of_a_partner_gets_that_partners_card(settings_env):
    cards = {"owner:igor-actor": IGOR, "owner:viktor": VIKTOR, "ref:onlineelena": ELENA}
    with patch.object(partner_card, "load_card", load_card_stub(cards)):
        assert await guest_cards(object(), "whieda", "igor-actor") == ([IGOR], True)


async def test_a_guest_from_ads_gets_two_cards_viktor_then_elena(settings_env):
    cards = {"owner:igor-actor": IGOR, "owner:viktor": VIKTOR, "ref:onlineelena": ELENA}
    with patch.object(partner_card, "load_card", load_card_stub(cards)):
        for owner in (None, "", "viktor"):  # без партнёра, пустой владелец, владелец «по умолчанию»
            assert await guest_cards(object(), "whieda", owner) == ([VIKTOR, ELENA], False), owner


async def test_a_partner_without_a_profile_is_treated_like_a_guest_without_a_partner(settings_env):
    cards = {"owner:viktor": VIKTOR, "ref:onlineelena": ELENA}
    with patch.object(partner_card, "load_card", load_card_stub(cards)):
        assert await guest_cards(object(), "whieda", "gone-actor") == ([VIKTOR, ELENA], False)


async def test_missing_cards_are_skipped_and_the_same_person_is_not_sent_twice(settings_env):
    with patch.object(partner_card, "load_card", load_card_stub({"owner:viktor": VIKTOR})):
        assert await guest_cards(object(), "whieda", None) == ([VIKTOR], False)
    with patch.object(partner_card, "load_card", load_card_stub({})):
        assert await guest_cards(object(), "whieda", None) == ([], False)
    same = OwnerCard(owner_id="viktor", ref_code="viktor", name="Виктор")
    with patch.object(partner_card, "load_card", load_card_stub({"owner:viktor": same, "ref:onlineelena": same})):
        assert await guest_cards(object(), "whieda", None) == ([same], False)


async def test_the_extra_card_refs_are_configurable(settings_env):
    settings_env.setenv("PLATFORM_MEETING_AD_CARD_REFS", " Anna ,onlineelena, anna ")
    get_settings.cache_clear()
    assert get_settings().parsed_meeting_ad_card_refs() == ("anna", "onlineelena")
    asked: list[str | None] = []

    async def _load(conn, tenant_id, *, owner_id=None, ref_code=None):
        asked.append(ref_code or owner_id)

    with patch.object(partner_card, "load_card", _load):
        await guest_cards(object(), "whieda", None)
    assert asked == ["viktor", "anna", "onlineelena"]


def test_the_default_extra_card_is_onlineelena(settings_env):
    assert get_settings().parsed_meeting_ad_card_refs() == ("onlineelena",)
    assert partner_card.is_ad_guest(None) and partner_card.is_ad_guest("viktor") and not partner_card.is_ad_guest("igor-actor")


# ---- тексты после встречи --------------------------------------------------------------------------


def test_the_summary_lists_who_came_and_who_did_not_and_skips_empty_lines():
    assert summary_text("чт 05.11 19:30 МСК", ["Анна", "Олег"], ["Ирина"]) == (
        "Живая встреча чт 05.11 19:30 МСК:\nБыли: Анна, Олег.\nЗаписались, но не пришли: Ирина."
    )
    assert summary_text("чт 05.11 19:30 МСК", [], ["Ирина"]) == "Живая встреча чт 05.11 19:30 МСК:\nЗаписались, но не пришли: Ирина."
    assert summary_text("чт 05.11 19:30 МСК", ["Анна"], []) == "Живая встреча чт 05.11 19:30 МСК:\nБыли: Анна."
    anon = summary_text("чт 05.11 19:30 МСК", ["Анна"], [], anonymous_attended=2, anonymous_no_show=1)
    assert "Были: Анна, из бота без заявки — 2." in anon and "Записались, но не пришли: из бота без заявки — 1." in anon


def test_a_long_list_of_names_is_cut():
    names = [f"Гость{i}" for i in range(45)]
    text = summary_text("чт 05.11 19:30 МСК", names, [])
    assert "Гость29" in text and "Гость30" not in text and "…и ещё 15" in text


def test_the_keys_of_the_messages():
    assert after_key(7, 1) == "mafter:7:1" and after_key("8", 2) == "mafter:8:2"
    assert summary_key(12, "igor-actor") == "msum:12:igor-actor"


def test_the_no_show_message_gives_the_recording_and_the_nearest_meetings():
    text, rows = no_show_message(meeting(), [NEXT_TUE], frozenset(), now=AFTER_NOW)
    assert text == "Вы записывались на Живую встречу чт 05.11 19:30 МСК, но не дошли. Ничего страшного!\nБлижайшие встречи — выберите удобную."
    assert rows == [[("▶️ Смотреть запись", RECORDING)], [("Записаться: вт 10.11 19:30", "mreg:13")]]
    registered = no_show_message(meeting(), [NEXT_TUE], frozenset({13}), now=AFTER_NOW)[1]
    assert registered[-1] == [("Вы записаны: вт 10.11 19:30", "mreg:13")]


def test_the_no_show_message_without_a_recording_or_meetings():
    text, rows = no_show_message(meeting(recording_url=None), [], frozenset(), now=AFTER_NOW)
    assert text.endswith("Ближайших живых встреч пока нет. Загляните сюда чуть позже.") and rows == []
    assert no_show_message(meeting(recording_url="не ссылка"), [], frozenset(), now=AFTER_NOW)[1] == []


# ---- карточка CRM ----------------------------------------------------------------------------------


def test_the_card_tags_and_notes():
    assert meeting_leads.attendance_card_change("attended", starts_at=START, now=AFTER_NOW) == (
        "Был(а) на Живой встрече: чт 05.11 19:30 МСК.", ["был 05.11"]
    )
    assert meeting_leads.attendance_card_change("no_show", starts_at=START, now=AFTER_NOW) == (
        "Записался(ась) на Живую встречу чт 05.11 19:30 МСК, но не пришёл(пришла).", ["не дошёл 05.11"]
    )
    with pytest.raises(ValueError):
        meeting_leads.attendance_card_change("joined", starts_at=START)  # type: ignore[arg-type]


async def test_the_card_gets_the_tag_the_note_and_the_ping_step_for_today():
    conn = FakeConn()
    record = AsyncMock(return_value=True)
    move = AsyncMock(return_value=datetime(2026, 11, 5).date())
    with patch.object(meeting_leads, "record_card_event", record), patch.object(meeting_leads, "move_card_step_today", move):
        assert await meeting_leads.card_attendance_event(conn, "whieda", "card-1", "attended", starts_at=START, now=AFTER_NOW) is True
    assert record.await_args.kwargs == {
        "tenant_id": "whieda", "contact_id": "card-1", "note": "Был(а) на Живой встрече: чт 05.11 19:30 МСК.", "add_tags": ["был 05.11"],
    }
    assert move.await_args.kwargs == {
        "tenant_id": "whieda", "contact_id": "card-1", "status": "new", "from_steps": ("invite", "ping", None), "to_step": "ping",
    }


async def test_no_card_means_nothing_happens_and_a_card_failure_is_logged_without_details(caplog):
    record = AsyncMock()
    with patch.object(meeting_leads, "record_card_event", record):
        assert await meeting_leads.card_attendance_event(FakeConn(), "whieda", None, "no_show", starts_at=START) is False
    record.assert_not_awaited()
    conn = FakeConn()
    move = AsyncMock()
    with patch.object(meeting_leads, "record_card_event", AsyncMock(side_effect=RuntimeError("Анна +79991112233"))), patch.object(
        meeting_leads, "move_card_step_today", move
    ), caplog.at_level(logging.WARNING, logger="app.leads.meeting_leads"):
        assert await meeting_leads.card_attendance_event(conn, "whieda", "card-1", "no_show", starts_at=START) is False
    move.assert_not_awaited()
    assert conn.rollbacks == 1
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "meeting_lead_card_attendance_failed" in text and "Анна" not in text and "79991112233" not in text


async def test_a_deleted_card_gets_no_step():
    move = AsyncMock()
    with patch.object(meeting_leads, "record_card_event", AsyncMock(return_value=False)), patch.object(meeting_leads, "move_card_step_today", move):
        assert await meeting_leads.card_attendance_event(FakeConn(), "whieda", "card-1", "attended", starts_at=START) is False
    move.assert_not_awaited()


# ---- разбор встречи: фейковая база -----------------------------------------------------------------


class AfterDb:
    """Таблица записей в памяти: запросы узнаются по тексту SQL из after_meeting."""

    def __init__(self, registrations: list[dict[str, Any]], summary_rows: list[dict[str, Any]] | None = None) -> None:
        self.registrations = {int(r["registration_id"]): {"claimed": False, **r} for r in registrations}
        self.summary_rows = summary_rows
        self.claims: list[int] = []

    async def fetch_all(self, conn, query, params=()):  # noqa: ARG002
        q = " ".join(query.split())
        if "r.status in ('registered', 'joined')" in q:
            return [dict(r) for r in self.registrations.values() if not r["claimed"] and r["status"] in ("registered", "joined")]
        if "r.status in ('attended', 'no_show')" in q:
            if self.summary_rows is not None:
                return self.summary_rows
            return [
                {"status": r["status"], "name": r.get("name"), "assigned_owner_id": r.get("assigned_owner_id")}
                for r in self.registrations.values() if r["claimed"]
            ]
        raise AssertionError(f"unexpected query: {q[:120]}")

    async def fetch_one(self, conn, query, params=()):  # noqa: ARG002
        q = " ".join(query.split())
        if q.startswith("update meeting_registrations set status = case"):
            _, _, registration_id = params
            row = self.registrations[int(registration_id)]
            if row["claimed"] or row["status"] not in ("registered", "joined"):
                return None
            row["claimed"] = True
            row["status"] = "attended" if row["status"] == "joined" else "no_show"
            self.claims.append(int(registration_id))
            return {"status": row["status"]}
        if "count(*)" in q:
            return {"n": sum(1 for r in self.registrations.values() if r["status"] in ("registered", "joined"))}
        raise AssertionError(f"unexpected query: {q[:120]}")


def pending(registration_id: int, status: str, *, owner: str | None = "igor-actor", name: str | None = "Анна", channel: str = "telegram",
            user: int = USER, card: str | None = "card-1") -> dict[str, Any]:
    return {
        "registration_id": registration_id, "lead_id": "lead", "channel": channel, "channel_user_id": user, "status": status,
        "assigned_owner_id": owner, "crm_card": card, "name": name,
    }


@pytest.fixture
def world(settings_env):
    """Всё, что снаружи разбора: визитки, ближайшие встречи, карточки CRM, очередь, чаты владельцев."""
    state = SimpleNamespace(
        conn=FakeConn(),
        messages=[],
        outbox=[],
        card_events=[],
        cards={"igor-actor": ([IGOR], True), None: ([VIKTOR, ELENA], False)},
        chats={"igor-actor": ("7001", 7001), "viktor": ("7002", 7002)},
        crm={7001: "https://igor.wwc.best/crm/", 7002: None},
        fail_for=set(),
    )

    async def guest_cards_stub(conn, tenant_id, owner_id):
        return state.cards[owner_id or None] if (owner_id or None) in state.cards else ([], False)

    async def enqueue(conn, **kwargs):
        if kwargs["user_id"] in state.fail_for:
            raise RuntimeError("boom Анна +79991112233")
        state.messages.append(kwargs)
        return {"created": True}

    async def enqueue_outbox(conn, **kwargs):
        state.outbox.append(kwargs)
        return {"created": True}

    async def card_event(conn, tenant_id, contact_id, kind, *, starts_at, now=None):
        if not contact_id:  # как настоящая функция: нет карточки — ничего не делает
            return False
        state.card_events.append((contact_id, kind, starts_at))
        return True

    async def owner_chat(conn, tenant_id, owner):
        return state.chats.get(owner)

    async def crm_button(conn, tenant_id, telegram_user_id):
        return state.crm.get(telegram_user_id)

    with ExitStack() as stack:
        stack.enter_context(patch.object(after_meeting, "guest_cards", guest_cards_stub))
        stack.enter_context(patch.object(after_meeting, "enqueue_guest_message", enqueue))
        stack.enter_context(patch.object(after_meeting, "enqueue_outbox_event", enqueue_outbox))
        stack.enter_context(patch.object(after_meeting, "card_attendance_event", card_event))
        stack.enter_context(patch.object(after_meeting, "owner_chat", owner_chat))
        stack.enter_context(patch.object(after_meeting, "crm_button_url", crm_button))
        stack.enter_context(patch.object(after_meeting.registrations, "open_meetings", AsyncMock(return_value=[NEXT_TUE])))
        stack.enter_context(patch.object(after_meeting.registrations, "active_registrations", AsyncMock(return_value=[])))
        yield state


def attach_db(stack_or_none, db: AfterDb) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch.object(after_meeting, "fetch_all", db.fetch_all))
    stack.enter_context(patch.object(after_meeting, "fetch_one", db.fetch_one))
    return stack


def context(**changes: Any) -> after_meeting._Pass:
    return after_meeting._Pass("whieda", "whieda-advisor-bot", meeting(**changes), AFTER_NOW)


async def run_meeting(world, db: AfterDb, **meeting_changes: Any) -> tuple[int, after_meeting._Pass]:
    ctx = context(**meeting_changes)
    with attach_db(None, db):
        processed = await after_meeting.process_meeting(world.conn, ctx)
    return processed, ctx


async def test_a_joined_guest_becomes_attended_and_gets_the_partner_card(world):
    db = AfterDb([pending(7, "joined")])
    processed, ctx = await run_meeting(world, db)
    assert processed == 1 and db.registrations[7]["status"] == "attended"
    (message,) = world.messages
    assert message["idempotency_key"] == "mafter:7:1" and message["event_type"] == "meeting_after"
    assert (message["channel"], message["user_id"], message["binding_id"]) == ("telegram", USER, "whieda-advisor-bot")
    assert message["text"].startswith("Спасибо, что были на Живой встрече!\nВаш партнёр WWC — Игорь Волков.")
    assert "Телефон: +7 999 111-22-33" in message["text"] and "Почта: igor@example.com" in message["text"]
    assert message["rows"] == card_rows(IGOR) and message["photo_url"] == "https://wwc.best/media/igor.jpg"
    assert message["due_at"] == AFTER_NOW
    assert world.card_events == [("card-1", "attended", START)]
    assert ctx.counts == {"attended": 1, "no_show": 0, "summaries": 1}


async def test_a_guest_who_did_not_press_becomes_no_show_and_gets_the_recording_and_the_next_meetings(world):
    db = AfterDb([pending(8, "registered", channel="max", user=USER + 1)])
    processed, _ = await run_meeting(world, db)
    assert processed == 1 and db.registrations[8]["status"] == "no_show"
    (message,) = world.messages
    assert message["idempotency_key"] == "mafter:8:1" and message["channel"] == "max" and message["user_id"] == USER + 1
    assert message["text"].startswith("Вы записывались на Живую встречу чт 05.11 19:30 МСК, но не дошли.")
    assert message["rows"] == [[("▶️ Смотреть запись", RECORDING)], [("Записаться: вт 10.11 19:30", "mreg:13")]]
    assert world.card_events == [("card-1", "no_show", START)]


async def test_a_guest_from_ads_gets_two_cards_in_a_row_viktor_first(world):
    db = AfterDb([pending(7, "joined", owner=None, name=None, card=None)])
    await run_meeting(world, db)
    first, second = world.messages
    assert (first["idempotency_key"], second["idempotency_key"]) == ("mafter:7:1", "mafter:7:2")
    assert first["text"].startswith("Спасибо, что были на Живой встрече!\nВиктор Хрипко — на связи по любым вопросам.")
    assert second["text"].startswith("Елена — тоже на связи по любым вопросам.") and "Телефон: +375291112233" in second["text"]
    assert first["rows"] == [[("Написать в Telegram", "https://t.me/khripko")], [("Сайт", "https://wwc.best")]]
    assert second["rows"] == [[("Написать в Max", "https://max.ru/u/elena")]]
    assert first["photo_url"] is None
    assert second["due_at"] - first["due_at"] == timedelta(seconds=2)   # порядок виден и по времени отправки
    assert world.card_events == []                                       # карточки CRM нет — события нет


async def test_without_any_card_the_guest_still_gets_the_thanks(world):
    world.cards.clear()
    db = AfterDb([pending(7, "joined", owner="nobody")])
    await run_meeting(world, db)
    (message,) = world.messages
    assert message["text"] == texts.AFTER_THANKS and not message.get("rows") and not message.get("photo_url")


async def test_an_already_processed_registration_sends_nothing(world):
    db = AfterDb([pending(7, "joined")])
    db.registrations[7]["claimed"] = True
    db.registrations[7]["status"] = "attended"
    processed, _ = await run_meeting(world, db)
    assert processed == 0 and world.messages == [] and world.card_events == []


async def test_after_the_last_registration_partners_get_the_summary_and_the_meeting_becomes_done(world):
    db = AfterDb([
        pending(7, "joined", name="Анна"),
        pending(8, "registered", name="Олег", user=USER + 1),
        pending(9, "registered", owner=None, name=None, user=USER + 2, card=None),   # без заявки: считаем числом, владелец — WWC
        pending(10, "joined", owner="igor-actor", name="Ирина", user=USER + 3),
    ])
    processed, ctx = await run_meeting(world, db)
    assert processed == 4 and ctx.counts == {"attended": 2, "no_show": 2, "summaries": 2}
    assert {o["idempotency_key"] for o in world.outbox} == {"msum:12:igor-actor", "msum:12:viktor"}
    by_key = {o["idempotency_key"]: o for o in world.outbox}
    igor = by_key["msum:12:igor-actor"]
    assert igor["event_type"] == "meeting_summary" and igor["due_at"] == AFTER_NOW
    assert igor["payload"] == {
        "chat_id": "7001",
        "text": "Живая встреча чт 05.11 19:30 МСК:\nБыли: Анна, Ирина.\nЗаписались, но не пришли: Олег.",
        "binding_id": "whieda-advisor-bot",
        "reply_markup": {"inline_keyboard": [[{"text": "📋 Открыть CRM", "url": "https://igor.wwc.best/crm/"}]]},
        "site_login_user_id": 7001,
    }
    viktor = by_key["msum:12:viktor"]["payload"]
    assert viktor["text"] == "Живая встреча чт 05.11 19:30 МСК:\nЗаписались, но не пришли: из бота без заявки — 1."
    assert "reply_markup" not in viktor and "site_login_user_id" not in viktor     # CRM у владельца закрыта — кнопки нет
    done = [sql for sql, params in world.conn.executed if sql.startswith("update meetings set status = 'done'")]
    assert len(done) == 1 and world.conn.executed[-1][1] == ("whieda", 12)


async def test_one_failing_registration_does_not_block_the_others_and_holds_back_the_summary(world, caplog):
    world.fail_for = {USER}
    db = AfterDb([pending(7, "joined"), pending(8, "registered", user=USER + 1, name="Олег")])
    with caplog.at_level(logging.WARNING, logger="whieda.funnel.after_meeting"):
        processed, ctx = await run_meeting(world, db)
    assert processed == 1 and world.conn.rollbacks == 1
    assert db.registrations[8]["status"] == "no_show"
    assert world.outbox == [] and not any(sql.startswith("update meetings") for sql, _ in world.conn.executed)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "meeting_after_registration_failed" in text and "Анна" not in text and "79991112233" not in text and "boom" not in text


async def test_a_meeting_without_registrations_is_just_marked_done(world):
    processed, _ = await run_meeting(world, AfterDb([]))
    assert processed == 0 and world.outbox == [] and world.messages == []
    assert any(sql.startswith("update meetings set status = 'done'") for sql, _ in world.conn.executed)


async def test_a_meeting_that_is_already_done_and_has_nothing_left_is_left_alone(world):
    processed, _ = await run_meeting(world, AfterDb([]), status="done")
    assert processed == 0 and world.conn.executed == [] and world.outbox == []


async def test_a_meeting_marked_done_by_the_owner_still_sends_to_the_people_who_are_left(world):
    db = AfterDb([pending(7, "joined")])
    processed, ctx = await run_meeting(world, db, status="done")
    assert processed == 1 and len(world.messages) == 1 and len(world.outbox) == 1
    # перевод в done защищён в самом SQL: «done» не перетирается, строки не меняются
    assert all("and status = 'planned'" in sql for sql, _ in world.conn.executed if sql.startswith("update meetings"))


async def test_summary_is_skipped_for_an_owner_without_a_chat(world):
    world.chats.pop("igor-actor")
    db = AfterDb([pending(7, "joined")])
    _, ctx = await run_meeting(world, db)
    assert world.outbox == [] and ctx.counts["summaries"] == 0
    assert world.messages    # гость своё сообщение получил


async def test_a_duplicate_summary_key_is_not_counted(world):
    async def enqueue_outbox(conn, **kwargs):
        return {"created": False}

    db = AfterDb([pending(7, "joined")])
    with patch.object(after_meeting, "enqueue_outbox_event", enqueue_outbox):
        _, ctx = await run_meeting(world, db)
    assert ctx.counts["summaries"] == 0


# ---- шаг по встречам -------------------------------------------------------------------------------


async def test_the_step_picks_meetings_that_ended_30_minutes_to_2_and_a_half_hours_ago():
    sql = " ".join(after_meeting._MEETINGS_SQL.split())
    assert "kind = 'live' and status in ('planned', 'done')" in sql
    assert "starts_at + duration_min * interval '1 minute' + %(delay)s <= %(now)s" in sql
    assert "+ %(window)s > %(now)s" in sql
    assert after_meeting.AFTER_DELAY == timedelta(minutes=30) and after_meeting.AFTER_WINDOW == timedelta(hours=2)


async def test_process_after_meetings_runs_each_meeting_and_survives_a_failing_one(caplog):
    conn = FakeConn()
    seen: list[int] = []

    async def process(c, ctx):
        seen.append(int(ctx.meeting["meeting_id"]))
        if ctx.meeting["meeting_id"] == 1:
            raise RuntimeError("password=secret")
        ctx.counts["attended"] = 1
        return 2

    fetch = AsyncMock(return_value=[meeting(meeting_id=1), meeting(meeting_id=2)])
    with ExitStack() as stack:
        stack.enter_context(patch.object(after_meeting, "tenant_connection", fake_tenant_connection(conn)))
        stack.enter_context(patch.object(after_meeting, "fetch_all", fetch))
        stack.enter_context(patch.object(after_meeting, "process_meeting", process))
        caplog.set_level(logging.INFO, logger="whieda.funnel.after_meeting")
        assert await after_meeting.process_after_meetings("whieda", "bot", now=AFTER_NOW) == 2
    assert seen == [1, 2] and conn.rollbacks == 1
    assert fetch.await_args.args[2]["now"] == AFTER_NOW
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "meeting_after_failed" in text and "secret" not in text


async def test_a_failing_selection_is_logged_and_the_step_stays_alive(caplog):
    @asynccontextmanager
    async def broken(tenant_id):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch.object(after_meeting, "tenant_connection", broken), caplog.at_level(logging.WARNING, logger="whieda.funnel.after_meeting"):
        assert await after_meeting.process_after_meetings("whieda", "bot", now=AFTER_NOW) == 0
    assert any(r.getMessage() == "meeting_after_plan_failed" for r in caplog.records)


async def test_the_crm_button_exists_only_when_the_owners_crm_is_open():
    viewer = SimpleNamespace(ref_code="igor", public_profile={})
    with patch.object(after_meeting, "load_viewer", AsyncMock(return_value=viewer)), patch.object(
        after_meeting, "lock_reason", lambda v: None
    ), patch.object(after_meeting, "crm_url", lambda v: "https://igor.wwc.best/crm/"):
        assert await after_meeting.crm_button_url(object(), "whieda", 7001) == "https://igor.wwc.best/crm/"
    with patch.object(after_meeting, "load_viewer", AsyncMock(return_value=viewer)), patch.object(
        after_meeting, "lock_reason", lambda v: "no_access"
    ):
        assert await after_meeting.crm_button_url(object(), "whieda", 7001) is None
    assert await after_meeting.crm_button_url(object(), "whieda", None) is None


# ---- воркер: фото визитки одним сообщением ---------------------------------------------------------


def worker_binding():
    return SimpleNamespace(bot_token="whieda-test-token", binding_id="b", tenant=SimpleNamespace(tenant_id="whieda"))


CARD_PAYLOAD = {
    "chat_id": "4242", "text": "Спасибо!\nВаш партнёр WWC — Игорь.", "binding_id": "b", "card_photo": "https://wwc.best/media/igor.jpg",
    "reply_markup": {"inline_keyboard": [[{"text": "Сайт", "url": "https://igor.wwc.best/"}]]},
}


@pytest.fixture
def telegram(monkeypatch):
    from app.jobs import worker

    state = SimpleNamespace(
        media=AsyncMock(return_value={"ok": True, "message_id": 1}),
        text=AsyncMock(return_value={"ok": True}),
        photo=AsyncMock(return_value={"ok": True}),
        worker=worker,
    )
    monkeypatch.setattr(worker, "send_telegram_media", state.media)
    monkeypatch.setattr(worker, "send_telegram_text", state.text)
    monkeypatch.setattr(worker, "send_telegram_photo", state.photo)
    return state


async def test_a_card_with_a_photo_goes_as_one_message_with_caption_and_buttons(telegram):
    await telegram.worker._send_due_notification(worker_binding(), dict(CARD_PAYLOAD))
    telegram.media.assert_awaited_once_with(
        "4242", "photo", "https://wwc.best/media/igor.jpg", bot_token="whieda-test-token", caption=CARD_PAYLOAD["text"],
        reply_markup=CARD_PAYLOAD["reply_markup"],
    )
    telegram.text.assert_not_awaited()


async def test_when_telegram_does_not_take_the_photo_the_text_still_goes_out(telegram, caplog):
    telegram.media.side_effect = TelegramDeliveryError("telegram_media_failed:400")
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        await telegram.worker._send_due_notification(worker_binding(), dict(CARD_PAYLOAD))
    telegram.text.assert_awaited_once()
    assert telegram.text.await_args.kwargs["text"] == CARD_PAYLOAD["text"]
    assert telegram.text.await_args.kwargs["reply_markup"] == CARD_PAYLOAD["reply_markup"]
    assert any(r.getMessage() == "due_notification_card_photo_failed" for r in caplog.records)


async def test_a_photo_that_is_rejected_without_an_exception_also_falls_back_to_text(telegram):
    telegram.media.return_value = {"ok": False, "status_code": 400}
    await telegram.worker._send_due_notification(worker_binding(), dict(CARD_PAYLOAD))
    telegram.text.assert_awaited_once()


async def test_an_ambiguous_photo_send_is_not_repeated_as_text(telegram):
    telegram.media.side_effect = TelegramDeliveryUnknown("telegram_send_ambiguous")
    with pytest.raises(TelegramDeliveryUnknown):
        await telegram.worker._send_due_notification(worker_binding(), dict(CARD_PAYLOAD))
    telegram.text.assert_not_awaited()


async def test_a_caption_that_is_too_long_goes_as_plain_text(telegram):
    await telegram.worker._send_due_notification(worker_binding(), {**CARD_PAYLOAD, "text": "а" * 1100})
    telegram.media.assert_not_awaited()
    telegram.text.assert_awaited_once()


async def test_a_message_without_card_photo_goes_the_old_way(telegram):
    payload = {key: value for key, value in CARD_PAYLOAD.items() if key != "card_photo"}
    await telegram.worker._send_due_notification(worker_binding(), payload)
    telegram.media.assert_not_awaited()
    telegram.text.assert_awaited_once()


# ---- Max: картинка по адресу -----------------------------------------------------------------------

IMAGE = "https://wwc.best/media/igor.jpg"


def max_payload(**changes: Any) -> dict[str, Any]:
    base = max_outbox.build_max_outbox_payload(
        binding_id="b", user_id=USER, text="Визитка", buttons=[[{"type": "link", "text": "Сайт", "url": "https://wwc.best"}]],
        image_url=IMAGE,
    )
    base.update(changes)
    return base


def test_the_max_payload_validates_and_keeps_the_image_url():
    assert max_payload()["image_url"] == IMAGE
    for bad in ("http://wwc.best/a.jpg", "wwc.best/a.jpg", "https://wwc.best/a b.jpg"):
        with pytest.raises(ValueError):
            max_outbox.build_max_outbox_payload(binding_id="b", user_id=USER, text="x", image_url=bad)
    assert "image_url" not in max_outbox.build_max_outbox_payload(binding_id="b", user_id=USER, text="x")


async def test_max_sends_the_image_by_url_together_with_the_text_and_buttons():
    send = AsyncMock(return_value={"ok": True})
    with patch.object(max_outbox, "send_max_message", send):
        await max_outbox.send_max_outbox_payload(max_payload())
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["attachments"] == [{"type": "image", "payload": {"url": IMAGE}}]
    assert kwargs["text"] == "Визитка" and kwargs["user_id"] == str(USER) and kwargs["buttons"][0][0]["type"] == "link"


async def test_when_max_does_not_take_the_image_the_text_and_buttons_go_without_it(caplog):
    send = AsyncMock(side_effect=[{"ok": False, "status": 400}, {"ok": True}])
    with patch.object(max_outbox, "send_max_message", send), caplog.at_level(logging.WARNING, logger="app.max.outbox"):
        await max_outbox.send_max_outbox_payload(max_payload())
    assert send.await_count == 2
    assert send.await_args_list[0].kwargs["attachments"] == [{"type": "image", "payload": {"url": IMAGE}}]
    assert send.await_args_list[1].kwargs["attachments"] is None and send.await_args_list[1].kwargs["buttons"]
    assert any(r.getMessage() == "max_image_url_rejected" for r in caplog.records)


async def test_when_max_rejects_both_the_delivery_fails_as_before():
    send = AsyncMock(return_value={"ok": False, "status": 400})
    with patch.object(max_outbox, "send_max_message", send), pytest.raises(TelegramDeliveryError, match="max_delivery_rejected:400"):
        await max_outbox.send_max_outbox_payload(max_payload())
    assert send.await_count == 2


async def test_an_ambiguous_max_send_is_not_repeated():
    send = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    with patch.object(max_outbox, "send_max_message", send), pytest.raises(TelegramDeliveryUnknown):
        await max_outbox.send_max_outbox_payload(max_payload())
    send.assert_awaited_once()


async def test_a_message_without_an_image_is_sent_once_as_before_and_a_bad_stored_url_is_dropped():
    send = AsyncMock(return_value={"ok": True})
    with patch.object(max_outbox, "send_max_message", send):
        await max_outbox.send_max_outbox_payload(max_payload(image_url=None))
        await max_outbox.send_max_outbox_payload(max_payload(image_url="http://insecure.example/a.jpg"))
    assert send.await_count == 2
    assert all(call.kwargs["attachments"] is None for call in send.await_args_list)
