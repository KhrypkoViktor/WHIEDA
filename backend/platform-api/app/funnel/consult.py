"""Личный разбор в боте WWC (Telegram и Max): два вопроса кнопками → Виктор или Елена → заявка ведущему
(срез 7 бот-воронки, V47, 31.10.2026).

Диалог без свободного текста. Состояние живёт в данных кнопок (одинаково в обоих мессенджерах, до 64 байт):

  funnel:consult                    вопрос 1 «Кто вы сейчас?»            → кнопки cq1:<кто>
  cq1:<кто>                         вопрос 2 «Чего ждёте от разбора?»    → кнопки cq2:<кто>:<цель>
  cq2:<кто>:<цель>                  вопрос 3 «С кем удобнее?»            → кнопки chost:<кто>:<цель>:<ведущий>
  chost:<кто>:<цель>:<ведущий>      заявка: строка в consult_requests, сообщение ведущему, ответ человеку

  <кто>  net | sell | biz | look      <цель>  money | team | product | system      <ведущий>  viktor | elena

Здесь только то, что не зависит от мессенджера: разбор кнопок, экраны (``Reply`` из app/funnel/guest.py), заявка
(``submit_request``), тексты сообщения ведущему. Отправку в Telegram и Max делает app/funnel/system_word.py.

Заявка. Повторная заявка того же человека (мессенджер + его id) тому же ведущему за 7 дней, пока предыдущая в статусе
``new`` или ``contacted``, не заводится: «Заявка уже есть, ведущий напишет». Заявку на Живую встречу, которую человек
открыл в боте, находим по его id (``lead_id`` и телефон, карточка CRM, партнёр), без неё — только таблица.

Уведомление ведущему — строка ``platform_outbox`` (Telegram), которую отправляет боевой воркер (как сообщение партнёру
о записи на встречу): чат — ``lead_actors`` актора ведущего из ``PLATFORM_CONSULT_HOSTS``; ключи ``consult:<заявка>`` и
``consult:<заявка>:owner`` (копия владельцу, если ведущая — Елена). Процесс без ``PLATFORM_SCHEDULED_NOTIFY_BINDINGS``
(стенд) ничего не ставит: заявка в таблице остаётся с пустым ``notified_at``.

Логи — без имён и телефонов: номер заявки, ключ ведущего, канал и последние четыре цифры id человека.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.crm.messages import plain
from app.crm.service import move_card_step_today, record_card_event, safe_error
from app.db import fetch_one, tenant_connection
from app.funnel import texts
from app.funnel.guest import Reply, Rows
from app.jobs.outbox import enqueue_outbox_event
from app.leads.channels import channel_actor_id
from app.leads.meeting_leads import (
    ALERT_CARD_STATUS,
    ALERT_CARD_STEP,
    MESSENGER_TITLES,
    NAME_MAX,
    load_lead_by_channel_user,
    notify_binding_id,
    owner_chat,
    plain_no_links,
)
from app.meetings.service import utcnow
from app.settings import get_settings

logger = logging.getLogger("whieda.funnel.consult")

EVENT_TYPE = "consult_request_created"
HOSTS = ("viktor", "elena")
OWNER_HOST = "viktor"  # копию заявки к другой ведущей получает он
# Столько дней предыдущая заявка (new или contacted) тому же ведущему считается «уже есть».
DUPLICATE_DAYS = 7
OPEN_STATUSES = ("new", "contacted")
# Шаги новой карточки, которые заявка на разбор вправе заменить на «Напомнить о себе»: «Пригласить» и «шага нет».
_REPLACEABLE_STEPS = ("invite", None)

_WHO = tuple(key for key, _ in texts.CONSULT_WHO_LABELS)
_GOAL = tuple(key for key, _ in texts.CONSULT_GOAL_LABELS)
_WHO_LABEL = dict(texts.CONSULT_WHO_LABELS)
_GOAL_LABEL = dict(texts.CONSULT_GOAL_LABELS)

_WHO_RE = "|".join(_WHO)
_GOAL_RE = "|".join(_GOAL)
_HOST_RE = "|".join(HOSTS)
_ACTION_RE = re.compile(
    rf"^(?:(?P<start>{re.escape(texts.FUNNEL_CONSULT_CALLBACK)})"
    rf"|{re.escape(texts.CONSULT_Q1_CALLBACK_PREFIX)}(?P<who1>{_WHO_RE})"
    rf"|{re.escape(texts.CONSULT_Q2_CALLBACK_PREFIX)}(?P<who2>{_WHO_RE}):(?P<goal2>{_GOAL_RE})"
    rf"|{re.escape(texts.CONSULT_HOST_CALLBACK_PREFIX)}(?P<who3>{_WHO_RE}):(?P<goal3>{_GOAL_RE}):(?P<host3>{_HOST_RE}))$"
)

# Префиксы Max-диспетчера (register_max_callback) для кнопок диалога; «funnel:consult» входит в общий «funnel:».
CONSULT_MAX_PREFIXES = (
    texts.CONSULT_Q1_CALLBACK_PREFIX,
    texts.CONSULT_Q2_CALLBACK_PREFIX,
    texts.CONSULT_HOST_CALLBACK_PREFIX,
)

# Ник в Telegram: 5–32 знака, латиница, цифры, подчёркивание. Иначе ссылку на него не строим.
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_MAX_USERNAME_RE = re.compile(r"^[\w.\-]{1,64}$")


# ---- кнопки и экраны (чистые функции) --------------------------------------------------------------


@dataclass(frozen=True)
class ConsultAction:
    """``ask_who`` — показать вопрос 1; ``ask_goal`` — вопрос 2 (известно ``who``); ``ask_host`` — вопрос 3
    (известны ``who`` и ``goal``); ``request`` — завести заявку к ``host``."""

    kind: Literal["ask_who", "ask_goal", "ask_host", "request"]
    who: str | None = None
    goal: str | None = None
    host: str | None = None


def parse_consult_callback(data: str | None) -> ConsultAction | None:
    """callback_data кнопки диалога → действие; не наша кнопка или неизвестное значение — None."""
    match = _ACTION_RE.match(str(data or "").strip())
    if not match:
        return None
    if match["start"]:
        return ConsultAction("ask_who")
    if match["who1"]:
        return ConsultAction("ask_goal", who=match["who1"])
    if match["who2"]:
        return ConsultAction("ask_host", who=match["who2"], goal=match["goal2"])
    return ConsultAction("request", who=match["who3"], goal=match["goal3"], host=match["host3"])


def who_reply() -> Reply:
    """Вопрос 1 «Кто вы сейчас?»: по кнопке в ряду, ``cq1:<кто>``."""
    rows: Rows = [[(label, f"{texts.CONSULT_Q1_CALLBACK_PREFIX}{key}")] for key, label in texts.CONSULT_WHO_LABELS]
    return Reply(texts.CONSULT_Q1, rows)


def goal_reply(who: str) -> Reply:
    """Вопрос 2 «Чего ждёте от разбора?»: ``cq2:<кто>:<цель>``."""
    rows: Rows = [
        [(label, f"{texts.CONSULT_Q2_CALLBACK_PREFIX}{who}:{key}")] for key, label in texts.CONSULT_GOAL_LABELS
    ]
    return Reply(texts.CONSULT_Q2, rows)


def host_names() -> dict[str, str]:
    return get_settings().parsed_consult_host_names()


def host_reply(who: str, goal: str) -> Reply:
    """Вопрос 3 «С кем удобнее?»: кнопки с именами ведущих, ``chost:<кто>:<цель>:<ведущий>``."""
    names = host_names()
    rows: Rows = [[(names[host], f"{texts.CONSULT_HOST_CALLBACK_PREFIX}{who}:{goal}:{host}")] for host in HOSTS]
    return Reply(texts.CONSULT_Q3, rows)


def question_reply(action: ConsultAction) -> Reply:
    """Экран вопроса для действий ``ask_*``."""
    if action.kind == "ask_who":
        return who_reply()
    if action.kind == "ask_goal" and action.who:
        return goal_reply(action.who)
    if action.kind == "ask_host" and action.who and action.goal:
        return host_reply(action.who, action.goal)
    raise ValueError("not a question action")


# ---- кто нажал -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requester:
    """Человек, нажавший кнопки. ``display_name`` — из профиля мессенджера (может быть пустым), ``username`` —
    ник, если он годится для ссылки (Telegram: проверен по правилам ника)."""

    channel: Literal["telegram", "max"]
    user_id: int
    display_name: str = ""
    username: str | None = None


def _profile_name(user: dict[str, Any]) -> str:
    parts = [str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()]
    name = " ".join(part for part in parts if part)
    return name or str(user.get("name") or "").strip()


def telegram_requester(callback: Any) -> Requester:
    """Из обновления Telegram (``callback_query.from``): имя и ник; чего нет — пусто."""
    user = (((getattr(callback, "raw", None) or {}).get("callback_query") or {}).get("from")) or {}
    username = str(user.get("username") or "").strip().lstrip("@")
    return Requester(
        channel="telegram",
        user_id=int(callback.user_id),
        display_name=_profile_name(user),
        username=username if _TELEGRAM_USERNAME_RE.match(username) else None,
    )


def max_requester(event: Any) -> Requester:
    """Из обновления Max (``callback.user``): имя; ник — из разобранного события."""
    user = (((getattr(event, "raw", None) or {}).get("callback") or {}).get("user")) or {}
    username = str(getattr(event, "username", None) or user.get("username") or "").strip().lstrip("@")
    return Requester(
        channel="max",
        user_id=int(event.user_id),
        display_name=_profile_name(user),
        username=username if _MAX_USERNAME_RE.match(username) else None,
    )


# ---- сообщение ведущему (чистые функции) -----------------------------------------------------------


def messenger_line(channel: str, username: str | None) -> str:
    """«Telegram @ник» (ник проверен, это единственное место, где @ в тексте остаётся), «Telegram» (без ника: id —
    отдельной строкой) или «Max» (ник Max без @: Telegram сделал бы из него ссылку на чужого человека)."""
    title = MESSENGER_TITLES.get(channel, plain(channel, 32))
    if channel == "telegram":
        return f"{title} @{username}" if username else title
    return f"{title} (ник {plain_no_links(username, 64)})" if username else title


def host_message_text(
    *,
    name: Any,
    channel: str,
    username: str | None,
    user_id: int,
    phone_e164: Any,
    who: str,
    goal: str,
    partner_code: Any,
    host_name: str | None = None,
    unreachable: bool = False,
) -> str:
    """Сообщение ведущему. Имя и код партнёра проходят через ``plain_no_links``: ``<`` и ``>`` становятся ‹ ›, адреса
    и @упоминания заменяются на «…», остальное (``&``) экранирует отправка (``format_telegram_html``). Телефона
    нет, партнёра нет — этих строк нет. Человек в Telegram без ника: строка «Telegram id: …» (кнопки «Написать»
    нет, см. ``host_markup``). ``host_name`` (копия владельцу) добавляет строку «Ведущий: …»; ``unreachable`` —
    что ведущему сообщение поставить не удалось."""
    lines = [texts.CONSULT_HOST_HEAD.format(name=plain_no_links(name, NAME_MAX) or texts.CONSULT_HOST_NO_NAME)]
    if host_name:
        lines.append(texts.CONSULT_HOST_LINE_HOST.format(host=plain_no_links(host_name, 64)))
    lines.append(texts.CONSULT_HOST_LINE_MESSENGER.format(messenger=messenger_line(channel, username)))
    if channel == "telegram" and not username:
        lines.append(texts.CONSULT_HOST_LINE_TG_ID.format(id=int(user_id)))
    phone = plain(phone_e164, 32)
    if phone:
        lines.append(texts.CONSULT_HOST_LINE_PHONE.format(phone=phone))
    lines.append(texts.CONSULT_HOST_LINE_WHO.format(who=_WHO_LABEL.get(who, plain(who, 64))))
    lines.append(texts.CONSULT_HOST_LINE_GOAL.format(goal=_GOAL_LABEL.get(goal, plain(goal, 64))))
    code = plain_no_links(partner_code, 64)
    if code:
        lines.append(texts.CONSULT_HOST_LINE_PARTNER.format(code=code))
    if channel == "max":
        lines.append(texts.CONSULT_HOST_MAX_REPLY)
    if unreachable:
        lines.append(texts.CONSULT_HOST_UNREACHABLE)
    return "\n".join(lines)


def host_markup(channel: str, username: str | None) -> dict[str, Any] | None:
    """Кнопка «Написать» — ``https://t.me/<ник>``, только у человека в Telegram с ником. Без ника и в Max кнопки нет:
    ссылка ``tg://user?id=`` у людей с закрытой приватностью даёт Telegram-ошибку 400 на всё сообщение (воркер
    отметил бы его ``dead``), поэтому id идёт строкой в тексте (``host_message_text``)."""
    if channel != "telegram" or not username:
        return None
    return {"inline_keyboard": [[{"text": texts.CONSULT_WRITE_BUTTON, "url": f"https://t.me/{username}"}]]}


def card_note(*, host_name: str, who: str, goal: str) -> str:
    return texts.CONSULT_CARD_NOTE.format(
        host=plain(host_name, 64), who=_WHO_LABEL.get(who, plain(who, 64)), goal=_GOAL_LABEL.get(goal, plain(goal, 64))
    )


# ---- база ------------------------------------------------------------------------------------------

_FIND_OPEN_SQL = """
    select request_id from consult_requests
    where tenant_id = %s and channel = %s and channel_user_id = %s and host = %s
      and status = any(%s)
      and created_at > %s::timestamptz - make_interval(days => %s)
    order by created_at desc, request_id desc
    limit 1
