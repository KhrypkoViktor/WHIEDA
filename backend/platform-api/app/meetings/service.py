"""Встречи: запросы к ``meetings`` (V43, 30.10.2026) и время по Москве.

Все запросы идут на подключении ``tenant_connection`` (RLS по арендатору) и всё равно фильтруют по ``tenant_id``.
Время: в базе ``timestamptz`` (UTC); человек видит и вводит Москву (Europe/Moscow, без перевода часов).

Ссылка входа (``join_url``) — внутренняя: публичный список читает отдельный набор колонок
(``_PUBLIC_COLUMNS``) и собирает ответ по белому списку полей (``public_meeting``), так что ссылка
в публичный ответ не попадёт даже по ошибке.

«Ближайшие» — запланированные (``status = 'planned'``), чей конец (начало + длительность) ещё впереди:
встреча, которая идёт прямо сейчас, остаётся в списке до окончания.

Функции записи не делают commit: его делает ``tenant_connection`` вызывающего. Публичный кэш (30 секунд)
сбрасывает вызывающий после commit — ``forget_public_meetings()``.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo

from app.db import fetch_all, fetch_one, tenant_connection
from app.ttl_cache import TtlCache

MSK = ZoneInfo("Europe/Moscow")

KINDS = ("live", "consult")
DEFAULT_TITLES = {"live": "Живая встреча", "consult": "Консультация"}
DEFAULT_DURATION_MIN = 60
MAX_TITLE_LEN = 200
MAX_URL_LEN = 500
LIST_LIMIT_MAX = 50
PUBLIC_LIMIT_MAX = 10

# Расписание по умолчанию: вторник и четверг, 19:30 по Москве.
DEFAULT_SLOT_WEEKDAYS = (1, 3)  # datetime.weekday(): 0 — понедельник
DEFAULT_SLOT_TIME = dt.time(19, 30)
DEFAULT_SLOTS_MAX = 52

_WEEKDAYS_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f]+$", re.IGNORECASE)

_COLUMNS = (
    "meeting_id, tenant_id, kind, title, starts_at, duration_min, join_url, hosts, "
    "status, recording_url, notes, created_by, created_at, updated_at"
)
_PUBLIC_COLUMNS = "meeting_id, kind, title, starts_at, duration_min, hosts"

PUBLIC_MEETINGS_TTL_SEC = 30.0
# (арендатор, kind) → до PUBLIC_LIMIT_MAX ближайших встреч в публичном виде (без join_url).
public_meetings_cache: TtlCache[list[dict[str, Any]]] = TtlCache(max_entries=64)


class MeetingError(Exception):
    """Отказ сервиса. ``code``: not_found, bad_status, duplicate, bad_kind, bad_url, bad_time, bad_duration.

    ``meeting`` — строка встречи, о которой речь (для bad_status и duplicate)."""

    def __init__(self, code: str, meeting: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.meeting = meeting


# ---- время -----------------------------------------------------------------------------------------


def utcnow() -> dt.datetime:
    # Отдельная функция, чтобы тесты подменяли «сейчас».
    return dt.datetime.now(dt.timezone.utc)


def as_msk(value: dt.datetime) -> dt.datetime:
    """Момент по Москве. Время без пояса считается московским."""
    if value.tzinfo is None:
        return value.replace(tzinfo=MSK)
    return value.astimezone(MSK)


def default_slots(from_dt: dt.datetime, count: int) -> list[dt.datetime]:
    """Ближайшие ``count`` вторников и четвергов 19:30 МСК строго после ``from_dt`` (по возрастанию)."""
    wanted = max(0, min(int(count), DEFAULT_SLOTS_MAX))
    start = as_msk(from_dt)
    day = start.date()
    slots: list[dt.datetime] = []
    while len(slots) < wanted:
        if day.weekday() in DEFAULT_SLOT_WEEKDAYS:
            slot = dt.datetime.combine(day, DEFAULT_SLOT_TIME, tzinfo=MSK)
            if slot > start:
                slots.append(slot)
        day += dt.timedelta(days=1)
    return slots


def resolve_msk_datetime(
    day: int, month: int, hour: int, minute: int, *, now: dt.datetime | None = None
) -> dt.datetime:
    """«05.11 19:30» → ближайший будущий такой момент по Москве (год подбирается сам).

    Неверная дата или время (31.02, 25:00) — ``MeetingError('bad_time')``. 29.02 уходит на ближайший високосный год."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise MeetingError("bad_time")
    current = as_msk(now or utcnow())
    for year in range(current.year, current.year + 9):
        try:
            candidate = dt.datetime(year, month, day, hour, minute, tzinfo=MSK)
        except ValueError:
            continue
        if candidate > current:
            return candidate
    raise MeetingError("bad_time")


