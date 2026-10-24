"""Напоминания о вебинарах витрины (Виктор через «Настройку рекламы», 18.10.2026).

Где: ``academy_showcases.support.webinars`` — метка ссылки с рекламы → вебинар::

    {"ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""}, …}

``url`` — ссылка на эфир; Виктор присылает её позже, пока пусто — напоминания со ссылкой не идут.

Кому: людям, открывшим бота по ссылке с рекламы (``academy_ad_opens``, V37). Вебинар человека —
по его строке таблицы заявок (интерес «…24 октября»), а без строки — по метке /start.

* за сутки, за 2 часа — без ссылки; за час и за 5 минут — только со ссылкой;
* открыл бота после времени напоминания — получает только оставшиеся;
* каждое — одному человеку один раз (ключ outbox), у каждого своё окно отправки (``REMINDERS``);
* администратору витрины: за 1,5 часа нет ссылки — одна строка; за 30 минут — список заявок
  на этот вебинар, которые бота не открыли (телефон, WhatsApp, Telegram по номеру).

Подтверждение даты (18.10.2026): ссылка «Спасибо» формы у всех — ig_web24, поэтому приветствие без
даты; когда находится строка заявки (до 10 минут после /start) — «Вы записаны на …» с кнопкой
«Перенести на …». Выбор в боте (``academy_ad_opens.webinar``, V38) важнее пункта формы.

Отправляет обычный цикл уведомлений (``process_due_notifications``): заблокировавший бота —
«dead» без повторов, остальные не страдают.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.academy.ad_leads import (
    MINSK,
    Lead,
    Opened,
    _contacts,
    _plain,
    fetch_leads,
    load_opens,
    match_lead,
    opened_for,
    sheet_config,
)

logger = logging.getLogger(__name__)

EVENT = "showcase_webinar_reminder"
NO_LINK_WARNING = timedelta(minutes=90)  # 16:00 при начале в 17:30
CALL_LIST = timedelta(minutes=30)  # 17:00
MESSAGE_LIMIT = 3800  # запас до 4096 после разметки


@dataclass(frozen=True)
class Webinar:
    tag: str
    at: datetime
    date: str
    url: str
    # После эфира — одно приглашение на повтор (``followup``: {at, to}) записанным, кто не оплатил.
    followup_at: datetime | None = None
    followup_to: str = ""
    # Серии (V40, 23.10.2026): '' — композиция; 'anatomy' — анатомия со своими датами, формой и текстами.
    series: str = ""
    form: str = ""  # слово в имени формы Meta, по которому заявка относится к серии («анатом»)
    subject: str = "по композиции"  # «вебинар … по композиции» в напоминаниях
    greeting: str = ""  # приветствие на /start с меткой этого вебинара; пусто — приветствие композиции
    label: str = ""  # подпись серии в алертах администратору («анатомия»)


def series_webinars(webinars: list["Webinar"], series: str) -> list["Webinar"]:
    return [w for w in webinars if w.series == series]


def webinar_series_of(lead: Lead | None, webinars: list["Webinar"]) -> str:
    """Серия заявки — по слову формы из настройки вебинаров; не подошла ни к одной — композиция ('')."""
    form = (lead.form_name if lead else "").casefold()
    return next((w.series for w in webinars if w.form and w.form in form), "")


FOLLOWUP_WINDOW = timedelta(hours=2)

# Прогрев (20.10.2026): support.webinar_warmup = [{"key": "w1", "before_min": 4650, "text": "…{date}…{time}…"}].
# Тексты — в настройке: правка текста без выпуска. Окно отправки — 2 часа после своего времени.
WARMUP_WINDOW = timedelta(hours=2)
_WARMUP_KEY_RE = re.compile(r"^[a-z0-9_]{1,20}$")


@dataclass(frozen=True)
class Warmup:
    key: str
    before: timedelta
    text: str
    photos: tuple[str, ...] = ()  # file_id бота витрины: одна — фото, несколько — альбом; текст — следом
    button: dict[str, str] | None = None  # {text, url} — кнопка-ссылка под текстом
    series: str = ""  # серия вебинаров, к которой относится прогрев ('' — композиция)
    # После эфира (О1, 24.10.2026): after_min вместо before_min — через столько минут после начала; оплатившим
    # не уходит. buttons — ряды кнопок: {text, url} или {text, action: admin | signup | move:<метка>}.
    after: bool = False
    buttons: tuple[tuple[tuple[str, str, str], ...], ...] = ()  # ((текст, "url"|"action", значение), …)


_PHOTO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
_ACTION_RE = re.compile(r"^(admin|signup|move:[A-Za-z0-9_-]{1,40})$")


def _warmup_markup(item: "Warmup", webinar: "Webinar") -> dict[str, Any] | None:
    """Кнопки сообщения: ссылка; «Написать администратору» (обращение); «Записаться на курс» — алерт
    администратору (``wsign:<метка>``); «Записаться на повтор» — перенос (``wbn:<метка>``)."""
    rows: list[list[dict[str, str]]] = [[item.button]] if item.button else []
    for row in item.buttons:
        out = []
        for label, kind, value in row:
            if kind == "url":
                out.append({"text": label, "url": value})
            elif value == "admin":
                out.append({"text": label, "callback_data": "shs:open:admin"})
            elif value == "signup":
                out.append({"text": label, "callback_data": f"wsign:{webinar.tag}"})
            elif value.startswith("move:"):
                out.append({"text": label, "callback_data": f"wbn:{value[5:]}"})
        if out:
            rows.append(out)
    return {"inline_keyboard": rows} if rows else None


# Копия рассылок (Виктор, 20.10.2026): support.broadcast_copy = [telegram id] — каждую рассылку
# (прогрев, напоминания, приглашение на повтор) один раз, как её видят люди, с пометкой «сколько получили».
_COPY_LABELS = {"day": "за сутки", "two_hours": "за 2 часа", "hour": "за час", "five": "за 5 минут",
                "followup": "приглашение на повтор"}


def copy_recipients(support: dict[str, Any] | None) -> list[int]:
    raw = (support or {}).get("broadcast_copy")
    return [int(x) for x in raw if str(x).isdigit()] if isinstance(raw, list) else []


def copy_header(webinar: Webinar, kind: str, sent: int) -> str:
    if kind.startswith("warm_"):
        label = f"прогрев {kind[5:].upper().replace('P', 'П')}"
    elif kind.startswith("post_"):
        label = f"после эфира {kind[5:].upper()}"
    else:
        label = _COPY_LABELS.get(kind, kind)
    return f"<i>📋 Копия рассылки · вебинар {webinar.date} · {label} · получили {sent}</i>\n\n"


def warmup_config(support: dict[str, Any] | None) -> list[Warmup]:
    raw = (support or {}).get("webinar_warmup")
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key, text = str(item.get("key") or ""), str(item.get("text") or "").strip()
        after = item.get("after_min") is not None
        try:
            minutes = int(item.get("after_min") if after else item.get("before_min"))
        except (TypeError, ValueError):
            continue
        photos = tuple(
            str(p) for p in (item.get("photos") if isinstance(item.get("photos"), list) else [])
            if _PHOTO_ID_RE.fullmatch(str(p))
        )[:10]
        raw_button = item.get("button") if isinstance(item.get("button"), dict) else {}
        button = (
            {"text": str(raw_button["text"])[:64], "url": str(raw_button["url"])}
            if raw_button.get("text") and str(raw_button.get("url") or "").startswith("https://") else None
        )
        rows: list[tuple[tuple[str, str, str], ...]] = []
        for raw_row in item.get("buttons") if isinstance(item.get("buttons"), list) else []:
            row = []
            for b in raw_row if isinstance(raw_row, list) else []:
                label = str((b or {}).get("text") or "").strip()[:64] if isinstance(b, dict) else ""
                url, action = str(b.get("url") or "") if label else "", str(b.get("action") or "") if label else ""
                if label and url.startswith("https://"):
                    row.append((label, "url", url))
                elif label and _ACTION_RE.fullmatch(action):
                    row.append((label, "action", action))
            if row:
                rows.append(tuple(row))
        if _WARMUP_KEY_RE.fullmatch(key) and text and 10 <= minutes <= 14 * 24 * 60:
            out.append(Warmup(
                key, timedelta(minutes=minutes), text, photos, button, str(item.get("series") or "").strip(),
                after, tuple(rows),
            ))
    return out


def warmup_text(item: Warmup, webinar: Webinar) -> str:
    return item.text.replace("{date}", webinar.date).replace("{time}", webinar.at.astimezone(MINSK).strftime("%H:%M"))


# Напоминание: ключ, за сколько до начала, нужна ли ссылка, сколько после своего времени ещё слать
# (опоздавший воркер или поздняя ссылка: «Через час начинаем» за 40 минут до эфира не шлём).
REMINDERS: tuple[tuple[str, timedelta, bool, timedelta], ...] = (
    ("day", timedelta(hours=24), False, timedelta(minutes=45)),
    ("two_hours", timedelta(hours=2), False, timedelta(minutes=30)),
    ("hour", timedelta(hours=1), True, timedelta(minutes=20)),
    ("five", timedelta(minutes=5), True, timedelta(minutes=10)),
)


def webinars_config(support: dict[str, Any] | None) -> list[Webinar]:
    raw = (support or {}).get("webinars")
    if not isinstance(raw, dict):
        return []
    out = []
    for tag, item in raw.items():
        if not isinstance(item, dict):
            continue
        try:
            at = datetime.fromisoformat(str(item.get("at") or ""))
        except ValueError:
            continue
        if at.tzinfo is None:
            continue
        url = str(item.get("url") or "").strip()
        follow = item.get("followup") if isinstance(item.get("followup"), dict) else {}
        try:
            follow_at = datetime.fromisoformat(str(follow.get("at") or "")) if follow else None
        except ValueError:
            follow_at = None
        if follow_at is not None and follow_at.tzinfo is None:
            follow_at = None
        out.append(Webinar(
            str(tag), at, str(item.get("date") or "").strip(), url if url.startswith("https://") else "",
            follow_at, str(follow.get("to") or "") if follow_at else "",
            series=str(item.get("series") or "").strip(),
            form=str(item.get("form") or "").strip().casefold(),
            subject=str(item.get("subject") or "").strip() or "по композиции",
            greeting=str(item.get("greeting") or "").strip(),
            label=str(item.get("label") or "").strip(),
        ))
    return out


def next_webinar(webinars: list[Webinar], after: datetime) -> Webinar | None:
    """Ближайший вебинар, который начинается позже ``after``."""
    future = sorted((w for w in webinars if w.at > after), key=lambda w: w.at)
    return future[0] if future else None


def followup_text(webinar: Webinar, repeat: Webinar) -> str:
    time = repeat.at.astimezone(MINSK).strftime("%H:%M")
    return (
        f"Не получилось прийти на вебинар {webinar.date} или хотите пересмотреть? "
        f"{repeat.date[:1].upper()}{repeat.date[1:]} в {time} по Минску — повтор эфира. Записать вас?"
    )


def followup_markup(repeat: Webinar) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": f"Записаться на {repeat.date}", "callback_data": f"wbn:{repeat.tag}"}]]}


def is_paid(lead: Lead | None) -> bool:
    """Кирилл отмечает оплативших в колонке «Статус» таблицы («оплатил», «оплата»…)."""
    return bool(lead and "оплат" in lead.status.casefold())


def reminder_text(kind: str, webinar: Webinar) -> str:
    time = webinar.at.astimezone(MINSK).strftime("%H:%M")
    if kind == "day":
        return (
            f"Завтра, {webinar.date}, в {time} по Минску — бесплатный вебинар Александры Пармон {webinar.subject}.\n\n"
            "Ссылку на эфир пришлём сюда, в этот чат."
        )
    if kind == "two_hours":
        return f"Сегодня в {time} по Минску — вебинар {webinar.subject} с Александрой Пармон. Ссылку пришлём за час до начала."
    if kind == "hour":
        return f"Через час начинаем. Ссылка на эфир: {webinar.url}\n\nНачало в {time} по Минску."
    return f"Начинаем через 5 минут. Заходите: {webinar.url}"


def _link_markup(webinar: Webinar) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Перейти к эфиру", "url": webinar.url}]]}


def due_messages(
    webinar: Webinar, warmups: list[Warmup], now: datetime,
) -> list[tuple[str, datetime, str, dict[str, Any] | None, tuple[str, ...]]]:
    """Что пора отправить записанным на вебинар: прогрев и напоминания — (ключ, время, текст, кнопки, фото)."""
    out: list[tuple[str, datetime, str, dict[str, Any] | None, tuple[str, ...]]] = []
    for item in warmups:
        if item.series != webinar.series:
            continue
        if item.after:
            moment = webinar.at + item.before
            if moment <= now < moment + WARMUP_WINDOW:
                out.append((f"post_{item.key}", moment, warmup_text(item, webinar), _warmup_markup(item, webinar), item.photos))
            continue
        moment = webinar.at - item.before
        if moment <= now < min(moment + WARMUP_WINDOW, webinar.at):
            out.append((f"warm_{item.key}", moment, warmup_text(item, webinar), _warmup_markup(item, webinar), item.photos))
    for kind, moment in due_reminders(webinar, now):
        markup = _link_markup(webinar) if kind in ("hour", "five") else None
        out.append((kind, moment, reminder_text(kind, webinar), markup, ()))
    return out


def due_reminders(webinar: Webinar, now: datetime) -> list[tuple[str, datetime]]:
    """Напоминания, время которых пришло и окно не закрылось: (ключ, время). Со ссылкой — только если она есть."""
    due = []
    for kind, before, needs_url, window in REMINDERS:
        moment = webinar.at - before
        if moment <= now < moment + window and (webinar.url or not needs_url):
            due.append((kind, moment))
    return due


def _in(moment: datetime, now: datetime, until: datetime) -> bool:
    return moment <= now < until


def marked_webinar(lead: Lead | None, webinars: list[Webinar]) -> str | None:
    """Колонка «Эфир» администратора: «31», «31.10», «на 31» → вебинар этого числа (Виктор, 23.10.2026)."""
    found = re.search(r"(?<!\d)(\d{1,2})(?!\d)", lead.webinar_mark) if lead and lead.webinar_mark else None
    if not found:
        return None
    day = int(found.group(1))
    return next((w.tag for w in webinars if w.at.astimezone(MINSK).day == day), None)


def lead_webinar(lead: Lead | None, webinars: list[Webinar]) -> str | None:
    """Метка вебинара по строке заявки: колонка «Эфир», иначе «Бесплатный вебинар 24 октября» → ig_web24."""
    if lead is None:
        return None
    webinars = series_webinars(webinars, webinar_series_of(lead, webinars))
    if marked := marked_webinar(lead, webinars):
        return marked
    # Сначала выбранный в форме пункт: имя формы одно на обе даты («Вебинар композиция 24.10»),
    # и Светлана Летохо, выбравшая 31 октября, попадала на 24-е (18.10.2026).
    for source in (lead.interest, lead.form_name):
        text = str(source or "").casefold()
        for webinar in webinars:
            short = webinar.at.astimezone(MINSK).strftime("%d.%m")
            if (webinar.date and webinar.date.casefold() in text) or short in text:
                # Форма ещё предлагает прошедшую дату: выбрал её после начала эфира — это ближайший.
                if lead.created is not None and webinar.at <= lead.created:
                    later = next_webinar(webinars, lead.created)
                    return later.tag if later else webinar.tag
                return webinar.tag
    return None


def person_webinar(opened: Opened, leads_by_id: dict[str, Lead], webinars: list[Webinar]) -> str:
    """Вебинар человека, открывшего бота: колонка «Эфир» администратора, выбор кнопкой в боте, пункт формы,
    метка /start."""
    lead = leads_by_id.get(opened.lead_id or "")
    if lead is not None and webinar_series_of(lead, webinars) != opened.series:
        lead = None  # строка другой серии к этой записи не относится
    mine = series_webinars(webinars, opened.series)
    if marked := marked_webinar(lead, mine):
        return marked
    if opened.webinar and any(w.tag == opened.webinar for w in mine):
        return opened.webinar
    return lead_webinar(lead, webinars) or opened.tag


def card_text(
    opened: Opened | None, leads: list[Lead], webinars: list[Webinar], *, username: str | None, name: str,
) -> str:
    """Кто это в таблице заявок — для темы обращения администратору: Кирилл не понимал, кто «Svetik»
    (#A-47, это Светлана Самохвал; 22.10.2026). Имя из формы, прямые контакты, вебинар, пометки."""
    by_id = {lead.lead_id: lead for lead in leads}
    series = opened.series if opened else ""
    if opened is not None:
        leads = [x for x in leads if webinar_series_of(x, webinars) == series]
    lead = (by_id.get(opened.lead_id or "") if opened else None) or match_lead(
        leads,
        username=(opened.username if opened else None) or username,
        name=(opened.name if opened else "") or name,
        opened_at=opened.opened_at if opened else None,
    )
    if opened is not None:
        tag = person_webinar(replace(opened, lead_id=lead.lead_id) if lead else opened, by_id, webinars)
    else:
        tag = lead_webinar(lead, webinars)
    webinar = next((w for w in webinars if w.tag == tag), None)
    when = f" · вебинар {webinar.date}" if webinar else ""
    if lead is None:
        return f"📋 В таблице заявок не найдено{when}"
    lines = [f"📋 В таблице: <b>{_plain(lead.name) or 'без имени'}</b>{when}"]
    if lead.phone:
        lines.append(f"📞 {lead.phone}")
        if contacts := _contacts(lead.phone):
            lines.append(contacts)
    if lead.status:
        lines.append(f"📝 {_plain(lead.status)}")
    return "\n".join(lines)


async def lead_card(tenant_id: str, showcase: Any, *, telegram_user_id: int, username: str | None, name: str) -> str:
    """``card_text`` по таблице и записям «открыл бота»; таблицы нет — пусто, не открылась — одна строка."""
    from app.db import tenant_connection

    config = sheet_config(showcase.support)
    if config is None:
        return ""
    try:
        leads = await fetch_leads(config)
    except Exception as exc:
        logger.warning("lead_card_sheet_failed", extra={"showcase": showcase.showcase_id, "error": type(exc).__name__})
        return "📋 Таблица заявок сейчас не открылась — найдите человека в ней вручную."
    async with tenant_connection(tenant_id) as conn:
        opens = await load_opens(conn, tenant_id, showcase.showcase_id)
    webinars = webinars_config(showcase.support)
    mine = [o for o in opens if o.telegram_user_id == int(telegram_user_id)]
    cards = [card_text(o, leads, webinars, username=username, name=name) for o in mine] or [
        card_text(None, leads, webinars, username=username, name=name)
    ]
    found = [c for c in cards if not c.startswith("📋 В таблице заявок не найдено")]
    return "\n\n".join(dict.fromkeys(found or cards[:1]))


# ---- подтверждение и перенос даты (Виктор, 18.10.2026) ------------------------------------------
# Ссылка «Спасибо» формы у всех ведёт на ig_web24, поэтому приветствие — без даты, а дату подтверждает
# отдельное сообщение, когда нашлась строка заявки (пункт формы). Кнопка — перенести на другую дату.


def _when(webinar: Webinar) -> str:
    return f"{webinar.date}, начало в {webinar.at.astimezone(MINSK).strftime('%H:%M')} по Минску"


def confirm_text(webinar: Webinar) -> str:
    return f"Вы записаны на {_when(webinar)}."


def moved_text(webinar: Webinar) -> str:
    return f"Готово: вы записаны на {_when(webinar)}."


def correction_text(webinar: Webinar) -> str:
    return f"Поправка: вы записаны на {_when(webinar)}."


def move_markup(webinars: list[Webinar], current: str, now: datetime) -> dict[str, Any] | None:
    """«Перенести на …» — на каждый другой вебинар той же серии, который ещё не начался."""
    series = next((w.series for w in webinars if w.tag == current), "")
    webinars = series_webinars(webinars, series)
    rows = [
        [{"text": f"Перенести на {w.date}", "callback_data": f"wbn:{w.tag}"}]
        for w in sorted(webinars, key=lambda item: item.at)
        if w.tag != current and w.at > now
    ]
    return {"inline_keyboard": rows} if rows else None


async def move_webinar(
    tenant_id: str, showcase: Any, telegram_user_id: int, tag: str, *, now: datetime | None = None,
) -> tuple[Webinar, Opened] | None:
    """Кнопка «Перенести на …»: выбор в боте записывается и важнее пункта формы. None — нет такого
    вебинара, он уже начался или человек не открывал бота по ссылке с рекламы."""
    from app.db import fetch_one, tenant_connection

    moment = now or datetime.now(timezone.utc)
    webinars = webinars_config(showcase.support)
    webinar = next((w for w in webinars if w.tag == tag), None)
    if webinar is None:
        return None
    if webinar.at <= moment:
        # Старая кнопка на прошедший эфир — записываем на ближайший той же серии (бот скажет, что дата прошла).
        webinar = next_webinar(series_webinars(webinars, webinar.series), moment)
        if webinar is None:
            return None
        tag = webinar.tag
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update academy_ad_opens set webinar = %s
            where tenant_id = %s and showcase_id = %s and telegram_user_id = %s and series = %s
            returning telegram_user_id, telegram_username, name, lead_id, opened_at, tag, webinar, series
            """,
            (tag, tenant_id, showcase.showcase_id, int(telegram_user_id), webinar.series),
        )
    if not row:
        return None
    opened = Opened(
        int(row["telegram_user_id"]), row["telegram_username"], str(row["name"] or ""), row["lead_id"],
        row["opened_at"], str(row["tag"] or ""), row["webinar"], str(row.get("series") or ""),
    )
    return webinar, opened


def call_list_texts(webinar: Webinar, leads: list[Lead], head: str | None = None, *, bot_url: str = "") -> list[str]:
    """«Не в боте — позвонить или написать до 17:30 (N человек)» и по человеку: имя, телефон, ссылки.
    ``bot_url`` — личная ссылка на бота с id заявки (19.10.2026): открыв её, человек сам находит свою строку."""
    time = webinar.at.astimezone(MINSK).strftime("%H:%M")
    head = head or f"📞 Не в боте — позвонить или написать до {time} ({len(leads)} чел.) · вебинар {webinar.date}"
    texts, current = [], head
    for number, lead in enumerate(leads, start=1):
        lines = [f"{number}. <b>{_plain(lead.name) or 'Без имени'}</b>"]
        if lead.phone:
            lines.append(f"📞 {lead.phone}")
            if contacts := _contacts(lead.phone):
                lines.append(contacts)
        if lead.username:
            lines.append(f"✈️ @{lead.username}")
        if bot_url:
            lines.append(f"🔗 Личная ссылка на бота: {bot_url}?start={webinar.tag}_{lead.lead_id}")
        block = "\n".join(lines)
        if len(current) + len(block) + 2 > MESSAGE_LIMIT:
            texts.append(current)
            current = f"{head} — продолжение"
        current += "\n\n" + block
    texts.append(current)
    return texts


async def showcase_bot_url(showcase: Any) -> str:
    """``https://t.me/<бот витрины>``, как в публичных настройках; бота не нашли — пусто (списки без личных ссылок)."""
    from app.academy.showcases import binding_username

    try:
        # Только бот витрины: запасной бот процесса (WWC) для личной ссылки ALMIRA неверен.
        username = await binding_username(showcase.telegram_binding_id)
    except Exception:
        logger.warning("webinar_bot_url_failed", exc_info=True)
        return ""
    return f"https://t.me/{username}" if username else ""


async def not_in_bot_texts(tenant_id: str, showcase: Any, *, now: datetime | None = None) -> list[str]:
    """Администратору по запросу «не в боте»: по каждому предстоящему вебинару — сколько заявок в боте
    и кто нет (телефон, WhatsApp, Telegram по номеру). Kirill, 18.10.2026: «проверить, кто зарегился»."""
    from app.db import tenant_connection

    moment = now or datetime.now(timezone.utc)
    webinars = sorted((w for w in webinars_config(showcase.support) if w.at > moment), key=lambda w: w.at)
    config = sheet_config(showcase.support)
    if not webinars or not config:
        return ["Нет предстоящих вебинаров или таблицы заявок."]
    leads = await fetch_leads(config)
    async with tenant_connection(tenant_id) as conn:
        opens = await load_opens(conn, tenant_id, showcase.showcase_id)
    texts: list[str] = []
    bot_url: str | None = None
    for webinar in webinars:
        mine = [lead for lead in leads if lead_webinar(lead, webinars) == webinar.tag]
        missing = [lead for lead in mine if opened_for(lead, opens, webinar.series) is None]
        if not missing:
            texts.append(f"Вебинар {webinar.date}: все {len(mine)} заявок — в боте.")
            continue
        head = (
            f"Вебинар {webinar.date}: в боте {len(mine) - len(missing)} из {len(mine)} заявок. "
            f"Не в боте — {len(missing)} чел.:"
        )
        if bot_url is None:
            bot_url = await showcase_bot_url(showcase)
        texts.extend(call_list_texts(webinar, missing, head, bot_url=bot_url))
    return texts


# Таблица для подтверждений читается не чаще раза в минуту (шаг воркера идёт раз в 30 с).
CONFIRM_WITHIN = timedelta(minutes=10)
CONFIRM_FETCH_SEC = 60.0
_last_confirm_fetch: dict[str, datetime] = {}


def confirm_key(showcase_id: str, telegram_user_id: int, series: str = "") -> str:
    if series:
        return f"webinar:{showcase_id}:{series}:confirm:{int(telegram_user_id)}"
    return f"webinar:{showcase_id}:confirm:{int(telegram_user_id)}"


async def webinar_step(tenant_ids: set[str], bindings: dict[str, Any], *, clock: datetime | None = None) -> int:
    """Подтверждения даты, напоминания и списки для администратора, время которых пришло.
    Возвращает число новых сообщений."""
    from app.academy.ad_leads import BOT_TAG, match_lead
    from app.academy.showcases import load_showcases
    from app.db import fetch_all, tenant_connection
    from app.jobs.outbox import enqueue_outbox_event
    from app.telegram.showcase_support import KIND_ADMIN, support_targets

    now = clock or datetime.now(timezone.utc)
    queued = 0
    for tenant_id in sorted(tenant_ids):
        for showcase in await load_showcases(tenant_id):
            webinars = webinars_config(showcase.support)
            binding_id = showcase.telegram_binding_id
            if not webinars or not binding_id or binding_id not in bindings:
                continue
            # Что пора делать по расписанию — считается без базы.
            warmups = warmup_config(showcase.support)
            work = [
                (webinar, due_messages(webinar, warmups, now),
                 not webinar.url and _in(webinar.at - NO_LINK_WARNING, now, webinar.at - timedelta(hours=1)),
                 _in(webinar.at - CALL_LIST, now, webinar.at))
                for webinar in webinars
            ]
            work = [item for item in work if item[1] or item[2] or item[3]]
            follow = [
                w for w in webinars
                if w.followup_at and w.followup_to and _in(w.followup_at, now, w.followup_at + FOLLOWUP_WINDOW)
            ]

            async def enqueue(
                conn: Any, key: str, chat_id: int, text: str, markup: dict[str, Any] | None = None,
                photos: tuple[str, ...] = (),
            ) -> int:
                payload: dict[str, Any] = {"chat_id": str(int(chat_id)), "text": text, "binding_id": binding_id}
                if markup:
                    payload["reply_markup"] = markup
                if photos:
                    payload["photos"] = list(photos)
                row = await enqueue_outbox_event(
                    conn, tenant_id=tenant_id, event_type=EVENT, idempotency_key=key, payload=payload, due_at=now,
                )
                return 1 if row and row.get("created") else 0

            copies = copy_recipients(showcase.support)

            async def copy(
                conn: Any, key: str, webinar: Webinar, kind: str, sent: int, text: str,
                markup: dict[str, Any] | None = None, photos: tuple[str, ...] = (),
            ) -> int:
                """Копия рассылки — один раз, когда она ушла хоть кому-то."""
                if not sent:
                    return 0
                head = copy_header(webinar, kind, sent)
                return sum([await enqueue(conn, f"{key}:copy:{uid}", uid, head + text, markup, photos) for uid in copies])

            async with tenant_connection(tenant_id) as conn:
                opens = await load_opens(conn, tenant_id, showcase.showcase_id)
                # Открыли бота за последние 10 минут и подтверждения ещё не было.
                recent = [o for o in opens if o.opened_at is not None and now - CONFIRM_WITHIN <= o.opened_at <= now]
                if recent:
                    done = {
                        r["idempotency_key"] for r in await fetch_all(
                            conn, "select idempotency_key from platform_outbox where idempotency_key = any(%s)",
                            ([confirm_key(showcase.showcase_id, o.telegram_user_id, o.series) for o in recent],),
                        )
                    }
                    recent = [
                        o for o in recent if confirm_key(showcase.showcase_id, o.telegram_user_id, o.series) not in done
                    ]
                fetch_key = f"{tenant_id}:{showcase.showcase_id}"
                last = _last_confirm_fetch.get(fetch_key)
                confirm_now = bool(recent) and (last is None or (now - last).total_seconds() >= CONFIRM_FETCH_SEC)
                if not work and not confirm_now and not follow:
                    continue
                config = sheet_config(showcase.support)
                leads: list[Lead] = []
                if config:
                    try:
                        leads = await fetch_leads(config)
                    except Exception as exc:
                        logger.warning("webinar_sheet_failed", extra={"showcase": showcase.showcase_id, "error": type(exc).__name__})
                if confirm_now:
                    _last_confirm_fetch[fetch_key] = now
                leads_by_id = {lead.lead_id: lead for lead in leads}
                lead_series = {lead.lead_id: webinar_series_of(lead, webinars) for lead in leads}

                # Вошёл в бота, а строка не нашлась в первые 10 минут (форма позже; вход без метки до 19.10 —
                # Павел Бацяновский, 20.10) — перед каждой рассылкой связываем по нику или имени с фамилией.
                # Строка — только своей серии: композиция и анатомия — разные заявки (V40, 23.10.2026).
                taken = {o.lead_id for o in opens if o.lead_id}
                for i, opened in enumerate(opens):
                    if opened.lead_id or not leads:
                        continue
                    lead = match_lead(
                        [x for x in leads if x.lead_id not in taken and lead_series[x.lead_id] == opened.series],
                        username=opened.username, name=opened.name, opened_at=opened.opened_at,
                    )
                    if lead is None:
                        continue
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update academy_ad_opens set lead_id = %s where tenant_id = %s and showcase_id = %s"
                            " and telegram_user_id = %s and series = %s and lead_id is null",
                            (lead.lead_id, tenant_id, showcase.showcase_id, opened.telegram_user_id, opened.series),
                        )
                    opens[i] = replace(opened, lead_id=lead.lead_id)
                    taken.add(lead.lead_id)

                # Человек уже в боте (запись другой серии), а заявку оставил и в этой — заводим ему запись серии,
                # по нику или имени с фамилией: записан и на композицию, и на анатомию — получает обе ветки.
                for series in sorted({w.series for w in webinars}):
                    present = {o.telegram_user_id for o in opens if o.series == series}
                    for lead in leads:
                        if lead.lead_id in taken or lead_series[lead.lead_id] != series:
                            continue
                        other = next(
                            (o for o in opens if o.telegram_user_id not in present
                             and match_lead([lead], username=o.username, name=o.name) is not None),
                            None,
                        )
                        if other is None:
                            continue
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username,"
                                " name, tag, lead_id, opened_at, series) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                                " on conflict do nothing",
                                (tenant_id, showcase.showcase_id, other.telegram_user_id, other.username, other.name,
                                 BOT_TAG, lead.lead_id, other.opened_at, series),
                            )
                        opens.append(Opened(
                            other.telegram_user_id, other.username, other.name, lead.lead_id, other.opened_at,
                            BOT_TAG, None, series,
                        ))
                        present.add(other.telegram_user_id)
                        taken.add(lead.lead_id)

                # Подтверждение даты: строка заявки нашлась — «Вы записаны на …» с кнопкой переноса.
                for opened in recent if confirm_now else []:
                    if opened.webinar:
                        continue  # уже выбрал в боте
                    lead = leads_by_id.get(opened.lead_id or "") or match_lead(
                        [x for x in leads if lead_series[x.lead_id] == opened.series],
                        username=opened.username, name=opened.name, opened_at=opened.opened_at,
                    )
                    tag = lead_webinar(lead, webinars)
                    webinar = next((w for w in webinars if w.tag == tag), None)
                    if lead is None or webinar is None or webinar.at <= now:
                        continue  # строки пока нет — попробуем на следующем шаге, пока не прошло 10 минут
                    if not opened.lead_id:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "update academy_ad_opens set lead_id = %s where tenant_id = %s and showcase_id = %s"
                                " and telegram_user_id = %s and series = %s and lead_id is null",
                                (lead.lead_id, tenant_id, showcase.showcase_id, opened.telegram_user_id, opened.series),
                            )
                    queued += await enqueue(
                        conn, confirm_key(showcase.showcase_id, opened.telegram_user_id, opened.series),
                        opened.telegram_user_id, confirm_text(webinar), move_markup(webinars, webinar.tag, now),
                    )

                # После эфира — одно приглашение на повтор записанным, кто не оплатил (колонка «Статус»).
                for webinar in follow:
                    repeat = next((w for w in webinars if w.tag == webinar.followup_to and w.at > now), None)
                    if repeat is None:
                        continue
                    sent = 0
                    for opened in opens:
                        if opened.series != webinar.series or person_webinar(opened, leads_by_id, webinars) != webinar.tag:
                            continue
                        lead = leads_by_id.get(opened.lead_id or "")
                        if is_paid(lead):
                            continue
                        sent += await enqueue(
                            conn, f"webinar:{showcase.showcase_id}:{webinar.tag}:followup:{opened.telegram_user_id}",
                            opened.telegram_user_id, followup_text(webinar, repeat), followup_markup(repeat),
                        )
                    queued += sent
                    queued += await copy(
                        conn, f"webinar:{showcase.showcase_id}:{webinar.tag}:followup", webinar, "followup", sent,
                        followup_text(webinar, repeat), followup_markup(repeat),
                    )
                    if sent:
                        logger.info("webinar_followup_queued", extra={"webinar": webinar.tag, "queued": sent})

                if not work:
                    continue
                admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)
                bot_url: str | None = None  # бот витрины для личных ссылок в списке — ищем раз и только по делу
                for webinar, reminders, warn, calls in work:
                    prefix = f"webinar:{showcase.showcase_id}:{webinar.tag}"
                    audience = [
                        o for o in opens
                        if o.series == webinar.series and person_webinar(o, leads_by_id, webinars) == webinar.tag
                    ]
                    same = {w.tag for w in webinars if w.series == webinar.series}
                    for kind, moment, text, markup, photos in reminders:
                        sent = 0
                        got: set[str] = set()
                        if kind.startswith("warm_"):
                            # Перенёс запись с 24 на 31 — тот же прогрев второй раз не получает.
                            got = {
                                str(r["idempotency_key"]).rsplit(":", 1)[-1] for r in await fetch_all(
                                    conn, "select idempotency_key from platform_outbox where idempotency_key like %s",
                                    (f"webinar:{showcase.showcase_id}:%:{kind}:%",),
                                )
                                if str(r["idempotency_key"]).split(":")[2] in same  # тот же прогрев той же серии
                            }
                        for opened in audience:
                            if opened.opened_at is not None and opened.opened_at > moment:
                                continue  # открыл бота позже — это сообщение ему уже не нужно
                            if str(opened.telegram_user_id) in got:
                                continue
                            if kind.startswith("post_") and is_paid(leads_by_id.get(opened.lead_id or "")):
                                continue  # предложение после эфира оплатившим не шлём
                            sent += await enqueue(
                                conn, f"{prefix}:{kind}:{opened.telegram_user_id}", opened.telegram_user_id, text, markup,
                                photos,
                            )
                        queued += sent
                        queued += await copy(conn, f"{prefix}:{kind}", webinar, kind, sent, text, markup, photos)
                        if sent:
                            logger.info("webinar_reminders_queued", extra={"webinar": webinar.tag, "kind": kind, "queued": sent})
                    if admin is None:
                        continue
                    if warn:
                        queued += await enqueue(
                            conn, f"{prefix}:no_link", admin,
                            f"⚠️ Вебинар {webinar.date}: нет ссылки на эфир — напоминания со ссылкой не уйдут.",
                        )
                    if calls:
                        missing = [
                            lead for lead in leads
                            if lead_webinar(lead, webinars) == webinar.tag
                            and opened_for(lead, opens, webinar.series) is None
                        ]
                        if missing:
                            if bot_url is None:
                                bot_url = await showcase_bot_url(showcase)
                            for index, text in enumerate(call_list_texts(webinar, missing, bot_url=bot_url)):
                                queued += await enqueue(conn, f"{prefix}:calls:{index}", admin, text)
    return queued
