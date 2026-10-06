"""«Мастерская WWC» в боте (03.10.2026): карточка товара, страна оплаты, заказ
с заявкой у владельца, реквизиты, чек, «Оплачено» / «Отклонить», доставка.

Ссылка с сайта: ``t.me/<бот>?start=shop_<code>[_<ref>]``; ``ref`` — код партнёра
хоста, только для статистики «кто привёл» (доли в v1 нет, владелец 03.10.2026).

Подтверждает только владелец (PLATFORM_BILLING_OWNER_TELEGRAM_ID). Заявка — канал
``shop`` в его форуме «site», тема «Мастерская · <товар> · <имя>»; без форума —
его личка, как обращения по сайту. Администратор Gemini заказов не видит. Карточка
Gemini в витрине ведёт на его собственный поток (``?start=gemini``).

После «Оплачено»: курс — доступ в Академии по Telegram id покупателя, файл — в
кабинете /me/ (раздел «Покупки»), услуга — договорённость о времени в той же заявке.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.referral_bonus.service import ensure_telegram_actor
from app.settings import get_settings
from app.shop.service import (
    BOT_START_PREFIX,
    EXTERNAL_START_PREFIX,
    STATUSES,
    ShopError,
    club_price_wusd_minor,
    confirm_order,
    description_text,
    get_item,
    is_shop_admin,
    is_club_member,
    item_visible,
    known_country,
    list_items,
    open_order,
    price_rub_minor,
    priced_for,
    reject_order,
    release_receipt,
    resolve_partner_ref,
    set_item_status,
    take_receipt,
    unavailable_reason,
    waiting_order_at,
)
from app.support.service import (
    CHANNEL_SHOP,
    get_open_ticket_for_user,
    get_ticket,
    open_or_reuse_ticket,
    record_relayed_message,
    ticket_label,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, copy_telegram_message, send_telegram_text
from app.telegram.money import PAYMENT_BY, PAYMENT_RU, both, money, wwc
from app.telegram.support import (
    _client_label,
    _display,
    _in_forum,
    _open_forum_topic,
    _reply_to_message_id,
    _request_waits_for_file,
    _send_site_header,
    _send_to_admin,
    _site_header_lines,
    is_support_admin,
    show_services,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

_START_RE = re.compile(r"^shop_([a-z0-9]+(?:-[a-z0-9]+)*)(?:_([a-z0-9][a-z0-9_-]{0,40}))?\Z")
_BUY_RE = re.compile(r"^shop:buy:([a-z0-9]+(?:-[a-z0-9]+)*):(RU|BY)(?::([a-z0-9][a-z0-9_-]{0,40}))?\Z")
_DECIDE_RE = re.compile(r"^shop:(paid|reject):([0-9a-f]{32})\Z")
_CARD_RE = re.compile(r"^shop:card:([a-z0-9]+(?:-[a-z0-9]+)*)\Z")
_SHOWCASE_RE = re.compile(r"^/?витрина(?:\s+([a-z0-9-]+)\s+([a-z]+))?\s*$", re.IGNORECASE)
CALLBACK_DATA_LIMIT = 64  # Telegram: callback_data — до 64 байт

SHOP_PAGE_PATH = "/masterskaya/"
PURCHASES_PATH = "/me/#purchases"

NOT_AVAILABLE_TEXT = "Этот товар сейчас недоступен. Всё, что есть, — на странице «Мастерская»: {url}"
NO_OWNER_TEXT = "Мастерская пока не подключена. Напишите Виктору: @sunraysword."
COURSE_NOT_READY_TEXT = "Курс готовится — продажа откроется, когда он будет готов."
RECEIPT_TEXT = "Чек получен. Виктор проверит оплату и подтвердит заказ — сообщение придёт сюда."
RECEIPT_LOST_TEXT = "Чек не удалось передать Виктору. Пришлите его сюда ещё раз через пару минут."
FORBIDDEN_TEXT = "Оплату заказов Мастерской подтверждает только Виктор."
STATUS_WORDS = {"new": "ждёт оплату", "receipt": "ждёт проверки чека", "paid": "оплачен",
                "delivered": "оплачен, доступ открыт", "cancelled": "отменён"}


def _site_base() -> str:
    return str(get_settings().platform_academy_site_base or "https://wwc.best").rstrip("/")


def _owner_id() -> int | None:
    value = get_settings().platform_billing_owner_telegram_id
    return int(value) if value else None


def _is_owner(user_id: int) -> bool:
    owner = _owner_id()
    return owner is not None and int(user_id) == owner


async def _send(chat_id: int, text: str, *, reply_markup: dict | None = None, thread_id: int | None = None) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup, message_thread_id=thread_id,
    )


# ---- start link ------------------------------------------------------------------------


def parse_shop_start_token(token: str) -> tuple[str, str | None] | None:
    """``shop_<code>[_<ref>]`` → (code, ref); ("", None) — битая ссылка; None — не наша."""
    raw = str(token or "").strip().lower()
    if not raw.startswith(BOT_START_PREFIX):
        return None
    match = _START_RE.fullmatch(raw)
    if not match:
        return "", None
    return match.group(1), match.group(2)


def buy_callback(code: str, country: str, ref: str | None) -> str:
    """``shop:buy:<code>:<RU|BY>[:<ref>]``; длинный ref отбрасываем — останется первое касание."""
    data = f"shop:buy:{code}:{country}"
    if ref and len(f"{data}:{ref}".encode()) <= CALLBACK_DATA_LIMIT:
        data = f"{data}:{ref}"
    return data


def _price_line(item: dict[str, Any]) -> str:
    return f"{money(price_rub_minor(item), 'RUB')} или {wwc(int(item['price_wusd_minor']))}"


def card_text(item: dict[str, Any], *, club: bool = False) -> str:
    lines = [str(item["title"])]
    if item.get("subtitle"):
        lines.append(str(item["subtitle"]))
    description = description_text(item)
    if description:
        lines += ["", description]
    club_wusd = club_price_wusd_minor(item)
    if club_wusd is not None and club:
        lines += ["", f"Цена для вас как участника клуба: {_price_line(priced_for(item, club=True))} (обычная — {_price_line(item)})."]
    else:
        lines += ["", f"Цена: {_price_line(item)}."]
        if club_wusd is not None:
            lines.append(f"Участникам клуба — {_price_line(priced_for(item, club=True))}.")
    if unavailable_reason(item) == "course_not_ready":
        lines += ["", COURSE_NOT_READY_TEXT]
    else:
        lines += ["", "Откуда будете оплачивать?"]
    return "\n".join(lines)


def card_keyboard(item: dict[str, Any], *, ref: str | None, country: str | None, club: bool = False) -> dict[str, Any] | None:
    if unavailable_reason(item):
        return None
    item = priced_for(item, club=club)
    verb = "Заказать" if item["kind"] == "service" else "Купить"
    buttons = {
        "RU": {"text": f"{verb} — {money(price_rub_minor(item), 'RUB')} · Россия", "callback_data": buy_callback(item["code"], "RU", ref)},
        "BY": {"text": f"{verb} — {wwc(int(item['price_wusd_minor']))} · Беларусь", "callback_data": buy_callback(item["code"], "BY", ref)},
    }
    order = ("BY", "RU") if country == "BY" else ("RU", "BY")
    return {"inline_keyboard": [[buttons[code]] for code in order]}


async def handle_shop_start(tenant: TenantContext, msg: TelegramMessage, token: str, *, trace_id: str) -> dict[str, Any]:
    """``/start shop_<code>[_<ref>]``: карточка товара и выбор страны оплаты."""
    if msg.chat_type != "private":
        return {"ok": True, "route": "shop", "status": "private_chat_required", "trace_id": trace_id}
    code, ref = parse_shop_start_token(token) or ("", None)
    item = await get_item(tenant.tenant_id, code) if code else None
    if not item_visible(item, admin=is_shop_admin(msg.user_id)):
        await _send(msg.chat_id, NOT_AVAILABLE_TEXT.format(url=_site_base() + SHOP_PAGE_PATH))
        return {"ok": False, "route": "shop", "status": "not_available", "trace_id": trace_id}
    if item["kind"] == "external":
        # Gemini и другие внешние инструменты — свой поток, не заказ Мастерской.
        url = str(item.get("external_url") or "")
        if url.startswith(EXTERNAL_START_PREFIX):
            return await show_services(msg.chat_id, trace_id=trace_id)
        await _send(msg.chat_id, f"{item['title']}: {url}")
        return {"ok": True, "route": "shop", "status": "external_link", "trace_id": trace_id}
    await send_card(tenant.tenant_id, msg.chat_id, msg.user_id, item, ref=ref)
    logger.info("shop_card_shown", extra={"trace_id": trace_id, "item": item["code"], "with_ref": bool(ref)})
    return {"ok": True, "route": "shop", "status": "card", "item": item["code"], "trace_id": trace_id}


async def send_card(tenant_id: str, chat_id: int, user_id: int, item: dict[str, Any], *, ref: str | None) -> None:
    """Карточка с ценой для этого человека (участнику клуба — клубная) и кнопками стран."""
    club = await is_club_member(tenant_id, user_id) if club_price_wusd_minor(item) is not None else False
    country = await known_country(tenant_id, user_id)
    await _send(chat_id, card_text(item, club=club), reply_markup=card_keyboard(item, ref=ref, country=country, club=club))


# ---- order -----------------------------------------------------------------------------


def payment_text(item: dict[str, Any], order: dict[str, Any], ticket: dict[str, Any]) -> str:
    details = PAYMENT_RU if order["currency"] == "RUB" else PAYMENT_BY
    lines = [
        f"Заказ {ticket_label(ticket)}: {order['item_title']} — {both(int(order['amount_minor']), str(order['currency']))}.",
        details,
    ]
    if item.get("requisites_note"):
        lines.append(str(item["requisites_note"]))
    lines.append("После перевода пришлите сюда скриншот чека. Вопросы пишите сюда же — ответит Виктор.")
    return "\n".join(lines)


def _who_brought(order: dict[str, Any]) -> str:
    ref = order.get("partner_ref_code")
    if not ref:
        return "Кто привёл: —"
    how = "по ссылке партнёра" if order.get("partner_ref_source") == "link" else "первое касание"
    return f"Кто привёл: {ref} ({how})"


async def _buy(
    tenant: TenantContext, callback: TelegramCallbackQuery, code: str, country: str, ref: str | None, *, trace_id: str
) -> dict[str, Any]:
    item = await get_item(tenant.tenant_id, code)
    if not item_visible(item, admin=is_shop_admin(callback.user_id)) or unavailable_reason(item):
        await _send(callback.chat_id, NOT_AVAILABLE_TEXT.format(url=_site_base() + SHOP_PAGE_PATH))
        return {"ok": False, "route": "shop", "status": "not_available", "trace_id": trace_id}
    owner = _owner_id()
    if owner is None:
        await _send(callback.chat_id, NO_OWNER_TEXT)
        return {"ok": False, "route": "shop", "status": "no_owner", "trace_id": trace_id}
    await ensure_telegram_actor(
        tenant.tenant_id, telegram_user_id=callback.user_id, telegram_chat_id=callback.chat_id, raw_update=callback.raw
    )
    partner_ref, source = await resolve_partner_ref(tenant.tenant_id, telegram_user_id=callback.user_id, link_ref=ref)
    ticket = await open_or_reuse_ticket(
        tenant.tenant_id,
        channel_code=CHANNEL_SHOP,
        offer_code=item["code"],
        offer_title=item["title"],
        user_telegram_user_id=callback.user_id,
        user_chat_id=callback.chat_id,
        user_display=_display(callback),
        admin_telegram_user_id=owner,
    )
    if ticket["created"]:
        ticket = await _open_forum_topic(tenant, ticket)
    club = await is_club_member(tenant.tenant_id, callback.user_id) if club_price_wusd_minor(item) is not None else False
    order = await open_order(
        tenant.tenant_id, item=item, telegram_user_id=callback.user_id, ticket_id=str(ticket["ticket_id"]),
        country_code=country, partner_ref_code=partner_ref, partner_ref_source=source, club=club,
    )
    price = both(int(order["amount_minor"]), str(order["currency"]))
    if club:
        price += " · клубная цена"  # иначе 7 500 ₽ за курс за 10 000 похоже на недоплату
    if ticket["created"]:
        hint = (
            "Пишите в эту тему — ответ уйдёт покупателю." if _in_forum(ticket)
            else "Ответьте на это сообщение (Reply) — ответ уйдёт покупателю."
        )
        header = [*_site_header_lines(ticket, None), f"Заказ: {order['item_title']} — {price}", _who_brought(order), "", "Ждём чек. " + hint]
        delivered = await _send_site_header(ticket, "\n".join(header))
        first_line = header[0]
    else:
        what = "новый заказ" if order["created"] else "снова нажал «Купить»"
        first_line = f"{_client_label(ticket)} · {what}: {order['item_title']} — {price}"
        delivered = await _send_to_admin(ticket, first_line + "\n" + _who_brought(order))
    await record_relayed_message(
        tenant.tenant_id,
        ticket_id=str(ticket["ticket_id"]),
        direction="system",
        text=first_line,
        delivered_chat_id=int(ticket["forum_chat_id"]) if _in_forum(ticket) else int(ticket["admin_telegram_user_id"]),
        delivered_message_id=delivered.get("message_id"),
    )
    if order["status"] == "receipt":
        # Чек уже пришёл: реквизиты второй раз не нужны, а владельцу — снова кнопки
        # (вдруг первое сообщение с ними потерялось).
        await _send_decision(tenant, order, ticket, _decision_text(order, ticket, "Покупатель снова нажал «Купить»; чек — выше в заявке."))
        await _send(callback.chat_id, RECEIPT_TEXT)
    else:
        await _send(callback.chat_id, payment_text(item, order, ticket))
    logger.info(
        "shop_order_opened",
        extra={"trace_id": trace_id, "item": item["code"], "order_created": order["created"], "ticket": ticket_label(ticket),
               "country": country, "ref_source": order.get("partner_ref_source")},
    )
    return {"ok": True, "route": "shop", "status": "order_opened", "order_id": order["order_id"], "ticket": ticket_label(ticket), "trace_id": trace_id}


# ---- receipt ---------------------------------------------------------------------------


def _decision_keyboard(order: dict[str, Any]) -> dict[str, Any]:
    token = str(order["order_id"]).replace("-", "")
    amount = money(int(order["amount_minor"]), str(order["currency"]))
    return {"inline_keyboard": [[
        {"text": f"Оплачено {amount}", "callback_data": f"shop:paid:{token}"},
        {"text": "Отклонить", "callback_data": f"shop:reject:{token}"},
    ]]}


def _decision_targets(ticket: dict[str, Any] | None) -> list[tuple[int, int | None]]:
    """Куда нести чек с кнопками: тема заявки (или личка, где идёт заявка), запасной путь —
    личка владельца (тему удалили, заявку закрыли, бот другой среды)."""
    targets: list[tuple[int, int | None]] = []
    if ticket is not None and ticket["status"] == "open":
        if _in_forum(ticket):
            targets.append((int(ticket["forum_chat_id"]), int(ticket["forum_thread_id"])))
        else:
            targets.append((int(ticket["admin_telegram_user_id"]), None))
    owner = _owner_id()
    if owner is not None and (owner, None) not in targets:
        targets.append((owner, None))
    return targets


async def _send_decision(
    tenant: TenantContext, order: dict[str, Any], ticket: dict[str, Any] | None, text: str, *, msg: TelegramMessage | None = None
) -> bool:
    """Чек (копия ``msg``) и строка с «Оплачено <сумма>» / «Отклонить» владельцу.
    False — не дошло никуда: тогда заказ нельзя держать в «receipt» без кнопок."""
    for chat, thread in _decision_targets(ticket):
        try:
            if msg is not None:
                await copy_telegram_message(
                    chat_id=str(chat), from_chat_id=str(msg.chat_id), message_id=msg.message_id,
                    bot_token=current_bot_binding().bot_token, message_thread_id=thread,
                )
            sent = await _send(chat, text, reply_markup=_decision_keyboard(order), thread_id=thread)
        except Exception:
            logger.warning("shop_decision_delivery_failed", extra={"order_id": order["order_id"]}, exc_info=True)
            continue
        if not sent.get("ok"):
            logger.warning("shop_decision_delivery_failed", extra={"order_id": order["order_id"]})
            continue
        if ticket is not None and msg is not None:
            await record_relayed_message(
                tenant.tenant_id, ticket_id=str(ticket["ticket_id"]), direction="user_to_admin", text="Чек по заказу",
                telegram_file_id=msg.file_id, source_chat_id=msg.chat_id, source_message_id=msg.message_id,
                delivered_chat_id=chat, delivered_message_id=sent.get("message_id"),
            )
        return True
    return False


def _decision_text(order: dict[str, Any], ticket: dict[str, Any] | None, note: str) -> str:
    who = _client_label(ticket) if ticket else f"Покупатель id {int(order['telegram_user_id'])}"
    amount = money(int(order["amount_minor"]), str(order["currency"]))
    return "\n".join([who, f"Чек по заказу: {order['item_title']} — {amount}", note])


async def _receipt_belongs_to_shop(tenant: TenantContext, msg: TelegramMessage, since: Any) -> bool:
    """Фото — чек Мастерской, только если его не ждёт другой поток: продление или анкета
    сайта ждут файл (30.09.2026), человек пишет в другой открытой заявке (Gemini,
    «Поддержка») позже, чем оформил заказ, или владелец/администратор отвечает Reply."""
    if _reply_to_message_id(msg) is not None and (is_support_admin(msg.user_id) or _is_owner(msg.user_id)):
        return False
    if await _request_waits_for_file(tenant, msg):
        return False
    ticket = await get_open_ticket_for_user(tenant.tenant_id, user_telegram_user_id=msg.user_id)
    if ticket and str(ticket.get("channel_code") or "") != CHANNEL_SHOP and ticket["last_message_at"] > since:
        return False
    return True


async def try_handle_shop_message(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Личный чат: «витрина» владельца и чек покупателя, у которого заказ ждёт оплату.

    Идёт раньше туннеля поддержки: иначе чек ушёл бы в заявку обычным вложением,
    без кнопки «Оплачено» (как было с заявками на сайт 30.09.2026)."""
    if msg.chat_type != "private":
        return None
    text = str(msg.text or "").strip()
    if not msg.file_id and _SHOWCASE_RE.fullmatch(text) and is_shop_admin(msg.user_id):
        return await _showcase_command(tenant, msg, trace_id=trace_id)
    if not msg.file_id:
        return None
    try:
        since = await waiting_order_at(tenant.tenant_id, telegram_user_id=msg.user_id)
    except RuntimeError as exc:
        # Routing checks run without a database pool (same rule as support.py).
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if since is None or not await _receipt_belongs_to_shop(tenant, msg, since):
        return None
    orders = await take_receipt(tenant.tenant_id, telegram_user_id=msg.user_id, file_id=msg.file_id)
    if not orders:
        return None
    delivered = 0
    for order in orders:
        ticket = await get_ticket(tenant.tenant_id, ticket_id=order["ticket_id"]) if order.get("ticket_id") else None
        if await _send_decision(tenant, order, ticket, _decision_text(order, ticket, "Чек выше."), msg=msg):
            delivered += 1
        else:
            # Без кнопок у владельца заказ застрял бы в «receipt»: пусть снова ждёт чек.
            await release_receipt(tenant.tenant_id, order_id=order["order_id"])
    if not delivered:
        await _send(msg.chat_id, RECEIPT_LOST_TEXT)
        logger.warning("shop_receipt_not_delivered", extra={"trace_id": trace_id, "orders": [o["order_id"] for o in orders]})
        return {"ok": False, "route": "shop", "status": "receipt_not_delivered", "trace_id": trace_id}
    await _send(msg.chat_id, RECEIPT_TEXT)
    logger.info("shop_receipt_received", extra={"trace_id": trace_id, "orders": [o["order_id"] for o in orders]})
    return {"ok": True, "route": "shop", "status": "receipt", "orders": delivered, "trace_id": trace_id}


