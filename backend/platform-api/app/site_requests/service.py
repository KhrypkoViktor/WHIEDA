"""Tenant-scoped collection and approval of new partner site requests."""

from __future__ import annotations

import json
from contextlib import nullcontext
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.site_requests.contacts import parse_contacts
from app.db import fetch_one, tenant_connection
from app.subscriptions.pricing import PaymentLine
from app.subscriptions.service import (
    SubscriptionError,
    normalize_partner_subdomain,
    record_payment_lines_in_connection,
)
from app.theme_access.service import ISSUED_SUBDOMAIN_TO_REF

logger = logging.getLogger(__name__)

# Прайс владельца 11.11.2026 (единый для всех, ₽; страница цен сайта). Это единственное место с ценами воронки
# «Заказать сайт»: кнопки, тексты оплаты и ledger берут суммы отсюда. Значения — в рублях целиком.
SITE_SETUP_RUB = 3_000                        # подключение сайта, один раз
# Сайт + клуб и сайт + курс — подключение бесплатно на любой срок (владелец 11.11.2026): строки site_setup нет.
SITE_TERM_RUB = {3: 3_000, 6: 5_400, 12: 9_600}  # сайт на N месяцев
CLUB_MONTH_RUB = 4_000                        # клуб, в месяц
COURSE_RUB = 10_000                           # курс онлайн-запуска; участникам клуба 7 500 (в боте не применяется)
TERMS = (3, 6, 12)
# Курс Академии, который открывает вариант «сайт + курс» (владелец: «онлайн-запуск: нейросети для партнёра,
# контент-завод, SMM, возражения и продажи»). Слаг — курс первого потока «WWC : AI \ SMM» (course_groups.py).
COURSE_SLUG = "online-start-4w"
COURSE_PRODUCT = "course_academy"
# Акция ноября (владелец 11.11.2026): клуб продаётся от 3 месяцев (12 000 ₽), первый месяц потока уже прошёл —
# остаток 12 000 − 4 000 = 8 000 ₽ за 2 месяца; + сайт 3 мес 3 000; подключение бесплатно = 11 000 ₽.
# Кнопка — по 30.11.2026 включительно (МСК).
BUNDLE_RUB = 11_000
BUNDLE_LAST_DAY = date(2026, 11, 30)
MSK = timezone(timedelta(hours=3))

# plan_code: site_N | club_N | course_N | bundle; «site» — старый код открытых заявок (= site_N при N=3).
PLANS: dict[str, dict[str, Any]] = {}
for _n in TERMS:
    PLANS[f"site_{_n}"] = {
        "kind": "site", "months": _n,
        "lines": (("platform_subscription", SITE_TERM_RUB[_n], _n, False, ""), ("site_setup", SITE_SETUP_RUB, 0, False, "")),
    }
    PLANS[f"club_{_n}"] = {
        "kind": "club", "months": _n,
        "lines": (
            ("platform_subscription", SITE_TERM_RUB[_n], _n, False, ""),
            ("club_subscription", CLUB_MONTH_RUB * _n, _n, False, ""),
        ),
    }
    PLANS[f"course_{_n}"] = {
        "kind": "course", "months": _n,
        "lines": (
            ("platform_subscription", SITE_TERM_RUB[_n], _n, False, ""),
            (COURSE_PRODUCT, COURSE_RUB, 0, False, ""),
        ),
    }
PLANS["bundle"] = {
    "kind": "bundle", "months": 3,
    "lines": (
        ("platform_subscription", SITE_TERM_RUB[3], 3, False, ""),
        ("club_subscription", 8_000, 2, True, "акция ноября: клуб до конца потока, 2 из 3 месяцев"),
    ),
}
PLANS["site"] = PLANS["site_3"]
LEGACY_PLAN_CODES = {"site"}
RUB_PER_WWC = 100

