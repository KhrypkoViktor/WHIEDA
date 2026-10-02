"""Academy v2: when a lesson opens and when it counts as done (no I/O).

Rules (``academy_modules.unlock``, a lesson's own ``unlock`` overrides its module's):
  open                         — open;
  after_prev                   — module: every lesson of the previous module is
                                 complete; lesson: the previous lesson of the course
                                 (in course order, across modules) is complete;
  date {"at": ts}              — open from that moment (``now >= at``);
  days_after_start {"days": n} — open n days after ``academy_access.started_at``.

A lesson is complete when it is done («Сделал») and, if it has a required homework,
the latest submission is accepted. Course access comes first: without it every
lesson is locked with the course reason (``purchase`` / ``pro``). Staff (the course
author, the owner and preview admins) see everything open.

Lock reasons for the site: ``after_prev`` | ``date:<iso>`` | ``days:<n>`` | ``purchase``
| ``pro``; ``opens_at`` is known for a date and for days with a start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

UNLOCK_TYPES = ("open", "after_prev", "date", "days_after_start")
MAX_UNLOCK_DAYS = 365
# Время без часового пояса — Москва/Минск: авторы и ученики там (UTC+3, без перехода).
NAIVE_TZ = timezone(timedelta(hours=3))
OPEN_RULE = {"type": "open"}


@dataclass(frozen=True)
class ModuleIn:
    key: str
    unlock: dict[str, Any] | None


@dataclass(frozen=True)
class LessonIn:
    key: str
    module_key: str
    unlock: dict[str, Any] | None
    done: bool
    assignment_required: bool | None = None  # None — no homework
    submission_status: str | None = None  # latest: submitted | accepted | returned


@dataclass(frozen=True)
class LessonLock:
    locked: bool
    reason: str | None = None
    opens_at: datetime | None = None


OPEN_LOCK = LessonLock(False)


def is_complete(lesson: LessonIn) -> bool:
    if not lesson.done:
        return False
    if lesson.assignment_required:
        return lesson.submission_status == "accepted"
    return True


def is_finished_by_student(lesson: LessonIn) -> bool:
    """Nothing left for the student to do: done, and the homework (if required) is
    handed in and not returned. Waiting for the author's review counts as finished."""
    if not lesson.done:
        return False
    if lesson.assignment_required:
        return lesson.submission_status in ("submitted", "accepted")
    return True


def _parse_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise ValueError("unlock_bad_date") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=NAIVE_TZ)
    return moment.astimezone(timezone.utc)


def parse_unlock(raw: Any, *, allow_none: bool = False) -> dict[str, Any] | None:
    """An author's rule → the stored form; ValueError for anything else."""
    if raw is None:
        return None if allow_none else dict(OPEN_RULE)
    if not isinstance(raw, dict):
        raise ValueError("unlock_bad_rule")
    kind = raw.get("type")
    if kind not in UNLOCK_TYPES:
        raise ValueError("unlock_bad_rule")
    if kind in ("open", "after_prev"):
        return {"type": kind}
    if kind == "date":
        if not raw.get("at"):
            raise ValueError("unlock_bad_date")
        return {"type": "date", "at": _parse_at(raw["at"]).isoformat()}
    try:
        days = int(raw.get("days"))
    except (TypeError, ValueError) as exc:
        raise ValueError("unlock_bad_days") from exc
    if days < 0 or days > MAX_UNLOCK_DAYS:
        raise ValueError("unlock_bad_days")
    return {"type": "days_after_start", "days": days}


def _rule_lock(rule: dict[str, Any] | None, *, prev_complete: bool, started_at: datetime | None, now: datetime) -> LessonLock:
    kind = (rule or OPEN_RULE).get("type", "open")
    if kind == "after_prev":
        return OPEN_LOCK if prev_complete else LessonLock(True, "after_prev")
    if kind == "date":
        try:
            at = _parse_at(rule["at"])
        except (KeyError, ValueError):
            return LessonLock(True, "date:unknown")  # испорченное правило: закрыто, а не открыто
        return OPEN_LOCK if now >= at else LessonLock(True, f"date:{at.isoformat()}", at)
    if kind == "days_after_start":
        try:
            days = int(rule.get("days"))
        except (TypeError, ValueError):
            return LessonLock(True, "days:unknown")
        if days <= 0:
            return OPEN_LOCK
        if started_at is None:
            return LessonLock(True, f"days:{days}")
        opens = started_at + timedelta(days=days)
        return OPEN_LOCK if now >= opens else LessonLock(True, f"days:{days}", opens)
    return OPEN_LOCK


def evaluate_locks(
    modules: list[ModuleIn],
    lessons: list[LessonIn],
    *,
    now: datetime,
    started_at: datetime | None,
    course_lock: str | None = None,
    bypass: bool = False,
) -> dict[str, LessonLock]:
    """``modules`` in course order; ``lessons`` in course order (module, then position).
    Lessons of a module missing from ``modules`` (no module) are open."""
    if course_lock:
        return {lesson.key: LessonLock(True, course_lock) for lesson in lessons}
    if bypass:
        return {lesson.key: OPEN_LOCK for lesson in lessons}
    complete = {lesson.key: is_complete(lesson) for lesson in lessons}
    by_module: dict[str, list[LessonIn]] = {}
    for lesson in lessons:
        by_module.setdefault(lesson.module_key, []).append(lesson)
    module_locks: dict[str, LessonLock] = {}
    previous_complete = True
    for module in modules:
        module_locks[module.key] = _rule_lock(module.unlock, prev_complete=previous_complete, started_at=started_at, now=now)
        previous_complete = all(complete[item.key] for item in by_module.get(module.key, []))
    result: dict[str, LessonLock] = {}
    for index, lesson in enumerate(lessons):
        if lesson.unlock:
            prev_done = complete[lessons[index - 1].key] if index > 0 else True
            result[lesson.key] = _rule_lock(lesson.unlock, prev_complete=prev_done, started_at=started_at, now=now)
        else:
            result[lesson.key] = module_locks.get(lesson.module_key, OPEN_LOCK)
    return result


def next_lesson_key(lessons: list[LessonIn], locks: dict[str, LessonLock]) -> str | None:
    """The first lesson that is open and still has something for the student to do."""
    for lesson in lessons:
        lock = locks.get(lesson.key, OPEN_LOCK)
        if not lock.locked and not is_finished_by_student(lesson):
            return lesson.key
    return None
