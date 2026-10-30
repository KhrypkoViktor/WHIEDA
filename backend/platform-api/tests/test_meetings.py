"""Встречи WWC (V43, 30.10.2026): разбор команд владельца и дат, расписание по умолчанию, ответы бота,
публичный список. База — подделка в памяти (``FakeMeetings``), настоящий SQL, RLS и миграция —
tests/test_meetings_postgres.py.
"""

from __future__ import annotations

import datetime as dt
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.meetings import service
from app.meetings.service import MeetingError
from app.settings import get_settings
from app.telegram import meetings_admin, processor
from app.telegram.meetings_admin import MeetingCommand, parse_command, try_handle_meetings_admin_message
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramMessage

UTC = dt.timezone.utc
MSK = service.MSK
OWNER = 7001
STRANGER = 9999
TENANT = SimpleNamespace(tenant_id="whieda")
# Пятница 30.10.2026, 15:00 по Москве.
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
ZOOM = "https://us02web.zoom.us/j/81234567890?pwd=SeCrEt"


def msk(year, month, day, hour=19, minute=30) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=MSK)


# ---- разбор команд ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("встречи", MeetingCommand("list")),
    ("Встречи", MeetingCommand("list")),
    ("  ВСТРЕЧИ  ", MeetingCommand("list")),
    ("встречи создать 4", MeetingCommand("slots", count=4)),
    ("Встречи  создать   10", MeetingCommand("slots", count=10)),
    ("встречи создать 1", MeetingCommand("slots", count=1)),
    ("встреча 05.11 19:30", MeetingCommand("create", day=5, month=11, hour=19, minute=30)),
    ("встреча 5.11 9:05", MeetingCommand("create", day=5, month=11, hour=9, minute=5)),
    ("Встреча 05.11 19:30 Разбор партнёров",
     MeetingCommand("create", day=5, month=11, hour=19, minute=30, title="Разбор партнёров")),
    ("встреча 05.11 19:30 https://zoom.us/j/1",
     MeetingCommand("create", day=5, month=11, hour=19, minute=30, join_url="https://zoom.us/j/1")),
    ("встреча 05.11 19:30 https://zoom.us/j/1?pwd=Ab Живая встреча про CRM",
     MeetingCommand("create", day=5, month=11, hour=19, minute=30, join_url="https://zoom.us/j/1?pwd=Ab",
                    title="Живая встреча про CRM")),
    ("встреча 05.11 19:30 Разбор HTTPS://Zoom.us/J/1 партнёров",
     MeetingCommand("create", day=5, month=11, hour=19, minute=30, join_url="HTTPS://Zoom.us/J/1",
                    title="Разбор партнёров")),
    ("ссылка встречи 12 https://zoom.us/j/9",
     MeetingCommand("set_url", meeting_id=12, join_url="https://zoom.us/j/9")),
    ("Ссылка встречи #12 https://zoom.us/j/9",
     MeetingCommand("set_url", meeting_id=12, join_url="https://zoom.us/j/9")),
    ("отменить встречу 12", MeetingCommand("cancel", meeting_id=12)),
    ("Отменить встречу #7", MeetingCommand("cancel", meeting_id=7)),
    ("встреча прошла 12", MeetingCommand("done", meeting_id=12)),
    ("Встреча прошла #3", MeetingCommand("done", meeting_id=3)),
    # дата разбирается позже, у сервиса: 31.02 — всё ещё команда «создать», ответ даст обработчик
    ("встреча 31.02 19:30", MeetingCommand("create", day=31, month=2, hour=19, minute=30)),
])
def test_commands_are_recognised(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "встреча 05.11",                       # нет времени
    "встреча 05.11 19.30",                 # время только через двоеточие
    "встреча 05.11.2026 19:30",            # год в команде не принимается
    "встреча 5 человек будет",             # цифра, но не дата
    "встречи создать",                     # нет числа
    "встречи создать 0",
    "встречи создать 11",
    "встречи создать много",
    "встречи создать 4 штуки",
    "ссылка встречи 12",                   # нет ссылки
    "ссылка встречи 12 не-ссылка",
    "ссылка встречи 12 https://",
    "ссылка встречи 12 https://zoom.us/j/9 пароль 123",   # лишнее после ссылки
    "ссылка встречи",
    "отменить встречу",
    "отменить встречу 12 потому что",
    "встреча прошла",
    "встреча прошла 12 вчера",
    "встреча 05.11 19:30 http://",         # битая ссылка в названии
    "встреча 05.11 19:30 https://a.ru/1 https://b.ru/2",   # две ссылки
    "встреча",
])
def test_recognised_command_with_a_wrong_format_gets_a_hint(text):
    command = parse_command(text)
    assert command is not None and command.action == "usage" and command.hint, text