# Колонка partner_site_requests.plan_code ограничена значениями ('site', 'bundle') (V10, check-ограничение).
# Миграций в этом срезе нет, поэтому в БД ложится «site»/«bundle», а точный вариант восстанавливается по
# сумме заявки (resolve_plan_code; суммы всех вариантов различаются). Расширите check — и db_plan_code
# начнёт писать полный код: достаточно добавить новые коды в DB_PLAN_CODES.
DB_PLAN_CODES = frozenset({"site", "bundle"})


def db_plan_code(plan_code: str) -> str:
    return plan_code if plan_code in DB_PLAN_CODES else "site"


def plan_lines(plan_code: str, currency: str) -> list[PaymentLine]:
    scale = RUB_PER_WWC if currency == "RUB" else 1
    return [
        PaymentLine(code, minor * scale, currency, months, promo, note)
        for code, minor, months, promo, note in PLANS[plan_code]["lines"]
    ]


def plan_total_minor(plan_code: str, currency: str) -> int:
    return sum(line.amount_minor for line in plan_lines(plan_code, currency))


def resolve_plan_code(stored_code: Any, total_minor: Any, currency: str = "RUB") -> str:
    """Точный вариант по записи заявки: код из БД + сумма. Старые заявки («site», «bundle») — как раньше."""
    stored = str(stored_code or "site")
    if stored in PLANS and stored not in LEGACY_PLAN_CODES and stored != "bundle":
        return stored
    if total_minor is None:
        return stored if stored in PLANS else "site"
    for code in PLANS:
        if code in LEGACY_PLAN_CODES or db_plan_code(code) != stored:
            continue
        if plan_total_minor(code, currency) == int(total_minor):
            return code
    raise SiteRequestError("Сумма заявки не совпадает с прайсом. Заявку нужно проверить вручную.")


def request_plan_code(request: dict[str, Any]) -> str:
    return resolve_plan_code(request.get("plan_code"), request.get("total_amount_minor"), str(request.get("currency") or "RUB"))


def plan_has_product(plan_code: str, product_code: str) -> bool:
    return any(line[0] == product_code for line in PLANS[plan_code]["lines"])


def bundle_available(now: datetime | None = None) -> bool:
    """Акция ноября показывается до 30.11.2026 включительно по Москве."""
    current = (now or datetime.now(timezone.utc)).astimezone(MSK)
    return current.date() <= BUNDLE_LAST_DAY


def _months_word(n: int) -> str:
    return f"{n} месяц" if n == 1 else f"{n} месяца" if n in (2, 3, 4) else f"{n} месяцев"


def _fmt_rub(minor: int) -> str:
    return f"{minor // 100:,}".replace(",", " ") + " ₽"


def plan_breakdown(plan_code: str, currency: str = "RUB") -> list[str]:
    """Состав пакета по строкам ledger: «сайт на 3 месяца — 3 000 ₽»."""
    names = {
        "platform_subscription": "сайт на {m}", "club_subscription": "клуб на {m}",
        COURSE_PRODUCT: "курс онлайн-запуска (нейросети, контент-завод, SMM, возражения и продажи)",
        "site_setup": "подключение сайта",
    }
    out = []
    for line in plan_lines(plan_code, currency):
        text = names[line.product_code].format(m=_months_word(line.access_months))
        if line.promo and line.product_code == "site_setup":
            text += " со скидкой"
        out.append(f"{text} — {_fmt_rub(line.amount_minor)}")
    if PLANS[plan_code]["kind"] in ("club", "course", "bundle"):
        out.append("подключение сайта — бесплатно")
    return out


def plan_summary(plan_code: str, currency: str = "RUB") -> str:
    head = "Акция ноября: " if plan_code == "bundle" else ""
    return head + "; ".join(plan_breakdown(plan_code, currency))


