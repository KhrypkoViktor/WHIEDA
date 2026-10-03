"""Academy v2: video → MP4 H.264 720p + a poster (job worker, one at a time).

Queue: a ``platform_outbox`` row ``academy_media_transcode`` with status «scheduled»
and no ``binding_id``. The ordinary queue (``process_pending_outbox``, including
older builds on the shared staging/production database, which mark any unknown
event «done») takes only «pending»/«failed»; the scheduled notification sender
takes only rows of its bots. So only this module ever touches the job.

One job at a time across every worker on the shared database: the claim runs
under an advisory lock and refuses while another job has a fresh heartbeat
(``updated_at`` is touched every minute while ffmpeg runs). A job whose worker
died (no heartbeat for 10 minutes) is taken again, at most ``MAX_ATTEMPTS`` times.

ffmpeg: ``-c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p``, AAC 128k,
``-movflags +faststart``; the shorter side becomes 720 (never upscaled, phone
portrait videos stay portrait); poster — a JPEG frame at the 3rd second (or the
middle of a shorter clip). A file ffmpeg cannot read fails at once — the same
input would fail again. The original is deleted after success and kept after a
failure (for a look by hand).

A worker transcodes only where it can: ffmpeg and ffprobe on PATH and the media
directory mounted (``PLATFORM_ACADEMY_MEDIA_DIR``).

An upload is untrusted: ffprobe and ffmpeg read it with the ``file`` protocol only and
with video container demuxers only (mov/mp4, matroska/webm, avi, mpeg) — a playlist
(HLS, concat) disguised as a video is refused before it can fetch anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db import fetch_one, get_pool, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.settings import get_settings

logger = logging.getLogger(__name__)

EVENT_TYPE = "academy_media_transcode"
MAX_ATTEMPTS = 3
HEARTBEAT_SEC = 60.0
STALE_AFTER = "10 minutes"
FFMPEG_TIMEOUT_SEC = 6 * 3600
POSTER_AT_SEC = 3.0
# Контейнер загрузки → демультиплексор ffmpeg; всё прочее (HLS, concat, картинки) — отказ.
CONTAINERS = {"mov,mp4,m4a,3gp,3g2,mj2": "mov", "matroska,webm": "matroska", "avi": "avi", "mpeg": "mpeg"}
SAFE_INPUT = ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,avi,mpeg"]
SCALE_720 = (
    "scale=w='if(gt(iw,ih),-2,trunc(min(720,iw)/2)*2)':h='if(gt(iw,ih),trunc(min(720,ih)/2)*2,-2)',setsar=1"
)


class TranscodeFailed(Exception):
    """ffmpeg could not make the video: final, not retried."""


async def enqueue_transcode(conn: Any, tenant_id: str, media_id: str) -> None:
    await enqueue_outbox_event(
        conn,
        tenant_id=tenant_id,
        event_type=EVENT_TYPE,
        idempotency_key=f"academy_transcode:{media_id}",
        payload={"media_id": str(media_id)},
        due_at=datetime.now(timezone.utc),
    )


def transcoder_available() -> bool:
    return bool(
        shutil.which("ffmpeg")
        and shutil.which("ffprobe")
        and Path(get_settings().platform_academy_media_dir).is_dir()
    )


async def claim_job() -> dict[str, Any] | None:
    """The next due job, or None (nothing due, or another job is running)."""
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        async with conn.transaction():
            await fetch_one(conn, "select pg_advisory_xact_lock(hashtext(%s)) as locked", (EVENT_TYPE,))
            running = await fetch_one(
                conn,
                f"""
                select 1 as busy from platform_outbox
                where event_type = %s and status = 'processing'
                  and updated_at > now() - interval '{STALE_AFTER}'
                limit 1
                """,
                (EVENT_TYPE,),
            )
            if running:
                return None
            row = await fetch_one(
                conn,
                f"""
                select outbox_id, tenant_id, payload, attempts, status
                from platform_outbox
                where event_type = %s
                  and ((status = 'scheduled' and due_at <= now())
                       or (status = 'processing' and updated_at <= now() - interval '{STALE_AFTER}'))
                order by due_at nulls first, outbox_id
                limit 1
                for update skip locked
                """,
                (EVENT_TYPE,),
            )
            if not row:
                return None
            if row["status"] == "processing" and int(row["attempts"] or 0) >= MAX_ATTEMPTS:
                # Воркер умирал на этом видео (например, не хватило памяти) — больше не берём.
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update platform_outbox set status = 'dead', last_error = 'stuck_processing',"
                        " updated_at = now() where outbox_id = %s",
                        (row["outbox_id"],),
                    )
                stuck = row
                row = None
            else:
                stuck = None
            if row is not None:
                attempts = int(row["attempts"] or 0) + 1
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        update platform_outbox set status = 'processing', attempts = %s, updated_at = now()
                        where outbox_id = %s
                        """,
                        (attempts, row["outbox_id"]),
                    )
    if stuck is not None:
        stuck_payload = stuck["payload"] if isinstance(stuck["payload"], dict) else json.loads(stuck["payload"] or "{}")
        if stuck_payload.get("media_id"):
            await _set_media(str(stuck["tenant_id"]), str(stuck_payload["media_id"]), status="failed",
                             error="transcode_stuck")
        return None
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
    return {"outbox_id": row["outbox_id"], "tenant_id": row["tenant_id"], "payload": payload, "attempts": attempts}


