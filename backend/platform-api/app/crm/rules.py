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

«Сделано» (POST …/done, CRM v2) — the current step is done, the card moves on
(``plan_done``):

| step done | status after | next_step, next_at                                  |
|-----------|--------------|-----------------------------------------------------|
| invite    | invited      | result @ the meeting date (meeting_at is required)  |
| result    | presented    | decide @ today + 2 days                             |
| decide    | deciding     | ping, no date — the partner picks it (or sends one) |
| resume    | deciding     | ping, no date — the partner picks it (or sends one) |
| ping      | unchanged    | client / partner: ping @ today + 30; else no date   |

A next_at sent with «Сделано» wins, as in a PATCH. A card that stays «Пауза»
(step ping on a paused card) needs that date: 400 next_at_required without it,
as everywhere a pause gets no date. The step decides the move, not the status:
«Сделано» on a client's «Пригласить» makes the card «Приглашён» again.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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
# The same shape as the CHECK on crm_contacts.phone_e164: ASCII digits only.
E164_RE = re.compile(r"\+[0-9]{10,15}")


def as_e164(value: Any) -> str | None:
    """A normalized number that the database accepts, or None."""
    text = str(value or "")
    return text if E164_RE.fullmatch(text) else None


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
    return as_e164(normalize_phone(raw)), raw


def looks_like_timezone(value: Any) -> bool:
    """Shape check before asking Postgres whether the IANA name exists."""
    name = str(value or "").strip()
    return 0 < len(name) <= 64 and bool(_TIMEZONE_RE.match(name))


def in_digest_window(local_time: time) -> bool:
    return DIGEST_WINDOW_START <= local_time < DIGEST_WINDOW_END


def digest_idempotency_key(account_id: str, local_date: date) -> str:
    return f"crm_digest:{account_id}:{local_date.isoformat()}"


# «Через час встреча» (CRM v2): the planner runs every 5 minutes and queues the
# reminder when the meeting is 50–65 minutes away — three passes. A meeting set
# later than 50 minutes ahead gets none: the partner has just put it in.
MEETING_REMIND_FROM = timedelta(minutes=50)
MEETING_REMIND_TO = timedelta(minutes=65)


def meeting_idempotency_key(contact_id: str, meeting_at: datetime) -> str:
    """One reminder per card and meeting time: a moved meeting gets its own."""
    return f"crm_meeting:{contact_id}:{int(meeting_at.timestamp())}"


def group_step(next_step: str | None) -> str:
    return next_step if next_step in NEXT_STEPS else "ping"


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


# ---- CRM v2: «Сделано», перенос, метки, приоритет, лента -------------------------

ACTIVITY_KINDS: tuple[str, ...] = ("note", "status", "step", "call", "message", "meeting", "created", "lead")
LOG_KINDS: tuple[str, ...] = ("call", "message")
# Как кнопки карточки на сайте (src/lib/crm/links.js): звонок и мессенджеры.
CHANNELS: tuple[str, ...] = ("phone", "whatsapp", "telegram", "viber", "max", "sms")

TAG_MAX = 32
TAGS_MAX = 10
SNOOZE_MAX_DAYS = 366
# «Удалено · Вернуть»: столько карточка ждёт восстановления, потом воркер её стирает.
RESTORE_WINDOW = timedelta(hours=24)

# «Сегодня» в приложении и утреннее сообщение: шаги, после которых нужно позвонить;
# остальное («Напомнить о себе») — напомнить.
CALL_STEPS: tuple[str, ...] = ("invite", "result", "decide", "resume")
SECTION_TITLES: dict[str, str] = {
    "meetings": "Встречи сегодня",
    "call": "Позвонить",
    "remind": "Напомнить",
    "overdue": "Просрочено",
}


@dataclass(frozen=True)
class DonePlan:
    status: str
    next_step: str | None
    next_at: date | None


