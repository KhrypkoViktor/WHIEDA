"""Мастерская WWC: каталог, заказы и доступы (platform_shop_v22.sql, 03.10.2026).

Покупка как всё в WWC: кнопка на сайте → бот (``?start=shop_<code>[_<ref>]``) →
карточка, страна оплаты, реквизиты → чек → «Оплачено» владельца → доставка:

* ``course``  — строка ``academy_access`` по Telegram id покупателя (купить может
  любой, не только партнёр с профилем) и строка ``shop_access``;
* ``digital`` — строка ``shop_access``: файл скачивается из кабинета /me/ по
  подписанной ссылке (как медиа Академии, 1 час);
* ``service`` — ничего: о времени договариваются в той же заявке;
* ``external`` — не через заказ (Gemini ведёт на свой поток ``?start=gemini``).

Решения владельца (03.10.2026): подтверждает только владелец; доли партнёру нет —
``partner_ref_code`` в заказе только для статистики «кто привёл». Продажи Gemini
остаются в ``service_sales``.

Видимость: ``published`` — всем; ``pilot`` — ещё и preview-админам (владелец и
супер-админы, как превью Академии); ``draft`` и ``archived`` — только в админке.
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from app.academy.content import render_markdown
from app.academy.keys import actor_ids_for_telegram
from app.academy.media_service import load_media, media_links, media_summary
from app.academy.service import grant_course_to_telegram_user, preview_admin_ids
from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger(__name__)

STATUSES = ("draft", "pilot", "published", "archived")
KINDS = ("service", "course", "digital", "external")
CATEGORIES = ("services", "courses", "materials", "tools")
COUNTRIES = ("RU", "BY")
# Россия платит в рублях, Беларусь — в WWC$ (как продление и заявка на сайт).
COUNTRY_CURRENCY = {"RU": "RUB", "BY": "WUSD"}
RUB_PER_WUSD = 100
BYN_PER_WUSD_TENTHS = 35  # 1 WWC$ = 3,5 BYN
# Брошенный заказ не забирает чужие фото (как заявка на сайт, STALE_AFTER).
STALE_AFTER = timedelta(days=3)
BOT_START_PREFIX = "shop_"
EXTERNAL_START_PREFIX = "?start="
CODE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_CODE_CHARS = 40


class ShopError(Exception):
    """A shop request was refused; ``code`` is machine-readable."""

    def __init__(self, status: int, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = dict(extra or {})


_ITEM_COLUMNS = """
code, kind, category, title, subtitle, description_md, description_html,
price_wusd_minor, price_rub_minor, price_byn_minor, price_club_wusd_minor, price_text,
cover_media_id::text as cover_media_id, course_slug, file_media_id::text as file_media_id,
external_url, partner_share_wusd_minor, confirmer, status, sort_order,
requisites_note, delivery_note, updated_by_telegram_user_id, created_at, updated_at
"""
# Same columns, qualified for joins.
_ITEM_COLUMNS_I = ", ".join(f"i.{col.strip()}" for col in _ITEM_COLUMNS.split(","))

_ORDER_COLUMNS = """
order_id::text as order_id, item_code, item_title, telegram_user_id, ticket_id::text as ticket_id,
partner_ref_code, partner_ref_source, country_code, currency, amount_minor, status,
receipt_file_id, receipt_at, paid_at, paid_by, delivered_at, cancelled_at, created_at, updated_at
"""


# ---- prices -----------------------------------------------------------------------------


def price_rub_minor(item: dict[str, Any]) -> int:
    value = item.get("price_rub_minor")
    return int(value) if value is not None else int(item["price_wusd_minor"]) * RUB_PER_WUSD


def price_byn_minor(item: dict[str, Any]) -> int:
    value = item.get("price_byn_minor")
    if value is not None:
        return int(value)
    return (int(item["price_wusd_minor"]) * BYN_PER_WUSD_TENTHS + 5) // 10


def club_price_wusd_minor(item: dict[str, Any]) -> int | None:
    value = item.get("price_club_wusd_minor")
    return int(value) if value is not None else None


def priced_for(item: dict[str, Any], *, club: bool) -> dict[str, Any]:
    """Товар с ценой для этого покупателя: участнику клуба — клубная (₽ = WWC$ × 100)."""
    club_wusd = club_price_wusd_minor(item)
    if not club or club_wusd is None:
        return item
    return {**item, "price_wusd_minor": club_wusd, "price_rub_minor": club_wusd * RUB_PER_WUSD, "price_byn_minor": None}


def price_for_country(item: dict[str, Any], country: str, *, club: bool = False) -> tuple[str, int]:
    """Валюта и сумма заказа по стране оплаты."""
    code = str(country or "").upper()
    if code not in COUNTRY_CURRENCY:
        raise ShopError(400, "bad_country")
    currency = COUNTRY_CURRENCY[code]
    priced = priced_for(item, club=club)
    return currency, price_rub_minor(priced) if currency == "RUB" else int(priced["price_wusd_minor"])


def major(minor: Any) -> int | float:
    """``1750`` → ``17.5``; ``10000`` → ``100``: цены сайту — в целых единицах."""
    amount = int(minor) / 100
    return int(amount) if amount.is_integer() else round(amount, 2)


def prices_out(item: dict[str, Any]) -> dict[str, int | float]:
    out = {
        "wusd": major(item["price_wusd_minor"]),
        "rub": major(price_rub_minor(item)),
        "byn": major(price_byn_minor(item)),
    }
    club = club_price_wusd_minor(item)
    if club is not None:
        out["club_wusd"] = major(club)
        out["club_rub"] = major(club * RUB_PER_WUSD)
    return out


# ---- who sees what ----------------------------------------------------------------------


def is_shop_admin(telegram_user_id: int | None) -> bool:
    """Владелец и супер-админы — те же, кто видит превью Академии."""
    return telegram_user_id is not None and int(telegram_user_id) in preview_admin_ids()


def visible_statuses(*, admin: bool) -> tuple[str, ...]:
    return ("published", "pilot") if admin else ("published",)


def item_visible(item: dict[str, Any] | None, *, admin: bool) -> bool:
    return bool(item) and str(item["status"]) in visible_statuses(admin=admin)


def unavailable_reason(item: dict[str, Any]) -> str | None:
    """Почему товар нельзя заказать в боте; None — можно."""
    if item["kind"] == "external":
        return "external"
    if item["kind"] == "course" and not item.get("course_slug"):
        return "course_not_ready"
    return None


def bot_link(bot_username: str | None, start: str) -> str | None:
    name = str(bot_username or "").lstrip("@").strip()
    return f"https://t.me/{name}?start={start}" if name else None


def item_cta(item: dict[str, Any], bot_username: str | None) -> dict[str, Any]:
    """Кнопка карточки: в бота (``start`` — токен, сайт добавит ``_<ref>``) или ссылка."""
    if item["kind"] == "external":
        url = str(item.get("external_url") or "").strip()
        if url.startswith(EXTERNAL_START_PREFIX):
            start = url[len(EXTERNAL_START_PREFIX):]
            return {"kind": "bot", "start": start, "url": bot_link(bot_username, start)}
        return {"kind": "link", "start": None, "url": url}
    start = f"{BOT_START_PREFIX}{item['code']}"
    return {"kind": "bot", "start": start, "url": bot_link(bot_username, start)}


def description_html(item: dict[str, Any]) -> str:
    stored = str(item.get("description_html") or "")
    if stored or not item.get("description_md"):
        return stored
    try:
        return render_markdown(item["description_md"])
    except ValueError:
        return ""


_TAG_RE = re.compile(r"<[^>]+>")
_BLANKS_RE = re.compile(r"\n{3,}")


def description_text(item: dict[str, Any]) -> str:
    """Описание для бота: markdown → обычный текст (бот сам экранирует HTML)."""
    rendered = description_html(item)
    if not rendered:
        return ""
    text = re.sub(r"</(p|h[1-6]|li|blockquote|tr)>|<br\s*/?>", "\n", rendered, flags=re.I)
    text = html.unescape(_TAG_RE.sub("", text))
    return _BLANKS_RE.sub("\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


# ---- catalog ----------------------------------------------------------------------------


async def get_item(tenant_id: str, code: str) -> dict[str, Any] | None:
    clean = str(code or "").strip().lower()
    if not CODE_RE.fullmatch(clean) or len(clean) > MAX_CODE_CHARS:
        return None
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn, f"select {_ITEM_COLUMNS} from shop_items where tenant_id = %s and code = %s", (tenant_id, clean)
        )


async def list_items(tenant_id: str, *, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            select {_ITEM_COLUMNS} from shop_items
            where tenant_id = %s and (%s::text[] is null or status = any(%s::text[]))
            order by sort_order, code
            """,
            (tenant_id, list(statuses) if statuses is not None else None, list(statuses) if statuses is not None else None),
        )


