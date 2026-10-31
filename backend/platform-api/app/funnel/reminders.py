"""Напоминания о Живой встрече и шаг воркера «встречи» (срез 5 бот-воронки, 31.10.2026).

Записанным (``meeting_registrations.status = 'registered'``) на живую запланированную встречу напоминание приходит
в тот мессенджер, где человек записался (Telegram — строка ``platform_outbox``, Max — ``enqueue_max_notification``):

  day        за 24 часа, окно 45 минут;
  two_hours  за 2 часа, окно 30 минут;
  hour       за 1 час, окно 20 минут, кнопка «Присоединиться»;
  five       за 5 минут, окно 10 минут (но не позже начала), кнопка «Присоединиться».

Окна и порядок — как у вебинаров Академии (``app/academy/webinars.py: REMINDERS``), сам код ALMIRA не тронут. Напоминание
уходит, пока ``время ≤ сейчас < время + окно`` (опоздавший воркер не пришлёт «через час» за 40 минут). Ключ outbox
``mrem:<запись>:<вид>``, отметка — ``reminders_sent[<вид>]`` в записи: проверка и отметка — один ``update`` в той же
транзакции, что и постановка сообщения, так что два воркера и повторный проход дают не больше одного сообщения.
Если человек записался позже времени «за 24 часа» или «за 2 часа», такое напоминание ему не шлётся: он только что получил
подтверждение записи. «За час» и «за 5 минут» (с кнопкой входа) идут всегда.

Кнопка «Присоединиться» — ``mjoin:<запись>`` (``app/funnel/registration.py: join``): ссылка входа выдаётся по нажатию.

Шаг ``reminders_step`` идёт раз в минуту рядом с ``no_bot_alert_step`` (``app/jobs/worker.py``), только для арендатора
whieda и только в процессе с ``PLATFORM_SCHEDULED_NOTIFY_BINDINGS``; следом за напоминаниями он запускает сообщения
после встречи (``app/funnel/after_meeting.py``). Имён и телефонов в логах нет: номера записей и классы ошибок.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.crm.service import safe_error
from app.db import fetch_all, fetch_one, tenant_connection
from app.funnel import texts
from app.funnel.guest import Rows
from app.funnel.outbound import enqueue_guest_message
from app.meetings.service import format_msk, as_msk
from app.telegram.bindings import BotBindingContext

logger = logging.getLogger("whieda.funnel.reminders")

TENANT_ID = "whieda"
EVENT_TYPE = "meeting_reminder"
BATCH_SIZE = 1000

# (вид, за сколько до начала, окно отправки, кнопка «Присоединиться»)
REMINDERS: tuple[tuple[str, timedelta, timedelta, bool], ...] = (
    ("day", timedelta(hours=24), timedelta(minutes=45), False),
    ("two_hours", timedelta(hours=2), timedelta(minutes=30), False),
    ("hour", timedelta(hours=1), timedelta(minutes=20), True),
    ("five", timedelta(minutes=5), timedelta(minutes=10), True),
)
KINDS = tuple(kind for kind, *_ in REMINDERS)
# Самое раннее напоминание уходит за сутки: встречи позже этого горизонта не выбираем.
HORIZON = timedelta(hours=24)

_CANDIDATES_SQL = """
    select r.registration_id, r.channel, r.channel_user_id, r.registered_at, r.reminders_sent,
           m.meeting_id, m.starts_at
    from meeting_registrations r
    join meetings m on m.tenant_id = r.tenant_id and m.meeting_id = r.meeting_id
    where r.tenant_id = %(tenant_id)s
      and r.status = 'registered'
      and m.kind = 'live' and m.status = 'planned'
      and m.starts_at > %(now)s and m.starts_at <= %(now)s + %(horizon)s
    order by m.starts_at, r.registration_id
    limit %(limit)s
"""

# Отметка ставится только пока запись активна и вида нет в reminders_sent: проверка и запись — один оператор.
_CLAIM_SQL = """
    update meeting_registrations
    set reminders_sent = coalesce(reminders_sent, '{}'::jsonb) || jsonb_build_object(%s::text, %s::text),
        updated_at = now()
    where tenant_id = %s and registration_id = %s and status = 'registered'
      and (coalesce(reminders_sent, '{}'::jsonb) -> %s::text) is null
    returning registration_id
