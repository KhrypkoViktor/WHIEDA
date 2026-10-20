"""Подарок за эфир (Виктор и Александра, 20.10.2026): в середине вебинара Александра называет кодовое
слово, человек пишет его боту витрины — бот открывает подарочный курс (первый урок интенсива) на 7 дней.

Настройка — ``academy_showcases.support.webinar_gift``::

    {"word": "мастерство", "course": "<slug подарочного курса>", "days": 7}

Слово принимается в день эфира — от начала до полуночи по Минску (вебинары — ``support.webinars``).
Доступ — строка ``academy_access`` с ``source = 'gift'`` (самостоятельно, задания без проверки) и
``payment_ref = 'code:<метка вебинара>[,<метка>…]'`` — заодно факт «был на эфире». Подарочный курс
(``kind = 'gift'``) в каталоге виден только тем, кому открыт.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from app.academy.ad_leads import MINSK
from app.academy.webinars import Webinar
from app.db import fetch_one, tenant_connection

GIFT_KIND = "gift"
GIFT_BUTTON = "🎨 Открыть урок"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_NOT_WORD = re.compile(r"[^0-9a-zа-я]+")
_EXTRA_WORDS = 4  # «кодовое слово мастерство», «мастерство!» — да; длинное сообщение со словом — нет
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@dataclass(frozen=True)
class Gift:
    word: str
    course: str
    days: int


@dataclass(frozen=True)
class Granted:
    title: str
    expires_at: datetime | None  # None — открыт бессрочно (выдан вручную)
    already: bool  # открыт раньше и ещё действует — срок не продлеваем


def _words(text: Any) -> list[str]:
    return _NOT_WORD.sub(" ", str(text or "").casefold().replace("ё", "е")).split()


def gift_config(support: dict[str, Any] | None) -> Gift | None:
    raw = (support or {}).get("webinar_gift")
    if not isinstance(raw, dict):
        return None
    word = " ".join(_words(raw.get("word")))
    course = str(raw.get("course") or "")
    try:
        days = 7 if raw.get("days") is None else int(raw["days"])
    except (TypeError, ValueError):
        return None
    if not word or not _SLUG_RE.fullmatch(course) or not 1 <= days <= 60:
        return None
    return Gift(word=word, course=course, days=days)


def is_code_word(text: Any, gift: Gift) -> bool:
    words, want = _words(text), gift.word.split()
    if not words or len(words) > len(want) + _EXTRA_WORDS:
        return False
    return any(words[i:i + len(want)] == want for i in range(len(words) - len(want) + 1))


def _day_end(webinar: Webinar) -> datetime:
    local = webinar.at.astimezone(MINSK)
    return datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=MINSK)


def gift_webinar(webinars: list[Webinar], now: datetime) -> Webinar | None:
    """Вебинар, в день которого слово сейчас действует: от начала эфира до полуночи по Минску."""
    return next((w for w in sorted(webinars, key=lambda w: w.at) if w.at <= now < _day_end(w)), None)


def _date(moment: datetime) -> str:
    local = moment.astimezone(MINSK)
    return f"{local.day} {_MONTHS[local.month - 1]}"


def gift_text(granted: Granted) -> str:
    until = f" — до {_date(granted.expires_at)}" if granted.expires_at else ""
    if granted.already:
        return f"🎁 «{granted.title}» уже открыт вам{until}."
    return (
        f"🎁 Готово! Открыли вам «{granted.title}»{until}.\n\n"
        "Задания в уроке можно выполнить для себя. Разбор работ Александрой — для участников интенсива."
    )


def gift_closed_text(webinars: list[Webinar], now: datetime) -> str:
    """Слово написали не в день эфира."""
    upcoming = sorted((w for w in webinars if w.at > now), key=lambda w: w.at)
    started = any(w.at <= now for w in webinars)
    if upcoming:
        nxt = upcoming[0]
        when = f"{nxt.date}, с {nxt.at.astimezone(MINSK).strftime('%H:%M')} до полуночи"
        if started:
            return f"Кодовое слово действует в день эфира до полуночи. Следующий эфир — {when}: слово сработает и там."
        return f"Кодовое слово назовут на эфире. Напишите его сюда в день эфира — {when}."
    return "Кодовое слово действовало в день эфира до полуночи — срок закончился."


async def grant_gift(
    tenant_id: str, gift: Gift, telegram_user_id: int, webinar_tag: str, now: datetime
) -> Granted | None:
    """Открыть подарочный курс на ``gift.days`` дней; действующий доступ не продлеваем, а метку
    вебинара дописываем. None — курса нет (не создан или снят с публикации)."""
    async with tenant_connection(tenant_id) as conn:
        course = await fetch_one(
            conn,
            "select course_id::text as course_id, title from academy_courses"
            " where tenant_id = %s and slug = %s and status = 'published'",
            (tenant_id, gift.course),
        )
        if not course:
            return None
        before = await fetch_one(
            conn,
            "select expires_at from academy_access where tenant_id = %s and course_id = %s::uuid"
            " and telegram_user_id = %s and revoked_at is null and (expires_at is null or expires_at > %s)",
            (tenant_id, course["course_id"], int(telegram_user_id), now),
        )
        row = await fetch_one(
            conn,
            """
            insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref, expires_at)
            values (%(t)s, %(c)s::uuid, %(u)s, 'gift', %(ref)s, %(exp)s)
            on conflict (tenant_id, course_id, telegram_user_id) do update set
              payment_ref = case
                when academy_access.payment_ref like 'code:%%'
                     and not (%(tag)s = any(string_to_array(substr(academy_access.payment_ref, 6), ',')))
                  then academy_access.payment_ref || ',' || %(tag)s
                else academy_access.payment_ref end,
              expires_at = case
                when academy_access.revoked_at is null
                     and (academy_access.expires_at is null or academy_access.expires_at > %(now)s)
                  then academy_access.expires_at
                else excluded.expires_at end,
              revoked_at = null
            returning expires_at
            """,
            {
                "t": tenant_id, "c": course["course_id"], "u": int(telegram_user_id), "tag": webinar_tag,
                "ref": f"code:{webinar_tag}", "exp": now + timedelta(days=gift.days), "now": now,
            },
        )
    return Granted(title=str(course["title"]), expires_at=(row or {}).get("expires_at"), already=before is not None)
