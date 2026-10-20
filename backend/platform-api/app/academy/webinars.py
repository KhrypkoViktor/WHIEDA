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
from dataclasses import dataclass
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


def warmup_config(support: dict[str, Any] | None) -> list[Warmup]:
    raw = (support or {}).get("webinar_warmup")
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key, text = str(item.get("key") or ""), str(item.get("text") or "").strip()
        try:
            minutes = int(item.get("before_min"))
        except (TypeError, ValueError):
            continue
        if _WARMUP_KEY_RE.fullmatch(key) and text and 10 <= minutes <= 14 * 24 * 60:
            out.append(Warmup(key, timedelta(minutes=minutes), text))
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
            f"Завтра, {webinar.date}, в {time} по Минску — бесплатный вебинар Александры Пармон по композиции.\n\n"
            "Ссылку на эфир пришлём сюда, в этот чат."
        )
    if kind == "two_hours":
        return f"Сегодня в {time} по Минску — вебинар по композиции с Александрой Пармон. Ссылку пришлём за час до начала."
    if kind == "hour":
        return f"Через час начинаем. Ссылка на эфир: {webinar.url}\n\nНачало в {time} по Минску."
    return f"Начинаем через 5 минут. Заходите: {webinar.url}"


def _link_markup(webinar: Webinar) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Перейти к эфиру", "url": webinar.url}]]}


def due_messages(
    webinar: Webinar, warmups: list[Warmup], now: datetime,
) -> list[tuple[str, datetime, str, dict[str, Any] | None]]:
    """Что пора отправить записанным на вебинар: прогрев и напоминания — (ключ, время, текст, кнопки)."""
    out: list[tuple[str, datetime, str, dict[str, Any] | None]] = []
    for item in warmups:
        moment = webinar.at - item.before
        if moment <= now < min(moment + WARMUP_WINDOW, webinar.at):
            out.append((f"warm_{item.key}", moment, warmup_text(item, webinar), None))
    for kind, moment in due_reminders(webinar, now):
        out.append((kind, moment, reminder_text(kind, webinar), _link_markup(webinar) if kind in ("hour", "five") else None))
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


