"""Ритм курса «Онлайн-старт»: отметки «Сегодня», серия и табло недели (V36, 17.10.2026).

Решение лида: Obsidian ``Online_start/RITM_resheniye.md`` §2–3 — тексты карточки и табло
оттуда, это контракт. Каждый день у ученика одна строка ``academy_daily_checkins``: три
галочки — пост или видео, один человек, пятёрка. Три галочки — день засчитан (``done_at``).
Серия — засчитанные дни подряд, кончая сегодня (или вчера, пока сегодня не засчитан).
Пропуск — серия с нуля, карточка пишет «начнём заново».

Кто в ритме: активная ``academy_access`` на курс (``PLATFORM_RHYTHM_COURSE``) и preview-админы
(владелец). Даты — по ``PLATFORM_RHYTHM_TIMEZONE`` (Минск). Здесь данные и тексты; бот,
кнопки и расписание — ``app.telegram.rhythm``. Экран «Сегодня» приложения WWC (/app/) берёт
то же самое через ``today_payload`` / ``toggle_today`` (роуты ``/academy/rhythm/today``, 17.10.2026).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.academy.service import _ACCESS_ACTIVE, AcademyError, AcademyViewer, course_outline, preview_admin_ids
from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger(__name__)

# Строит ссылку урока по его slug: бот — со входом на витрину курса, сайт — путь /academy/.
LessonUrl = Callable[[str], "str | Awaitable[str]"]

FIELDS = ("post", "person", "five")
LINES = {
    "post": "Пост или видео",
    "person": "Один человек: написать, позвонить, пригласить",
    "five": "Пятёрка: комментарий и сохранение у четверых",
}
BUTTONS = {"post": "Пост", "person": "Человек", "five": "Пятёрка"}
# Код галочки в callback_data (≤ 64 байт): rh:<код>:<ГГГГММДД>.
CODES = {"p": "post", "h": "person", "f": "five"}
FIELD_CODES = {field: code for code, field in CODES.items()}
BOARD_LIMIT = 20
BOARD_STREAK_FROM = 3
HISTORY_DAYS = 400  # серия дольше года не встретится: курс — 4 недели
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def course_slug() -> str:
    return str(get_settings().platform_rhythm_course or "").strip()


def zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().platform_rhythm_timezone)
    except Exception:  # опечатка в .env не роняет worker
        logger.warning("rhythm_timezone_invalid")
        return ZoneInfo("Europe/Minsk")


def local_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(zone())


def local_today(now: datetime | None = None) -> date:
    return local_now(now).date()


def setting_time(value: str, default: time) -> time:
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        logger.warning("rhythm_time_invalid")
        return default


def card_time() -> time:
    return setting_time(get_settings().platform_rhythm_card_time, time(9, 0))


def board_time() -> time:
    return setting_time(get_settings().platform_rhythm_board_time, time(20, 0))


def week1_start() -> date | None:
    raw = str(get_settings().platform_rhythm_week1_start or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning("rhythm_week1_start_invalid")
        return None


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ru_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def streak(done_days: Iterable[date], today: date) -> int:
    """Засчитанные дни подряд, кончая сегодня; сегодня ещё не засчитан — кончая вчера."""
    days = set(done_days)
    cursor = today if today in days else today - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def streak_phrase(count: int, *, broken: bool) -> str:
    if count > 0:
        return f"Серия: {count} {plural(count, 'день', 'дня', 'дней')} подряд"
    return "Серия: начнём заново" if broken else "Серия пока не началась"


def lesson_line(lesson: dict[str, Any] | None) -> str | None:
    if not lesson or not lesson.get("title"):
        return None
    line = f"📖 Урок дня: «{lesson['title']}»"
    minutes = lesson.get("minutes")
    if isinstance(minutes, int) and minutes > 0:
        line += f", {minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"
    return line


def mark(value: Any) -> str:
    return "✅" if value else "☐"


def card_text(day: date, marks: dict[str, Any], count: int, *, broken: bool, lesson: dict[str, Any] | None) -> str:
    lines = [f"Сегодня, {ru_date(day)}. {streak_phrase(count, broken=broken)}."]
    lines += [f"{mark(marks.get(field))} {LINES[field]}" for field in FIELDS]
    extra = lesson_line(lesson)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def card_keyboard(day: date, marks: dict[str, Any], lesson_url: str | None) -> dict[str, Any]:
    stamp = day.strftime("%Y%m%d")
    rows = [[
        {"text": f"{mark(marks.get(field))} {BUTTONS[field]}", "callback_data": f"rh:{FIELD_CODES[field]}:{stamp}"}
        for field in FIELDS
    ]]
    if lesson_url:
        rows.append([{"text": "📖 Урок дня", "url": lesson_url}])
    return {"inline_keyboard": rows}


def week_of(day: date) -> tuple[date, date]:
    """Понедельник и воскресенье недели дня."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def week_number(sunday: date, start: date | None) -> int:
    if start is None:
        return 1
    return max(1, (sunday - week_of(start)[0]).days // 7 + 1)


def first_name(*candidates: Any) -> str:
    for value in candidates:
        words = str(value or "").split()
        if words:
            return words[0][:40]
    return "Участник"


def board_lines(people: list[dict[str, Any]]) -> list[str]:
    shown = sorted(
        (row for row in people if int(row["days"]) > 0),
        key=lambda row: (-int(row["days"]), -int(row["streak"]), str(row["name"]).lower()),
    )[:BOARD_LIMIT]
    lines = []
    for row in shown:
        line = f"{row['name']} — {int(row['days'])} из 7"
        if int(row["streak"]) >= BOARD_STREAK_FROM:
            line += f", серия {int(row['streak'])}"
        lines.append(line)
    return lines


def board_text(week: int, people: list[dict[str, Any]]) -> str | None:
    """Табло недели; ни одного засчитанного дня — None (в группу ничего)."""
    lines = board_lines(people)
    if not lines:
        return None
    return "\n".join([f"Неделя {week}. Кто в ритме:", *lines])


# ---------------------------------------------------------------- база


async def _course_id(conn: Any, tenant_id: str, course_slug: str) -> str | None:
    row = await fetch_one(
        conn,
        "select course_id::text as course_id from academy_courses where tenant_id = %s and slug = %s",
        (tenant_id, course_slug),
    )
    return row["course_id"] if row else None


async def rhythm_members(tenant_id: str, course_slug: str) -> list[int]:
    """Ученики с активным доступом и preview-админы; курса в тенанте нет — никого."""
    async with tenant_connection(tenant_id) as conn:
        course_id = await _course_id(conn, tenant_id, course_slug)
        if course_id is None:
            return []
        rows = await fetch_all(
            conn,
            f"""
            select telegram_user_id from academy_access
            where tenant_id = %s and course_id = %s::uuid and {_ACCESS_ACTIVE}
            """,
            (tenant_id, course_id),
        )
    return sorted({int(row["telegram_user_id"]) for row in rows} | set(preview_admin_ids()))


async def is_member(tenant_id: str, course_slug: str, telegram_user_id: int) -> bool:
    if int(telegram_user_id) in preview_admin_ids():
        return True
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            f"""
            select 1 as ok from academy_access a
            join academy_courses c on c.tenant_id = a.tenant_id and c.course_id = a.course_id
            where a.tenant_id = %s and c.slug = %s and a.telegram_user_id = %s and {_ACCESS_ACTIVE}
            """,
            (tenant_id, course_slug, int(telegram_user_id)),
        )
    return row is not None


