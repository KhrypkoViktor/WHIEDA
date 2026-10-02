"""WWC CRM morning message: names by group, buttons, the 09:00 window in the
account timezone, one row per day, access, no personal data in the logs."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.crm.messages import DIGEST_MAX_LINES, digest_message
from app.crm.rules import digest_idempotency_key, in_digest_window, today_sections
from app.jobs.worker import _final_delivery_error
from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown

TODAY = date(2026, 9, 25)
TODAY_URL = "https://igor.wwc.best/crm/#today"


def _card(cid: str, name: str, step: str | None = "invite", next_at: date | None = TODAY, **extra) -> dict:
    return {"contact_id": cid, "name": name, "next_step": step, "next_at": next_at, "meeting_at": None,
            "meeting_today": False, "meeting_local": None, **extra}


def _meeting(cid: str, name: str, hh: int, mm: int = 0) -> dict:
    return _card(cid, name, step="result", next_at=TODAY, meeting_today=True,
                 meeting_at=datetime(2026, 9, 25, hh - 3, mm, tzinfo=timezone.utc), meeting_local=f"{hh:02d}:{mm:02d}")


def _contact_url(contact_id: str) -> str:
    return f"https://igor.wwc.best/crm/?contact={contact_id}#contact/{contact_id}"


def _message(rows: list[dict]) -> dict | None:
    sections = today_sections(rows, TODAY, split_overdue=False)
    return digest_message(sections, TODAY, today_url=TODAY_URL, contact_url=_contact_url)


def test_names_by_group_meetings_first_with_time():
    message = _message([
        _card("c1", "Вера", step="ping"),
        _card("c2", "Борис", step="result", next_at=date(2026, 9, 23)),
        _meeting("c3", "Анна Петрова", 18, 30),
        _meeting("c4", "Глеб", 14),
        _card("c5", "Дмитрий", step="invite"),
    ])
    assert message["text"] == (
        "Доброе утро! Сегодня в WWC CRM:\n\n"
        "📅 Встречи сегодня\n• 14:00 Глеб\n• 18:30 Анна Петрова\n\n"
        "📞 Позвонить\n• Борис — узнать результат (с 23.09)\n• Дмитрий — пригласить на встречу\n\n"
        "🔔 Напомнить\n• Вера"
    )
    keyboard = message["reply_markup"]["inline_keyboard"]
    assert keyboard[0] == [{"text": "📋 Открыть «Сегодня»", "url": TODAY_URL}]
    # The first three people of the message, in its order.
    assert [row[0]["text"] for row in keyboard[1:]] == ["👤 Глеб", "👤 Анна Петрова", "👤 Борис"]
    assert keyboard[1][0]["url"] == _contact_url("c4")


def test_at_most_ten_lines_and_the_rest_is_counted():
    rows = [_card(f"c{i}", f"Человек {i}") for i in range(14)] + [_card("r1", "Напомнить 1", step="ping")]
    message = _message(rows)
    lines = [line for line in message["text"].splitlines() if line.startswith("• ")]
    assert len(lines) == DIGEST_MAX_LINES
    assert "🔔 Напомнить" not in message["text"]
    assert message["text"].endswith("…и ещё 5 — в приложении.")
    assert len(message["reply_markup"]["inline_keyboard"]) == 4  # «Сегодня» + 3 people


def test_nothing_today_means_no_message():
    assert _message([]) is None
    assert _message([_card("c1", "Завтра", next_at=date(2026, 9, 26))]) is None


def test_names_cannot_break_the_html_message():
    message = _message([_card("c1", "<b>Анна</b> <a href=x>", step="ping")])
    assert "<" not in message["text"] and ">" not in message["text"]
    assert "‹b›Анна‹/b›" in message["text"]
    long_name = "Очень длинное имя " * 10
    button = _message([_card("c1", long_name)])["reply_markup"]["inline_keyboard"][1][0]["text"]
    assert len(button) <= 42 and button.endswith("…")


@pytest.mark.parametrize(
    "utc, tz, expected",
    [
        (datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc), "Europe/Moscow", True),      # 09:00
        (datetime(2026, 9, 25, 6, 9, 59, tzinfo=timezone.utc), "Europe/Moscow", True),  # 09:09:59
        (datetime(2026, 9, 25, 6, 10, tzinfo=timezone.utc), "Europe/Moscow", False),    # 09:10
        (datetime(2026, 9, 25, 5, 59, tzinfo=timezone.utc), "Europe/Moscow", False),    # 08:59
        (datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc), "Asia/Yekaterinburg", False),  # 11:00
        (datetime(2026, 9, 25, 4, 5, tzinfo=timezone.utc), "Asia/Yekaterinburg", True),   # 09:05
        (datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc), "Europe/Minsk", True),         # 09:00
    ],
)
def test_window_is_nine_local(utc, tz, expected):
    assert in_digest_window(utc.astimezone(ZoneInfo(tz)).time()) is expected


def test_idempotency_key_is_per_account_and_local_day():
    assert digest_idempotency_key("acc-1", date(2026, 9, 25)) == "crm_digest:acc-1:2026-09-25"
    assert digest_idempotency_key("acc-1", date(2026, 9, 25)) != digest_idempotency_key("acc-1", date(2026, 9, 26))
    assert digest_idempotency_key("acc-1", date(2026, 9, 25)) != digest_idempotency_key("acc-2", date(2026, 9, 25))


@asynccontextmanager
async def _fake_connection(_tenant_id):
    yield object()


PAID_UNTIL = datetime(2030, 1, 1, tzinfo=timezone.utc)
EXPIRED = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _row(account_id: str, local: datetime, user_id: int, due: int, *, paid_until=PAID_UNTIL, ref="igor") -> dict:
    return {
        "account_id": account_id,
        "telegram_user_id": user_id,
        "local_now": local,
        "chat_id": str(user_id),
        "due_count": due,
        "ref_code": ref,
        "public_profile": {"subdomain": ref} if ref else None,
        "paid_until": paid_until,
    }


@pytest.fixture
def open_to_all_pro(monkeypatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_only_the_window_and_people_with_access_are_chosen(open_to_all_pro):
    from app.crm.digest import select_digest_accounts

    rows = [
        _row("in-window", datetime(2026, 9, 25, 9, 3), 101, 2),
        _row("too-late", datetime(2026, 9, 25, 11, 0), 102, 1),
        _row("pro-ended", datetime(2026, 9, 25, 9, 2), 104, 1, paid_until=EXPIRED, ref="petr"),
        _row("no-partner", datetime(2026, 9, 25, 9, 2), 105, 1, paid_until=None, ref=None),
        _row("zero", datetime(2026, 9, 25, 9, 1), 106, 0),
    ]
    assert [row["account_id"] for row in select_digest_accounts(rows)] == ["in-window"]


def test_plan_builds_text_buttons_and_sign_in_target(open_to_all_pro):
    from app.crm.digest import plan_digests, select_digest_accounts

    accounts = select_digest_accounts([_row("acc", datetime(2026, 9, 25, 9, 3), 101, 2)])
    contacts = [
        {**_card("11111111-1111-4111-8111-111111111111", "Анна"), "account_id": "acc"},
        {**_card("22222222-2222-4222-8222-222222222222", "Чужая"), "account_id": "other"},
    ]
    planned = plan_digests(accounts, contacts, "whieda-advisor-bot")
    assert [item["idempotency_key"] for item in planned] == ["crm_digest:acc:2026-09-25"]
    payload = planned[0]["payload"]
    assert payload["chat_id"] == "101" and payload["binding_id"] == "whieda-advisor-bot"
    assert payload["site_login_user_id"] == 101
    assert payload["url"] == "https://igor.wwc.best/crm/#today"
    assert "Анна" in payload["text"] and "Чужая" not in payload["text"]
    card_button = payload["reply_markup"]["inline_keyboard"][1][0]
    assert card_button["url"] == (
        "https://igor.wwc.best/crm/?contact=11111111-1111-4111-8111-111111111111"
        "#contact/11111111-1111-4111-8111-111111111111"
    )
    # An account whose people all moved away since the first read gets nothing.
    assert plan_digests(accounts, [], "whieda-advisor-bot") == []


def test_pilot_list_limits_the_morning_message(monkeypatch, open_to_all_pro):
    from app.crm.digest import select_digest_accounts
    from app.settings import get_settings

    rows = [_row("a", datetime(2026, 9, 25, 9, 3), 101, 1), _row("b", datetime(2026, 9, 25, 9, 3), 102, 1)]
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "102")
    get_settings.cache_clear()
    assert [row["chat_id"] for row in select_digest_accounts(rows)] == ["102"]
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "")
    get_settings.cache_clear()
    assert select_digest_accounts(rows) == []  # empty pilot = nobody


@pytest.mark.asyncio
async def test_names_are_read_only_for_chosen_accounts_and_second_pass_adds_nothing(open_to_all_pro):
    from app.crm import digest

    rows = [_row("acc", datetime(2026, 9, 25, 9, 6), 101, 1), _row("late", datetime(2026, 9, 25, 12, 0), 102, 3)]
    contacts = [{**_card("11111111-1111-4111-8111-111111111111", "Анна"), "account_id": "acc"}]
    names = AsyncMock(return_value=contacts)
    connections = []

    @asynccontextmanager
    async def counting_connection(tenant_id):
        connections.append(tenant_id)
        yield object()

    created = AsyncMock(return_value={"outbox_id": 1, "status": "scheduled", "created": True})
    with (
        patch.object(digest, "_accounts_due", AsyncMock(return_value=rows)),
        patch.object(digest, "_digest_contacts", names),
        patch.object(digest, "tenant_connection", counting_connection),
        patch.object(digest, "enqueue_outbox_event", created),
    ):
        assert await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"}) == 1
    assert names.await_args.args == ("whieda", ["acc"])  # never the names of «late»
    assert connections == ["whieda"]
    assert created.await_args.kwargs["idempotency_key"] == "crm_digest:acc:2026-09-25"
    assert created.await_args.kwargs["due_at"] is not None

    again = AsyncMock(return_value={"outbox_id": 1, "status": "scheduled", "created": False})
    with (
        patch.object(digest, "_accounts_due", AsyncMock(return_value=rows)),
        patch.object(digest, "_digest_contacts", AsyncMock(return_value=contacts)),
        patch.object(digest, "tenant_connection", _fake_connection),
        patch.object(digest, "enqueue_outbox_event", again),
    ):
        assert await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"}) == 0

    quiet = AsyncMock()
    with patch.object(digest, "_accounts_due", AsyncMock(return_value=rows)), patch.object(digest, "_digest_contacts", quiet):
        assert await digest.enqueue_crm_digests({"whieda": "b"}, in_window=lambda _t: False) == 0
    quiet.assert_not_awaited()  # nobody in the window — no names are read at all


@pytest.mark.asyncio
async def test_a_failing_tenant_logs_no_row_data(open_to_all_pro, caplog):
    from app.crm import digest

    class Boom(Exception):
        sqlstate = "23514"

        def __str__(self):
            return "Failing row contains (Анна, +79286729288)"

    with patch.object(digest, "_accounts_due", AsyncMock(side_effect=Boom())):
        assert await digest.enqueue_crm_digests({"whieda": "b"}) == 0
    assert "Анна" not in caplog.text and "+7928" not in caplog.text
    record = next(r for r in caplog.records if r.message == "crm_digest_plan_failed")
    assert (record.error_class, record.sqlstate, record.exc_info) == ("Boom", "23514", None)


def test_window_bounds_are_constants():
    assert in_digest_window(time(9, 0))
    assert not in_digest_window(time(9, 10))


def test_ambiguous_or_rejected_delivery_is_final():
    assert _final_delivery_error(TelegramDeliveryUnknown("timeout"))
    assert _final_delivery_error(TelegramDeliveryError("telegram_send_failed:403"))
    assert _final_delivery_error(TelegramDeliveryError("telegram_send_failed:400"))
    assert not _final_delivery_error(TelegramDeliveryError("telegram_send_failed:502"))
    assert not _final_delivery_error(RuntimeError("connection reset"))


@pytest.mark.asyncio
async def test_worker_step_is_off_without_notify_bindings(monkeypatch):
    from app.jobs import worker
    from app.settings import get_settings

    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)
    get_settings.cache_clear()
    try:
        with patch.object(worker, "resolve_bot_binding_context", AsyncMock()) as resolve:
            assert await worker.scheduled_notifications_step(plan_crm=True) == {}
        resolve.assert_not_awaited()
    finally:
        get_settings.cache_clear()
