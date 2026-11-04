"""Инцидент 04.11.2026: воркер около двух часов стоял на оборванном соединении с базой, напоминания эфира
не ушли и никто не знал. Теперь соединение рвётся примерно за минуту, а зависший цикл через 5 минут
перезапускается с сигналом владельцу."""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest

from app import db
from app.jobs import stall_watchdog, worker
from app.site_requests import nudge

TOKEN = "123456:SECRET-bot-token"
OWNER = 777


class _Exited(Exception):
    pass


@pytest.fixture
def watchdog(monkeypatch):
    """Сторож без настоящего выхода из процесса и без настоящей сети; posts — что ушло в Telegram."""
    posts: list[dict] = []

    def exit_process(code: int) -> None:
        raise _Exited(code)

    def post(url: str, *, json: dict, timeout: float):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(stall_watchdog.os, "_exit", exit_process)
    monkeypatch.setattr(stall_watchdog.httpx, "post", post)
    monkeypatch.setattr(
        stall_watchdog, "get_settings",
        lambda: SimpleNamespace(environment="production", platform_billing_owner_telegram_id=OWNER, telegram_bot_token=TOKEN),
    )
    return SimpleNamespace(posts=posts)


def _silent_for(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(stall_watchdog, "_last_beat", time.monotonic() - seconds)


def test_moving_loop_is_left_alone(watchdog, monkeypatch):
    _silent_for(monkeypatch, stall_watchdog.STALL_LIMIT_SEC - 30)
    stall_watchdog.check_stall()
    assert watchdog.posts == []


def test_stalled_loop_alerts_owner_then_exits(watchdog, monkeypatch):
    _silent_for(monkeypatch, 301)
    with pytest.raises(_Exited) as exited:
        stall_watchdog.check_stall()
    assert exited.value.args == (1,)
    assert len(watchdog.posts) == 1
    assert watchdog.posts[0]["url"].endswith(f"/bot{TOKEN}/sendMessage")
    assert watchdog.posts[0]["json"]["chat_id"] == OWNER
    assert "5 мин" in watchdog.posts[0]["json"]["text"]


def test_beat_resets_the_timer(watchdog, monkeypatch):
    _silent_for(monkeypatch, 301)
    stall_watchdog.beat()
    stall_watchdog.check_stall()
    assert watchdog.posts == []


def test_failed_alert_still_exits_and_keeps_token_out_of_log(watchdog, monkeypatch, caplog):
    def post(url: str, **_kwargs):
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(stall_watchdog.httpx, "post", post)
    _silent_for(monkeypatch, 301)
    with caplog.at_level(logging.ERROR, logger="app.jobs.stall_watchdog"), pytest.raises(_Exited):
        stall_watchdog.check_stall()
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == ["worker_stall_alert_failed: ConnectError"]
    assert TOKEN not in caplog.text
    assert not any(TOKEN in str(vars(r)) for r in caplog.records)


def test_production_without_token_says_so_and_still_exits(watchdog, monkeypatch, caplog):
    monkeypatch.setattr(
        stall_watchdog, "get_settings",
        lambda: SimpleNamespace(environment="production", platform_billing_owner_telegram_id=OWNER, telegram_bot_token=None),
    )
    _silent_for(monkeypatch, 301)
    with caplog.at_level(logging.ERROR, logger="app.jobs.stall_watchdog"), pytest.raises(_Exited):
        stall_watchdog.check_stall()
    assert watchdog.posts == []
    assert "worker_stall_alert_failed: no owner id or bot token in settings" in caplog.text


def test_hung_alert_does_not_block_the_restart(watchdog, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(stall_watchdog, "_alert_owner", lambda _silent_sec: release.wait(30))
    monkeypatch.setattr(stall_watchdog, "ALERT_WAIT_SEC", 0.05)
    _silent_for(monkeypatch, 301)
    started = time.monotonic()
    try:
        with pytest.raises(_Exited):
            stall_watchdog.check_stall()
        assert time.monotonic() - started < 5
    finally:
        release.set()


def test_staging_restarts_without_alert(watchdog, monkeypatch):
    monkeypatch.setattr(
        stall_watchdog, "get_settings",
        lambda: SimpleNamespace(environment="staging", platform_billing_owner_telegram_id=OWNER, telegram_bot_token=TOKEN),
    )
    _silent_for(monkeypatch, 301)
    with pytest.raises(_Exited):
        stall_watchdog.check_stall()
    assert watchdog.posts == []


def test_real_hung_await_ends_the_process_with_code_1(tmp_path):
    """Настоящий поток и настоящий зависший await: обычный выход сюда не дошёл бы."""
    script = textwrap.dedent(
        """
        import asyncio
        from app.jobs import stall_watchdog as w
        w.STALL_LIMIT_SEC = 0.3
        w.CHECK_INTERVAL_SEC = 0.05
        w._alert_owner = lambda silent_sec: print("alert", flush=True)
        w.start_stall_watchdog()
        asyncio.run(asyncio.Event().wait())
        """
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, done.stderr
    assert "alert" in done.stdout


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_every_step_of_a_pass_beats(monkeypatch):
    """Один проход — по отметке перед каждым шагом: срок считается на шаг, а не на весь проход."""
    beats = []

    async def sleep(_seconds: float) -> None:
        raise _Stop

    class _Slot:
        async def step(self) -> None:
            return None

    monkeypatch.setattr(worker, "beat", lambda: beats.append(1))
    monkeypatch.setattr(worker, "init_pool_for_worker", AsyncMock())
    monkeypatch.setattr(worker, "process_pending_outbox", AsyncMock(return_value=0))
    monkeypatch.setattr(worker, "TranscodeSlot", _Slot)
    monkeypatch.setattr(worker, "scheduled_notifications_step", AsyncMock(return_value={}))
    monkeypatch.setattr(worker, "academy_notifications_step", AsyncMock(return_value=0))
    monkeypatch.setattr(worker, "cleanup_abandoned_uploads", AsyncMock())
    monkeypatch.setattr(worker, "login_cleanup_step", AsyncMock())
    monkeypatch.setattr("app.telegram.club_meetings.club_meetings_step", AsyncMock())
    monkeypatch.setattr("app.site_requests.nudge.site_request_nudge_step", AsyncMock(return_value=0))
    monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(_Stop):
        await worker.worker_loop(2.0)
    # очередь, перекодирование, плановые, Академия, клуб, загрузки, входы, заявки на сайт
    assert len(beats) == 8


@pytest.mark.asyncio
async def test_each_outbox_row_beats(monkeypatch):
    """Очередь из 20 строк при лежащем n8n может идти дольше 5 минут, хотя цикл жив."""
    beats = []

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class _Conn(_Tx):
        def transaction(self):
            return _Tx()

    pool = SimpleNamespace(connection=lambda timeout: _Conn())
    rows = [{"outbox_id": n} for n in range(3)]
    monkeypatch.setattr(worker, "beat", lambda: beats.append(1))
    monkeypatch.setattr(worker, "get_pool", lambda: pool)
    monkeypatch.setattr(worker, "ensure_outbox_table", AsyncMock())
    monkeypatch.setattr(worker, "fetch_all", AsyncMock(return_value=rows))
    monkeypatch.setattr(worker, "_dispatch_event", AsyncMock(return_value=True))
    assert await worker.process_pending_outbox() == 3
    assert len(beats) == 3


@pytest.mark.asyncio
async def test_pool_connections_have_their_own_tcp_deadline(monkeypatch):
    seen: dict = {}

    class _Pool:
        def __init__(self, **kwargs) -> None:
            seen.update(kwargs)

        async def open(self) -> None:
            return None

        async def wait(self) -> None:
            return None

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "AsyncConnectionPool", _Pool)
    await db.init_pool()
    assert seen["kwargs"]["keepalives"] == 1
    assert seen["kwargs"]["keepalives_idle"] == 30
    assert seen["kwargs"]["tcp_user_timeout"] == 60_000
    # Прежние настройки на месте: без dict_row весь код чтения строк сломается.
    assert seen["kwargs"]["row_factory"] is db.dict_row
    assert seen["kwargs"]["prepare_threshold"] is None


@pytest.mark.asyncio
async def test_each_partner_nudge_beats(monkeypatch):
    beats = []
    monkeypatch.setattr(nudge, "beat", lambda: beats.append(1))
    monkeypatch.setattr(nudge, "find_candidates", AsyncMock(return_value=[
        {"rid": f"r{n}", "status": "awaiting_form", "upd": "2026-11-04T10:00:00", "display_name": "x"} for n in range(3)
    ]))
    monkeypatch.setattr(nudge, "first_name", lambda _row: "x")
    await nudge.run_nudge(send=False)
    assert len(beats) == 3


def test_liveness_options_are_valid_for_libpq():
    """Опечатка в имени параметра уронила бы подключение к базе при старте, а не в тесте выше."""
    dsn = psycopg.conninfo.make_conninfo("host=db.invalid dbname=x", **db._TCP_LIVENESS)
    parsed = psycopg.pq.Conninfo.parse(dsn.encode())
    values = {o.keyword.decode(): o.val.decode() for o in parsed if o.val is not None}
    assert values["keepalives_idle"] == "30"
    assert values["keepalives_interval"] == "10"
    assert values["keepalives_count"] == "3"
    assert values["tcp_user_timeout"] == "60000"