def lead_webinar(lead: Lead | None, webinars: list[Webinar]) -> str | None:
    """Метка вебинара по строке заявки: «Бесплатный вебинар 24 октября» → ig_web24."""
    if lead is None:
        return None
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
    """Вебинар человека, открывшего бота: выбор кнопкой в боте, иначе пункт формы, иначе метка /start."""
    if opened.webinar and any(w.tag == opened.webinar for w in webinars):
        return opened.webinar
    return lead_webinar(leads_by_id.get(opened.lead_id or ""), webinars) or opened.tag


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
    """«Перенести на …» — на каждый другой вебинар, который ещё не начался."""
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
        # Старая кнопка на прошедший эфир — записываем на ближайший (бот скажет, что дата прошла).
        webinar = next_webinar(webinars, moment)
        if webinar is None:
            return None
        tag = webinar.tag
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update academy_ad_opens set webinar = %s
            where tenant_id = %s and showcase_id = %s and telegram_user_id = %s
            returning telegram_user_id, telegram_username, name, lead_id, opened_at, tag, webinar
            """,
            (tag, tenant_id, showcase.showcase_id, int(telegram_user_id)),
        )
    if not row:
        return None
    opened = Opened(
        int(row["telegram_user_id"]), row["telegram_username"], str(row["name"] or ""), row["lead_id"],
        row["opened_at"], str(row["tag"] or ""), row["webinar"],
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
        missing = [lead for lead in mine if opened_for(lead, opens) is None]
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


def confirm_key(showcase_id: str, telegram_user_id: int) -> str:
    return f"webinar:{showcase_id}:confirm:{int(telegram_user_id)}"


async def webinar_step(tenant_ids: set[str], bindings: dict[str, Any], *, clock: datetime | None = None) -> int:
    """Подтверждения даты, напоминания и списки для администратора, время которых пришло.
    Возвращает число новых сообщений."""
    from app.academy.ad_leads import match_lead
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

            async def enqueue(conn: Any, key: str, chat_id: int, text: str, markup: dict[str, Any] | None = None) -> int:
                payload: dict[str, Any] = {"chat_id": str(int(chat_id)), "text": text, "binding_id": binding_id}
                if markup:
                    payload["reply_markup"] = markup
                row = await enqueue_outbox_event(
                    conn, tenant_id=tenant_id, event_type=EVENT, idempotency_key=key, payload=payload, due_at=now,
                )
                return 1 if row and row.get("created") else 0

            async with tenant_connection(tenant_id) as conn:
                opens = await load_opens(conn, tenant_id, showcase.showcase_id)
                # Открыли бота за последние 10 минут и подтверждения ещё не было.
                recent = [o for o in opens if o.opened_at is not None and now - CONFIRM_WITHIN <= o.opened_at <= now]
                if recent:
                    done = {
                        r["idempotency_key"] for r in await fetch_all(
                            conn, "select idempotency_key from platform_outbox where idempotency_key = any(%s)",
                            ([confirm_key(showcase.showcase_id, o.telegram_user_id) for o in recent],),
                        )
                    }
                    recent = [o for o in recent if confirm_key(showcase.showcase_id, o.telegram_user_id) not in done]
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

                # Подтверждение даты: строка заявки нашлась — «Вы записаны на …» с кнопкой переноса.
                for opened in recent if confirm_now else []:
                    if opened.webinar:
                        continue  # уже выбрал в боте
                    lead = leads_by_id.get(opened.lead_id or "") or match_lead(
                        leads, username=opened.username, name=opened.name, opened_at=opened.opened_at,
                    )
                    tag = lead_webinar(lead, webinars)
                    webinar = next((w for w in webinars if w.tag == tag), None)
                    if lead is None or webinar is None or webinar.at <= now:
                        continue  # строки пока нет — попробуем на следующем шаге, пока не прошло 10 минут
                    if not opened.lead_id:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "update academy_ad_opens set lead_id = %s where tenant_id = %s and showcase_id = %s"
                                " and telegram_user_id = %s and lead_id is null",
                                (lead.lead_id, tenant_id, showcase.showcase_id, opened.telegram_user_id),
                            )
                    queued += await enqueue(
                        conn, confirm_key(showcase.showcase_id, opened.telegram_user_id), opened.telegram_user_id,
                        confirm_text(webinar), move_markup(webinars, webinar.tag, now),
                    )

                # После эфира — одно приглашение на повтор записанным, кто не оплатил (колонка «Статус»).
                for webinar in follow:
                    repeat = next((w for w in webinars if w.tag == webinar.followup_to and w.at > now), None)
                    if repeat is None:
                        continue
                    sent = 0
                    for opened in opens:
                        if person_webinar(opened, leads_by_id, webinars) != webinar.tag:
                            continue
                        lead = leads_by_id.get(opened.lead_id or "")
                        if is_paid(lead):
                            continue
                        sent += await enqueue(
                            conn, f"webinar:{showcase.showcase_id}:{webinar.tag}:followup:{opened.telegram_user_id}",
                            opened.telegram_user_id, followup_text(webinar, repeat), followup_markup(repeat),
                        )
                    queued += sent
                    if sent:
                        logger.info("webinar_followup_queued", extra={"webinar": webinar.tag, "queued": sent})

                if not work:
                    continue
                admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)
                bot_url: str | None = None  # бот витрины для личных ссылок в списке — ищем раз и только по делу
                for webinar, reminders, warn, calls in work:
                    prefix = f"webinar:{showcase.showcase_id}:{webinar.tag}"
                    audience = [o for o in opens if person_webinar(o, leads_by_id, webinars) == webinar.tag]
                    for kind, moment, text, markup in reminders:
                        sent = 0
                        for opened in audience:
                            if opened.opened_at is not None and opened.opened_at > moment:
                                continue  # открыл бота позже — это сообщение ему уже не нужно
                            sent += await enqueue(
                                conn, f"{prefix}:{kind}:{opened.telegram_user_id}", opened.telegram_user_id, text, markup,
                            )
                        queued += sent
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
                            if lead_webinar(lead, webinars) == webinar.tag and opened_for(lead, opens) is None
                        ]
                        if missing:
                            if bot_url is None:
                                bot_url = await showcase_bot_url(showcase)
                            for index, text in enumerate(call_list_texts(webinar, missing, bot_url=bot_url)):
                                queued += await enqueue(conn, f"{prefix}:calls:{index}", admin, text)
    return queued