async def set_site_request_plan(tenant_id: str, actor_id: str, plan_code: str) -> dict[str, Any]:
    plan = str(plan_code or "").strip().lower()
    if plan not in PLANS:
        raise SiteRequestError("Выберите вариант кнопкой.")
    if plan == "bundle" and not bundle_available():
        raise SiteRequestError("Акция ноября закончилась. Выберите другой вариант.")
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            "select country_code from partner_site_requests where tenant_id = %s and actor_id = %s and status = 'awaiting_plan' for update",
            (tenant_id, actor_id),
        )
        if not request:
            raise SiteRequestError("Сейчас выбор пакета не ожидается.")
        currency = "RUB"  # только рубли на карту Т-Банк (09.10.2026)
        lines = plan_lines(plan, currency)
        subscription = next(l.amount_minor for l in lines if l.product_code == "platform_subscription")
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set plan_code = %s, total_amount_minor = %s, subscription_amount_minor = %s, currency = %s,
                status = 'awaiting_payment', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_plan'
            returning *
            """,
            (db_plan_code(plan), plan_total_minor(plan, currency), subscription, currency, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось сохранить выбор.")
    return row


class SiteRequestError(ValueError):
    pass


async def get_open_site_request(tenant_id: str, actor_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select * from partner_site_requests
            where tenant_id = %s and actor_id = %s
              and status not in ('provisioned', 'rejected', 'cancelled')
            order by created_at desc limit 1
            """,
            (tenant_id, actor_id),
        )


# Заявка, которая не двигалась столько времени, брошена: она больше не
# перехватывает обычные сообщения партнёра (30.09.2026: анкеты с 22.09 и
# продления с 12.09 отвечали на всё «напишите адрес» / «оплатите» и глотали
# фото). Ждущее вложение (фото анкеты, чек) она по-прежнему принимает.
STALE_AFTER = timedelta(days=3)


def request_is_stale(request: dict[str, Any], *, now: datetime | None = None) -> bool:
    updated = request.get("updated_at") or request.get("created_at")
    if not isinstance(updated, datetime):
        return False
    return (now or datetime.now(timezone.utc)) - updated > STALE_AFTER