@pytest.mark.parametrize("text", [
    "", "   ", "привет", "почта", "расскажи про встречи", "встречи в клубе по вторникам?", "встречи клуба",
    "встреча была отличной", "встречу перенесли", "встреча с партнёром завтра", "ссылка встречи нужна",
    "ссылка на встречу", "отменить встречу позже", "отменить подписку 12", "встречи?", "встречи создать-ка",
    "/meetings", "встречаемся в 19:30",
])
def test_ordinary_phrases_are_not_commands(text):
    assert parse_command(text) is None


def test_no_text_is_not_a_command():
    assert parse_command(None) is None


# ---- время ---------------------------------------------------------------------------------------------


def test_date_without_a_year_takes_the_nearest_future_moment():
    assert service.resolve_msk_datetime(5, 11, 19, 30, now=NOW) == msk(2026, 11, 5)
    # сегодня 15:00 МСК: 19:30 сегодня ещё впереди, 14:00 уже нет — следующий год
    assert service.resolve_msk_datetime(30, 10, 19, 30, now=NOW) == msk(2026, 10, 30)
    assert service.resolve_msk_datetime(30, 10, 14, 0, now=NOW) == msk(2027, 10, 30, 14, 0)
    assert service.resolve_msk_datetime(1, 1, 10, 0, now=NOW) == msk(2027, 1, 1, 10, 0)


def test_the_moment_must_be_strictly_in_the_future():
    exactly_now = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # 15:00 МСК
    assert service.resolve_msk_datetime(30, 10, 15, 0, now=exactly_now) == msk(2027, 10, 30, 15, 0)
    one_minute_later = service.resolve_msk_datetime(30, 10, 15, 1, now=exactly_now)
    assert one_minute_later == msk(2026, 10, 30, 15, 1)


def test_the_year_is_chosen_by_moscow_not_by_utc():
    # 31.12 22:30 UTC — в Москве уже 1 января следующего года
    now = dt.datetime(2026, 12, 31, 22, 30, tzinfo=UTC)
    assert service.resolve_msk_datetime(1, 1, 12, 0, now=now) == msk(2027, 1, 1, 12, 0)
    assert service.resolve_msk_datetime(31, 12, 23, 59, now=now) == msk(2027, 12, 31, 23, 59)


def test_29_february_goes_to_the_nearest_leap_year():
    assert service.resolve_msk_datetime(29, 2, 19, 30, now=NOW) == msk(2028, 2, 29)


@pytest.mark.parametrize(("day", "month", "hour", "minute"), [
    (31, 2, 19, 30), (30, 2, 19, 30), (31, 4, 19, 30), (0, 5, 19, 30), (32, 5, 19, 30),
    (5, 13, 19, 30), (5, 0, 19, 30), (5, 11, 24, 0), (5, 11, 25, 0), (5, 11, 19, 60),
])
def test_impossible_date_or_time_is_refused(day, month, hour, minute):
    with pytest.raises(MeetingError) as excinfo:
        service.resolve_msk_datetime(day, month, hour, minute, now=NOW)
    assert excinfo.value.code == "bad_time"


def test_default_slots_are_the_next_tuesdays_and_thursdays_at_1930_moscow():
    slots = service.default_slots(NOW, 4)  # пятница 30.10, 15:00 МСК
    assert slots == [msk(2026, 11, 3), msk(2026, 11, 5), msk(2026, 11, 10), msk(2026, 11, 12)]
    assert [s.weekday() for s in slots] == [1, 3, 1, 3]
    assert all(s.utcoffset() == dt.timedelta(hours=3) for s in slots)
    assert [s.astimezone(UTC).strftime("%H:%M") for s in slots] == ["16:30"] * 4


def test_default_slots_start_strictly_after_the_given_moment():
    tuesday = msk(2026, 11, 3)
    assert service.default_slots(tuesday - dt.timedelta(minutes=1), 1) == [tuesday]
    assert service.default_slots(tuesday, 1) == [msk(2026, 11, 5)]
    assert service.default_slots(msk(2026, 11, 3, 8, 0), 1) == [tuesday]   # утром во вторник — сегодняшний
    assert service.default_slots(msk(2026, 11, 3, 20, 0), 1) == [msk(2026, 11, 5)]


def test_default_slots_count_by_moscow_day_not_utc_day():
    # вторник 03.11, 21:30 UTC = среда 04.11, 00:30 МСК: ближайший — четверг
    assert service.default_slots(dt.datetime(2026, 11, 3, 21, 30, tzinfo=UTC), 1) == [msk(2026, 11, 5)]


def test_default_slots_cross_the_year_and_treat_naive_time_as_moscow():
    assert service.default_slots(dt.datetime(2026, 12, 30, 12, 0), 3) == [msk(2026, 12, 31), msk(2027, 1, 5), msk(2027, 1, 7)]


@pytest.mark.parametrize(("count", "expected_len"), [(0, 0), (-3, 0), (1, 1), (10, 10)])
def test_default_slots_count(count, expected_len):
    slots = service.default_slots(NOW, count)
    assert len(slots) == expected_len and slots == sorted(slots)