"""

_INSERT_SQL = """
    insert into consult_requests (tenant_id, lead_id, channel, channel_user_id, display_name, host, answers)
    values (%s, %s::uuid, %s, %s, %s, %s, %s::jsonb)
    returning request_id
"""

# Партнёр человека без заявки на встречу: тот, чья ссылка закрепила его в боте (первое касание).
_ATTRIBUTION_SQL = """
    select rp.ref_code
    from partner_referral_attributions a
    join referral_profiles rp on rp.tenant_id = a.tenant_id and rp.owner_id = a.inviter_actor_id
    where a.tenant_id = %s and a.invitee_actor_id = %s
    order by rp.enabled desc, rp.created_at, rp.ref_code
    limit 1
"""

_NOTIFIED_SQL = """
    update consult_requests set notified_at = now(), updated_at = now()
    where tenant_id = %s and request_id = %s
"""


def _user_ref(user_id: int) -> str:
    return str(user_id)[-4:]


async def find_open_request(
    conn: Any, tenant_id: str, channel: str, user_id: int, host: str, now: datetime
) -> int | None:
    """Номер заявки этого человека к этому ведущему за последние 7 дней в статусе new или contacted; нет — None."""
    row = await fetch_one(
        conn, _FIND_OPEN_SQL, (tenant_id, channel, int(user_id), host, list(OPEN_STATUSES), now, DUPLICATE_DAYS)
    )
    return int(row["request_id"]) if row else None


async def _partner_code(conn: Any, tenant_id: str, requester: Requester, lead: Any) -> str:
    """Код партнёра для сообщения ведущему: из заявки на встречу (активный, иначе первый), иначе тот, кто закрепил
    человека в боте. Сбой запроса не мешает заявке: пустая строка."""
    if lead is not None and (lead.active_ref_code or lead.first_ref_code):
        return str(lead.active_ref_code or lead.first_ref_code)
    try:
        async with conn.transaction():
            row = await fetch_one(
                conn, _ATTRIBUTION_SQL, (tenant_id, channel_actor_id(tenant_id, requester.channel, requester.user_id))
            )
        return str(row["ref_code"]) if row else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("consult_partner_lookup_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return ""


async def _enqueue_message(
    conn: Any,
    *,
    tenant_id: str,
    request_id: int,
    actor_id: str | None,
    binding_id: str,
    text: str,
    markup: dict[str, Any] | None,
    key_suffix: str,
) -> Literal["queued", "duplicate", "no_chat", "error"]:
    """Одно сообщение в ``platform_outbox`` для чата актора. Своя точка отката: сбой не отменяет заявку и второе сообщение."""
    try:
        async with conn.transaction():
            chat = await owner_chat(conn, tenant_id, actor_id)
            if chat is None:
                return "no_chat"
            payload: dict[str, Any] = {
                "chat_id": chat[0],
                "text": text,
                "binding_id": binding_id,
                "consult_request_id": request_id,
            }
            if markup:
                payload["reply_markup"] = markup
            row = await enqueue_outbox_event(
                conn,
                tenant_id=tenant_id,
                event_type=EVENT_TYPE,
                idempotency_key=f"consult:{request_id}{key_suffix}",
                payload=payload,
                due_at=datetime.now(timezone.utc),
            )
            return "queued" if row and row.get("created") else "duplicate"
    except Exception as exc:  # noqa: BLE001
        logger.warning("consult_notify_failed", extra={"tenant_id": tenant_id, "request_id": request_id, **safe_error(exc)})
        return "error"


async def notify_hosts(
    conn: Any,
    *,
    tenant_id: str,
    request_id: int,
    host: str,
    requester: Requester,
    name: str,
    phone_e164: str | None,
    who: str,
    goal: str,
    partner_code: str,
) -> bool:
    """Сообщение ведущему и, если ведущая — Елена, копия владельцу (ключи ``consult:<заявка>`` и
    ``consult:<заявка>:owner``). True — ведущему сообщение поставлено, ``notified_at`` проставлен. Бота для плановых
    сообщений в процессе нет (стенд) — ничего не ставится."""
    try:
        binding_id = await notify_binding_id(conn, tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("consult_notify_binding_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return False
    if not binding_id:
        return False
    settings = get_settings()
    hosts = settings.parsed_consult_hosts()
    names = settings.parsed_consult_host_names()
    common = dict(
        name=name, channel=requester.channel, username=requester.username, user_id=requester.user_id,
        phone_e164=phone_e164, who=who, goal=goal, partner_code=partner_code,
    )
    markup = host_markup(requester.channel, requester.username)
    host_status = await _enqueue_message(
        conn, tenant_id=tenant_id, request_id=request_id, actor_id=hosts.get(host), binding_id=binding_id,
        text=host_message_text(**common), markup=markup, key_suffix="",
    )
    notified = host_status in ("queued", "duplicate")
    if notified:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(_NOTIFIED_SQL, (tenant_id, request_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("consult_notified_mark_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
    owner_actor = hosts.get(OWNER_HOST)
    owner_status = "skipped"
    if host != OWNER_HOST and owner_actor and owner_actor != hosts.get(host):
        owner_status = await _enqueue_message(
            conn, tenant_id=tenant_id, request_id=request_id, actor_id=owner_actor, binding_id=binding_id,
            text=host_message_text(**common, host_name=names[host], unreachable=not notified),
            markup=markup, key_suffix=":owner",
        )
    logger.info(
        "funnel_consult_notified",
        extra={"request_id": request_id, "host": host, "host_status": host_status, "owner_status": owner_status},
    )
    return notified


async def mark_card(conn: Any, tenant_id: str, card_id: str, *, host_name: str, who: str, goal: str) -> None:
    """Карточка CRM заявки на встречу: метка «разбор», заметка с ответами и ведущим, шаг «Напомнить о себе» на сегодня
    (только новой карточке с шагом «Пригласить» или без шага, не отложенному партнёром). Свой savepoint: сбой карточки
    не отменяет заявку."""
    try:
        async with conn.transaction():
            await move_card_step_today(
                conn, tenant_id=tenant_id, contact_id=card_id, status=ALERT_CARD_STATUS,
                from_steps=_REPLACEABLE_STEPS, to_step=ALERT_CARD_STEP,
            )
            await record_card_event(
                conn, tenant_id=tenant_id, contact_id=card_id,
                note=card_note(host_name=host_name, who=who, goal=goal), add_tags=[texts.CONSULT_CARD_TAG],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("consult_card_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})


@dataclass(frozen=True)
class SubmitResult:
    """``created`` — заявка заведена; ``exists`` — за 7 дней тому же ведущему уже есть открытая."""

    status: Literal["created", "exists"]
    reply: Reply
    request_id: int | None
    notified: bool = False


async def submit_request(
    tenant_id: str, requester: Requester, who: str, goal: str, host: str, *, now: datetime | None = None
) -> SubmitResult:
    """``chost:<кто>:<цель>:<ведущий>``: одна транзакция — повтор за 7 дней отсекается (блокировка по человеку и ведущему
    против двойного нажатия), заявка записывается, ведущему и владельцу ставятся сообщения, карточка CRM получает метку.
    Сбой самой записи — исключение (вызывающий отвечает «Не получилось»); сбой сообщения или карточки заявку не отменяет."""
    if who not in _WHO or goal not in _GOAL or host not in HOSTS:
        raise ValueError("unknown consult answer")
    current = now or utcnow()
    names = host_names()
    messenger = MESSENGER_TITLES.get(requester.channel, requester.channel)
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            "select pg_advisory_xact_lock(hashtext(%s)) as locked",
            (f"consult:{tenant_id}:{requester.channel}:{requester.user_id}:{host}",),
        )
        existing = await find_open_request(conn, tenant_id, requester.channel, requester.user_id, host, current)
        if existing is not None:
            logger.info(
                "funnel_consult",
                extra={
                    "status": "exists", "host": host, "channel": requester.channel,
                    "user_ref": _user_ref(requester.user_id), "request_id": existing,
                },
            )
            return SubmitResult("exists", Reply(texts.CONSULT_ALREADY), existing)
        lead = await load_lead_by_channel_user(conn, tenant_id, requester.channel, requester.user_id)
        name = " ".join(requester.display_name.split())[:NAME_MAX] or (lead.name if lead else "")
        row = await fetch_one(
            conn,
            _INSERT_SQL,
            (
                tenant_id, lead.lead_id if lead else None, requester.channel, int(requester.user_id), name or None, host,
                json.dumps({"who": who, "goal": goal}),
            ),
        )
        request_id = int(row["request_id"])
        partner_code = await _partner_code(conn, tenant_id, requester, lead)
        notified = await notify_hosts(
            conn, tenant_id=tenant_id, request_id=request_id, host=host, requester=requester, name=name,
            phone_e164=lead.phone_e164 if lead else None, who=who, goal=goal, partner_code=partner_code,
        )
        if lead is not None and lead.crm_card:
            await mark_card(conn, tenant_id, lead.crm_card, host_name=names[host], who=who, goal=goal)
    logger.info(
        "funnel_consult",
        extra={
            "status": "created", "host": host, "channel": requester.channel, "user_ref": _user_ref(requester.user_id),
            "request_id": request_id, "with_lead": lead is not None, "notified": notified,
        },
    )
    reply = Reply(texts.CONSULT_ACCEPTED.format(host=names[host], messenger=messenger))
    return SubmitResult("created", reply, request_id, notified)


# ---- одно действие кнопки (для Telegram и Max) -----------------------------------------------------


@dataclass(frozen=True)
class ConsultResult:
    """Ответ на нажатие: экран и короткий статус для журнала и ответа обработчика."""

    reply: Reply
    status: str
    ok: bool = True


async def handle_action(tenant_id: str, action: ConsultAction, requester: Requester | None = None) -> ConsultResult:
    """Действие диалога. Вопросы — без базы; заявка (``request``) — ``submit_request``, сбой базы — «Не получилось»
    (человек не остаётся без ответа). ``requester`` для заявки обязателен."""
    if action.kind == "ask_who":
        return ConsultResult(question_reply(action), "consult")
    if action.kind == "ask_goal":
        return ConsultResult(question_reply(action), "consult_goal")
    if action.kind == "ask_host":
        return ConsultResult(question_reply(action), "consult_host")
    if requester is None or not (action.who and action.goal and action.host):
        raise ValueError("a request needs a requester and all answers")
    try:
        result = await submit_request(tenant_id, requester, action.who, action.goal, action.host)
    except Exception as exc:  # noqa: BLE001 — на кнопку надо ответить, даже если база не отозвалась
        logger.warning(
            "funnel_consult_failed",
            extra={"channel": requester.channel, "user_ref": _user_ref(requester.user_id), **safe_error(exc)},
        )
        return ConsultResult(Reply(texts.GUEST_ERROR), "consult_error", ok=False)
    return ConsultResult(result.reply, f"consult_{result.status}")
