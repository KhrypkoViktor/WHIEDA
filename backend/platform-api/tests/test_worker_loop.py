"""worker_loop survives a failing queue step (incident 23.10.2026: DB went read-only,
process_pending_outbox raised outside try/except, the container restarted 14 times).

The loop keeps living, logs ``outbox_step_failed`` and pauses 2 -> 4 -> 8 ... <= 60 s
between passes while the step keeps failing; one good pass resets the pause."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.jobs import worker
from app.jobs.worker import FailureBackoff


class _StopLoop(Exception):
    """Raised by the fake sleep to leave worker_loop after the planned passes."""


def test_backoff_doubles_up_to_cap_and_resets():
    backoff = FailureBackoff()
    assert [backoff.failed() for _ in range(8)] == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    backoff.succeeded()
    assert [backoff.failed() for _ in range(3)] == [2.0, 4.0, 8.0]


def test_backoff_never_exceeds_cap_for_odd_start():
    backoff = FailureBackoff(start=5.0, cap=12.0)
    assert [backoff.failed() for _ in range(4)] == [5.0, 10.0, 12.0, 12.0]


class _Harness:
    """Replaces every step of worker_loop; records the sleeps and the calls."""

    def __init__(self, monkeypatch, outbox_results: list, *, poll: float = 2.0) -> None:
        self.sleeps: list[float] = []
        self.outbox_calls = 0
        self.transcode_calls = 0
        self.poll = poll
        results = list(outbox_results)

        async def outbox(batch_size: int = 20) -> int:
            self.outbox_calls += 1
            outcome = results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        class _Slot:
            async def step(inner) -> None:  # noqa: N805
                self.transcode_calls += 1

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            if not results:
                raise _StopLoop

        monkeypatch.setattr(worker, "init_pool_for_worker", AsyncMock())
        monkeypatch.setattr(worker, "process_pending_outbox", outbox)
        monkeypatch.setattr(worker, "TranscodeSlot", _Slot)
        monkeypatch.setattr(worker, "scheduled_notifications_step", AsyncMock(return_value={}))
        monkeypatch.setattr(worker, "academy_notifications_step", AsyncMock(return_value=0))
        monkeypatch.setattr(worker, "cleanup_abandoned_uploads", AsyncMock())
        monkeypatch.setattr(worker, "login_cleanup_step", AsyncMock())
        monkeypatch.setattr("app.telegram.club_meetings.club_meetings_step", AsyncMock())
        self.site_nudge = AsyncMock(return_value=0)
        monkeypatch.setattr("app.site_requests.nudge.site_request_nudge_step", self.site_nudge)
        # Only the module-level name is replaced: the real asyncio.sleep stays intact for the test runner.
        monkeypatch.setattr(worker, "asyncio", SimpleNamespace(sleep=sleep))

    async def run(self) -> None:
        with pytest.raises(_StopLoop):
            await worker.worker_loop(self.poll)


@pytest.mark.asyncio
async def test_outbox_failure_does_not_stop_the_loop(monkeypatch, caplog):
    boom = RuntimeError("cannot execute UPDATE in a read-only transaction")
    harness = _Harness(monkeypatch, [boom, boom, 3])
    with caplog.at_level(logging.ERROR, logger="app.jobs.worker"):
        await harness.run()
    # Three passes happened: the failures did not end worker_loop, and the other
    # per-pass step (transcode) still ran after each of them.
    assert harness.outbox_calls == 3
    assert harness.transcode_calls == 3
    failed = [r for r in caplog.records if r.getMessage() == "outbox_step_failed"]
    assert len(failed) == 2
    assert all(r.exc_info for r in failed)


@pytest.mark.asyncio
async def test_pause_grows_while_failing_and_resets_after_success(monkeypatch):
    boom = RuntimeError("db down")
    # 7 failures, one success, 2 failures again, one last success to stop.
    harness = _Harness(monkeypatch, [boom] * 7 + [1] + [boom] * 2 + [0])
    await harness.run()
    assert harness.sleeps == [
        2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0,  # growing, capped at 60 s
        2.0,  # success: back to the ordinary poll interval
        2.0, 4.0,  # next failure starts from the bottom again
        2.0,  # success
    ]


@pytest.mark.asyncio
async def test_failure_pause_is_never_shorter_than_the_poll_interval(monkeypatch):
    harness = _Harness(monkeypatch, [RuntimeError("x"), RuntimeError("x"), 0], poll=10.0)
    await harness.run()
    assert harness.sleeps == [10.0, 10.0, 10.0]


@pytest.mark.asyncio
async def test_healthy_loop_sleeps_the_poll_interval_only(monkeypatch):
    harness = _Harness(monkeypatch, [0, 2, 0])
    await harness.run()
    assert harness.sleeps == [2.0, 2.0, 2.0]


@pytest.mark.asyncio
async def test_cancellation_still_stops_the_loop(monkeypatch):
    """Shutdown (SIGTERM -> task cancel) must not be swallowed by the new try/except."""
    harness = _Harness(monkeypatch, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await worker.worker_loop(2.0)
    assert harness.outbox_calls == 1


@pytest.mark.asyncio
async def test_site_request_nudge_runs_in_the_loop_and_its_failure_does_not_stop_it(monkeypatch, caplog):
    harness = _Harness(monkeypatch, [0, 0, 0])
    harness.site_nudge.side_effect = RuntimeError("telegram down")
    with caplog.at_level(logging.ERROR, logger="app.jobs.worker"):
        await harness.run()
    # Первый проход сразу проверяет время (шаг сам решает «10:00 и только бой»); сбой в журнал, цикл жив.
    assert harness.outbox_calls == 3
    assert harness.site_nudge.await_count == 1
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == ["site_request_nudge_failed"]
