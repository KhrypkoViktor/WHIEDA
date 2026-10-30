"""Заявка на Живую встречу (``kind = "meeting"`` в POST /api/v1/leads, V44, 30.10.2026).

Страница «Живая встреча» шлёт имя, телефон, мессенджер и выбранную встречу; Core
сохраняет заявку в ``website_leads`` (``lead_kind = 'meeting'``), закрепляет человека за
первым партнёром, подтвердившим его в боте, заводит карточку в WWC CRM и сам, без n8n, пишет партнёру в Telegram
(о заявках с одним телефоном тому же партнёру — не чаще раза в сутки).
Дальше человек идёт в бота по ссылке ``?start=lead_<public_id>``: следующие срезы находят
заявку (``load_lead_by_public_id``) и отмечают открытие бота (``mark_bot_opened``).

Здесь:
  * чистые разборщики полей (телефон, метки рекламы, метка площадки, id встречи);
  * выбор владельца «первый партнёр навсегда» (``pick_person_anchor``);
  * тексты: заметка и метки карточки, сообщение партнёру (чистые функции);
  * работа с базой: встреча, привязка человека к первой заявке, уведомление партнёру,
    поиск заявки и отметка «открыл бота».

Имена и телефоны — только в ``platform_outbox.payload`` и в Telegram, в логи они не
попадают (как в ``app/crm/messages.py``): логируются класс ошибки и SQLSTATE.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from app.crm.messages import OPEN_CARD_LABEL, plain
from app.crm.rules import E164_RE, NOTE_MAX
from app.crm.service import (
    crm_contact_url,
    load_viewer,
    lock_reason,
    record_card_event,
    restore_card_step,
    safe_error,
)
from app.db import fetch_all, fetch_one
from app.jobs.outbox import enqueue_outbox_event
from app.meetings.service import as_msk, format_msk, utcnow
from app.settings import get_settings
from app.site_requests.contacts import normalize_phone

logger = logging.getLogger(__name__)

KIND_MEETING = "meeting"
MEETING_PRODUCT_NAME = "Живая встреча"
MESSENGERS = ("telegram", "max")
MESSENGER_TITLES = {"telegram": "Telegram", "max": "Max", "whatsapp": "WhatsApp"}

UTM_KEYS = ("source", "medium", "campaign", "content", "term")
UTM_VALUE_MAX = 200
SOURCE_TAG_MAX = 64
NAME_MAX = 160
URL_MAX = 1000
# «партнёр <64>, метка <64>, источник <200>» с запасом: строка «Откуда» не обрезается.
ORIGIN_MAX = 400

CARD_SOURCE = "встреча"
TAG_BOT_NOT_OPENED = "бот не открыл"
TAG_IN_BOT = "в боте"

PARTNER_EVENT = "lead_meeting_created"
# Одному партнёру о заявках с одним и тем же телефоном пишем не чаще раза в это число часов.
PARTNER_NOTIFY_WINDOW_HOURS = 24
BOT_NOTE = "Открыл(а) бота WWC в {channel}."

# Запись, перенос и отмена из бота (срез 4 бот-воронки, V46): метка и заметка в карточке CRM.
TAG_REGISTERED_PREFIX = "записан "
REGISTRATION_NOTE = "Записался(ась) на Живую встречу: {when}."
MOVE_NOTE = "Перенёс(ла) запись на Живую встречу: {old} → {new}."
CANCEL_NOTE = "Отменил(а) запись на Живую встречу: {when}."

# Алерт «бот не открыл» (app/funnel/no_bot_alert.py) ставит новой карточке шаг «Напомнить о себе»
# на сегодня и запоминает дату в metadata заявки: открытие бота возвращает шаг по умолчанию,
# если партнёр шаг не трогал.
ALERT_STEP_KEY = "no_bot_alert_step"
ALERT_CARD_STATUS = "new"
ALERT_CARD_STEP = "ping"

# Страницы тестовых стендов не пишут партнёрам: стенд делит базу с боем (как в crm-card).
_TEST_HOST_PREFIXES = ("staging.", "admin", "cabinet.")

_PHONE_CHARS_RE = re.compile(r"[\d\s()+\-.]{7,32}")
# Для записи на встречу номер начинается с кода страны 1-9 (общий E164_RE CRM пропускает «+0…»: его не трогаем).
_MEETING_PHONE_RE = re.compile(r"\+[1-9]\d*")
# Адреса и упоминания в имени и метках: Telegram сделал бы из них кликабельные ссылки в сообщении партнёру.
_LINK_RE = re.compile(r"(?:https?://|ftp://|tg://|www\.|(?:t|telegram)\.me/)\S*", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
LINK_PLACEHOLDER = "…"
_SOURCE_TAG_RE = re.compile(r"[a-z0-9_-]{1,64}")
_INT64_MAX = 2**63 - 1
# Сколько прежних заявок по телефону смотреть: человек редко записывается больше пары раз.
_ANCHOR_ROWS_MAX = 100


# ---- разбор полей (чистые функции) -------------------------------------------------------------


def normalize_meeting_phone(raw: Any) -> str | None:
    """«+7 (999) 123-45-67» → «+79991234567». Не телефон — None.

    Кроме разбора ``normalize_phone`` строка должна состоять только из знаков телефона:
    «звоните на 8999… после шести» телефоном не считается. Номер начинается с «+1»…«+9»:
    «+0…» кода страны не имеет."""
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    if not text or not _PHONE_CHARS_RE.fullmatch(text):
        return None
    e164 = normalize_phone(text)
    return e164 if e164 and E164_RE.fullmatch(e164) and _MEETING_PHONE_RE.fullmatch(e164) else None


def clean_messenger(value: Any) -> str | None:
    text = " ".join(str(value or "").split()).lower()
    return text if text in MESSENGERS else None


def clean_utm(value: Any) -> dict[str, str]:
    """Только пять меток (source, medium, campaign, content, term), каждая — строка до 200
    знаков; пустые и лишние ключи отбрасываются. Принимает и ``utm_source``-имена."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key in UTM_KEYS:
        raw = value.get(key)
        if raw in (None, ""):
            raw = value.get(f"utm_{key}")
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            continue
        text = " ".join(str(raw).split())[:UTM_VALUE_MAX]
        if text:
            out[key] = text
    return out


