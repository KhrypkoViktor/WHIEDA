"""Pure rules of the partner diary: statuses, default next step, phone, texts.

No database and no clock here: callers pass the account's local «today» and
local dates (Postgres computes them from the account timezone), so every rule is
a plain function with unit tests (tests/test_crm_rules.py).

Status → default next step (TZ_IMPLEMENTER_V1, «Правила статусов»):

| status           | next_step | next_at                                   |
|------------------|-----------|-------------------------------------------|
| new              | invite    | today                                     |
| invited          | result    | the meeting date (meeting_at is required) |
| presented        | decide    | today + 2 days                            |
| deciding         | ping      | not set — the partner picks it            |
| client / partner | ping      | today + 30 days (repeat purchase)         |
| paused           | resume    | required date                             |

A PATCH that changes the status gets these defaults; a next_step / next_at sent
explicitly in the same PATCH wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

from app.site_requests.contacts import normalize_phone

STATUSES: tuple[str, ...] = ("new", "invited", "presented", "deciding", "client", "partner", "paused")
NEXT_STEPS: tuple[str, ...] = ("invite", "result", "decide", "ping", "resume")

STATUS_TITLES: dict[str, str] = {
    "new": "Новый контакт",
    "invited": "Приглашён",
    "presented": "Презентация проведена",
    "deciding": "Решает",
    "client": "Клиент",
    "partner": "Партнёр",
    "paused": "Пауза",
}

STEP_TITLES: dict[str, str] = {
    "invite": "Пригласить на встречу",
    "result": "Узнать результат",
    "decide": "Довести до решения",
    "ping": "Напомнить о себе",
    "resume": "Вернуться к разговору",
}

NAME_MAX = 200
SOURCE_MAX = 200
PHONE_RAW_MAX = 64
NOTE_MAX = 4000

# Morning message: 09:00 local, the worker looks every 5 minutes → two chances.
DIGEST_WINDOW_START = time(9, 0)
DIGEST_WINDOW_END = time(9, 10)

_TIMEZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}$")


class CrmRuleError(ValueError):
    """A request the rules refuse; ``code`` goes to the API as ``error``."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class StepPlan:
    next_step: str | None
    next_at: date | None


def default_plan(status: str, *, today: date, meeting_date: date | None = None) -> StepPlan:
    """The next step a status brings when the partner did not choose one."""
    if status == "new":
        return StepPlan("invite", today)
    if status == "invited":
        if meeting_date is None:
            raise CrmRuleError("meeting_at_required")
        return StepPlan("result", meeting_date)
    if status == "presented":
        return StepPlan("decide", today + timedelta(days=2))
    if status == "deciding":
        return StepPlan("ping", None)
    if status in ("client", "partner"):
        return StepPlan("ping", today + timedelta(days=30))
    if status == "paused":
        # «Пауза (до даты)»: без даты правило не применяется — дату задаёт партнёр.
        raise CrmRuleError("next_at_required")
    raise CrmRuleError("invalid_status")


def plan_for_patch(
    *,
    current_status: str,
    new_status: str | None,
    today: date,
    meeting_date: date | None,
    meeting_given: bool,
    next_step: str | None,
    next_step_given: bool,
    next_at: date | None,
    next_at_given: bool,
    current_next_step: str | None,
    current_next_at: date | None,
) -> StepPlan:
    """next_step / next_at after a PATCH.

    - explicit values in the PATCH always win;
    - a status change fills the rest from ``default_plan``;
    - a new meeting time of an invited contact moves «узнать результат» to it.
    """
    if next_step_given and next_step is not None and next_step not in NEXT_STEPS:
        raise CrmRuleError("invalid_next_step")
    status_changed = new_status is not None and new_status != current_status
    status = new_status if new_status is not None else current_status
    if status not in STATUSES:
        raise CrmRuleError("invalid_status")

    step, when = current_next_step, current_next_at
    if status_changed:
        if status == "paused" and next_at_given and next_at is not None:
            default = StepPlan("resume", next_at)
        elif status == "deciding" and next_at_given:
            default = StepPlan("ping", next_at)
        else:
            default = default_plan(status, today=today, meeting_date=meeting_date)
        step, when = default.next_step, default.next_at
    elif status == "invited" and meeting_given and meeting_date is not None:
        step, when = "result", meeting_date

    if next_step_given:
        step = next_step
    if next_at_given:
        when = next_at
    if status == "paused" and when is None:
        raise CrmRuleError("next_at_required")
    return StepPlan(step, when)


def clean_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise CrmRuleError("name_required")
    return name[:NAME_MAX]


def clean_source(value: Any) -> str:
    return " ".join(str(value or "").split())[:SOURCE_MAX]


def clean_note(value: Any) -> str:
    body = str(value or "").strip()
    if not body:
        raise CrmRuleError("note_required")
    if len(body) > NOTE_MAX:
        raise CrmRuleError("note_too_long")
    return body


