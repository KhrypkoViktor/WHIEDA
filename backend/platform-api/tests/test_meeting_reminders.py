"""Срез 5 бот-воронки, часть 1: напоминания о Живой встрече и кнопка «Присоединиться» (31.10.2026).

Без базы и сети: база подменена фейками на границах модулей, Telegram и Max — моками. Настоящий SQL, отметки в jsonb,
сквозной сценарий запись → напоминания → join → после встречи — в tests/test_meeting_reminders_postgres.py.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.funnel import guest_bot, outbound, registration, reminders, texts
from app.funnel.guest import Reply, is_link, rows_to_max, rows_to_telegram
from app.funnel.guest_bot import parse_guest_callback
from app.max import processor as max_processor
from app.meetings import registrations
from app.meetings.registrations import JOIN_OPENS_BEFORE, Registration
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramCallbackQuery
from app.tenancy import TenantContext
from tests.test_lead_start_guest import max_callback

UTC = timezone.utc
# 05.11.2026 16:30 UTC = чт 05.11 19:30 МСК
START = datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
USER = 4242_0007
ZOOM = "https://us02web.zoom.us/j/81234567890?pwd=SeCrEt"
BOT_TOKEN = "whieda-test-token"


def at(offset: timedelta) -> datetime:
    """Момент за ``offset`` до начала встречи."""
    return START - offset


# ---- окна и ключи ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=25), []),
        (timedelta(hours=24), ["day"]),
        (timedelta(hours=23, minutes=16), ["day"]),
        (timedelta(hours=23, minutes=15), []),  # окно «за сутки» — 45 минут
        (timedelta(hours=5), []),
        (timedelta(hours=2), ["two_hours"]),
        (timedelta(hours=1, minutes=31), ["two_hours"]),
        (timedelta(hours=1, minutes=30), []),  # окно «за 2 часа» — 30 минут
        (timedelta(hours=1, minutes=10), []),
        (timedelta(hours=1), ["hour"]),
        (timedelta(minutes=41), ["hour"]),
        (timedelta(minutes=40), []),  # окно «за час» — 20 минут
        (timedelta(minutes=20), []),
        (timedelta(minutes=5), ["five"]),
        (timedelta(minutes=1), ["five"]),
        (timedelta(seconds=1), ["five"]),
        (timedelta(0), []),  # «через 5 минут» после начала не шлём
        (-timedelta(minutes=3), []),
    ],
)
def test_the_four_reminders_fire_inside_their_own_windows(offset, expected):
    assert reminders.due_kinds(START, at(offset)) == expected


def test_the_windows_never_overlap():
    seen = set()
    for minute in range(0, 24 * 60 + 60):
        due = reminders.due_kinds(START, START - timedelta(minutes=minute))
        assert len(due) <= 1, minute
        seen.update(due)
    assert seen == {"day", "two_hours", "hour", "five"}


def test_a_reminder_already_sent_is_not_due_again():
    now = at(timedelta(hours=1))
    assert reminders.due_kinds(START, now) == ["hour"]
    assert reminders.due_kinds(START, now, sent={"hour": "2026-11-05T15:30:00+00:00"}) == []
    assert reminders.due_kinds(START, now, sent={"day": "x"}) == ["hour"]  # другой вид не мешает


def test_a_person_who_registered_after_the_day_or_two_hour_moment_is_not_reminded_of_them():
    # записался за 23 ч 50 мин: «за сутки» ему уже не нужно — он только что получил подтверждение
    late = at(timedelta(hours=23, minutes=50))
    assert reminders.due_kinds(START, late, registered_at=late) == []
    assert reminders.due_kinds(START, late, registered_at=at(timedelta(hours=30))) == ["day"]
    two = at(timedelta(hours=1, minutes=45))
    assert reminders.due_kinds(START, two, registered_at=two) == []
    # а «за час» и «за 5 минут» (с кнопкой входа) идут всегда
    hour = at(timedelta(minutes=55))
    assert reminders.due_kinds(START, hour, registered_at=hour) == ["hour"]
    five = at(timedelta(minutes=3))
    assert reminders.due_kinds(START, five, registered_at=five) == ["five"]


def test_the_outbox_key_is_registration_and_kind():
    assert reminders.reminder_key(7, "hour") == "mrem:7:hour"
    assert reminders.reminder_key("12", "five") == "mrem:12:five"
    assert set(reminders.KINDS) == {"day", "two_hours", "hour", "five"}


def test_the_candidates_are_only_live_planned_meetings_within_a_day():
    sql = " ".join(reminders._CANDIDATES_SQL.split())
    assert "r.status = 'registered'" in sql and "m.kind = 'live'" in sql and "m.status = 'planned'" in sql
    assert "m.starts_at > %(now)s" in sql and "m.starts_at <= %(now)s + %(horizon)s" in sql
    assert reminders.HORIZON == timedelta(hours=24)


# ---- тексты и кнопки -------------------------------------------------------------------------------


def test_the_texts_give_the_moscow_date_and_time():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert "Завтра, чт 05.11 19:30 МСК" in reminders.reminder_text("day", START, now=now)
    assert "в 19:30 по Москве" in reminders.reminder_text("two_hours", START, now=now)
    assert "19:30 по Москве" in reminders.reminder_text("hour", START, now=now)
    assert "«Присоединиться»" in reminders.reminder_text("hour", START, now=now)
    assert "через 5 минут" in reminders.reminder_text("five", START, now=now)
    with pytest.raises(ValueError):
        reminders.reminder_text("later", START)


def test_year_is_shown_in_the_day_text_only_when_it_is_not_the_current_one():
    january = datetime(2027, 1, 12, 16, 30, tzinfo=UTC)
    assert "вт 12.01.2027 19:30 МСК" in reminders.reminder_text("day", january, now=datetime(2026, 12, 31, 9, 0, tzinfo=UTC))


def test_only_hour_and_five_carry_the_join_button():
    assert reminders.reminder_rows("day", 7) == [] and reminders.reminder_rows("two_hours", 7) == []
    for kind in ("hour", "five"):
        assert reminders.reminder_rows(kind, 7) == [[("Присоединиться", "mjoin:7")]]


def test_every_reminder_text_is_marked_as_a_placeholder_in_the_texts_module():
    source = open(texts.__file__, encoding="utf-8").read()
    block = source.split("# --- срез 5", 1)[1]
    assert block.count("# текст: воронка") >= 5 and "# текст: партнёру" in block


# ---- постановка в очередь: Telegram и Max ----------------------------------------------------------


NOW = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)  # за час до начала


async def test_a_telegram_message_goes_to_platform_outbox_with_the_join_callback():
    outbox = AsyncMock(return_value={"created": True})
    with patch.object(outbound, "enqueue_outbox_event", outbox):
        row = await outbound.enqueue_guest_message(
            object(), tenant_id="whieda", binding_id="whieda-advisor-bot", channel="telegram", user_id=USER,
            idempotency_key="mrem:7:hour", event_type="meeting_reminder", text="Через час", rows=[[("Присоединиться", "mjoin:7")]],
            due_at=NOW,
        )
    assert row == {"created": True}
    kwargs = outbox.await_args.kwargs
    assert kwargs["tenant_id"] == "whieda" and kwargs["idempotency_key"] == "mrem:7:hour" and kwargs["due_at"] == NOW
    assert kwargs["event_type"] == "meeting_reminder"
    assert kwargs["payload"] == {
        "chat_id": str(USER), "text": "Через час", "binding_id": "whieda-advisor-bot",
        "reply_markup": {"inline_keyboard": [[{"text": "Присоединиться", "callback_data": "mjoin:7"}]]},
    }


async def test_a_max_message_goes_through_enqueue_max_notification_with_a_callback_button():
    outbox = AsyncMock(return_value={"created": True})
    with patch.object(outbound, "enqueue_max_notification", outbox):
        await outbound.enqueue_guest_message(
            "conn", tenant_id="whieda", binding_id="whieda-advisor-bot", channel="max", user_id=USER,
            idempotency_key="mrem:8:hour", event_type="meeting_reminder", text="Через час", rows=[[("Присоединиться", "mjoin:8")]],
            due_at=NOW,
        )
    args, kwargs = outbox.await_args
    assert args == ("conn", "whieda")
    assert kwargs == {
        "idempotency_key": "mrem:8:hour", "due_at": NOW, "binding_id": "whieda-advisor-bot", "user_id": USER,
        "text": "Через час", "buttons": [[{"type": "callback", "text": "Присоединиться", "payload": "mjoin:8"}]],
        "image_url": None, "event_type": "meeting_reminder",
    }


async def test_a_message_without_buttons_has_no_keyboard_and_a_card_photo_rides_along():
    outbox = AsyncMock(return_value={"created": True})
    with patch.object(outbound, "enqueue_outbox_event", outbox):
        await outbound.enqueue_guest_message(
            object(), tenant_id="whieda", binding_id="b", channel="telegram", user_id=USER, idempotency_key="k",
            event_type="meeting_after", text="Спасибо", photo_url="https://wwc.best/media/a.jpg", due_at=NOW,
        )
    assert outbox.await_args.kwargs["payload"] == {
        "chat_id": str(USER), "text": "Спасибо", "binding_id": "b", "card_photo": "https://wwc.best/media/a.jpg",
    }
    max_outbox = AsyncMock(return_value={"created": True})
    with patch.object(outbound, "enqueue_max_notification", max_outbox):
        await outbound.enqueue_guest_message(
            object(), tenant_id="whieda", binding_id="b", channel="max", user_id=USER, idempotency_key="k",
            event_type="meeting_after", text="Спасибо", rows=[[("Сайт", "https://wwc.best")]],
            photo_url="https://wwc.best/media/a.jpg", due_at=NOW,
        )
    kwargs = max_outbox.await_args.kwargs
    assert kwargs["buttons"] == [[{"type": "link", "text": "Сайт", "url": "https://wwc.best"}]]
    assert kwargs["image_url"] == "https://wwc.best/media/a.jpg"


async def test_an_unknown_channel_is_refused():
    with pytest.raises(ValueError):
        await outbound.enqueue_guest_message(
            object(), tenant_id="whieda", binding_id="b", channel="sms", user_id=1, idempotency_key="k",
            event_type="e", text="t", due_at=NOW,
        )


def test_link_buttons_become_url_buttons_in_both_messengers():
    rows = [[("Открыть", "https://wwc.best/x"), ("Нажать", "mjoin:7")]]
    assert is_link("https://wwc.best") and is_link("HTTP://a.b") and not is_link("mjoin:7") and not is_link("funnel:tour")
    assert rows_to_telegram(rows) == {
        "inline_keyboard": [[{"text": "Открыть", "url": "https://wwc.best/x"}, {"text": "Нажать", "callback_data": "mjoin:7"}]]
    }
    assert rows_to_max(rows) == [
        [{"type": "link", "text": "Открыть", "url": "https://wwc.best/x"}, {"type": "callback", "text": "Нажать", "payload": "mjoin:7"}]
    ]
    assert rows_to_telegram([]) is None and rows_to_max([]) is None


# ---- remind_one / enqueue_reminders ----------------------------------------------------------------


def row(registration_id: int = 7, channel: str = "telegram", user: int = USER, **extra: Any) -> dict[str, Any]:
    return {
        "registration_id": registration_id, "channel": channel, "channel_user_id": user,
        "registered_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "reminders_sent": {}, "meeting_id": 12,
        "starts_at": START, **extra,
    }


async def test_remind_one_marks_the_registration_and_queues_the_message_with_the_key():
    claim = AsyncMock(return_value={"registration_id": 7})
    enqueue = AsyncMock(return_value={"created": True})
    with patch.object(reminders, "fetch_one", claim), patch.object(reminders, "enqueue_guest_message", enqueue):
        outcome = await reminders.remind_one(
            object(), tenant_id="whieda", binding_id="bot", row=row(), kind="hour", now=NOW
        )
    assert outcome == "queued"
    claim_args = claim.await_args.args
    assert claim_args[2] == ("hour", NOW.isoformat(), "whieda", 7, "hour")
    assert "status = 'registered'" in " ".join(claim_args[1].split()) and "is null" in claim_args[1]
    kwargs = enqueue.await_args.kwargs
    assert kwargs["idempotency_key"] == "mrem:7:hour" and kwargs["channel"] == "telegram" and kwargs["user_id"] == USER
    assert kwargs["rows"] == [[("Присоединиться", "mjoin:7")]] and kwargs["due_at"] == NOW
    assert kwargs["event_type"] == "meeting_reminder" and "19:30" in kwargs["text"]


async def test_remind_one_does_nothing_when_the_mark_is_already_there():
    enqueue = AsyncMock()
    with patch.object(reminders, "fetch_one", AsyncMock(return_value=None)), patch.object(reminders, "enqueue_guest_message", enqueue):
        outcome = await reminders.remind_one(object(), tenant_id="whieda", binding_id="bot", row=row(), kind="hour", now=NOW)
    assert outcome == "raced"
    enqueue.assert_not_awaited()


async def test_remind_one_reports_a_duplicate_key():
    with patch.object(reminders, "fetch_one", AsyncMock(return_value={"registration_id": 7})), patch.object(
        reminders, "enqueue_guest_message", AsyncMock(return_value={"created": False})
    ):
        assert await reminders.remind_one(object(), tenant_id="whieda", binding_id="bot", row=row(), kind="five", now=NOW) == "duplicate"


class FakeConn:
    """Подключение с savepoint: откат считается, когда из ``transaction()`` вылетает исключение."""

    def __init__(self) -> None:
        self.rollbacks = 0
        self.commits = 0
        self.executed: list[tuple[str, Any]] = []

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        except BaseException:
            self.rollbacks += 1
            raise
        self.commits += 1

    def cursor(self):
        conn = self

        class _Cursor:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            async def execute(self_inner, sql, params=()):
                conn.executed.append((" ".join(str(sql).split()), params))

        return _Cursor()


def fake_tenant_connection(conn: FakeConn):
    @asynccontextmanager
    async def _connection(tenant_id: str):
        yield conn

    return _connection


async def test_enqueue_reminders_queues_telegram_and_max_and_survives_one_failure(caplog):
    conn = FakeConn()
    candidates = [row(7, "telegram", USER), row(8, "max", USER + 1), row(9, "telegram", USER + 2)]
    calls: list[tuple[int, str]] = []

    async def enqueue(c, **kwargs):
        calls.append((int(kwargs["user_id"]), kwargs["channel"]))
        if kwargs["user_id"] == USER + 1:
            raise RuntimeError("password=secret Анна +79991112233")
        return {"created": True}

    with ExitStack() as stack:
        stack.enter_context(patch.object(reminders, "tenant_connection", fake_tenant_connection(conn)))
        stack.enter_context(patch.object(reminders, "fetch_all", AsyncMock(return_value=candidates)))
        stack.enter_context(patch.object(reminders, "fetch_one", AsyncMock(return_value={"registration_id": 1})))
        stack.enter_context(patch.object(reminders, "enqueue_guest_message", AsyncMock(side_effect=enqueue)))
        caplog.set_level(logging.INFO, logger="whieda.funnel.reminders")
        queued = await reminders.enqueue_reminders("whieda", "bot", now=NOW)
    assert queued == 2 and conn.rollbacks == 1
    assert calls == [(USER, "telegram"), (USER + 1, "max"), (USER + 2, "telegram")]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "meeting_reminder_failed" in text and "secret" not in text and "Анна" not in text and "79991112233" not in text


async def test_enqueue_reminders_skips_what_is_not_due_and_what_was_sent():
    conn = FakeConn()
    candidates = [
        row(7, reminders_sent={"hour": "x"}),                       # уже отправлено
        row(8, starts_at=START + timedelta(hours=3)),               # встреча далеко
        row(9, registered_at=NOW),                                  # записался только что — но «за час» всё равно идёт
    ]
    enqueue = AsyncMock(return_value={"created": True})
    with ExitStack() as stack:
        stack.enter_context(patch.object(reminders, "tenant_connection", fake_tenant_connection(conn)))
        stack.enter_context(patch.object(reminders, "fetch_all", AsyncMock(return_value=candidates)))
        stack.enter_context(patch.object(reminders, "fetch_one", AsyncMock(return_value={"registration_id": 1})))
        stack.enter_context(patch.object(reminders, "enqueue_guest_message", enqueue))
        assert await reminders.enqueue_reminders("whieda", "bot", now=NOW) == 1
    assert enqueue.await_args.kwargs["idempotency_key"] == "mrem:9:hour"


async def test_a_failing_selection_is_logged_and_the_step_stays_alive(caplog):
    @asynccontextmanager
    async def broken(tenant_id):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch.object(reminders, "tenant_connection", broken), caplog.at_level(logging.WARNING, logger="whieda.funnel.reminders"):
        assert await reminders.enqueue_reminders("whieda", "bot", now=NOW) == 0
    assert any(r.getMessage() == "meeting_reminders_plan_failed" for r in caplog.records)


# ---- шаг воркера -----------------------------------------------------------------------------------


def binding(tenant_id="whieda", binding_id="whieda-advisor-bot"):
    return SimpleNamespace(binding_id=binding_id, tenant=SimpleNamespace(tenant_id=tenant_id))


async def test_the_step_works_only_for_the_whieda_binding_and_runs_reminders_then_after_meeting():
    plan = AsyncMock(return_value=3)
    after = AsyncMock(return_value=2)
    with patch.object(reminders, "enqueue_reminders", plan), patch("app.funnel.after_meeting.process_after_meetings", after):
        assert await reminders.reminders_step({"whieda": binding()}, now=NOW) == {"meeting_reminders": 3, "meeting_after": 2}
        plan.assert_awaited_once_with("whieda", "whieda-advisor-bot", now=NOW)
        after.assert_awaited_once_with("whieda", "whieda-advisor-bot", now=NOW)
        plan.reset_mock()
        after.reset_mock()
        # процесс без бота whieda (стенд) или с чужим тенантом молчит и в базу не ходит
        assert await reminders.reminders_step({}) == {}
        assert await reminders.reminders_step({"other": binding("other", "other-bot")}) == {}
        plan.assert_not_awaited()
        after.assert_not_awaited()


@pytest.fixture
def worker_env(monkeypatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


async def test_the_worker_runs_the_reminders_step_on_the_minute_and_keeps_sending_when_it_fails(worker_env, caplog):
    from app.jobs import worker

    send = AsyncMock(return_value=1)
    step = AsyncMock(return_value={"meeting_reminders": 2, "meeting_after": 1})
    with ExitStack() as stack:
        stack.enter_context(patch.object(worker, "scheduled_notify_bindings", AsyncMock(return_value={"whieda": binding()})))
        stack.enter_context(patch.object(worker, "process_due_notifications", send))
        stack.enter_context(patch("app.funnel.no_bot_alert.no_bot_alert_step", AsyncMock(return_value=0)))
        stack.enter_context(patch("app.funnel.reminders.reminders_step", step))
        result = await worker.scheduled_notifications_step(plan_crm=False, plan_no_bot=True)
        assert result == {"no_bot_alerts": 0, "meeting_reminders": 2, "meeting_after": 1, "due_sent": 1}
        step.assert_awaited_once()
        step.reset_mock()
        assert await worker.scheduled_notifications_step(plan_crm=False) == {"due_sent": 1}  # не по расписанию
        step.assert_not_awaited()
        step.side_effect = RuntimeError("db down")
        with caplog.at_level(logging.ERROR, logger="app.jobs.worker"):
            assert await worker.scheduled_notifications_step(plan_crm=False, plan_no_bot=True) == {"no_bot_alerts": 0, "due_sent": 1}
    assert any(r.message == "meeting_reminders_step_failed" for r in caplog.records)


# ---- «Присоединиться»: база ------------------------------------------------------------------------


def reg(status: str = "registered", **changes: Any) -> Registration:
    base = dict(registration_id=7, meeting_id=12, lead_id=None, channel="telegram", channel_user_id=USER, status=status)
    base.update(changes)
    return Registration(**base)


def meeting_row(**changes: Any) -> dict[str, Any]:
    base = {
        "meeting_id": 12, "kind": "live", "title": "Живая встреча", "starts_at": START, "duration_min": 60,
        "status": "planned", "join_url": ZOOM,
    }
    base.update(changes)
    return base


async def run_join(status="registered", *, now: datetime, meeting: dict | None = None, found: bool = True):
    conn = FakeConn()
    lock = AsyncMock(return_value=reg(status) if found else None)
    fetch = AsyncMock(return_value=meeting_row() if meeting is None else meeting)
    with patch.object(registrations, "_lock_own_registration", lock), patch.object(registrations, "fetch_one", fetch):
        outcome = await registrations.join(
            conn, "whieda", registration_id=7, channel="telegram", channel_user_id=USER, now=now
        )
    return outcome, conn


async def test_the_first_press_inside_the_window_marks_the_registration_joined():
    outcome, conn = await run_join(now=at(timedelta(minutes=30)))
    assert outcome.status == "joined" and outcome.registration.status == "joined"
    assert outcome.registration.joined_at == at(timedelta(minutes=30)) and outcome.meeting["join_url"] == ZOOM
    sql, params = conn.executed[0]
    assert sql.startswith("update meeting_registrations set status = 'joined', joined_at = %s") and "status = 'registered'" in sql
    assert params == (at(timedelta(minutes=30)), "whieda", 7)


async def test_the_second_press_gives_the_same_link_and_changes_nothing():
    outcome, conn = await run_join("joined", now=at(timedelta(minutes=2)))
    assert outcome.status == "again" and outcome.meeting["join_url"] == ZOOM and conn.executed == []


async def test_the_window_opens_75_minutes_before_the_start_and_closes_at_the_end():
    assert JOIN_OPENS_BEFORE == timedelta(minutes=75)
    for offset in (timedelta(minutes=75), timedelta(0), -timedelta(minutes=59)):
        outcome, _ = await run_join(now=at(offset))
        assert outcome.status == "joined", offset
    early, conn = await run_join(now=at(timedelta(minutes=76)))
    assert early.status == "too_early" and conn.executed == []
    day_before, _ = await run_join(now=at(timedelta(hours=24)))
    assert day_before.status == "too_early"
    late, conn = await run_join(now=at(-timedelta(minutes=60)))  # ровно конец
    assert late.status == "too_late" and conn.executed == []
    much_later, _ = await run_join(now=at(-timedelta(hours=5)))
    assert much_later.status == "too_late"


async def test_a_press_for_a_cancelled_or_finished_meeting_changes_nothing():
    cancelled, conn = await run_join(now=at(timedelta(minutes=10)), meeting=meeting_row(status="cancelled"))
    assert cancelled.status == "cancelled" and conn.executed == []
    done, conn = await run_join(now=at(timedelta(minutes=10)), meeting=meeting_row(status="done"))
    assert done.status == "too_late" and conn.executed == []


@pytest.mark.parametrize("status", ["moved", "cancelled", "attended", "no_show"])
async def test_a_registration_that_is_not_active_any_more_is_stale(status):
    outcome, conn = await run_join(status, now=at(timedelta(minutes=10)))
    assert outcome.status == "stale" and conn.executed == []


async def test_a_foreign_or_unknown_registration_is_not_found():
    outcome, conn = await run_join(now=at(timedelta(minutes=10)), found=False)
    assert outcome.status == "not_found" and outcome.registration is None and conn.executed == []


async def test_a_registration_whose_meeting_is_gone_is_stale():
    conn = FakeConn()
    with patch.object(registrations, "_lock_own_registration", AsyncMock(return_value=reg())), patch.object(
        registrations, "fetch_one", AsyncMock(return_value=None)
    ):
        outcome = await registrations.join(conn, "whieda", registration_id=7, channel="telegram", channel_user_id=USER, now=NOW)
    assert outcome.status == "stale"


async def test_join_refuses_an_unknown_channel():
    with pytest.raises(ValueError):
        await registrations.join(FakeConn(), "whieda", registration_id=7, channel="sms", channel_user_id=USER)


# ---- «Присоединиться»: ответы ----------------------------------------------------------------------


@asynccontextmanager
async def fake_connection(tenant_id: str):
    yield SimpleNamespace(tenant=tenant_id)


def join_outcome(status: str, *, join_url: str | None = ZOOM, **changes) -> registrations.JoinOutcome:
    meeting = meeting_row(join_url=join_url)
    return registrations.JoinOutcome(status, reg("joined"), meeting, **changes)


async def run_action(outcome: registrations.JoinOutcome, now: datetime = NOW):
    with patch.object(registration, "tenant_connection", fake_connection), patch.object(
        registrations, "join", AsyncMock(return_value=outcome)
    ):
        return await registration.join("whieda", "telegram", USER, 7, now=now)


async def test_the_answer_to_a_press_is_the_link_button():
    for status in ("joined", "again"):
        result = await run_action(join_outcome(status))
        assert result.status == status and result.reply.text == texts.JOIN_OPEN
        assert result.reply.rows == [[("▶️ Открыть встречу", ZOOM)]]
        assert not result.clear_keyboard


async def test_without_a_link_the_guest_is_told_it_will_come_a_few_minutes_before():
    for url in (None, "", "   ", "not-a-link", "ftp://x.y/z"):
        result = await run_action(join_outcome("joined", join_url=url))
        assert result.status == "joined" and result.reply.text == "Ссылку пришлём за несколько минут до начала." and result.reply.rows == []


async def test_a_press_outside_the_window_gets_a_polite_answer():
    early = await run_action(join_outcome("too_early"), now=datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    assert early.status == "too_early" and "чт 05.11 19:30 МСК" in early.reply.text and early.reply.rows == []
    assert (await run_action(join_outcome("too_late"))).reply.text == texts.JOIN_TOO_LATE
    assert (await run_action(join_outcome("cancelled"))).reply.text == texts.JOIN_CANCELLED
    for status in ("stale", "not_found"):
        stale = await run_action(join_outcome(status))
        assert stale.reply.text == texts.REGISTRATION_STALE and not stale.clear_keyboard


async def test_the_join_url_never_reaches_the_log(caplog):
    with caplog.at_level(logging.INFO, logger="whieda.funnel.registration"):
        await run_action(join_outcome("joined"))
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "join" in text and "zoom.us" not in text and "SeCrEt" not in text


# ---- «Присоединиться»: кнопки Telegram и Max -------------------------------------------------------


def test_the_join_callback_is_parsed_and_registered_for_max():
    assert parse_guest_callback("mjoin:7") == guest_bot.GuestAction("join", registration_id=7)
    assert parse_guest_callback(" mjoin:7 ") == guest_bot.GuestAction("join", registration_id=7)
    for bad in ("mjoin:", "mjoin:x", "mjoin:7:8", "mjoin7", "mjoin:-1"):
        assert parse_guest_callback(bad) is None, bad
    assert "mjoin:" in guest_bot.GUEST_MAX_PREFIXES
    assert max_processor.resolve_max_callback("mjoin:7")[1] is guest_bot.max_guest_callback
    assert max_processor.resolve_max_callback("funnel:consult")[1] is not guest_bot.max_guest_callback  # «Личный разбор» не наш


def tg_cb(data: str, chat_type: str = "private") -> TelegramCallbackQuery:
    return TelegramCallbackQuery(chat_id=USER, user_id=USER, callback_query_id="cq-1", data=data, chat_type=chat_type, raw={})


@pytest.fixture
def tg(whieda_bot_binding):
    state = SimpleNamespace(text=AsyncMock(return_value={"ok": True}), answer=AsyncMock(return_value={"ok": True}))
    with ExitStack() as stack:
        stack.enter_context(patch.object(guest_bot, "send_telegram_text", state.text))
        stack.enter_context(patch.object(guest_bot, "answer_callback_query", state.answer))
        stack.enter_context(binding_context_scope(whieda_bot_binding))
        yield state


async def test_the_telegram_press_answers_with_a_url_button(tg):
    action = AsyncMock(return_value=registration.ActionResult(Reply(texts.JOIN_OPEN, [[("▶️ Открыть встречу", ZOOM)]]), "joined"))
    with patch.object(registration, "join", action):
        result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mjoin:7"), trace_id="t1")
    assert result == {"ok": True, "route": "guest_callback", "trace_id": "t1", "status": "joined"}
    action.assert_awaited_once_with("whieda", "telegram", USER, 7)
    tg.answer.assert_awaited_once_with(callback_query_id="cq-1", bot_token=BOT_TOKEN)
    sent = tg.text.await_args.kwargs
    assert sent["text"] == texts.JOIN_OPEN
    assert sent["reply_markup"] == {"inline_keyboard": [[{"text": "▶️ Открыть встречу", "url": ZOOM}]]}


async def test_the_telegram_join_in_a_group_is_refused_and_a_failure_is_answered(tg):
    result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mjoin:7", "group"), trace_id="t1")
    assert result["status"] == "private_chat_required"
    with patch.object(registration, "join", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await guest_bot.try_handle_guest_callback(TENANT, tg_cb("mjoin:7"), trace_id="t1")
    assert result["ok"] is False and result["status"] == "error"
    assert tg.text.await_args.kwargs["text"] == texts.GUEST_ERROR


async def test_the_max_press_answers_with_a_link_button(monkeypatch):
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(guest_bot, "send_max_text", send)
    action = AsyncMock(return_value=registration.ActionResult(Reply(texts.JOIN_OPEN, [[("▶️ Открыть встречу", ZOOM)]]), "again"))
    with patch.object(registration, "join", action):
        assert await guest_bot.max_guest_callback(TENANT, max_callback("mjoin:7"), "t1") is None
    action.assert_awaited_once_with("whieda", "max", USER, 7)
    kwargs = send.await_args.kwargs
    assert kwargs["text"] == texts.JOIN_OPEN and kwargs["user_id"] == USER
    assert kwargs["buttons"] == [[{"type": "link", "text": "▶️ Открыть встречу", "url": ZOOM}]]
