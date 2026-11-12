"""Ответ на «Я уже партнёр WHIEDA» (ТЗ 28, раздел 8, решения лида 11.11).

Кнопка стоит в ряду кнопок приветствия гостя (``app/funnel/guest.py: menu_rows``, ``funnel:partner``). Отдельного вопроса
«вы уже партнёр?» на входе нет: холодный человек сначала получает приветствие и карту, а тот, кто уже в системе, нажимает
эту кнопку и уходит на путь партнёра:

  1. бот отмечает человека партнёром WHIEDA по его слову (``bot_stages.promote``: ведущий потом подтверждает в CRM;
     партнёра WWC и так не понижаем, повтор ничего не меняет);
  2. сообщение «Тогда начнём с системы…» и картинка «Карта системы» с прежней подписью (для партнёра);
  3. сообщение про ролик о системе и цены: кнопки «Ролик о системе» и «Цены и подключение» (ссылки).

Дальше человек видит меню партнёра WHIEDA (``app/bot_stages/menus.py``). Сбой записи статуса не отменяет ответ: человек
получает карту и ссылки в любом случае. Один и тот же путь для Telegram и Max (кнопки — ряды ``(подпись, адрес)``).
"""

from __future__ import annotations

import logging
from typing import Any

from app.bot_stages.service import STAGE_PARTNER_WHIEDA, promote
from app.funnel import texts
from app.funnel.guest import Reply, rows_to_max, rows_to_telegram
from app.funnel.network import guest_offers_blocked

logger = logging.getLogger("whieda.funnel.partner_answer")


def video_reply() -> Reply:
    """Второе сообщение: про ролик и цены, две кнопки-ссылки (каждая в своём ряду)."""
    return Reply(
        texts.PARTNER_ANSWER_VIDEO,
        [
            [(texts.PARTNER_VIDEO_BUTTON, texts.SYSTEM_TOUR_FALLBACK_URL)],
            [(texts.PARTNER_PRICES_BUTTON, texts.PARTNER_PRICES_URL)],
        ],
    )


async def mark_partner(tenant_id: str, channel: str, user_id: int) -> str:
    """Отметить человека партнёром WHIEDA. ``changed`` / ``already`` / ``error`` (сбой базы: в журнал, ответ идёт дальше)."""
    try:
        return await promote(tenant_id, channel, int(user_id), STAGE_PARTNER_WHIEDA)
    except Exception as exc:  # noqa: BLE001 — человек не должен остаться без ответа
        logger.warning("partner_answer_promote_failed", extra={"channel": channel, "error_class": type(exc).__name__})
        return "error"


async def answer_telegram(tenant: Any, chat_id: int, user_id: int, *, trace_id: str | None = None) -> dict[str, Any]:
    """Telegram: статус, текст, карта (прежняя подпись), ролик и цены."""
    from app.funnel.system_word import send_tour_telegram
    from app.telegram.bindings import current_bot_binding
    from app.telegram.delivery import send_telegram_text

    outcome = await mark_partner(tenant.tenant_id, "telegram", user_id)
    token = current_bot_binding().bot_token
    await send_telegram_text(chat_id=str(chat_id), text=texts.PARTNER_ANSWER_TEXT, bot_token=token)
    blocked = await guest_offers_blocked(tenant.tenant_id, "telegram", user_id)
    tour = await send_tour_telegram(tenant, chat_id, trace_id=trace_id, offers=not blocked, partner=True)
    video = video_reply()
    await send_telegram_text(chat_id=str(chat_id), text=video.text, bot_token=token, reply_markup=rows_to_telegram(video.rows))
    return {"ok": True, "route": "guest_callback", "status": "partner_path", "stage": outcome, "tour": tour.get("status")}


async def answer_max(tenant: Any, chat_id: int | None, user_id: int, trace_id: str) -> dict[str, Any]:
    """Max: то же самое тем же путём (``send_max_text`` / ``send_tour_max``)."""
    from app.funnel.system_word import send_tour_max
    from app.max.client import send_max_text

    outcome = await mark_partner(tenant.tenant_id, "max", user_id)
    await send_max_text(chat_id=chat_id, user_id=user_id, text=texts.PARTNER_ANSWER_TEXT)
    blocked = await guest_offers_blocked(tenant.tenant_id, "max", user_id)
    tour = await send_tour_max(tenant, chat_id=chat_id, user_id=user_id, trace_id=trace_id, offers=not blocked, partner=True)
    video = video_reply()
    await send_max_text(chat_id=chat_id, user_id=user_id, text=video.text, buttons=rows_to_max(video.rows))
    return {"ok": True, "route": "max_guest_callback", "status": "partner_path", "stage": outcome, "tour": tour.get("status")}