async def _cover_urls(tenant_id: str, items: list[dict[str, Any]], viewer_id: int | None) -> dict[str, str]:
    ids = [item["cover_media_id"] for item in items if item.get("cover_media_id")]
    if not ids:
        return {}
    async with tenant_connection(tenant_id) as conn:
        media = await load_media(conn, tenant_id, ids)
    # Обложка — витрина: подпись на id зрителя, гостю — 0 (ссылка живёт час).
    urls = {}
    for media_id, row in media.items():
        url = media_links(row, int(viewer_id or 0)).get("url") if row.get("kind") == "image" else None
        if url:
            urls[media_id] = url
    return urls


def public_item(item: dict[str, Any], *, cover_url: str | None, bot_username: str | None) -> dict[str, Any]:
    """Карточка для сайта. Доли партнёра нет и не показывается (владелец, 03.10.2026)."""
    return {
        "code": item["code"],
        "kind": item["kind"],
        "category": item["category"],
        "title": item["title"],
        "subtitle": item.get("subtitle"),
        "description_html": description_html(item),
        "prices": prices_out(item),
        "price_text": item.get("price_text"),
        "cover_url": cover_url,
        "cta": item_cta(item, bot_username),
        "available": unavailable_reason(item) in (None, "external"),
        "status": item["status"],
    }


