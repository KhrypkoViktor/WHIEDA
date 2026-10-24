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
* Строка не нашлась — под этой строкой кнопки «Это …» по свежим заявкам без бота (``link_markup``):
  администратор сам связывает человека со строкой (``link_open``), в таблице — «да» (24.10.2026).

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
from dataclasses import dataclass, replace
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
# Форма за минуту до /start — это он, даже если «ник» в форме другой: туда пишут ник Instagram
# (Маргарита Пятых — «Sirni_pelmen», открыла бота @Bonni_P через 13 с после формы, 20.10.2026).
CLASH_TIGHT = timedelta(seconds=60)
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
    # Пометки администратора: все колонки после lead_status, кроме «В боте» (Кирилл пишет «написал»,
    # «оплатил» и в безымянные колонки, «Статус» у него пуст — 19.10.2026).
    status: str = ""
    tab: str = ""  # gid вкладки таблицы, откуда строка (кнопка «Открыть таблицу заявок» — на неё)
    # Колонка «Эфир» (Кирилл, 23.10.2026): 24 или 31 — на какой вебинар человек записан по факту звонка.
    webinar_mark: str = ""
    base: str = ""  # анатомия: «Есть ли у вас база академического рисунка?» — Да / Немного / Нет


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
    series: str = ""  # V40: серия вебинаров ('' — композиция, 'anatomy' …): своя строка в каждой серии
    joined: tuple[tuple[str, datetime], ...] = ()  # V41: (метка, первое нажатие «Присоединиться к вебинару»)


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
    # Meta пишет ответ строчными: «курс_«игра_формы»» → «Курс «Игра формы»».
    text = re.sub(r"«(\w)", lambda m: "«" + m.group(1).upper(), text)
    return text[:1].upper() + text[1:] if text else ""


def wants_course(lead: Lead) -> bool:
    """В форме вебинара выбран курс, а не дата: на эфир такого человека бот всё равно записывает — по
    дате из имени формы (Валентина Чижикова, форма 24.10, 21.10.2026), а Кириллу — подсказка предложить курс."""
    return lead.interest.casefold().startswith("курс")


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


def series_markers(support: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """(слово в имени формы, серия, подпись) из ``support.webinars``: {"series": "anatomy", "form": "анатом",
    "label": "анатомия"}. Композиция — серия '' без слова: всё, что не подошло к другим."""
    raw = (support or {}).get("webinars")
    out: list[tuple[str, str, str]] = []
    for item in (raw.values() if isinstance(raw, dict) else []):
        if not isinstance(item, dict):
            continue
        series, form = str(item.get("series") or "").strip(), str(item.get("form") or "").strip().casefold()
        if series and form and (form, series) not in [(f, x) for f, x, _ in out]:
            out.append((form, series, str(item.get("label") or "").strip()))
    return out


def lead_series(lead: Lead | None, markers: list[tuple[str, str, str]]) -> str:
    form = (lead.form_name if lead else "").casefold()
    return next((series for marker, series, _ in markers if marker in form), "")


def series_label(series: str, markers: list[tuple[str, str, str]]) -> str:
    return next((label for _, x, label in markers if x == series and label), "")


FLAG_HEADER = "В боте"  # колонку пишет скрипт таблицы (lead-flags), это не пометка администратора
WEBINAR_HEADER = "эфир"  # колонка «Эфир» администратора: 24 / 31 — важнее пункта формы и кнопки в боте


def _rows(csv_text: str) -> list[tuple[dict[str, str], str]]:
    """Строки как {заголовок: значение} (первая колонка с таким именем) и пометки администратора —
    непустые значения всех колонок после lead_status, кроме «В боте», включая безымянные."""
    reader = csv.reader(io.StringIO(csv_text))
    names = [str(name or "").strip() for name in next(reader, [])]
    notes_from = names.index("lead_status") + 1 if "lead_status" in names else len(names)
    out = []
    for values in reader:
        row: dict[str, str] = {}
        for index, name in enumerate(names):
            if name and name not in row:
                row[name] = values[index] if index < len(values) else ""
        notes = [
            str(values[index]).strip() for index in range(notes_from, len(values))
            if str(values[index]).strip() and (index >= len(names) or names[index] != FLAG_HEADER)
        ]
        out.append((row, " | ".join(notes)))
    return out


def parse_leads(csv_text: str) -> list[Lead]:
    leads = []
    for row, notes in _rows(csv_text):
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
                interest=_interest(_column(row, "что_интересует") or _column(row, "какой_эфир")),
                source=_source(row.get("platform", ""), row.get("ad_name", ""), row.get("is_organic", "")),
                form_name=str(row.get("form_name") or "").strip(),
                status=notes,
                base=_interest(next((str(v) for k, v in row.items() if "академическ" in k.casefold()), "")),
                webinar_mark=next(
                    (str(value).strip() for key, value in row.items() if key.casefold() == WEBINAR_HEADER), ""
                ),
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
    return interest[:1].lower() + interest[1:] if interest else lead.form_name


def _when(moment: datetime | None) -> str:
    return moment.astimezone(MINSK).strftime("%d.%m в %H:%M") if moment else ""


def _contacts(phone: str) -> str:
    """Написать напрямую: WhatsApp и Telegram по номеру (ссылки https — format_telegram_html их сохраняет)."""
    digits = phone.lstrip("+")
    if not phone.startswith("+") or len(digits) < 10:
        return ""
    return f'💬 <a href="https://wa.me/{digits}">WhatsApp</a> · <a href="https://t.me/+{digits}">Telegram по номеру</a>'


def alert_text(lead: Lead, opened: Opened | None = None, *, label: str = "") -> str:
    """Алерт по строке таблицы; ``opened`` — человек уже в боте: имя — ссылка на его чат; ``label`` — серия
    вебинаров («анатомия»), чтобы Кирилл не путал её с композицией."""
    what = _what(lead)
    head = " · ".join(x for x in (label, what) if x)
    lines = ["🔔 Новая заявка" + (f" · {head}" if head else "")]
    if wants_course(lead):
        lines.append(f"💡 Выбран курс, а не дата эфира. На вебинар бот запишет по форме ({lead.form_name}) — предложите и курс.")
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
    if lead.base:
        lines.append(f"✏️ База академического рисунка: {lead.base}")
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


def _tight(created: datetime | None, opened_at: datetime | None) -> bool:
    return bool(created and opened_at) and -OPEN_CLOCK_SKEW <= opened_at - created <= CLASH_TIGHT


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
        if (not _nicks_clash(username, lead.username) or _tight(lead.created, opened_at))
        and (gap := _near(lead.created, opened_at)) is not None
    ]
    return min(timed, key=lambda item: item[0])[1] if timed else None