def format_msk(value: dt.datetime, *, now: dt.datetime | None = None) -> str:
    """``чт 05.11 19:30 МСК``; год добавляется, только если это не текущий год."""
    local = as_msk(value)
    current_year = as_msk(now or utcnow()).year
    day = local.strftime("%d.%m") + (f".{local.year}" if local.year != current_year else "")
    return f"{_WEEKDAYS_RU[local.weekday()]} {day} {local:%H:%M} МСК"


# ---- проверка полей --------------------------------------------------------------------------------


def normalize_join_url(raw: str | None) -> str | None:
    """Адрес встречи: http(s), без пробелов, до 500 знаков, с хостом. Иначе None."""
    value = str(raw or "").strip()
    if not value or len(value) > MAX_URL_LEN or not _URL_RE.fullmatch(value):
        return None
    rest = value.split("://", 1)[1]
    host = re.split(r"[/?#]", rest, maxsplit=1)[0].rsplit("@", 1)[-1]
    return value if host.strip(".:") else None


def clean_title(raw: str | None, kind: str) -> str:
    title = " ".join(str(raw or "").split())[:MAX_TITLE_LEN].strip()
    return title or DEFAULT_TITLES.get(kind, "")


def _clean_hosts(hosts: Iterable[str] | None) -> list[str]:
    return [name for name in (" ".join(str(h).split()) for h in (hosts or ())) if name]


def has_link(row: dict[str, Any]) -> bool:
    return bool(str(row.get("join_url") or "").strip())


def public_meeting(row: dict[str, Any]) -> dict[str, Any]:
    """Строка встречи → публичный вид. Белый список полей: ссылка входа, запись и заметки сюда не попадают."""
    kind = str(row.get("kind") or "")
    return {
        "id": int(row["meeting_id"]),
        "title": str(row.get("title") or "").strip() or DEFAULT_TITLES.get(kind, ""),
        "starts_at": as_msk(row["starts_at"]).isoformat(timespec="seconds"),
        "duration_min": int(row["duration_min"]),
        "hosts": [str(name) for name in (row.get("hosts") or [])],
    }


# ---- чтение ----------------------------------------------------------------------------------------


def _check_kind(kind: str | None) -> None:
    if kind is not None and kind not in KINDS:
        raise MeetingError("bad_kind")


async def _upcoming(
    conn: Any, tenant: str, kind: str | None, limit: int, now: dt.datetime | None, columns: str
) -> list[dict[str, Any]]:
    _check_kind(kind)
    clauses = [
        "tenant_id = %s",
        "status = 'planned'",
        "starts_at + duration_min * interval '1 minute' > %s",
    ]
    params: list[Any] = [tenant, now or utcnow()]
    if kind is not None:
        clauses.append("kind = %s")
        params.append(kind)
    params.append(max(1, min(int(limit), LIST_LIMIT_MAX)))
    query = (
        f"select {columns} from meetings where {' and '.join(clauses)} "
        "order by starts_at, meeting_id limit %s"
    )
    return list(await fetch_all(conn, query, tuple(params)))