def clean_source_tag(value: Any) -> str | None:
    """Метка площадки: до 64 знаков из [a-z0-9_-] (регистр не важен). Иначе None."""
    text = str(value or "").strip().lower()
    return text if _SOURCE_TAG_RE.fullmatch(text) else None


def parse_meeting_id(value: Any) -> int | None:
    """id встречи из тела запроса: целое число или строка из цифр, 1…2^63-1. Иначе None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        number = int(value.strip())
    else:
        return None
    return number if 0 < number <= _INT64_MAX else None


def meeting_problem(row: dict[str, Any] | None, now: datetime) -> str | None:
    """Почему встречу нельзя привязать к заявке: not_found, not_live, not_planned, in_past.
    None — встреча подходит (живая, запланированная, в будущем)."""
    if row is None:
        return "not_found"
    if row.get("kind") != "live":
        return "not_live"
    if row.get("status") != "planned":
        return "not_planned"
    starts_at = row.get("starts_at")
    if not isinstance(starts_at, datetime) or starts_at <= now:
        return "in_past"
    return None


def pick_person_anchor(rows: list[dict[str, Any]], organic_owner_id: str) -> tuple[str | None, str | None]:
    """«Первый партнёр навсегда»: ``(person_lead_id, locked_owner_id)``.

    ``rows`` — прежние заявки этого арендатора с тем же телефоном, от старой к новой
    (``lead_id``, ``assigned_owner_id``, ``bot_opened``, ``owner_active``). ``person_lead_id`` — первая
    заявка человека (у самой первой заявки поля нет). ``locked_owner_id`` — владелец первой заявки,
    которая закрепляет человека. Якорь — только заявка, по которой человек подтвердил себя в боте
    (``bot_opened``: открыл бота по ссылке заявки; номер из формы сайта чужой человек мог вписать
    в чужую заявку) и чей владелец — партнёр с действующей подпиской (``owner_active``: ушедший
    партнёр номер не держит, иначе ему молча не доходили бы сообщения). Владелец «по умолчанию»
    (organic) человека не закрепляет, иначе заявка, пришедшая по ссылке партнёра, навсегда осталась бы
    у владельца WWC. Нет прежних заявок или якоря — ``(None, None)`` / ``(первая, None)``: заявка идёт
    по обычной маршрутизации."""
    if not rows:
        return None, None
    first = str(rows[0]["lead_id"])
    for row in rows:
        owner = row.get("assigned_owner_id")
        if owner and owner != organic_owner_id and row.get("bot_opened") and row.get("owner_active"):
            return first, str(owner)
    return first, None


def is_test_page(page_url: str | None) -> bool:
    host = (urlsplit(str(page_url or "")).hostname or "").lower()
    return host.startswith(_TEST_HOST_PREFIXES)


# ---- тексты (чистые функции) -----------------------------------------------------------------


def plain_no_links(value: Any, limit: int = NAME_MAX) -> str:
    """``plain`` без адресов и упоминаний: «http(s)://…», «www.…», «t.me/…» и «@имя» заменяются на «…».
    Имя и метки приходят с публичной формы; Telegram сделал бы из них кликабельные ссылки в сообщении
    партнёру. Экранирование HTML остаётся за ``plain`` (``<`` ``>``) и отправкой (``&``)."""
    text = plain(value, 10**6)
    text = _MENTION_RE.sub(LINK_PLACEHOLDER, _LINK_RE.sub(LINK_PLACEHOLDER, text))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def origin_text(*, ref_code: Any = None, source_tag: Any = None, utm_source: Any = None) -> str:
    """«партнёр elena, метка reels_oct, источник instagram»; ничего не известно — «сайт».
    Адреса и @упоминания в кодах и метках заменены на «…» (``plain_no_links``)."""
    parts = []
    if ref_code:
        parts.append(f"партнёр {plain_no_links(ref_code, 64)}")
    if source_tag:
        parts.append(f"метка {plain_no_links(source_tag, SOURCE_TAG_MAX)}")
    if utm_source:
        parts.append(f"источник {plain_no_links(utm_source, UTM_VALUE_MAX)}")
    return ", ".join(parts) or "сайт"


def meeting_when(starts_at: datetime | None, *, now: datetime | None = None) -> str:
    """«чт 05.11 19:30 МСК» или пустая строка, когда встреча не выбрана."""
    return format_msk(starts_at, now=now) if isinstance(starts_at, datetime) else ""


def card_note(
    *,
    phone_e164: str,
    messenger: str | None,
    starts_at: datetime | None,
    origin: str,
    now: datetime | None = None,
) -> str:
    lines = ["Запись на Живую встречу.", f"Телефон: {phone_e164}"]
    if messenger:
        lines.append(f"Мессенджер: {MESSENGER_TITLES.get(messenger, messenger)}")
    when = meeting_when(starts_at, now=now)
    if when:
        lines.append(f"Встреча: {when}")
    lines.append(f"Откуда: {origin}")
    return "\n".join(lines)[:NOTE_MAX]


def card_tags(starts_at: datetime | None) -> list[str]:
    """``записан 05.11`` (если встреча выбрана) и ``бот не открыл`` — пока не пришёл в бота."""
    tags = []
    if isinstance(starts_at, datetime):
        tags.append(f"записан {as_msk(starts_at):%d.%m}")
    tags.append(TAG_BOT_NOT_OPENED)
    return tags


def partner_message_text(
    *,
    name: Any,
    phone_e164: str,
    messenger: str | None,
    starts_at: datetime | None,
    origin: str,
    now: datetime | None = None,
) -> str:
    """Сообщение партнёру о новой записи. Имя и метки проходят через ``plain_no_links``: ``<`` и ``>``
    становятся ‹ ›, адреса (``http(s)://``, ``www.``, ``t.me/``) и ``@упоминания`` заменяются на «…»
    (иначе Telegram сделал бы их ссылками), а остальное экранирует отправка (``format_telegram_html``)."""
    when = meeting_when(starts_at, now=now)
    lines = [
        f"Новая запись на Живую встречу {when}".rstrip(),
        f"Имя: {plain_no_links(name, NAME_MAX)}",
        f"Телефон: {phone_e164}",
    ]
    if messenger:
        lines.append(f"Мессенджер: {MESSENGER_TITLES.get(messenger, messenger)}")
    lines.append(f"Откуда: {plain_no_links(origin, ORIGIN_MAX)}")
    return "\n".join(lines)


# ---- база: встреча, человек, уведомление -------------------------------------------------------


async def lookup_meeting(
    conn: Any, tenant_id: str, meeting_id: int | None, *, now: datetime | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """``(встреча, причина отказа)``. Нет id — ``(None, None)``. Подходящая встреча —
    ``(строка, None)``; иначе ``(None, причина)`` из ``meeting_problem`` или ``lookup_failed``
    (сбой чтения не должен терять заявку). Колонка join_url не читается."""
    if meeting_id is None:
        return None, None
    current = now or utcnow()
    try:
        async with conn.transaction():
            row = await fetch_one(
                conn,
                "select meeting_id, kind, status, starts_at from meetings where tenant_id = %s and meeting_id = %s",
                (tenant_id, int(meeting_id)),
            )
    except Exception as exc:
        logger.warning("meeting_lead_lookup_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return None, "lookup_failed"
    problem = meeting_problem(row, current)
    return (row, None) if problem is None else (None, problem)


async def find_person_anchor(
    conn: Any, tenant_id: str, phone_e164: str, organic_owner_id: str
) -> tuple[str | None, str | None]:
    """Первая заявка человека и закреплённый партнёр (``pick_person_anchor``) по телефону.

    У каждой прежней заявки читаются ``bot_opened`` (``bot_opened_at`` не пусто) и ``owner_active``
    (владелец — партнёр с включённым профилем и подпиской ``active``/``grace``, как в маршрутизации
    владельца заявки).

    Блокировка на (арендатор, телефон) до конца транзакции: две одновременные первые заявки
    одного человека идут по очереди, и вторая видит первую."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"website_leads_phone:{tenant_id}:{phone_e164}",),
        )
    rows = await fetch_all(
        conn,
        """
        select wl.lead_id::text as lead_id, wl.assigned_owner_id,
               (wl.bot_opened_at is not null) as bot_opened,
               exists (
                 select 1
                 from referral_profiles rp
                 join partner_subscriptions ps
                   on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
                 where rp.tenant_id = wl.tenant_id and rp.owner_id = wl.assigned_owner_id and rp.enabled = true
                   and partner_subscription_state(ps.paid_until, now()) in ('active', 'grace')
               ) as owner_active
        from website_leads wl
        where wl.tenant_id = %s and wl.phone_e164 = %s and wl.assigned_owner_id is not null
        order by wl.created_at, wl.lead_id
        limit %s
        """,
        (tenant_id, phone_e164, _ANCHOR_ROWS_MAX),
    )
    return pick_person_anchor(rows, organic_owner_id)


