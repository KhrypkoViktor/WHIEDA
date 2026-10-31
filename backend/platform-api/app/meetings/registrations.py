"""Запись на Живую встречу: запросы к ``meeting_registrations`` (V46, 30.10.2026).

Один человек (мессенджер + его id) — одна активная запись на одну встречу. «Активная» — любая, кроме
``moved`` (перенёс) и ``cancelled`` (отменил); это же условие у уникального индекса таблицы.

Встреча годится для записи, если она живая (``kind = 'live'``), запланированная и ещё не началась —
то же правило, что у заявки с выбранной встречей (``meeting_problem``). Перенести или отменить можно
запись, пока встреча не началась, а если её отменил владелец — в любой момент.

Все функции работают на открытом ``tenant_connection`` вызывающего (RLS по арендатору, фильтр по
``tenant_id`` всё равно есть) и сами не делают commit. Ссылка входа на встречу (``join_url``) читается
только в ``join`` — по нажатию «Присоединиться» (срез 5).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from app.db import fetch_all, fetch_one
from app.leads.meeting_leads import meeting_problem
from app.meetings.service import utcnow

CHANNELS = ("telegram", "max")
INACTIVE_STATUSES = ("moved", "cancelled")

# «Присоединиться» работает с этого момента до конца встречи: напоминание с кнопкой уходит за час, запас — на опоздавший воркер.
JOIN_OPENS_BEFORE = timedelta(minutes=75)

_MEETING_COLUMNS = "meeting_id, kind, title, starts_at, duration_min, status"
_REGISTRATION_COLUMNS = (
    "registration_id, meeting_id, lead_id::text as lead_id, channel, channel_user_id, status, "
    "registered_at, joined_at, moved_from"
)
# Встреча для кнопки «Присоединиться»: те же колонки и ссылка входа (наружу из бота уходит только при нажатии).
_JOIN_MEETING_COLUMNS = _MEETING_COLUMNS + ", join_url"
# Запись вместе со встречей одним запросом: колонки встречи с приставкой m_.
_JOINED_COLUMNS = (
    "r.registration_id, r.meeting_id, r.lead_id::text as lead_id, r.channel, r.channel_user_id, r.status, "
    "r.registered_at, r.joined_at, r.moved_from, "
    "m.kind as m_kind, m.title as m_title, m.starts_at as m_starts_at, "
    "m.duration_min as m_duration_min, m.status as m_status"
)


@dataclass(frozen=True)
class Registration:
    registration_id: int
    meeting_id: int
    lead_id: str | None
    channel: str
    channel_user_id: int
    status: str
    registered_at: datetime | None = None
    joined_at: datetime | None = None
    moved_from: int | None = None  # registration_id прежней записи, если эта появилась при переносе


@dataclass(frozen=True)
class RegisterOutcome:
    """``registered`` — записали; ``already`` — активная запись на эту встречу уже есть;
    ``closed`` — встреча не годится (не живая, отменена, прошла или началась); ``not_found`` — такой встречи нет."""

    status: Literal["registered", "already", "closed", "not_found"]
    registration: Registration | None = None
    meeting: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChangeOutcome:
    """Перенос или отмена записи.

    ``moved`` / ``cancelled`` — сделано; ``not_found`` — записи нет или она чужая; ``not_active`` — запись уже
    перенесена, отменена или встреча состоялась; ``too_late`` — встреча уже началась; ``closed`` — встреча,
    на которую переносят, не годится; ``same`` — перенос на ту же встречу; ``already`` — на ту встречу человек
    уже записан (прежняя запись не тронута).

    ``registration`` — новая запись (при переносе) или отменённая (при отмене); ``old_registration`` — прежняя;
    ``meeting`` — встреча новой записи (при отмене — отменённая); ``old_meeting`` — прежняя встреча."""

    status: Literal["moved", "cancelled", "not_found", "not_active", "too_late", "closed", "same", "already"]
    registration: Registration | None = None
    old_registration: Registration | None = None
    meeting: dict[str, Any] | None = None
    old_meeting: dict[str, Any] | None = None


@dataclass(frozen=True)
class JoinOutcome:
    """Нажатие «Присоединиться».

    ``joined`` — первое нажатие в окне (запись стала ``joined``); ``again`` — повторное (ссылка та же);
    ``too_early`` / ``too_late`` — не в окне; ``cancelled`` — встречу отменили; ``stale`` — запись уже перенесена,
    отменена или встреча состоялась; ``not_found`` — записи нет или она чужая. ``meeting`` — встреча со ссылкой входа."""

    status: Literal["joined", "again", "too_early", "too_late", "cancelled", "stale", "not_found"]
    registration: Registration | None = None
    meeting: dict[str, Any] | None = None


def _registration(row: dict[str, Any]) -> Registration:
    return Registration(
        registration_id=int(row["registration_id"]),
        meeting_id=int(row["meeting_id"]),
        lead_id=row.get("lead_id") or None,
        channel=str(row["channel"]),
        channel_user_id=int(row["channel_user_id"]),
        status=str(row["status"]),
        registered_at=row.get("registered_at"),
        joined_at=row.get("joined_at"),
        moved_from=int(row["moved_from"]) if row.get("moved_from") is not None else None,
    )


def _check_channel(channel: str) -> None:
    if channel not in CHANNELS:
        raise ValueError("channel must be telegram or max")


def changeable(meeting: dict[str, Any] | None, now: datetime) -> bool:
    """Запись на эту встречу ещё можно перенести или отменить: встреча не началась или её отменили."""
    if meeting is None or meeting.get("status") == "cancelled":
        return True
    starts_at = meeting.get("starts_at")
    return meeting.get("status") == "planned" and isinstance(starts_at, datetime) and starts_at > now


# ---- чтение ----------------------------------------------------------------------------------------


async def get_meeting_row(conn: Any, tenant: str, meeting_id: int) -> dict[str, Any] | None:
    """Встреча без ссылки входа и заметок."""
    return await fetch_one(
        conn,
        f"select {_MEETING_COLUMNS} from meetings where tenant_id = %s and meeting_id = %s",
        (tenant, int(meeting_id)),
    )


async def open_meetings(
    conn: Any,
    tenant: str,
    *,
    limit: int,
    exclude_meeting_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Ближайшие живые встречи, на которые можно записаться: запланированные и ещё не начавшиеся."""
    clauses = ["tenant_id = %s", "kind = 'live'", "status = 'planned'", "starts_at > %s"]
    params: list[Any] = [tenant, now or utcnow()]
    if exclude_meeting_id is not None:
        clauses.append("meeting_id <> %s")
        params.append(int(exclude_meeting_id))
    params.append(max(1, min(int(limit), 10)))
    return list(
        await fetch_all(
            conn,
            f"select {_MEETING_COLUMNS} from meetings where {' and '.join(clauses)} "
            "order by starts_at, meeting_id limit %s",
            tuple(params),
        )
    )


