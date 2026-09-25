"""Load an Academy course bundle (from build_bundle.py) into Core.

    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda < bundle.json
    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda \\
        --author <actor_id> --status draft --access purchase < bundle.json
    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda publish <slug>

Upserts the course by slug and lessons by (course, slug); lessons missing from
the bundle are archived, not deleted — progress rows stay. Runs with
PLATFORM_DATABASE_URL (owner role, RLS bypass), sets app.tenant_id anyway.

Someone else's course is checked before students see it (owner, 25.09.2026):
  --status   draft | published. Not given: a NEW course lands as ``draft``, an
             existing one keeps its status (re-loading a live course to fix a
             typo does not hide it). ``publish <slug>`` opens a checked draft.
  --access   free | purchase | pro. Not given: ``purchase`` for an author's course
             (``--author``: students get keys from the author), otherwise the
             bundle's own ``access_rule`` (the platform course stays ``pro``).
  --author   lead_actors.actor_id of the author (a WHIEDA partner). Not given:
             an existing course keeps its author, a new one has none (platform).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg.types.json import Jsonb

STATUSES = ("draft", "published")
ACCESS_RULES = ("free", "purchase", "pro")


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ["PLATFORM_DATABASE_URL"]


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
    access_rule = access or ("purchase" if author else course.get("access_rule", "pro"))
    with psycopg.connect(_dsn(dsn)) as conn:
        conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        if author:
            known = conn.execute(
                "select 1 from lead_actors where tenant_id = %s and actor_id = %s", (tenant_id, author)
            ).fetchone()
            if not known:
                raise SystemExit(f"author {author!r} is not a lead_actors row of tenant {tenant_id!r}")
        course_id, course_status = conn.execute(
            """
            insert into academy_courses (tenant_id, slug, title, subtitle, access_rule, author_actor_id, status)
            values (%s, %s, %s, %s, %s, %s, coalesce(%s, 'draft'))
            on conflict (tenant_id, slug) do update set
              title = excluded.title, subtitle = excluded.subtitle,
              access_rule = excluded.access_rule,
              author_actor_id = coalesce(%s, academy_courses.author_actor_id),
              status = coalesce(%s, academy_courses.status),
              updated_at = now()
            returning course_id, status
            """,
            (
                tenant_id, course["slug"], course["title"], course.get("subtitle"), access_rule, author, status,
                author, status,
            ),
        ).fetchone()
        slugs = []
        for lesson in lessons:
            slugs.append(lesson["slug"])
            conn.execute(
                """
                insert into academy_lessons (
                  tenant_id, course_id, slug, module_title, position, title, short_title,
                  result_text, est_minutes, body_html, checklist, video, status
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'published')
                on conflict (tenant_id, course_id, slug) do update set
                  module_title = excluded.module_title, position = excluded.position,
                  title = excluded.title, short_title = excluded.short_title,
                  result_text = excluded.result_text, est_minutes = excluded.est_minutes,
                  body_html = excluded.body_html, checklist = excluded.checklist,
                  video = excluded.video, status = 'published', updated_at = now()
                """,
                (
                    tenant_id, course_id, lesson["slug"], lesson.get("module_title") or "",
                    int(lesson["position"]), lesson["title"], lesson.get("short_title"),
                    lesson.get("result_text"), lesson.get("est_minutes"), lesson["body_html"],
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
        conn.commit()
    return {
        "course": course["slug"],
        "status": course_status,
        "access_rule": access_rule,
        "author": author,
        "lessons": len(slugs),
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
