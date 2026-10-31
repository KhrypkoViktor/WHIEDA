"""Сообщения после Живой встречи: «был» / «не был», визитка партнёра, итог партнёру (срез 5 бот-воронки, 31.10.2026).

Через 30 минут после конца встречи (``starts_at + duration_min``) и в течение двух часов шаг воркера
(``app/funnel/reminders.py: reminders_step``) разбирает записи встреч ``planned`` / ``done``:

* ``joined`` (нажал «Присоединиться») → ``attended``, гостю — визитка партнёра (``app/funnel/partner_card.py``: фото с
  подписью и кнопками «Написать в Telegram / Max», «Сайт»; гостю с рекламы — две визитки подряд: владельца WWC и
  ``onlineelena``);
* ``registered`` (не нажал) → ``no_show``, гостю — «не дошли», запись встречи (кнопка-ссылка, если владелец задал
  ``recording_url``) и ближайшие встречи кнопками «Записаться» (``mreg:``);
* карточка CRM гостя (если есть): метка ``был дд.мм`` / ``не дошёл дд.мм``, заметка, шаг «Напомнить о себе» на сегодня
  (``card_attendance_event``);
* партнёру — по одному сообщению на владельца, у которого были гости на этой встрече: «Живая встреча <дата>: были — …;
  записались, но не пришли — …» и кнопка «Открыть CRM». Владелец — ``website_leads.assigned_owner_id``; гости без
  заявки и с рекламы — ``PLATFORM_ORGANIC_OWNER_ID``.

Отметки: ``reminders_sent.after_sent`` у записи (ставится тем же ``update``, что меняет статус, — повторный проход ничего
не шлёт); ключ outbox гостю ``mafter:<запись>:<n>``, партнёру ``msum:<встреча>:<владелец>``. После обработки всех записей
встреча ``planned`` становится ``done`` (и итог партнёрам строится из базы, поэтому доживает перезапуск воркера).
Запись, на которой обработка упала, остаётся прежней и повторяется следующим проходом (пока не закончится окно в 2 часа);
итог партнёру и перевод встречи в ``done`` ждут, пока необработанных записей не останется.

Имён и телефонов в логах нет: номера записей и встреч, классы ошибок.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.crm.service import crm_url, load_viewer, lock_reason, safe_error
from app.db import fetch_all, fetch_one, tenant_connection
from app.funnel import texts
from app.funnel.guest import LIST_MEETINGS, Rows, register_rows
from app.funnel.outbound import enqueue_guest_message
from app.funnel.partner_card import OwnerCard, card_intro, card_rows, card_text, guest_cards
from app.jobs.outbox import enqueue_outbox_event
from app.leads.meeting_leads import card_attendance_event, owner_chat, plain_no_links
from app.meetings import registrations
from app.meetings.service import format_msk, normalize_join_url
from app.settings import get_settings

logger = logging.getLogger("whieda.funnel.after_meeting")

EVENT_GUEST = "meeting_after"
EVENT_SUMMARY = "meeting_summary"
AFTER_KEY = "after_sent"
# Когда после конца встречи писать и как долго ещё брать встречу в работу.
AFTER_DELAY = timedelta(minutes=30)
AFTER_WINDOW = timedelta(hours=2)
MEETINGS_LIMIT = 20
NAME_MAX = 40
NAMES_SHOWN = 30
# Вторая визитка уходит чуть позже первой: по порядку времени отправки видно, кто первый.
CARD_SPACING = timedelta(seconds=2)

_MEETINGS_SQL = """
    select meeting_id, title, starts_at, duration_min, status, recording_url
    from meetings
    where tenant_id = %(tenant_id)s
      and kind = 'live' and status in ('planned', 'done')
      and starts_at + duration_min * interval '1 minute' + %(delay)s <= %(now)s
      and starts_at + duration_min * interval '1 minute' + %(delay)s + %(window)s > %(now)s
    order by starts_at, meeting_id
    limit %(limit)s
"""

_REGISTRATIONS_SQL = """
    select r.registration_id, r.lead_id::text as lead_id, r.channel, r.channel_user_id, r.status,
           l.assigned_owner_id, coalesce(l.metadata, '{}'::jsonb) ->> 'crm_card' as crm_card
    from meeting_registrations r
    left join website_leads l on l.tenant_id = r.tenant_id and l.lead_id = r.lead_id
    where r.tenant_id = %s and r.meeting_id = %s and r.status in ('registered', 'joined')
      and (coalesce(r.reminders_sent, '{}'::jsonb) -> 'after_sent') is null
    order by r.registration_id
