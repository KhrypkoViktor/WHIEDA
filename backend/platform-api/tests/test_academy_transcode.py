"""Academy v2 transcoding: one job per worker, portrait stays portrait, the loop runs it."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from app.academy import transcode
from app.academy.transcode import TranscodeFailed, TranscodeSlot, run_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


@pytest.mark.asyncio
async def test_slot_runs_one_job_at_a_time():
    release = asyncio.Event()

    async def slow_job(job):
        await release.wait()
        return "ready"

    slot = TranscodeSlot()
    with patch.object(transcode, "transcoder_available", return_value=True), patch.object(
        transcode, "claim_job", AsyncMock(return_value={"outbox_id": 1, "tenant_id": "whieda", "payload": {}})
    ) as claim, patch.object(transcode, "process_job", slow_job):
        assert await slot.step() is True
        assert await slot.step() is False  # пока идёт первая — вторую не берём
        assert claim.await_count == 1
        release.set()
        await slot.task
        assert await slot.step() is True
        release.set()
        await slot.task


@pytest.mark.asyncio
async def test_slot_does_nothing_without_ffmpeg_or_media_dir():
    slot = TranscodeSlot()
    with patch.object(transcode, "transcoder_available", return_value=False), patch.object(
        transcode, "claim_job", AsyncMock()
    ) as claim:
        assert await slot.step() is False
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_loop_steps_the_transcode_slot(monkeypatch):
    from app.jobs import worker

    calls = []

    class FakeSlot:
        async def step(self):
            calls.append("step")

    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    monkeypatch.setattr(worker, "init_pool_for_worker", AsyncMock())
    monkeypatch.setattr(worker, "process_pending_outbox", AsyncMock(return_value=0))
    monkeypatch.setattr(worker, "TranscodeSlot", FakeSlot)
    # Уборка брошенных загрузок — в первом же проходе (потом раз в час); ею цикл и остановим.
    cleanup = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(worker, "cleanup_abandoned_uploads", cleanup)
    from app.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(asyncio.CancelledError):
            await worker.worker_loop(poll_interval_sec=0)
    finally:
        get_settings.cache_clear()
    assert calls == ["step"]
    cleanup.assert_awaited_once()


@needs_ffmpeg
def test_portrait_phone_video_becomes_720_wide(tmp_path):
    source = tmp_path / "portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=1080x1920:rate=25",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True,
    )
    result = run_ffmpeg(source, tmp_path / "720.mp4", tmp_path / "poster.jpg")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(tmp_path / "720.mp4")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert probe == "720,1280"
    assert result["duration_sec"] == 2
    assert (tmp_path / "poster.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    assert not list(tmp_path.glob("*.tmp"))


@needs_ffmpeg
def test_small_video_is_not_upscaled_and_garbage_fails(tmp_path):
    source = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=25",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True,
    )
    run_ffmpeg(source, tmp_path / "720.mp4", tmp_path / "poster.jpg")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(tmp_path / "720.mp4")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert probe == "640,360"
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"definitely not a video" * 10)
    with pytest.raises(TranscodeFailed) as failed:
        run_ffmpeg(garbage, tmp_path / "g.mp4", tmp_path / "g.jpg")
    assert str(failed.value).startswith("ffmpeg:")
    assert not (tmp_path / "g.mp4").exists() and not list(tmp_path.glob("g.*.tmp"))


@pytest.mark.asyncio
async def test_heartbeat_failure_does_not_abandon_a_running_encode():
    import time

    from app.academy.transcode import run_with_heartbeat

    beats = []

    async def broken_heartbeat():
        beats.append(1)
        raise RuntimeError("database blip")

    def encode(value):
        time.sleep(0.3)
        return {"done": value}

    assert await run_with_heartbeat(encode, 7, heartbeat=broken_heartbeat, interval=0.05) == {"done": 7}
    assert len(beats) >= 2


@needs_ffmpeg
def test_only_video_containers_reach_ffmpeg(tmp_path):
    playlist = tmp_path / "trick.mp4"
    playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://169.254.169.254/x.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(TranscodeFailed) as refused:
        run_ffmpeg(playlist, tmp_path / "720.mp4", tmp_path / "poster.jpg")
    # Отказ до кодирования: плейлист HLS не открывается, ссылки из него не запрашиваются.
    assert str(refused.value).startswith("ffmpeg: unsupported container")
    assert not (tmp_path / "720.mp4").exists()
