"""Owner-only referral corrections and bonus adjustments for Telegram."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.referral_bonus.service import (
    BonusRedemptionError,
    BonusRedemptionExpiredError,
    BonusRedemptionForbiddenError,
    cancel_referral_admin_intent,
    confirm_referral_admin_intent,
    create_referral_admin_intent,
    referral_actor_by_ref,
    referral_dashboard,
)
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.update_parser import parse_telegram_callback, parse_telegram_message
from app.tenancy import TenantContext


_REF = r"ref:([A-Za-z0-9][A-Za-z0-9_-]{0,62})"
_BALANCE_RE = re.compile(rf"^(?:бонусы|/bonuses)\s+{_REF}$", re.IGNORECASE)
_ASSIGN_RE = re.compile(rf"^(?:реферер|/referrer)\s+{_REF}\s+{_REF}$", re.IGNORECASE)
_ADJUST_RE = re.compile(
    rf"^(?:корректировка-бонусов|/bonus-adjust)\s+{_REF}\s+([+-][0-9]+(?:[.,][0-9]{{1,2}})?)\s+(?:WUSD|W\$)\s+(.+)$",
    re.IGNORECASE,
)
_CALLBACK_RE = re.compile(r"^refadmin:(confirm|cancel):([0-9a-f]{32})$")
_USAGE = (
    "Команды:\n"
    "бонусы ref:code\n"
    "реферер ref:кого ref:кто-пригласил\n"
    "корректировка-бонусов ref:code +10 W$ причина"
)


def _owner_allowed(user_id: int) -> bool:
    owner_id = get_settings().platform_billing_owner_telegram_id
    return owner_id is not None and user_id == owner_id


def _first_token(text: str) -> str:
    return str(text or "").strip().split(maxsplit=1)[0].lower()


def is_referral_admin_command_candidate(text: str) -> bool:
    return "ref:" in str(text or "").lower() and _first_token(text) in {
        "бонусы", "/bonuses", "реферер", "/referrer",
        "корректировка-бонусов", "/bonus-adjust",
    }


def _minor_wusd(raw_amount: str) -> int:
    try:
        amount = Decimal(str(raw_amount).replace(",", "."))
    except InvalidOperation as exc:
        raise BonusRedemptionError(_USAGE) from exc
    minor = int(amount * 100)
    if amount == 0 or Decimal(minor) / 100 != amount:
        raise BonusRedemptionError(_USAGE)
    return minor


def _wusd(amount_minor: int) -> str:
    major, minor = divmod(abs(int(amount_minor)), 100)
    sign = "-" if amount_minor < 0 else "+" if amount_minor > 0 else ""
    return f"{sign}{major},{minor:02d} W$" if minor else f"{sign}{major} W$"


def _compact_id(intent_id: Any) -> str:
    return str(intent_id).replace("-", "")


def _full_id(token: str) -> str:
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text,
        bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup,
    )


def _intent_keyboard(intent_id: Any) -> dict[str, Any]:
    token = _compact_id(intent_id)
    return {
        "inline_keyboard": [[
            {"text": "Подтвердить", "callback_data": f"refadmin:confirm:{token}"},
            {"text": "Отмена", "callback_data": f"refadmin:cancel:{token}"},
        ]]
    }


async def try_handle_referral_admin_message(
    tenant: TenantContext,
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    msg = parse_telegram_message(update)
    if not msg or not is_referral_admin_command_candidate(msg.text):
        return None
    base = {"ok": True, "route": "referral_admin", "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if not _owner_allowed(msg.user_id):
        await _deliver(msg.chat_id, "Команда недоступна.")
        return {**base, "ok": False, "status": "forbidden"}

    text = str(msg.text or "").strip()
    try:
        balance = _BALANCE_RE.fullmatch(text)
        if balance:
            partner = await referral_actor_by_ref(tenant.tenant_id, balance.group(1).lower())
            dashboard = await referral_dashboard(tenant.tenant_id, actor_id=str(partner["owner_id"]))
            await _deliver(
                msg.chat_id,
                "\n".join([
                    f"Партнёр: {partner.get('display_name') or partner['ref_code']}",
                    f"Ref: {partner['ref_code']}",
                    f"Бонусный баланс: {_wusd(int(dashboard['balance_wusd_minor'])).lstrip('+')}",
                    f"Приглашено: {dashboard['invited_count']}",
                    f"Оплатили сайт: {dashboard['paid_count']}",
                ]),
            )
            return {**base, "status": "balance"}

        assign = _ASSIGN_RE.fullmatch(text)
        if assign:
            invitee_ref, inviter_ref = (value.lower() for value in assign.groups())
            if invitee_ref == inviter_ref:
                raise BonusRedemptionError("Нельзя назначить партнёра своим реферером.")
            intent = await create_referral_admin_intent(
                tenant.tenant_id,
                operation="assign_referrer",
                payload={"invitee_ref": invitee_ref, "inviter_ref": inviter_ref},
                telegram_chat_id=msg.chat_id,
                telegram_user_id=msg.user_id,
            )
            await _deliver(
                msg.chat_id,
                f"Назначить ref:{inviter_ref} пригласившим для ref:{invitee_ref}?",
                reply_markup=_intent_keyboard(intent["intent_id"]),
            )
            return {**base, "status": "assign_preview", "intent_id": str(intent["intent_id"])}

        adjustment = _ADJUST_RE.fullmatch(text)
        if adjustment:
            ref_code, raw_amount, reason = adjustment.groups()
            reason = reason.strip()
            if not reason:
                raise BonusRedemptionError(_USAGE)
            amount_minor = _minor_wusd(raw_amount)
            intent = await create_referral_admin_intent(
                tenant.tenant_id,
                operation="adjust_bonus",
                payload={
                    "ref_code": ref_code.lower(),
                    "amount_minor": amount_minor,
                    "reason": reason[:500],
                },
                telegram_chat_id=msg.chat_id,
                telegram_user_id=msg.user_id,
            )
            await _deliver(
                msg.chat_id,
                f"Корректировать бонусы ref:{ref_code.lower()} на {_wusd(amount_minor)}?\nПричина: {reason[:500]}",
                reply_markup=_intent_keyboard(intent["intent_id"]),
            )
            return {**base, "status": "adjust_preview", "intent_id": str(intent["intent_id"])}
        raise BonusRedemptionError(_USAGE)
    except BonusRedemptionError as exc:
        await _deliver(msg.chat_id, str(exc))
        return {**base, "ok": False, "status": "invalid_command"}


async def try_handle_referral_admin_callback(
    tenant: TenantContext,
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    callback = parse_telegram_callback(update)
    if not callback or not callback.data.startswith("refadmin:"):
        return None
    base = {"ok": True, "route": "referral_admin_callback", "trace_id": trace_id}
    await answer_callback_query(
        callback_query_id=callback.callback_query_id,
        bot_token=current_bot_binding().bot_token,
    )
    if callback.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if not _owner_allowed(callback.user_id):
        await _deliver(callback.chat_id, "Команда недоступна.")
        return {**base, "ok": False, "status": "forbidden"}
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        await _deliver(callback.chat_id, "Подтверждение недействительно.")
        return {**base, "ok": False, "status": "invalid_callback"}
    action, token = match.groups()
    try:
        if action == "cancel":
            state = await cancel_referral_admin_intent(
                tenant.tenant_id,
                intent_id=_full_id(token),
                telegram_chat_id=callback.chat_id,
                telegram_user_id=callback.user_id,
            )
            await _deliver(callback.chat_id, "Уже подтверждено." if state == "confirmed" else "Отменено.")
            return {**base, "status": state}
        result = await confirm_referral_admin_intent(
            tenant.tenant_id,
            intent_id=_full_id(token),
            telegram_chat_id=callback.chat_id,
            telegram_user_id=callback.user_id,
        )
        if result["operation"] == "assign_referrer":
            text = (
                "Реферер уже был назначен."
                if result.get("idempotent")
                else f"Готово: ref:{result['inviter_ref']} назначен пригласившим для ref:{result['invitee_ref']}."
            )
        else:
            text = (
                "Корректировка уже была записана."
                if result.get("idempotent")
                else f"Готово: в бонусы внесено {_wusd(int(result['amount_minor']))}."
            )
        await _deliver(callback.chat_id, text)
        return {**base, "status": "confirmed", "idempotent": bool(result.get("idempotent"))}
    except BonusRedemptionExpiredError:
        await _deliver(callback.chat_id, "Подтверждение истекло. Отправьте команду ещё раз.")
        return {**base, "ok": False, "status": "expired"}
    except (BonusRedemptionError, BonusRedemptionForbiddenError) as exc:
        await _deliver(callback.chat_id, str(exc) or "Подтверждение недействительно.")
        return {**base, "ok": False, "status": "rejected"}
