"""Трекер привычек: база и ответы (ТЗ §6–7). Подсчёт и проверки — ``app.habits.rules``, без базы.

Все изменения человека идут под блокировкой ``pg_advisory_xact_lock`` на (тенант, человек): лимит
в 7 активных привычек не обходится двумя одновременными запросами. День считается по поясу
человека (``tz`` с каждым запросом, последний запоминается в ``habit_profile``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from app.db import fetch_all, fetch_one, tenant_connection
from app.habits import rules
from app.habits.rules import HabitSpec, HabitsError
from app.settings import get_settings

_COLUMNS = "habit_id, template, title, rhythm, goal, params, start_day, rhythm_changed_on, status, position, created_at"
_FALLBACK_TZ = "Europe/Minsk"


def now_utc() -> datetime:
    """Единственное место, где сервис берёт «сейчас»; тесты подменяют его."""
    return datetime.now(timezone.utc)


def default_tz() -> str:
    name = str(get_settings().platform_rhythm_timezone or "").strip()
    try:
        return rules.parse_tz(name) or _FALLBACK_TZ
    except HabitsError:
        return _FALLBACK_TZ


# ------------------------------------------------------------------ контекст человека


async def _lock(conn: Any, tenant_id: str, uid: int) -> None:
    await fetch_one(conn, "select pg_advisory_xact_lock(hashtextextended(%s, 0)) as locked", (f"habits:{tenant_id}:{uid}",))


async def _context(conn: Any, tenant_id: str, uid: int, tz_param: Any) -> tuple[str, date, date]:
    """Пояс (из запроса — и он запоминается; иначе последний; иначе общий по умолчанию), сегодня, вчера."""
    asked = rules.parse_tz(tz_param)
    stored = await fetch_one(
        conn, "select tz from habit_profile where tenant_id = %s and telegram_user_id = %s", (tenant_id, uid)
    )
    saved = stored["tz"] if stored else None
    if asked and asked != saved:
        await conn.execute(
            """
            insert into habit_profile (tenant_id, telegram_user_id, tz) values (%s, %s, %s)
            on conflict (tenant_id, telegram_user_id) do update set tz = excluded.tz, updated_at = now()
            """,
            (tenant_id, uid, asked),
        )
    tz = asked or saved or default_tz()
    try:
        today, yesterday = rules.person_days(now_utc(), tz)
    except Exception:  # запомненный пояс, которого нет в базе поясов сервера
        tz = default_tz()
        today, yesterday = rules.person_days(now_utc(), tz)
    return tz, today, yesterday


async def _rows(conn: Any, tenant_id: str, uid: int, habit_id: str | None = None) -> list[dict[str, Any]]:
    extra, params = ("and habit_id = %s::uuid", (habit_id,)) if habit_id else ("", ())
    return await fetch_all(
        conn,
        f"""
        select {_COLUMNS} from habits
        where tenant_id = %s and telegram_user_id = %s and deleted_at is null {extra}
        order by (status = 'paused'), position, created_at
        """,
        (tenant_id, uid, *params),
    )


async def _history(conn: Any, ids: list[Any]) -> tuple[dict[Any, set[date]], dict[Any, list[tuple[date, date | None]]]]:
    marks: dict[Any, set[date]] = {i: set() for i in ids}
    pauses: dict[Any, list[tuple[date, date | None]]] = {i: [] for i in ids}
    if not ids:
        return marks, pauses
    for row in await fetch_all(conn, "select habit_id, day from habit_marks where habit_id = any(%s)", (ids,)):
        marks[row["habit_id"]].add(row["day"])
    for row in await fetch_all(
        conn, "select habit_id, from_day, to_day from habit_pauses where habit_id = any(%s)", (ids,)
    ):
        pauses[row["habit_id"]].append((row["from_day"], row["to_day"]))
    return marks, pauses


def _spec(row: dict[str, Any], pauses: list[tuple[date, date | None]]) -> HabitSpec:
    return HabitSpec(row["rhythm"], int(row["goal"]), row["start_day"], row["rhythm_changed_on"], tuple(pauses))


def _view(row: dict[str, Any], marks: set[date], pauses: list, today: date, yesterday: date) -> dict[str, Any]:
    t = rules.tally(_spec(row, pauses), marks, today)
    return {
        "id": str(row["habit_id"]),
        "template": row["template"],
        "title": row["title"],
        "rhythm": row["rhythm"],
        "goal": int(row["goal"]),
        "params": row["params"] or {},
        "status": row["status"],
        "start_day": row["start_day"].isoformat(),
        "marks": {"today": today in marks, "yesterday": yesterday in marks},
        "period": None if row["rhythm"] == "daily" else {"done": t.period_done, "goal": t.period_goal},
        "streak": t.streak,
    }


def _progress_item(row: dict[str, Any], marks: set[date], pauses: list, today: date) -> dict[str, Any]:
    t = rules.tally(_spec(row, pauses), marks, today)
    return {
        "id": str(row["habit_id"]),
        "template": row["template"],
        "title": row["title"],
        "rhythm": row["rhythm"],
        "goal": int(row["goal"]),
        "params": row["params"] or {},
        "status": row["status"],
        "period": t.period,
        "passed": t.passed,
        "done": t.done,
        "missed": t.missed,
        "streak": t.streak,
        "paused": row["status"] == "paused",
    }


async def _one(conn: Any, tenant_id: str, uid: int, habit_id: str) -> dict[str, Any]:
    """Своя привычка; чужая, удалённая и не-UUID — одинаково 404 ``habit_not_found``."""
    try:
        uuid.UUID(str(habit_id))
    except ValueError as exc:
        raise HabitsError(404, "habit_not_found") from exc
    rows = await _rows(conn, tenant_id, uid, str(habit_id))
    if not rows:
        raise HabitsError(404, "habit_not_found")
    return rows[0]


async def _view_one(conn: Any, row: dict[str, Any], today: date, yesterday: date) -> dict[str, Any]:
    marks, pauses = await _history(conn, [row["habit_id"]])
    return _view(row, marks[row["habit_id"]], pauses[row["habit_id"]], today, yesterday)


async def _active_count(conn: Any, tenant_id: str, uid: int) -> int:
    row = await fetch_one(
        conn,
        """
        select count(*)::int as n from habits
        where tenant_id = %s and telegram_user_id = %s and deleted_at is null and status = 'active'
        """,
        (tenant_id, uid),
    )
    return int(row["n"])


# ------------------------------------------------------------------ запросы


async def list_habits(tenant_id: str, uid: int, tz_param: Any) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        tz, today, yesterday = await _context(conn, tenant_id, uid, tz_param)
        rows = await _rows(conn, tenant_id, uid)
        marks, pauses = await _history(conn, [row["habit_id"] for row in rows])
    habits = [_view(r, marks[r["habit_id"]], pauses[r["habit_id"]], today, yesterday) for r in rows]
    return {
        "ok": True,
        "enabled": True,
        "tz": tz,
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "limit": rules.LIMIT,
        "active_count": sum(1 for h in habits if h["status"] == "active"),
        "habits": habits,
    }


async def progress(tenant_id: str, uid: int, tz_param: Any) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        tz, today, _ = await _context(conn, tenant_id, uid, tz_param)
        rows = await _rows(conn, tenant_id, uid)
        marks, pauses = await _history(conn, [row["habit_id"] for row in rows])
    return {
        "ok": True,
        "enabled": True,
        "tz": tz,
        "today": today.isoformat(),
        "habits": [_progress_item(r, marks[r["habit_id"]], pauses[r["habit_id"]], today) for r in rows],
    }


async def add_habit(tenant_id: str, uid: int, body: dict[str, Any]) -> dict[str, Any]:
    clean = rules.clean_new(body)
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        _, today, yesterday = await _context(conn, tenant_id, uid, body.get("tz"))
        if await _active_count(conn, tenant_id, uid) >= rules.LIMIT:
            raise HabitsError(409, "habit_limit")
        nxt = await fetch_one(
            conn,
            """
            select coalesce(max(position), -1) + 1 as position from habits
            where tenant_id = %s and telegram_user_id = %s and deleted_at is null
            """,
            (tenant_id, uid),
        )
        row = await fetch_one(
            conn,
            f"""
            insert into habits (tenant_id, telegram_user_id, template, title, rhythm, goal, params, start_day, position)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning {_COLUMNS}
            """,
            (tenant_id, uid, clean["template"], clean["title"], clean["rhythm"], clean["goal"],
             Jsonb(clean["params"]), today, min(int(nxt["position"]), 32000)),
        )
        return _view(row, set(), [], today, yesterday)


async def update_habit(tenant_id: str, uid: int, habit_id: str, body: dict[str, Any]) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        _, today, yesterday = await _context(conn, tenant_id, uid, body.get("tz"))
        row = await _one(conn, tenant_id, uid, habit_id)
        patch = rules.clean_patch(row, body)
        sets, values = [], []
        for field in ("rhythm", "goal", "title"):
            if field in patch:
                sets.append(f"{field} = %s")
                values.append(patch[field])
        if "params" in patch:
            sets.append("params = %s")
            values.append(Jsonb(patch["params"]))
        if patch["rhythm_changed"]:
            sets.append("rhythm_changed_on = %s")
            values.append(today)
        if sets:
            row = await fetch_one(
                conn,
                f"update habits set {', '.join(sets)}, updated_at = now() where habit_id = %s::uuid returning {_COLUMNS}",
                (*values, habit_id),
            )
        return await _view_one(conn, row, today, yesterday)


async def pause_habit(tenant_id: str, uid: int, habit_id: str, tz_param: Any) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        _, today, yesterday = await _context(conn, tenant_id, uid, tz_param)
        row = await _one(conn, tenant_id, uid, habit_id)
        if row["status"] == "active":
            row = await fetch_one(
                conn,
                f"update habits set status = 'paused', updated_at = now() where habit_id = %s::uuid returning {_COLUMNS}",
                (habit_id,),
            )
            await conn.execute(
                """
                insert into habit_pauses (habit_id, from_day, to_day, tenant_id) values (%s::uuid, %s, null, %s)
                on conflict (habit_id, from_day) do update set to_day = null
                """,
                (habit_id, today, tenant_id),
            )
        return await _view_one(conn, row, today, yesterday)


async def resume_habit(tenant_id: str, uid: int, habit_id: str, tz_param: Any) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        _, today, yesterday = await _context(conn, tenant_id, uid, tz_param)
        row = await _one(conn, tenant_id, uid, habit_id)
        if row["status"] == "paused":
            if await _active_count(conn, tenant_id, uid) >= rules.LIMIT:
                raise HabitsError(409, "habit_limit")
            row = await fetch_one(
                conn,
                f"update habits set status = 'active', updated_at = now() where habit_id = %s::uuid returning {_COLUMNS}",
                (habit_id,),
            )
            await conn.execute(
                "update habit_pauses set to_day = %s where habit_id = %s::uuid and to_day is null", (today, habit_id)
            )
            # Пауза, закрытая в день начала (или раньше после смены пояса), ничего не охватывает.
            await conn.execute("delete from habit_pauses where habit_id = %s::uuid and to_day <= from_day", (habit_id,))
        return await _view_one(conn, row, today, yesterday)


async def delete_habit(tenant_id: str, uid: int, habit_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        await _one(conn, tenant_id, uid, habit_id)
        await conn.execute(
            "update habits set deleted_at = now(), updated_at = now() where habit_id = %s::uuid", (habit_id,)
        )
    return {"ok": True}


async def set_mark(tenant_id: str, uid: int, habit_id: str, day_raw: Any, done: Any, tz_param: Any) -> dict[str, Any]:
    day = rules.parse_day(day_raw)
    if not isinstance(done, bool):
        raise HabitsError(400, "bad_params")
    async with tenant_connection(tenant_id) as conn:
        await _lock(conn, tenant_id, uid)
        _, today, yesterday = await _context(conn, tenant_id, uid, tz_param)
        row = await _one(conn, tenant_id, uid, habit_id)
        rules.check_mark_day(day, today=today, start_day=row["start_day"])
        if done:
            await conn.execute(
                """
                insert into habit_marks (habit_id, day, tenant_id, telegram_user_id) values (%s::uuid, %s, %s, %s)
                on conflict (habit_id, day) do nothing
                """,
                (habit_id, day, tenant_id, uid),
            )
        else:
            await conn.execute("delete from habit_marks where habit_id = %s::uuid and day = %s", (habit_id, day))
        return await _view_one(conn, row, today, yesterday)