def test_format_shows_weekday_moscow_time_and_the_year_only_when_it_is_not_current():
    assert service.format_msk(msk(2026, 11, 5), now=NOW) == "чт 05.11 19:30 МСК"
    assert service.format_msk(dt.datetime(2026, 11, 3, 16, 30, tzinfo=UTC), now=NOW) == "вт 03.11 19:30 МСК"
    assert service.format_msk(dt.datetime(2026, 11, 3, 21, 30, tzinfo=UTC), now=NOW) == "ср 04.11 00:30 МСК"
    assert service.format_msk(msk(2027, 1, 5), now=NOW) == "вт 05.01.2027 19:30 МСК"


@pytest.mark.parametrize(("raw", "ok"), [
    ("https://zoom.us/j/81234567890?pwd=Ab", True),
    ("http://zoom.us/j/1", True),
    ("HTTPS://ZOOM.US/J/1", True),
    ("https://us02web.zoom.us/j/1#x", True),
    ("zoom.us/j/1", False), ("ftp://zoom.us/j/1", False), ("https://", False), ("https:///j/1", False),
    ("https://:", False), ("https://zoom us/j/1", False), ("", False), (None, False), ("javascript:alert(1)", False),
    ("https://zoom.us/j/" + "1" * 600, False), ("https://zoom.us/j/1\nhttps://evil.example", False),
])
def test_join_url_validation(raw, ok):
    assert (service.normalize_join_url(raw) is not None) is ok


# ---- публичный вид -------------------------------------------------------------------------------------


def _row(meeting_id=1, *, starts_at=None, kind="live", title="Живая встреча", duration=60, join_url=ZOOM,
         hosts=None, status="planned", tenant="whieda") -> dict:
    return {
        "meeting_id": meeting_id, "tenant_id": tenant, "kind": kind, "title": title,
        "starts_at": starts_at or msk(2026, 11, 5), "duration_min": duration, "join_url": join_url,
        "hosts": hosts if hosts is not None else ["Виктор"], "status": status, "recording_url": "https://rec.example/x",
        "notes": "внутренняя заметка", "created_by": "telegram:1", "created_at": NOW, "updated_at": NOW,
    }


def test_public_view_is_a_whitelist_without_link_recording_or_notes():
    item = service.public_meeting(_row(7, starts_at=dt.datetime(2026, 11, 5, 16, 30, tzinfo=UTC), hosts=["Виктор", "Ольга"]))
    assert item == {"id": 7, "title": "Живая встреча", "starts_at": "2026-11-05T19:30:00+03:00",
                    "duration_min": 60, "hosts": ["Виктор", "Ольга"]}
    assert set(item) == {"id", "title", "starts_at", "duration_min", "hosts"}


def test_public_view_gives_a_default_title_and_an_empty_host_list():
    assert service.public_meeting(_row(title=None, hosts=[]))["title"] == "Живая встреча"
    assert service.public_meeting(_row(title="  ", kind="consult"))["title"] == "Консультация"
    assert service.public_meeting({**_row(), "hosts": None})["hosts"] == []


# ---- подделка базы -------------------------------------------------------------------------------------


