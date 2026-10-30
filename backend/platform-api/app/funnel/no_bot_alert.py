"""Алерт владельцу заявки: «записался(ась) на Живую встречу, но бота не открыл(а)» (срез 9 бот-воронки, 30.10.2026).

Человек оставил заявку на Живую встречу (``lead_kind = 'meeting'``, app/leads/meeting_leads.py) и должен
открыть бота по ссылке ``?start=lead_<public_id>``: бот ставит ``bot_opened_at`` (``mark_bot_opened``).
Если за три минуты бот так и не открыт, партнёру-владельцу заявки уходит сообщение с телефоном и кнопками
«WhatsApp» и «Открыть карточку»: напишите в WhatsApp или позвоните. Образец текста и паузы — алерт
рекламных заявок Академии (app/academy/ad_leads.py, ``OPEN_GRACE``), его код здесь не трогается.

Шаг воркера ``no_bot_alert_step`` идёт раз в минуту (app/jobs/worker.py, рядом с CRM-напоминаниями, тот же
гейт: процесс с ``PLATFORM_SCHEDULED_NOTIFY_BINDINGS``, только тенант whieda). Выборка: заявки без
``bot_opened_at``, созданные от суток до трёх минут назад, у которых нет отметки ``metadata.no_bot_alert_at``.
Новых таблиц нет: отметки лежат в ``website_leads.metadata``:

* ``no_bot_alert_at`` — алерт поставлен в очередь; ставится в той же транзакции и тем же ``update``, что
  проверяет ``bot_opened_at is null`` и отсутствие отметки, поэтому два воркера и гонка с открытием
  бота дают не больше одного сообщения;
* ``no_bot_alert_skip`` — алерт не нужен или невозможен (страница стенда, у владельца нет чата в Telegram):
  причина вместо отметки, чтобы заявку не перебирали каждую минуту;
* ``no_bot_alert_step`` — дата, на которую алерт поставил карточке шаг «Напомнить о себе» (открытие бота
  вернёт шаг по умолчанию, ``mark_bot_opened``).

Сообщение — строка ``platform_outbox`` (``lead_meeting_no_bot_alert``, ключ ``no_bot_alert:<lead_id>``, due сейчас);
отправляет боевой воркер, как «Новая запись на Живую встречу». Телефон и имя — только в payload и в Telegram,
в логи они не попадают: логируются класс ошибки и SQLSTATE.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.crm.messages import OPEN_CARD_LABEL, plain
from app.crm.rules import E164_RE
from app.crm.service import move_card_step_today, record_card_event, safe_error
from app.db import fetch_all, fetch_one, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.leads.meeting_leads import (
    ALERT_CARD_STATUS,
    ALERT_CARD_STEP,
    MESSENGER_TITLES,
    NAME_MAX,
    card_button_url,
    is_test_page,
    meeting_when,
    owner_chat,
)
from app.settings import get_settings
from app.telegram.bindings import BotBindingContext

logger = logging.getLogger(__name__)

TENANT_ID = "whieda"
EVENT_TYPE = "lead_meeting_no_bot_alert"
ALERT_AT_KEY = "no_bot_alert_at"
ALERT_SKIP_KEY = "no_bot_alert_skip"

# Сколько ждём открытия бота и как давно созданные заявки ещё берём (позже — поздно писать).
OPEN_GRACE = timedelta(minutes=3)
LOOKBACK = timedelta(hours=24)
BATCH_SIZE = 50

WHATSAPP_LABEL = "WhatsApp"
CARD_NOTE = "Бот не открыт за 3 минуты. Следующий шаг: написать в WhatsApp или позвонить."
# Шаги, которые алерт вправе заменить: «Пригласить» новой карточки и «шага нет».
_REPLACEABLE_STEPS = ("invite", None)

SKIP_TEST_PAGE = "test_page"
SKIP_NO_CHAT = "no_owner_chat"

_CANDIDATES_SQL = """
    select l.lead_id::text as lead_id, l.name, l.phone_e164, l.messenger_pref, l.page_url,
           coalesce(l.assigned_owner_id, %(organic_owner_id)s) as owner_id,
           l.metadata ->> 'crm_card' as card_id,
           m.starts_at
    from website_leads l
    left join meetings m on m.tenant_id = l.tenant_id and m.meeting_id = l.meeting_id
    where l.tenant_id = %(tenant_id)s
      and l.lead_kind = 'meeting'
      and l.deleted_at is null
      and l.bot_opened_at is null
      and l.created_at <= now() - %(grace)s
      and l.created_at > now() - %(lookback)s
      and coalesce(l.metadata, '{}'::jsonb) ->> 'no_bot_alert_at' is null
      and coalesce(l.metadata, '{}'::jsonb) ->> 'no_bot_alert_skip' is null
    order by l.created_at, l.lead_id
    limit %(limit)s
