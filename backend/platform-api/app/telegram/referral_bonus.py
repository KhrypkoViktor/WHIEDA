"""Telegram presentation layer for the WWC referral bonus service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from app.referral_bonus.service import (
    BonusRedemptionError,
    BonusRedemptionExpiredError,
    BonusRedemptionForbiddenError,
    cancel_bonus_redemption_intent,
    confirm_bonus_redemption_intent,
    create_bonus_redemption_intent,
    ensure_telegram_actor,
    get_or_create_invite_code,
    referral_dashboard,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext


_COMMAND_RE = re.compile(r"^/(cabinet|referral|invite|support)(?:@\w+)?$", re.IGNORECASE)
_CALLBACK_RE = re.compile(
    r"^referral:(history|list|redeem:platform_(?:3|6|12)m|confirm|cancel)(?::([0-9a-f]{32}))?$"
)

SUPPORT_URL = "https://t.me/sunraysword"


def is_referral_command(text: str) -> bool:
    return bool(_COMMAND_RE.fullmatch(str(text or "").strip()))


def _referral_command(text: str) -> str | None:
    match = _COMMAND_RE.fullmatch(str(text or "").strip())
    return match.group(1).lower() if match else None


def invitation_text(link: str) -> str:
    intro = (
        "Привет! WWC — экосистема для партнёров международного MLM: продукты, "
        "калькулятор, личный сайт и инструменты для работы с людьми. "
        "Посмотри, думаю, тебе пригодится"
    )
    return f"{intro}: {link}" if link else intro


def _wusd(amount_minor: int) -> str:
    major, minor = divmod(abs(int(amount_minor)), 100)
    sign = "−" if amount_minor < 0 else ""
    return f"{sign}{major},{minor:02d} W$" if minor else f"{sign}{major} W$"


def _points(amount_minor: int) -> str:
    return f"{int(amount_minor):,}".replace(",", " ") + " баллов"


def _token_from_uuid(value: Any) -> str:
    return str(value).replace("-", "")


def _history_text(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "История бонусов пока пуста."
    lines = ["История бонусов:", ""]
    for entry in entries[:10]:
        created_at = entry.get("created_at")
        if isinstance(created_at, datetime):
            date = created_at.strftime("%d.%m.%Y")
        else:
            date = ""
        description = str(entry.get("description") or entry.get("entry_type") or "Операция")
        lines.append(f"{date}  {_points(int(entry['amount_minor']))} — {description}".strip())
    return "\n".join(lines)


def _referrals_text(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "Вы пока никого не пригласили."
    status_labels = {
        "active": "сайт активен",
        "grace": "льготный период",
        "suspended": "оплата просрочена",
        "no_subscription": "ожидает оплаты",
        "no_site": "сайт не создан",
    }
    request_labels = {
        "awaiting_country": "оформляет заявку",
        "awaiting_subdomain": "оформляет заявку",
        "awaiting_photo": "оформляет заявку",
        "awaiting_text": "оформляет заявку",
        "awaiting_payment": "ожидает оплаты",
        "pending_confirmation": "оплата на проверке",
        "pending_provisioning": "сайт создаётся",
    }
    lines = ["Мои рефералы", ""]
    for entry in entries[:20]:
        name = str(entry.get("display_name") or "Партнёр")
        username = str(entry.get("telegram_username") or "").strip()
        if username and not username.startswith("@"):
            username = f"@{username}"
        status = status_labels.get(str(entry.get("subscription_status") or ""), "статус уточняется")
        request_status = request_labels.get(str(entry.get("site_request_status") or ""))
        if request_status and str(entry.get("subscription_status") or "") in {"no_site", "no_subscription"}:
            status = request_status
        attributed_at = entry.get("attributed_at")
        date = attributed_at.strftime("%d.%m.%Y") if isinstance(attributed_at, datetime) else ""
        identity = " ".join(part for part in (name, username) if part)
        suffix = f" · {date}" if date else ""
        lines.append(f"• {identity} — {status}{suffix}")
    return "\n".join(lines)


def _dashboard_keyboard(
    *,
    bot_username: str,
    invite_code: str,
    site_url: str,
    has_site: bool,
) -> dict[str, Any]:
    link = f"https://t.me/{bot_username}?start=ref_{invite_code}"
    invite = invitation_text(link)
    share_url = "https://t.me/share/url?" + urlencode(
        {"url": link, "text": invitation_text("")}
    )
    rows: list[list[dict[str, Any]]] = [
        [{"text": "Мой сайт" if has_site else "Посмотреть WWC", "url": site_url}],
        [{"text": "Скопировать приглашение", "copy_text": {"text": invite}}],
        [{"text": "Отправить приглашение", "url": share_url}],
        [{"text": "Мои рефералы", "callback_data": "referral:list"}],
        [{"text": "История баллов", "callback_data": "referral:history"}],
        [{"text": "Поддержка", "url": SUPPORT_URL}],
    ]
    if has_site:
        rows.insert(1, [{"text": "Продлить платформу", "callback_data": "renew:start"}])
    else:
        rows.insert(1, [{"text": "Создать свой сайт", "callback_data": "site:create"}])
    return {"inline_keyboard": rows}


def _invitation_keyboard(link: str) -> dict[str, Any]:
    invite = invitation_text(link)
    share_url = "https://t.me/share/url?" + urlencode(
        {"url": link, "text": invitation_text("")}
    )
    return {
        "inline_keyboard": [
            [{"text": "Скопировать приглашение", "copy_text": {"text": invite}}],
            [{"text": "Отправить приглашение", "url": share_url}],
        ]
    }


async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=binding.bot_token, reply_markup=reply_markup
    )


async def _actor_for_telegram(
    tenant: TenantContext,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    raw_update: dict[str, Any],
) -> str:
    return await ensure_telegram_actor(
        tenant.tenant_id,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        raw_update=raw_update,
    )


async def show_referral_dashboard(
    tenant: TenantContext,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    raw_update: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    actor_id = await _actor_for_telegram(
        tenant,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        raw_update=raw_update,
    )
    invite_code = await get_or_create_invite_code(tenant.tenant_id, actor_id)
    dashboard = await referral_dashboard(tenant.tenant_id, actor_id=actor_id)
    username = str(current_bot_binding().bot_username or "").lstrip("@").strip()
    if not username:
        await _deliver(telegram_chat_id, "Реферальная ссылка временно недоступна: бот ещё не настроен.")
        return {"ok": False, "route": "referral", "status": "bot_username_missing", "trace_id": trace_id}
    link = f"https://t.me/{username}?start=ref_{invite_code}"
    site = dashboard.get("site") or {}
    site_url = str(site.get("url") or "https://wwc.best/")
    status = str(site.get("subscription_status") or "no_subscription")
    days = int(site.get("days_remaining") or 0)
    paid_until = site.get("paid_until")
    paid_until_text = paid_until.strftime("%d.%m.%Y") if isinstance(paid_until, datetime) else "—"
    if status == "active":
        access_line = f"Сайт активен: ещё {days} дн."
    elif status == "grace":
        access_line = "Оплаченный период завершён. Действует льготный срок."
    elif site:
        access_line = "Сайт ожидает продления."
    else:
        access_line = "Персональный сайт ещё не создан."
    text = "\n".join(
        [
            "Личный кабинет",
            "",
            access_line,
            f"Оплачено до: {paid_until_text}",
            f"Сайт: {site_url}",
            f"Баланс: {_points(dashboard['balance_wusd_minor'])}",
            "",
            "Ваша реферальная ссылка:",
            link,
            "",
            f"Приглашено: {dashboard['invited_count']}",
            f"Оплатили сайт: {dashboard['paid_count']}",
            "",
            "20% начисляется с первой оплаты платформы и 10% с продлений.",
            "Каждые 3 000 баллов автоматически продлевают ваш сайт ещё на 3 месяца.",
            "Баллы нельзя вывести деньгами.",
        ]
    )
    await _deliver(
        telegram_chat_id,
        text,
        reply_markup=_dashboard_keyboard(
            bot_username=username,
            invite_code=invite_code,
            site_url=site_url,
            has_site=bool(site),
        ),
    )
    return {"ok": True, "route": "referral", "actor_id": actor_id, "trace_id": trace_id}


async def show_invitation(
    tenant: TenantContext,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    raw_update: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    actor_id = await _actor_for_telegram(
        tenant,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        raw_update=raw_update,
    )
    invite_code = await get_or_create_invite_code(tenant.tenant_id, actor_id)
    username = str(current_bot_binding().bot_username or "").lstrip("@").strip()
    if not username:
        await _deliver(telegram_chat_id, "Приглашение временно недоступно.")
        return {"ok": False, "route": "referral_invite", "status": "bot_username_missing", "trace_id": trace_id}
    link = f"https://t.me/{username}?start=ref_{invite_code}"
    await _deliver(
        telegram_chat_id,
        invitation_text(link),
        reply_markup=_invitation_keyboard(link),
    )
    return {"ok": True, "route": "referral_invite", "actor_id": actor_id, "trace_id": trace_id}


async def show_support(telegram_chat_id: int, *, trace_id: str) -> dict[str, Any]:
    await _deliver(
        telegram_chat_id,
        "Есть вопрос по платформе, оплате или личному сайту? Напишите Виктору.",
        reply_markup={"inline_keyboard": [[{"text": "Написать Виктору", "url": SUPPORT_URL}]]},
    )
    return {"ok": True, "route": "support", "trace_id": trace_id}


async def try_handle_referral_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    command = _referral_command(msg.text)
    if command is None:
        return None
    if msg.chat_type != "private":
        return {"ok": True, "route": "referral", "status": "private_chat_required", "trace_id": trace_id}
    if command == "support":
        return await show_support(msg.chat_id, trace_id=trace_id)
    if command == "invite":
        return await show_invitation(
            tenant,
            telegram_user_id=msg.user_id,
            telegram_chat_id=msg.chat_id,
            raw_update=msg.raw,
            trace_id=trace_id,
        )
    return await show_referral_dashboard(
        tenant,
        telegram_user_id=msg.user_id,
        telegram_chat_id=msg.chat_id,
        raw_update=msg.raw,
        trace_id=trace_id,
    )


async def try_handle_referral_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    binding = current_bot_binding()
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=binding.bot_token)
    if callback.chat_type != "private":
        return {"ok": True, "route": "referral_callback", "status": "private_chat_required", "trace_id": trace_id}
    actor_id = await _actor_for_telegram(
        tenant,
        telegram_user_id=callback.user_id,
        telegram_chat_id=callback.chat_id,
        raw_update=callback.raw,
    )
    action, token = match.groups()
    if action == "history":
        dashboard = await referral_dashboard(tenant.tenant_id, actor_id=actor_id)
        await _deliver(callback.chat_id, _history_text(dashboard["history"]))
        return {"ok": True, "route": "referral_history", "trace_id": trace_id}
    if action == "list":
        dashboard = await referral_dashboard(tenant.tenant_id, actor_id=actor_id)
        await _deliver(callback.chat_id, _referrals_text(dashboard["referrals"]))
        return {"ok": True, "route": "referral_list", "trace_id": trace_id}
    if action.startswith("redeem:"):
        plan_code = action.removeprefix("redeem:")
        try:
            intent = await create_bonus_redemption_intent(
                tenant.tenant_id,
                actor_id=actor_id,
                plan_code=plan_code,
                telegram_chat_id=callback.chat_id,
                telegram_user_id=callback.user_id,
            )
        except BonusRedemptionError as exc:
            await _deliver(callback.chat_id, str(exc))
            return {"ok": False, "route": "referral_redeem_preview", "status": "rejected", "trace_id": trace_id}
        compact_id = _token_from_uuid(intent["intent_id"])
        await _deliver(
            callback.chat_id,
            f"Продлить персональный сайт на {intent['access_months']} мес. за {_wusd(int(intent['cost_wusd_minor']))}?",
            reply_markup={"inline_keyboard": [[
                {"text": "Подтвердить", "callback_data": f"referral:confirm:{compact_id}"},
                {"text": "Отмена", "callback_data": f"referral:cancel:{compact_id}"},
            ]]},
        )
        return {"ok": True, "route": "referral_redeem_preview", "intent_id": str(intent["intent_id"]), "trace_id": trace_id}
    if not token:
        return {"ok": False, "route": "referral_callback", "status": "invalid", "trace_id": trace_id}
    intent_id = f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"
    try:
        if action == "cancel":
            status = await cancel_bonus_redemption_intent(
                tenant.tenant_id,
                intent_id=intent_id,
                actor_id=actor_id,
                telegram_chat_id=callback.chat_id,
                telegram_user_id=callback.user_id,
            )
            await _deliver(callback.chat_id, "Списание отменено." if status == "cancelled" else "Тариф уже продлён.")
            return {"ok": True, "route": "referral_redeem_cancel", "status": status, "trace_id": trace_id}
        result = await confirm_bonus_redemption_intent(
            tenant.tenant_id,
            intent_id=intent_id,
            actor_id=actor_id,
            telegram_chat_id=callback.chat_id,
            telegram_user_id=callback.user_id,
        )
        date = result.get("paid_until")
        suffix = f" Доступ до: {date.strftime('%d.%m.%Y')}" if isinstance(date, datetime) else ""
        await _deliver(callback.chat_id, ("Тариф уже был продлён." if result.get("idempotent") else "Тариф продлён бонусами.") + suffix)
        return {"ok": True, "route": "referral_redeem_confirm", "idempotent": bool(result.get("idempotent")), "trace_id": trace_id}
    except (BonusRedemptionError, BonusRedemptionExpiredError, BonusRedemptionForbiddenError) as exc:
        await _deliver(callback.chat_id, str(exc))
        return {"ok": False, "route": "referral_redeem_confirm", "status": "rejected", "trace_id": trace_id}