async def public_catalog(tenant_id: str, *, viewer_id: int | None) -> dict[str, Any]:
    """``GET /api/v1/public/shop``: published всем; pilot — ещё и preview-админу."""
    admin = is_shop_admin(viewer_id)
    items = await list_items(tenant_id, statuses=visible_statuses(admin=admin))
    covers = await _cover_urls(tenant_id, items, viewer_id)
    bot = get_settings().telegram_bot_username
    return {
        "ok": True,
        "preview": admin,
        "items": [public_item(i, cover_url=covers.get(str(i.get("cover_media_id") or "")), bot_username=bot) for i in items],
    }


# ---- who brought the buyer (statistics only, no money) ----------------------------------


async def resolve_partner_ref(
    tenant_id: str, *, telegram_user_id: int, link_ref: str | None
) -> tuple[str | None, str | None]:
    """Код партнёра для заказа: из ссылки ``shop_<code>_<ref>``, если такой партнёр
    есть и это не сам покупатель; иначе первое касание из атрибуции."""
    async with tenant_connection(tenant_id) as conn:
        buyer = await actor_ids_for_telegram(conn, tenant_id, int(telegram_user_id))
        ref = str(link_ref or "").strip().lower()
        if ref:
            row = await fetch_one(
                conn,
                "select ref_code, owner_id from referral_profiles where tenant_id = %s and lower(ref_code) = %s and enabled = true",
                (tenant_id, ref),
            )
            if row and str(row["owner_id"]) not in buyer:
                return str(row["ref_code"]), "link"
        if not buyer:
            return None, None
        first = await fetch_one(
            conn,
            """
            select rp.ref_code from partner_referral_attributions a
            join referral_profiles rp
              on rp.tenant_id = a.tenant_id and rp.owner_id = a.inviter_actor_id and rp.enabled = true
            where a.tenant_id = %s and a.invitee_actor_id = any(%s) and not (rp.owner_id = any(%s))
            order by a.created_at
            limit 1
            """,
            (tenant_id, buyer, buyer),
        )
    return (str(first["ref_code"]), "attribution") if first else (None, None)


async def is_club_member(tenant_id: str, telegram_user_id: int) -> bool:
    """Активный клуб (club_subscription не истёк) у одного из профилей этого человека."""
    async with tenant_connection(tenant_id) as conn:
        actors = await actor_ids_for_telegram(conn, tenant_id, int(telegram_user_id))
        if not actors:
            return False
        row = await fetch_one(
            conn,
            """
            select 1 as ok from referral_profiles rp
            join partner_product_access pa
              on pa.tenant_id = rp.tenant_id and pa.ref_code = rp.ref_code
             and pa.product_code = 'club_subscription' and pa.paid_until > now()
            where rp.tenant_id = %s and rp.enabled = true and rp.owner_id = any(%s)
            limit 1
            """,
            (tenant_id, actors),
        )
    return bool(row)


