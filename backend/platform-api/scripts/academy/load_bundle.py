"""Load an Academy course bundle (from build_bundle.py) into Core.

    docker exec -i core-api-1 python scripts/academy/load_bundle.py whieda < bundle.json

Upserts the course by slug and lessons by (course, slug); lessons missing from
the bundle are archived, not deleted — progress rows stay. The course is
published. Runs with PLATFORM_DATABASE_URL (owner role, RLS bypass), sets
app.tenant_id anyway.
"""

from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.types.json import Jsonb


def load(tenant_id: str, bundle: dict) -> dict:
    course = bundle["course"]
    lessons = bundle["lessons"]
    with psycopg.connect(os.environ["PLATFORM_DATABASE_URL"]) as conn:
        conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        course_id = conn.execute(
            """
            insert into academy_courses (tenant_id, slug, title, subtitle, access_rule, status)
            values (%s, %s, %s, %s, %s, 'published')
            on conflict (tenant_id, slug) do update set
              title = excluded.title, subtitle = excluded.subtitle,
              access_rule = excluded.access_rule, status = 'published', updated_at = now()
            returning course_id
            """,
            (tenant_id, course["slug"], course["title"], course.get("subtitle"), course.get("access_rule", "pro")),
        ).fetchone()[0]
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
    return {"course": course["slug"], "lessons": len(slugs), "archived": [row[0] for row in archived]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: load_bundle.py <tenant_id> < bundle.json")
    print(json.dumps(load(sys.argv[1], json.load(sys.stdin)), ensure_ascii=False))