async def toggle_mark(tenant_id: str, telegram_user_id: int, day: date, field: str) -> dict[str, Any]:
    """Переключить галочку дня; три галочки — ``done_at`` (первое время), снятая — null."""
    if field not in FIELDS:
        raise ValueError(field)
    new = {name: (f"not d.{name}" if name == field else f"d.{name}") for name in FIELDS}
    done = f"case when {new['post']} and {new['person']} and {new['five']} then coalesce(d.done_at, now()) end"
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            f"""
            insert into academy_daily_checkins as d (tenant_id, telegram_user_id, day, {field})
            values (%s, %s, %s, true)
            on conflict (tenant_id, telegram_user_id, day) do update
              set {field} = {new[field]}, done_at = {done}, updated_at = now()
            returning post, person, five, done_at
            """,
            (tenant_id, int(telegram_user_id), day),
        )
    return dict(row)


async def card_state(tenant_id: str, telegram_user_id: int, day: date, today: date) -> dict[str, Any]:
    """Галочки дня карточки, серия на сегодня и была ли серия раньше (для «начнём заново»)."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select day, post, person, five, done_at from academy_daily_checkins
            where tenant_id = %s and telegram_user_id = %s and day >= %s and day <= %s
            """,
            (tenant_id, int(telegram_user_id), today - timedelta(days=HISTORY_DAYS), max(day, today)),
        )
    by_day = {row["day"]: row for row in rows}
    done = {row["day"] for row in rows if row["done_at"] is not None and row["day"] <= today}
    count = streak(done, today)
    current = by_day.get(day) or {}
    return {
        "marks": {field: bool(current.get(field)) for field in FIELDS},
        "streak": count,
        "broken": count == 0 and bool(done),
    }


