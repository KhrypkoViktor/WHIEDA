"""Почта для заявок в Telegram-боте: партнёр — «почта …», владелец за партнёра — «почта для заявок <кто> …».

Партнёр (личный чат):
  почта | почта для заявок | /leademail            — показать адрес;
  почта <адрес> | почта для заявок <адрес>         — задать;
  почта убрать | почта для заявок убрать           — убрать.
Владелец (PLATFORM_BILLING_OWNER_TELEGRAM_ID и super-admins):
  почта для заявок <ref | id | имя> <адрес>        — задать за партнёра;
  почта для заявок <ref | id | имя> убрать         — убрать.
Команда с «<кто>» у не-владельца не распознаётся: обработчик молча возвращает None, текст идёт дальше по цепочке.
Партнёру при команде владельца бот ничего не шлёт: владелец сообщает ему сам.
"""

from __future__ import annotations

from typing import Any

from app.lead_email import service
from app.login_identities import service as login_service
from app.login_identities.rules import LoginIdentityError
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

INVALID_EMAIL_TEXT = "Не похоже на адрес почты. Пример: имя@gmail.com"
NO_SITE_TEXT = (
    "Почта для заявок нужна партнёрам с сайтом. "
    "Чтобы заказать сайт, откройте /start и нажмите «Заказать сайт WWC»."
)
NOT_SET_TEXT = "Почта для заявок не задана. Напишите: почта имя@gmail.com"
CLEARED_TEXT = "Почта для заявок убрана, остаётся Telegram."


def set_text(email: str) -> str:
    return f"Готово. Заявки с сайта будут приходить и на {email}. Убрать: «почта убрать»."


def current_text(email: str) -> str:
    return f"Почта для заявок: {email}. Заявки приходят и на неё, и в Telegram. Убрать: «почта убрать»."


def owner_set_text(title: str, email: str) -> str:
    return f"Почта для заявок у {title}: {email}"


def owner_cleared_text(title: str) -> str:
    return f"Почта для заявок у {title} убрана."


def owner_no_site_text(title: str) -> str:
    return f"У {title} нет сайта WWC — почту для заявок задать нельзя."


def ambiguous_text(candidates: list[dict]) -> str:
    lines = ["Нашлось несколько — укажите ref или id:"]
    for person in candidates[:10]:
        telegram_user_id = person.get("telegram_user_id")
        suffix = f" · id {telegram_user_id}" if telegram_user_id is not None else ""
        lines.append(f"• {login_service.person_title(person)}{suffix}")
    return "\n".join(lines)


def error_text(exc: LoginIdentityError) -> str:
    if exc.code == "partner_not_found":
        return f"Не нашёл: {exc.details.get('target', '')}. Укажите ref, id Telegram или имя как на сайте."
    if exc.code == "partner_ambiguous":
        return ambiguous_text(list(exc.details.get("candidates") or []))
    return "Не получилось. Напишите: почта для заявок <ref или имя> <адрес>"


async def _deliver(chat_id: int, text: str) -> None:
    await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token)


def _first_email(sites: list[service.Site]) -> str | None:
    return next((site.lead_email for site in sites if site.lead_email), None)


async def _own(tenant_id: str, msg: TelegramMessage, command: service.Command, base: dict[str, Any]) -> dict[str, Any]:
    email = None
    if command.action == "set":
        email = service.normalize_email(command.value)
        if email is None:
            await _deliver(msg.chat_id, INVALID_EMAIL_TEXT)
            return {**base, "ok": False, "status": "invalid_email"}
    sites = await service.sites_of_telegram(tenant_id, msg.user_id)
    if not sites:
        await _deliver(msg.chat_id, NO_SITE_TEXT)
        return {**base, "status": "no_site"}
    refs = [site.ref_code for site in sites]
    if command.action == "set":
        await service.set_lead_email(tenant_id, refs, str(email), telegram_user_id=msg.user_id, by="self")
        await _deliver(msg.chat_id, set_text(str(email)))
        return {**base, "status": "set"}
    if command.action == "clear":
        await service.clear_lead_email(tenant_id, refs, telegram_user_id=msg.user_id, by="self")
        await _deliver(msg.chat_id, CLEARED_TEXT)
        return {**base, "status": "cleared"}
    current = _first_email(sites)
    await _deliver(msg.chat_id, current_text(current) if current else NOT_SET_TEXT)
    return {**base, "status": "shown"}


async def _for_target(
    tenant_id: str, msg: TelegramMessage, command: service.Command, base: dict[str, Any]
) -> dict[str, Any]:
    email = None
    if command.action == "set":
        email = service.normalize_email(command.value)
        if email is None:
            await _deliver(msg.chat_id, INVALID_EMAIL_TEXT)
            return {**base, "ok": False, "status": "invalid_email"}
    try:
        target = await service.resolve_owner_target(tenant_id, command.target)
    except LoginIdentityError as exc:
        await _deliver(msg.chat_id, error_text(exc))
        return {**base, "ok": False, "status": exc.code}
    if not target.sites:
        await _deliver(msg.chat_id, owner_no_site_text(target.title))
        return {**base, "status": "no_site"}
    refs = [site.ref_code for site in target.sites]
    by = "owner"
    if command.action == "set":
        await service.set_lead_email(tenant_id, refs, str(email), telegram_user_id=target.telegram_user_id, by=by)
        await _deliver(msg.chat_id, owner_set_text(target.title, str(email)))
        return {**base, "status": "set", "telegram_user_id": target.telegram_user_id}
    await service.clear_lead_email(tenant_id, refs, telegram_user_id=target.telegram_user_id, by=by)
    await _deliver(msg.chat_id, owner_cleared_text(target.title))
    return {**base, "status": "cleared", "telegram_user_id": target.telegram_user_id}


async def try_handle_lead_email_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    # Сначала — кто пишет: «<кто> <адрес>» у не-владельца не команда, текст идёт следующим обработчикам.
    command = service.parse_command(msg.text, is_owner=login_service.owner_allowed(msg.user_id))
    if command is None:
        return None
    base = {"ok": True, "route": "lead_email", "action": command.action, "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if command.target:
        return await _for_target(tenant.tenant_id, msg, command, base)
    return await _own(tenant.tenant_id, msg, command, base)