async def find_active(
    conn: Any, tenant: str, *, meeting_id: int, channel: str, channel_user_id: int
) -> Registration | None:
    """Активная запись человека на эту встречу или None."""
    _check_channel(channel)
    row = await fetch_one(
        conn,
        f"""
        select {_REGISTRATION_COLUMNS} from meeting_registrations
        where tenant_id = %s and meeting_id = %s and channel = %s and channel_user_id = %s
          and status not in ('moved', 'cancelled')
        order by registration_id
        limit 1
        """,
        (tenant, int(meeting_id), channel, int(channel_user_id)),
    )
    return _registration(row) if row else None


async def get_registration(
    conn: Any, tenant: str, *, registration_id: int, channel: str, channel_user_id: int
) -> Registration | None:
    """Запись по номеру, если она принадлежит этому человеку (чужая или несуществующая — None). Без блокировки строки."""
    _check_channel(channel)
    row = await fetch_one(
        conn,
        f"""
        select {_REGISTRATION_COLUMNS} from meeting_registrations
        where tenant_id = %s and registration_id = %s and channel = %s and channel_user_id = %s
        """,
        (tenant, int(registration_id), channel, int(channel_user_id)),
    )
    return _registration(row) if row else None


async def active_registrations(
    conn: Any,
    tenant: str,
    *,
    channel: str,
    channel_user_id: int,
    limit: int = 3,
    now: datetime | None = None,
) -> list[tuple[Registration, dict[str, Any]]]:
    """Записи человека со статусом ``registered`` на запланированные и ещё не начавшиеся встречи,
    ближайшая первой: ``(запись, встреча)``."""
    _check_channel(channel)
    rows = await fetch_all(
        conn,
        f"""
        select {_JOINED_COLUMNS}
        from meeting_registrations r
        join meetings m on m.tenant_id = r.tenant_id and m.meeting_id = r.meeting_id
        where r.tenant_id = %s and r.channel = %s and r.channel_user_id = %s
          and r.status = 'registered' and m.status = 'planned' and m.starts_at > %s
        order by m.starts_at, r.registration_id
        limit %s
        """,
        (tenant, channel, int(channel_user_id), now or utcnow(), max(1, min(int(limit), 10))),
    )
    return [
        (
            _registration(row),
            {
                "meeting_id": int(row["meeting_id"]),
                "kind": row["m_kind"],
                "title": row["m_title"],
                "starts_at": row["m_starts_at"],
                "duration_min": row["m_duration_min"],
                "status": row["m_status"],
            },
        )
        for row in rows
    ]


