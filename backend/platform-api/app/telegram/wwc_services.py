"""What WWC itself sells, asked in plain words.

Until now «хочу заказать сайт» or «сколько стоит подписка» fell through to the
WHIEDA advisor, which knows the catalogue but not our own offer, and answered
«Не понял вопрос» (owner, 22.09.2026). Both questions now get a straight answer
and the buttons that start the order.
"""

from __future__ import annotations

import re
from typing import Any

from app.telegram.delivery import send_telegram_text
from app.telegram.bindings import current_bot_binding
from app.telegram.support import SERVICES_CARD_CALLBACK
from app.tenancy import TenantContext
from app.telegram.update_parser import TelegramMessage

_SITE_ORDER_RE = re.compile(
    r"(заказать|закажу|хочу|нужен|нужна|нуженли|сделать|создать|подключить|оформить)\W+"
    r"(свой\s+|личный\s+|персональный\s+)?(сайт|платформ|визитк)",
    re.I,
)
# «мой сайт» is not here on purpose: that is a partner asking about his own site.
_SITE_ORDER_SHORT = {"сайт", "хочу сайт", "нужен сайт", "заказать сайт", "заказ сайта"}

_PRICE_WORDS_RE = re.compile(r"(сколько\s+стоит|цена|стоимость|почём|почем|прайс|тариф)", re.I)
_OUR_SERVICE_RE = re.compile(
    r"(подписк|платформ|личн\w*\s+сайт|свой\s+сайт|сайт\s+wwc|wwc|клуб|академи|настройк\w*\s+сайта)",
    re.I,
)

PRICE_TEXT = (
    "Что есть в WWC (1 WWC$ = 100 ₽):\n\n"
    "🌐 Платформа (личный сайт)\n"
    "• 3 месяца — 30 WWC$ (3 000 ₽)\n"
    "• 6 месяцев — 54 WWC$ (5 400 ₽)\n"
    "• 12 месяцев — 96 WWC$ (9 600 ₽)\n"
    "• настройка сайта, разово — 20 WWC$ (2 000 ₽)\n\n"
    "👥 Клуб — 40 WWC$ в месяц (120 WWC$ за 3 месяца)\n"
    "📦 Пакет «Платформа + Клуб» на 3 месяца — 105 WWC$ (10 500 ₽)\n\n"
    "🤖 Gemini Pro — отдельная услуга, оформляется через администратора.\n\n"
    "Оплату подтверждает Виктор; после подтверждения доступ открывается сразу."
)

SITE_ORDER_TEXT = (
    "Сделаем ваш сайт WWC: страница с вашим именем, фото и текстом о вас, "
    "каталог и приём заявок. Оформление идёт прямо здесь: страна → адрес сайта "
    "→ фото → текст о себе → оплата.\n\n"
    "Сайт + настройка — 5 000 ₽ (50 WWC$).\n"
    "Платформа + Клуб на 3 месяца — 10 500 ₽ (105 WWC$)."
)


def is_site_order_request(text: str | None) -> bool:
    value = str(text or "").strip().lower().rstrip("?!.")
    if not value or value.startswith("/"):
        return False
    if value in _SITE_ORDER_SHORT:
        return True
    return bool(_SITE_ORDER_RE.search(value))


def is_our_price_question(text: str | None) -> bool:
    value = str(text or "").strip().lower()
    if not value or value.startswith("/"):
        return False
    return bool(_PRICE_WORDS_RE.search(value) and _OUR_SERVICE_RE.search(value))


def _keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Заказать сайт WWC", "callback_data": "site:create"}],
            [{"text": "Подключить Gemini Pro", "callback_data": SERVICES_CARD_CALLBACK}],
        ]
    }


async def try_handle_wwc_service_text(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """Answer «закажу сайт» / «сколько стоит подписка» before the advisor sees them."""
    if msg.chat_type != "private" or not msg.text:
        return None
    if is_site_order_request(msg.text):
        route, text = "wwc_site_order", SITE_ORDER_TEXT
    elif is_our_price_question(msg.text):
        route, text = "wwc_prices", PRICE_TEXT
    else:
        return None
    await send_telegram_text(
        chat_id=str(msg.chat_id),
        text=text,
        bot_token=current_bot_binding().bot_token,
        reply_markup=_keyboard(),
    )
    return {"ok": True, "route": route, "trace_id": trace_id}
