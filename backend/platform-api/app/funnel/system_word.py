"""Слово СИСТЕМА в боте WWC (Telegram и Max): видео-тур с двумя кнопками (срез 8 бот-воронки, 30.10.2026).

Человек пишет «система» (любой регистр, можно с «!» или «.» на конце) → бот присылает видео-тур ``system_tour``
из хранилища медиа (app/bot_media) с подписью ``SYSTEM_TOUR_CAPTION`` и кнопками «Живая встреча» и «Личный разбор».
Тур ещё не загружен (или канал не принял файл) → текст ``SYSTEM_TOUR_PENDING``, первой кнопкой-ссылкой ролик о компании
(``SYSTEM_TOUR_FALLBACK_URL``) и теми же двумя кнопками ниже: человек из рекламы не упирается в тупик.

Кнопка «Живая встреча» (``funnel:meet``) открывает список встреч для записи — это обработчик гостя
(app/funnel/guest_bot.py); «Личный разбор» (``funnel:consult``) запускает диалог из двух вопросов и выбора ведущего
(app/funnel/consult.py, срез 7): кнопки ``cq1:`` / ``cq2:`` / ``chost:`` обрабатываются здесь же. В Max обработчики
регистрируются в app/max/processor.py, в Telegram — в цепочке callback процессора.

Слово работает только в личном чате бота WWC (тенант ``whieda``): боты других тенантов и витрин отвечают
на «система» как раньше.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.bot_media.service import send_media
from app.bot_stages.service import stages_enabled
from app.funnel import consult, texts
from app.funnel.guest import rows_to_max, rows_to_telegram
from app.funnel.network import guest_offers_blocked, partner_contact_reply
from app.max.client import send_max_text
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.funnel")

FUNNEL_TENANT_ID = "whieda"
CALLBACK_PREFIX = "funnel:"
SYSTEM_WORD_RE = re.compile(r"^\s*система\s*[!.]?\s*$", re.IGNORECASE)

# «Живая встреча» (funnel:meet) с 30.10.2026 открывает список встреч для записи (app/funnel/guest_bot.py);
# «Личный разбор» (funnel:consult) с 31.10.2026 ведёт в диалог app/funnel/consult.py.


def is_system_word(text: str | None) -> bool:
    return bool(SYSTEM_WORD_RE.match(str(text or "")))


def _labels() -> tuple[str, str]:
    """Подписи двух кнопок под туром: три меню включены (04.11.2026) — те же, что в меню гостя, с картинкой."""
    if stages_enabled():
        return texts.STAGE_MEET_BUTTON, texts.STAGE_CONSULT_BUTTON
    return texts.FUNNEL_MEET_BUTTON, texts.FUNNEL_CONSULT_BUTTON


def _rows(*, presentation: bool, offers: bool = True) -> list[list[tuple[str, str]]]:
    """Ряды кнопок под туром: «Живая встреча» и «Личный разбор», каждая в своём ряду. Тура нет (``presentation``) —
    первым рядом кнопка-ссылка на ролик о компании (адрес с ``https://`` rows_to_* превращают в кнопку-ссылку).
    ``offers=False`` — гость чужого партнёра (app/funnel/network.py): ни встречи, ни разбора."""
    meet, consult = _labels()
    rows = [[(meet, texts.FUNNEL_MEET_CALLBACK)], [(consult, texts.FUNNEL_CONSULT_CALLBACK)]] if offers else []
    if presentation:
        rows.insert(0, [(texts.SYSTEM_TOUR_FALLBACK_BUTTON, texts.SYSTEM_TOUR_FALLBACK_URL)])
    return rows


def telegram_markup(*, presentation: bool = False, offers: bool = True) -> dict[str, Any]:
    """Inline-клавиатура Telegram: две кнопки, каждая в своём ряду; с ``presentation`` — ещё ссылка на ролик сверху."""
    return rows_to_telegram(_rows(presentation=presentation, offers=offers)) or {}


def max_buttons(*, presentation: bool = False, offers: bool = True) -> list[list[dict[str, Any]]]:
    """Ряды кнопок Max (callback и link): те же кнопки, что в Telegram."""
    return rows_to_max(_rows(presentation=presentation, offers=offers)) or []


# ---- Telegram --------------------------------------------------------------------------------------


async def try_handle_system_word(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """«система» в личном чате бота WWC. Не то слово, чужой тенант или группа — ``None``: текст идёт дальше."""
    if tenant.tenant_id != FUNNEL_TENANT_ID or msg.chat_type != "private" or not is_system_word(msg.text):
        return None
    blocked = await guest_offers_blocked(tenant.tenant_id, "telegram", msg.user_id)
    return await send_tour_telegram(tenant, msg.chat_id, trace_id=trace_id, offers=not blocked)


async def send_tour_telegram(
    tenant: TenantContext, chat_id: int, *, trace_id: str | None = None, offers: bool = True
) -> dict[str, Any]:
    """Видео-тур в личный чат Telegram с кнопками «Живая встреча» и «Личный разбор» (или текст с ссылкой на ролик о
    компании, если тура нет).
    Им пользуются слово СИСТЕМА и кнопка «Показ системы» в меню гостя. ``offers=False`` — гость чужого партнёра: без этих
    двух кнопок (под видео ничего, под текстом-заглушкой — только ссылка на ролик о компании)."""
    base: dict[str, Any] = {"ok": True, "route": "funnel_system", "trace_id": trace_id}
    try:
        result = await send_media(
            None, tenant.tenant_id, texts.SYSTEM_TOUR_KEY, channel="telegram", chat_id=chat_id,
            caption=texts.SYSTEM_TOUR_CAPTION if offers else texts.SYSTEM_TOUR_CAPTION_PLAIN,
            reply_markup=telegram_markup(offers=offers) or None,
        )
    except Exception:  # noqa: BLE001 — человек не должен остаться без ответа из-за базы или сети
        logger.exception("funnel_system_media_error", extra={"trace_id": trace_id})
        result = {"ok": False, "status": "error"}
    if result.get("ok"):
        return {**base, "status": "tour", "kind": result.get("kind")}
    await send_telegram_text(
        chat_id=str(chat_id), text=texts.SYSTEM_TOUR_PENDING,
        bot_token=current_bot_binding().bot_token, reply_markup=telegram_markup(presentation=True, offers=offers),
    )
    return {**base, "status": "pending", "reason": result.get("status")}


async def try_handle_funnel_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """«Личный разбор» под туром и кнопки диалога (``funnel:consult``, ``cq1:``, ``cq2:``, ``chost:``): вопрос или
    заявка ведущему (app/funnel/consult.py). Не наша кнопка и чужой тенант — ``None``."""
    action = consult.parse_consult_callback(callback.data)
    if action is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    bot_token = current_bot_binding().bot_token
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=bot_token)
    if callback.chat_type != "private":
        return {"ok": True, "route": "funnel_callback", "status": "private_chat_required", "trace_id": trace_id}
    if await guest_offers_blocked(tenant.tenant_id, "telegram", callback.user_id):
        # «Личный разбор» — только для своей сети: гостю чужого партнёра (в том числе по старой кнопке) — «партнёр свяжется».
        reply = partner_contact_reply()
        await send_telegram_text(
            chat_id=str(callback.chat_id), text=reply.text, bot_token=bot_token, reply_markup=rows_to_telegram(reply.rows)
        )
        return {"ok": True, "route": "funnel_callback", "status": "partner_contact", "trace_id": trace_id}
    requester = consult.telegram_requester(callback) if action.kind == "request" else None
    result = await consult.handle_action(tenant.tenant_id, action, requester)
    await send_telegram_text(
        chat_id=str(callback.chat_id), text=result.reply.text, bot_token=bot_token,
        reply_markup=rows_to_telegram(result.reply.rows),
    )
    return {"ok": result.ok, "route": "funnel_callback", "status": result.status, "trace_id": trace_id}


# ---- Max -------------------------------------------------------------------------------------------


async def handle_system_word_max(tenant: TenantContext, event: Any, trace_id: str) -> dict[str, Any]:
    """«система» в личке Max: тур по токену Max (при первой отправке файл уедет из Telegram в Max) или заглушка."""
    blocked = await guest_offers_blocked(tenant.tenant_id, "max", event.user_id)
    return await send_tour_max(tenant, chat_id=event.chat_id or None, user_id=event.user_id, trace_id=trace_id, offers=not blocked)


async def send_tour_max(
    tenant: TenantContext, *, chat_id: int | None, user_id: int, trace_id: str, offers: bool = True
) -> dict[str, Any]:
    """Видео-тур в личку Max с теми же кнопками (слово СИСТЕМА и кнопка «Показ системы» в меню гостя)."""
    base: dict[str, Any] = {"ok": True, "route": "max_funnel_system", "trace_id": trace_id}
    try:
        result = await send_media(
            None, tenant.tenant_id, texts.SYSTEM_TOUR_KEY, channel="max",
            chat_id=chat_id, user_id=user_id,
            caption=texts.SYSTEM_TOUR_CAPTION if offers else texts.SYSTEM_TOUR_CAPTION_PLAIN,
            buttons=max_buttons(offers=offers) or None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("max_funnel_system_media_error", extra={"trace_id": trace_id})
        result = {"ok": False, "status": "error"}
    if result.get("ok"):
        return {**base, "status": "tour", "kind": result.get("kind")}
    await send_max_text(
        chat_id=chat_id, user_id=user_id, text=texts.SYSTEM_TOUR_PENDING, buttons=max_buttons(presentation=True, offers=offers)
    )
    return {**base, "status": "pending", "reason": result.get("status")}


async def max_funnel_callback(tenant: TenantContext, event: Any, trace_id: str) -> None:
    """Нажатие кнопки «Личного разбора» в Max (``funnel:consult``, ``cq1:``, ``cq2:``, ``chost:``): вопрос или заявка
    ведущему новым сообщением (текст сообщения с кнопками не заменяем). Как и Telegram-двойник
    (``try_handle_funnel_callback``), отвечает только в боте WWC (тенант ``whieda``) и в личном чате
    (``chat_type == "dialog"``); иначе тихо ``None``."""
    action = consult.parse_consult_callback(str(event.payload))
    if action is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    if str(getattr(event, "chat_type", "dialog") or "dialog") != "dialog":
        return None
    if await guest_offers_blocked(tenant.tenant_id, "max", event.user_id):
        reply = partner_contact_reply()
        await send_max_text(
            chat_id=event.chat_id or None, user_id=event.user_id, text=reply.text, buttons=rows_to_max(reply.rows)
        )
        return None
    requester = consult.max_requester(event) if action.kind == "request" else None
    result = await consult.handle_action(tenant.tenant_id, action, requester)
    await send_max_text(
        chat_id=event.chat_id or None, user_id=event.user_id, text=result.reply.text,
        buttons=rows_to_max(result.reply.rows),
    )
    return None