async def _notify_binding_id(conn: Any, tenant_id: str) -> str | None:
    """Бот, которым этот процесс шлёт плановые уведомления, из PLATFORM_SCHEDULED_NOTIFY_BINDINGS:
    первый активный binding этого арендатора. Процесс без переменной (стенд) — None."""
    wanted = list(get_settings().parsed_scheduled_notify_bindings())
    if not wanted:
        return None
    row = await fetch_one(
        conn,
        """
        select binding_id from tenant_bot_bindings
        where tenant_id = %s and binding_id = any(%s) and status = 'active'
        order by array_position(%s::text[], binding_id)
        limit 1
        """,
        (tenant_id, wanted, wanted),
    )
    return str(row["binding_id"]) if row else None


async def owner_chat(conn: Any, tenant_id: str, owner_actor_id: str | None) -> tuple[str, int | None] | None:
    """Чат владельца заявки: ``(chat_id, telegram_user_id)`` из ``lead_actors`` (сначала
    ``telegram_chat_id``, иначе ``telegram_user_id``). Нет владельца или чата — None."""
    if not owner_actor_id:
        return None
    actor = await fetch_one(
        conn,
        """
        select telegram_chat_id, telegram_user_id from lead_actors
        where tenant_id = %s and actor_id = %s
        limit 1
        """,
        (tenant_id, owner_actor_id),
    )
    telegram_user_id = actor.get("telegram_user_id") if actor else None
    chat_id = str((actor or {}).get("telegram_chat_id") or telegram_user_id or "").strip()
    if not chat_id:
        return None
    return chat_id, int(telegram_user_id) if telegram_user_id is not None else None