async def course_offers(conn: Any, tenant_id: str, slugs: list[str]) -> dict[str, dict[str, Any]]:
    """Курсы Академии, которые продаются в Мастерской (опубликованная карточка kind=course):
    ``{course_slug: {code, start, url, prices}}`` — кнопка «Купить» в боте и на сайте."""
    wanted = sorted({str(slug) for slug in slugs if slug})
    if not wanted:
        return {}
    rows = await fetch_all(
        conn,
        f"""
        select {_ITEM_COLUMNS} from shop_items
        where tenant_id = %s and kind = 'course' and status = 'published' and course_slug = any(%s)
        order by sort_order, code
        """,
        (tenant_id, wanted),
    )
    bot = get_settings().telegram_bot_username
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        slug = str(row["course_slug"])
        if slug in out:
            continue
        start = f"{BOT_START_PREFIX}{row['code']}"
        out[slug] = {"code": row["code"], "start": start, "url": bot_link(bot, start), "prices": prices_out(row)}
    return out


# ---- orders -----------------------------------------------------------------------------


async def open_order(
    tenant_id: str,
    *,
    item: dict[str, Any],
    telegram_user_id: int,
    ticket_id: str | None,
    country_code: str,
    partner_ref_code: str | None,
    partner_ref_source: str | None,
    club: bool = False,
) -> dict[str, Any]:
    """Новый заказ или уже открытый на этот товар (до оплаты — один); ``created``.
    ``club`` — покупатель в клубе: сумма по клубной цене, если она у товара есть."""
    if unavailable_reason(item):
        raise ShopError(409, "not_for_sale")
    currency, amount = price_for_country(item, country_code, club=club)
    country = str(country_code).upper()
    async with tenant_connection(tenant_id) as conn:
        created = await fetch_one(
            conn,
            f"""
            insert into shop_orders (
              tenant_id, item_code, item_title, telegram_user_id, ticket_id,
              partner_ref_code, partner_ref_source, country_code, currency, amount_minor
            ) values (%s, %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s)
            on conflict (tenant_id, telegram_user_id, item_code) where status in ('new', 'receipt')
            do nothing
            returning {_ORDER_COLUMNS}
            """,
            (
                tenant_id, item["code"], item["title"], int(telegram_user_id), ticket_id,
                partner_ref_code, partner_ref_source if partner_ref_code else None, country, currency, amount,
            ),
        )
        if created:
            return {**created, "created": True}
        # Тот же товар ещё раз до оплаты: тот же заказ; до чека можно сменить страну.
        existing = await fetch_one(
            conn,
            f"""
            update shop_orders set
              country_code = case when status = 'new' then %s else country_code end,
              currency = case when status = 'new' then %s else currency end,
              amount_minor = case when status = 'new' then %s else amount_minor end,
              ticket_id = coalesce(%s::uuid, ticket_id),
              updated_at = now()
            where tenant_id = %s and telegram_user_id = %s and item_code = %s and status in ('new', 'receipt')
            returning {_ORDER_COLUMNS}
            """,
            (country, currency, amount, ticket_id, tenant_id, int(telegram_user_id), item["code"]),
        )
    if not existing:
        raise RuntimeError("could not open shop order")
    return {**existing, "created": False}


async def waiting_order_at(tenant_id: str, *, telegram_user_id: int, now: datetime | None = None) -> datetime | None:
    """Когда человек последний раз оформил заказ, который ждёт чек (свежий ``new``); None — такого нет."""
    since = (now or datetime.now(timezone.utc)) - STALE_AFTER
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select max(updated_at) as at from shop_orders
            where tenant_id = %s and telegram_user_id = %s and status = 'new' and updated_at >= %s
            """,
            (tenant_id, int(telegram_user_id), since),
        )
    return (row or {}).get("at")


async def release_receipt(tenant_id: str, *, order_id: str) -> bool:
    """Чек не дошёл до владельца — заказ снова ждёт чек: покупатель пришлёт его ещё раз."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update shop_orders set status = 'new', receipt_file_id = null, receipt_at = null, updated_at = now()
            where tenant_id = %s and order_id = %s::uuid and status = 'receipt'
            returning order_id
            """,
            (tenant_id, str(order_id)),
        )
    return row is not None


async def take_receipt(
    tenant_id: str, *, telegram_user_id: int, file_id: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Фото или файл покупателя со свежим заказом ``new`` — чек: заказ → ``receipt``.
    Пусто — у человека нет заказа, который ждёт оплату."""
    since = (now or datetime.now(timezone.utc)) - STALE_AFTER
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            update shop_orders set status = 'receipt', receipt_file_id = %s, receipt_at = now(), updated_at = now()
            where tenant_id = %s and telegram_user_id = %s and status = 'new' and updated_at >= %s
            returning {_ORDER_COLUMNS}
            """,
            (str(file_id), tenant_id, int(telegram_user_id), since),
        )


async def _grant_access(conn: Any, tenant_id: str, order: dict[str, Any]) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into shop_access (tenant_id, item_code, telegram_user_id, order_id)
            values (%s, %s, %s, %s::uuid)
            on conflict (tenant_id, item_code, telegram_user_id)
            do update set revoked_at = null, order_id = excluded.order_id, granted_at = now()
            """,
            (tenant_id, order["item_code"], int(order["telegram_user_id"]), order["order_id"]),
        )


