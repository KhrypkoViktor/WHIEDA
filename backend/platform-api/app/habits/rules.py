"""Чистые правила трекера привычек: подсчёт, шаблоны, проверка входа, дни человека (без базы).

Правила подсчёта — ТЗ §5. Главное: сегодняшний день, неделя или месяц идут в счёт, только когда
уже выполнены; пропуском становятся, когда закончились. Период, который привычка не прожила
целиком (добавлена в середине, была пауза, сменили ритм), считается только как победа.
Пауза — интервал ``[from_day, to_day)``: ``to_day`` — день «Продолжить», с него привычка снова идёт;
``None`` — пауза идёт. Отметка в день паузы не пропадает и идёт в счёт.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

LIMIT = 7
TITLE_MAX = 40
PAGES_RANGE = (1, 500)
LITERS_RANGE = (0.6, 9.0)  # 20 кг × 30 мл … 300 кг × 30 мл
DEFAULT_PAGES = 10

TEMPLATES = ("water", "warmup", "cardio", "strength", "reading", "book", "calls", "meetings", "custom")
RHYTHMS = ("daily", "weekly", "monthly")
PERIOD_KIND = {"daily": "day", "weekly": "week", "monthly": "month"}

# Ритмы, которые шаблон допускает; значения по умолчанию — ТЗ §4.
_TEMPLATE_RHYTHMS: dict[str, tuple[str, ...]] = {
    "water": ("daily",),
    "warmup": ("daily",),
    "cardio": ("daily", "weekly"),
    "strength": ("daily", "weekly"),
    "reading": ("daily",),
    "book": ("monthly",),
    "calls": ("daily", "weekly"),
    "meetings": ("daily", "weekly"),
    "custom": RHYTHMS,
}
_TEMPLATE_DEFAULT: dict[str, tuple[str, int]] = {
    "water": ("daily", 1),
    "warmup": ("daily", 1),
    "cardio": ("daily", 1),
    "strength": ("weekly", 2),
    "reading": ("daily", 1),
    "book": ("monthly", 1),
    "calls": ("daily", 1),
    "meetings": ("weekly", 2),
    "custom": ("daily", 1),
}

_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")


class HabitsError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


# ------------------------------------------------------------------ дни человека


def parse_tz(value: Any) -> str | None:
    """IANA-имя пояса или ``None``, если значения нет; кривое — 400 ``bad_tz``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    name = str(value).strip()
    if not _TZ_RE.fullmatch(name):
        raise HabitsError(400, "bad_tz")
    try:
        ZoneInfo(name)
    except Exception as exc:  # неизвестный ключ, мусор, нет базы поясов
        raise HabitsError(400, "bad_tz") from exc
    return name


