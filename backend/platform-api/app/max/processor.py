"""Обработка событий Max: старт по deep link (ref_… / site_…), вопросы советнику.

Первая версия канала (20.09.2026): человек приходит по ссылке партнёра
https://max.ru/<bot>?start=ref_<код>, закрепляется за пригласившим, видит карточку
«вы пришли от …» с сайтом партнёра, дальше задаёт вопросы по продуктам — отвечает
тот же советник, что и в Telegram. Кабинет партнёра, оплаты и заказ сайта — только
в Telegram (следующие версии).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.advisor.service import handle_structured_query
from app.funnel.system_word import CALLBACK_PREFIX as FUNNEL_CALLBACK_PREFIX
from app.funnel.system_word import handle_system_word_max, is_system_word, max_funnel_callback
from app.leads.channels import accept_channel_referral_start, ensure_channel_actor
from app.leads.start_sources import parse_source_start_token, register_start_source, unknown_source_start
from app.max.client import answer_max_callback, send_max_text
from app.max.my_leads import is_my_leads_text, my_leads_answer
from app.max.update_parser import MaxCallbackEvent, MaxEvent
from app.referral_bonus.service import inviter_card, parse_referral_start_token, parse_site_start_token
from app.telegram.modes import should_deliver_telegram_response
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.max")

CHANNEL = "max"

# «Мои заявки» (17.10.2026): бот сам ничего не присылает (правила Max, п. 1.5) — человек просит словом.
MY_LEADS_HINT = "Напишите «Мои заявки» — покажу заявки с сайта."

WELCOME_PLAIN = (
    "Здравствуйте! Это бот WWC — продукты и технологии WHIEDA.\n"
    "Напишите вопрос по товару (например, «стельки с анионами» или «активатор клеток») — отвечу по документам.\n"
    + MY_LEADS_HINT
)


def _greeting(status: str, inviter: Any) -> str:
    who = ""
    if inviter and getattr(inviter, "display_name", ""):
        who = f"Вы пришли от партнёра: {inviter.display_name}."
        if getattr(inviter, "site_url", ""):
            who += f"\nСайт партнёра: {inviter.site_url}"
    lines = {
        "attributed": ["Приглашение сохранено.", who],
        "already_registered": ["Мы уже знакомы — пригласивший не меняется.", who],
        "invalid": ["Ссылка-приглашение недействительна или больше не активна."],
        "self_referral": ["Свою ссылку нельзя использовать для себя."],
    }[status]
    lines.append("")
    lines.append("Напишите вопрос по товару — отвечу по официальным документам WHIEDA.")
    lines.append(MY_LEADS_HINT)
    return "\n".join(line for line in lines if line is not None)


async def _reply(event: MaxEvent, text: str) -> None:
    if event.chat_id:
        await send_max_text(chat_id=event.chat_id, text=text)
    else:
        await send_max_text(user_id=event.user_id, text=text)


async def _handle_max_source_start(tenant: TenantContext, event: MaxEvent, source: Any, trace_id: str) -> dict[str, Any]:
    """Метка источника (V42): ``?start=src_<метка>[_<код>]`` или незнакомый параметр — обычный старт плюс строка
    в bot_start_sources; код партнёра после метки — первое касание партнёра, как у ref_<код>."""

    async def accept_partner(invite_code: str) -> Any:
        return await accept_channel_referral_start(
            tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
            username=event.username, display_name=event.display_name, invite_code=invite_code,
        )

    async def ensure_actor() -> Any:
        return await ensure_channel_actor(
            tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
            username=event.username, display_name=event.display_name,
        )

    result = await register_start_source(
        tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id,
        source=source, accept_partner=accept_partner, ensure_actor=ensure_actor,
    )
    await _reply(event, WELCOME_PLAIN)
    logger.info(
        "max_start_source",
        extra={"trace_id": trace_id, "tag": source.tag, "first_seen": result.first_seen, "recorded": result.recorded},
    )
    return {"ok": True, "route": "max_start", "status": "source", "tag": source.tag, "trace_id": trace_id}


async def handle_max_start(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    payload = event.text
    invite_code = parse_referral_start_token(payload)
    if invite_code is None:
        invite_code = parse_site_start_token(payload)
    if invite_code is None and payload:
        # Не ref_/site_: метка источника src_<метка>[_<код>] или незнакомый параметр — не ошибка и не «отбросили».
        return await _handle_max_source_start(
            tenant, event, parse_source_start_token(payload) or unknown_source_start(), trace_id
        )
    if invite_code is None or not invite_code:
        # Старт без приглашения: человек есть, атрибуции нет.
        await ensure_channel_actor(
            tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
            username=event.username, display_name=event.display_name,
        )
        await _reply(event, WELCOME_PLAIN)
        return {"ok": True, "route": "max_start", "status": "plain", "trace_id": trace_id}
    result = await accept_channel_referral_start(
        tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
        username=event.username, display_name=event.display_name, invite_code=invite_code,
    )
    inviter = await inviter_card(tenant.tenant_id, result.inviter_actor_id) if result.inviter_actor_id else None
    await _reply(event, _greeting(result.status, inviter))
    logger.info("max_start", extra={"trace_id": trace_id, "status": result.status, "user_ref": str(event.user_id)[-4:]})
    return {"ok": result.status == "attributed", "route": "max_start", "status": result.status, "trace_id": trace_id}


async def handle_max_message(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    await ensure_channel_actor(
        tenant.tenant_id, channel=CHANNEL, channel_user_id=event.user_id, channel_chat_id=event.chat_id,
        username=event.username, display_name=event.display_name,
    )
    if not event.text:
        return {"ok": True, "route": "max_message", "status": "empty", "trace_id": trace_id}
    if is_my_leads_text(event.text):
        text, status = await my_leads_answer(tenant, event.user_id)
        await _reply(event, text)
        return {"ok": True, "route": "max_my_leads", "status": status, "trace_id": trace_id}
    wish = await _try_max_wish(tenant, event, trace_id)
    if wish is not None:
        return wish
    # Слово «система» → видео-тур с кнопками (воронка, 30.10.2026): после служебных слов, до советника.
    if is_system_word(event.text):
        return await handle_system_word_max(tenant, event, trace_id)
    body = {
        "session": f"max:{event.chat_id or event.user_id}",
        "question": event.text,
        "surface": "max",
        "country": "RU",
        "language": "ru",
    }
    core_response = await handle_structured_query(tenant, body, trace_id)
    if should_deliver_telegram_response(core_response):
        await _reply(event, str(core_response.get("answer_text") or ""))
    return {"ok": True, "route": "max_message", "answer_mode": core_response.get("answer_mode"), "trace_id": trace_id}


async def handle_max_chat_membership(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    """Бота добавили в чат Max (или убрали): запоминаем — туда пойдут посты из
    Telegram-канала; владельцу — строка в Telegram, чтобы видел, куда подключились."""
    from app.max.client import get_chat
    from app.max.crosspost import remember_max_chat
    from app.settings import get_settings
    from app.telegram.bindings import resolve_bot_binding_context
    from app.telegram.delivery import send_telegram_text

    added = event.kind == "chat_added"
    info = await get_chat(int(event.chat_id)) if added else {}
    title = str(info.get("title") or "").strip() or None
    row = await remember_max_chat(
        tenant.tenant_id, chat_id=int(event.chat_id), title=title, chat_type=str(info.get("type") or event.text),
        is_channel=event.text == "channel", added_by=event.user_id or None, active=added,
    )
    owner = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if owner.isdigit() and binding is not None:
        from app.max.bridges import bridge_for_max

        name = (row or {}).get("title") or title or str(event.chat_id)
        bridge = bridge_for_max(event.chat_id)
        if bridge is not None:
            text = (
                f"Бот WWC добавлен в Max: «{name}» — мост с Telegram-группой «{bridge.title}»."
                if added else f"Бота WWC убрали из Max: «{name}». Мост с «{bridge.title}» остановлен."
            )
        else:
            text = (
                f"Бот WWC добавлен в Max: «{name}». Посты из Telegram-канала теперь повторяются и здесь."
                if added else f"Бота WWC убрали из Max: «{name}». Посты туда больше не идут."
            )
        await send_telegram_text(chat_id=owner, text=text, bot_token=binding.bot_token)
    logger.info("max_chat_membership", extra={"trace_id": trace_id, "added": added, "chat_id": event.chat_id})
    return {"ok": True, "route": "max_chat", "added": added, "chat_id": event.chat_id}


async def _try_max_wish(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any] | None:
    """«Пожелание» в Max (V25): как в Telegram — слово, потом само пожелание текстом.
    Пост владельцу уходит в Telegram, в тему «💡 Пожелания»."""
    from app.feedback.service import start_wish, submit_wish, waiting_wish
    from app.telegram.bindings import binding_context_scope, resolve_bot_binding_context
    from app.telegram.feedback import WISH_PROMPT, deliver_wish, is_wish_request, thanks_text

    if not tenant.entitlements.get("site_support", False):
        return None
    if is_wish_request(event.text):
        await start_wish(
            tenant.tenant_id, channel=CHANNEL, user_id=event.user_id, chat_id=event.chat_id or event.user_id,
            display=event.display_name, ref_code=None,
        )
        await _reply(event, WISH_PROMPT)
        return {"ok": True, "route": "max_wish", "status": "awaiting", "trace_id": trace_id}
    waiting = await waiting_wish(tenant.tenant_id, channel=CHANNEL, user_id=event.user_id)
    if not waiting:
        return None
    row = await submit_wish(tenant.tenant_id, waiting["feedback_id"], text=event.text, file_id=None, media_kind=None)
    if not row:
        return None
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if binding is not None:
        with binding_context_scope(binding):
            await deliver_wish(tenant, row)
    await _reply(event, thanks_text(int(row["feedback_no"])))
    return {"ok": True, "route": "max_wish", "status": "new", "no": int(row["feedback_no"]), "trace_id": trace_id}


async def handle_max_group_event(tenant: TenantContext, event: MaxEvent, trace_id: str) -> dict[str, Any]:
    """Группа Max: работает только мост группы потока (V27) — перенос сообщений в
    Telegram и контроль состава. Остальные группы молча пропускаем."""
    from app.max.bridge import bridge_max_message, watch_max_join
    from app.max.bridges import bridge_for_max
    from app.telegram.bindings import resolve_bot_binding_context

    bridge = bridge_for_max(event.chat_id)
    if bridge is None:
        return {"ok": True, "route": "max_group", "status": "not_bridged", "trace_id": trace_id}
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if binding is None:
        return {"ok": False, "route": "max_group", "status": "no_telegram_bot", "trace_id": trace_id}
    if event.kind == "member_added":
        result = await watch_max_join(bridge, event.raw, bot_token=binding.bot_token)
    else:
        result = await bridge_max_message(tenant.tenant_id, bridge, event.raw, bot_token=binding.bot_token)
    return {"route": "max_group", "trace_id": trace_id, **result}


async def process_max_event(tenant: TenantContext, event: MaxEvent | MaxCallbackEvent, trace_id: str) -> dict[str, Any]:
    if event.kind == "callback":
        return await handle_max_callback(tenant, event, trace_id)
    if event.kind in {"chat_added", "chat_removed"}:
        return await handle_max_chat_membership(tenant, event, trace_id)
    if event.kind in {"group_message", "member_added"}:
        return await handle_max_group_event(tenant, event, trace_id)
    if event.kind == "start":
        return await handle_max_start(tenant, event, trace_id)
    return await handle_max_message(tenant, event, trace_id)


# --- Нажатия кнопок (M2, 30.10.2026) ---------------------------------------------------------------
# Сценарии подключают свои обработчики по префиксу payload кнопки: register_max_callback("menu:", handler).
# Диспетчер находит обработчик (самый длинный подходящий префикс), вызывает его и в любом случае отвечает
# на нажатие (POST /answers), чтобы у человека не крутился индикатор. Неизвестный payload — тихий ответ.


@dataclass
class MaxCallbackAnswer:
    """Что обработчик просит показать в ответ на нажатие (всё необязательно)."""

    notification: str | None = None  # одноразовое уведомление человеку
    text: str | None = None  # заменить текст сообщения с кнопками
    buttons: list[list[dict[str, Any]]] | None = None  # заменить клавиатуру сообщения (max_button_*)


MaxCallbackHandler = Callable[[TenantContext, MaxCallbackEvent, str], Awaitable["MaxCallbackAnswer | dict[str, Any] | None"]]
_CALLBACK_HANDLERS: dict[str, MaxCallbackHandler] = {}


def register_max_callback(prefix: str, handler: MaxCallbackHandler) -> None:
    """Обработчик нажатий для payload, начинающихся с prefix. handler(tenant, event, trace_id) возвращает
    MaxCallbackAnswer, словарь с теми же полями или None (тихий ответ). Повторная регистрация того же префикса
    другим обработчиком — ошибка (два сценария не должны молча перехватывать друг друга)."""
    if not prefix or not prefix.strip():
        raise ValueError("max_callback_prefix_required")
    current = _CALLBACK_HANDLERS.get(prefix)
    if current is not None and current is not handler:
        raise ValueError(f"max_callback_prefix_taken:{prefix}")
    _CALLBACK_HANDLERS[prefix] = handler


def unregister_max_callback(prefix: str) -> None:
    _CALLBACK_HANDLERS.pop(prefix, None)


def resolve_max_callback(payload: str) -> tuple[str, MaxCallbackHandler] | None:
    matches = [prefix for prefix in _CALLBACK_HANDLERS if payload.startswith(prefix)]
    if not matches:
        return None
    prefix = max(matches, key=len)
    return prefix, _CALLBACK_HANDLERS[prefix]


def _as_callback_answer(result: Any) -> MaxCallbackAnswer:
    if isinstance(result, MaxCallbackAnswer):
        return result
    if isinstance(result, dict):
        return MaxCallbackAnswer(
            notification=result.get("notification"), text=result.get("text"), buttons=result.get("buttons")
        )
    return MaxCallbackAnswer()


async def handle_max_callback(tenant: TenantContext, event: MaxCallbackEvent, trace_id: str) -> dict[str, Any]:
    found = resolve_max_callback(event.payload)
    answer = MaxCallbackAnswer()
    status = "unknown"
    prefix = None
    if found is not None:
        prefix, handler = found
        status = "handled"
        try:
            answer = _as_callback_answer(await handler(tenant, event, trace_id))
        except Exception:  # noqa: BLE001 — человеку всё равно отвечаем, ошибку только логируем
            logger.exception("max_callback_handler_failed", extra={"trace_id": trace_id, "prefix": prefix})
            status = "handler_failed"
    answered = False
    try:
        result = await answer_max_callback(
            event.callback_id, notification=answer.notification, text=answer.text, buttons=answer.buttons
        )
        answered = bool((result or {}).get("ok"))
    except Exception:  # noqa: BLE001 — сеть или не настроенный токен: webhook уже получил 200
        logger.exception("max_callback_answer_failed", extra={"trace_id": trace_id})
    logger.info(
        "max_callback",
        extra={"trace_id": trace_id, "status": status, "prefix": prefix, "answered": answered, "user_ref": str(event.user_id)[-4:]},
    )
    return {"ok": status != "handler_failed", "route": "max_callback", "status": status, "prefix": prefix, "answered": answered, "trace_id": trace_id}


# Кнопки воронки («Живая встреча», «Личный разбор» под туром слова СИСТЕМА): payload funnel:meet | funnel:consult.
register_max_callback(FUNNEL_CALLBACK_PREFIX, max_funnel_callback)
