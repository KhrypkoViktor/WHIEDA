"""CRM morning message: text, the 09:00 window in the account timezone, one row per day."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.crm.rules import digest_idempotency_key, digest_text, in_digest_window
from app.jobs.worker import _final_delivery_error
from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown

URL = "https://igor.wwc.best/crm/#today"


def test_text_lists_only_steps_with_work_in_fixed_order():
    text = digest_text({"decide": 1, "invite": 3, "result": 2, "ping": 0}, URL)
    assert text == (
        "Сегодня в ежедневнике: пригласить на встречу — 3, узнать результат — 2, "
        "довести до решения — 1.\n\nhttps://igor.wwc.best/crm/#today"
    )
    assert digest_text({"resume": 1}, URL).startswith("Сегодня в ежедневнике: вернуться к разговору — 1.")


def test_nothing_due_means_no_message():
    assert digest_text({}, URL) is None
    assert digest_text({"invite": 0}, URL) is None


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


def _row(account_id: str, local: datetime, user_id: int, counts: dict, *, paid_until=PAID_UNTIL, ref="igor") -> dict:
    return {
        "account_id": account_id,
        "telegram_user_id": user_id,
        "local_now": local,
        "chat_id": str(user_id),
        "counts": counts,
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


def test_plan_takes_only_the_window_and_people_with_access(open_to_all_pro):
    from app.crm.digest import plan_digests

    rows = [
        _row("in-window", datetime(2026, 9, 25, 9, 3), 101, {"invite": 2}),
        _row("too-late", datetime(2026, 9, 25, 11, 0), 102, {"invite": 1}),
        _row("pro-ended", datetime(2026, 9, 25, 9, 2), 104, {"invite": 1}, paid_until=EXPIRED, ref="petr"),
        _row("no-partner", datetime(2026, 9, 25, 9, 2), 105, {"invite": 1}, paid_until=None, ref=None),
        _row("zero", datetime(2026, 9, 25, 9, 1), 106, {"invite": 0}),
    ]
    planned = plan_digests(rows, "whieda-advisor-bot")
    assert [item["idempotency_key"] for item in planned] == ["crm_digest:in-window:2026-09-25"]
    payload = planned[0]["payload"]
    assert payload["chat_id"] == "101"
    assert payload["binding_id"] == "whieda-advisor-bot"
    assert payload["url"] == "https://igor.wwc.best/crm/#today"
    assert payload["text"].startswith("Сегодня в ежедневнике: пригласить на встречу — 2.")


def test_pilot_list_limits_the_morning_message(monkeypatch, open_to_all_pro):
    from app.crm.digest import plan_digests
    from app.settings import get_settings

    rows = [_row("a", datetime(2026, 9, 25, 9, 3), 101, {"result": 1}), _row("b", datetime(2026, 9, 25, 9, 3), 102, {"result": 1})]
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "102")
    get_settings.cache_clear()
    assert [item["payload"]["chat_id"] for item in plan_digests(rows, "b")] == ["102"]
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "")
    get_settings.cache_clear()
    assert plan_digests(rows, "b") == []  # empty pilot = nobody


@pytest.mark.asyncio
async def test_one_read_one_connection_and_second_pass_adds_nothing(open_to_all_pro):
    from app.crm import digest

    rows = [_row("acc", datetime(2026, 9, 25, 9, 6), 101, {"result": 1}), _row("acc2", datetime(2026, 9, 25, 9, 6), 102, {"invite": 3})]
    reads = AsyncMock(return_value=rows)
    connections = []

    @asynccontextmanager
    async def counting_connection(tenant_id):
        connections.append(tenant_id)
        yield object()

    created = AsyncMock(return_value={"outbox_id": 1, "status": "scheduled", "created": True})
    with (
        patch.object(digest, "_accounts_due", reads),
        patch.object(digest, "tenant_connection", counting_connection),
        patch.object(digest, "enqueue_outbox_event", created),
    ):
        assert await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"}) == 2
    assert reads.await_count == 1 and connections == ["whieda"]
    assert [call.kwargs["idempotency_key"] for call in created.await_args_list] == [
        "crm_digest:acc:2026-09-25", "crm_digest:acc2:2026-09-25"]
    assert all(call.kwargs["due_at"] is not None for call in created.await_args_list)

    again = AsyncMock(return_value={"outbox_id": 1, "status": "scheduled", "created": False})
    with (
        patch.object(digest, "_accounts_due", AsyncMock(return_value=rows)),
        patch.object(digest, "tenant_connection", _fake_connection),
        patch.object(digest, "enqueue_outbox_event", again),
    ):
        assert await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"}) == 0


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