def person_days(now: datetime, tz: str) -> tuple[date, date]:
    """(сегодня, вчера) по поясу человека; ``now`` — момент, aware или UTC."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date()
    return today, today - timedelta(days=1)


def parse_day(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise HabitsError(400, "bad_day") from exc


def check_mark_day(day: date, *, today: date, start_day: date) -> None:
    """Ставить можно за сегодня и вчера и не раньше дня добавления."""
    if day not in (today, today - timedelta(days=1)):
        raise HabitsError(400, "day_closed")
    if day < start_day:
        raise HabitsError(400, "before_start")


# ------------------------------------------------------------------ подсчёт


@dataclass(frozen=True)
class HabitSpec:
    rhythm: str
    goal: int
    start_day: date
    rhythm_changed_on: date | None = None
    pauses: tuple[tuple[date, date | None], ...] = ()

    @property
    def counting_from(self) -> date:
        """С какого дня считается нынешний ритм (старые дни остаются отметками)."""
        if self.rhythm_changed_on and self.rhythm_changed_on > self.start_day:
            return self.rhythm_changed_on
        return self.start_day


@dataclass(frozen=True)
class Tally:
    period: str  # day | week | month
    passed: int
    done: int
    missed: int
    streak: int
    period_done: int  # отметок в текущей неделе или месяце (у ежедневной — за сегодня)
    period_goal: int


def _active_pauses(pauses: Any) -> list[tuple[date, date | None]]:
    return [(a, b) for a, b in pauses if b is None or b > a]


def _paused_on(pauses: list[tuple[date, date | None]], day: date) -> bool:
    return any(a <= day and (b is None or day < b) for a, b in pauses)


def _paused_within(pauses: list[tuple[date, date | None]], first: date, last: date) -> bool:
    return any(a <= last and (b is None or b > first) for a, b in pauses)


def _month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def _week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def period_bounds(rhythm: str, day: date) -> tuple[date, date]:
    if rhythm == "weekly":
        return _week_bounds(day)
    if rhythm == "monthly":
        return _month_bounds(day)
    return day, day


def tally(spec: HabitSpec, marks: Any, today: date) -> Tally:
    """Подсчёт на «сейчас» (``today`` — сегодня по поясу человека). Отметки позже сегодня не идут в счёт."""
    days = {d for d in marks if d <= today}
    pauses = _active_pauses(spec.pauses)
    begin = spec.counting_from
    outcomes: list[str] = []  # хронологически: "done" | "missed" | "skip"

    if spec.rhythm == "daily":
        cursor = begin
        while cursor <= today:
            if cursor in days:
                outcomes.append("done")
            elif cursor < today and not _paused_on(pauses, cursor):
                outcomes.append("missed")
            else:
                outcomes.append("skip")
            cursor += timedelta(days=1)
        return _result("day", outcomes, 1 if today in days else 0, 1)

    per_period: Counter[date] = Counter()
    for d in days:
        per_period[period_bounds(spec.rhythm, d)[0]] += 1
    first, last = period_bounds(spec.rhythm, begin)
    while first <= today:
        count = per_period[first]
        complete = begin <= first and not _paused_within(pauses, first, last)
        if count >= spec.goal:
            outcomes.append("done")
        elif last < today and complete:
            outcomes.append("missed")
        else:
            outcomes.append("skip")
        first, last = period_bounds(spec.rhythm, last + timedelta(days=1))
    current = period_bounds(spec.rhythm, today)[0]
    return _result(PERIOD_KIND[spec.rhythm], outcomes, per_period[current], spec.goal)


def _result(kind: str, outcomes: list[str], period_done: int, period_goal: int) -> Tally:
    done = outcomes.count("done")
    missed = outcomes.count("missed")
    streak = 0
    for outcome in reversed(outcomes):
        if outcome == "skip":
            continue
        if outcome != "done":
            break
        streak += 1
    return Tally(kind, done + missed, done, missed, streak, period_done, period_goal)


# ------------------------------------------------------------------ вход


def clean_title(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        raise HabitsError(400, "bad_title")
    text = " ".join(text.split())
    if not text or len(text) > TITLE_MAX:
        raise HabitsError(400, "bad_title")
    return text


def _int_in(value: Any, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, float):
        if value != int(value):
            raise ValueError(value)
        value = int(value)
    if not isinstance(value, int) or not low <= value <= high:
        raise ValueError(value)
    return value


def clean_rhythm(template: str, rhythm: Any, goal: Any) -> tuple[str, int]:
    """(ритм, цель) для шаблона: daily — 1, weekly — 1–7, monthly — 1; ритм шаблона из таблицы §4."""
    if rhythm not in RHYTHMS or rhythm not in _TEMPLATE_RHYTHMS[template]:
        raise HabitsError(400, "bad_rhythm")
    try:
        if rhythm == "weekly":
            return rhythm, _int_in(goal, 1, 7)
        if goal is not None:
            _int_in(goal, 1, 1)
    except ValueError as exc:
        raise HabitsError(400, "bad_rhythm") from exc
    return rhythm, 1


def clean_params(template: str, params: Any) -> dict[str, Any]:
    """Параметры шаблона: вода — ``liters`` (0,1 л), чтение — ``pages`` (1–500, по умолчанию 10), прочее — пусто."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise HabitsError(400, "bad_params")
    if template == "water":
        if set(params) != {"liters"}:
            raise HabitsError(400, "bad_params")
        liters = params["liters"]
        if isinstance(liters, bool) or not isinstance(liters, (int, float)):
            raise HabitsError(400, "bad_params")
        tenths = round(float(liters) * 10)
        if abs(float(liters) * 10 - tenths) > 1e-6 or not LITERS_RANGE[0] <= tenths / 10 <= LITERS_RANGE[1]:
            raise HabitsError(400, "bad_params")
        return {"liters": tenths / 10}
    if template == "reading":
        if set(params) - {"pages"}:
            raise HabitsError(400, "bad_params")
        try:
            return {"pages": _int_in(params.get("pages", DEFAULT_PAGES), *PAGES_RANGE)}
        except ValueError as exc:
            raise HabitsError(400, "bad_params") from exc
    if params:
        raise HabitsError(400, "bad_params")
    return {}


def clean_new(body: dict[str, Any]) -> dict[str, Any]:
    """Новая привычка: шаблон, ритм и цель (по умолчанию из шаблона), параметры, название «своей»."""
    template = body.get("template")
    if template not in TEMPLATES:
        raise HabitsError(400, "bad_template")
    default_rhythm, default_goal = _TEMPLATE_DEFAULT[template]
    rhythm = body.get("rhythm", default_rhythm)
    goal = body.get("goal", default_goal if rhythm == default_rhythm else None)
    rhythm, goal = clean_rhythm(template, rhythm, goal)
    title = clean_title(body.get("title")) if template == "custom" else None
    return {"template": template, "title": title, "rhythm": rhythm, "goal": goal,
            "params": clean_params(template, body.get("params"))}


def clean_patch(current: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Изменения привычки: только переданные поля; ``rhythm_changed`` — сменился ли ритм или цель."""
    template = current["template"]
    out: dict[str, Any] = {"rhythm_changed": False}
    if "rhythm" in body or "goal" in body:
        rhythm = body.get("rhythm", current["rhythm"])
        goal = body.get("goal", current["goal"] if rhythm == current["rhythm"] else None)
        rhythm, goal = clean_rhythm(template, rhythm, goal)
        out.update(rhythm=rhythm, goal=goal, rhythm_changed=(rhythm, goal) != (current["rhythm"], current["goal"]))
    if "params" in body:
        out["params"] = clean_params(template, body["params"])
    if "title" in body:
        if template != "custom":
            raise HabitsError(400, "bad_title")
        out["title"] = clean_title(body["title"])
    return out