async def begin_site_request(tenant_id: str, actor_id: str) -> dict[str, Any]:
    existing = await get_open_site_request(tenant_id, actor_id)
    if existing:
        # Вернулись к анкете кнопкой — она снова свежая.
        async with tenant_connection(tenant_id) as conn:
            touched = await fetch_one(
                conn,
                "update partner_site_requests set updated_at = now() where tenant_id = %s and request_id = %s returning *",
                (tenant_id, existing["request_id"]),
            )
        return touched or existing
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into partner_site_requests (tenant_id, actor_id, status, country_code)
            values (%s, %s, 'awaiting_name', 'RU')
            returning *
            """,
            (tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось создать заявку.")
    return row


async def set_site_request_country(
    tenant_id: str, actor_id: str, country_code: str
) -> dict[str, Any]:
    # Только рубли на карту Т-Банк (владелец, 09.10.2026: представители WHIEDA против оплаты
    # во внутренней валюте). Старые кнопки «Беларусь / другая страна» ведут сюда же.
    country = "RU"
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set country_code = %s, status = 'awaiting_name', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_country'
            returning *
            """,
            (country, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Заявка уже перешла к следующему шагу.")
    return row


# Адреса сайтов партнёров — только личные имена (владелец, 08.10.2026: «whieda — занят,
# пусть не борзеют; бизнес и прочее — технические домены»). Слово целиком или часть
# адреса из RESERVED_PARTS — отказ, адрес оставляем за WWC.
RESERVED_SUBDOMAINS = frozenset({
    "www", "api", "app", "admin", "mail", "smtp", "ftp", "cdn", "static", "media", "img", "files",
    "dev", "test", "stage", "staging", "demo", "beta", "core", "n8n", "bot", "bots", "status",
    "shop", "store", "masterskaya", "academy", "akademiya", "club", "klub", "support", "help", "start",
    "business", "biznes", "office", "official", "news", "blog", "promo", "pay", "partner", "partners",
    "team", "komanda", "company", "kompaniya", "catalog", "katalog", "efir", "biohacking", "leader", "lider",
    # Витрины Академии авторов и служебное имя для их CNAME (V29, 11.10.2026).
    "almira", "edge",
})
RESERVED_PARTS = ("whieda", "wwc", "wiieda", "vieda")


def subdomain_reserved(subdomain: str) -> bool:
    value = str(subdomain or "").lower()
    return value in RESERVED_SUBDOMAINS or any(part in value for part in RESERVED_PARTS)


# Мат и оскорбления в адресе сайта (владелец, 15.10.2026: «а если бы её завели под
# именем ПИЗДА?»). Корни — вхождением (в именах не встречаются), короткие слова —
# только целым адресом или его частью через дефис: «hui» сидит в «Chuikov», «eban» —
# в «Lebanova». Цифры-подмены (3→e, 0→o, 1→i, 4→ch) снимаются перед проверкой.
OFFENSIVE_ROOTS = (
    "pizd", "pisd", "xuy", "xuj", "huyl", "huil", "blyad", "blyat", "blya-", "mudak", "mudil", "pidor", "pidar",
    "pidr", "gandon", "gondon", "zalup", "govno", "shlyuh", "shluh", "yeban", "yebat", "zaeb", "eblan", "dolboeb",
    "fuck", "cunt", "bitch", "whore", "porn", "hitler", "nigger",
)
OFFENSIVE_WORDS = frozenset({
    "hui", "huy", "huj", "xui", "suka", "nazi", "shit", "blya", "ebat", "eban", "ebal", "ebla", "jopa", "zhopa", "zopa",
    "sraka", "sex", "xxx", "dick", "ass", "anal", "pisya", "siski", "drochit", "dura", "durak", "loh", "lox",
    "tvar", "urod", "debil", "idiot", "kretin",
})
_LEET = {"0": "o", "1": "i", "4": "ch", "@": "a", "$": "s"}
# «3» — и «e», и «z» (pi3da): проверяем оба прочтения.
_READINGS = (str.maketrans({**_LEET, "3": "e"}), str.maketrans({**_LEET, "3": "z"}))


def subdomain_offensive(subdomain: str) -> bool:
    raw = str(subdomain or "").lower()
    for reading in _READINGS:
        value = raw.translate(reading)
        if any(root in value for root in OFFENSIVE_ROOTS):
            return True
        if value in OFFENSIVE_WORDS or any(p in OFFENSIVE_WORDS for p in value.split("-") if p):
            return True
    return False


def clean_partner_name(value: str) -> str:
    """Имя и фамилия для сайта (V23): два слова и больше, без ссылок и цифр."""
    name = " ".join(str(value or "").split())
    words = [w for w in name.split(" ") if w]
    if len(words) < 2 or len(name) > 120 or any(ch.isdigit() for ch in name) or "/" in name or "@" in name:
        raise SiteRequestError("Напишите имя и фамилию — так вас увидят на сайте. Например: Наталья Иванова.")
    return name


async def set_site_request_name(tenant_id: str, actor_id: str, name: str) -> dict[str, Any]:
    value = clean_partner_name(name)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set partner_name = %s, status = 'awaiting_subdomain', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_name'
            returning *
            """,
            (value, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас имя не ожидается.")
    return row


# Шаги, которые можно пропустить (владелец, 08.10.2026): обязательны только имя и адрес.
# Фото — позже, текст — стандартный, контакты — из Telegram.
SKIPPABLE_STEPS: dict[str, tuple[str, str]] = {
    "awaiting_photo": ("awaiting_text", "фото"),
    "awaiting_text": ("awaiting_contacts", "текст о себе"),
    "awaiting_contacts": ("awaiting_plan", "контакты"),
}


async def skip_site_request_step(tenant_id: str, actor_id: str, *, conn: Any = None) -> tuple[dict[str, Any], str]:
    """«Пропустить»: шаг закрывается пустым. Возвращает заявку и что пропущено.

    ``conn`` — открытое ``tenant_connection`` того же арендатора (кнопка «Пропустить» ищет актора и
    закрывает шаг одной транзакцией); без него функция открывает свою, как раньше."""
    async with (nullcontext(conn) if conn is not None else tenant_connection(tenant_id)) as conn:
        current = await fetch_one(
            conn,
            """
            select status from partner_site_requests
            where tenant_id = %s and actor_id = %s
              and status not in ('provisioned', 'rejected', 'cancelled')
            order by created_at desc limit 1
            for update
            """,
            (tenant_id, actor_id),
        )
        step = SKIPPABLE_STEPS.get(str((current or {}).get("status") or ""))
        if not step:
            raise SiteRequestError("Этот шаг пропустить нельзя.")
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set status = %s, updated_at = now()
            where tenant_id = %s and actor_id = %s and status = %s
            returning *
            """,
            (step[0], tenant_id, actor_id, current["status"]),
        )
    if not row:
        raise SiteRequestError("Заявка уже перешла к следующему шагу.")
    return row, step[1]