def split_phone(value: Any) -> tuple[str | None, str | None]:
    """(phone_e164, phone_raw). Unknown format → (None, raw): the number stays visible."""
    raw = " ".join(str(value or "").split())[:PHONE_RAW_MAX]
    if not raw:
        return None, None
    return normalize_phone(raw), raw


def looks_like_timezone(value: Any) -> bool:
    """Shape check before asking Postgres whether the IANA name exists."""
    name = str(value or "").strip()
    return 0 < len(name) <= 64 and bool(_TIMEZONE_RE.match(name))


def in_digest_window(local_time: time) -> bool:
    return DIGEST_WINDOW_START <= local_time < DIGEST_WINDOW_END


def digest_idempotency_key(account_id: str, local_date: date) -> str:
    return f"crm_digest:{account_id}:{local_date.isoformat()}"


def group_step(next_step: str | None) -> str:
    return next_step if next_step in NEXT_STEPS else "ping"


def digest_text(counts: dict[str, int], url: str) -> str | None:
    """«Сегодня в ежедневнике: пригласить на встречу — 3, …» + ссылка; None — дел нет."""
    parts = [
        f"{STEP_TITLES[step][0].lower()}{STEP_TITLES[step][1:]} — {int(counts[step])}"
        for step in NEXT_STEPS
        if int(counts.get(step) or 0) > 0
    ]
    if not parts:
        return None
    return "Сегодня в ежедневнике: " + ", ".join(parts) + f".\n\n{url}"


def group_today(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """«Сегодня»: contacts with next_at <= today, grouped by step in a fixed order."""
    due = [row for row in rows if row.get("next_at") is not None and row["next_at"] <= today]
    groups = []
    for step in NEXT_STEPS:
        members = [row for row in due if group_step(row.get("next_step")) == step]
        if members:
            groups.append({"step": step, "title": STEP_TITLES[step], "contacts": members})
    return {
        "date": today.isoformat(),
        "groups": groups,
        "overdue": sum(1 for row in due if row["next_at"] < today),
    }


# ---- access ---------------------------------------------------------------


@dataclass(frozen=True)
class CrmViewer:
    telegram_user_id: int
    is_preview_admin: bool
    partner_paid: bool
    ref_code: str | None = None
    public_profile: Any = None


def access_lock_reason(viewer: CrmViewer, pilot_ids: frozenset[int]) -> str | None:
    """None — the diary is open; otherwise the API error code.

    Preview admins (billing owner, super admins) always pass: the owner checks
    the pilot. With a pilot list only its people pass; then paid PRO decides.
    """
    if viewer.is_preview_admin:
        return None
    if pilot_ids and int(viewer.telegram_user_id) not in pilot_ids:
        return "crm_pilot_only"
    return None if viewer.partner_paid else "pro_required"


# ---- site leads -------------------------------------------------------------

_PHONEISH_RE = re.compile(r"[\d\s()+\-.]{7,25}")


def phone_from_lead_contact(contact: Any) -> tuple[str | None, str | None]:
    """A lead's «contact» is a phone, a @handle or an e-mail. Only a phone-shaped
    string becomes phone_e164/phone_raw; anything else goes to the first note."""
    raw = " ".join(str(contact or "").split())
    if not raw or not _PHONEISH_RE.fullmatch(raw):
        return None, None
    e164 = normalize_phone(raw)
    return (e164, raw[:PHONE_RAW_MAX]) if e164 else (None, None)


def lead_note_text(*, contact: Any, product_name: Any, comment: Any, phone_known: bool) -> str:
    lines = ["Заявка с сайта."]
    product = " ".join(str(product_name or "").split())
    if product:
        lines.append(f"Интерес: {product}")
    if not phone_known and str(contact or "").strip():
        lines.append(f"Контакт: {' '.join(str(contact).split())}")
    text = " ".join(str(comment or "").split())
    if text:
        lines.append(f"Комментарий: {text}")
    return "\n".join(lines)[:NOTE_MAX]


# ---- export -------------------------------------------------------------------

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> str:
    """Spreadsheet-safe cell: a leading = + - @ would run as a formula in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


EXPORT_HEADER = ("Имя", "Телефон", "Откуда знакомы", "Статус", "Следующий шаг", "Дата", "Заметки")


def export_row(row: dict[str, Any]) -> list[str]:
    next_at = row.get("next_at")
    return [
        csv_cell(row.get("name")),
        csv_cell(row.get("phone_e164") or row.get("phone_raw") or ""),
        csv_cell(row.get("source") or ""),
        STATUS_TITLES.get(str(row.get("status")), str(row.get("status") or "")),
        STEP_TITLES.get(str(row.get("next_step")), "") if row.get("next_step") else "",
        next_at.strftime("%d.%m.%Y") if isinstance(next_at, date) else "",
        csv_cell(row.get("notes") or ""),
    ]
