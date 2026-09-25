"""CRM morning message: text, the 09:00 window in the account timezone, one row per day."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.crm.rules import CrmViewer, digest_idempotency_key, digest_text, in_digest_window
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


def _account(account_id: str, local: datetime, user_id: int) -> dict:
    return {
        "account_id": account_id,
        "telegram_user_id": user_id,
        "timezone": "Europe/Moscow",
        "local_now": local,
        "chat_id": str(user_id),
    }


@pytest.mark.asyncio
async def test_plans_one_row_in_window_and_skips_the_rest():
    from app.crm import digest

    accounts = [
        _account("in-window", datetime(2026, 9, 25, 9, 3), 101),
        _account("too-late", datetime(2026, 9, 25, 11, 0), 102),
        _account("nothing-due", datetime(2026, 9, 25, 9, 1), 103),
        _account("locked", datetime(2026, 9, 25, 9, 2), 104),
    ]
    counts = {"in-window": {"invite": 2}, "nothing-due": {}, "locked": {"invite": 1}}
    viewers = {
        101: CrmViewer(101, False, True, "igor", {"subdomain": "igor"}),
        104: CrmViewer(104, False, False, "petr", {}),
    }
    enqueue = AsyncMock(return_value={"outbox_id": 1, "status": "pending", "created": True})
    with patch.object(digest, "_accounts_with_contacts", AsyncMock(return_value=accounts)), patch.object(
        digest, "_due_counts", AsyncMock(side_effect=lambda _t, account_id, _d: counts[account_id])
    ), patch.object(digest, "load_viewer", AsyncMock(side_effect=lambda _t, uid: viewers[uid])), patch.object(
        digest, "tenant_connection", _fake_connection
    ), patch.object(digest, "enqueue_outbox_event", enqueue):
        planned = await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"})

    assert planned == 1
    kwargs = enqueue.await_args.kwargs
    assert kwargs["event_type"] == "crm_daily_digest"
    assert kwargs["idempotency_key"] == "crm_digest:in-window:2026-09-25"
    assert kwargs["payload"]["chat_id"] == "101"
    assert kwargs["payload"]["binding_id"] == "whieda-advisor-bot"
    assert kwargs["payload"]["url"] == "https://igor.wwc.best/crm/#today"
    assert kwargs["payload"]["text"].startswith("Сегодня в ежедневнике: пригласить на встречу — 2.")
    assert kwargs["due_at"] is not None


@pytest.mark.asyncio
async def test_second_pass_in_the_window_adds_nothing():
    from app.crm import digest

    accounts = [_account("acc", datetime(2026, 9, 25, 9, 6), 101)]
    enqueue = AsyncMock(return_value={"outbox_id": 1, "status": "pending", "created": False})
    with patch.object(digest, "_accounts_with_contacts", AsyncMock(return_value=accounts)), patch.object(
        digest, "_due_counts", AsyncMock(return_value={"result": 1})
    ), patch.object(
        digest, "load_viewer", AsyncMock(return_value=CrmViewer(101, False, True, "igor", {"subdomain": "igor"}))
    ), patch.object(digest, "tenant_connection", _fake_connection), patch.object(digest, "enqueue_outbox_event", enqueue):
        assert await digest.enqueue_crm_digests({"whieda": "whieda-advisor-bot"}) == 0
    assert enqueue.await_args.kwargs["idempotency_key"] == "crm_digest:acc:2026-09-25"


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
