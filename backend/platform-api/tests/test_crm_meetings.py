"""«Через час встреча»: text, button, window, duplicates, access, sign-in links
made at sending. The SQL window and timezones run for real in
tests/test_crm_v2_postgres.py; here — the pure parts and the planner wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.crm.messages import meeting_reminder_message
from app.crm.rules import MEETING_REMIND_FROM, MEETING_REMIND_TO, meeting_idempotency_key

CARD = "https://igor.wwc.best/crm/?contact=c1#contact/c1"
MEETING = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
PAID_UNTIL = datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_text_has_name_phone_local_time_and_card_button():
    message = meeting_reminder_message(name="Анна  Петрова", phone="+79286729288", local_time="14:00", card_url=CARD)
    assert message["text"] == "⏰ Через час встреча: Анна Петрова, +79286729288\nНачало в 14:00 по вашему времени."
    assert message["reply_markup"] == {"inline_keyboard": [[{"text": "👤 Открыть карточку", "url": CARD}]]}


def test_text_without_phone_and_with_html_in_the_name():
    message = meeting_reminder_message(name="<b>Олег", phone=None, local_time="09:30", card_url=CARD)
    assert message["text"].startswith("⏰ Через час встреча: ‹b›Олег\n")
    assert "<" not in message["text"]


def test_one_key_per_card_and_meeting_time():
    key = meeting_idempotency_key("c1", MEETING)
    assert key == f"crm_meeting:c1:{int(MEETING.timestamp())}"
    assert key == meeting_idempotency_key("c1", MEETING.astimezone(timezone(timedelta(hours=3))))  # same instant
    assert key != meeting_idempotency_key("c1", MEETING + timedelta(minutes=30))  # moved meeting
    assert key != meeting_idempotency_key("c2", MEETING)


def test_window_is_about_an_hour_with_three_planner_passes():
    assert MEETING_REMIND_FROM == timedelta(minutes=50)
    assert MEETING_REMIND_TO == timedelta(minutes=65)
    from app.jobs.worker import CRM_DIGEST_INTERVAL_SEC

    passes = (MEETING_REMIND_TO - MEETING_REMIND_FROM) / timedelta(seconds=CRM_DIGEST_INTERVAL_SEC)
    assert passes >= 3  # a missed pass still leaves two chances


@pytest.fixture
def open_to_all_pro(monkeypatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _due(contact_id: str, user_id: int, *, paid_until=PAID_UNTIL, ref="igor") -> dict:
    return {
        "contact_id": contact_id, "account_id": "acc", "telegram_user_id": user_id, "name": "Анна",
        "phone_e164": "+79286729288", "phone_raw": "8 928 672-92-88", "meeting_at": MEETING,
        "meeting_local": "14:00", "chat_id": str(user_id), "ref_code": ref,
        "public_profile": {"subdomain": ref} if ref else None, "paid_until": paid_until,
    }


def test_plan_skips_people_without_access(open_to_all_pro):
    from app.crm.meetings import plan_meeting_reminders

    planned = plan_meeting_reminders(
        [_due("c1", 101), _due("c2", 102, paid_until=datetime(2020, 1, 1, tzinfo=timezone.utc)), _due("c3", 103, ref=None)],
        "whieda-advisor-bot",
    )
    assert [item["contact_id"] for item in planned] == ["c1"]
    payload = planned[0]["payload"]
    assert payload["chat_id"] == "101" and payload["binding_id"] == "whieda-advisor-bot"
    assert payload["site_login_user_id"] == 101
    assert payload["text"].startswith("⏰ Через час встреча: Анна, +79286729288")
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://igor.wwc.best/crm/?contact=c1#contact/c1"
    assert planned[0]["idempotency_key"] == meeting_idempotency_key("c1", MEETING)


class _Conn:
    """A connection whose nested transaction() is a no-op savepoint."""

    @asynccontextmanager
    async def transaction(self):
        yield self


@pytest.mark.asyncio
async def test_only_a_claimed_card_is_queued(open_to_all_pro):
    from app.crm import meetings

    @asynccontextmanager
    async def connection(_tenant_id):
        yield _Conn()

    # c1: the planner wins the card; c2: another pass already set meeting_reminded_at.
    claims = AsyncMock(side_effect=[{"contact_id": "c1"}, None])
    enqueue = AsyncMock(return_value={"outbox_id": 1, "status": "scheduled", "created": True})
    with (
        patch.object(meetings, "_meetings_due", AsyncMock(return_value=[_due("c1", 101), _due("c2", 101)])),
        patch.object(meetings, "tenant_connection", connection),
        patch.object(meetings, "fetch_one", claims),
        patch.object(meetings, "enqueue_outbox_event", enqueue),
    ):
        assert await meetings.enqueue_meeting_reminders({"whieda": "whieda-advisor-bot"}) == 1
    assert enqueue.await_count == 1
    assert enqueue.await_args.kwargs["event_type"] == "crm_meeting_reminder"
    assert enqueue.await_args.kwargs["due_at"] is not None
    # The claim names the meeting time: a meeting moved since the read is not claimed.
    assert claims.await_args_list[0].args[2] == ("whieda", "c1", MEETING)


@pytest.mark.asyncio
async def test_one_failing_card_does_not_drop_the_others(open_to_all_pro, caplog):
    from app.crm import meetings

    class Refused(Exception):
        sqlstate = "23514"

        def __str__(self):
            return "Failing row contains (Анна, +79286729288)"

    @asynccontextmanager
    async def connection(_tenant_id):
        yield _Conn()

    claims = AsyncMock(side_effect=[Refused(), {"contact_id": "c2"}])
    enqueue = AsyncMock(return_value={"outbox_id": 2, "status": "scheduled", "created": True})
    with (
        patch.object(meetings, "_meetings_due", AsyncMock(return_value=[_due("c1", 101), _due("c2", 101)])),
        patch.object(meetings, "tenant_connection", connection),
        patch.object(meetings, "fetch_one", claims),
        patch.object(meetings, "enqueue_outbox_event", enqueue),
    ):
        assert await meetings.enqueue_meeting_reminders({"whieda": "whieda-advisor-bot"}) == 1
    assert enqueue.await_args.kwargs["payload"]["contact_id"] == "c2"
    record = next(r for r in caplog.records if r.message == "crm_meeting_queue_failed")
    assert (record.error_class, record.sqlstate) == ("Refused", "23514")
    assert "Анна" not in caplog.text and "+7928" not in caplog.text


@pytest.mark.asyncio
async def test_a_failing_tenant_logs_no_names(open_to_all_pro, caplog):
    from app.crm import meetings

    class Boom(Exception):
        sqlstate = "57014"

        def __str__(self):
            return "Анна +79286729288"

    with patch.object(meetings, "_meetings_due", AsyncMock(side_effect=Boom())):
        assert await meetings.enqueue_meeting_reminders({"whieda": "b"}) == 0
    assert "Анна" not in caplog.text and "+7928" not in caplog.text
    record = next(r for r in caplog.records if r.message == "crm_meeting_plan_failed")
    assert (record.error_class, record.sqlstate) == ("Boom", "57014")


def _binding():
    from app.telegram.bindings import BotBindingContext
    from app.tenancy import TenantContext

    return BotBindingContext(
        binding_id="whieda-advisor-bot",
        tenant=TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True}),
        bot_token_ref="env:TEST_TOKEN",
        webhook_secret_ref="env:TEST_SECRET",
        bot_username="test_bot",
        status="active",
        processing_mode="core",
        bot_token="test-token",
        webhook_secret="test-secret",
    )


@pytest.mark.asyncio
async def test_site_buttons_get_a_fresh_sign_in_link_when_sent():
    from app.jobs import worker

    payload = {
        "chat_id": "101",
        "text": "⏰ Через час встреча: Анна",
        "site_login_user_id": 101,
        "reply_markup": {"inline_keyboard": [
            [{"text": "👤 Открыть карточку", "url": "https://igor.wwc.best/crm/?contact=c1#contact/c1"}],
            [{"text": "Продлить", "callback_data": "renew:start"}],
        ]},
    }
    login = AsyncMock(side_effect=lambda url, **kw: url.split("#")[0] + "#wwc-login=fresh")
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.site_login.with_site_login", login), patch.object(worker, "send_telegram_text", send):
        await worker._send_due_notification(_binding(), payload)
    assert login.await_count == 1
    assert login.await_args.kwargs == {"tenant_id": "whieda", "telegram_user_id": 101}
    keyboard = send.await_args.kwargs["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["url"] == "https://igor.wwc.best/crm/?contact=c1#wwc-login=fresh"
    assert keyboard[1][0] == {"text": "Продлить", "callback_data": "renew:start"}
    # The stored payload keeps the plain link: no login token at rest.
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("#contact/c1")


@pytest.mark.asyncio
async def test_without_sign_in_target_buttons_are_sent_as_stored():
    from app.jobs import worker

    markup = {"inline_keyboard": [[{"text": "x", "url": "https://igor.wwc.best/crm/#today"}]]}
    login = AsyncMock()
    send = AsyncMock(return_value={"ok": True})
    with patch("app.telegram.site_login.with_site_login", login), patch.object(worker, "send_telegram_text", send):
        await worker._send_due_notification(_binding(), {"chat_id": "1", "text": "t", "reply_markup": markup})
        await worker._send_due_notification(
            _binding(), {"chat_id": "1", "text": "t", "reply_markup": markup, "site_login_user_id": True}
        )
    login.assert_not_awaited()
    assert send.await_args.kwargs["reply_markup"] == markup