async def person_has_registration(conn: Any, tenant: str, *, channel: str, channel_user_id: int) -> bool:
    """Есть ли у человека хоть одна запись на встречу (любого статуса): этого достаточно, чтобы считать его гостем."""
    _check_channel(channel)
    row = await fetch_one(
        conn,
        """
        select 1 as found from meeting_registrations
        where tenant_id = %s and channel = %s and channel_user_id = %s
        limit 1
        """,
        (tenant, channel, int(channel_user_id)),
    )
    return row is not None


# ---- запись ----------------------------------------------------------------------------------------


async def _insert_active(
    conn: Any,
    tenant: str,
    *,
    meeting_id: int,
    channel: str,
    channel_user_id: int,
    lead_id: str | None,
    moved_from: int | None = None,
) -> Registration | None:
    """Новая активная запись; None — такая уже есть (уникальный индекс не пустил)."""
    row = await fetch_one(
        conn,
        f"""
        insert into meeting_registrations (tenant_id, meeting_id, lead_id, channel, channel_user_id, moved_from)
        values (%s, %s, %s::uuid, %s, %s, %s)
        on conflict (tenant_id, meeting_id, channel, channel_user_id)
          where status not in ('moved', 'cancelled')
        do nothing
        returning {_REGISTRATION_COLUMNS}
        """,
        (tenant, int(meeting_id), lead_id, channel, int(channel_user_id), moved_from),
    )
    return _registration(row) if row else None


async def register(
    conn: Any,
    tenant: str,
    *,
    meeting_id: int,
    channel: str,
    channel_user_id: int,
    lead_id: str | None = None,
    now: datetime | None = None,
) -> RegisterOutcome:
    """Записать человека на встречу. Повтор безопасен: вторая запись не создаётся (``already``)."""
    _check_channel(channel)
    current = now or utcnow()
    meeting = await get_meeting_row(conn, tenant, meeting_id)
    problem = meeting_problem(meeting, current)
    if problem == "not_found":
        return RegisterOutcome("not_found")
    if problem is not None:
        return RegisterOutcome("closed", None, meeting)
    created = await _insert_active(
        conn, tenant, meeting_id=meeting_id, channel=channel, channel_user_id=channel_user_id, lead_id=lead_id
    )
    if created is not None:
        return RegisterOutcome("registered", created, meeting)
    existing = await find_active(conn, tenant, meeting_id=meeting_id, channel=channel, channel_user_id=channel_user_id)
    return RegisterOutcome("already", existing, meeting)


async def _lock_own_registration(
    conn: Any, tenant: str, registration_id: int, channel: str, channel_user_id: int
) -> Registration | None:
    """Запись, которая принадлежит этому человеку; строка заблокирована до конца транзакции."""
    row = await fetch_one(
        conn,
        f"""
        select {_REGISTRATION_COLUMNS} from meeting_registrations
        where tenant_id = %s and registration_id = %s and channel = %s and channel_user_id = %s
        for update
        """,
        (tenant, int(registration_id), channel, int(channel_user_id)),
    )
    return _registration(row) if row else None


async def _set_status(conn: Any, tenant: str, registration_id: int, status: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update meeting_registrations set status = %s, updated_at = now() "
            "where tenant_id = %s and registration_id = %s",
            (status, tenant, int(registration_id)),
        )