def opened_for(lead: Lead, opens: list[Opened], series: str = "") -> Opened | None:
    """Открыл ли бота человек этой заявки: по строке, сопоставленной при открытии, по нику, по имени."""
    for opened in opens:
        if opened.lead_id and opened.lead_id == lead.lead_id:
            return opened
    if lead.username:
        wanted = lead.username.casefold()
        for opened in opens:
            if opened.username and opened.username.lstrip("@").casefold() == wanted:
                return opened
    def free(opened: Opened) -> bool:
        return not opened.lead_id or opened.series != series

    for opened in opens:
        if free(opened) and _same_name(lead.name, opened.name):
            return opened
    # Строка при открытии ещё не дошла до таблицы — ближайшее по времени открытие без своей строки.
    timed = [
        (gap, opened) for opened in opens
        if free(opened)
        and (not _nicks_clash(lead.username, opened.username) or _tight(lead.created, opened.opened_at))
        and (gap := _near(lead.created, opened.opened_at)) is not None
    ]
    return min(timed, key=lambda item: item[0])[1] if timed else None


def opened_text(*, name: str, username: str | None, user_id: int | None, webinar: bool, found: bool = True) -> str:
    """Имя — ссылка в личный чат (``tg://user?id=…``, ``format_telegram_html`` её сохраняет):
    у человека без публичного ника администратор открывает чат нажатием на имя (16.10.2026).
    Строка таблицы не нашлась (``found=False``) — подсказка про личную ссылку (19.10.2026)."""
    who = _plain(name) or "Без имени"
    if user_id:
        who = f'<a href="tg://user?id={int(user_id)}">{who}</a>'
    nick = f" (@{username})" if username else " (без @ — чат по имени)"
    text = f"✅ <b>{who}</b>{nick} — в боте" + (", ссылку на эфир получит здесь." if webinar else ".")
    if not found:
        text += " В таблице заявок не найдена — отправьте человеку личную ссылку из списка «не в боте»."
    return text


