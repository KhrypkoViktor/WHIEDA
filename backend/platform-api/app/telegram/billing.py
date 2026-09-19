"""Owner-only manual partner subscription commands for Telegram."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.settings import get_settings
from app.referral_bonus.service import referral_payment_notification_context
from app.telegram.money import both, money, wwc, wwc_signed
from app.subscriptions.pricing import (
    PRODUCT_LABELS,
    USAGE as MULTILINE_USAGE,
    lines_from_json,
    parse_payment_command,
)
from app.subscriptions.service import (
    GRACE_PERIOD,
    PartnerIdentityAmbiguousError,
    PartnerNotFoundError,
    PaymentIntentCancelledError,
    PaymentIntentExpiredError,
    PaymentIntentForbiddenError,
    PaymentIntentNotFoundError,
    SubscriptionError,
    cancel_payment_intent,
    confirm_payment_intent,
    create_lines_intent,
    create_payment_intent,
    list_due_subscriptions,
    resolve_partner_for_billing,
    set_personal_price,
    set_unlimited_access,
    subscription_state,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.update_parser import parse_telegram_callback, parse_telegram_message
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo("Europe/Moscow")
_IDENTIFIER = r"(?:@[A-Za-z0-9_]{1,32}|ref:[A-Za-z0-9][A-Za-z0-9_-]{0,62})"
_PAY_RE = re.compile(
    rf"^(?:оплата|/pay)\s+({_IDENTIFIER})\s+([0-9]+(?:[.,][0-9]{{1,2}})?)\s+(RUB|WUSD|WWC\$|W\$)(?:\s+(3|6|12))?$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(rf"^(?:статус|/status)\s+({_IDENTIFIER})$", re.IGNORECASE)
_DUE_RE = re.compile(r"^/due$", re.IGNORECASE)
_CALLBACK_RE = re.compile(r"^billing:(confirm|cancel|price|price_cancel|unlimited|unlimited_cancel):([0-9a-f]{32})$")
# «безлимит ref:dev» — the owner's technical site and the co-founder's site never expire.
_UNLIMITED_RE = re.compile(rf"^(?:безлимит|/unlimited)\s+({_IDENTIFIER})\s*$", re.IGNORECASE)
_UNLIMITED_USAGE = "Формат: безлимит ref:code — сайт (PRO) без срока, без платежа и бонусов."
# Pending «безлимит» confirmations, keyed by a token in the button.
_UNLIMITED_INTENTS: dict[str, dict[str, Any]] = {}
_PRICE_RE = re.compile(
    rf"^(?:цена|/price)\s+({_IDENTIFIER})\s+(pro|платформа|сайт|клуб|club|настройка(?:\s+сайта)?|setup|курс|академия|course)\s+"
    r"(снять|сброс|([0-9]+(?:[.,][0-9]{1,2})?)\s+(WWC\$|W\$|WUSD))(?:\s+(.+))?$",
    re.IGNORECASE,
)
_PRICE_USAGE = "Формат: цена ref:code PRO 15 WWC$ причина — или: цена ref:code PRO снять"
# Pending personal-price confirmations, keyed by a token in the button.
_PRICE_INTENTS: dict[str, dict[str, Any]] = {}
_PAY_USAGE = "Формат: оплата ref:code 30 WWC$ [3|6|12] или оплата @username 3000 RUB [3|6|12]"
_STATUS_USAGE = "Формат: статус @username или статус ref:code"


@dataclass(frozen=True)
class BillingCommand:
    kind: Literal["pay", "status", "due"]
    identifier: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    access_months: int = 3


def _first_token(text: str) -> str:
    return str(text or "").strip().split(maxsplit=1)[0].lower()




async def notify_payment_participants(payment: dict[str, Any]) -> None:
    bonus = payment.get("referral_bonus") or {}
    context = await referral_payment_notification_context(
        str(payment["tenant_id"]),
        ref_code=str(payment["ref_code"]),
        inviter_actor_id=str(bonus["actor_id"]) if bonus.get("actor_id") else None,
    )
    binding = current_bot_binding()
    customer = context.get("customer") or {}
    customer_chat = str(customer.get("telegram_chat_id") or "").strip()
    if customer_chat:
        period_end = payment["period_end"]
        remaining = max(0, (period_end.date() - datetime.now(timezone.utc).date()).days)
        await send_telegram_text(
            chat_id=customer_chat,
            bot_token=binding.bot_token,
            text="\n".join(
                [
                    "Оплата подтверждена.",
                    f"Сайт: {context.get('site_url') or 'https://wwc.best/'}",
                    f"Доступ до: {_date(period_end)}. Осталось дней: {remaining}.",
                    "Личный кабинет: /cabinet",
                ]
            ),
        )
    inviter = context.get("inviter") or {}
    inviter_chat = str(inviter.get("telegram_chat_id") or "").strip()
    if inviter_chat and bonus and not bonus.get("idempotent"):
        auto = bonus.get("auto_redemption") or {}
        lines = [
            f"{customer.get('display_name') or payment['ref_code']} подключился.",
            f"Начислено: {wwc_signed(int(bonus['amount_minor']))}.",
        ]
        balance = bonus.get("balance_points")
        if balance is not None:
            lines.append(f"Баланс: {wwc(int(balance))}.")
        if int(auto.get("redeemed_blocks") or 0) > 0:
            lines.extend(
                [
                    f"Автоматически списано: {wwc(int(auto['spent_points']))}.",
                    f"Ваш сайт продлён ещё на {int(auto['access_months'])} мес.",
                    f"Осталось дней: {int(auto['days_remaining'])}.",
                ]
            )
        lines.append("Личный кабинет: /cabinet")
        await send_telegram_text(
            chat_id=inviter_chat,
            bot_token=binding.bot_token,
            text="\n".join(lines),
        )


def is_billing_command_candidate(text: str) -> bool:
    return _first_token(text) in {"оплата", "/pay", "статус", "/status", "/due", "цена", "/price", "безлимит", "/unlimited"}


def parse_billing_command(text: str) -> BillingCommand:
    normalized = str(text or "").strip()
    pay = _PAY_RE.fullmatch(normalized)
    if pay:
        identifier, raw_amount, currency, raw_months = pay.groups()
        currency = currency.upper()
        if currency == "W$":
            currency = "WUSD"
        if currency == "RUB" and ("." in raw_amount or "," in raw_amount):
            raise SubscriptionError(_PAY_USAGE)
        try:
            amount = Decimal(raw_amount.replace(",", "."))
        except InvalidOperation as exc:
            raise SubscriptionError(_PAY_USAGE) from exc
        amount_minor = int(amount * 100)
        if amount <= 0 or Decimal(amount_minor) / 100 != amount:
            raise SubscriptionError(_PAY_USAGE)
        return BillingCommand(
            kind="pay",
            identifier=identifier,
            amount_minor=amount_minor,
            currency=currency,
            access_months=int(raw_months or 3),
        )
    status = _STATUS_RE.fullmatch(normalized)
    if status:
        return BillingCommand(kind="status", identifier=status.group(1))
    if _DUE_RE.fullmatch(normalized):
        return BillingCommand(kind="due")
    if _first_token(normalized) in {"оплата", "/pay"}:
        raise SubscriptionError(_PAY_USAGE)
    raise SubscriptionError(_STATUS_USAGE)


def _owner_allowed(user_id: int) -> bool:
    owner_id = get_settings().platform_billing_owner_telegram_id
    return owner_id is not None and user_id == owner_id


def _date(value: datetime | None) -> str:
    if value is None:
        return "нет"
    return value.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M МСК")


def _amount(amount_minor: int, currency: str) -> str:
    return money(amount_minor, currency)




async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text,
        bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup,
    )


def _message_id(update: dict[str, Any]) -> int | None:
    raw = ((update or {}).get("message") or {}).get("message_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _preview_text(intent: dict[str, Any]) -> str:
    previous = intent.get("paid_until")
    return "\n".join(
        [
            f"Партнёр: {intent['display_name']}",
            f"Ref: {intent['ref_code']}",
            f"Сайт: {intent['hostname']}",
            f"Платёж: {_amount(intent['amount_minor'], intent['currency'])}",
            f"Срок: {intent['access_months']} мес.",
            f"Было оплачено до: {_date(previous)}",
            f"Станет оплачено до: {_date(intent['period_end'])}",
            f"Льготный срок до: {_date(intent['grace_until'])}",
        ]
    )


def _intent_keyboard(intent_id: Any) -> dict[str, Any]:
    token = str(intent_id).replace("-", "")
    return {
        "inline_keyboard": [
            [
                {"text": "Подтвердить", "callback_data": f"billing:confirm:{token}"},
                {"text": "Отмена", "callback_data": f"billing:cancel:{token}"},
            ]
        ]
    }


def _status_text(partner: dict[str, Any], *, at: datetime | None = None) -> str:
    current = at or datetime.now(timezone.utc)
    paid_until = partner.get("paid_until")
    state = subscription_state(paid_until, at=current)
    labels = {
        "no_subscription": "оплаты нет",
        "active": "активен",
        "grace": "льготный срок",
        "suspended": "приостановлен",
    }
    grace_until = paid_until + GRACE_PERIOD if paid_until else None
    club_until = partner.get("club_paid_until")
    club_line = f"CLUB: до {club_until.astimezone(MOSCOW).strftime('%d.%m.%Y')}" if club_until else "CLUB: не подключён"
    return "\n".join(
        [
            f"Партнёр: {partner['display_name']}",
            f"Ref: {partner['ref_code']}",
            f"Сайт: {partner['hostname']}",
            f"PRO (сайт): {labels[state]}",
            f"Оплачено до: {_date(paid_until)}",
            f"Льготный срок до: {_date(grace_until)}",
            club_line,
        ]
    )


def _due_text(rows: list[dict[str, Any]], *, at: datetime | None = None) -> str:
    current = at or datetime.now(timezone.utc)
    groups: dict[str, list[str]] = {"active": [], "grace": [], "suspended": []}
    for row in rows:
        state = subscription_state(row.get("paid_until"), at=current)
        if state not in groups:
            continue
        name = str(row.get("display_name") or row["ref_code"])
        groups[state].append(f"{name} ({row['ref_code']}) — {_date(row.get('paid_until'))}")
    sections = [
        ("Истекают за 7 дней", groups["active"]),
        ("Льготный срок", groups["grace"]),
        ("Приостановлены", groups["suspended"]),
    ]
    lines: list[str] = []
    for title, values in sections:
        lines.append(f"{title}:")
        lines.extend(values[:20] or ["нет"])
    return "\n".join(lines)


async def try_handle_billing_message(
    tenant: TenantContext,
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    msg = parse_telegram_message(update)
    if not msg or not is_billing_command_candidate(msg.text):
        return None
    base = {"ok": True, "route": "billing", "trace_id": trace_id}
    if msg.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if not _owner_allowed(msg.user_id):
        await _deliver(msg.chat_id, "Команда недоступна.")
        return {**base, "ok": False, "status": "forbidden"}
    try:
        first = _first_token(msg.text)
        if first in {"цена", "/price"}:
            return await _handle_price_command(tenant, msg, base)
        if first in {"безлимит", "/unlimited"}:
            return await _handle_unlimited_command(tenant, msg, base)
        if first in {"оплата", "/pay"} and ("\n" in msg.text.strip() or not _PAY_RE.fullmatch(msg.text.strip())):
            return await _handle_multiline_payment(tenant, msg, update, base)
        command = parse_billing_command(msg.text)
        if command.kind == "pay":
            message_id = _message_id(update)
            if message_id is None:
                raise SubscriptionError("Не удалось определить сообщение. Отправьте команду ещё раз.")
            intent = await create_payment_intent(
                tenant.tenant_id,
                identifier=command.identifier or "",
                amount_minor=command.amount_minor or 0,
                currency=command.currency or "",
                telegram_chat_id=msg.chat_id,
                telegram_message_id=message_id,
                telegram_user_id=msg.user_id,
                access_months=command.access_months,
            )
            await _deliver(
                msg.chat_id,
                _preview_text(intent),
                reply_markup=_intent_keyboard(intent["intent_id"]),
            )
            return {**base, "status": "preview", "intent_id": str(intent["intent_id"])}
        if command.kind == "status":
            partner = await resolve_partner_for_billing(
                tenant.tenant_id, command.identifier or ""
            )
            await _deliver(msg.chat_id, _status_text(partner))
            return {**base, "status": "status"}
        rows = await list_due_subscriptions(tenant.tenant_id, horizon_days=7)
        await _deliver(msg.chat_id, _due_text(rows))
        return {**base, "status": "due", "count": len(rows)}
    except (PartnerNotFoundError, PartnerIdentityAmbiguousError):
        await _deliver(msg.chat_id, "Партнёр не найден однозначно. Проверьте @username или ref:code.")
        return {**base, "ok": False, "status": "partner_not_found"}
    except SubscriptionError as exc:
        await _deliver(msg.chat_id, str(exc))
        return {**base, "ok": False, "status": "invalid_command"}


async def try_handle_billing_callback(
    tenant: TenantContext,
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    callback = parse_telegram_callback(update)
    if not callback or not callback.data.startswith("billing:"):
        return None
    base = {"ok": True, "route": "billing_callback", "trace_id": trace_id}
    binding = current_bot_binding()
    await answer_callback_query(
        callback_query_id=callback.callback_query_id,
        bot_token=binding.bot_token,
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
    if action in {"price", "price_cancel"}:
        return await _handle_price_callback(tenant, callback, action, token, base)
    if action in {"unlimited", "unlimited_cancel"}:
        return await _handle_unlimited_callback(tenant, callback, action, token, base)
    intent_id = f"{token[0:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:32]}"
    try:
        if action == "cancel":
            status = await cancel_payment_intent(
                tenant.tenant_id,
                intent_id=intent_id,
                telegram_chat_id=callback.chat_id,
                telegram_user_id=callback.user_id,
            )
            text = "Платёж уже был подтверждён." if status == "confirmed" else "Отменено."
            await _deliver(callback.chat_id, text)
            return {**base, "status": status}
        payment = await confirm_payment_intent(
            tenant.tenant_id,
            intent_id=intent_id,
            telegram_chat_id=callback.chat_id,
            telegram_user_id=callback.user_id,
        )
        if not payment.get("idempotent"):
            try:
                await notify_payment_participants(payment)
            except Exception:
                logger.exception(
                    "telegram_billing_participant_notification_failed",
                    extra={"trace_id": trace_id, "payment_id": str(payment.get("payment_id"))},
                )
        if payment.get("multiline"):
            lines = ["Платёж записан." if not payment.get("idempotent") else "Платёж уже был записан."]
            for item in payment.get("lines") or []:
                label = PRODUCT_LABELS.get(str(item["product_code"]), str(item["product_code"]))
                lines.append(f"{label}: {money(int(item['amount_minor']), str(item['currency']))}")
            offset = payment.get("bonus_offset") or {}
            if offset.get("bonus_offset_minor"):
                lines.append(
                    f"Бонусами списано: {wwc(int(offset['bonus_offset_minor']))}. "
                    f"Остаток бонусов: {wwc(int(offset.get('bonus_balance_minor') or 0))}."
                )
            if payment.get("paid_until"):
                lines.append(f"PRO до: {_date(payment['paid_until'])}")
            if payment.get("club_paid_until"):
                lines.append(f"CLUB до: {_date(payment['club_paid_until'])}")
            await _deliver(callback.chat_id, "\n".join(lines))
        else:
            await _deliver(
                callback.chat_id,
                "\n".join(
                    [
                        "Платёж записан." if not payment.get("idempotent") else "Платёж уже был записан.",
                        f"ID: {str(payment['payment_id'])[:8]}",
                        f"Доступ до: {_date(payment['period_end'])}",
                        f"Grace до: {_date(payment['period_end'] + GRACE_PERIOD)}",
                    ]
                ),
            )
        return {
            **base,
            "status": "confirmed",
            "payment_id": str(payment["payment_id"]),
            "idempotent": bool(payment.get("idempotent")),
        }
    except PaymentIntentExpiredError:
        message, status = "Подтверждение истекло. Отправьте команду оплаты ещё раз.", "expired"
    except PaymentIntentCancelledError:
        message, status = "Этот платёж отменён.", "cancelled"
    except (PaymentIntentForbiddenError, PaymentIntentNotFoundError):
        message, status = "Подтверждение недействительно.", "invalid_callback"
    except SubscriptionError:
        logger.exception("telegram_billing_confirmation_failed", extra={"trace_id": trace_id})
        message, status = "Платёж не записан. Отправьте команду оплаты ещё раз.", "failed"
    await _deliver(callback.chat_id, message)
    return {**base, "ok": False, "status": status}


# ----------------------------------------------------------------------------
# Multi-line payments and personal prices (products v7)
# ----------------------------------------------------------------------------

def _lines_preview(intent: dict[str, Any]) -> str:
    lines = lines_from_json(intent["lines"] if not isinstance(intent["lines"], str) else __import__("json").loads(intent["lines"]))
    out = [f"Партнёр: {intent['display_name']}", f"Ref: {intent['ref_code']}", f"Сайт: {intent['hostname']}", ""]
    reasons = intent.get("price_reasons") or {}
    for line in lines:
        label = PRODUCT_LABELS.get(line.product_code, line.product_code)
        term = f", {line.access_months} мес." if line.access_months else ""
        tag = " — акция" if line.promo else (f" — персональная цена: {reasons[line.product_code]}" if line.product_code in reasons else "")
        out.append(f"{label}: {both(line.amount_minor, line.currency)}{term}{tag}")
    out.append(f"Получено: {both(int(intent['received_minor']), str(intent['currency']))}")
    bonus = int(intent.get("bonus_minor") or 0)
    if bonus:
        balance = int(intent.get("bonus_balance_minor") or 0)
        out.append(f"Бонусами: {wwc(bonus)} (на балансе {wwc(balance)}, останется {wwc(balance - bonus)})")
    out.append("")
    out.append(f"PRO сейчас до: {_date(intent.get('paid_until'))}")
    out.append(f"CLUB сейчас до: {_date(intent.get('club_paid_until'))}")
    out.append("")
    out.append("Сумма сходится. Провести операцию?" if not bonus else "Сумма сходится: строки = получено + бонусы. Провести операцию?")
    return "\n".join(out)


async def _handle_multiline_payment(
    tenant: TenantContext, msg: Any, update: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any]:
    parsed = parse_payment_command(msg.text)
    message_id = _message_id(update)
    if message_id is None:
        raise SubscriptionError("Не удалось определить сообщение. Отправьте команду ещё раз.")
    intent = await create_lines_intent(
        tenant.tenant_id,
        identifier=parsed.identifier,
        lines=parsed.lines,
        received_minor=parsed.received_minor,
        currency=parsed.currency,
        telegram_chat_id=msg.chat_id,
        telegram_message_id=message_id,
        telegram_user_id=msg.user_id,
        bonus_minor=parsed.bonus_minor,
    )
    await _deliver(msg.chat_id, _lines_preview(intent), reply_markup=_intent_keyboard(intent["intent_id"]))
    return {**base, "status": "preview", "intent_id": str(intent["intent_id"]), "multiline": True}


_PRICE_PRODUCTS = {
    "pro": "platform_subscription", "платформа": "platform_subscription", "сайт": "platform_subscription",
    "клуб": "club_subscription", "club": "club_subscription",
    "настройка": "site_setup", "настройка сайта": "site_setup", "setup": "site_setup",
    "курс": "course_academy", "академия": "course_academy", "course": "course_academy",
}


async def _handle_price_command(tenant: TenantContext, msg: Any, base: dict[str, Any]) -> dict[str, Any]:
    match = _PRICE_RE.fullmatch(msg.text.strip())
    if not match:
        raise SubscriptionError(_PRICE_USAGE)
    identifier, product_word, action, raw_amount, _currency, reason = match.groups()
    product_code = _PRICE_PRODUCTS[re.sub(r"\s+", " ", product_word.lower())]
    partner = await resolve_partner_for_billing(tenant.tenant_id, identifier)
    revoke = raw_amount is None
    if not revoke and not (reason or "").strip():
        raise SubscriptionError("Укажите причину персональной цены — она сохраняется навсегда.")
    price_minor = None if revoke else int(round(float(raw_amount.replace(",", ".")) * 100))
    token = __import__("uuid").uuid4().hex
    _PRICE_INTENTS[token] = {
        "ref_code": partner["ref_code"], "product_code": product_code, "price_wusd_minor": price_minor,
        "reason": (reason or "").strip(), "user_id": msg.user_id,
    }
    label = PRODUCT_LABELS[product_code]
    text = (
        f"{partner['display_name']} (ref:{partner['ref_code']})\n"
        + (f"Снять персональную цену на {label}, вернуть общий тариф?" if revoke
           else f"Персональная цена: {label}: {both(price_minor, 'WUSD')} — действует до отмены.\nПричина: {reason.strip()}\n\nЗаписать?")
    )
    await _deliver(
        msg.chat_id, text,
        reply_markup={"inline_keyboard": [[
            {"text": "Подтвердить", "callback_data": f"billing:price:{token}"},
            {"text": "Отмена", "callback_data": f"billing:price_cancel:{token}"},
        ]]},
    )
    return {**base, "status": "price_preview"}


async def _handle_price_callback(
    tenant: TenantContext, callback: Any, action: str, token: str, base: dict[str, Any]
) -> dict[str, Any]:
    pending = _PRICE_INTENTS.pop(token, None)
    if not pending or pending["user_id"] != callback.user_id:
        await _deliver(callback.chat_id, "Подтверждение недействительно или устарело. Отправьте команду ещё раз.")
        return {**base, "ok": False, "status": "invalid_callback"}
    if action == "price_cancel":
        await _deliver(callback.chat_id, "Отменено.")
        return {**base, "status": "cancelled"}
    result = await set_personal_price(
        tenant.tenant_id,
        ref_code=pending["ref_code"],
        product_code=pending["product_code"],
        price_wusd_minor=pending["price_wusd_minor"],
        reason=pending["reason"],
        approved_by_telegram_user_id=callback.user_id,
    )
    label = PRODUCT_LABELS[pending["product_code"]]
    if pending["price_wusd_minor"] is None:
        await _deliver(callback.chat_id, f"Готово: у ref:{pending['ref_code']} снова общий тариф на {label}.")
    else:
        await _deliver(callback.chat_id, f"Готово: ref:{pending['ref_code']} — {label} по {both(int(result['price_wusd_minor']), 'WUSD')}.")
    return {**base, "status": "price_set"}


# ----------------------------------------------------------------------------
# «Безлимит»: the owner's technical site and the co-founder's site never expire
# ----------------------------------------------------------------------------

async def _handle_unlimited_command(tenant: TenantContext, msg: Any, base: dict[str, Any]) -> dict[str, Any]:
    match = _UNLIMITED_RE.fullmatch(msg.text.strip())
    if not match:
        raise SubscriptionError(_UNLIMITED_USAGE)
    partner = await resolve_partner_for_billing(tenant.tenant_id, match.group(1))
    token = __import__("uuid").uuid4().hex
    _UNLIMITED_INTENTS[token] = {"ref_code": partner["ref_code"], "user_id": msg.user_id}
    text = (
        f"{partner['display_name']} (ref:{partner['ref_code']})\n"
        f"Сайт: {partner['hostname']}\n"
        f"PRO сейчас до: {_date(partner.get('paid_until'))}\n\n"
        "Безлимит: PRO без срока (до 31.12.2099), без платежа и без бонусов.\n\nЗаписать?"
    )
    await _deliver(
        msg.chat_id, text,
        reply_markup={"inline_keyboard": [[
            {"text": "Подтвердить", "callback_data": f"billing:unlimited:{token}"},
            {"text": "Отмена", "callback_data": f"billing:unlimited_cancel:{token}"},
        ]]},
    )
    return {**base, "status": "unlimited_preview"}


async def _handle_unlimited_callback(
    tenant: TenantContext, callback: Any, action: str, token: str, base: dict[str, Any]
) -> dict[str, Any]:
    pending = _UNLIMITED_INTENTS.pop(token, None)
    if not pending or pending["user_id"] != callback.user_id:
        await _deliver(callback.chat_id, "Подтверждение недействительно или устарело. Отправьте команду ещё раз.")
        return {**base, "ok": False, "status": "invalid_callback"}
    if action == "unlimited_cancel":
        await _deliver(callback.chat_id, "Отменено.")
        return {**base, "status": "cancelled"}
    result = await set_unlimited_access(tenant.tenant_id, ref_code=pending["ref_code"])
    await _deliver(callback.chat_id, f"Готово: ref:{result['ref_code']} — PRO до {_date(result['paid_until'])} (безлимит).")
    return {**base, "status": "unlimited_set"}