def plan_done(
    *,
    status: str,
    next_step: str | None,
    today: date,
    meeting_date: date | None,
    next_at: date | None = None,
    next_at_given: bool = False,
) -> DonePlan:
    """The card after «Сделано» on its current step (table in the module docstring).

    ``meeting_date`` — the local date of the meeting the partner sent with «Сделано»
    or of a future meeting already on the card; «invite» cannot be done without it.
    """
    if status not in STATUSES:
        raise CrmRuleError("invalid_status")
    if next_step not in NEXT_STEPS:
        raise CrmRuleError("no_next_step")
    if next_step == "invite":
        if meeting_date is None:
            raise CrmRuleError("meeting_at_required")
        new_status, plan = "invited", StepPlan("result", meeting_date)
    elif next_step == "result":
        new_status, plan = "presented", default_plan("presented", today=today)
    elif next_step in ("decide", "resume"):
        new_status, plan = "deciding", default_plan("deciding", today=today)
    else:  # ping
        new_status = status
        plan = default_plan(status, today=today) if status in ("client", "partner") else StepPlan("ping", None)
    when = next_at if next_at_given else plan.next_at
    if new_status == "paused" and when is None:
        raise CrmRuleError("next_at_required")
    return DonePlan(new_status, plan.next_step, when)


def snooze_until(today: date, *, days: Any = None, on: date | None = None) -> date:
    """«Перенести»: exactly one of ``days`` (1…366) or a date from today to a year ahead."""
    if (days is None) == (on is None):
        raise CrmRuleError("snooze_required")
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= SNOOZE_MAX_DAYS:
            raise CrmRuleError("invalid_snooze")
        return today + timedelta(days=days)
    if not isinstance(on, date) or isinstance(on, datetime) or not today <= on <= today + timedelta(days=SNOOZE_MAX_DAYS):
        raise CrmRuleError("invalid_snooze")
    return on


def clean_tag(value: Any) -> str:
    """«#vip  клиент» → «vip клиент»; empty stays empty."""
    return " ".join(str(value or "").split()).lstrip("#").strip()


def clean_tags(value: Any) -> list[str]:
    """Up to TAGS_MAX tags of TAG_MAX characters; the first spelling of a tag wins
    (VIP and vip are one tag), empty ones are dropped, null clears the list."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise CrmRuleError("invalid_tags")
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise CrmRuleError("invalid_tags")
        tag = clean_tag(item)
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            raise CrmRuleError("tag_too_long")
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
    if len(tags) > TAGS_MAX:
        raise CrmRuleError("too_many_tags")
    return tags


def clean_priority(value: Any) -> int:
    """⭐ on the card: 0 or 1 (true/false accepted)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise CrmRuleError("invalid_priority")


def clean_log(kind: Any, channel: Any) -> tuple[str, str]:
    """A tap on «Позвонить» / a messenger: (kind, channel). A call defaults to the phone."""
    if kind not in LOG_KINDS:
        raise CrmRuleError("invalid_kind")
    channel = channel or ("phone" if kind == "call" else None)
    if channel not in CHANNELS:
        raise CrmRuleError("invalid_channel")
    return kind, channel


def today_sections(rows: list[dict[str, Any]], today: date, *, split_overdue: bool = True) -> list[dict[str, Any]]:
    """«Сегодня» by meaning: meetings today, call, remind, overdue.

    ``rows`` carry ``meeting_today`` (the meeting falls on the account's local
    today); such a card is listed once, under meetings. ``split_overdue=False``
    (the morning message) keeps overdue cards in «позвонить» / «напомнить».
    Input order is kept inside a section; meetings go by time.
    """
    meetings = sorted((row for row in rows if row.get("meeting_today")), key=lambda row: row["meeting_at"])
    due = [
        row for row in rows
        if not row.get("meeting_today") and row.get("next_at") is not None and row["next_at"] <= today
    ]
    overdue = [row for row in due if split_overdue and row["next_at"] < today]
    current = [row for row in due if not (split_overdue and row["next_at"] < today)]
    call = [row for row in current if group_step(row.get("next_step")) in CALL_STEPS]
    remind = [row for row in current if group_step(row.get("next_step")) not in CALL_STEPS]
    ordered = (("meetings", meetings), ("call", call), ("remind", remind), ("overdue", overdue))
    return [{"key": key, "title": SECTION_TITLES[key], "contacts": members} for key, members in ordered if members]


# ---- access ---------------------------------------------------------------