# ---- «Это …»: администратор связывает человека в боте со строкой таблицы -------------------------
# Кирилл, 24.10.2026: «-» без ника открыл бота по ссылке из WhatsApp через 44 мин после формы — ни ник, ни имя,
# ни время (``OPEN_AFTER_FORM``) не сошлись, в таблице «нет», хотя человек в боте. Кто это, знает администратор.
LINK_WITHIN = timedelta(hours=48)
LINK_MAX = 6
LINK_PREFIX = "alink:"
LINK_HINT = "Знаете, кто это? Нажмите на заявку — в таблице будет «да»."


def link_candidates(
    leads: list[Lead], opens: list[Opened], series: str, *, now: datetime, within: timedelta = LINK_WITHIN,
) -> list[Lead]:
    """Свежие заявки серии (за ``within``), чей человек бота не открыл, — новые сверху, не больше ``LINK_MAX``."""
    fresh = [
        lead for lead in leads
        if lead.created and timedelta(0) <= now - lead.created <= within and opened_for(lead, opens, series) is None
    ]
    return sorted(fresh, key=lambda lead: lead.created, reverse=True)[:LINK_MAX]


def link_markup(telegram_user_id: int, candidates: list[Lead]) -> dict[str, Any] | None:
    """По кнопке на заявку: «Это Имя · 24.10 11:01 · …2233» → ``alink:<id в Telegram>:<id заявки>``."""
    rows = []
    for lead in candidates:
        data = f"{LINK_PREFIX}{int(telegram_user_id)}:{lead.lead_id}"
        if len(data.encode()) > 64:
            continue
        digits = re.sub(r"\D", "", lead.phone or "")
        label = f"Это {(_plain(lead.name) or 'Без имени')[:28]} · {_when(lead.created).replace(' в ', ' ')}"
        if len(digits) >= 4:
            label += f" · …{digits[-4:]}"
        rows.append([{"text": label[:64], "callback_data": data}])
    return {"inline_keyboard": rows} if rows else None


def parse_link(data: str) -> tuple[int, str] | None:
    match = re.fullmatch(rf"{LINK_PREFIX}(\d{{1,15}}):([A-Za-z0-9_-]{{1,40}})", str(data or ""))
    return (int(match.group(1)), match.group(2)) if match else None


async def link_open(tenant_id: str, showcase_id: str, *, telegram_user_id: int, lead_id: str, series: str) -> bool:
    """Строка таблицы для открытия бота без своей строки; уже связанное не трогаем. True — связали."""
    from app.db import fetch_one, tenant_connection

    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "update academy_ad_opens set lead_id = %s where tenant_id = %s and showcase_id = %s"
            " and telegram_user_id = %s and series = %s and lead_id is null returning telegram_user_id",
            (lead_id, tenant_id, showcase_id, int(telegram_user_id), series),
        )
    return row is not None


# ---- таблица ------------------------------------------------------------------------------------


def sheet_config(support: dict[str, Any] | None) -> dict[str, Any] | None:
    """``leads_sheet = {id, gid, gids?, since}``. ``gids`` — все вкладки с заявками (21.10.2026: форма 31.10
    Meta пишет в свою вкладку той же таблицы); ``gid`` — первая."""
    raw = (support or {}).get("leads_sheet")
    if not isinstance(raw, dict):
        return None
    sheet_id, gid = str(raw.get("id") or "").strip(), str(raw.get("gid") or "0").strip()
    if not _SHEET_ID_RE.fullmatch(sheet_id) or not _GID_RE.fullmatch(gid):
        return None
    extra = raw.get("gids") if isinstance(raw.get("gids"), list) else []
    gids = list(dict.fromkeys([gid] + [str(g).strip() for g in extra if _GID_RE.fullmatch(str(g).strip())]))
    return {"id": sheet_id, "gid": gid, "gids": gids, "since": str(raw.get("since") or "").strip()}


def sheet_url(config: dict[str, Any], tab: str = "") -> str:
    return _SHEET_URL.format(id=config["id"], gid=tab or config["gid"])


def merge_leads(tabs: list[list[Lead]]) -> list[Lead]:
    """Заявки всех вкладок одним списком по времени (поиск идёт с конца — свежие важнее); один id — одна заявка."""
    seen: set[str] = set()
    out: list[Lead] = []
    for lead in (lead for tab in tabs for lead in tab):
        if lead.lead_id in seen:
            continue
        seen.add(lead.lead_id)
        out.append(lead)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(out, key=lambda lead: lead.created or floor)