async def confirm_order(tenant_id: str, *, order_id: str, paid_by: int) -> dict[str, Any]:
    """«Оплачено»: заказ → paid и сразу доставка в той же транзакции.

    ``idempotent`` — заказ уже был оплачен или отменён (повторное нажатие);
    ``delivered`` — курс или файл открыт; у курса без курса в Академии — False."""
    try:
        canonical = str(uuid.UUID(str(order_id)))
    except ValueError as exc:
        raise ShopError(404, "order_not_found") from exc
    async with tenant_connection(tenant_id) as conn:
        order = await fetch_one(
            conn,
            f"""
            update shop_orders set status = 'paid', paid_at = now(), paid_by = %s, updated_at = now()
            where tenant_id = %s and order_id = %s::uuid and status in ('new', 'receipt')
            returning {_ORDER_COLUMNS}
            """,
            (int(paid_by), tenant_id, canonical),
        )
        if not order:
            existing = await fetch_one(
                conn, f"select {_ORDER_COLUMNS} from shop_orders where tenant_id = %s and order_id = %s::uuid", (tenant_id, canonical)
            )
            if not existing:
                raise ShopError(404, "order_not_found")
            item = await fetch_one(conn, f"select {_ITEM_COLUMNS} from shop_items where tenant_id = %s and code = %s", (tenant_id, existing["item_code"]))
            return {"order": existing, "item": item, "idempotent": True, "delivered": existing["status"] == "delivered"}
        item = await fetch_one(conn, f"select {_ITEM_COLUMNS} from shop_items where tenant_id = %s and code = %s", (tenant_id, order["item_code"]))
        delivered = False
        kind = str((item or {}).get("kind") or "")
        if kind == "course" and item.get("course_slug"):
            try:
                # Savepoint: сбой Академии — предупреждение владельцу, не потерянная оплата.
                async with conn.transaction():
                    delivered = await grant_course_to_telegram_user(
                        conn, tenant_id, course_slug=str(item["course_slug"]),
                        telegram_user_id=int(order["telegram_user_id"]), payment_ref=f"shop:{order['order_id']}",
                    )
            except psycopg.Error:
                logger.warning("shop_course_grant_failed", extra={"item": item["code"]}, exc_info=True)
                delivered = False
        elif kind == "digital":
            delivered = True
        if delivered:
            await _grant_access(conn, tenant_id, order)
            order = await fetch_one(
                conn,
                f"""
                update shop_orders set status = 'delivered', delivered_at = now(), updated_at = now()
                where tenant_id = %s and order_id = %s::uuid
                returning {_ORDER_COLUMNS}
                """,
                (tenant_id, canonical),
            )
    return {"order": order, "item": item, "idempotent": False, "delivered": delivered}


async def reject_order(tenant_id: str, *, order_id: str) -> dict[str, Any]:
    """«Отклонить»: оплату не подтвердили — заказ отменён; оплаченный не трогаем."""
    try:
        canonical = str(uuid.UUID(str(order_id)))
    except ValueError as exc:
        raise ShopError(404, "order_not_found") from exc
    async with tenant_connection(tenant_id) as conn:
        order = await fetch_one(
            conn,
            f"""
            update shop_orders set status = 'cancelled', cancelled_at = now(), updated_at = now()
            where tenant_id = %s and order_id = %s::uuid and status in ('new', 'receipt')
            returning {_ORDER_COLUMNS}
            """,
            (tenant_id, canonical),
        )
        if order:
            return {"order": order, "idempotent": False}
        existing = await fetch_one(
            conn, f"select {_ORDER_COLUMNS} from shop_orders where tenant_id = %s and order_id = %s::uuid", (tenant_id, canonical)
        )
    if not existing:
        raise ShopError(404, "order_not_found")
    return {"order": existing, "idempotent": True}


async def known_country(tenant_id: str, telegram_user_id: int) -> str | None:
    """Страна оплаты из прошлого заказа человека — первой показываем её кнопку."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select country_code from shop_orders
            where tenant_id = %s and telegram_user_id = %s and country_code is not null
            order by created_at desc limit 1
            """,
            (tenant_id, int(telegram_user_id)),
        )
    return str(row["country_code"]) if row else None