async def board_people(tenant_id: str, course_slug: str, sunday: date) -> tuple[int, list[dict[str, Any]]]:
    """Номер недели и по каждому в ритме: имя, засчитанные дни недели, серия на воскресенье."""
    members = await rhythm_members(tenant_id, course_slug)
    if not members:
        return 1, []
    monday = week_of(sunday)[0]
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select telegram_user_id, day from academy_daily_checkins
            where tenant_id = %s and telegram_user_id = any(%s) and done_at is not null
              and day >= %s and day <= %s
            """,
            (tenant_id, members, sunday - timedelta(days=HISTORY_DAYS), sunday),
        )
        first = await fetch_one(
            conn,
            "select min(day) as day from academy_daily_checkins where tenant_id = %s and telegram_user_id = any(%s)",
            (tenant_id, members),
        )
        names = await fetch_all(
            conn,
            """
            select m.uid as telegram_user_id, p.name as profile_name, la.display_name
            from unnest(%s::bigint[]) as m(uid)
            left join academy_profiles p on p.tenant_id = %s and p.telegram_user_id = m.uid
            left join lateral (
              select display_name from lead_actors
              where tenant_id = %s and telegram_user_id = m.uid
              order by active desc
              limit 1
            ) la on true
            """,
            (members, tenant_id, tenant_id),
        )
    done: dict[int, set[date]] = {}
    for row in rows:
        done.setdefault(int(row["telegram_user_id"]), set()).add(row["day"])
    name_of = {int(row["telegram_user_id"]): first_name(row["profile_name"], row["display_name"]) for row in names}
    people = [
        {
            "telegram_user_id": uid,
            "name": name_of.get(uid, "Участник"),
            "days": sum(1 for day in days if monday <= day <= sunday),
            "streak": streak(days, sunday),
        }
        for uid, days in done.items()
    ]
    start = week1_start() or (first["day"] if first and first["day"] else None)
    return week_number(sunday, start), people


# ---------------------------------------------------------------- урок дня и экран «Сегодня»


def site_lesson_path(course: str, lesson_slug: str) -> str:
    """Урок на сайте без входа в бот: ``/academy/?course=<slug>&lesson=<slug>``."""
    return f"/academy/?{urlencode({'course': course, 'lesson': lesson_slug})}"


async def lesson_of_day(
    tenant_id: str, course: str, telegram_user_id: int, url_for: LessonUrl
) -> dict[str, Any] | None:
    """Следующий непройденный открытый урок курса; курс закрыт или пройден — None.

    ``url_for(lesson_slug)`` строит ссылку (может быть корутиной): бот — адрес витрины курса,
    сайт — ``site_lesson_path``.
    """
    uid = int(telegram_user_id)
    viewer = AcademyViewer(telegram_user_id=uid, is_preview_admin=uid in preview_admin_ids(), partner_paid=False)
    try:
        outline = await course_outline(tenant_id, course, viewer)
    except AcademyError:
        return None
    upcoming = outline["course"].get("next_lesson")
    lesson = next((row for row in outline["lessons"] if row["slug"] == upcoming), None)
    if lesson is None or lesson.get("locked"):
        return None
    url = url_for(lesson["slug"])
    if inspect.isawaitable(url):
        url = await url
    return {"slug": lesson["slug"], "title": lesson["title"], "minutes": lesson.get("minutes"), "url": url}


def today_view(
    course: str, day: date, *, member: bool, state: dict[str, Any] | None = None, lesson: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Ответ ``/academy/rhythm/today``: не участнику — нули и ``lesson: null``."""
    state = state or {}
    marks = state.get("marks") or {}
    return {
        "ok": True,
        "member": member,
        "course_slug": course,
        "day": day.isoformat(),
        "marks": {field: bool(marks.get(field)) for field in FIELDS},
        "streak": int(state.get("streak") or 0),
        "broken": bool(state.get("broken")),
        "lesson": lesson if member else None,
    }


async def today_payload(tenant_id: str, telegram_user_id: int, *, today: date | None = None) -> dict[str, Any]:
    """Карточка «Сегодня» для сайта; не участник курса — ``member: false`` (200, не 403)."""
    day = today or local_today()
    course = course_slug()
    if not course or not await is_member(tenant_id, course, telegram_user_id):
        return today_view(course, day, member=False)
    return await _member_payload(tenant_id, telegram_user_id, course, day)


async def _member_payload(tenant_id: str, telegram_user_id: int, course: str, day: date) -> dict[str, Any]:
    state = await card_state(tenant_id, telegram_user_id, day, day)
    lesson = await lesson_of_day(tenant_id, course, telegram_user_id, lambda slug: site_lesson_path(course, slug))
    return today_view(course, day, member=True, state=state, lesson=lesson)


async def toggle_today(tenant_id: str, telegram_user_id: int, field: Any) -> dict[str, Any]:
    """Переключить галочку сегодняшнего дня (день — ``local_today()``) и вернуть карточку.

    400 ``bad_field`` — такой галочки нет; 403 ``rhythm_not_member`` — курс не открыт ученику.
    """
    if not isinstance(field, str) or field not in FIELDS:
        raise AcademyError(400, "bad_field")
    course = course_slug()
    if not course or not await is_member(tenant_id, course, telegram_user_id):
        raise AcademyError(403, "rhythm_not_member")
    day = local_today()
    await toggle_mark(tenant_id, telegram_user_id, day, field)
    return await _member_payload(tenant_id, telegram_user_id, course, day)
