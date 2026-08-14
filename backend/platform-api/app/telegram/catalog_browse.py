"""Telegram catalog browse — paginated product list and callback actions."""

from __future__ import annotations

import logging
import math
from typing import Any

from app.advisor.service import handle_structured_query
from app.advisor.sql import repository as repo
from app.db import tenant_connection
from app.settings import get_settings
from app.telegram.delivery import (
    answer_callback_query,
    deliver_structured_advisor_response,
    send_telegram_text,
)
from app.telegram.navigation import (
    ParsedCallback,
    catalog_list_inline_keyboard,
    clamp_page,
    clamp_page_size,
    main_menu_reply_keyboard,
    menu_intent_to_advisor_question,
    parse_callback_data,
    product_action_question,
    product_actions_inline_keyboard,
    resolve_menu_text_intent,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 8
SAFE_MENU_TEXT = (
    "Выберите раздел меню или нажмите «📦 Товары», чтобы открыть каталог."
)


async def _deliver_navigation_text(
    chat_id: int | str,
    text: str,
    *,
    inline_markup: dict | None = None,
    include_main_menu: bool = True,
) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not text.strip():
        return
    reply_markup = inline_markup
    if include_main_menu and inline_markup is None:
        reply_markup = main_menu_reply_keyboard()
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text.strip(),
        bot_token=settings.telegram_bot_token,
        reply_markup=reply_markup,
    )
    if include_main_menu and inline_markup is not None:
        await send_telegram_text(
            chat_id=str(chat_id),
            text="Разделы меню:",
            bot_token=settings.telegram_bot_token,
            reply_markup=main_menu_reply_keyboard(),
        )