# ---- the buyer's cabinet: /me/purchases and file links ----------------------------------


def course_path(course_slug: str) -> str:
    return f"/academy/?course={course_slug}"


async def list_purchases(tenant_id: str, telegram_user_id: int) -> dict[str, Any]:
    """Мои заказы и доступы (кабинет /me/#purchases). Ссылка на файл — подписанная, 1 час."""
    uid = int(telegram_user_id)
    async with tenant_connection(tenant_id) as conn:
        orders = await fetch_all(
            conn,
            """
            select o.order_id::text as order_id, o.item_code, o.item_title, o.status, o.currency, o.amount_minor,
                   o.created_at, o.paid_at, o.delivered_at, i.kind, i.category, t.ticket_no
            from shop_orders o
            left join shop_items i on i.tenant_id = o.tenant_id and i.code = o.item_code
            left join support_tickets t on t.ticket_id = o.ticket_id
            where o.tenant_id = %s and o.telegram_user_id = %s
            order by o.created_at desc
            limit 50
            """,
            (tenant_id, uid),
        )
        access = await fetch_all(
            conn,
            f"""
            select a.granted_at, {_ITEM_COLUMNS_I}
            from shop_access a
            join shop_items i on i.tenant_id = a.tenant_id and i.code = a.item_code
            where a.tenant_id = %s and a.telegram_user_id = %s and a.revoked_at is null
            order by a.granted_at desc
            """,
            (tenant_id, uid),
        )
        media = await load_media(
            conn, tenant_id, [row.get("file_media_id") for row in access] + [row.get("cover_media_id") for row in access]
        )
    expires_in = int(get_settings().platform_academy_media_url_ttl_seconds)
    out_access = []
    for row in access:
        file_row = media.get(str(row.get("file_media_id") or "")) if row["kind"] == "digital" else None
        cover = media.get(str(row.get("cover_media_id") or ""))
        summary = media_summary(file_row, uid) if file_row else None
        out_access.append(
            {
                "item": {
                    "code": row["code"], "kind": row["kind"], "category": row["category"], "title": row["title"],
                    "subtitle": row.get("subtitle"),
                    "cover_url": media_links(cover, uid).get("url") if cover and cover.get("kind") == "image" else None,
                },
                "granted_at": _iso(row["granted_at"]),
                "download_url": (summary or {}).get("url"),
                "file": {"name": summary["name"], "size": summary["size"], "mime": summary["mime"]} if summary else None,
                "expires_in": expires_in if summary and summary.get("url") else None,
                "course_url": course_path(str(row["course_slug"])) if row["kind"] == "course" and row.get("course_slug") else None,
            }
        )
    return {
        "ok": True,
        "orders": [
            {
                "order_id": o["order_id"],
                "item": {"code": o["item_code"], "title": o["item_title"], "kind": o.get("kind"), "category": o.get("category")},
                "status": o["status"],
                "amount": {"value": major(o["amount_minor"]), "currency": o["currency"]},
                "ticket": f"#S-{int(o['ticket_no'])}" if o.get("ticket_no") is not None else None,
                "created_at": _iso(o["created_at"]),
                "paid_at": _iso(o.get("paid_at")),
                "delivered_at": _iso(o.get("delivered_at")),
            }
            for o in orders
        ],
        "access": out_access,
    }


async def file_link(tenant_id: str, item_code: str, telegram_user_id: int) -> dict[str, Any]:
    """``GET /shop/files/{code}``: свежая подписанная ссылка на файл тому, у кого он куплен
    (или preview-админу)."""
    uid = int(telegram_user_id)
    item = await get_item(tenant_id, item_code)
    if not item or item["kind"] != "digital":
        raise ShopError(404, "item_not_found")
    async with tenant_connection(tenant_id) as conn:
        if not is_shop_admin(uid):
            granted = await fetch_one(
                conn,
                """
                select 1 as ok from shop_access
                where tenant_id = %s and item_code = %s and telegram_user_id = %s and revoked_at is null
                """,
                (tenant_id, item["code"], uid),
            )
            if not granted:
                raise ShopError(403, "purchase_required")
        media = await load_media(conn, tenant_id, [item.get("file_media_id")])
    row = media.get(str(item.get("file_media_id") or ""))
    if not row or row.get("status") != "ready":
        raise ShopError(404, "file_not_ready")
    summary = media_summary(row, uid)
    if not summary.get("url"):
        raise ShopError(503, "media_unavailable")
    return {
        "ok": True,
        "item_code": item["code"],
        "name": summary["name"],
        "size": summary["size"],
        "mime": summary["mime"],
        "url": summary["url"],
        "expires_in": int(get_settings().platform_academy_media_url_ttl_seconds),
    }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else None