@dataclass(frozen=True)
class CrmViewer:
    telegram_user_id: int
    is_preview_admin: bool
    partner_paid: bool
    ref_code: str | None = None
    public_profile: Any = None


def access_lock_reason(viewer: CrmViewer, pilot_ids: frozenset[int] | None) -> str | None:
    """None — the diary is open; otherwise the API error code.

    Fail-closed: ``pilot_ids`` None means «every partner with paid PRO» ('*' in
    the setting); a set — only those people, and an empty set — nobody.
    Preview admins (billing owner, super admins) always pass: the owner checks
    the pilot. Then paid PRO decides.
    """
    if viewer.is_preview_admin:
        return None
    if pilot_ids is not None and int(viewer.telegram_user_id) not in pilot_ids:
        return "crm_pilot_only"
    return None if viewer.partner_paid else "pro_required"


# ---- site leads -------------------------------------------------------------

_PHONEISH_RE = re.compile(r"[\d\s()+\-.]{7,25}")


def phone_from_lead_contact(contact: Any) -> tuple[str | None, str | None]:
    """A lead's «contact» is a phone, a @handle or an e-mail. Only a phone-shaped
    string becomes phone_e164/phone_raw; anything else goes to the first note."""
    raw = " ".join(unicodedata.normalize("NFKC", str(contact or "")).split())
    if not raw or not _PHONEISH_RE.fullmatch(raw):
        return None, None
    e164 = as_e164(normalize_phone(raw))
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
        # A normalized number is not a formula: written as is (+79286729288).
        as_e164(row.get("phone_e164")) or csv_cell(row.get("phone_raw") or ""),
        csv_cell(row.get("source") or ""),
        STATUS_TITLES.get(str(row.get("status")), str(row.get("status") or "")),
        STEP_TITLES.get(str(row.get("next_step")), "") if row.get("next_step") else "",
        next_at.strftime("%d.%m.%Y") if isinstance(next_at, date) else "",
        csv_cell(row.get("notes") or ""),
    ]


# ---- message templates (CRM v2) ------------------------------------------------------

TEMPLATE_TITLE_MAX = 60
TEMPLATE_BODY_MAX = 1000
TEMPLATES_MAX = 30
# Подстановки, которые сайт заменяет перед отправкой: имя контакта и имя партнёра.
TEMPLATE_PLACEHOLDERS: tuple[str, ...] = ("{имя}", "{мое_имя}")

# Шесть шаблонов, которые партнёр получает при первом открытии «Шаблонов».
# Коротко и вежливо, без обещаний дохода и здоровья (ТЗ v2, раздел 4).
# Тексты — предложение исполнителя: лид согласует с владельцем.
DEFAULT_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "Приглашение",
        "{имя}, здравствуйте! Это {мое_имя}. Хочу пригласить вас на короткую встречу: "
        "расскажу, чем занимаюсь, и отвечу на вопросы. Когда вам удобно?",
    ),
    (
        "Напоминание о встрече",
        "{имя}, добрый день! Напоминаю о нашей встрече. "
        "Если планы поменялись, напишите — подберём другое время.",
    ),
    (
        "После презентации",
        "{имя}, спасибо, что нашли время на встречу! "
        "Если появились вопросы, пишите — с удовольствием отвечу.",
    ),
    (
        "Подумали?",
        "{имя}, добрый день! Удалось обдумать то, что мы обсуждали? "
        "Если остались вопросы, я на связи.",
    ),
    (
        "Возобновление",
        "{имя}, здравствуйте! Это {мое_имя}. Давно не общались — как ваши дела? "
        "Если тема ещё интересна, давайте созвонимся.",
    ),
    (
        "Спасибо клиенту",
        "{имя}, спасибо за доверие! Если появятся вопросы по заказу, пишите — я на связи.",
    ),
)


def clean_template_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise CrmRuleError("title_required")
    if len(title) > TEMPLATE_TITLE_MAX:
        raise CrmRuleError("title_too_long")
    return title


def clean_template_body(value: Any) -> str:
    body = str(value or "").strip()
    if not body:
        raise CrmRuleError("body_required")
    if len(body) > TEMPLATE_BODY_MAX:
        raise CrmRuleError("body_too_long")
    return body