async def _set_outbox(outbox_id: int, status: str, error: str | None = None) -> None:
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update platform_outbox
                set status = %s, last_error = %s, updated_at = now(),
                    due_at = case when %s = 'scheduled' then now() + interval '5 minutes' else due_at end
                where outbox_id = %s
                """,
                (status, error, status, outbox_id),
            )


async def _heartbeat(outbox_id: int) -> None:
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "update platform_outbox set updated_at = now() where outbox_id = %s and status = 'processing'",
                (outbox_id,),
            )


async def _set_media(tenant_id: str, media_id: str, *, status: str, variants: dict | None = None, error: str | None = None) -> None:
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update academy_media
                set status = %s, variants = coalesce(%s::jsonb, variants), error = %s, updated_at = now()
                where tenant_id = %s and media_id = %s::uuid
                """,
                (status, json.dumps(variants) if variants is not None else None, error, tenant_id, media_id),
            )


def _ffmpeg_error(exc: subprocess.CalledProcessError) -> str:
    text = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ("ffmpeg: " + (lines[-1] if lines else f"exit {exc.returncode}"))[:300]


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        check=True, capture_output=True, text=True, timeout=120,
    ).stdout.strip()
    try:
        return max(0.0, float(out))
    except ValueError:
        return 0.0


def probe_container(source: Path) -> str:
    """The demuxer for an upload, or TranscodeFailed («unsupported container»)."""
    try:
        name = subprocess.run(
            ["ffprobe", "-v", "error", *SAFE_INPUT, "-show_entries", "format=format_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            check=True, capture_output=True, timeout=120,
        ).stdout.decode("utf-8", "replace").strip()
    except subprocess.CalledProcessError as exc:
        raise TranscodeFailed(("ffmpeg: unsupported container: " + _ffmpeg_error(exc)[len("ffmpeg: "):])[:300]) from exc
    except subprocess.TimeoutExpired as exc:
        raise TranscodeFailed("ffmpeg: unsupported container: probe timeout") from exc
    demuxer = CONTAINERS.get(name)
    if demuxer is None:
        raise TranscodeFailed(f"ffmpeg: unsupported container {name or '?'}"[:300])
    return demuxer


def run_ffmpeg(source: Path, mp4: Path, poster: Path) -> dict[str, Any]:
    """Blocking (runs in a thread). Writes ``mp4`` and ``poster`` atomically."""
    mp4_tmp = mp4.with_name(mp4.name + ".tmp")
    poster_tmp = poster.with_name(poster.name + ".tmp")
    demuxer = probe_container(source)
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                *SAFE_INPUT, "-f", demuxer, "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?", "-vf", SCALE_720,
                "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(mp4_tmp),
            ],
            check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_SEC,
        )
        duration = probe_duration(mp4_tmp)
        at = POSTER_AT_SEC if duration > POSTER_AT_SEC * 2 else duration / 2
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(mp4_tmp),
                "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", str(poster_tmp),
            ],
            check=True, capture_output=True, timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        for leftover in (mp4_tmp, poster_tmp):
            leftover.unlink(missing_ok=True)
        raise TranscodeFailed(_ffmpeg_error(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        for leftover in (mp4_tmp, poster_tmp):
            leftover.unlink(missing_ok=True)
        raise TranscodeFailed("ffmpeg: timeout") from exc
    mp4_tmp.replace(mp4)
    poster_tmp.replace(poster)
    return {"duration_sec": int(round(duration))}


async def run_with_heartbeat(fn: Any, *args: Any, heartbeat: Any, interval: float) -> Any:
    """Run a blocking ``fn`` in a thread to its end, touching the job every ``interval``.
    A failed heartbeat (database blip) is logged and waited out — never a second ffmpeg."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    while True:
        finished, _ = await asyncio.wait({task}, timeout=interval)
        if finished:
            return task.result()
        try:
            await heartbeat()
        except Exception:
            logger.warning("academy_transcode_heartbeat_failed", exc_info=True)


async def process_job(job: dict[str, Any] | None) -> str:
    """Run one claimed job to the end; returns the media status it left."""
    if not job:
        return "none"
    from app.academy.media import get_media_store, media_key

    tenant_id = str(job["tenant_id"])
    media_id = str(job["payload"].get("media_id") or "")
    outbox_id = int(job["outbox_id"])
    async with tenant_connection(tenant_id) as conn:
        media = await fetch_one(
            conn,
            """
            select media_id::text as media_id, kind, status, storage_key from academy_media
            where tenant_id = %s and media_id = %s::uuid
            """,
            (tenant_id, media_id),
        ) if media_id else None
    if not media or media["kind"] != "video" or media["status"] != "processing":
        await _set_outbox(outbox_id, "done", "nothing_to_do")
        return str((media or {}).get("status") or "missing")
    store = get_media_store()
    source = store.path(media["storage_key"])
    mp4_key = media_key(tenant_id, media_id, "720.mp4")
    poster_key = media_key(tenant_id, media_id, "poster.jpg")
    if not source.is_file():
        await _set_media(tenant_id, media_id, status="failed", error="source_missing")
        await _set_outbox(outbox_id, "dead", "source_missing")
        return "failed"
    try:
        result = await run_with_heartbeat(
            run_ffmpeg, source, store.path(mp4_key), store.path(poster_key),
            heartbeat=lambda: _heartbeat(outbox_id), interval=HEARTBEAT_SEC,
        )
    except TranscodeFailed as exc:
        logger.warning("academy_transcode_failed", extra={"media_id": media_id, "error": str(exc)})
        await _set_media(tenant_id, media_id, status="failed", error=str(exc))
        await _set_outbox(outbox_id, "dead", str(exc))
        return "failed"
    except Exception as exc:  # диск, база, убитый процесс: повторим, пока есть попытки
        logger.exception("academy_transcode_error", extra={"media_id": media_id})
        final = int(job.get("attempts") or 1) >= MAX_ATTEMPTS
        if final:
            await _set_media(tenant_id, media_id, status="failed", error=f"transcode_error: {type(exc).__name__}")
        await _set_outbox(outbox_id, "dead" if final else "scheduled", type(exc).__name__)
        return "failed" if final else "processing"
    variants = {"mp4_720": mp4_key, "poster": poster_key, "duration_sec": result["duration_sec"]}
    await _set_media(tenant_id, media_id, status="ready", variants=variants)
    await _set_outbox(outbox_id, "done")
    try:
        source.unlink()
    except OSError:
        logger.warning("academy_transcode_source_kept", extra={"media_id": media_id})
    return "ready"


class TranscodeSlot:
    """The worker's single running job."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None

    async def step(self) -> bool:
        """Start the next job if nothing runs here; True when one was started."""
        if self.task is not None and not self.task.done():
            return False
        if self.task is not None and self.task.done() and not self.task.cancelled() and self.task.exception():
            logger.warning("academy_transcode_task_failed", exc_info=self.task.exception())
        self.task = None
        if not transcoder_available():
            return False
        job = await claim_job()
        if job is None:
            return False
        self.task = asyncio.create_task(process_job(job))
        return True
