"""Academy v2 media in the database: rows, links of a viewer, who may see what.

Who gets a link to a media (``GET /media/{id}/url``):
  * the person who uploaded it, the owner and preview admins;
  * a cover or a description picture of a published course — anyone who sees the
    Academy (the catalog shows it before the key);
  * a lesson's video, file or picture — a student with access to that course, or
    its author;
  * a homework photo — the student who handed it in, or the course author.
Lesson schedule locks are not re-checked here: a media id is only ever shown in a
lesson the viewer could open.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.academy.media import MediaError, signed_url
from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger(__name__)

_MEDIA_COLUMNS = """
    media_id::text as media_id, owner_actor_id, owner_telegram_user_id, kind, original_name, mime, size_bytes,
    chunk_size, storage_key, status, variants, error, created_at, updated_at
"""


def _valid_ids(ids: list[Any]) -> list[str]:
    """Ids from the outside → canonical UUID strings; anything else is dropped (no 500 on ::uuid)."""
    out: list[str] = []
    for value in ids:
        text = str(value or "").strip().lower()
        if len(text) != 36:
            continue
        try:
            canonical = str(uuid.UUID(text))
        except ValueError:
            continue
        if canonical not in out:
            out.append(canonical)
    return out


async def load_media(conn: Any, tenant_id: str, ids: list[Any]) -> dict[str, dict[str, Any]]:
    wanted = _valid_ids(ids)
    if not wanted:
        return {}
    rows = await fetch_all(
        conn,
        f"select {_MEDIA_COLUMNS} from academy_media where tenant_id = %s and media_id = any(%s::uuid[])",
        (tenant_id, wanted),
    )
    return {row["media_id"]: row for row in rows}


def media_links(row: dict[str, Any] | None, telegram_user_id: int) -> dict[str, str]:
    """Signed links of a ready media for this viewer; {} when not ready or links are off."""
    if not row or row.get("status") != "ready":
        return {}
    try:
        if row["kind"] == "video":
            variants = row.get("variants") or {}
            if not variants.get("mp4_720"):
                return {}
            links = {"url": signed_url(str(variants["mp4_720"]), telegram_user_id)}
            if variants.get("poster"):
                links["poster_url"] = signed_url(str(variants["poster"]), telegram_user_id)
            return links
        return {"url": signed_url(str(row["storage_key"]), telegram_user_id)}
    except MediaError:
        logger.warning("academy_media_links_unavailable")
        return {}


def media_summary(row: dict[str, Any], telegram_user_id: int) -> dict[str, Any]:
    links = media_links(row, telegram_user_id)
    out = {
        "media_id": row["media_id"],
        "kind": row["kind"],
        "name": row["original_name"],
        "size": int(row["size_bytes"]),
        "mime": row["mime"],
        "status": row["status"],
        "url": links.get("url"),
    }
    if row["kind"] == "video":
        out["poster_url"] = links.get("poster_url")
        out["duration_sec"] = (row.get("variants") or {}).get("duration_sec")
    return out


async def media_access(conn: Any, tenant_id: str, row: dict[str, Any], viewer: Any) -> bool:
    from app.academy.service import (
        _access_rows,
        academy_visible,
        course_lock_reason,
        is_staff_for,
        viewer_actor_ids,
    )

    if viewer.is_preview_admin or row.get("owner_telegram_user_id") == viewer.telegram_user_id:
        return True
    actor_ids = await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)
    if row.get("owner_actor_id") and str(row["owner_actor_id"]) in actor_ids:
        return True
    media_id = row["media_id"]
    courses = await fetch_all(
        conn,
        """
        select c.course_id::text as course_id, c.access_rule, c.author_actor_id,
               (c.cover_media_id = %(m)s::uuid
                or strpos(coalesce(c.description_html, ''), 'media:' || %(m)s::text) > 0) as showcase
        from academy_courses c
        where c.tenant_id = %(t)s and c.status = 'published'
          and (
            c.cover_media_id = %(m)s::uuid
            or strpos(coalesce(c.description_html, ''), 'media:' || %(m)s::text) > 0
            or exists (
              select 1 from academy_lessons l
              where l.tenant_id = c.tenant_id and l.course_id = c.course_id and l.status = 'published'
                and (l.files @> jsonb_build_array(%(m)s::text)
                     or l.video ->> 'media_id' = %(m)s::text
                     or strpos(l.body_html, 'media:' || %(m)s::text) > 0)
            )
          )
        """,
        {"t": tenant_id, "m": media_id},
    )
    if courses:
        access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
        for course in courses:
            if course["showcase"] and academy_visible(viewer):
                return True
            if is_staff_for(course, viewer, actor_ids):
                return True
            if course_lock_reason(course, viewer, has_access_row=course["course_id"] in access) is None:
                return True
    homework = await fetch_all(
        conn,
        """
        select s.telegram_user_id, c.author_actor_id
        from academy_submissions s
        join academy_lessons l on l.tenant_id = s.tenant_id and l.lesson_id = s.lesson_id
        join academy_courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
        where s.tenant_id = %s and s.media @> jsonb_build_array(%s::text)
        """,
        (tenant_id, media_id),
    )
    for item in homework:
        if int(item["telegram_user_id"]) == viewer.telegram_user_id or is_staff_for(item, viewer, actor_ids):
            return True
    return False


async def media_url(tenant_id: str, media_id: str, viewer: Any) -> dict[str, Any]:
    """``GET /media/{id}/url``: a fresh signed link (1 hour) for someone allowed to see it."""
    wanted = _valid_ids([media_id])
    if not wanted:
        raise MediaError(404, "media_not_found")
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            f"select {_MEDIA_COLUMNS} from academy_media where tenant_id = %s and media_id = %s::uuid",
            (tenant_id, wanted[0]),
        )
        if not row:
            raise MediaError(404, "media_not_found")
        if not await media_access(conn, tenant_id, row, viewer):
            raise MediaError(403, "media_forbidden")
    summary = media_summary(row, viewer.telegram_user_id)
    if row["status"] == "ready" and not summary.get("url"):
        raise MediaError(503, "media_unavailable")
    return {**summary, "expires_in": int(get_settings().platform_academy_media_url_ttl_seconds)}


# ---- uploads ---------------------------------------------------------------------------------

MAX_OPEN_UPLOADS = 20  # одновременно незавершённых загрузок у одного человека
STUDENT_KINDS = ("image",)
AUTHOR_KINDS = ("image", "file", "video")


def _upload_out(row: dict[str, Any], telegram_user_id: int, received: list[int] | None = None) -> dict[str, Any]:
    from app.academy.media import chunks_total

    out = media_summary(row, telegram_user_id)
    out.update(
        {
            "chunk_size": int(row["chunk_size"]),
            "chunks_total": chunks_total(int(row["size_bytes"]), int(row["chunk_size"])),
            "error": row.get("error"),
        }
    )
    if row["status"] == "uploading":
        out["chunks_received"] = list(received or [])
    return out


async def init_upload(
    tenant_id: str,
    viewer: Any,
    *,
    kind: str,
    name: str,
    mime: str,
    size: int,
    allowed_kinds: tuple[str, ...],
    chunk_size: int | None = None,
) -> dict[str, Any]:
    from app.academy.keys import actor_ids_for_telegram
    from app.academy.media import CHUNK_SIZE, MIME_TYPES, check_upload, media_key

    if kind not in allowed_kinds:
        raise MediaError(403 if kind in MIME_TYPES else 400, "kind_not_allowed" if kind in MIME_TYPES else "bad_kind")
    ext = check_upload(kind, mime, size, name)
    media_id = str(uuid.uuid4())
    key = media_key(tenant_id, media_id, f"original.{ext}")
    async with tenant_connection(tenant_id) as conn:
        busy = await fetch_one(
            conn,
            """
            select count(*) as n from academy_media
            where tenant_id = %s and owner_telegram_user_id = %s and status = 'uploading'
              and created_at > now() - interval '3 days'
            """,
            (tenant_id, viewer.telegram_user_id),
        )
        if busy and int(busy["n"]) >= MAX_OPEN_UPLOADS:
            raise MediaError(429, "too_many_uploads")
        actor_ids = await actor_ids_for_telegram(conn, tenant_id, viewer.telegram_user_id)
        row = await fetch_one(
            conn,
            f"""
            insert into academy_media (
              tenant_id, media_id, owner_actor_id, owner_telegram_user_id, kind, original_name, mime,
              size_bytes, chunk_size, storage_key, status
            ) values (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, 'uploading')
            returning {_MEDIA_COLUMNS}
            """,
            (
                tenant_id, media_id, actor_ids[0] if actor_ids else None, viewer.telegram_user_id, kind,
                str(name).strip(), str(mime).strip().lower(), int(size), int(chunk_size or CHUNK_SIZE), key,
            ),
        )
    return _upload_out(row, viewer.telegram_user_id, [])


async def _own_media(conn: Any, tenant_id: str, viewer: Any, media_id: str, *, lock: str = "") -> dict[str, Any]:
    wanted = _valid_ids([media_id])
    row = None
    if wanted:
        row = await fetch_one(
            conn,
            f"""
            select {_MEDIA_COLUMNS} from academy_media
            where tenant_id = %s and media_id = %s::uuid and owner_telegram_user_id = %s
            {lock}
            """,
            (tenant_id, wanted[0], viewer.telegram_user_id),
        )
    if not row:
        raise MediaError(404, "media_not_found")
    return row


async def put_chunk(tenant_id: str, viewer: Any, media_id: str, n: int, data: bytes) -> dict[str, Any]:
    import asyncio

    from app.academy.media import chunk_length, chunks_total, get_media_store

    store = get_media_store()
    async with tenant_connection(tenant_id) as conn:
        row = await _own_media(conn, tenant_id, viewer, media_id, lock="for share")
        if row["status"] != "uploading":
            raise MediaError(409, "not_uploading", {"status": row["status"]})
        expected = chunk_length(int(row["size_bytes"]), int(n), int(row["chunk_size"]))
        if len(data) != expected:
            raise MediaError(400, "bad_chunk_size", {"expected": expected})
        await asyncio.to_thread(
            store.write_chunk, tenant_id, row["media_id"], int(n), int(n) * int(row["chunk_size"]), data
        )
    received = store.received(tenant_id, row["media_id"])
    return {
        "media_id": row["media_id"],
        "chunk": int(n),
        "chunks_received": len(received),
        "chunks_total": chunks_total(int(row["size_bytes"]), int(row["chunk_size"])),
    }


async def upload_status(tenant_id: str, viewer: Any, media_id: str) -> dict[str, Any]:
    """The site resumes an interrupted upload from ``chunks_received``."""
    from app.academy.media import get_media_store

    async with tenant_connection(tenant_id) as conn:
        row = await _own_media(conn, tenant_id, viewer, media_id)
    received = get_media_store().received(tenant_id, row["media_id"]) if row["status"] == "uploading" else []
    return _upload_out(row, viewer.telegram_user_id, received)


async def complete_upload(tenant_id: str, viewer: Any, media_id: str) -> dict[str, Any]:
    """Every chunk in place → the original. An image is checked by its signature; a
    video goes to the transcoding queue. Repeating ``complete`` is harmless."""
    import asyncio

    from app.academy.media import chunks_total, get_media_store, image_signature_ok
    from app.academy.transcode import enqueue_transcode

    store = get_media_store()
    async with tenant_connection(tenant_id) as conn:
        row = await _own_media(conn, tenant_id, viewer, media_id, lock="for update")
        if row["status"] in ("processing", "ready"):
            return _upload_out(row, viewer.telegram_user_id)
        if row["status"] == "failed":
            raise MediaError(409, "upload_failed", {"error": row.get("error")})
        total = chunks_total(int(row["size_bytes"]), int(row["chunk_size"]))
        received = set(store.received(tenant_id, row["media_id"]))
        missing = [n for n in range(total) if n not in received]
        if missing:
            raise MediaError(409, "upload_incomplete", {"missing": missing[:50]})
        if row["kind"] == "image" and not image_signature_ok(row["mime"], store.part_head(tenant_id, row["media_id"])):
            await asyncio.to_thread(store.discard_upload, tenant_id, row["media_id"])
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update academy_media set status = 'failed', error = 'bad_image', updated_at = now()
                    where tenant_id = %s and media_id = %s::uuid
                    """,
                    (tenant_id, row["media_id"]),
                )
            failed = True
        else:
            failed = False
            await asyncio.to_thread(store.assemble, tenant_id, row["media_id"], row["storage_key"], int(row["size_bytes"]))
            status = "processing" if row["kind"] == "video" else "ready"
            row = await fetch_one(
                conn,
                f"""
                update academy_media set status = %s, updated_at = now()
                where tenant_id = %s and media_id = %s::uuid
                returning {_MEDIA_COLUMNS}
                """,
                (status, tenant_id, row["media_id"]),
            )
            if status == "processing":
                await enqueue_transcode(conn, tenant_id, row["media_id"])
    if failed:
        # Вне транзакции: статус «failed» уже записан, ошибка — для ответа сайту.
        raise MediaError(422, "bad_image")
    return _upload_out(row, viewer.telegram_user_id)