async def list_upcoming(
    conn: Any, tenant: str, kind: str | None, limit: int, *, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Ближайшие запланированные встречи (``kind=None`` — любые), целые строки: для владельца в боте."""
    return await _upcoming(conn, tenant, kind, limit, now, _COLUMNS)


async def list_public_upcoming(
    conn: Any, tenant: str, kind: str | None, limit: int, *, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """То же, но только колонки публичного списка: ссылка входа из базы даже не читается."""
    return await _upcoming(conn, tenant, kind, limit, now, _PUBLIC_COLUMNS)


async def get_meeting(conn: Any, tenant: str, meeting_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"select {_COLUMNS} from meetings where tenant_id = %s and meeting_id = %s",
        (tenant, int(meeting_id)),
    )


async def load_public_meetings(tenant: str, kind: str) -> list[dict[str, Any]]:
    """Публичный список для сайта: до ``PUBLIC_LIMIT_MAX`` ближайших, в памяти процесса 30 секунд."""
    key = (tenant, kind)
    cached = public_meetings_cache.get(key)
    if cached is None:
        async with tenant_connection(tenant) as conn:
            rows = await list_public_upcoming(conn, tenant, kind, PUBLIC_LIMIT_MAX)
        cached = [public_meeting(row) for row in rows]
        public_meetings_cache.set(key, cached, ttl=PUBLIC_MEETINGS_TTL_SEC)
    # Копии: вызывающий может менять ответ, кэш от этого не портится.
    return [{**item, "hosts": list(item["hosts"])} for item in cached]


def forget_public_meetings() -> None:
    """Встречи изменились: следующий запрос сайта прочитает базу заново (вызывать после commit)."""
    public_meetings_cache.clear()


# ---- запись ----------------------------------------------------------------------------------------


async def create_meeting(
    conn: Any,
    tenant: str,
    *,
    starts_at: dt.datetime,
    kind: str = "live",
    title: str | None = None,
    duration_min: int = DEFAULT_DURATION_MIN,
    join_url: str | None = None,
    hosts: Iterable[str] | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Новая встреча. Если на это время уже есть не отменённая встреча того же вида —
    ``MeetingError('duplicate', <та встреча>)``: повтор команды или повторная доставка сообщения не плодит дубли."""
    if kind not in KINDS:
        raise MeetingError("bad_kind")
    if not 1 <= int(duration_min) <= 1440:
        raise MeetingError("bad_duration")
    url = None
    if join_url is not None and str(join_url).strip():
        url = normalize_join_url(join_url)
        if url is None:
            raise MeetingError("bad_url")
    moment = as_msk(starts_at)
    same = await fetch_one(
        conn,
        f"select {_COLUMNS} from meetings "
        "where tenant_id = %s and kind = %s and starts_at = %s and status <> 'cancelled' "
        "order by meeting_id limit 1",
        (tenant, kind, moment),
    )
    if same:
        raise MeetingError("duplicate", same)
    row = await fetch_one(
        conn,
        "insert into meetings (tenant_id, kind, title, starts_at, duration_min, join_url, hosts, notes, created_by) "
        f"values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning {_COLUMNS}",
        (
            tenant,
            kind,
            clean_title(title, kind),
            moment,
            int(duration_min),
            url,
            _clean_hosts(hosts),
            (str(notes).strip() or None) if notes else None,
            created_by,
        ),
    )
    if row is None:  # insert … returning всегда отдаёт строку; пустой ответ — сбой драйвера
        raise RuntimeError("meetings insert returned no row")
    return row


async def create_default_slots(
    conn: Any,
    tenant: str,
    count: int,
    *,
    now: dt.datetime | None = None,
    created_by: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``count`` ближайших слотов расписания по умолчанию → (созданные, уже существовавшие).

    Слот, на который уже есть живая встреча, пропускается: команда безопасна при повторе."""
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for slot in default_slots(now or utcnow(), count):
        try:
            created.append(await create_meeting(conn, tenant, starts_at=slot, kind="live", created_by=created_by))
        except MeetingError as exc:
            if exc.code != "duplicate" or exc.meeting is None:
                raise
            skipped.append(exc.meeting)
    return created, skipped


async def _explain_missing(conn: Any, tenant: str, meeting_id: int) -> MeetingError:
    existing = await get_meeting(conn, tenant, meeting_id)
    if existing is None:
        return MeetingError("not_found")
    return MeetingError("bad_status", existing)


async def set_join_url(conn: Any, tenant: str, meeting_id: int, url: str) -> dict[str, Any]:
    """Задать или заменить ссылку входа у запланированной встречи."""
    value = normalize_join_url(url)
    if value is None:
        raise MeetingError("bad_url")
    row = await fetch_one(
        conn,
        "update meetings set join_url = %s, updated_at = now() "
        f"where tenant_id = %s and meeting_id = %s and status = 'planned' returning {_COLUMNS}",
        (value, tenant, int(meeting_id)),
    )
    if row is None:
        raise await _explain_missing(conn, tenant, meeting_id)
    return row


async def _finish(conn: Any, tenant: str, meeting_id: int, status: str) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        "update meetings set status = %s, updated_at = now() "
        f"where tenant_id = %s and meeting_id = %s and status = 'planned' returning {_COLUMNS}",
        (status, tenant, int(meeting_id)),
    )
    if row is None:
        raise await _explain_missing(conn, tenant, meeting_id)
    return row


async def cancel_meeting(conn: Any, tenant: str, meeting_id: int) -> dict[str, Any]:
    """Запланированная → отменена. Прошедшую или уже отменённую не трогает (``bad_status``)."""
    return await _finish(conn, tenant, meeting_id, "cancelled")


async def mark_done(conn: Any, tenant: str, meeting_id: int) -> dict[str, Any]:
    """Запланированная → прошла. Отменённую или уже отмеченную не трогает (``bad_status``)."""
    return await _finish(conn, tenant, meeting_id, "done")
