"""Слово СИСТЕМА в боте WWC (Telegram и Max): видео-тур с двумя кнопками (срез 8 бот-воронки, 30.10.2026).

Человек пишет «система» (любой регистр, можно с «!» или «.» на конце) → бот присылает видео-тур ``system_tour``
из хранилища медиа (app/bot_media) с подписью ``SYSTEM_TOUR_CAPTION`` и кнопками «Живая встреча» и «Личный разбор».
Тур ещё не загружен (или канал не принял файл) → текст ``SYSTEM_TOUR_PENDING`` с теми же кнопками: человек
не остаётся без ответа.

Кнопка «Живая встреча» (``funnel:meet``) открывает список встреч для записи — это обработчик гостя
(app/funnel/guest_bot.py); «Личный разбор» (``funnel:consult``) пока отвечает текстом-заглушкой: запись на разбор —
отдельный срез воронки. В Max обработчик ``funnel:`` регистрируется в app/max/processor.py, в Telegram —
в цепочке callback процессора.

Слово работает только в личном чате бота WWC (тенант ``whieda``): боты других тенантов и витрин отвечают
на «система» как раньше.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.bot_media.service import send_media
from app.funnel import texts
from app.max.client import max_button_callback, send_max_text
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.funnel")

FUNNEL_TENANT_ID = "whieda"
CALLBACK_PREFIX = "funnel:"
SYSTEM_WORD_RE = re.compile(r"^\s*система\s*[!.]?\s*$", re.IGNORECASE)

# «Живая встреча» (funnel:meet) с 30.10.2026 открывает список встреч для записи (app/funnel/guest_bot.py);
# «Личный разбор» пока отвечает заглушкой — запись на разбор — отдельный срез воронки.
_PLACEHOLDERS = {
    texts.FUNNEL_CONSULT_CALLBACK: texts.FUNNEL_CONSULT_PLACEHOLDER,
}


def is_system_word(text: str | None) -> bool:
    return bool(SYSTEM_WORD_RE.match(str(text or "")))


def telegram_markup() -> dict[str, Any]:
    """Inline-клавиатура Telegram: две кнопки, каждая в своём ряду."""
    return {
        "inline_keyboard": [
            [{"text": texts.FUNNEL_MEET_BUTTON, "callback_data": texts.FUNNEL_MEET_CALLBACK}],
            [{"text": texts.FUNNEL_CONSULT_BUTTON, "callback_data": texts.FUNNEL_CONSULT_CALLBACK}],
        ]
    }


def max_buttons() -> list[list[dict[str, Any]]]:
    """Ряды кнопок Max (max_button_callback): те же две кнопки."""
    return [
        [max_button_callback(texts.FUNNEL_MEET_BUTTON, texts.FUNNEL_MEET_CALLBACK)],
        [max_button_callback(texts.FUNNEL_CONSULT_BUTTON, texts.FUNNEL_CONSULT_CALLBACK)],
    ]


# ---- Telegram --------------------------------------------------------------------------------------


async def try_handle_system_word(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """«система» в личном чате бота WWC. Не то слово, чужой тенант или группа — ``None``: текст идёт дальше."""
    if tenant.tenant_id != FUNNEL_TENANT_ID or msg.chat_type != "private" or not is_system_word(msg.text):
        return None
    return await send_tour_telegram(tenant, msg.chat_id, trace_id=trace_id)


async def send_tour_telegram(tenant: TenantContext, chat_id: int, *, trace_id: str | None = None) -> dict[str, Any]:
    """Видео-тур в личный чат Telegram с кнопками «Живая встреча» и «Личный разбор» (или текст-заглушка, если тура нет).
    Им пользуются слово СИСТЕМА и кнопка «Показ системы» в меню гостя."""
    base: dict[str, Any] = {"ok": True, "route": "funnel_system", "trace_id": trace_id}
    try:
        result = await send_media(
            None, tenant.tenant_id, texts.SYSTEM_TOUR_KEY, channel="telegram", chat_id=chat_id,
            caption=texts.SYSTEM_TOUR_CAPTION, reply_markup=telegram_markup(),
        )
    except Exception:  # noqa: BLE001 — человек не должен остаться без ответа из-за базы или сети
        logger.exception("funnel_system_media_error", extra={"trace_id": trace_id})
        result = {"ok": False, "status": "error"}
    if result.get("ok"):
        return {**base, "status": "tour", "kind": result.get("kind")}
    await send_telegram_text(
        chat_id=str(chat_id), text=texts.SYSTEM_TOUR_PENDING,
        bot_token=current_bot_binding().bot_token, reply_markup=telegram_markup(),
    )
    return {**base, "status": "pending", "reason": result.get("status")}


async def try_handle_funnel_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """Нажатие «Живая встреча» / «Личный разбор» под туром: пока текст-заглушка."""
    placeholder = _PLACEHOLDERS.get(callback.data)
    if placeholder is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    bot_token = current_bot_binding().bot_token
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=bot_token)
    if callback.chat_type != "private":
        return {"ok": True, "route": "funnel_callback", "status": "private_chat_required", "trace_id": trace_id}
    await send_telegram_text(chat_id=str(callback.chat_id), text=placeholder, bot_token=bot_token)
    return {"ok": True, "route": "funnel_callback", "status": callback.data.removeprefix(CALLBACK_PREFIX), "trace_id": trace_id}


# ---- Max -------------------------------------------------------------------------------------------


async def handle_system_word_max(tenant: TenantContext, event: Any, trace_id: str) -> dict[str, Any]:
    """«система» в личке Max: тур по токену Max (при первой отправке файл уедет из Telegram в Max) или заглушка."""
    return await send_tour_max(tenant, chat_id=event.chat_id or None, user_id=event.user_id, trace_id=trace_id)


async def send_tour_max(
    tenant: TenantContext, *, chat_id: int | None, user_id: int, trace_id: str
) -> dict[str, Any]:
    """Видео-тур в личку Max с теми же кнопками (слово СИСТЕМА и кнопка «Показ системы» в меню гостя)."""
    base: dict[str, Any] = {"ok": True, "route": "max_funnel_system", "trace_id": trace_id}
    try:
        result = await send_media(
            None, tenant.tenant_id, texts.SYSTEM_TOUR_KEY, channel="max",
            chat_id=chat_id, user_id=user_id,
            caption=texts.SYSTEM_TOUR_CAPTION, buttons=max_buttons(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("max_funnel_system_media_error", extra={"trace_id": trace_id})
        result = {"ok": False, "status": "error"}
    if result.get("ok"):
        return {**base, "status": "tour", "kind": result.get("kind")}
    await send_max_text(chat_id=chat_id, user_id=user_id, text=texts.SYSTEM_TOUR_PENDING, buttons=max_buttons())
    return {**base, "status": "pending", "reason": result.get("status")}


async def max_funnel_callback(tenant: TenantContext, event: Any, trace_id: str) -> None:
    """Нажатие кнопки воронки в Max: текст-заглушка новым сообщением (текст сообщения с туром не заменяем).
    Как и Telegram-двойник (``try_handle_funnel_callback``), отвечает только в боте WWC (тенант ``whieda``) и
    в личном чате (``chat_type == "dialog"``); иначе тихо ``None``."""
    placeholder = _PLACEHOLDERS.get(str(event.payload))
    if placeholder is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    if str(getattr(event, "chat_type", "dialog") or "dialog") != "dialog":
        return None
    await send_max_text(chat_id=event.chat_id or None, user_id=event.user_id, text=placeholder)
    return None