async def card_button_url(conn: Any, tenant_id: str, telegram_user_id: int | None, card_id: str | None) -> str | None:
    """Ссылка кнопки «Открыть карточку» (превращается во вход на сайт при отправке) — только если
    карточка есть, владелец известен и CRM у него открыта (``lock_reason`` пуст)."""
    if not card_id or telegram_user_id is None:
        return None
    viewer = await load_viewer(tenant_id, int(telegram_user_id), conn=conn)
    return crm_contact_url(viewer, card_id) if lock_reason(viewer) is None else None


async def partner_already_told(
    conn: Any, tenant_id: str, lead_id: str, phone_e164: str, owner_actor_id: str
) -> bool:
    """Партнёру этого человека о нём уже писали за последние ``PARTNER_NOTIFY_WINDOW_HOURS`` часов:
    есть прежняя заявка того же арендатора с тем же телефоном и тем же владельцем, по которой сообщение
    ``lead_meeting:<lead_id>`` уже стояло в ``platform_outbox`` (любым статусом). Текущая заявка не
    считается. Цикл заявок с одним номером и разными ключами идемпотентности партнёру одного
    сообщения за сутки не прибавляет; другому партнёру (другой владелец) сообщение уйдёт."""
    row = await fetch_one(
        conn,
        """
        select 1 as told
        from website_leads wl
        join platform_outbox ob
          on ob.tenant_id = wl.tenant_id and ob.idempotency_key = 'lead_meeting:' || wl.lead_id::text
        where wl.tenant_id = %s and wl.phone_e164 = %s and wl.assigned_owner_id = %s
          and wl.lead_id <> %s::uuid
          and wl.created_at > now() - make_interval(hours => %s)
          and ob.event_type = %s
        limit 1
        """,
        (tenant_id, phone_e164, owner_actor_id, lead_id, PARTNER_NOTIFY_WINDOW_HOURS, PARTNER_EVENT),
    )
    return row is not None