async def move(
    conn: Any,
    tenant: str,
    *,
    registration_id: int,
    to_meeting_id: int,
    channel: str,
    channel_user_id: int,
    now: datetime | None = None,
) -> ChangeOutcome:
    """Перенос: прежняя запись получает ``moved``, на новую встречу создаётся ``registered`` с ``moved_from``."""
    _check_channel(channel)
    current = now or utcnow()
    old = await _lock_own_registration(conn, tenant, registration_id, channel, channel_user_id)
    if old is None:
        return ChangeOutcome("not_found")
    if old.status != "registered":
        return ChangeOutcome("not_active", old_registration=old)
    old_meeting = await get_meeting_row(conn, tenant, old.meeting_id)
    if not changeable(old_meeting, current):
        return ChangeOutcome("too_late", old_registration=old, old_meeting=old_meeting)
    if int(to_meeting_id) == old.meeting_id:
        return ChangeOutcome("same", old_registration=old, meeting=old_meeting, old_meeting=old_meeting)
    target = await get_meeting_row(conn, tenant, to_meeting_id)
    if meeting_problem(target, current) is not None:
        return ChangeOutcome("closed", old_registration=old, meeting=target, old_meeting=old_meeting)
    created = await _insert_active(
        conn,
        tenant,
        meeting_id=int(to_meeting_id),
        channel=channel,
        channel_user_id=channel_user_id,
        lead_id=old.lead_id,
        moved_from=old.registration_id,
    )
    if created is None:
        return ChangeOutcome("already", old_registration=old, meeting=target, old_meeting=old_meeting)
    await _set_status(conn, tenant, old.registration_id, "moved")
    return ChangeOutcome("moved", created, replace(old, status="moved"), target, old_meeting)


async def cancel(
    conn: Any,
    tenant: str,
    *,
    registration_id: int,
    channel: str,
    channel_user_id: int,
    now: datetime | None = None,
) -> ChangeOutcome:
    """Отмена записи: ``cancelled``. Запись остаётся в таблице — для цифр воронки."""
    _check_channel(channel)
    current = now or utcnow()
    old = await _lock_own_registration(conn, tenant, registration_id, channel, channel_user_id)
    if old is None:
        return ChangeOutcome("not_found")
    if old.status != "registered":
        return ChangeOutcome("not_active", old_registration=old)
    meeting = await get_meeting_row(conn, tenant, old.meeting_id)
    if not changeable(meeting, current):
        return ChangeOutcome("too_late", old_registration=old, meeting=meeting, old_meeting=meeting)
    await _set_status(conn, tenant, old.registration_id, "cancelled")
    done = replace(old, status="cancelled")
    return ChangeOutcome("cancelled", done, done, meeting, meeting)


async def join(
    conn: Any,
    tenant: str,
    *,
    registration_id: int,
    channel: str,
    channel_user_id: int,
    now: datetime | None = None,
) -> JoinOutcome:
    """«Присоединиться»: в окне (за ``JOIN_OPENS_BEFORE`` до начала и до конца встречи) первое нажатие ставит
    ``joined`` и ``joined_at``; повторное ничего не меняет. Вне окна запись не трогается. Ссылка входа — в ``meeting``."""
    _check_channel(channel)
    current = now or utcnow()
    reg = await _lock_own_registration(conn, tenant, registration_id, channel, channel_user_id)
    if reg is None:
        return JoinOutcome("not_found")
    if reg.status not in ("registered", "joined"):
        return JoinOutcome("stale", reg)
    meeting = await fetch_one(
        conn,
        f"select {_JOIN_MEETING_COLUMNS} from meetings where tenant_id = %s and meeting_id = %s",
        (tenant, reg.meeting_id),
    )
    if meeting is None:
        return JoinOutcome("stale", reg)
    if meeting["status"] == "cancelled":
        return JoinOutcome("cancelled", reg, meeting)
    starts_at = meeting["starts_at"]
    ends_at = starts_at + timedelta(minutes=int(meeting["duration_min"]))
    if meeting["status"] != "planned" or current >= ends_at:
        return JoinOutcome("too_late", reg, meeting)
    if current < starts_at - JOIN_OPENS_BEFORE:
        return JoinOutcome("too_early", reg, meeting)
    if reg.status == "joined":
        return JoinOutcome("again", reg, meeting)
    async with conn.cursor() as cur:
        await cur.execute(
            "update meeting_registrations set status = 'joined', joined_at = %s, updated_at = now() "
            "where tenant_id = %s and registration_id = %s and status = 'registered'",
            (current, tenant, reg.registration_id),
        )
    return JoinOutcome("joined", replace(reg, status="joined", joined_at=current), meeting)