"""

# Статус и отметка меняются одним оператором и только у активной записи без отметки: два воркера и повторный проход
# не разберут запись дважды.
_CLAIM_SQL = """
    update meeting_registrations
    set status = case when status = 'joined' then 'attended' else 'no_show' end,
        reminders_sent = coalesce(reminders_sent, '{}'::jsonb) || jsonb_build_object('after_sent', %s::text),
        updated_at = now()
    where tenant_id = %s and registration_id = %s and status in ('registered', 'joined')
      and (coalesce(reminders_sent, '{}'::jsonb) -> 'after_sent') is null
    returning status
"""

_LEFT_SQL = """
    select count(*) as n from meeting_registrations
    where tenant_id = %s and meeting_id = %s and status in ('registered', 'joined')
"""

_SUMMARY_SQL = """
    select r.status, l.name, l.assigned_owner_id
    from meeting_registrations r
    left join website_leads l on l.tenant_id = r.tenant_id and l.lead_id = r.lead_id
    where r.tenant_id = %s and r.meeting_id = %s and r.status in ('attended', 'no_show')
      and (coalesce(r.reminders_sent, '{}'::jsonb) -> 'after_sent') is not null
    order by r.registration_id
"""

_DONE_SQL = """
    update meetings set status = 'done', updated_at = now()
    where tenant_id = %s and meeting_id = %s and status = 'planned'
