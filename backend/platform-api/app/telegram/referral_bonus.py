"""Telegram presentation layer for the WWC referral bonus service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

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


_COMMAND_RE = re.compile(r"^/(?:cabinet|referral)(?:@\w+)?$", re.IGNORECASE)
_CALLBACK_RE = re.compile(
    r"^referral:(history|redeem:platform_(?:3|6|12)m|confirm|cancel)(?::([0-9a-f]{32}))?$"
)


def is_referral_command(text: str) -> bool:
    return bool(_COMMAND_RE.fullmatch(str(text or "").strip()))


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


def _dashboard_keyboard(
    *,
    bot_username: str,
    invite_code: str,
    site_url: str,
    has_site: bool,
) -> dict[str, Any]:
    link = f"https://t.me/{bot_username}?start=ref_{invite_code}"
    share_url = "https://t.me/share/url?url=" + quote(link, safe="")
    rows: list[list[dict[str, Any]]] = [
        [{"text": "Мой сайт" if has_site else "Посмотреть WWC", "url": site_url}],
        [{"text": "Скопировать мою ссылку", "copy_text": {"text": link}}],
        [{"text": "Поделиться", "url": share_url}],
        [{"text": "История бонусов", "callback_data": "referral:history"}],
    ]
    if not has_site:
        rows.insert(1, [{"text": "Создать свой сайт", "callback_data": "site:create"}])
    return {"inline_keyboard": rows}


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


async def try_handle_referral_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    if not is_referral_command(msg.text):
        return None
    if msg.chat_type != "private":
        return {"ok": True, "route": "referral", "status": "private_chat_required", "trace_id": trace_id}
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