# ---- «Оплачено» / «Отклонить» ----------------------------------------------------------


def _buyer_after_payment(result: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    order, item = result["order"], result.get("item") or {}
    title = str(order["item_title"])
    kind = str(item.get("kind") or "")
    keyboard = None
    if kind == "course" and result["delivered"]:
        lines = [f"Оплата подтверждена: {title}. Доступ в Академии открыт."]
        url = f"{_site_base()}/academy/?course={item['course_slug']}"
        keyboard = {"inline_keyboard": [[{"text": "Открыть курс", "url": url}]]}
    elif kind == "course":
        lines = [f"Оплата подтверждена: {title}. Доступ к курсу откроем отдельно — напишем сюда."]
    elif kind == "digital":
        lines = [f"Оплата подтверждена: {title}. Файл — в личном кабинете, раздел «Покупки»."]
        keyboard = {"inline_keyboard": [[{"text": "Открыть кабинет", "url": _site_base() + PURCHASES_PATH}]]}
    else:
        lines = [f"Оплата подтверждена: {title}."]
        if not item.get("delivery_note"):
            lines.append("Договоримся о времени здесь, в этом чате.")
    if item.get("delivery_note"):
        lines.append(str(item["delivery_note"]))
    return "\n".join(lines), keyboard


def _owner_after_payment(result: dict[str, Any]) -> str:
    order, item = result["order"], result.get("item") or {}
    head = f"Оплачено: {order['item_title']} — {money(int(order['amount_minor']), str(order['currency']))}."
    kind = str(item.get("kind") or "")
    if kind == "course":
        if result["delivered"]:
            return head + " Курс открыт покупателю."
        return head + " ⚠️ Курс не найден в Академии — доступ не открыт, откройте вручную."
    if kind == "digital":
        if not item.get("file_media_id"):
            return head + " ⚠️ Файл ещё не загружен: покупатель увидит его в кабинете, когда файл появится в каталоге."
        return head + " Файл доступен в кабинете покупателя."
    return head + " Договоритесь о времени в этой заявке."


async def _decide(
    tenant: TenantContext, callback: TelegramCallbackQuery, action: str, token: str, *, trace_id: str
) -> dict[str, Any]:
    chat, thread = callback.chat_id, callback.thread_id
    if not _is_owner(callback.user_id):
        await _send(chat, FORBIDDEN_TEXT, thread_id=thread)
        return {"ok": False, "route": "shop_decision", "status": "forbidden", "trace_id": trace_id}
    order_id = str(uuid.UUID(token))
    try:
        result = await (reject_order(tenant.tenant_id, order_id=order_id) if action == "reject"
                        else confirm_order(tenant.tenant_id, order_id=order_id, paid_by=callback.user_id))
    except ShopError:
        await _send(chat, "Заказ не найден.", thread_id=thread)
        return {"ok": False, "route": "shop_decision", "status": "not_found", "trace_id": trace_id}
    order = result["order"]
    if result["idempotent"]:
        await _send(chat, f"Заказ «{order['item_title']}» уже {STATUS_WORDS.get(order['status'], order['status'])}.", thread_id=thread)
        return {"ok": True, "route": "shop_decision", "status": "already_decided", "trace_id": trace_id}
    ticket = await get_ticket(tenant.tenant_id, ticket_id=order["ticket_id"]) if order.get("ticket_id") else None
    buyer_chat = int(ticket["user_chat_id"]) if ticket else int(order["telegram_user_id"])
    if action == "reject":
        await _send(buyer_chat, f"Оплату по заказу «{order['item_title']}» не удалось подтвердить. Если это ошибка — напишите сюда, Виктор ответит.")
        await _send(chat, f"Заказ отклонён: {order['item_title']}.", thread_id=thread)
        logger.info("shop_order_rejected", extra={"trace_id": trace_id, "order_id": order["order_id"]})
        return {"ok": True, "route": "shop_decision", "status": "rejected", "trace_id": trace_id}
    text, keyboard = _buyer_after_payment(result)
    await _send(buyer_chat, text, reply_markup=keyboard)
    await _send(chat, _owner_after_payment(result), thread_id=thread)
    logger.info(
        "shop_order_paid",
        extra={"trace_id": trace_id, "order_id": order["order_id"], "item": order["item_code"], "delivered": result["delivered"]},
    )
    return {"ok": True, "route": "shop_decision", "status": "paid", "delivered": result["delivered"], "trace_id": trace_id}


async def try_handle_shop_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    data = str(callback.data or "")
    if not data.startswith("shop:"):
        return None
    decide = _DECIDE_RE.fullmatch(data)
    buy = None if decide else _BUY_RE.fullmatch(data)
    card = None if decide or buy else _CARD_RE.fullmatch(data)
    if not decide and not buy and not card:
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    if decide:
        # Кнопки владельца: в теме заявки (форум «site») или в его личке.
        return await _decide(tenant, callback, decide.group(1), decide.group(2), trace_id=trace_id)
    if callback.chat_type != "private":
        return {"ok": True, "route": "shop", "status": "private_chat_required", "trace_id": trace_id}
    if card:
        # «Купить курс» на замке Академии → та же карточка, что по ссылке shop_<code>.
        item = await get_item(tenant.tenant_id, card.group(1))
        if not item_visible(item, admin=is_shop_admin(callback.user_id)) or item["kind"] == "external":
            await _send(callback.chat_id, NOT_AVAILABLE_TEXT.format(url=_site_base() + SHOP_PAGE_PATH))
            return {"ok": False, "route": "shop", "status": "not_available", "trace_id": trace_id}
        await send_card(tenant.tenant_id, callback.chat_id, callback.user_id, item, ref=None)
        return {"ok": True, "route": "shop", "status": "card", "item": item["code"], "trace_id": trace_id}
    code, country, ref = buy.groups()
    return await _buy(tenant, callback, code, country, ref, trace_id=trace_id)


# ---- «витрина» (owner, private chat) ---------------------------------------------------


async def _showcase_command(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any]:
    """«витрина» — список товаров со статусами; «витрина <code> <status>» — сменить статус."""
    match = _SHOWCASE_RE.fullmatch(str(msg.text or "").strip())
    code, status = (match.group(1), (match.group(2) or "").lower()) if match else (None, "")
    if code:
        if status not in STATUSES:
            await _send(msg.chat_id, f"Статусы: {', '.join(STATUSES)}. Например: витрина lending published")
            return {"ok": False, "route": "shop_showcase", "status": "bad_status", "trace_id": trace_id}
        try:
            item = await set_item_status(tenant.tenant_id, code.lower(), status, updated_by=msg.user_id)
        except ShopError:
            await _send(msg.chat_id, f"Товара {code} нет. Список: «витрина».")
            return {"ok": False, "route": "shop_showcase", "status": "not_found", "trace_id": trace_id}
        await _send(msg.chat_id, f"{item['code']}: {item['status']}.")
        logger.info("shop_item_status_set", extra={"trace_id": trace_id, "item": item["code"], "status": item["status"]})
        return {"ok": True, "route": "shop_showcase", "status": "updated", "trace_id": trace_id}
    items = await list_items(tenant.tenant_id)
    lines = ["Мастерская — товары и статусы:"]
    lines += [f"{i['code']} · {i['status']} · {i['title']} · {money(price_rub_minor(i), 'RUB')}" for i in items]
    lines += ["", "Сменить: витрина <code> published|pilot|draft|archived"]
    await _send(msg.chat_id, "\n".join(lines))
    return {"ok": True, "route": "shop_showcase", "status": "listed", "trace_id": trace_id}