# ---- admin: the catalog from the site (owner / preview admins) --------------------------

_TEXT_LIMITS = {"title": 200, "subtitle": 300, "description_md": 20000, "price_text": 120,
                "requisites_note": 1000, "delivery_note": 1000, "external_url": 500}
_PRICE_FIELDS = {"price_wusd": "price_wusd_minor", "price_rub": "price_rub_minor", "price_byn": "price_byn_minor",
                 "price_club_wusd": "price_club_wusd_minor"}
EDITABLE_FIELDS = frozenset(
    {"title", "subtitle", "description_md", "category", "price_text", "status", "sort_order", "cover_media_id",
     "file_media_id", "course_slug", "external_url", "requisites_note", "delivery_note", *_PRICE_FIELDS}
)


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ShopError(400, f"{field}_required")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise ShopError(400, f"{field}_required")
        return None
    if len(text) > _TEXT_LIMITS[field]:
        raise ShopError(400, f"{field}_too_long", {"limit": _TEXT_LIMITS[field]})
    return text


def _minor(value: Any, field: str, *, nullable: bool) -> int | None:
    if value is None:
        if nullable:
            return None
        raise ShopError(400, f"{field}_required")
    try:
        amount = round(float(value) * 100)
    except (TypeError, ValueError) as exc:
        raise ShopError(400, f"bad_{field}") from exc
    if amount < 0 or amount > 10_000_000_00:
        raise ShopError(400, f"bad_{field}")
    return int(amount)