async def fetch_leads(config: dict[str, Any]) -> list[Lead]:
    """Все вкладки ``gids``; не прочиталась хоть одна — ошибка: неполный список врал бы «не в боте»."""
    tabs: list[list[Lead]] = []
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC, follow_redirects=True) as client:
        for gid in config.get("gids") or [config["gid"]]:
            response = await client.get(_EXPORT_URL.format(id=config["id"], gid=gid))
            response.raise_for_status()
            if "text/csv" not in response.headers.get("content-type", ""):
                # Доступ по ссылке закрыт — Google отдаёт страницу входа, а не CSV.
                raise RuntimeError("leads_sheet_not_csv")
            tabs.append([replace(lead, tab=gid) for lead in parse_leads(response.content.decode("utf-8-sig"))])
    return merge_leads(tabs)


# ---- кто открыл бота ----------------------------------------------------------------------------


async def record_open(
    tenant_id: str, showcase_id: str, *, telegram_user_id: int, username: str | None, name: str, tag: str,
    lead_id: str | None, overwrite: bool = True, series: str = "",
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
            insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username, name, tag, lead_id, series)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, showcase_id, telegram_user_id, series) {on_conflict}
            returning telegram_user_id
            """,
            (tenant_id, showcase_id, int(telegram_user_id), username, name, tag, lead_id, series),
        )


def _joined(raw: Any) -> tuple[tuple[str, datetime], ...]:
    out = []
    for tag, at in (raw if isinstance(raw, dict) else {}).items():
        try:
            out.append((str(tag), datetime.fromisoformat(str(at))))
        except ValueError:
            continue
    return tuple(sorted(out, key=lambda x: x[1]))


def joined_text(opened: Opened | None) -> str:
    """Колонка «На эфире»: «✅ 24.10 17:26» — первое нажатие кнопки эфира, по каждому вебинару."""
    if opened is None or not opened.joined:
        return ""
    return "✅ " + ", ".join(at.astimezone(MINSK).strftime("%d.%m %H:%M") for _, at in opened.joined)


async def load_opens(conn: Any, tenant_id: str, showcase_id: str) -> list[Opened]:
    from app.db import fetch_all

    rows = await fetch_all(
        conn,
        """
        select telegram_user_id, telegram_username, name, lead_id, opened_at, tag, webinar, series, joined
        from academy_ad_opens where tenant_id = %s and showcase_id = %s order by opened_at desc
        """,
        (tenant_id, showcase_id),
    )
    return [
        Opened(
            int(r["telegram_user_id"]), r["telegram_username"], str(r["name"] or ""), r["lead_id"], r["opened_at"],
            str(r["tag"] or ""), r["webinar"], str(r.get("series") or ""), _joined(r.get("joined")),
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
    markers = series_markers(showcase.support)
    in_bot: set[str] = set()
    joined: dict[str, str] = {}  # «На эфире» (24.10.2026): id → «✅ 24.10 17:26»
    for lead in leads:
        opened = opened_for(lead, opens, lead_series(lead, markers))
        if opened is not None:
            in_bot.add(lead.lead_id)
            if opened.joined:
                joined[lead.lead_id] = joined_text(opened)
    not_in_bot = {lead.lead_id for lead in leads} - in_bot
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "in_bot": sorted(in_bot),
        "not_in_bot": sorted(not_in_bot),
        "joined": joined,
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
            markers = series_markers(showcase.support)
            async with tenant_connection(tenant_id) as conn:
                opens = await load_opens(conn, tenant_id, showcase.showcase_id)
                for lead in fresh:
                    series = lead_series(lead, markers)
                    opened = opened_for(lead, opens, series)
                    if opened is None and lead.created is not None and wall - lead.created < OPEN_GRACE:
                        continue  # бота обычно открывают сразу после формы — ждём, чтобы отметка была верной
                    row = await enqueue_outbox_event(
                        conn,
                        tenant_id=tenant_id,
                        event_type=EVENT,
                        idempotency_key=f"ad_lead:{showcase.showcase_id}:{lead.lead_id}",
                        payload={
                            "chat_id": str(int(admin)),
                            "text": alert_text(lead, opened, label=series_label(series, markers)),
                            "binding_id": binding_id,
                            "reply_markup": {"inline_keyboard": [[
                                {"text": "Открыть таблицу заявок", "url": sheet_url(config, lead.tab)},
                            ]]},
                        },
                        due_at=datetime.now(timezone.utc),
                    )
                    if row and row.get("created"):
                        queued += 1
            if queued:
                logger.info("ad_leads_queued", extra={"showcase": showcase.showcase_id, "queued": queued})
    return queued
