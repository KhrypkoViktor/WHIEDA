"""Три меню бота WWC в Telegram: показ меню по статусу, кнопки «Что дальше» / «Пригласить» / «Ещё», вопрос гостя вместо
советчика, синяя кнопка «Меню» по статусу, подтверждение ведущего и слово владельца «партнёр @ник» (04.11.2026).

Куда это подключено в ``app/telegram/processor.py``:
  * ``show_stage_menu`` — в начале ``show_referral_dashboard``: гостю — меню гостя, партнёру WHIEDA — своё меню,
    партнёру WWC — ``None`` (дальше идёт кабинет с сайтом и бонусами, ``app/telegram/referral_bonus.py``);
  * ``try_handle_stage_callback`` — кнопки ``stg:…`` (в том числе «Да» / «Нет» под вопросом ведущему);
  * ``try_handle_guest_text`` — свободный текст гостя: «Нажмите «Задать вопрос»» вместо советчика по товарам;
  * ``try_handle_owner_partner_word`` — владелец: «партнёр @ник» и «партнёр снять @ник»;
  * ``stage_command`` — команды «/site» и «/course» партнёра WHIEDA.

Всё работает только в боте WWC (арендатор ``whieda``) и пока включено ``PLATFORM_BOT_STAGES``; иначе каждая функция
возвращает ``None`` и бот отвечает как прежде. Сбой базы или сети гостя и партнёра не ломает: ответ — как прежде.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.bot_stages import menus
from app.bot_stages.service import (
    STAGE_GUEST,
    STAGE_PARTNER_WHIEDA,
    STAGE_PARTNER_WWC,
    demote_to_guest,
    find_telegram_person,
    inviter_actor_id,
    person_stage,
    promote,
    stages_enabled,
    user_ref,
)
from app.crm.service import safe_error
from app.db import tenant_connection
from app.funnel import texts
from app.funnel.guest import Reply, rows_to_max, rows_to_telegram
from app.funnel.guest_bot import stage_menu
from app.referral_bonus.service import inviter_card
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import _call_telegram, answer_callback_query, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext
from app.ttl_cache import TtlCache

logger = logging.getLogger("whieda.bot_stages")

# Синяя кнопка «Меню» этого чата. Кабинет партнёра открывают чаще всего, и каждый раз ходить в Telegram за тем же списком
# команд незачем: процесс помнит, что с чатом. Значение — статус и его состояние: ``<статус>`` (список стоит, 6 часов),
# ``pending:<статус>`` (вызов идёт, минуту — второй не запускаем), ``failed:<статус>`` (Telegram не ответил, 5 минут не
# повторяем: иначе на каждом /start новая задача и новое ожидание). Смена статуса — другое значение, и команды ставятся заново.
_COMMANDS_TTL_SEC = 6 * 3600
_COMMANDS_PENDING_TTL_SEC = 60
_COMMANDS_FAILED_TTL_SEC = 5 * 60
# Вызов setMyCommands необязателен — ждать его дольше нескольких секунд незачем (по умолчанию в _call_telegram десять).
COMMANDS_TIMEOUT_SEC = 3.0
_synced_commands: TtlCache[str] = TtlCache(max_entries=50_000)
# Фоновые вызовы: держим ссылку, пока идут, иначе сборщик мусора может убрать задачу на лету (как «печатает…» в routes.py).
_background_tasks: set[asyncio.Task] = set()

_COMMAND_RE = re.compile(r"^/(site|course)(?:@\w+)?\s*$", re.IGNORECASE)
_CONFIRM_RE = re.compile(r"^stg:(y|n):([0-9a-f]{32})$")
_OWNER_WORD_RE = re.compile(
    r"^\s*/?партн[её]р\s+(?:(снять|убрать)\s+)?(@[A-Za-z0-9_]{3,32}|\d{5,15})\s*$", re.IGNORECASE
)
_OWNER_WORD_START_RE = re.compile(r"^\s*/?партн[её]р(?:\s|$)", re.IGNORECASE)


def stage_command(text: str | None) -> str | None:
    """«/site» → ``site``, «/course» → ``course``; остальное — ``None``. Команды живут в меню партнёра WHIEDA."""
    match = _COMMAND_RE.match(str(text or "").strip())
    return match.group(1).lower() if match else None


# ---- отправка --------------------------------------------------------------------------------------


async def send_reply(chat_id: int | str, reply: Reply) -> None:
    await send_telegram_text(
        chat_id=str(chat_id), text=reply.text, bot_token=current_bot_binding().bot_token,
        reply_markup=rows_to_telegram(reply.rows),
    )


async def _set_chat_commands(
    cache_key: tuple[str, int], chat_id: int, stage: str, commands: list[dict[str, str]], bot_token: str
) -> None:
    """Сам вызов setMyCommands (фоновая задача): короткий таймаут, итог — в кэш процесса. Сбой — в лог и в кэш на пять
    минут, чтобы не повторять его на каждом /start."""
    ok = False
    try:
        result = await asyncio.wait_for(
            _call_telegram(
                "setMyCommands",
                {"commands": commands, "scope": {"type": "chat", "chat_id": int(chat_id)}},
                bot_token=bot_token,
                timeout_sec=COMMANDS_TIMEOUT_SEC,
            ),
            timeout=COMMANDS_TIMEOUT_SEC,
        )
        ok = bool(result.get("ok"))
    except Exception as exc:  # noqa: BLE001 — таймаут, сеть, ошибка Telegram: меню команд необязательное
        logger.warning("bot_stage_commands_failed", extra={"stage": stage, "error_class": type(exc).__name__})
    if _synced_commands.get(cache_key) != f"pending:{stage}":
        return  # пока шёл вызов, статус чата сменился и поставлен новый список: этот итог устарел
    if ok:
        _synced_commands.set(cache_key, stage, _COMMANDS_TTL_SEC)
    else:
        _synced_commands.set(cache_key, f"failed:{stage}", _COMMANDS_FAILED_TTL_SEC)


async def sync_chat_commands(chat_id: int | str, stage: str) -> bool:
    """Синяя кнопка «Меню» этого чата по статусу: setMyCommands со scope=chat. Ставится при первом касании (меню гостя)
    и при смене статуса. Вызов идёт фоновой задачей и ответа не задерживает: меню человеку уходит, не дожидаясь
    Telegram (раньше ждали до десяти секунд). Возвращает ``True``, если список уже стоит или поставлен в работу, ``False`` —
    нечего ставить (три меню выключены) или недавний вызов не удался и пять минут не повторяется."""
    binding = current_bot_binding()
    if not stages_enabled(binding.tenant.tenant_id):
        return False
    cache_key = (binding.binding_id, int(chat_id))
    state = _synced_commands.get(cache_key)
    if state in (stage, f"pending:{stage}"):
        return True
    if state == f"failed:{stage}":
        return False
    site_support = bool(binding.tenant.entitlements.get("site_support", False))
    commands = menus.commands_for_stage(stage, include_wish=site_support, include_max=site_support)
    _synced_commands.set(cache_key, f"pending:{stage}", _COMMANDS_PENDING_TTL_SEC)
    try:
        task = asyncio.get_running_loop().create_task(
            _set_chat_commands(cache_key, int(chat_id), stage, commands, binding.bot_token)
        )
    except RuntimeError:  # нет работающего цикла (вызов вне async-кода): ставить некому
        _synced_commands.pop(cache_key)
        return False
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return True


async def flush_background() -> None:
    """Дождаться фоновых вызовов setMyCommands. Нужно тестам и остановке процесса; боевой путь результата не ждёт."""
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


# ---- пример сайта партнёра -------------------------------------------------------------------------


def _as_url(value: str) -> str:
    """Адрес без схемы («olga.wwc.best») — https: кнопка-ссылка без неё стала бы кнопкой с данными."""
    value = str(value or "").strip()
    return value if value.lower().startswith(("http://", "https://")) else f"https://{value}"


async def example_site_url(tenant_id: str, channel: str, user_id: int) -> str | None:
    """Адрес для кнопки «Пример сайта партнёра»: сайт пригласившего; нет сайта — демо из PLATFORM_DEMO_PARTNER_URL;
    нет и его — ``None`` (кнопки не будет)."""
    try:
        async with tenant_connection(tenant_id) as conn:
            inviter = await inviter_actor_id(conn, tenant_id, channel, user_id)
        card = await inviter_card(tenant_id, inviter) if inviter else None
        if card and card.site_url.strip():
            return _as_url(card.site_url)
    except Exception as exc:  # noqa: BLE001 — кнопка важнее ссылки: падаем на демо-сайт
        logger.warning("bot_stage_inviter_site_failed", extra={"channel": channel, **safe_error(exc)})
    demo = get_settings().platform_demo_partner_url.strip()
    return _as_url(demo) if demo else None


# ---- показ меню ------------------------------------------------------------------------------------


async def show_stage_menu(
    tenant: TenantContext, *, telegram_user_id: int, telegram_chat_id: int, trace_id: str
) -> dict[str, Any] | None:
    """Меню по статусу человека. Гость и партнёр WHIEDA — своё меню (и своя синяя кнопка «Меню»); партнёру WWC — ``None``:
    вызывающий показывает кабинет. Три меню выключены, чужой арендатор или сбой базы — ``None``: бот ответит как прежде."""
    if not stages_enabled(tenant.tenant_id):
        return None
    stage, guest_reply = await stage_menu(tenant.tenant_id, "telegram", telegram_user_id)
    if stage is None:
        return None
    if stage == STAGE_GUEST and guest_reply is not None:
        await send_reply(telegram_chat_id, guest_reply)
    elif stage == STAGE_PARTNER_WHIEDA:
        example = await example_site_url(tenant.tenant_id, "telegram", telegram_user_id)
        await send_reply(telegram_chat_id, menus.partner_whieda_reply(channel=menus.TELEGRAM, example_url=example))
    else:
        await sync_chat_commands(telegram_chat_id, STAGE_PARTNER_WWC)
        return None
    await sync_chat_commands(telegram_chat_id, stage)
    logger.info("bot_stage_menu", extra={"stage": stage, "user_ref": user_ref(telegram_user_id), "trace_id": trace_id})
    return {"ok": True, "route": "stage_menu", "stage": stage, "trace_id": trace_id}


# ---- кнопки stg:… ----------------------------------------------------------------------------------


async def try_handle_stage_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    """Кнопки трёх меню: ``stg:next`` («Что дальше»), ``stg:menu`` («← Назад»), ``stg:invite`` («Пригласить»),
    ``stg:more`` («Ещё»), ``stg:y:<карточка>`` / ``stg:n:<карточка>`` («Да» / «Нет» ведущему). Не наша кнопка, три меню
    выключены или чужой арендатор — ``None``."""
    data = str(callback.data or "")
    if not data.startswith(texts.STAGE_CALLBACK_PREFIX) or not stages_enabled(tenant.tenant_id):
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    base: dict[str, Any] = {"ok": True, "route": "stage_callback", "trace_id": trace_id}
    if callback.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    confirm = _CONFIRM_RE.match(data)
    if confirm:
        from app.bot_stages.confirm import handle_confirm_callback

        return await handle_confirm_callback(tenant, callback, confirm.group(1) == "y", confirm.group(2), trace_id=trace_id)
    if data not in (texts.STAGE_NEXT_CALLBACK, texts.STAGE_MENU_CALLBACK, texts.STAGE_INVITE_CALLBACK, texts.STAGE_MORE_CALLBACK):
        return {**base, "status": "unknown"}
    try:
        stage = await person_stage(tenant.tenant_id, "telegram", callback.user_id)
    except Exception as exc:  # noqa: BLE001 — на кнопку надо ответить, даже если база не отозвалась
        logger.warning("bot_stage_callback_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        await send_reply(callback.chat_id, Reply(texts.GUEST_ERROR))
        return {**base, "ok": False, "status": "error"}
    from app.telegram.referral_bonus import show_invitation, show_referral_dashboard

    if data == texts.STAGE_NEXT_CALLBACK and stage == STAGE_PARTNER_WHIEDA:
        example = await example_site_url(tenant.tenant_id, "telegram", callback.user_id)
        await send_reply(callback.chat_id, menus.partner_whieda_next_reply(channel=menus.TELEGRAM, example_url=example))
        return {**base, "status": "next"}
    if data == texts.STAGE_MORE_CALLBACK and stage == STAGE_PARTNER_WWC:
        await send_reply(callback.chat_id, menus.more_reply())
        return {**base, "status": "more"}
    if data == texts.STAGE_INVITE_CALLBACK and stage == STAGE_PARTNER_WWC:
        shown = await show_invitation(
            tenant, telegram_user_id=callback.user_id, telegram_chat_id=callback.chat_id, raw_update=callback.raw,
            trace_id=trace_id,
        )
        return {**base, "status": "invite", **{k: v for k, v in shown.items() if k == "ok"}}
    # «← Назад» и кнопка не по статусу (устаревшее сообщение): меню по статусу человека.
    shown = await show_referral_dashboard(
        tenant, telegram_user_id=callback.user_id, telegram_chat_id=callback.chat_id, raw_update=callback.raw,
        trace_id=trace_id,
    )
    return {**base, "status": "menu", **{k: v for k, v in shown.items() if k == "ok"}}


# ---- свободный текст гостя -------------------------------------------------------------------------


async def try_handle_guest_text(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str, only_catalog_words: bool = False
) -> dict[str, Any] | None:
    """Гость написал текст, который не взяло ничто раньше: советчика по товарам у гостя нет — «Нажмите «Задать вопрос»»
    и та же кнопка. ``only_catalog_words`` — только слова каталога и меню товаров («товары», «/products», «/match» …):
    так гость не открывает каталог, а остальной его текст по-прежнему доходит до открытого обращения.
    Не гость, три меню выключены, чужой арендатор, группа или сбой базы — ``None``."""
    if not stages_enabled(tenant.tenant_id) or msg.chat_type != "private" or not (msg.text or "").strip():
        return None
    if only_catalog_words:
        from app.telegram.navigation import resolve_menu_text_intent

        if not resolve_menu_text_intent(msg.text):
            return None
    try:
        stage = await person_stage(tenant.tenant_id, "telegram", msg.user_id)
    except Exception as exc:  # noqa: BLE001 — не гость по умолчанию: бот ответит как прежде
        logger.warning("bot_stage_guest_text_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        return None
    if stage != STAGE_GUEST:
        return None
    await send_reply(msg.chat_id, menus.guest_free_text_reply())
    return {"ok": True, "route": "guest_free_text", "trace_id": trace_id}


# ---- «Поддержка» гостя и партнёра WHIEDA -----------------------------------------------------------------


async def support_view_for_stage(tenant: TenantContext, user_id: int) -> tuple[str, str] | None:
    """Как открыть обращение «Поддержка» человеку без сайта: ``(приглашение человеку, шапка владельцу)``. Гостю и
    партнёру WHIEDA — то же приглашение и тот же вид обращения, что у кнопки гостя «Задать вопрос» («Напишите вопрос одним
    сообщением — передам Виктору», а не «Напишите, что изменить на вашем сайте…»); владельцу вместо «Сайт: не найден» —
    «Гость …» или «Партнёр WHIEDA». Партнёр WWC, три меню выключены, чужой арендатор или сбой базы — ``None``: обращение
    открывается как прежде."""
    if not stages_enabled(tenant.tenant_id):
        return None
    try:
        stage = await person_stage(tenant.tenant_id, "telegram", user_id)
        if stage == STAGE_GUEST:
            from app.funnel.guest_bot import guest_ask_header

            return texts.ASK_PROMPT, await guest_ask_header(tenant.tenant_id, user_id)
    except Exception as exc:  # noqa: BLE001 — поддержка важнее вида обращения
        logger.warning("bot_stage_support_view_failed", extra={"user_ref": user_ref(user_id), **safe_error(exc)})
        return None
    if stage == STAGE_PARTNER_WHIEDA:
        return texts.ASK_PROMPT, texts.STAGE_PW_ASK_HEADER
    return None


# ---- человеку, которого повысили -------------------------------------------------------------------


async def announce_partner_whieda(tenant_id: str, channel: str, user_id: int) -> bool:
    """Поздравление и новое меню человеку, ставшему партнёром WHIEDA: два сообщения в его мессенджере (Telegram — ботом
    этого запроса, Max — ботом Max) и синяя кнопка «Меню» его чата. ``False`` — написать не удалось (закрыл чат)."""
    example = await example_site_url(tenant_id, channel, user_id)
    menu = menus.partner_whieda_reply(channel=channel, example_url=example)
    try:
        if channel == menus.TELEGRAM:
            await send_reply(user_id, Reply(texts.STAGE_PW_CONGRATS))
            await send_reply(user_id, menu)
        else:
            from app.max.client import send_max_text

            await send_max_text(user_id=int(user_id), text=texts.STAGE_PW_CONGRATS)
            await send_max_text(user_id=int(user_id), text=menu.text, buttons=rows_to_max(menu.rows))
    except Exception as exc:  # noqa: BLE001 — человек мог закрыть чат с ботом
        logger.warning(
            "bot_stage_announce_failed", extra={"channel": channel, "user_ref": user_ref(user_id), **safe_error(exc)}
        )
        return False
    if channel == menus.TELEGRAM:
        await sync_chat_commands(user_id, STAGE_PARTNER_WHIEDA)
    return True


# ---- слово владельца «партнёр @ник» ----------------------------------------------------------------


def _person_name(person: dict[str, Any]) -> str:
    return " ".join(str(person.get("display_name") or "").split())[:80] or f"id {person.get('telegram_user_id')}"


async def try_handle_owner_partner_word(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """Владелец переводит своих людей словом: «партнёр @ник» (или id Telegram) → партнёр WHIEDA, «партнёр снять @ник» →
    снова гость. Не владелец, другое слово или три меню выключены — ``None``: текст идёт дальше как прежде."""
    if not stages_enabled(tenant.tenant_id) or msg.chat_type != "private":
        return None
    text = str(msg.text or "")
    if not _OWNER_WORD_START_RE.match(text):
        return None
    owner = get_settings().platform_billing_owner_telegram_id
    if owner is None or int(msg.user_id) != int(owner):
        return None
    base: dict[str, Any] = {"ok": True, "route": "stage_owner_word", "trace_id": trace_id}
    parsed = _OWNER_WORD_RE.match(text)
    if not parsed:
        await send_reply(msg.chat_id, Reply(texts.STAGE_OWNER_USAGE))
        return {**base, "status": "usage"}
    remove, who = parsed.group(1), parsed.group(2)
    try:
        found = await find_telegram_person(tenant.tenant_id, who)
        if not found:
            await send_reply(msg.chat_id, Reply(texts.STAGE_OWNER_NOT_FOUND.format(who=who)))
            return {**base, "status": "not_found"}
        if len(found) > 1:
            await send_reply(msg.chat_id, Reply(texts.STAGE_OWNER_AMBIGUOUS.format(who=who)))
            return {**base, "status": "ambiguous"}
        person = found[0]
        user_id, name = int(person["telegram_user_id"]), _person_name(person)
        if remove:
            outcome = await demote_to_guest(tenant.tenant_id, "telegram", user_id)
            line = {
                "changed": texts.STAGE_OWNER_REMOVED,
                "already": texts.STAGE_OWNER_NOT_GUEST_ANYMORE,
                "has_site": texts.STAGE_OWNER_HAS_SITE,
            }[outcome]
            if outcome == "changed":
                await sync_chat_commands(user_id, STAGE_GUEST)
            await send_reply(msg.chat_id, Reply(line.format(name=name)))
            return {**base, "status": f"remove_{outcome}"}
        outcome = await promote(tenant.tenant_id, "telegram", user_id, STAGE_PARTNER_WHIEDA)
        if outcome == "already":
            stage = await person_stage(tenant.tenant_id, "telegram", user_id)
            line = texts.STAGE_OWNER_HAS_SITE if stage == STAGE_PARTNER_WWC else texts.STAGE_OWNER_ALREADY
            await send_reply(msg.chat_id, Reply(line.format(name=name)))
            return {**base, "status": "already"}
        delivered = await announce_partner_whieda(tenant.tenant_id, "telegram", user_id)
        line = texts.STAGE_OWNER_DONE if delivered else texts.STAGE_OWNER_DONE_UNDELIVERED
        await send_reply(msg.chat_id, Reply(line.format(name=name)))
        return {**base, "status": "promoted", "delivered": delivered}
    except Exception as exc:  # noqa: BLE001 — владельцу отвечаем, даже если база не отозвалась
        logger.warning("bot_stage_owner_word_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        await send_reply(msg.chat_id, Reply(texts.GUEST_ERROR))
        return {**base, "ok": False, "status": "error"}