class FakeMeetings:
    """Таблица meetings в памяти. Запросы узнаются по тексту SQL из service; настоящий SQL — в *_postgres.py."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.queries: list[str] = []
        self.connections = 0
        self.writes = 0

    @asynccontextmanager
    async def tenant_connection(self, tenant_id):
        self.connections += 1
        yield SimpleNamespace(tenant_id=tenant_id)

    def add(self, **fields) -> dict:
        row = _row(len(self.rows) + 1, **fields)
        self.rows.append(row)
        return row

    def _find(self, tenant, meeting_id):
        return next((r for r in self.rows if r["tenant_id"] == tenant and r["meeting_id"] == meeting_id), None)

    async def fetch_one(self, conn, query, params=()):  # noqa: ARG002
        q = " ".join(query.split())
        self.queries.append(q)
        if q.startswith("insert into meetings"):
            tenant, kind, title, starts_at, duration, url, hosts, notes, created_by = params
            self.writes += 1
            row = _row(len(self.rows) + 1, tenant=tenant, kind=kind, title=title, starts_at=starts_at.astimezone(UTC),
                       duration=duration, join_url=url, hosts=hosts)
            row.update(notes=notes, created_by=created_by, recording_url=None)
            self.rows.append(row)
            return dict(row)
        if q.startswith("update meetings set join_url"):
            url, tenant, meeting_id = params
            row = self._find(tenant, meeting_id)
            if row is None or row["status"] != "planned":
                return None
            self.writes += 1
            row["join_url"] = url
            return dict(row)
        if q.startswith("update meetings set status"):
            status, tenant, meeting_id = params
            row = self._find(tenant, meeting_id)
            if row is None or row["status"] != "planned":
                return None
            self.writes += 1
            row["status"] = status
            return dict(row)
        if "and kind = %s and starts_at = %s and status <> 'cancelled'" in q:
            tenant, kind, starts_at = params
            same = [r for r in self.rows if r["tenant_id"] == tenant and r["kind"] == kind
                    and r["starts_at"] == starts_at and r["status"] != "cancelled"]
            return dict(same[0]) if same else None
        if "where tenant_id = %s and meeting_id = %s" in q:
            tenant, meeting_id = params
            row = self._find(tenant, meeting_id)
            return dict(row) if row else None
        raise AssertionError(f"unexpected query: {q[:100]}")

    async def fetch_all(self, conn, query, params=()):  # noqa: ARG002
        q = " ".join(query.split())
        self.queries.append(q)
        assert "from meetings where tenant_id = %s and status = 'planned'" in q, q[:100]
        tenant, now, *rest = params
        limit = rest[-1]
        kind = rest[0] if len(rest) == 2 else None
        found = [
            r for r in self.rows
            if r["tenant_id"] == tenant and r["status"] == "planned"
            and r["starts_at"] + dt.timedelta(minutes=r["duration_min"]) > now
            and (kind is None or r["kind"] == kind)
        ]
        found.sort(key=lambda r: (r["starts_at"], r["meeting_id"]))
        wanted = [c.strip() for c in q.split(" from meetings", 1)[0].removeprefix("select ").split(",")]
        return [{k: v for k, v in r.items() if k in wanted} for r in found[:limit]]


@pytest.fixture
def fake():
    store = FakeMeetings()
    with patch.object(service, "tenant_connection", store.tenant_connection), \
         patch.object(meetings_admin, "tenant_connection", store.tenant_connection), \
         patch.object(service, "fetch_one", store.fetch_one), \
         patch.object(service, "fetch_all", store.fetch_all), \
         patch.object(service, "utcnow", lambda: NOW):
        yield store


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.delenv("PLATFORM_SUPER_ADMIN_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sent():
    out: list[str] = []
    with patch.object(meetings_admin, "_deliver", AsyncMock(side_effect=lambda chat, text: out.append(text))):
        yield out


def _msg(text: str, user_id: int = OWNER, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type=chat_type,
                           file_id=None, raw={})


async def say(text: str, **kw) -> dict | None:
    return await try_handle_meetings_admin_message(TENANT, _msg(text, **kw), trace_id="t1")


# ---- команды владельца ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_tells_how_to_create(owner_env, fake, sent):
    result = await say("встречи")
    assert result["status"] == "listed" and result["route"] == "meetings_admin"
    assert sent == ["Ближайших встреч нет. Создать: встреча 05.11 19:30 или встречи создать 4"]


@pytest.mark.asyncio
async def test_create_stores_utc_moment_and_confirms_in_two_lines(owner_env, fake, sent):
    result = await say("встреча 05.11 19:30")
    assert result["status"] == "created" and result["meeting_id"] == 1
    stored = fake.rows[0]
    assert stored["starts_at"] == dt.datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
    assert stored["starts_at"].utcoffset() == dt.timedelta(0)
    assert (stored["kind"], stored["title"], stored["status"], stored["join_url"]) == ("live", "Живая встреча", "planned", None)
    assert stored["created_by"] == f"telegram:{OWNER}"
    assert sent == [
        "Создана встреча #1 · чт 05.11 19:30 МСК · Живая встреча · ссылка нет\n"
        "Ссылку добавьте: ссылка встречи 1 https://…"
    ]


@pytest.mark.asyncio
async def test_create_with_link_and_title_keeps_the_link_out_of_the_reply(owner_env, fake, sent):
    await say(f"встреча 05.11 19:30 {ZOOM} Разбор партнёров")
    stored = fake.rows[0]
    assert (stored["join_url"], stored["title"]) == (ZOOM, "Разбор партнёров")
    assert sent == ["Создана встреча #1 · чт 05.11 19:30 МСК · Разбор партнёров · ссылка есть"]


@pytest.mark.asyncio
async def test_the_year_is_the_nearest_future_one_and_shown_when_it_is_next_year(owner_env, fake, sent):
    await say("встреча 05.01 10:00")
    assert fake.rows[0]["starts_at"] == dt.datetime(2027, 1, 5, 7, 0, tzinfo=UTC)
    assert "вт 05.01.2027 10:00 МСК" in sent[0]


@pytest.mark.asyncio
async def test_the_same_time_twice_does_not_create_a_duplicate(owner_env, fake, sent):
    await say("встреча 05.11 19:30")
    result = await say("встреча 05.11 19:30 Другое название")
    assert result["status"] == "duplicate" and result["meeting_id"] == 1
    assert len(fake.rows) == 1 and fake.writes == 1
    assert sent[-1] == "На это время уже есть встреча #1 · чт 05.11 19:30 МСК · Живая встреча · ссылка нет. Новую не создавал."


@pytest.mark.asyncio
async def test_a_cancelled_meeting_does_not_block_the_same_time(owner_env, fake, sent):
    await say("встреча 05.11 19:30")
    await say("отменить встречу 1")
    result = await say("встреча 05.11 19:30")
    assert result["status"] == "created" and result["meeting_id"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["встреча 31.02 19:30", "встреча 05.13 19:30", "встреча 05.11 25:00", "встреча 05.11 19:60"])
async def test_impossible_date_gets_a_hint_and_never_opens_the_database(owner_env, fake, sent, text):
    result = await say(text)
    assert result["status"] == "invalid_time" and result["ok"] is False
    assert sent == ["Такой даты или времени нет. Формат: встреча 05.11 19:30 [https://ссылка] [название]"]
    assert fake.connections == 0 and fake.rows == []


@pytest.mark.asyncio
async def test_wrong_format_gets_the_format_hint_and_never_opens_the_database(owner_env, fake, sent):
    result = await say("встреча 05.11")
    assert result["status"] == "invalid_command" and result["ok"] is False
    assert sent == ["Не понял. Формат: встреча 05.11 19:30 [https://ссылка] [название]"]
    result = await say("встреча")
    assert sent[-1].startswith("Команды для встреч:") and "отменить встречу 12" in sent[-1]
    assert fake.connections == 0


@pytest.mark.asyncio
async def test_list_shows_planned_upcoming_in_time_order_with_link_marks(owner_env, fake, sent):
    fake.add(starts_at=msk(2026, 11, 10), title="Поздняя", join_url=None)
    fake.add(starts_at=msk(2026, 11, 5), title="Ранняя")
    fake.add(starts_at=msk(2026, 11, 7), title="Отменённая", status="cancelled")
    fake.add(starts_at=msk(2026, 11, 8), title="Прошла", status="done")
    fake.add(starts_at=msk(2026, 10, 29), title="Вчерашняя")
    await say("встречи")
    assert sent == [
        "Ближайшие встречи:\n"
        "#2 · чт 05.11 19:30 МСК · Ранняя · ссылка есть\n"
        "#1 · вт 10.11 19:30 МСК · Поздняя · ссылка нет"
    ]
    assert "zoom" not in sent[0] and "SeCrEt" not in sent[0]


@pytest.mark.asyncio
async def test_a_meeting_in_progress_stays_in_the_list_until_it_ends(owner_env, fake, sent):
    # сейчас 15:00 МСК: встреча с 14:30 на 60 минут ещё идёт, с 13:00 на 60 — закончилась
    fake.add(starts_at=msk(2026, 10, 30, 14, 30), title="Идёт")
    fake.add(starts_at=msk(2026, 10, 30, 13, 0), title="Закончилась")
    await say("встречи")
    assert "Идёт" in sent[0] and "Закончилась" not in sent[0]


@pytest.mark.asyncio
async def test_list_is_limited_to_ten(owner_env, fake, sent):
    for day in range(1, 16):
        fake.add(starts_at=msk(2026, 12, day), title=f"Встреча {day}")
    await say("встречи")
    assert sent[0].count("\n") == 10 and "Встреча 10" in sent[0] and "Встреча 11" not in sent[0]


@pytest.mark.asyncio
async def test_create_schedule_makes_the_nearest_slots_and_is_safe_to_repeat(owner_env, fake, sent):
    result = await say("встречи создать 4")
    assert result["status"] == "slots_created" and result["created"] == [1, 2, 3, 4]
    assert [r["starts_at"] for r in fake.rows] == [
        dt.datetime(2026, 11, 3, 16, 30, tzinfo=UTC), dt.datetime(2026, 11, 5, 16, 30, tzinfo=UTC),
        dt.datetime(2026, 11, 10, 16, 30, tzinfo=UTC), dt.datetime(2026, 11, 12, 16, 30, tzinfo=UTC),
    ]
    assert all((r["kind"], r["title"], r["join_url"]) == ("live", "Живая встреча", None) for r in fake.rows)
    assert sent[-1] == (
        "Создано 4: #1 вт 03.11 19:30 · #2 чт 05.11 19:30 · #3 вт 10.11 19:30 · #4 чт 12.11 19:30 (МСК).\n"
        "Ссылки добавьте: ссылка встречи <номер> https://…"
    )
    again = await say("встречи создать 4")
    assert again["status"] == "slots_exist" and again["created"] == [] and len(fake.rows) == 4
    assert sent[-1].startswith("Новых нет: ближайшие слоты уже созданы — #1 вт 03.11 19:30")


@pytest.mark.asyncio
async def test_create_schedule_skips_only_the_slots_that_already_exist(owner_env, fake, sent):
    await say("встреча 05.11 19:30 Своё название")
    result = await say("встречи создать 3")
    assert result["created"] == [2, 3]
    assert [r["starts_at"].astimezone(MSK).strftime("%d.%m") for r in fake.rows] == ["05.11", "03.11", "10.11"]
    assert sent[-1] == (
        "Создано 2: #2 вт 03.11 19:30 · #3 вт 10.11 19:30 (МСК).\n"
        "Уже были, пропустил: #1 чт 05.11 19:30."
    )


@pytest.mark.asyncio
async def test_set_and_replace_the_link(owner_env, fake, sent):
    fake.add(join_url=None)
    result = await say("ссылка встречи 1 https://zoom.us/j/111")
    assert result["status"] == "url_set" and fake.rows[0]["join_url"] == "https://zoom.us/j/111"
    await say("ссылка встречи #1 https://zoom.us/j/222")
    assert fake.rows[0]["join_url"] == "https://zoom.us/j/222"
    assert sent == ["Ссылка встречи #1 сохранена (чт 05.11 19:30 МСК)."] * 2
    assert all("zoom.us" not in text for text in sent)


@pytest.mark.asyncio
async def test_unknown_number_is_answered_with_a_pointer_to_the_list(owner_env, fake, sent):
    for text in ("ссылка встречи 99 https://zoom.us/j/1", "отменить встречу 99", "встреча прошла 99"):
        result = await say(text)
        assert result["status"] == "not_found" and result["ok"] is False, text
    assert sent == ["Встречи #99 нет. Список: встречи"] * 3 and fake.writes == 0


@pytest.mark.asyncio
async def test_meetings_of_another_tenant_are_not_reachable(owner_env, fake, sent):
    fake.add(tenant="other-tenant")
    for text in ("отменить встречу 1", "встреча прошла 1", "ссылка встречи 1 https://zoom.us/j/1"):
        assert (await say(text))["status"] == "not_found"
    await say("встречи")
    assert fake.rows[0]["status"] == "planned" and fake.rows[0]["join_url"] == ZOOM
    assert sent[-1] == "Ближайших встреч нет. Создать: встреча 05.11 19:30 или встречи создать 4"


@pytest.mark.asyncio
async def test_cancel_and_done_follow_the_status_rules(owner_env, fake, sent):
    fake.add()
    fake.add(starts_at=msk(2026, 11, 7))
    fake.add(starts_at=msk(2026, 11, 8))
    assert (await say("отменить встречу 1"))["status"] == "cancelled"
    assert sent[-1] == "Встреча #1 отменена (чт 05.11 19:30 МСК)."
    assert (await say("отменить встречу 1"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #1 уже отменена."
    assert (await say("встреча прошла 1"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #1 отменена — отметить прошедшей нельзя."
    assert (await say("ссылка встречи 1 https://zoom.us/j/1"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #1 отменена — ссылку не меняю."

    assert (await say("встреча прошла 2"))["status"] == "done"
    assert sent[-1] == "Встреча #2 отмечена как прошедшая."
    assert (await say("встреча прошла 2"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #2 уже отмечена как прошедшая."
    assert (await say("отменить встречу #2"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #2 уже прошла — отменить нельзя."
    assert (await say("ссылка встречи 2 https://zoom.us/j/1"))["status"] == "bad_status"
    assert sent[-1] == "Встреча #2 уже прошла — ссылку не меняю."
    assert [r["status"] for r in fake.rows] == ["cancelled", "done", "planned"]
    assert fake.rows[0]["join_url"] == ZOOM  # отказ ссылку не тронул


@pytest.mark.asyncio
async def test_the_link_is_never_repeated_in_any_reply_or_log(owner_env, fake, sent, caplog):
    caplog.set_level("DEBUG")
    await say(f"встреча 05.11 19:30 {ZOOM}")
    await say("ссылка встречи 1 https://zoom.us/j/777?pwd=Another")
    await say("встречи")
    await say("отменить встречу 1")
    text = " ".join(sent) + " " + caplog.text
    assert "zoom.us" not in text and "SeCrEt" not in text and "Another" not in text
    assert "meetings admin: action=create status=created meeting_id=1" in caplog.text


@pytest.mark.asyncio
async def test_every_write_forgets_the_public_cache_after_commit(owner_env, fake, sent):
    for text, expected_forgotten in (
        ("встречи", False),
        ("встреча 05.11 19:30", True),
        ("встреча 05.11 19:30", False),            # дубль: ничего не записано
        ("встречи создать 2", True),
        ("встречи создать 2", False),              # всё уже есть
        ("ссылка встречи 1 https://zoom.us/j/1", True),
        ("отменить встречу 1", True),
        ("отменить встречу 1", False),
        ("встреча прошла 2", True),
    ):
        service.public_meetings_cache.set(("whieda", "live"), [{"id": 0}], ttl=30)
        await say(text)
        assert (service.public_meetings_cache.get(("whieda", "live")) is None) is expected_forgotten, text


@pytest.mark.asyncio
async def test_not_the_owner_group_or_ordinary_phrase_is_not_answered(owner_env, fake, sent):
    assert await say("встречи", user_id=STRANGER) is None
    assert await say("встреча 05.11 19:30", user_id=STRANGER) is None
    assert await say("встречи", chat_type="group") is None
    assert await say("встреча 05.11 19:30", chat_type="supergroup") is None
    assert await say("встречи в клубе по вторникам?") is None
    assert await say("привет") is None
    assert sent == [] and fake.connections == 0 and fake.rows == []


@pytest.mark.asyncio
async def test_without_a_configured_owner_nobody_is_the_owner(monkeypatch, fake, sent):
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    get_settings.cache_clear()
    try:
        assert await say("встречи") is None and sent == []
    finally:
        get_settings.cache_clear()


# ---- через процессор -----------------------------------------------------------------------------------

_EARLIER_HANDLERS = (
    "try_handle_wish_message", "try_handle_profile_reject_reason", "try_handle_shop_message", "try_handle_support_message",
    "try_handle_renewal_message", "try_handle_site_request_message", "try_start_renewal_by_text",
    "try_handle_billing_message", "try_handle_referral_admin_message",
)


def _update(text: str, user_id: int) -> dict:
    return {"message": {"message_id": 5, "text": text, "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id}}}


def _through_processor(stack: ExitStack):
    stack.enter_context(patch("app.telegram.processor._link_partner_chat", AsyncMock()))
    for name in _EARLIER_HANDLERS:
        stack.enter_context(patch(f"app.telegram.processor.{name}", AsyncMock(return_value=None)))
    return stack.enter_context(
        patch("app.telegram.processor.try_handle_login_identity_message", AsyncMock(return_value={"ok": True, "route": "next_handler"}))
    )


@pytest.mark.asyncio
async def test_the_owner_command_is_answered_by_our_handler_through_the_processor(owner_env, fake, sent, whieda_tenant, whieda_bot_binding):
    with ExitStack() as stack:
        next_handler = _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update("встреча 05.11 19:30", OWNER), "t1", binding=whieda_bot_binding)
    assert result["route"] == "meetings_admin" and result["status"] == "created"
    next_handler.assert_not_awaited()
    assert len(fake.rows) == 1 and sent[0].startswith("Создана встреча #1")


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "user_id"), [
    ("встречи", STRANGER),                      # у не-владельца такого слова нет — помощник «события» работает как раньше
    ("встреча 05.11 19:30", STRANGER),
    ("встречи в клубе по вторникам?", OWNER),   # обычная фраза владельца
])
async def test_what_is_not_our_command_falls_through_to_the_next_handler(owner_env, fake, sent, whieda_tenant, whieda_bot_binding, text, user_id):
    with ExitStack() as stack:
        next_handler = _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update(text, user_id), "t1", binding=whieda_bot_binding)
    assert result["route"] == "next_handler"
    next_handler.assert_awaited_once()
    assert sent == [] and fake.rows == []


def test_processor_routes_to_the_meetings_handler_after_dialogs_and_the_referral_admin():
    assert processor.try_handle_meetings_admin_message is meetings_admin.try_handle_meetings_admin_message
    source = open(processor.__file__, encoding="utf-8").read()
    at = source.index("try_handle_meetings_admin_message(tenant, msg")
    assert source.index("try_handle_support_message(tenant, msg") < at, "диалоги поддержки и заявок — раньше"
    assert source.index("try_handle_site_request_message(tenant, msg") < at
    assert source.index("try_handle_referral_admin_message(") < at
    assert at < source.index("try_handle_login_identity_message(tenant, msg")
    assert at < source.index("handle_advisor_query(tenant, msg, trace_id)")


# ---- публичный список ---------------------------------------------------------------------------------

HOST = {"host": "wwc.best"}


@pytest.mark.asyncio
async def test_public_list_shape_header_and_no_link(owner_env, fake, client):
    fake.add(starts_at=msk(2026, 11, 5), hosts=["Виктор", "Ольга"])
    fake.add(starts_at=msk(2026, 11, 3), title="Вторничная", join_url=None, hosts=[])
    response = await client.get("/api/v1/public/meetings?kind=live&limit=2", headers=HOST)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.json() == {"meetings": [
        {"id": 2, "title": "Вторничная", "starts_at": "2026-11-03T19:30:00+03:00", "duration_min": 60, "hosts": []},
        {"id": 1, "title": "Живая встреча", "starts_at": "2026-11-05T19:30:00+03:00", "duration_min": 60,
         "hosts": ["Виктор", "Ольга"]},
    ]}
    for forbidden in ("join_url", "zoom.us", "SeCrEt", "recording", "notes", "внутренняя", "created_by", "tenant"):
        assert forbidden not in response.text, forbidden


@pytest.mark.asyncio
async def test_public_query_never_reads_the_link_column(owner_env, fake, client):
    fake.add()
    await client.get("/api/v1/public/meetings?kind=live", headers=HOST)
    selects = [q for q in fake.queries if q.startswith("select") and "from meetings" in q]
    assert selects and all("join_url" not in q and "notes" not in q and "recording_url" not in q for q in selects)


@pytest.mark.asyncio
async def test_public_list_is_also_served_without_the_api_prefix(owner_env, fake, client):
    fake.add()
    response = await client.get("/v1/public/meetings", headers=HOST)
    assert response.status_code == 200 and [m["id"] for m in response.json()["meetings"]] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "expected_count"), [
    ("", 3), ("?limit=2", 2), ("?limit=1", 1), ("?limit=0", 1), ("?limit=-5", 1),
    ("?limit=10", 10), ("?limit=11", 10), ("?limit=500", 10),
])
async def test_public_limit_defaults_to_three_and_never_exceeds_ten(owner_env, fake, client, query, expected_count):
    for day in range(1, 16):
        fake.add(starts_at=msk(2026, 12, day), title=f"Встреча {day}")
    response = await client.get(f"/api/v1/public/meetings{query}", headers=HOST)
    assert response.status_code == 200 and len(response.json()["meetings"]) == expected_count


@pytest.mark.asyncio
async def test_public_limit_that_is_not_a_number_is_refused(owner_env, fake, client):
    response = await client.get("/api/v1/public/meetings?limit=many", headers=HOST)
    assert response.status_code == 422 and fake.connections == 0


@pytest.mark.asyncio
async def test_public_list_shows_only_live_and_refuses_consult(owner_env, fake, client):
    fake.add(title="Живая")
    fake.add(kind="consult", title="Консультация эксперта", starts_at=msk(2026, 11, 4))
    live = await client.get("/api/v1/public/meetings", headers=HOST)
    consult = await client.get("/api/v1/public/meetings?kind=consult", headers=HOST)
    assert [m["title"] for m in live.json()["meetings"]] == ["Живая"]
    # Консультации публично не отдаются (решение лида 30.10.2026): тот же ответ, что на неизвестный kind.
    assert consult.status_code == 422 and consult.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_public_list_refuses_an_unknown_kind_without_caching(owner_env, fake, client):
    response = await client.get("/api/v1/public/meetings?kind=private", headers=HOST)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert fake.connections == 0


@pytest.mark.asyncio
async def test_public_list_hides_cancelled_done_and_past_meetings(owner_env, fake, client):
    fake.add(title="Будет")
    fake.add(title="Отменена", status="cancelled")
    fake.add(title="Прошла", status="done")
    fake.add(title="Давно", starts_at=msk(2026, 10, 1))
    titles = [m["title"] for m in (await client.get("/api/v1/public/meetings?limit=10", headers=HOST)).json()["meetings"]]
    assert titles == ["Будет"]


@pytest.mark.asyncio
async def test_public_list_is_per_tenant(owner_env, fake, client):
    fake.add(title="Whieda")
    fake.add(title="Чужая", tenant="test-acme")
    mine = await client.get("/api/v1/public/meetings", headers=HOST)
    theirs = await client.get("/api/v1/public/meetings", headers={"host": "acme.test.local"})
    assert [m["title"] for m in mine.json()["meetings"]] == ["Whieda"]
    assert [m["title"] for m in theirs.json()["meetings"]] == ["Чужая"]


@pytest.mark.asyncio
async def test_public_list_unknown_host_is_not_found(owner_env, fake, client):
    response = await client.get("/api/v1/public/meetings", headers={"host": "nobody.example"})
    assert response.status_code == 404 and fake.connections == 0


@pytest.mark.asyncio
async def test_public_list_is_kept_in_memory_until_a_command_changes_meetings(owner_env, fake, sent, client):
    fake.add(title="Первая")
    await client.get("/api/v1/public/meetings", headers=HOST)
    await client.get("/api/v1/public/meetings?limit=1", headers=HOST)
    await client.get("/v1/public/meetings", headers=HOST)
    assert fake.connections == 1, "три запроса сайта — одно чтение базы"
    await say("отменить встречу 1")          # владелец изменил — следующий запрос читает базу заново
    body = (await client.get("/api/v1/public/meetings", headers=HOST)).json()
    assert body == {"meetings": []}
    assert fake.connections == 3  # +1 команда владельца, +1 перечитывание сайта


@pytest.mark.asyncio
async def test_public_cache_hands_out_copies(fake):
    fake.add(hosts=["Виктор"])
    first = await service.load_public_meetings("whieda", "live")
    first[0]["title"] = "испорчено"
    first[0]["hosts"].append("лишний")
    second = await service.load_public_meetings("whieda", "live")
    assert second[0]["title"] == "Живая встреча" and second[0]["hosts"] == ["Виктор"]
    assert fake.connections == 1


def test_the_router_is_mounted_on_both_prefixes(app):
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/public/meetings"] and "get" in paths["/v1/public/meetings"]


@pytest.mark.asyncio
async def test_invalid_kind_is_refused_by_the_service():
    with pytest.raises(MeetingError) as excinfo:
        await service.list_upcoming(object(), "whieda", "private", 3)
    assert excinfo.value.code == "bad_kind"
    with pytest.raises(MeetingError) as excinfo:
        await service.create_meeting(object(), "whieda", starts_at=msk(2026, 11, 5), kind="private")
    assert excinfo.value.code == "bad_kind"