"""

# Отметка ставится только пока бот не открыт и отметок нет: проверка и запись — один оператор.
_CLAIM_SQL = """
    update website_leads
    set metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('no_bot_alert_at', now())
    where tenant_id = %s and lead_id = %s::uuid and bot_opened_at is null
      and coalesce(metadata, '{}'::jsonb) ->> 'no_bot_alert_at' is null
      and coalesce(metadata, '{}'::jsonb) ->> 'no_bot_alert_skip' is null
    returning lead_id
"""

_SKIP_SQL = """
    update website_leads
    set metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('no_bot_alert_skip', %s::text)
    where tenant_id = %s and lead_id = %s::uuid and bot_opened_at is null
      and coalesce(metadata, '{}'::jsonb) ->> 'no_bot_alert_at' is null
      and coalesce(metadata, '{}'::jsonb) ->> 'no_bot_alert_skip' is null
    returning lead_id
"""

_STEP_MARK_SQL = """
    update website_leads
    set metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('no_bot_alert_step', %s::text)
    where tenant_id = %s and lead_id = %s::uuid
"""


# ---- тексты (чистые функции) -----------------------------------------------------------------


def whatsapp_url(phone_e164: Any) -> str | None:
    """``https://wa.me/79991234567`` по номеру E.164; не номер — None."""
    phone = str(phone_e164 or "")
    return f"https://wa.me/{phone[1:]}" if E164_RE.fullmatch(phone) else None


def alert_text(
    *,
    name: Any,
    phone_e164: Any,
    messenger: str | None,
    starts_at: datetime | None,
    now: datetime | None = None,
) -> str:
    """«⚠️ Анна записался(ась) на Живую встречу чт 05.11 19:30 МСК, но бота не открыл(а). Телефон: +7….
    Мессенджер: Telegram. Напишите в WhatsApp или позвоните.» Имя проходит через ``plain``: ``<`` и ``>``
    становятся ‹ ›, остальное (``&``) экранирует отправка (``format_telegram_html``), так что разметку
    имя вставить не может. Встреча не выбрана / нет телефона / нет мессенджера — этих кусков нет."""
    who = plain(name, NAME_MAX) or "Без имени"
    head = f"⚠️ {who} записался(ась) на Живую встречу"
    when = meeting_when(starts_at, now=now)
    if when:
        head += f" {when}"
    parts = [f"{head}, но бота не открыл(а)."]
    phone = plain(phone_e164, 32)
    if phone:
        parts.append(f"Телефон: {phone}.")
    if messenger:
        parts.append(f"Мессенджер: {MESSENGER_TITLES.get(messenger, plain(messenger, 32))}.")
    parts.append("Напишите в WhatsApp или позвоните.")
    return " ".join(parts)


def alert_markup(phone_e164: Any, card_url: str | None) -> dict[str, Any] | None:
    """Кнопки «WhatsApp» (если есть номер) и «Открыть карточку» (если карточка есть и CRM открыта)."""
    rows: list[list[dict[str, str]]] = []
    wa = whatsapp_url(phone_e164)
    if wa:
        rows.append([{"text": WHATSAPP_LABEL, "url": wa}])
    if card_url:
        rows.append([{"text": OPEN_CARD_LABEL, "url": card_url}])
    return {"inline_keyboard": rows} if rows else None


# ---- база ------------------------------------------------------------------------------------