async def notify_partner(
    conn: Any,
    *,
    tenant_id: str,
    lead_id: str,
    owner_actor_id: str | None,
    name: str,
    phone_e164: str,
    messenger: str | None,
    starts_at: datetime | None,
    origin: str,
    page_url: str | None,
    card_id: str | None,
) -> bool:
    """Ставит в ``platform_outbox`` сообщение партнёру в Telegram: «Новая запись на Живую
    встречу…» и кнопка «Открыть карточку» (если карточка есть и CRM партнёру открыта, как в
    напоминании «через час встреча»). Отправляет боевой воркер. Ключ ``lead_meeting:<lead_id>``:
    повтор заявки второго сообщения не даёт. Новая заявка с тем же телефоном тому же партнёру в
    течение суток сообщения не ставит (``partner_already_told``): карточка и заметка в CRM при этом
    обновляются. Любой сбой — в лог, заявка уже сохранена. True — сообщение поставлено."""
    if not owner_actor_id or is_test_page(page_url):
        return False
    try:
        async with conn.transaction():
            chat = await owner_chat(conn, tenant_id, owner_actor_id)
            if chat is None:
                return False
            chat_id, telegram_user_id = chat
            binding_id = await _notify_binding_id(conn, tenant_id)
            if not binding_id:
                return False
            if await partner_already_told(conn, tenant_id, lead_id, phone_e164, owner_actor_id):
                logger.info("meeting_lead_notify_deduped", extra={"tenant_id": tenant_id, "lead_id": lead_id})
                return False
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": partner_message_text(
                    name=name, phone_e164=phone_e164, messenger=messenger, starts_at=starts_at, origin=origin
                ),
                "binding_id": binding_id,
                "lead_id": lead_id,
            }
            card_url = await card_button_url(conn, tenant_id, telegram_user_id, card_id)
            if card_url:
                payload["reply_markup"] = {"inline_keyboard": [[{"text": OPEN_CARD_LABEL, "url": card_url}]]}
                payload["site_login_user_id"] = telegram_user_id
            row = await enqueue_outbox_event(
                conn,
                tenant_id=tenant_id,
                event_type=PARTNER_EVENT,
                idempotency_key=f"lead_meeting:{lead_id}",
                payload=payload,
                due_at=datetime.now(timezone.utc),
            )
            return bool(row and row.get("created"))
    except Exception as exc:
        logger.warning("meeting_lead_notify_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return False


# ---- заявка для следующих срезов ---------------------------------------------------------------


@dataclass(frozen=True)
class MeetingLead:
    lead_id: str
    public_id: str
    name: str
    phone_e164: str | None
    messenger_pref: str | None
    meeting_id: int | None
    assigned_owner_id: str | None
    first_ref_code: str | None
    active_ref_code: str | None
    utm: dict[str, Any]
    source_tag: str | None
    telegram_user_id: int | None
    max_user_id: int | None
    bot_opened_at: datetime | None
    # Карточка CRM, заведённая при заявке (metadata.crm_card); None — карточки нет (у владельца не открыт ежедневник).
    crm_card: str | None = None


_LEAD_COLUMNS = """
    lead_id::text as lead_id, public_id, name, phone_e164, messenger_pref, meeting_id,
    assigned_owner_id, first_ref_code, active_ref_code, utm, source_tag,
    telegram_user_id, max_user_id, bot_opened_at,
    coalesce(metadata, '{}'::jsonb) ->> 'crm_card' as crm_card
"""


def _lead_from_row(row: dict[str, Any]) -> MeetingLead:
    utm = row.get("utm")
    return MeetingLead(
        lead_id=row["lead_id"],
        public_id=row["public_id"],
        name=row["name"],
        phone_e164=row.get("phone_e164"),
        messenger_pref=row.get("messenger_pref"),
        meeting_id=int(row["meeting_id"]) if row.get("meeting_id") is not None else None,
        assigned_owner_id=row.get("assigned_owner_id"),
        first_ref_code=row.get("first_ref_code"),
        active_ref_code=row.get("active_ref_code"),
        utm=dict(utm) if isinstance(utm, dict) else {},
        source_tag=row.get("source_tag"),
        telegram_user_id=int(row["telegram_user_id"]) if row.get("telegram_user_id") is not None else None,
        max_user_id=int(row["max_user_id"]) if row.get("max_user_id") is not None else None,
        bot_opened_at=row.get("bot_opened_at"),
        crm_card=row.get("crm_card") or None,
    )


async def load_lead_by_public_id(conn: Any, tenant_id: str, public_id: str) -> MeetingLead | None:
    """Заявка на встречу по её public_id (из ``?start=lead_<public_id>``). Заявки других видов,
    удалённые и чужого арендатора — None."""
    key = " ".join(str(public_id or "").split())[:64]
    if not key:
        return None
    row = await fetch_one(
        conn,
        f"""
        select {_LEAD_COLUMNS}
        from website_leads
        where tenant_id = %s and public_id = %s and lead_kind = 'meeting' and deleted_at is null
        limit 1
        """,
        (tenant_id, key),
    )
    return _lead_from_row(row) if row else None


async def load_lead_by_channel_user(
    conn: Any, tenant_id: str, channel: Literal["telegram", "max"], channel_user_id: int
) -> MeetingLead | None:
    """Последняя заявка на встречу человека, который открыл бота по её ссылке: по его id в мессенджере
    (``telegram_user_id`` / ``max_user_id`` ставит ``mark_bot_opened``). Нет такой заявки — None.
    Режим гостя (``app/funnel/guest.py``) узнаёт по ней, что перед ним не партнёр, а гость встречи."""
    if channel not in MESSENGERS:
        raise ValueError("channel must be telegram or max")
    column = "telegram_user_id" if channel == "telegram" else "max_user_id"
    row = await fetch_one(
        conn,
        f"""
        select {_LEAD_COLUMNS}
        from website_leads
        where tenant_id = %s and {column} = %s and lead_kind = 'meeting' and deleted_at is null
        order by created_at desc, lead_id
        limit 1
        """,
        (tenant_id, int(channel_user_id)),
    )
    return _lead_from_row(row) if row else None


async def load_lead_card_id(conn: Any, tenant_id: str, lead_id: str | None) -> str | None:
    """Карточка CRM заявки (``metadata.crm_card``) по ``lead_id``; None — заявки нет или карточка не заводилась."""
    if not lead_id:
        return None
    row = await fetch_one(
        conn,
        """
        select coalesce(metadata, '{}'::jsonb) ->> 'crm_card' as crm_card
        from website_leads
        where tenant_id = %s and lead_id = %s::uuid and deleted_at is null
        """,
        (tenant_id, lead_id),
    )
    return (row or {}).get("crm_card") or None


async def mark_bot_opened(
    conn: Any,
    tenant_id: str,
    public_id: str,
    *,
    channel: Literal["telegram", "max"],
    channel_user_id: int,
) -> MeetingLead | None:
    """Человек открыл бота по ссылке заявки. Ставит ``bot_opened_at`` (только если пусто) и
    ``telegram_user_id`` / ``max_user_id`` канала (первый открывший остаётся). При первом
    открытии в карточке CRM снимается метка «бот не открыл», ставится «в боте», пишется заметка
    «Открыл(а) бота…» и строка истории. Повтор ничего не дублирует. Нет такой заявки — None.

    ``conn`` — открытое ``tenant_connection`` того же арендатора. Карточка меняется под
    savepoint: её сбой не отменяет отметку на заявке."""
    if channel not in MESSENGERS:
        raise ValueError("channel must be telegram or max")
    key = " ".join(str(public_id or "").split())[:64]
    if not key:
        return None
    lead = await fetch_one(
        conn,
        """
        select lead_id::text as lead_id, bot_opened_at is null as first_open,
               coalesce(metadata, '{}'::jsonb) ->> 'crm_card' as crm_card,
               coalesce(metadata, '{}'::jsonb) ->> 'no_bot_alert_step' as alert_step
        from website_leads
        where tenant_id = %s and public_id = %s and lead_kind = 'meeting' and deleted_at is null
        for update
        """,
        (tenant_id, key),
    )
    if lead is None:
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update website_leads
            set bot_opened_at = coalesce(bot_opened_at, now()),
                telegram_user_id = case when %s = 'telegram' then coalesce(telegram_user_id, %s) else telegram_user_id end,
                max_user_id = case when %s = 'max' then coalesce(max_user_id, %s) else max_user_id end,
                updated_at = now()
            where tenant_id = %s and lead_id = %s::uuid
            """,
            (channel, int(channel_user_id), channel, int(channel_user_id), tenant_id, lead["lead_id"]),
        )
    if lead["first_open"] and lead["crm_card"]:
        await _mark_card_in_bot(conn, tenant_id, lead["crm_card"], channel)
        if lead["alert_step"]:
            await _restore_alert_step(conn, tenant_id, lead["lead_id"], lead["crm_card"], lead["alert_step"])
    return await load_lead_by_public_id(conn, tenant_id, key)


async def _mark_card_in_bot(conn: Any, tenant_id: str, contact_id: str, channel: str) -> None:
    try:
        async with conn.transaction():
            await record_card_event(
                conn,
                tenant_id=tenant_id,
                contact_id=contact_id,
                note=BOT_NOTE.format(channel=MESSENGER_TITLES[channel]),
                add_tags=[TAG_IN_BOT],
                remove_tags=[TAG_BOT_NOT_OPENED],
            )
    except Exception as exc:
        logger.warning("meeting_lead_card_bot_mark_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})


# ---- запись на встречу из бота: метка и заметка в карточке --------------------------------------


def registered_tag(starts_at: datetime) -> str:
    """``записан 05.11`` — метка карточки; та же, что ставится при заявке с выбранной встречей."""
    return f"{TAG_REGISTERED_PREFIX}{as_msk(starts_at):%d.%m}"


def registration_card_change(
    kind: Literal["registered", "moved", "cancelled"],
    *,
    starts_at: datetime,
    old_starts_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str, list[str], list[str]]:
    """``(заметка, добавить метки, снять метки)`` для карточки CRM: запись — метка новой встречи;
    перенос — старая метка снимается, новая ставится; отмена — метка снимается."""
    when = meeting_when(starts_at, now=now)
    if kind == "registered":
        return REGISTRATION_NOTE.format(when=when), [registered_tag(starts_at)], []
    if kind == "moved":
        if old_starts_at is None:
            raise ValueError("old_starts_at is required for a move")
        note = MOVE_NOTE.format(old=meeting_when(old_starts_at, now=now), new=when)
        return note, [registered_tag(starts_at)], [registered_tag(old_starts_at)]
    if kind == "cancelled":
        return CANCEL_NOTE.format(when=when), [], [registered_tag(starts_at)]
    raise ValueError("kind must be registered, moved or cancelled")


async def card_registration_event(
    conn: Any,
    tenant_id: str,
    contact_id: str | None,
    kind: Literal["registered", "moved", "cancelled"],
    *,
    starts_at: datetime,
    old_starts_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Запись, перенос или отмена: метки и заметка в карточке CRM заявки. Нет карточки (``contact_id``
    пуст) — ничего не делает. Меняется под savepoint: сбой карточки не отменяет запись. True — карточка обновлена."""
    if not contact_id:
        return False
    note, add_tags, remove_tags = registration_card_change(
        kind, starts_at=starts_at, old_starts_at=old_starts_at, now=now
    )
    try:
        async with conn.transaction():
            return await record_card_event(
                conn, tenant_id=tenant_id, contact_id=contact_id, note=note, add_tags=add_tags, remove_tags=remove_tags
            )
    except Exception as exc:
        logger.warning("meeting_lead_card_registration_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return False

async def _restore_alert_step(conn: Any, tenant_id: str, lead_id: str, contact_id: str, alert_date: str) -> None:
    """Человек открыл бота после алерта «бот не открыл»: шаг «Напомнить о себе», который поставил
    алерт, снимается — возвращается шаг по умолчанию («Пригласить на сегодня»), если партнёр шаг
    и дату не менял. Метка алерта в заявке убирается в любом случае. Свой savepoint: сбой не
    отменяет отметку «открыл бота»."""
    try:
        async with conn.transaction():
            await restore_card_step(
                conn,
                tenant_id=tenant_id,
                contact_id=contact_id,
                status=ALERT_CARD_STATUS,
                from_step=ALERT_CARD_STEP,
                from_next_at=date.fromisoformat(alert_date),
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update website_leads set metadata = coalesce(metadata, '{}'::jsonb) - %s
                    where tenant_id = %s and lead_id = %s::uuid
                    """,
                    (ALERT_STEP_KEY, tenant_id, lead_id),
                )
    except Exception as exc:
        logger.warning("meeting_lead_alert_step_restore_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