def _media_id(value: Any, field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except ValueError as exc:
        raise ShopError(400, f"bad_{field}") from exc


def clean_item_fields(body: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Поля правки товара (сайт: экран «Мастерская» владельца) → колонки shop_items.
    Цены — в целых единицах (100 = 100 WWC$); rub/byn null — считать из WWC$."""
    unknown = set(body) - EDITABLE_FIELDS
    if unknown:
        raise ShopError(400, "unknown_fields", {"fields": sorted(unknown)})
    out: dict[str, Any] = {}
    for field, value in body.items():
        if field in _PRICE_FIELDS:
            out[_PRICE_FIELDS[field]] = _minor(value, field, nullable=field != "price_wusd")
        elif field == "status":
            if value not in STATUSES:
                raise ShopError(400, "bad_status", {"allowed": list(STATUSES)})
            out["status"] = value
        elif field == "category":
            if value not in CATEGORIES:
                raise ShopError(400, "bad_category", {"allowed": list(CATEGORIES)})
            out["category"] = value
        elif field == "sort_order":
            try:
                out["sort_order"] = int(value)
            except (TypeError, ValueError) as exc:
                raise ShopError(400, "bad_sort_order") from exc
        elif field in ("cover_media_id", "file_media_id"):
            if field == "file_media_id" and kind != "digital" and value is not None:
                raise ShopError(400, "file_only_for_digital")
            out[field] = _media_id(value, field)
        elif field == "course_slug":
            if kind != "course" and value is not None:
                raise ShopError(400, "course_slug_only_for_course")
            slug = str(value or "").strip().lower() or None
            if slug is not None and not CODE_RE.fullmatch(slug):
                raise ShopError(400, "bad_course_slug")
            out["course_slug"] = slug
        elif field == "external_url":
            if kind != "external":
                if value is not None:
                    raise ShopError(400, "external_url_only_for_external")
                out["external_url"] = None
                continue
            url = _text(value, field, required=True)
            if not (url.startswith(EXTERNAL_START_PREFIX) or url.startswith("https://")):
                raise ShopError(400, "bad_external_url")
            out["external_url"] = url
        else:
            out[field] = _text(value, field, required=field == "title")
    if "description_md" in out:
        md = out["description_md"] or ""
        out["description_md"] = md
        out["description_html"] = render_markdown(md) if md else ""
    return out


async def _check_references(conn: Any, tenant_id: str, fields: dict[str, Any]) -> None:
    """Обложка — картинка Академии, файл — файл Академии, курс — существующий курс."""
    for field, kind in (("cover_media_id", "image"), ("file_media_id", "file")):
        if fields.get(field):
            row = await fetch_one(
                conn, "select kind from academy_media where tenant_id = %s and media_id = %s::uuid", (tenant_id, fields[field])
            )
            if not row:
                raise ShopError(400, "media_not_found", {"field": field})
            if row["kind"] != kind:
                raise ShopError(400, "media_wrong_kind", {"field": field, "expected": kind})
    if fields.get("course_slug"):
        course = await fetch_one(
            conn, "select 1 as ok from academy_courses where tenant_id = %s and slug = %s", (tenant_id, fields["course_slug"])
        )
        if not course:
            raise ShopError(400, "course_not_found")


async def update_item(tenant_id: str, code: str, body: dict[str, Any], *, updated_by: int) -> dict[str, Any]:
    item = await get_item(tenant_id, code)
    if not item:
        raise ShopError(404, "item_not_found")
    fields = clean_item_fields(body, kind=str(item["kind"]))
    if not fields:
        return item
    assignments = ", ".join(f"{column} = %s" for column in fields)
    async with tenant_connection(tenant_id) as conn:
        await _check_references(conn, tenant_id, fields)
        row = await fetch_one(
            conn,
            f"""
            update shop_items set {assignments}, updated_by_telegram_user_id = %s, updated_at = now()
            where tenant_id = %s and code = %s
            returning {_ITEM_COLUMNS}
            """,
            (*fields.values(), int(updated_by), tenant_id, item["code"]),
        )
    return row


async def create_item(tenant_id: str, body: dict[str, Any], *, updated_by: int) -> dict[str, Any]:
    data = dict(body)
    code = str(data.pop("code", "") or "").strip().lower()
    kind = str(data.pop("kind", "") or "").strip()
    if not CODE_RE.fullmatch(code) or len(code) > MAX_CODE_CHARS:
        raise ShopError(400, "bad_code")
    if kind not in KINDS:
        raise ShopError(400, "bad_kind", {"allowed": list(KINDS)})
    for required in ("title", "category", "price_wusd"):
        if data.get(required) is None:
            raise ShopError(400, f"{required}_required")
    if kind == "external" and not data.get("external_url"):
        raise ShopError(400, "external_url_required")
    data.setdefault("status", "draft")
    fields = clean_item_fields(data, kind=kind)
    columns = ["tenant_id", "code", "kind", *fields, "updated_by_telegram_user_id"]
    values = [tenant_id, code, kind, *fields.values(), int(updated_by)]
    async with tenant_connection(tenant_id) as conn:
        await _check_references(conn, tenant_id, fields)
        row = await fetch_one(
            conn,
            f"""
            insert into shop_items ({", ".join(columns)})
            values ({", ".join(["%s"] * len(values))})
            on conflict (tenant_id, code) do nothing
            returning {_ITEM_COLUMNS}
            """,
            tuple(values),
        )
    if not row:
        raise ShopError(409, "code_taken")
    return row


async def set_item_status(tenant_id: str, code: str, status: str, *, updated_by: int) -> dict[str, Any]:
    """Бот: «витрина <code> <status>»."""
    return await update_item(tenant_id, code, {"status": status}, updated_by=updated_by)


def admin_item(item: dict[str, Any], *, cover_url: str | None, bot_username: str | None) -> dict[str, Any]:
    return {
        **public_item(item, cover_url=cover_url, bot_username=bot_username),
        "sort_order": int(item["sort_order"]),
        "description_md": item.get("description_md") or "",
        "price_overrides": {
            "rub": major(item["price_rub_minor"]) if item.get("price_rub_minor") is not None else None,
            "byn": major(item["price_byn_minor"]) if item.get("price_byn_minor") is not None else None,
        },
        "course_slug": item.get("course_slug"),
        "file_media_id": item.get("file_media_id"),
        "cover_media_id": item.get("cover_media_id"),
        "external_url": item.get("external_url"),
        "requisites_note": item.get("requisites_note"),
        "delivery_note": item.get("delivery_note"),
        "confirmer": item.get("confirmer"),
        "updated_at": _iso(item.get("updated_at")),
    }


async def admin_catalog(tenant_id: str, *, viewer_id: int) -> dict[str, Any]:
    items = await list_items(tenant_id)
    covers = await _cover_urls(tenant_id, items, viewer_id)
    bot = get_settings().telegram_bot_username
    return {
        "ok": True,
        "statuses": list(STATUSES),
        "items": [admin_item(i, cover_url=covers.get(str(i.get("cover_media_id") or "")), bot_username=bot) for i in items],
    }


async def admin_item_out(tenant_id: str, item: dict[str, Any], *, viewer_id: int) -> dict[str, Any]:
    covers = await _cover_urls(tenant_id, [item], viewer_id)
    return admin_item(item, cover_url=covers.get(str(item.get("cover_media_id") or "")), bot_username=get_settings().telegram_bot_username)
