"""Academy: a mini-course opens for free after a test and the installed app (V39, 19.10.2026).

Решение лида: ``PROCESS/wwc-mini-courses-20261016/TZ_UNLOCK.md`` пп. 3–5. Правило — ключ ``unlock``
в ``academy_courses.free_for`` (V35)::

    {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}, "app": true}}

Все перечисленные условия выполнены (и человек — из ``members``, если они заданы) —
``free_reason = "unlock"`` (``app.academy.service.free_reason_for``):
курс открыт без покупки, ученик проходит его самостоятельно, как при ``club`` и ``course``.

* ``unlock_progress`` — закрытому покупкой курсу с таким правилом: что человек уже сделал
  (``quiz_passed``, ``app_installed``), куда идти за тестом (``quiz_url``, ``quiz_course_title``)
  и может ли он вообще открыть курс теста (``eligible``: обычная проверка замка этого курса,
  у «Запуска» — PRO или строка доступа). Открытым и курсам без правила — null. Правило с
  ``members`` (только клуб и/или ученики названных курсов, 19.10.2026): не участнику — тоже null,
  он видит только покупку; сообщение об открытии ему не шлётся.
* ``mark_app_installed`` — отметка приложения: сайт сам шлёт её из установленного приложения
  (``POST /academy/app/installed``, раз в сутки на устройство). Таблица ``academy_app_installs``.
* ``notify_unlocks`` — как только после сданного теста или отметки приложения условия курса
  выполнились, человеку уходит одно сообщение в бот (очередь ``platform_outbox`` с кнопкой
  со входом на сайт — тот же путь, что у уведомлений о домашках и «Урока дня»). Запись в
  ``academy_unlock_notices`` — только когда сообщение поставлено в очередь; сбой не ломает ответ
  роута (лог), следующий повод проверит заново. Не шлём тому, кому курс и так открыт
  (куплен, подарен, клуб, участник курса).
"""

from __future__ import annotations

import logging
from typing import Any

from app.academy.notify import (
    course_page_url,
    enqueue_notification,
)
from app.academy.service import (
    AcademyViewer,
    FreeFacts,
    UnlockSpec,
    _access_rows,
    _iso,
    _load_course,
    course_lock_reason,
    course_unlock,
    free_facts,
    free_reason_for,
    is_staff_for,
    quiz_path,
    unlock_member,
    viewer_actor_ids,
)
from app.academy.showcases import showcase_for_author
from app.db import fetch_all, fetch_one, tenant_connection

logger = logging.getLogger(__name__)

PLATFORMS = ("android", "ios", "desktop")
UNLOCK_EVENT = "academy_unlock_opened"
OPEN_COURSE_BUTTON = "Открыть"


def clean_platform(value: Any) -> str | None:
    """``android`` | ``ios`` | ``desktop``; anything else (unknown, wrong type) — None."""
    return value if isinstance(value, str) and value in PLATFORMS else None


def unlock_view(
    spec: UnlockSpec, facts: FreeFacts, *, quiz_course_title: str | None, eligible: bool
) -> dict[str, Any]:
    """The ``unlock`` object of a closed course. A condition the rule does not have counts as met."""
    return {
        "quiz_passed": spec.quiz is None or spec.quiz in facts.passed_quizzes,
        "app_installed": not spec.app or facts.app_installed,
        "quiz_url": quiz_path(*spec.quiz) if spec.quiz is not None else None,
        "quiz_course_title": quiz_course_title,
        "eligible": eligible,
    }


async def _quiz_course(conn: Any, tenant_id: str, viewer: AcademyViewer, slug: str) -> tuple[str | None, bool]:
    """(title, can the person open the test's course) by the course's own lock — the same rules as everywhere."""
    course = await _load_course(conn, tenant_id, slug)
    if course is None:
        return None, False
    uid = viewer.telegram_user_id
    if is_staff_for(course, viewer, await viewer_actor_ids(conn, tenant_id, uid)):
        return course["title"], True
    access = await _access_rows(conn, tenant_id, uid)
    reason = free_reason_for(course, await free_facts(conn, tenant_id, uid, [course]))
    lock = course_lock_reason(course, viewer, has_access_row=course["course_id"] in access, free_reason=reason)
    return course["title"], lock is None


async def unlock_progress(
    conn: Any, tenant_id: str, viewer: AcademyViewer, course: dict[str, Any], facts: FreeFacts | None
) -> dict[str, Any] | None:
    """Progress towards opening a course closed by purchase; None — the course has no ``unlock`` rule,
    or the rule is for ``members`` and the person is not one (they see the purchase only)."""
    spec = course_unlock(course)
    if spec is None or (spec.members is not None and not unlock_member(spec, facts)):
        return None
    title, eligible = None, True
    if spec.quiz is not None:
        title, eligible = await _quiz_course(conn, tenant_id, viewer, spec.quiz[0])
    return unlock_view(
        spec, facts or FreeFacts(club=False, course_slugs=frozenset()), quiz_course_title=title, eligible=eligible
    )


