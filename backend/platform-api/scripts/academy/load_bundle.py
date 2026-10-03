"""Load an Academy course bundle (from build_bundle.py) into Core.

    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda < bundle.json
    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda \\
        --author <actor_id> --status draft --access purchase < bundle.json
    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda publish <slug>

Upserts the course by slug and lessons by (course, slug); lessons missing from
the bundle are archived, not deleted — progress rows stay. Runs with
PLATFORM_DATABASE_URL (owner role, RLS bypass), sets app.tenant_id anyway.

Academy v2 (02.10.2026):
  * modules — one per ``module_title`` in lesson order (matched by title on a
    re-load, their unlock rules kept); a module gone from the bundle is deleted
    when no lesson points to it any more;
  * pictures — the bundle's ``media`` (path → mime + base64, from build_bundle.py)
    go to ``academy_media`` under ``PLATFORM_ACADEMY_MEDIA_DIR`` (the API container
    mounts it); ``src="img/…"`` (and the old ``/academy/img/…``) become ``media:<id>``.
    The id is stable for the same course, path and bytes: re-loading adds nothing.

Someone else's course is checked before students see it (owner, 25.09.2026):
  --status   draft | published. Not given: a NEW course lands as ``draft``, an
             existing one keeps its status (re-loading a live course to fix a
             typo does not hide it). ``publish <slug>`` opens a checked draft.
  --access   free | purchase | pro. Not given: a new course with ``--author`` is
             ``purchase`` (students get keys from the author); an existing
             author's course keeps its rule (re-loading without flags must not
             open it to every PRO partner); a platform course takes the bundle's
             own ``access_rule`` (the platform course stays ``pro``).
  --author   lead_actors.actor_id of the author: a WHIEDA partner linked to Telegram
             (telegram_user_id — the bot commands and the payment use it)
             and owning an enabled referral profile (the Academy term is paid on it).
             Not given: an existing course keeps its author, a new one has none.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

STATUSES = ("draft", "published")
ACCESS_RULES = ("free", "purchase", "pro")
DEFAULT_MEDIA_DIR = "/opt/whieda-platform-core/media/academy"
IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
IMAGE_SIGNATURES = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",), "image/gif": (b"GIF87a", b"GIF89a")}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MEDIA_NAMESPACE = uuid.UUID("5b0b5b8e-8f3c-4a39-9c8e-2f1d0a4c7e11")


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ["PLATFORM_DATABASE_URL"]


def _image_ok(mime: str, data: bytes) -> bool:
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(prefix) for prefix in IMAGE_SIGNATURES.get(mime, ()))


def _store_pictures(conn, tenant_id: str, course_slug: str, author: str | None, media: dict) -> dict[str, str]:
    """Bundle pictures → academy_media rows and files; returns path → media_id."""
    if not media:
        return {}
    root = Path(os.environ.get("PLATFORM_ACADEMY_MEDIA_DIR") or DEFAULT_MEDIA_DIR)
    if not root.is_dir():
        raise SystemExit(f"media directory {root} is missing (PLATFORM_ACADEMY_MEDIA_DIR)")
    if not re.fullmatch(r"[a-z0-9_-]+", tenant_id):
        raise SystemExit(f"odd tenant id {tenant_id!r}")
    mapping = {}
    for path, item in media.items():
        mime = str(item.get("mime") or "")
        data = base64.b64decode(item["data_b64"])
        if mime not in IMAGE_EXT or not _image_ok(mime, data) or len(data) > MAX_IMAGE_BYTES:
            raise SystemExit(f"picture {path!r}: not a png/jpg/webp/gif up to 20 MB")
        digest = hashlib.sha256(data).hexdigest()
        media_id = str(uuid.uuid5(MEDIA_NAMESPACE, f"{tenant_id}:{course_slug}:{path}:{digest}"))
        key = f"{tenant_id}/academy/{media_id}/original.{IMAGE_EXT[mime]}"
        target = root / tenant_id / "academy" / media_id / f"original.{IMAGE_EXT[mime]}"
        if not target.is_file() or target.stat().st_size != len(data):
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".tmp")
            partial.write_bytes(data)
            partial.replace(target)
        conn.execute(
            """
            insert into academy_media (tenant_id, media_id, owner_actor_id, kind, original_name, mime, size_bytes,
                                       storage_key, status)
            values (%s, %s::uuid, %s, 'image', %s, %s, %s, %s, 'ready')
            on conflict (media_id) do nothing
            """,
            (tenant_id, media_id, author, Path(path).name[:255] or "picture", mime, len(data), key),
        )
        mapping[path] = media_id
    return mapping


def _with_media(body_html: str, mapping: dict[str, str]) -> str:
    for path, media_id in mapping.items():
        for src in (path, "/academy/img/" + Path(path).name):
            body_html = body_html.replace(f'src="{src}"', f'src="media:{media_id}"')
    return body_html


def _sync_modules(conn, tenant_id: str, course_id, lessons: list[dict]) -> dict[str, str]:
    """One module per module_title, in lesson order; matched by title on a re-load."""
    titles: list[str] = []
    for lesson in lessons:
        title = str(lesson.get("module_title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    existing = {
        row[0]: str(row[1])
        for row in conn.execute(
            "select title, module_id from academy_modules where tenant_id = %s and course_id = %s",
            (tenant_id, course_id),
        ).fetchall()
    }
    ids = {}
    for position, title in enumerate(titles, start=1):
        if title in existing:
            conn.execute(
                "update academy_modules set position = %s, updated_at = now() where module_id = %s::uuid",
                (position, existing[title]),
            )
            ids[title] = existing[title]
        else:
            ids[title] = str(
                conn.execute(
                    """
                    insert into academy_modules (tenant_id, course_id, position, title)
                    values (%s, %s, %s, %s) returning module_id
                    """,
                    (tenant_id, course_id, position, title),
                ).fetchone()[0]
            )
    return ids


def _drop_stale_modules(conn, tenant_id: str, course_id, keep: dict[str, str]) -> None:
    stale = conn.execute(
        """
        select module_id from academy_modules
        where tenant_id = %s and course_id = %s and not (module_id = any(%s::uuid[]))
        """,
        (tenant_id, course_id, list(keep.values())),
    ).fetchall()
    for (module_id,) in stale:
        conn.execute(
            "update academy_lessons set module_id = null where module_id = %s and status = 'archived'", (module_id,)
        )
        busy = conn.execute("select 1 from academy_lessons where module_id = %s limit 1", (module_id,)).fetchone()
        if not busy:
            conn.execute("delete from academy_modules where module_id = %s", (module_id,))


def load(
    tenant_id: str,
    bundle: dict,
    *,
    author: str | None = None,
    status: str | None = None,
    access: str | None = None,
    dsn: str | None = None,
) -> dict:
    if status is not None and status not in STATUSES:
        raise SystemExit(f"--status must be one of {STATUSES}")
    if access is not None and access not in ACCESS_RULES:
        raise SystemExit(f"--access must be one of {ACCESS_RULES}")
    course = bundle["course"]
    lessons = bundle["lessons"]
    new_access = access or ("purchase" if author else course.get("access_rule", "pro"))
    with psycopg.connect(_dsn(dsn)) as conn:
        conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        if author:
            known = conn.execute(
                """
                select 1 from lead_actors la
                join referral_profiles rp
                  on rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
                where la.tenant_id = %s and la.actor_id = %s and la.active = true
                  and la.telegram_user_id is not null
                limit 1
                """,
                (tenant_id, author),
            ).fetchone()
            if not known:
                raise SystemExit(
                    f"author {author!r}: no active lead_actors row with Telegram and an enabled"
                    f" referral profile in {tenant_id!r}"
                )
        course_id, course_status, access_rule, course_author = conn.execute(
            """
            insert into academy_courses (tenant_id, slug, title, subtitle, access_rule, author_actor_id, status)
            values (%s, %s, %s, %s, %s, %s, coalesce(%s, 'draft'))
            on conflict (tenant_id, slug) do update set
              title = excluded.title, subtitle = excluded.subtitle,
              access_rule = case
                when %s::text is not null then %s::text
                when academy_courses.author_actor_id is not null then academy_courses.access_rule
                else excluded.access_rule
              end,
              author_actor_id = coalesce(%s, academy_courses.author_actor_id),
              status = coalesce(%s, academy_courses.status),
              updated_at = now()
            returning course_id, status, access_rule, author_actor_id
            """,
            (
                tenant_id, course["slug"], course["title"], course.get("subtitle"), new_access, author, status,
                access, access, author, status,
            ),
        ).fetchone()
        pictures = _store_pictures(conn, tenant_id, course["slug"], course_author, bundle.get("media") or {})
        modules = _sync_modules(conn, tenant_id, course_id, lessons)
        slugs = []
        for lesson in lessons:
            slugs.append(lesson["slug"])
            module_title = str(lesson.get("module_title") or "").strip()
            conn.execute(
                """
                insert into academy_lessons (
                  tenant_id, course_id, slug, module_title, module_id, position, title, short_title,
                  result_text, est_minutes, body_html, checklist, video, status
                ) values (%s, %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, 'published')
                on conflict (tenant_id, course_id, slug) do update set
                  module_title = excluded.module_title, module_id = excluded.module_id, position = excluded.position,
                  title = excluded.title, short_title = excluded.short_title,
                  result_text = excluded.result_text, est_minutes = excluded.est_minutes,
                  body_html = excluded.body_html, checklist = excluded.checklist,
                  video = excluded.video, status = 'published', updated_at = now()
                """,
                (
                    tenant_id, course_id, lesson["slug"], module_title, modules.get(module_title),
                    int(lesson["position"]), lesson["title"], lesson.get("short_title"),
                    lesson.get("result_text"), lesson.get("est_minutes"), _with_media(lesson["body_html"], pictures),
                    Jsonb(lesson.get("checklist") or []),
                    Jsonb(lesson["video"]) if lesson.get("video") else None,
                ),
            )
        archived = conn.execute(
            """
            update academy_lessons set status = 'archived', updated_at = now()
            where tenant_id = %s and course_id = %s and status <> 'archived' and not (slug = any(%s))
            returning slug
            """,
            (tenant_id, course_id, slugs),
        ).fetchall()
        _drop_stale_modules(conn, tenant_id, course_id, modules)
        conn.commit()
    return {
        "course": course["slug"],
        "status": course_status,
        "access_rule": access_rule,
        "author": author,
        "lessons": len(slugs),
        "modules": len(modules),
        "media": len(pictures),
        "archived": [row[0] for row in archived],
    }


def publish(tenant_id: str, slug: str, *, dsn: str | None = None) -> dict:
    """A checked draft → published: students see it (with its lock)."""
    with psycopg.connect(_dsn(dsn)) as conn:
        conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        row = conn.execute(
            """
            update academy_courses set status = 'published', updated_at = now()
            where tenant_id = %s and slug = %s and status <> 'archived'
            returning slug, status, access_rule, author_actor_id
            """,
            (tenant_id, slug),
        ).fetchone()
        conn.commit()
    if not row:
        raise SystemExit(f"course {slug!r} not found in tenant {tenant_id!r} (or archived)")
    return {"course": row[0], "status": row[1], "access_rule": row[2], "author": row[3]}


def main(argv: list[str] | None = None) -> dict:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[1] == "publish":
        if len(args) != 3:
            raise SystemExit("usage: load_bundle.py <tenant_id> publish <slug>")
        return publish(args[0], args[2])
    parser = argparse.ArgumentParser(description="Load an Academy course bundle from stdin.")
    parser.add_argument("tenant_id")
    parser.add_argument("--author", default=None, help="lead_actors.actor_id of the course author")
    parser.add_argument("--status", choices=STATUSES, default=None, help="new course: draft by default")
    parser.add_argument("--access", choices=ACCESS_RULES, default=None, help="author's course: purchase by default")
    parsed = parser.parse_args(args)
    return load(
        parsed.tenant_id, json.load(sys.stdin), author=parsed.author, status=parsed.status, access=parsed.access
    )


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
