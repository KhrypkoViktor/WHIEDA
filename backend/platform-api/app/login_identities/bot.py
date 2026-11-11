"""Команды владельца в Telegram-боте: «код входа <кто>», «способы входа <кто>», «отвязать google|max|yandex|vk <кто>».

Только владелец (PLATFORM_BILLING_OWNER_TELEGRAM_ID) и super-admins. Для всех остальных это не команда:
обработчик молча возвращает None, и «код входа не приходит» партнёра идёт дальше по цепочке — в поддержку
(ревью 15.10.2026, P1). Партнёру бот ничего не шлёт: код владелец передаёт сам. ``<кто>`` — ref,
id Telegram или имя (при нескольких совпадениях — список с ref).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db import tenant_connection
from app.login_identities import service
from app.login_identities.rules import (
    PROVIDER_TITLES,
    LoginIdentityError,
    is_self_code_text,
    method_label,
    parse_owner_command,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

MOSCOW = ZoneInfo("Europe/Moscow")

USAGE = (
    "Команды входа:\n"
    "код входа <ref или имя>\n"
    "способы входа <ref или имя>\n"
    "отвязать google <ref или имя>\n"
    "отвязать max <ref или имя>\n"
    "отвязать yandex <ref или имя>\n"
    "отвязать vk <ref или имя>"
)


def is_login_identity_command(text: str) -> bool:
    return parse_owner_command(text) is not None


def _msk(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")


def code_text(issued: dict[str, Any]) -> str:
    person = service.person_title(issued["person"])
    return (
        f"Код входа для {person}: {issued['code_display']}\n"
        "Действует 24 часа, один раз.\n\n"
        "Человеку: на сайте «Войти» → «Не получается через Telegram?» → Google или Max → ввести код. "
        "Дальше вход в одно касание; заявки по-прежнему приходят в Telegram."
    )


def methods_text(person: dict[str, Any], identities: list[dict[str, Any]], code_until: datetime | None) -> str:
    lines = [f"Способы входа — {service.person_title(person)}:", "• Telegram"]
    for row in identities:
        provider = str(row["provider"])
        title = PROVIDER_TITLES.get(provider, provider)
        label = method_label(provider, row)
        since = _msk(row.get("linked_at"))
        suffix = f" (с {since})" if since else ""
        lines.append(f"• {title}: {label}{suffix}" if label != title else f"• {title}{suffix}")
    lines.append("")
    lines.append(f"Активный код: до {_msk(code_until)} МСК." if code_until else "Активного кода нет.")
    return "\n".join(lines)


def ambiguous_text(candidates: list[dict[str, Any]]) -> str:
    lines = ["Нашлось несколько — укажите ref или id:"]
    for person in candidates[:10]:
        lines.append(f"• {service.person_title(person)} · id {person.get('telegram_user_id')}")
    return "\n".join(lines)


def error_text(exc: LoginIdentityError) -> str:
    if exc.code == "partner_not_found":
        return f"Не нашёл: {exc.details.get('target', '')}. Укажите ref, id Telegram или имя как на сайте."
    if exc.code == "partner_ambiguous":
        return ambiguous_text(list(exc.details.get("candidates") or []))
    if exc.code == "forbidden":
        return "Команда недоступна."
    if exc.code == "invalid_provider":
        return USAGE
    return "Не получилось. " + USAGE


async def _deliver(chat_id: int, text: str) -> None:
    await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token)


async def _methods(tenant_id: str, target: str) -> str:
    async with tenant_connection(tenant_id) as conn:
        person = await service.resolve_person(conn, tenant_id, target)
        telegram_user_id = int(person["telegram_user_id"])
        identities = await service.list_identities(conn, tenant_id, telegram_user_id)
        code_until = await service.active_code_info(conn, tenant_id, telegram_user_id)
    return methods_text(person, identities, code_until)


async def _unlink(tenant_id: str, *, provider: str, target: str, revoked_by: int) -> str:
    async with tenant_connection(tenant_id) as conn:
        person = await service.resolve_person(conn, tenant_id, target)
    revoked = await service.revoke_identity(
        tenant_id,
        telegram_user_id=int(person["telegram_user_id"]),
        provider=provider,
        revoked_by=f"owner:{int(revoked_by)}",
    )
    title = PROVIDER_TITLES.get(provider, provider)
    if not revoked:
        return f"У {service.person_title(person)} нет привязки {title}."
    return f"{title} отвязан у {service.person_title(person)}. Войти этим способом снова можно только по новому коду."


async def try_handle_login_identity_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    # Сначала — кто пишет: у не-владельца такого слова нет, текст уходит следующим обработчикам без ответа.
    if not service.owner_allowed(msg.user_id):
        return None
    parsed = parse_owner_command(msg.text)
    if parsed is None:
        return None
    action, provider, target = parsed
    base = {"ok": True, "route": "login_identity_admin", "action": action, "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    try:
        if action == "issue":
            issued = await service.issue_code(tenant.tenant_id, issued_by=msg.user_id, target=target)
            await _deliver(msg.chat_id, code_text(issued))
            return {**base, "status": "issued", "telegram_user_id": int(issued["person"]["telegram_user_id"])}
        if action == "methods":
            await _deliver(msg.chat_id, await _methods(tenant.tenant_id, target))
            return {**base, "status": "methods"}
        await _deliver(
            msg.chat_id,
            await _unlink(tenant.tenant_id, provider=str(provider), target=target, revoked_by=msg.user_id),
        )
        return {**base, "status": "unlinked", "provider": provider}
    except LoginIdentityError as exc:
        await _deliver(msg.chat_id, error_text(exc))
        return {**base, "ok": False, "status": exc.code}


# ---- «код входа» для себя: партнёр сам, без владельца ------------------------------------------------


def self_code_text(issued: dict[str, Any]) -> str:
    return (
        f"Ваш код для входа на сайт: {issued['code_display']}. Действует 24 часа, один раз. "
        "На сайте после Яндекс ID, VK ID или Google введите эти 6 цифр — дальше вход в одно касание. "
        "Никому не пересылайте этот код."
    )


def self_code_error_text(exc: LoginIdentityError) -> str:
    if exc.code == "code_rate_limited":
        return "Код уже выдавали несколько раз сегодня. Попробуйте завтра или напишите в поддержку."
    if exc.code == "partner_not_found":
        return "Не нашёл вас среди партнёров. Напишите в поддержку — подключим."
    return "Не получилось выдать код. Попробуйте чуть позже или напишите в поддержку."


async def self_code_answer(tenant_id: str, telegram_user_id: int) -> tuple[str, str]:
    """(текст, статус): код тому, кто просит, для себя. Не бросает LoginIdentityError."""
    try:
        issued = await service.issue_self_code(tenant_id, telegram_user_id=int(telegram_user_id))
    except LoginIdentityError as exc:
        return self_code_error_text(exc), exc.code
    return self_code_text(issued), "issued"


async def handle_self_code_request(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any]:
    """Общая точка для слова «код входа» и /start login_code. В группе код не шлём и ничего не отвечаем."""
    base = {"ok": True, "route": "login_self_code", "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    text, status = await self_code_answer(tenant.tenant_id, msg.user_id)
    await _deliver(msg.chat_id, text)
    return {**base, "ok": status == "issued", "status": status}


async def try_handle_self_code_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """«код входа» / «код для входа» без цели — любому, включая владельца (свой код). С целью — не здесь."""
    if not is_self_code_text(msg.text):
        return None
    return await handle_self_code_request(tenant, msg, trace_id=trace_id)


async def max_self_code_answer(tenant_id: str, max_user_id: int) -> tuple[str, str]:
    """То же в Max: только если этот Max подтверждённо связан с человеком (account_identities, provider max)."""
    from app.max.webapp import _telegram_id_for_max

    async with tenant_connection(tenant_id) as conn:
        telegram_user_id = await _telegram_id_for_max(conn, tenant_id, int(max_user_id))
    if telegram_user_id is None:
        return (
            "Не вижу, чей это Max. Напишите «код входа» в Telegram-боте — пришлю код там.",
            "not_linked",
        )
    return await self_code_answer(tenant_id, telegram_user_id)
