"""Команды владельца в Telegram-боте: «код входа <кто>», «способы входа <кто>», «отвязать google|max <кто>».

Только владелец (PLATFORM_BILLING_OWNER_TELEGRAM_ID) и super-admins; остальным — «Команда недоступна.»
(как у referral_admin). Партнёру бот ничего не шлёт: код владелец передаёт сам. ``<кто>`` — ref,
id Telegram или имя (при нескольких совпадениях — список с ref).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db import tenant_connection
from app.login_identities import service
from app.login_identities.rules import PROVIDER_TITLES, LoginIdentityError, method_label, parse_owner_command
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
    "отвязать max <ref или имя>"
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
    parsed = parse_owner_command(msg.text)
    if parsed is None:
        return None
    action, provider, target = parsed
    base = {"ok": True, "route": "login_identity_admin", "action": action, "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if not service.owner_allowed(msg.user_id):
        await _deliver(msg.chat_id, "Команда недоступна.")
        return {**base, "ok": False, "status": "forbidden"}
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