"""


# ---- чистые функции ------------------------------------------------------------------------------


def after_key(registration_id: int, number: int) -> str:
    """``mafter:<запись>:<n>`` — ключ outbox сообщения гостю (у визиток n = 1, 2, у «не дошли» n = 1)."""
    return f"mafter:{int(registration_id)}:{int(number)}"


def summary_key(meeting_id: int, owner_id: str) -> str:
    """``msum:<встреча>:<владелец>`` — ключ outbox итога партнёру."""
    return f"msum:{int(meeting_id)}:{owner_id}"


def _names_text(names: list[str], anonymous: int) -> str:
    shown = names[:NAMES_SHOWN]
    parts = list(shown)
    if len(names) > NAMES_SHOWN:
        parts.append(f"…и ещё {len(names) - NAMES_SHOWN}")
    if anonymous:
        parts.append(texts.SUMMARY_ANONYMOUS.format(count=anonymous))
    return ", ".join(parts)


def summary_text(
    when: str, attended: list[str], no_show: list[str], *, anonymous_attended: int = 0, anonymous_no_show: int = 0
) -> str:
    """«Живая встреча чт 05.11 19:30 МСК:» + строки «Были: …» и «Записались, но не пришли: …» (пустая строка не пишется).
    Имена уже очищены; гости без заявки считаются числом."""
    lines = [texts.SUMMARY_HEAD.format(when=when)]
    if attended or anonymous_attended:
        lines.append(texts.SUMMARY_ATTENDED.format(names=_names_text(attended, anonymous_attended)))
    if no_show or anonymous_no_show:
        lines.append(texts.SUMMARY_NO_SHOW.format(names=_names_text(no_show, anonymous_no_show)))
    return "\n".join(lines)


def no_show_message(
    meeting: dict[str, Any],
    upcoming: list[dict[str, Any]],
    registered_ids: frozenset[int],
    *,
    now: datetime,
) -> tuple[str, Rows]:
    """Текст и кнопки гостю, который не пришёл: запись (если есть ссылка) и ближайшие встречи кнопками ``mreg:``."""
    text = texts.AFTER_NO_SHOW.format(when=format_msk(meeting["starts_at"], now=now))
    rows: Rows = []
    recording = normalize_join_url(meeting.get("recording_url"))
    if recording:
        rows.append([(texts.AFTER_NO_SHOW_RECORDING_BUTTON, recording)])
    if upcoming:
        text += "\n" + texts.AFTER_NO_SHOW_NEXT
        rows.extend(register_rows(upcoming, registered_ids, now=now))
    else:
        text += "\n" + texts.GUEST_NO_MEETINGS
    return text, rows


# ---- база ----------------------------------------------------------------------------------------


@dataclass
class _Pass:
    """Что известно о встрече на этом проходе: кэш визиток и ближайших встреч (одни на всех гостей встречи)."""

    tenant_id: str
    binding_id: str
    meeting: dict[str, Any]
    now: datetime
    cards: dict[str | None, tuple[list[OwnerCard], bool]] = field(default_factory=dict)
    upcoming: list[dict[str, Any]] | None = None
    counts: dict[str, int] = field(default_factory=lambda: {"attended": 0, "no_show": 0, "summaries": 0})


async def _cards_for(conn: Any, ctx: _Pass, owner_id: str | None) -> tuple[list[OwnerCard], bool]:
    key = owner_id or None
    if key not in ctx.cards:
        ctx.cards[key] = await guest_cards(conn, ctx.tenant_id, key)
    return ctx.cards[key]


async def _send_attended(conn: Any, ctx: _Pass, row: dict[str, Any]) -> None:
    cards, partner = await _cards_for(conn, ctx, row.get("assigned_owner_id"))
    registration_id = int(row["registration_id"])
    common = {
        "tenant_id": ctx.tenant_id,
        "binding_id": ctx.binding_id,
        "channel": str(row["channel"]),
        "user_id": int(row["channel_user_id"]),
        "event_type": EVENT_GUEST,
    }
    if not cards:
        await enqueue_guest_message(
            conn, idempotency_key=after_key(registration_id, 1), text=texts.AFTER_THANKS, due_at=ctx.now, **common
        )
        return
    for position, card in enumerate(cards):
        await enqueue_guest_message(
            conn,
            idempotency_key=after_key(registration_id, position + 1),
            text=card_text(card, card_intro(card, position=position, partner=partner)),
            rows=card_rows(card),
            photo_url=card.photo_url,
            due_at=ctx.now + CARD_SPACING * position,
            **common,
        )


async def _send_no_show(conn: Any, ctx: _Pass, row: dict[str, Any]) -> None:
    if ctx.upcoming is None:
        ctx.upcoming = await registrations.open_meetings(conn, ctx.tenant_id, limit=LIST_MEETINGS, now=ctx.now)
    active = await registrations.active_registrations(
        conn, ctx.tenant_id, channel=str(row["channel"]), channel_user_id=int(row["channel_user_id"]), limit=10, now=ctx.now
    )
    registered = frozenset(int(meeting["meeting_id"]) for _, meeting in active)
    text, rows = no_show_message(ctx.meeting, ctx.upcoming, registered, now=ctx.now)
    await enqueue_guest_message(
        conn,
        tenant_id=ctx.tenant_id,
        binding_id=ctx.binding_id,
        channel=str(row["channel"]),
        user_id=int(row["channel_user_id"]),
        idempotency_key=after_key(int(row["registration_id"]), 1),
        event_type=EVENT_GUEST,
        text=text,
        rows=rows,
        due_at=ctx.now,
    )


async def _process_registration(conn: Any, ctx: _Pass, row: dict[str, Any]) -> str | None:
    """Одна запись: статус и отметка, сообщение гостю, карточка CRM. Итог — ``attended`` / ``no_show`` или None, если
    запись уже разобрана. Вызывающий держит savepoint: исключение отменяет и статус, и сообщения."""
    claimed = await fetch_one(conn, _CLAIM_SQL, (ctx.now.isoformat(), ctx.tenant_id, int(row["registration_id"])))
    if claimed is None:
        return None
    status = str(claimed["status"])
    if status == "attended":
        await _send_attended(conn, ctx, row)
    else:
        await _send_no_show(conn, ctx, row)
    await card_attendance_event(
        conn, ctx.tenant_id, row.get("crm_card"), "attended" if status == "attended" else "no_show",
        starts_at=ctx.meeting["starts_at"], now=ctx.now,
    )
    return status


async def crm_button_url(conn: Any, tenant_id: str, telegram_user_id: int | None) -> str | None:
    """Ссылка кнопки «Открыть CRM» (превращается во вход на сайт при отправке) — только если CRM у владельца открыта."""
    if telegram_user_id is None:
        return None
    viewer = await load_viewer(tenant_id, int(telegram_user_id), conn=conn)
    return crm_url(viewer) if lock_reason(viewer) is None else None


async def _send_summaries(conn: Any, ctx: _Pass) -> int:
    """По сообщению каждому владельцу, у которого были гости на этой встрече (из базы: доживает перезапуск воркера)."""
    organic = get_settings().platform_organic_owner_id
    rows = await fetch_all(conn, _SUMMARY_SQL, (ctx.tenant_id, int(ctx.meeting["meeting_id"])))
    by_owner: dict[str, dict[str, Any]] = {}
    for row in rows:
        owner = str(row.get("assigned_owner_id") or organic)
        group = by_owner.setdefault(owner, {"attended": [], "no_show": [], "anon_attended": 0, "anon_no_show": 0})
        name = plain_no_links(row.get("name"), NAME_MAX) if row.get("name") else ""
        if name:
            group[row["status"]].append(name)
        else:
            group["anon_" + row["status"]] += 1
    when = format_msk(ctx.meeting["starts_at"], now=ctx.now)
    sent = 0
    for owner, group in by_owner.items():
        chat = await owner_chat(conn, ctx.tenant_id, owner)
        if chat is None:
            logger.info("meeting_summary_no_chat", extra={"meeting_id": ctx.meeting["meeting_id"]})
            continue
        chat_id, telegram_user_id = chat
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": summary_text(
                when, group["attended"], group["no_show"],
                anonymous_attended=group["anon_attended"], anonymous_no_show=group["anon_no_show"],
            ),
            "binding_id": ctx.binding_id,
        }
        crm = await crm_button_url(conn, ctx.tenant_id, telegram_user_id)
        if crm:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": texts.OPEN_CRM_BUTTON, "url": crm}]]}
            payload["site_login_user_id"] = telegram_user_id
        queued = await enqueue_outbox_event(
            conn,
            tenant_id=ctx.tenant_id,
            event_type=EVENT_SUMMARY,
            idempotency_key=summary_key(int(ctx.meeting["meeting_id"]), owner),
            payload=payload,
            due_at=ctx.now,
        )
        if queued and queued.get("created"):
            sent += 1
    return sent


async def process_meeting(conn: Any, ctx: _Pass) -> int:
    """Разобрать записи встречи, затем (когда необработанных не осталось) разослать итог партнёрам и перевести встречу
    ``planned`` → ``done``. Возвращает число разобранных записей."""
    meeting_id = int(ctx.meeting["meeting_id"])
    processed = failed = 0
    for row in await fetch_all(conn, _REGISTRATIONS_SQL, (ctx.tenant_id, meeting_id)):
        try:
            async with conn.transaction():
                outcome = await _process_registration(conn, ctx, row)
        except Exception as exc:
            failed += 1
            logger.warning(
                "meeting_after_registration_failed",
                extra={"meeting_id": meeting_id, "registration_id": row.get("registration_id"), **safe_error(exc)},
            )
            continue
        if outcome:
            processed += 1
            ctx.counts[outcome] += 1
    if failed or (processed == 0 and ctx.meeting["status"] == "done"):
        return processed
    try:
        async with conn.transaction():
            left = await fetch_one(conn, _LEFT_SQL, (ctx.tenant_id, meeting_id))
            if int(left["n"]) == 0:
                ctx.counts["summaries"] += await _send_summaries(conn, ctx)
                async with conn.cursor() as cur:
                    await cur.execute(_DONE_SQL, (ctx.tenant_id, meeting_id))
    except Exception as exc:
        logger.warning("meeting_after_finish_failed", extra={"meeting_id": meeting_id, **safe_error(exc)})
    return processed


async def process_after_meetings(tenant_id: str, binding_id: str, *, now: datetime | None = None) -> int:
    """Сообщения после встреч, закончившихся 30 минут – 2 часа 30 минут назад. Возвращает число разобранных записей.
    Сбой выборки или одной встречи — в журнал, шаг жив."""
    moment = now or datetime.now(timezone.utc)
    total = 0
    try:
        async with tenant_connection(tenant_id) as conn:
            meetings = await fetch_all(
                conn,
                _MEETINGS_SQL,
                {"tenant_id": tenant_id, "delay": AFTER_DELAY, "window": AFTER_WINDOW, "now": moment, "limit": MEETINGS_LIMIT},
            )
            for meeting in meetings:
                ctx = _Pass(tenant_id, binding_id, dict(meeting), moment)
                try:
                    async with conn.transaction():
                        total += await process_meeting(conn, ctx)
                except Exception as exc:
                    logger.warning(
                        "meeting_after_failed", extra={"meeting_id": meeting.get("meeting_id"), **safe_error(exc)}
                    )
                    continue
                if any(ctx.counts.values()):
                    logger.info("meeting_after", extra={"meeting_id": meeting["meeting_id"], **ctx.counts})
    except Exception as exc:
        logger.warning("meeting_after_plan_failed", extra=safe_error(exc))
    return total