async def _ack_callback(callback_query_id: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    await answer_callback_query(
        callback_query_id=callback_query_id,
        bot_token=settings.telegram_bot_token,
    )


async def _run_advisor_question(
    tenant: TenantContext,
    chat_id: int | str,
    question: str,
    trace_id: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session": f"telegram:{chat_id}",
        "question": question,
        "surface": "telegram",
        "country": "BY",
        "language": "ru",
    }
    core_response = await handle_structured_query(tenant, body, trace_id)
    settings = get_settings()
    if settings.telegram_bot_token:
        await deliver_structured_advisor_response(
            chat_id,
            core_response,
            bot_token=settings.telegram_bot_token,
            reply_markup=main_menu_reply_keyboard(),
        )
    return core_response


async def render_catalog_page(
    tenant: TenantContext,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[str, dict[str, Any] | None]:
    safe_page = clamp_page(page)
    safe_size = clamp_page_size(page_size)
    async with tenant_connection(tenant.tenant_id) as conn:
        total = await repo.count_catalog_products(conn, tenant.tenant_id)
        if total == 0:
            return (
                "Каталог пока пуст. Напишите название товара или артикул.",
                main_menu_reply_keyboard(),
            )
        total_pages = max(1, math.ceil(total / safe_size))
        if safe_page > total_pages:
            safe_page = total_pages
        products = await repo.list_catalog_products(conn, tenant.tenant_id, safe_page, safe_size)
    lines = [f"Каталог WHIEDA — страница {safe_page} из {total_pages}", ""]
    for index, product in enumerate(products, start=1):
        name = str(product.get("canonical_name") or product.get("sku") or "").strip()
        lines.append(f"{index}. {name}")
    lines.append("")
    lines.append("Выберите товар кнопкой ниже.")
    markup = catalog_list_inline_keyboard(
        page=safe_page,
        total_pages=total_pages,
        products=products,
    )
    return "\n".join(lines), markup


async def handle_catalog_products(
    tenant: TenantContext,
    chat_id: int | str,
    *,
    page: int = 1,
    trace_id: str = "",
) -> dict[str, Any]:
    text, inline_markup = await render_catalog_page(tenant, page=page)
    await _deliver_navigation_text(chat_id, text, inline_markup=inline_markup, include_main_menu=True)
    return {"ok": True, "route": "catalog_products", "page": page, "trace_id": trace_id}


async def handle_catalog_sku(
    tenant: TenantContext,
    chat_id: int | str,
    sku: str,
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    async with tenant_connection(tenant.tenant_id) as conn:
        product = await repo.resolve_catalog_product_by_sku(conn, tenant.tenant_id, sku)
    if not product:
        await handle_safe_menu_fallback(tenant, chat_id, trace_id=trace_id)
        return {"ok": True, "route": "catalog_sku_missing", "trace_id": trace_id}
    name = str(product.get("canonical_name") or sku).strip()
    text = f"{name}\n\nВыберите действие:"
    await _deliver_navigation_text(
        chat_id,
        text,
        inline_markup=product_actions_inline_keyboard(sku),
        include_main_menu=True,
    )
    return {"ok": True, "route": "catalog_sku", "sku": sku, "trace_id": trace_id}


async def handle_product_action(
    tenant: TenantContext,
    chat_id: int | str,
    action: str,
    sku: str,
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    async with tenant_connection(tenant.tenant_id) as conn:
        product = await repo.resolve_catalog_product_by_sku(conn, tenant.tenant_id, sku)
    if not product:
        await handle_safe_menu_fallback(tenant, chat_id, trace_id=trace_id)
        return {"ok": True, "route": "product_action_missing", "trace_id": trace_id}
    name = str(product.get("canonical_name") or sku).strip()
    if action == "compare":
        text = (
            f"Для сравнения напишите, например:\n"
            f"сравни {name} и [второй товар]"
        )
        await _deliver_navigation_text(chat_id, text, include_main_menu=True)
        return {"ok": True, "route": "product_compare_hint", "sku": sku, "trace_id": trace_id}
    question = product_action_question(action, name)
    if not question:
        await handle_safe_menu_fallback(tenant, chat_id, trace_id=trace_id)
        return {"ok": True, "route": "product_action_invalid", "trace_id": trace_id}
    core_response = await _run_advisor_question(tenant, chat_id, question, trace_id)
    return {
        "ok": True,
        "route": "product_action",
        "action": action,
        "sku": sku,
        "answer_mode": core_response.get("answer_mode"),
        "trace_id": trace_id,
    }


async def handle_safe_menu_fallback(
    tenant: TenantContext,
    chat_id: int | str,
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    await _deliver_navigation_text(chat_id, SAFE_MENU_TEXT, include_main_menu=True)
    return {"ok": True, "route": "navigation_fallback", "trace_id": trace_id}


async def handle_main_menu(
    tenant: TenantContext,
    chat_id: int | str,
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    _ = tenant
    lines = [
        "Главное меню WHIEDA:",
        "",
        "📦 Товары — каталог",
        "🧮 Калькулятор — расчёт корзины",
        "📈 Бизнес — вход и маркетинг",
        "🏢 О компании",
        "🧭 Подбор — стартовый набор",
        "📅 Встречи — события",
    ]
    await _deliver_navigation_text(chat_id, "\n".join(lines), include_main_menu=True)
    return {"ok": True, "route": "main_menu", "trace_id": trace_id}


async def dispatch_parsed_callback(
    tenant: TenantContext,
    chat_id: int | str,
    parsed: ParsedCallback,
    *,
    trace_id: str = "",
) -> dict[str, Any]:
    if parsed.kind == "nav_products":
        return await handle_catalog_products(tenant, chat_id, page=1, trace_id=trace_id)
    if parsed.kind == "nav_menu":
        return await handle_main_menu(tenant, chat_id, trace_id=trace_id)
    if parsed.kind == "catalog_page" and parsed.page is not None:
        return await handle_catalog_products(tenant, chat_id, page=parsed.page, trace_id=trace_id)
    if parsed.kind == "catalog_sku" and parsed.sku:
        return await handle_catalog_sku(tenant, chat_id, parsed.sku, trace_id=trace_id)
    if parsed.kind == "product_action" and parsed.action and parsed.sku:
        return await handle_product_action(
            tenant,
            chat_id,
            parsed.action,
            parsed.sku,
            trace_id=trace_id,
        )
    return await handle_safe_menu_fallback(tenant, chat_id, trace_id=trace_id)


async def handle_navigation_text(
    tenant: TenantContext,
    msg: TelegramMessage,
    trace_id: str,
) -> dict[str, Any] | None:
    intent = resolve_menu_text_intent(msg.text)
    if not intent:
        return None
    if intent == "nav_products":
        return await handle_catalog_products(tenant, msg.chat_id, page=1, trace_id=trace_id)
    advisor_question = menu_intent_to_advisor_question(intent)
    if not advisor_question:
        return await handle_safe_menu_fallback(tenant, msg.chat_id, trace_id=trace_id)
    core_response = await _run_advisor_question(tenant, msg.chat_id, advisor_question, trace_id)
    return {
        "ok": True,
        "route": "navigation_text",
        "intent": intent,
        "answer_mode": core_response.get("answer_mode"),
        "trace_id": trace_id,
    }


async def handle_callback_query(
    tenant: TenantContext,
    callback: TelegramCallbackQuery,
    trace_id: str,
) -> dict[str, Any]:
    await _ack_callback(callback.callback_query_id)
    parsed = parse_callback_data(callback.data)
    if not parsed:
        return await handle_safe_menu_fallback(tenant, callback.chat_id, trace_id=trace_id)
    return await dispatch_parsed_callback(
        tenant,
        callback.chat_id,
        parsed,
        trace_id=trace_id,
    )