async def set_site_request_intro_voice(tenant_id: str, actor_id: str, file_id: str) -> dict[str, Any]:
    """Голосовое вместо текста о себе (V23): текст расшифруем сами."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set intro_voice_file_id = %s, status = 'awaiting_contacts', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            returning *
            """,
            (file_id, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас текст не ожидается.")
    return row


async def subdomain_taken(conn: Any, tenant_id: str, subdomain: str, actor_id: str = "") -> bool:
    """Занят ли адрес <subdomain>.wwc.best.

    Сверяем не только ref_code: адрес сайта может отличаться от него (elena.wwc.best
    ведёт на onlineelena). 24.09.2026 бот принял elena от нового партнёра, а на сайте
    этот адрес давно у другого человека.
    """
    if subdomain in ISSUED_SUBDOMAIN_TO_REF:
        return True
    row = await fetch_one(
        conn,
        """
        select 1 as taken from referral_profiles
        where ref_code = %s or public_profile->>'subdomain' = %s
           or public_profile->>'public_site_url' like %s
           -- Старые адреса партнёра (svelaya → doronina, 25.09.2026) ведут на него же.
           or coalesce(public_profile->'retired_subdomains', '[]'::jsonb) ? %s
        union all
        select 1 as taken from partner_site_requests
        where tenant_id = %s and requested_subdomain = %s and actor_id <> %s
          and status not in ('rejected', 'cancelled')
        limit 1
        """,
        (subdomain, subdomain, f"https://{subdomain}.wwc.best%", subdomain, tenant_id, subdomain, actor_id),
    )
    return bool(row)


async def set_site_request_subdomain(
    tenant_id: str, actor_id: str, subdomain: str
) -> dict[str, Any]:
    try:
        normalized = normalize_partner_subdomain(subdomain)
    except SubscriptionError as exc:
        raise SiteRequestError(
            "Напишите другое имя: латинские буквы, цифры и дефис, без пробелов."
        ) from exc
    if subdomain_offensive(normalized):
        raise SiteRequestError(
            "Такой адрес не подойдёт. Напишите имя или имя и фамилию латиницей, например: olga-ivanova."
        )
    if subdomain_reserved(normalized):
        raise SiteRequestError(
            f"Адрес {normalized}.wwc.best занят. Для сайтов партнёров используем личные имена — "
            "напишите имя или имя и фамилию латиницей, например: olga-ivanova."
        )
    async with tenant_connection(tenant_id) as conn:
        if await subdomain_taken(conn, tenant_id, normalized, actor_id):
            raise SiteRequestError(
                f"Адрес {normalized}.wwc.best уже занят. Напишите другой вариант — "
                "например, добавьте фамилию или город."
            )
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set requested_subdomain = %s, status = 'awaiting_lang', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_subdomain'
            returning *
            """,
            (normalized, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас имя сайта не ожидается.")
    return row


SITE_LANGS: dict[str, str] = {"ru": "Русский", "en": "English", "de": "Deutsch"}


async def set_site_request_lang(tenant_id: str, actor_id: str, lang: str) -> dict[str, Any]:
    """Основной язык сайта и визитки (V28, 10.10.2026) — после адреса, дальше фото."""
    code = str(lang or "").strip().lower()
    if code not in SITE_LANGS:
        raise SiteRequestError("Выберите язык кнопкой: Русский, English или Deutsch.")
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set site_lang = %s, status = 'awaiting_photo', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_lang'
            returning *
            """,
            (code, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас язык сайта не ожидается.")
    return row


async def set_site_request_photo(
    tenant_id: str, actor_id: str, file_id: str
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set profile_photo_file_id = %s, status = 'awaiting_text', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_photo'
            returning *
            """,
            (file_id, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас фотография не ожидается.")
    return row


async def set_site_request_intro(
    tenant_id: str, actor_id: str, intro_text: str
) -> dict[str, Any]:
    value = str(intro_text or "").strip()
    if len(value) < 2:
        raise SiteRequestError("Напишите пару слов о себе или нажмите «Пропустить».")
    if len(value) > 3000:
        raise SiteRequestError("Текст длиннее 3000 знаков. Пришлите более короткий вариант.")
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select country_code from partner_site_requests
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            for update
            """,
            (tenant_id, actor_id),
        )
        if not request:
            raise SiteRequestError("Сейчас текст не ожидается.")
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set intro_text = %s, status = 'awaiting_contacts', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            returning *
            """,
            (value, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось сохранить текст.")
    return row


async def set_site_request_contacts(
    tenant_id: str, actor_id: str, contacts_text: str
) -> dict[str, Any]:
    """Контакты для сайта одним сообщением (V12, 24.09.2026). «Нет» — тоже ответ:
    шаг закрывается с пустым разбором, сайт соберётся из Telegram."""
    value = str(contacts_text or "").strip()
    if not value:
        raise SiteRequestError("Пришлите контакты сообщением или напишите «нет».")
    if len(value) > 2000:
        raise SiteRequestError("Сообщение длиннее 2000 знаков. Пришлите покороче.")
    parsed = parse_contacts(value)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set contacts_text = %s, contacts = %s::jsonb,
                status = 'awaiting_plan', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_contacts'
            returning *
            """,
            (value, json.dumps(parsed, ensure_ascii=False), tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас контакты не ожидаются.")
    return row


async def submit_site_payment_proof(
    tenant_id: str,
    actor_id: str,
    *,
    chat_id: int,
    message_id: int,
    file_id: str,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set proof_chat_id = %s, proof_message_id = %s, proof_file_id = %s,
                status = 'pending_confirmation', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_payment'
            returning *
            """,
            (chat_id, message_id, file_id, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас подтверждение оплаты не ожидается.")
    return row


async def _grant_course(conn: Any, tenant_id: str, ref_code: str, payment_ref: str) -> bool:
    import psycopg

    from app.academy.service import grant_course_access

    try:
        async with conn.transaction():
            return await grant_course_access(conn, tenant_id, ref_code, COURSE_SLUG, payment_ref) is not None
    except psycopg.Error:
        logger.warning("site_request_course_grant_failed", extra={"tenant_id": tenant_id, "ref_code": ref_code}, exc_info=True)
        return False


async def confirm_site_request(
    tenant_id: str,
    *,
    request_id: str,
    admin_telegram_user_id: int,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select sr.*, la.display_name, la.telegram_chat_id, la.telegram_user_id
            from partner_site_requests sr
            join lead_actors la
              on la.tenant_id = sr.tenant_id and la.actor_id = sr.actor_id
            where sr.tenant_id = %s and sr.request_id = %s::uuid
            for update of sr
            """,
            (tenant_id, request_id),
        )
        if not request:
            raise SiteRequestError("Заявка не найдена.")
        if request["status"] == "pending_provisioning":
            return {**request, "idempotent": True}
        if request["status"] != "pending_confirmation":
            raise SiteRequestError("Эту заявку сейчас нельзя подтвердить.")
        # Адрес могли занять, пока заявка ждала оплаты, — ловим до записи денег.
        if await subdomain_taken(conn, tenant_id, str(request["requested_subdomain"]), str(request["actor_id"])):
            raise SiteRequestError("Имя сайта уже занято. Заявку нужно проверить вручную.")
        profile = json.dumps(
            {
                "subdomain": request["requested_subdomain"],
                "display_name": request["display_name"],
                "intro_text": request["intro_text"],
                "telegram_photo_file_id": request["profile_photo_file_id"],
                "provisioning_status": "pending",
            },
            ensure_ascii=False,
        )
        created = await fetch_one(
            conn,
            """
            insert into referral_profiles (
              ref_code, tenant_id, owner_id, display_mode, public_profile,
              country_code, enabled
            ) values (%s, %s, %s, 'named', %s::jsonb, %s, false)
            on conflict (ref_code) do nothing
            returning ref_code
            """,
            (
                request["requested_subdomain"], tenant_id, request["actor_id"],
                profile, request["country_code"],
            ),
        )
        if not created:
            raise SiteRequestError("Имя сайта уже занято. Заявку нужно проверить вручную.")
        # Профиль до сборки сайта выключен (enabled = false), а строка подписки
        # создаётся платёжным путём только для включённых — заявка падала на
        # «active referral profile not found» и подтверждение никогда не проходило
        # (найдено тестом 19.09.2026). Строку заводим здесь; сроки проставит платёж.
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                values (%s, %s, null)
                on conflict (tenant_id, ref_code) do nothing
                """,
                (tenant_id, request["requested_subdomain"]),
            )
        # Одна оплата — несколько строк (сайт + настройка или сайт + клуб + настройка): так же, как
        # владелец записывает вручную через «оплата ref:… / пакет …». Настройка сайта
        # раньше в ledger не попадала, и 2 000 ₽ терялись в отчётах.
        currency = str(request["currency"])
        plan = resolve_plan_code(request.get("plan_code"), request.get("total_amount_minor"), currency)
        result = await record_payment_lines_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=str(request["requested_subdomain"]),
            lines=plan_lines(plan, currency),
            received_minor=plan_total_minor(plan, currency),
            currency=currency,
            telegram_chat_id=int(request["proof_chat_id"]),
            telegram_message_id=int(request["proof_message_id"]),
            telegram_user_id=int(request["telegram_user_id"]),
        )
        # Курс из варианта «сайт + курс»: открываем курс онлайн-запуска штатной выдачей Академии. Она же вызывается
        # из платёжной строки по course_slug тарифа, но у тарифа course_academy слаг может быть пуст, поэтому
        # курс назначаем явно (повтор безопасен). Сбой выдачи платёж не откатывает — владелец увидит «не выдан».
        course_granted = False
        course_line = next((l for l in result["lines"] if l["product_code"] == COURSE_PRODUCT), None)
        if course_line and not result.get("idempotent"):
            course_granted = await _grant_course(conn, tenant_id, str(request["requested_subdomain"]), str(course_line["payment_id"]))
        pro = next((l for l in result["lines"] if l["product_code"] == "platform_subscription"), result["lines"][0])
        payment = {**pro, "referral_bonus": result.get("referral_bonus"), "club_paid_until": result.get("club_paid_until")}
        updated = await fetch_one(
            conn,
            """
            update partner_site_requests
            set status = 'pending_provisioning', subscription_payment_id = %s,
                confirmed_by_telegram_user_id = %s, confirmed_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning *
            """,
            (payment["payment_id"], admin_telegram_user_id, tenant_id, request_id),
        )
    return {**updated, "payment": payment, "idempotent": False, "plan": plan, "course_access_granted": course_granted}


async def reject_site_request(
    tenant_id: str, *, request_id: str, admin_telegram_user_id: int
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set status = 'rejected', confirmed_by_telegram_user_id = %s,
                rejected_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
              and status = 'pending_confirmation'
            returning *
            """,
            (admin_telegram_user_id, tenant_id, request_id),
        )
    if not row:
        raise SiteRequestError("Заявку уже обработали или она не найдена.")
    return row