async def find_candidates(conn: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Заявки, которым пора слать алерт (см. docstring модуля): самые старые вперёд."""
    return await fetch_all(
        conn,
        _CANDIDATES_SQL,
        {
            "tenant_id": tenant_id,
            "organic_owner_id": get_settings().platform_organic_owner_id,
            "grace": OPEN_GRACE,
            "lookback": LOOKBACK,
            "limit": BATCH_SIZE,
        },
    )


async def _skip(conn: Any, tenant_id: str, lead_id: str, reason: str) -> str:
    await fetch_one(conn, _SKIP_SQL, (reason, tenant_id, lead_id))
    return f"skipped:{reason}"


async def _mark_card(conn: Any, tenant_id: str, lead_id: str, card_id: str) -> None:
    """Карточка CRM: заметка «бот не открыт за 3 минуты» и шаг «Напомнить о себе» на сегодня (если
    карточка новая и шаг не отложен партнёром). Свой savepoint: сбой карточки не отменяет сообщение."""
    try:
        async with conn.transaction():
            moved = await move_card_step_today(
                conn,
                tenant_id=tenant_id,
                contact_id=card_id,
                status=ALERT_CARD_STATUS,
                from_steps=_REPLACEABLE_STEPS,
                to_step=ALERT_CARD_STEP,
            )
            await record_card_event(conn, tenant_id=tenant_id, contact_id=card_id, note=CARD_NOTE)
            if moved is not None:
                async with conn.cursor() as cur:
                    await cur.execute(_STEP_MARK_SQL, (moved.isoformat(), tenant_id, lead_id))
    except Exception as exc:
        logger.warning("no_bot_alert_card_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})


async def alert_one(
    conn: Any, *, tenant_id: str, binding_id: str, row: dict[str, Any], now: datetime | None = None
) -> str:
    """Один алерт. Итог: ``queued`` (сообщение поставлено), ``duplicate`` (ключ уже был), ``raced``
    (бот открыт или отметка поставлена, пока шли проверки) или ``skipped:<причина>``.
    Вызывающий держит savepoint: любое исключение отменяет и отметку, и сообщение."""
    lead_id = str(row["lead_id"])
    if is_test_page(row.get("page_url")):
        return await _skip(conn, tenant_id, lead_id, SKIP_TEST_PAGE)
    chat = await owner_chat(conn, tenant_id, row.get("owner_id"))
    if chat is None:
        return await _skip(conn, tenant_id, lead_id, SKIP_NO_CHAT)
    chat_id, telegram_user_id = chat
    card_id = row.get("card_id") or None
    card_url = await card_button_url(conn, tenant_id, telegram_user_id, card_id)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": alert_text(
            name=row.get("name"),
            phone_e164=row.get("phone_e164"),
            messenger=row.get("messenger_pref"),
            starts_at=row.get("starts_at"),
            now=now,
        ),
        "binding_id": binding_id,
        "lead_id": lead_id,
    }
    markup = alert_markup(row.get("phone_e164"), card_url)
    if markup:
        payload["reply_markup"] = markup
        if card_url:
            payload["site_login_user_id"] = telegram_user_id
    if await fetch_one(conn, _CLAIM_SQL, (tenant_id, lead_id)) is None:
        return "raced"
    queued = await enqueue_outbox_event(
        conn,
        tenant_id=tenant_id,
        event_type=EVENT_TYPE,
        idempotency_key=f"no_bot_alert:{lead_id}",
        payload=payload,
        due_at=now or datetime.now(timezone.utc),
    )
    if not (queued and queued.get("created")):
        return "duplicate"
    if card_id:
        await _mark_card(conn, tenant_id, lead_id, card_id)
    return "queued"


async def enqueue_no_bot_alerts(tenant_id: str, binding_id: str, *, now: datetime | None = None) -> int:
    """Ставит в очередь алерты всем подходящим заявкам арендатора, возвращает их число. Сбой одной
    заявки (её savepoint откатывается) остальных не останавливает; сбой выборки — в лог, шаг жив."""
    queued = skipped = 0
    try:
        async with tenant_connection(tenant_id) as conn:
            for row in await find_candidates(conn, tenant_id):
                try:
                    async with conn.transaction():
                        outcome = await alert_one(conn, tenant_id=tenant_id, binding_id=binding_id, row=row, now=now)
                except Exception as exc:
                    logger.warning("no_bot_alert_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
                    continue
                if outcome == "queued":
                    queued += 1
                elif outcome.startswith("skipped:"):
                    skipped += 1
    except Exception as exc:
        logger.warning("no_bot_alert_plan_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
    if queued or skipped:
        logger.info("no_bot_alert", extra={"tenant_id": tenant_id, "queued": queued, "skipped": skipped})
    return queued


async def no_bot_alert_step(bindings: Mapping[str, BotBindingContext], *, now: datetime | None = None) -> int:
    """Шаг воркера (раз в минуту, из ``scheduled_notifications_step``): ``bindings`` — боты этого процесса
    по арендаторам (``PLATFORM_SCHEDULED_NOTIFY_BINDINGS``). Нет бота арендатора whieda (процесс стенда) —
    ничего не делает и в базу не ходит. Возвращает число поставленных алертов."""
    binding = bindings.get(TENANT_ID)
    if binding is None:
        return 0
    return await enqueue_no_bot_alerts(TENANT_ID, binding.binding_id, now=now)