def unlock_text(course_title: str, spec: UnlockSpec, quiz_course_title: str | None, *, kind: str = "mini") -> str:
    """«🎁 Мини-курс «…» открыт бесплатно: вы сдали тест «…» и поставили приложение WWC.» — по условиям правила."""
    done = []
    if spec.quiz is not None:
        done.append(f"сдали тест «{quiz_course_title}»" if quiz_course_title else "сдали итоговый тест")
    if spec.app:
        done.append("поставили приложение WWC")
    what = "Мини-курс" if kind == "mini" else "Курс"
    return f"🎁 {what} «{course_title}» открыт бесплатно: вы {' и '.join(done)}."


async def mark_app_installed(tenant_id: str, telegram_user_id: int, platform: Any) -> dict[str, Any]:
    """Upsert of the mark (first and last seen; a known platform is not wiped by an unknown one), then the
    check of the courses it may open."""
    uid = int(telegram_user_id)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into academy_app_installs as a (tenant_id, telegram_user_id, platform)
            values (%s, %s, %s)
            on conflict (tenant_id, telegram_user_id) do update
              set last_seen_at = now(), platform = coalesce(excluded.platform, a.platform)
            returning first_seen_at
            """,
            (tenant_id, uid, clean_platform(platform)),
        )
        await notify_unlocks(conn, tenant_id, uid)
    return {"ok": True, "installed": True, "first_seen_at": _iso((row or {}).get("first_seen_at"))}


async def notify_unlocks(
    conn: Any, tenant_id: str, telegram_user_id: int, *, quiz: tuple[str, str] | None = None
) -> int:
    """Message the person about every course that this event has just opened for them; returns how many.

    ``quiz`` — (course slug, lesson slug) of the test that was just passed; None — the app mark came.
    Never raises: the work runs in a savepoint, a failure is logged and the caller's transaction (the
    attempt, the mark) goes on."""
    try:
        async with conn.transaction():
            return await _notify_unlocks(conn, tenant_id, int(telegram_user_id), quiz)
    except Exception:
        logger.warning(
            "academy_unlock_notify_failed", exc_info=True, extra={"tenant_id": tenant_id, "quiz": quiz}
        )
        return 0


async def _notify_unlocks(conn: Any, tenant_id: str, uid: int, quiz: tuple[str, str] | None) -> int:
    courses = await fetch_all(
        conn,
        """
        select c.course_id::text as course_id, c.slug, c.title, c.kind, c.access_rule, c.free_for,
               c.author_actor_id
        from academy_courses c
        where c.tenant_id = %s and c.status = 'published' and c.access_rule = 'purchase'
          and jsonb_typeof(c.free_for -> 'unlock') = 'object'
          and not exists (
            select 1 from academy_unlock_notices n
            where n.tenant_id = c.tenant_id and n.course_id = c.course_id and n.telegram_user_id = %s
          )
        order by c.sort_order, c.title
        """,
        (tenant_id, uid),
    )
    # Тест сдан — ждут именно этого теста; отметка приложения — те, кому приложение нужно.
    waiting = []
    for course in courses:
        spec = course_unlock(course)
        if spec is not None and ((spec.quiz == quiz) if quiz is not None else spec.app):
            waiting.append((course, spec))
    if not waiting:
        return 0
    owned = await _access_rows(conn, tenant_id, uid)
    sent = 0
    for course, spec in waiting:
        if course["course_id"] in owned:
            continue  # куплен или подарен — «открыт бесплатно за тест» было бы неправдой
        if free_reason_for(course, await free_facts(conn, tenant_id, uid, [course])) != "unlock":
            continue  # условия ещё не все, либо курс и так открыт клубом / курсом
        quiz_title = None
        if spec.quiz is not None:
            quiz_row = await _load_course(conn, tenant_id, spec.quiz[0])
            quiz_title = quiz_row["title"] if quiz_row else None
        showcase = await showcase_for_author(tenant_id, course.get("author_actor_id"))
        queued = await enqueue_notification(
            conn,
            tenant_id,
            event_type=UNLOCK_EVENT,
            key=f"{UNLOCK_EVENT}:{course['course_id']}:{uid}",
            recipient=uid,
            text=unlock_text(course["title"], spec, quiz_title, kind=str(course.get("kind") or "")),
            button_text=OPEN_COURSE_BUTTON,
            url=course_page_url(course["slug"], showcase.base_url),
            binding_id=showcase.telegram_binding_id,
        )
        if not queued:
            continue  # бот уведомлений не настроен: записи нет, следующий повод попробует снова
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into academy_unlock_notices (tenant_id, course_id, telegram_user_id)
                values (%s, %s::uuid, %s)
                on conflict (tenant_id, course_id, telegram_user_id) do nothing
                """,
                (tenant_id, course["course_id"], uid),
            )
        sent += 1
    return sent