"""


# ---- чистые функции ------------------------------------------------------------------------------


def reminder_key(registration_id: int, kind: str) -> str:
    """``mrem:<запись>:<вид>`` — ключ идемпотентности строки outbox."""
    return f"mrem:{int(registration_id)}:{kind}"


def due_kinds(
    starts_at: datetime,
    now: datetime,
    *,
    registered_at: datetime | None = None,
    sent: Mapping[str, Any] | None = None,
) -> list[str]:
    """Напоминания, время которых пришло, а окно не закрылось и не закрыто началом встречи; уже отправленные
    (``sent``) и «за сутки» / «за 2 часа» для записавшихся позже своего времени пропускаются."""
    already = sent or {}
    due: list[str] = []
    for kind, before, window, with_button in REMINDERS:
        moment = starts_at - before
        until = min(moment + window, starts_at)
        if not (moment <= now < until) or kind in already:
            continue
        if not with_button and registered_at is not None and registered_at > moment:
            continue
        due.append(kind)
    return due


def reminder_text(kind: str, starts_at: datetime, *, now: datetime | None = None) -> str:
    """Текст напоминания. Время и дата — московские (``format_msk``)."""
    when = format_msk(starts_at, now=now)
    time = f"{as_msk(starts_at):%H:%M}"
    if kind == "day":
        return texts.REMINDER_DAY.format(when=when)
    if kind == "two_hours":
        return texts.REMINDER_TWO_HOURS.format(time=time)
    if kind == "hour":
        return texts.REMINDER_HOUR.format(time=time)
    if kind == "five":
        return texts.REMINDER_FIVE
    raise ValueError("unknown reminder kind")


def reminder_rows(kind: str, registration_id: int) -> Rows:
    """«Присоединиться» (``mjoin:<запись>``) под напоминаниями «за час» и «за 5 минут»; у остальных кнопок нет."""
    if any(k == kind and with_button for k, _, _, with_button in REMINDERS):
        return [[(texts.JOIN_BUTTON, f"{texts.JOIN_CALLBACK_PREFIX}{int(registration_id)}")]]
    return []


# ---- база ----------------------------------------------------------------------------------------


async def find_candidates(conn: Any, tenant_id: str, now: datetime) -> list[dict[str, Any]]:
    """Активные записи на живые запланированные встречи, которые начнутся в ближайшие сутки."""
    return await fetch_all(
        conn,
        _CANDIDATES_SQL,
        {"tenant_id": tenant_id, "now": now, "horizon": HORIZON, "limit": BATCH_SIZE},
    )


async def remind_one(
    conn: Any, *, tenant_id: str, binding_id: str, row: dict[str, Any], kind: str, now: datetime
) -> str:
    """Одно напоминание. Итог: ``queued``, ``duplicate`` (ключ уже был) или ``raced`` (отметка уже стоит или запись
    перестала быть активной). Вызывающий держит savepoint: исключение отменяет и отметку, и сообщение."""
    registration_id = int(row["registration_id"])
    claimed = await fetch_one(conn, _CLAIM_SQL, (kind, now.isoformat(), tenant_id, registration_id, kind))
    if claimed is None:
        return "raced"
    queued = await enqueue_guest_message(
        conn,
        tenant_id=tenant_id,
        binding_id=binding_id,
        channel=str(row["channel"]),
        user_id=int(row["channel_user_id"]),
        idempotency_key=reminder_key(registration_id, kind),
        event_type=EVENT_TYPE,
        text=reminder_text(kind, row["starts_at"], now=now),
        rows=reminder_rows(kind, registration_id),
        due_at=now,
    )
    return "queued" if queued and queued.get("created") else "duplicate"


async def enqueue_reminders(tenant_id: str, binding_id: str, *, now: datetime | None = None) -> int:
    """Ставит в очередь все напоминания, время которых пришло; возвращает их число. Сбой одной записи (её savepoint
    откатывается) остальных не останавливает; сбой выборки — в журнал, шаг жив."""
    moment = now or datetime.now(timezone.utc)
    queued = 0
    try:
        async with tenant_connection(tenant_id) as conn:
            for row in await find_candidates(conn, tenant_id, moment):
                for kind in due_kinds(
                    row["starts_at"], moment, registered_at=row.get("registered_at"), sent=row.get("reminders_sent")
                ):
                    try:
                        async with conn.transaction():
                            outcome = await remind_one(
                                conn, tenant_id=tenant_id, binding_id=binding_id, row=row, kind=kind, now=moment
                            )
                    except Exception as exc:
                        logger.warning(
                            "meeting_reminder_failed",
                            extra={"kind": kind, "registration_id": row.get("registration_id"), **safe_error(exc)},
                        )
                        continue
                    if outcome == "queued":
                        queued += 1
    except Exception as exc:
        logger.warning("meeting_reminders_plan_failed", extra=safe_error(exc))
    if queued:
        logger.info("meeting_reminders", extra={"tenant_id": tenant_id, "queued": queued})
    return queued


async def reminders_step(bindings: Mapping[str, BotBindingContext], *, now: datetime | None = None) -> dict[str, int]:
    """Шаг воркера (раз в минуту, из ``scheduled_notifications_step``): напоминания, затем сообщения после встречи.
    ``bindings`` — боты этого процесса по арендаторам; нет бота whieda (процесс стенда) — ничего не делает и в базу
    не ходит. Возвращает ``{"meeting_reminders": N, "meeting_after": M}`` (поставлено напоминаний; обработано записей)."""
    binding = bindings.get(TENANT_ID)
    if binding is None:
        return {}
    from app.funnel.after_meeting import process_after_meetings

    result = {"meeting_reminders": await enqueue_reminders(TENANT_ID, binding.binding_id, now=now)}
    result["meeting_after"] = await process_after_meetings(TENANT_ID, binding.binding_id, now=now)
    return result
