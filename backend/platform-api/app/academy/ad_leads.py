"""Заявки с рекламы витрины (16.10.2026): строки таблицы Meta → алерт администратору витрины.

Meta пишет каждую заявку моментальной формы в Google Таблицу (у ALMIRA — «ALMIRA · Заявки»).
Где таблица — ``academy_showcases.support.leads_sheet``: ``{"id": …, "gid": …, "since": ISO}``;
``since`` — с какой заявки слать алерты (старые строки таблицы не дублируем).

* Опрос — в шаге уведомлений Академии воркера, не чаще ``POLL_INTERVAL_SEC``, и только там,
  где бот витрины разрешается (бой: у staging токена нет — он таблицу не читает).
* Каждая новая строка — запись ``platform_outbox`` с ключом ``ad_lead:<витрина>:<id лида>``:
  повторный опрос и рестарт ничего не дублируют. Отправляет обычный цикл уведомлений.
* Алерт — по строке на поле (имя, открыт ли бот, телефон, WhatsApp и Telegram по номеру, ник,
  скидка, интерес, источник, время по Минску), пустые поля пропускаются. Кнопка — открыть таблицу.
* «Бот открыт» — строка ``academy_ad_opens`` (V37): /start ig_… пишет её с ником, именем и
  строкой таблицы. Алерт ждёт ``OPEN_GRACE`` после заявки (бота обычно открывают сразу после
  формы), чтобы отметка была верной; открыл раньше — алерт уходит сразу (17.10.2026).
* ``opened_text`` — короткая строка, когда человек с формы открыл бота (``/start ig_…``):
  имя и ник из строки таблицы (по нику, иначе по имени), иначе из Telegram; имя — ссылка
  в личный чат, чтобы администратор открыл человека без публичного ника.

* ``lead_flags`` — «в боте / нет» по каждой строке таблицы для скрипта таблицы (Apps Script, раз в
  5 минут): только id заявок, без имён, телефонов и ников. Ключ — ``leads_sheet.flags_key`` (18.10.2026).

Таблица читается CSV-выгрузкой по ссылке (``export?format=csv&gid=…``): пока она открыта
«всем, у кого есть ссылка». Закрыть доступ можно после перехода на сервисный аккаунт.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EVENT = "showcase_ad_lead_row"
POLL_INTERVAL_SEC = 90.0
OPEN_GRACE = timedelta(minutes=3)
# Вход в бота не по ссылке с рекламы (вход на сайт через Telegram, /start, ключ курса) — тоже «в боте»:
# Латыпова вошла на сайт академии через Telegram и числилась «не в боте» (18.10.2026).
BOT_TAG = "bot"
# Бота открывают с экрана «спасибо» формы: строка Meta создана за секунды–минуты до /start.
# Имя в Telegram часто другое («Svetik» — «Светлана Самохвал»), поэтому ещё и по времени (17.10.2026).
OPEN_AFTER_FORM = timedelta(minutes=5)
OPEN_CLOCK_SKEW = timedelta(seconds=15)
FETCH_TIMEOUT_SEC = 15.0
MINSK = timezone(timedelta(hours=3))
_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{id}/export?format=csv&gid={gid}"
_SHEET_URL = "https://docs.google.com/spreadsheets/d/{id}/edit#gid={gid}"
_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
_GID_RE = re.compile(r"^\d{1,12}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
_PLATFORMS = {"ig": "Instagram", "fb": "Facebook", "msg": "Messenger", "an": "Audience Network"}

# Последний опрос каждой таблицы (монотонные секунды): шаг уведомлений идёт раз в 30 с.
_last_poll: dict[str, float] = {}


@dataclass(frozen=True)
class Lead:
    lead_id: str
    created: datetime | None
    name: str
    phone: str
    username: str | None
    nick_raw: str
    studied: bool | None
    interest: str
    source: str
    form_name: str
    status: str = ""  # колонка «Статус» таблицы: Кирилл отмечает оплативших


@dataclass(frozen=True)
class Opened:
    """Человек открыл бота витрины по ссылке с рекламы (строка academy_ad_opens)."""

    telegram_user_id: int
    username: str | None
    name: str
    lead_id: str | None
    opened_at: datetime | None = None
    tag: str = ""  # метка /start: ig_web24 …
    webinar: str | None = None  # V38: дата, выбранная кнопкой в боте


# ---- разбор строк таблицы -----------------------------------------------------------------------


def _strip_prefix(value: str) -> str:
    """Meta пишет id и телефоны с префиксами: «l:…», «p:+375…», «ag:…»."""
    return re.sub(r"^[a-z]{1,3}:", "", str(value or "").strip())


def clean_phone(value: str) -> str:
    raw = _strip_prefix(value)
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return ("+" if raw.startswith("+") or len(digits) >= 11 else "") + digits


def clean_username(value: str) -> str | None:
    raw = str(value or "").strip()
    raw = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", "", raw, flags=re.I).lstrip("@").strip().strip("/")
    return raw if _USERNAME_RE.fullmatch(raw) else None


def _interest(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()
    return text[:1].upper() + text[1:] if text else ""


def _studied(value: str) -> bool | None:
    text = str(value or "").strip().lower()
    if text.startswith("да"):
        return True
    if text.startswith("нет"):
        return False
    return None


def _source(platform: str, ad_name: str, organic: str) -> str:
    if str(organic or "").strip().lower() == "true":
        return "органика"
    where = _PLATFORMS.get(str(platform or "").strip().lower(), str(platform or "").strip())
    return " · ".join(part for part in (where, str(ad_name or "").strip()) if part)


def _created(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _column(row: dict[str, str], prefix: str) -> str:
    """Колонки вопросов Meta называет по тексту вопроса («учились_в_almira_раньше?») — ищем по началу."""
    for key, value in row.items():
        if str(key or "").strip().lower().startswith(prefix):
            return str(value or "").strip()
    return ""


def parse_leads(csv_text: str) -> list[Lead]:
    leads = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        lead_id = _strip_prefix(row.get("id", ""))
        if not lead_id:
            continue
        nick = _column(row, "ваш_ник")
        leads.append(
            Lead(
                lead_id=lead_id,
                created=_created(row.get("created_time", "")),
                name=_column(row, "полное_имя") or _column(row, "full_name"),
                phone=clean_phone(_column(row, "номер_телефона") or _column(row, "phone")),
                username=clean_username(nick),
                nick_raw=nick,
                studied=_studied(_column(row, "учились")),
                interest=_interest(_column(row, "что_интересует")),
                source=_source(row.get("platform", ""), row.get("ad_name", ""), row.get("is_organic", "")),
                form_name=str(row.get("form_name") or "").strip(),
                status=str(row.get("Статус") or "").strip(),
            )
        )
    return leads


# ---- тексты -------------------------------------------------------------------------------------


def _plain(value: str) -> str:
    """Имя в разметке: без угловых скобок, чтобы имя из формы или Telegram не подменяло ссылку."""
    return re.sub(r"[<>]", "", str(value or "")).strip()


def _what(lead: Lead) -> str:
    """«вебинар 24 октября» из «Бесплатный вебинар 24 октября»; иначе интерес или имя формы."""
    interest = lead.interest
    if interest.lower().startswith("бесплатный вебинар"):
        return interest[len("бесплатный "):]
    return interest.lower() if interest else lead.form_name


def _when(moment: datetime | None) -> str:
    return moment.astimezone(MINSK).strftime("%d.%m в %H:%M") if moment else ""


def _contacts(phone: str) -> str:
    """Написать напрямую: WhatsApp и Telegram по номеру (ссылки https — format_telegram_html их сохраняет)."""
    digits = phone.lstrip("+")
    if not phone.startswith("+") or len(digits) < 10:
        return ""
    return f'💬 <a href="https://wa.me/{digits}">WhatsApp</a> · <a href="https://t.me/+{digits}">Telegram по номеру</a>'


def alert_text(lead: Lead, opened: Opened | None = None) -> str:
    """Алерт по строке таблицы; ``opened`` — человек уже в боте: имя — ссылка на его чат."""
    what = _what(lead)
    lines = ["🔔 Новая заявка" + (f" · {what}" if what else "")]
    name = _plain(lead.name) or (_plain(opened.name) if opened else "")
    if opened:
        lines.append(f'<b><a href="tg://user?id={int(opened.telegram_user_id)}">{name or "Без имени"}</a></b>')
        lines.append("✅ Бот открыт" + (" — ссылку на эфир получит там" if what.startswith("вебинар") else ""))
    else:
        if name:
            lines.append(f"<b>{name}</b>")
        lines.append("⚠️ Бот не открыт — свяжитесь напрямую")
    if lead.phone:
        lines.append(f"📞 {lead.phone}")
        if contacts := _contacts(lead.phone):
            lines.append(contacts)
    username = lead.username or (opened.username if opened else None)
    if username:
        lines.append(f"✈️ @{username}")
    elif lead.nick_raw:
        lines.append(f"✈️ {lead.nick_raw}")
    else:
        lines.append("✈️ ник не указан")
    if lead.studied is True:
        lines.append("🎓 Уже учились в ALMIRA — скидка 15%")
    elif lead.studied is False:
        lines.append("🎓 Впервые в ALMIRA")
    if lead.interest:
        lines.append(f"🎯 Интерес: {lead.interest}")
    if lead.source:
        lines.append(f"📣 {lead.source}")
    if lead.created:
        lines.append(f"🕓 {_when(lead.created)}")
    return "\n".join(lines)


def _norm_name(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _near(created: datetime | None, opened_at: datetime | None) -> tuple[bool, timedelta] | None:
    """Ключ близости заявки и открытия бота (меньше — ближе), None — не похоже на одного человека.
    Заявка до открытия всегда раньше заявки «чуть после» (запас на часы): «TheOldMan999» открыл бота
    через 108 с после своей формы и за 46 с до чужой (17.10.2026)."""
    if created is None or opened_at is None:
        return None
    gap = opened_at - created
    return (gap < timedelta(0), abs(gap)) if -OPEN_CLOCK_SKEW <= gap <= OPEN_AFTER_FORM else None


def _nicks_clash(a: str | None, b: str | None) -> bool:
    """Оба ника известны и разные — это разные люди."""
    return bool(a and b and a.lstrip("@").casefold() != b.lstrip("@").casefold())


def _same_name(a: str, b: str) -> bool:
    """Имя и фамилия совпали (в любом порядке). Одно имя — не признак: «Наталья» @Lliiss12345 открыла
    бота без заявки, а сопоставилась с другой Натальей, и та выпала бы из «Не в боте» (18.10.2026)."""
    x, y = _norm_name(a), _norm_name(b)
    return len(x.split()) >= 2 and x in (y, " ".join(reversed(y.split())))


def match_lead(
    leads: list[Lead], *, username: str | None, name: str, opened_at: datetime | None = None,
) -> Lead | None:
    """Строка таблицы человека, открывшего бота: по нику, по имени и фамилии (в любом порядке), иначе
    ближайшая по времени заявка за ``OPEN_AFTER_FORM`` до открытия, если ники не спорят."""
    if username:
        wanted = username.lstrip("@").casefold()
        for lead in reversed(leads):
            if lead.username and lead.username.casefold() == wanted:
                return lead
    for lead in reversed(leads):
        # Имя и фамилия важнее «ника» из формы: туда пишут что угодно (Вита Василюк — «Vita»).
        if _same_name(name, lead.name):
            return lead
    timed = [
        (gap, lead) for lead in leads
        if not _nicks_clash(username, lead.username) and (gap := _near(lead.created, opened_at)) is not None
    ]
    return min(timed, key=lambda item: item[0])[1] if timed else None


def opened_for(lead: Lead, opens: list[Opened]) -> Opened | None:
    """Открыл ли бота человек этой заявки: по строке, сопоставленной при открытии, по нику, по имени."""
    for opened in opens:
        if opened.lead_id and opened.lead_id == lead.lead_id:
            return opened
    if lead.username:
        wanted = lead.username.casefold()
        for opened in opens:
            if opened.username and opened.username.lstrip("@").casefold() == wanted:
                return opened
    for opened in opens:
        if not opened.lead_id and _same_name(lead.name, opened.name):
            return opened
    # Строка при открытии ещё не дошла до таблицы — ближайшее по времени открытие без своей строки.
    timed = [
        (gap, opened) for opened in opens
        if not opened.lead_id and not _nicks_clash(lead.username, opened.username)
        and (gap := _near(lead.created, opened.opened_at)) is not None
    ]
    return min(timed, key=lambda item: item[0])[1] if timed else None


def opened_text(*, name: str, username: str | None, user_id: int | None, webinar: bool) -> str:
    """Имя — ссылка в личный чат (``tg://user?id=…``, ``format_telegram_html`` её сохраняет):
    у человека без публичного ника администратор открывает чат нажатием на имя (16.10.2026)."""
    who = _plain(name) or "Без имени"
    if user_id:
        who = f'<a href="tg://user?id={int(user_id)}">{who}</a>'
    nick = f" (@{username})" if username else " (без @ — чат по имени)"
    return f"✅ <b>{who}</b>{nick} — в боте" + (", ссылку на эфир получит здесь." if webinar else ".")


# ---- таблица ------------------------------------------------------------------------------------


def sheet_config(support: dict[str, Any] | None) -> dict[str, str] | None:
    raw = (support or {}).get("leads_sheet")
    if not isinstance(raw, dict):
        return None
    sheet_id, gid = str(raw.get("id") or "").strip(), str(raw.get("gid") or "0").strip()
    if not _SHEET_ID_RE.fullmatch(sheet_id) or not _GID_RE.fullmatch(gid):
        return None
    return {"id": sheet_id, "gid": gid, "since": str(raw.get("since") or "").strip()}


def sheet_url(config: dict[str, str]) -> str:
    return _SHEET_URL.format(id=config["id"], gid=config["gid"])


async def fetch_leads(config: dict[str, str]) -> list[Lead]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC, follow_redirects=True) as client:
        response = await client.get(_EXPORT_URL.format(id=config["id"], gid=config["gid"]))
    response.raise_for_status()
    if "text/csv" not in response.headers.get("content-type", ""):
        # Доступ по ссылке закрыт — Google отдаёт страницу входа, а не CSV.
        raise RuntimeError("leads_sheet_not_csv")
    return parse_leads(response.content.decode("utf-8-sig"))


# ---- кто открыл бота ----------------------------------------------------------------------------


async def record_open(
    tenant_id: str, showcase_id: str, *, telegram_user_id: int, username: str | None, name: str, tag: str,
    lead_id: str | None, overwrite: bool = True,
) -> None:
    """Человек открыл бота витрины. /start ig_… — с меткой рекламы (повторное открытие обновляет ник, имя,
    строку); любой другой вход (``tag`` BOT_TAG, ``overwrite=False``) — только если записи ещё нет: вход
    на сайт через Telegram не стирает метку рекламы."""
    from app.db import fetch_one, tenant_connection

    on_conflict = (
        """do update
              set telegram_username = coalesce(excluded.telegram_username, academy_ad_opens.telegram_username),
                  name = excluded.name,
                  tag = excluded.tag,
                  lead_id = coalesce(excluded.lead_id, academy_ad_opens.lead_id)"""
        if overwrite else "do nothing"
    )
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            f"""
            insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username, name, tag, lead_id)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, showcase_id, telegram_user_id) {on_conflict}
            returning telegram_user_id
            """,
            (tenant_id, showcase_id, int(telegram_user_id), username, name, tag, lead_id),
        )


async def load_opens(conn: Any, tenant_id: str, showcase_id: str) -> list[Opened]:
    from app.db import fetch_all

    rows = await fetch_all(
        conn,
        """
        select telegram_user_id, telegram_username, name, lead_id, opened_at, tag, webinar from academy_ad_opens
        where tenant_id = %s and showcase_id = %s order by opened_at desc
        """,
        (tenant_id, showcase_id),
    )
    return [
        Opened(
            int(r["telegram_user_id"]), r["telegram_username"], str(r["name"] or ""), r["lead_id"], r["opened_at"],
            str(r["tag"] or ""), r["webinar"],
        )
        for r in rows
    ]


async def alert_pending(tenant_id: str, showcase: Any, lead: Lead | None) -> bool:
    """Алерт по строке этого человека ещё впереди — отметка «бот открыт» будет в нём, отдельное
    «✅ … — в боте» не нужно. Строка старше ``since`` алерта не получит — тогда False."""
    config = sheet_config(showcase.support)
    if lead is None or config is None or lead.created is None:
        return False
    since = _created(config["since"]) if config["since"] else None
    if since is not None and lead.created < since:
        return False
    from app.db import fetch_one, tenant_connection

    async with tenant_connection(tenant_id) as conn:
        queued = await fetch_one(
            conn,
            "select 1 as queued from platform_outbox where idempotency_key = %s",
            (f"ad_lead:{showcase.showcase_id}:{lead.lead_id}",),
        )
    return queued is None


# ---- флаги «в боте» для таблицы ----------------------------------------------------------------


class SheetUnavailable(Exception):
    """Таблицу заявок не удалось прочитать (доступ закрыт, Google не ответил)."""


def flags_key(support: dict[str, Any] | None) -> str:
    """Ключ запроса флагов: ``leads_sheet.flags_key``. Пусто — флаги не настроены (нет ключа или таблицы)."""
    if sheet_config(support) is None:
        return ""
    raw = (support or {}).get("leads_sheet", {}).get("flags_key")
    return raw.strip() if isinstance(raw, str) else ""


async def lead_flags(tenant_id: str, showcase: Any) -> dict[str, Any]:
    """Флаг «в боте» для таблицы заявок (Виктор, 18.10.2026): id строк, чей человек открыл бота, и остальные.

    Тот же ``opened_for``, что и в алертах, — таблица и алерт не расходятся. Повторяющийся id попадает
    только в ``in_bot``, если хоть одна его строка сопоставлена. Нет таблицы — ``SheetUnavailable``."""
    from app.db import tenant_connection

    config = sheet_config(showcase.support)
    if config is None:
        raise SheetUnavailable("leads_sheet_not_configured")
    try:
        leads = await fetch_leads(config)
    except Exception as exc:
        logger.warning("ad_leads_flags_fetch_failed", extra={"showcase": showcase.showcase_id, "error": type(exc).__name__})
        raise SheetUnavailable(type(exc).__name__) from exc
    async with tenant_connection(tenant_id) as conn:
        opens = await load_opens(conn, tenant_id, showcase.showcase_id)
    in_bot = {lead.lead_id for lead in leads if opened_for(lead, opens) is not None}
    not_in_bot = {lead.lead_id for lead in leads} - in_bot
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "in_bot": sorted(in_bot),
        "not_in_bot": sorted(not_in_bot),
    }


# ---- шаг воркера --------------------------------------------------------------------------------


async def ad_leads_step(
    tenant_ids: set[str], bindings: dict[str, Any], *, now: float | None = None, clock: datetime | None = None,
) -> int:
    """Новые строки таблиц заявок витрин → алерты администраторам. Возвращает число новых алертов."""
    from app.academy.showcases import load_showcases
    from app.db import tenant_connection
    from app.jobs.outbox import enqueue_outbox_event
    from app.telegram.showcase_support import KIND_ADMIN, support_targets

    moment = time.monotonic() if now is None else now
    wall = clock or datetime.now(timezone.utc)
    queued = 0
    for tenant_id in sorted(tenant_ids):
        for showcase in await load_showcases(tenant_id):
            config = sheet_config(showcase.support)
            binding_id = showcase.telegram_binding_id
            if not config or not binding_id or binding_id not in bindings:
                continue
            key = f"{tenant_id}:{showcase.showcase_id}"
            if moment - _last_poll.get(key, float("-inf")) < POLL_INTERVAL_SEC:
                continue
            _last_poll[key] = moment
            admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)
            if admin is None:
                logger.info("ad_leads_no_admin", extra={"showcase": showcase.showcase_id})
                continue
            try:
                leads = await fetch_leads(config)
            except Exception as exc:
                logger.warning("ad_leads_fetch_failed", extra={"showcase": showcase.showcase_id, "error": type(exc).__name__})
                continue
            since = _created(config["since"]) if config["since"] else None
            fresh = [lead for lead in leads if since is None or (lead.created is not None and lead.created >= since)]
            if not fresh:
                continue
            markup = {"inline_keyboard": [[{"text": "Открыть таблицу заявок", "url": sheet_url(config)}]]}
            async with tenant_connection(tenant_id) as conn:
                opens = await load_opens(conn, tenant_id, showcase.showcase_id)
                for lead in fresh:
                    opened = opened_for(lead, opens)
                    if opened is None and lead.created is not None and wall - lead.created < OPEN_GRACE:
                        continue  # бота обычно открывают сразу после формы — ждём, чтобы отметка была верной
                    row = await enqueue_outbox_event(
                        conn,
                        tenant_id=tenant_id,
                        event_type=EVENT,
                        idempotency_key=f"ad_lead:{showcase.showcase_id}:{lead.lead_id}",
                        payload={
                            "chat_id": str(int(admin)),
                            "text": alert_text(lead, opened),
                            "binding_id": binding_id,
                            "reply_markup": markup,
                        },
                        due_at=datetime.now(timezone.utc),
                    )
                    if row and row.get("created"):
                        queued += 1
            if queued:
                logger.info("ad_leads_queued", extra={"showcase": showcase.showcase_id, "queued": queued})
    return queued
