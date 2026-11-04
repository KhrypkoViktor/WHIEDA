"""Три меню бота WWC в Max: те же гость, партнёр WHIEDA и партнёр WWC, где Max их умеет (04.11.2026).

В Max нет кабинета, оплат и заказа сайта в самом боте (они в Telegram и на сайте), поэтому:

  гость            то же меню гостя (``app/funnel/guest.py``): «Показ системы», «Живая встреча», «Личный разбор»,
                   «Задать вопрос» (последняя отправляет в Telegram-бота);
  партнёр WHIEDA   то же меню, что в Telegram, но «Заказать сайт» и «Курс» — кнопки-ссылки на сайт, «Поддержка» —
                   «Задать вопрос»; «Что дальше» отвечает здесь же;
  партнёр WWC      текст и кнопки-ссылки: свой сайт, кабинет на сайте, Telegram-бот, «Задать вопрос».

Свободный текст гостя — «Нажмите «Задать вопрос»» вместо ответа советчика по товарам. Всё работает только в боте WWC
(арендатор ``whieda``) и пока включено ``PLATFORM_BOT_STAGES``; иначе — ``None``, бот отвечает как прежде.
"""

from __future__ import annotations

import logging
from typing import Any

from app.bot_stages import menus
from app.bot_stages.service import (
    STAGE_GUEST,
    STAGE_PARTNER_WHIEDA,
    STAGE_PARTNER_WWC,
    own_site_url,
    person_stage,
    stages_enabled,
    user_ref,
)
from app.bot_stages.telegram import example_site_url
from app.crm.service import safe_error
from app.db import tenant_connection
from app.funnel import texts
from app.funnel.guest import Reply, rows_to_max
from app.funnel.guest_bot import stage_menu, telegram_bot_url
from app.max.client import send_max_text
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.bot_stages")

CHANNEL = "max"


async def _send(event: Any, reply: Reply) -> None:
    await send_max_text(chat_id=event.chat_id or None, user_id=event.user_id, text=reply.text, buttons=rows_to_max(reply.rows))


async def show_stage_menu_max(tenant: TenantContext, event: Any, trace_id: str) -> dict[str, Any] | None:
    """Меню по статусу человека в Max (событие — старт, сообщение или нажатие кнопки). Сбой базы, чужой арендатор или
    выключенные три меню — ``None``: бот ответит как прежде."""
    if not stages_enabled(tenant.tenant_id):
        return None
    stage, guest_reply = await stage_menu(tenant.tenant_id, CHANNEL, event.user_id)
    if stage is None:
        return None
    try:
        if stage == STAGE_GUEST and guest_reply is not None:
            reply = guest_reply
        elif stage == STAGE_PARTNER_WHIEDA:
            example = await example_site_url(tenant.tenant_id, CHANNEL, event.user_id)
            reply = menus.partner_whieda_reply(channel=menus.MAX, example_url=example)
        elif stage == STAGE_PARTNER_WWC:
            async with tenant_connection(tenant.tenant_id) as conn:
                site = await own_site_url(conn, tenant.tenant_id, CHANNEL, event.user_id)
            reply = menus.partner_wwc_max_reply(site_url=site, telegram_bot_url=telegram_bot_url())
        else:
            return None
    except Exception as exc:  # noqa: BLE001 — человек не должен остаться без ответа: дальше обычное приветствие
        logger.warning("bot_stage_max_menu_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        return None
    await _send(event, reply)
    logger.info("bot_stage_menu", extra={"channel": CHANNEL, "stage": stage, "user_ref": user_ref(event.user_id), "trace_id": trace_id})
    return {"ok": True, "route": "max_stage_menu", "stage": stage, "trace_id": trace_id}


async def try_handle_guest_text_max(tenant: TenantContext, event: Any, trace_id: str) -> dict[str, Any] | None:
    """Свободный текст гостя в Max: советчика по товарам у гостя нет — «Нажмите «Задать вопрос»» и та же кнопка.
    Не гость, три меню выключены, чужой арендатор или сбой базы — ``None``: текст идёт советчику, как прежде."""
    if not stages_enabled(tenant.tenant_id) or not (event.text or "").strip():
        return None
    try:
        stage = await person_stage(tenant.tenant_id, CHANNEL, event.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_stage_max_guest_text_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        return None
    if stage != STAGE_GUEST:
        return None
    await _send(event, menus.guest_free_text_reply())
    return {"ok": True, "route": "max_guest_free_text", "trace_id": trace_id}


async def max_stage_callback(tenant: TenantContext, event: Any, trace_id: str) -> None:
    """Кнопки трёх меню в Max (``stg:next`` — «Что дальше», ``stg:menu`` — «← В кабинет»); регистрируется в
    ``app/max/processor.py`` по префиксу ``stg:``. Ответ — новым сообщением; в группах и при выключенных трёх меню — тихо."""
    payload = str(event.payload or "")
    if tenant.tenant_id != "whieda" or not stages_enabled(tenant.tenant_id):
        return None
    if str(getattr(event, "chat_type", "dialog") or "dialog") != "dialog":
        return None
    if payload == texts.STAGE_NEXT_CALLBACK:
        try:
            stage = await person_stage(tenant.tenant_id, CHANNEL, event.user_id)
            if stage == STAGE_PARTNER_WHIEDA:
                example = await example_site_url(tenant.tenant_id, CHANNEL, event.user_id)
                await _send(event, menus.partner_whieda_next_reply(channel=menus.MAX, example_url=example))
                return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("bot_stage_max_callback_failed", extra={"trace_id": trace_id, **safe_error(exc)})
            await send_max_text(chat_id=event.chat_id or None, user_id=event.user_id, text=texts.GUEST_ERROR)
            return None
    # «← В кабинет» и кнопка не по статусу: меню по статусу человека.
    await show_stage_menu_max(tenant, event, trace_id)
    return None
