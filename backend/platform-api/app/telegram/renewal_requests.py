"""Telegram UX for partner subscription renewal payment proofs."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.referral_bonus.service import ensure_telegram_actor
from app.renewal_requests.service import (
    set_renewal_plan,
    plan_title,
    list_renewal_offers,
    RenewalRequestError,
    begin_renewal_request,
    cancel_renewal_request,
    confirm_renewal_request,
    get_open_renewal_request,
    reject_renewal_request,
    set_renewal_country,
    set_renewal_period,
    submit_renewal_payment_proof,
)
from app.settings import get_settings
from app.telegram.club_group import invite_to_club
from app.telegram.billing import notify_payment_participants
from app.telegram.bindings import current_bot_binding
from app.telegram.money import PAYMENT_BY, PAYMENT_RU, both, money
from app.telegram.delivery import answer_callback_query, copy_telegram_message, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

_CALLBACK_RE = re.compile(
    r"^renew:(start|cancel|months:(?:3|6|12)|plan:[a-z0-9_]{2,40}|country:(?:BY|RU)|confirm|reject)(?::([0-9a-f]{32}))?$"
)


def _cancel_row() -> list[dict[str, str]]:
    return [{"text": "Отменить", "callback_data": "renew:cancel"}]


def _owner_allowed(user_id: int) -> bool:
    configured = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    return configured.isdigit() and int(configured) == int(user_id)


def _request_token(request_id: Any) -> str:
    return str(request_id).replace("-", "")


def _request_id(token: str) -> str:
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def _amount(amount_minor: int, currency: str) -> str:
    return money(amount_minor, currency)


async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text,
        bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup,
    )


async def _actor(
    tenant: TenantContext, message: TelegramMessage | TelegramCallbackQuery
) -> str:
    return await ensure_telegram_actor(
        tenant.tenant_id,
        telegram_user_id=message.user_id,
        telegram_chat_id=message.chat_id,
        raw_update=message.raw,
    )


def _what(request: dict[str, Any]) -> str:
    """Что оплачивается — для партнёра и для владельца."""
    title = str(request.get("plan_title") or "").strip()
    return title or f"Сайт на {request['access_months']} мес."


def _payment_text(request: dict[str, Any]) -> str:
    amount = both(int(request["amount_minor"]), str(request["currency"]))
    details = PAYMENT_RU if request["country_code"] == "RU" else PAYMENT_BY
    return "\n".join(
        [
            f"{_what(request)}: {amount}.",
            details,
            "После перевода пришлите сюда скриншот чека.",
        ]
    )


def _offer_button(offer: dict[str, Any]) -> list[dict[str, str]]:
    wusd = int(offer["price_wusd_minor"]) // 100
    rub = int(offer["price_rub_minor"]) // 100
    rub_text = f"{rub:,}".replace(",", " ")
    return [{
        "text": f"{offer['title']} — {wusd} W$ / {rub_text} ₽",
        "callback_data": f"renew:plan:{offer['plan_code']}",
    }]


async def _prompt(chat_id: int, request: dict[str, Any], tenant_id: str) -> None:
    status = str(request["status"])
    if status == "awaiting_period":
        # Все услуги из каталога тарифов (V13): сайт на 3/6/12, сайт + клуб, курсы.
        offers = await list_renewal_offers(tenant_id)
        await _deliver(
            chat_id,
            "Что оплачиваете?",
            reply_markup={"inline_keyboard": [_offer_button(o) for o in offers] + [_cancel_row()]},
        )
    elif status == "awaiting_country":
        await _deliver(
            chat_id,
            "Выберите страну оплаты.",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Беларусь", "callback_data": "renew:country:BY"},
                    {"text": "Россия", "callback_data": "renew:country:RU"},
                ], _cancel_row()]
            },
        )
    elif status == "awaiting_payment":
        request = {**request, "plan_title": await plan_title(tenant_id, request.get("plan_code"))}
        await _deliver(
            chat_id,
            _payment_text(request),
            reply_markup={"inline_keyboard": [_cancel_row()]},
        )
    elif status == "pending_confirmation":
        await _deliver(chat_id, "Чек получен. Виктор проверит оплату и подтвердит продление.")


async def try_handle_renewal_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    await answer_callback_query(
        callback_query_id=callback.callback_query_id,
        bot_token=current_bot_binding().bot_token,
    )
    if callback.chat_type != "private":
        return {"ok": False, "route": "renewal", "status": "private_chat_required", "trace_id": trace_id}
    action, token = match.groups()
    try:
        if action in {"confirm", "reject"}:
            if not _owner_allowed(callback.user_id) or not token:
                await _deliver(callback.chat_id, "Команда недоступна.")
                return {"ok": False, "route": "renewal_admin", "status": "forbidden", "trace_id": trace_id}
            if action == "reject":
                request = await reject_renewal_request(
                    tenant.tenant_id,
                    request_id=_request_id(token),
                    admin_telegram_user_id=callback.user_id,
                )
                await _deliver(int(request["proof_chat_id"]), "Оплату не удалось подтвердить. Напишите Виктору.")
                await _deliver(callback.chat_id, "Заявка на продление отклонена.")
                return {"ok": True, "route": "renewal_reject", "trace_id": trace_id}
            request = await confirm_renewal_request(
                tenant.tenant_id,
                request_id=_request_id(token),
                admin_telegram_user_id=callback.user_id,
            )
            payment = request.get("payment") or {}
            if payment and not request.get("idempotent"):
                try:
                    await notify_payment_participants(payment)
                except Exception:
                    logger.exception(
                        "renewal_participant_notification_failed",
                        extra={"trace_id": trace_id, "payment_id": str(payment.get("payment_id"))},
                    )
            if not request.get("idempotent") and str(request.get("plan_code") or "").startswith("bundle"):
                try:
                    chat_id = int(request["proof_chat_id"])
                    await invite_to_club(chat_id, chat_id, str(request.get("ref_code") or ""))
                except Exception:
                    logger.exception("renewal_club_invite_failed", extra={"trace_id": trace_id})
            await _deliver(
                callback.chat_id,
                "Продление подтверждено." if not request.get("idempotent") else "Продление уже было подтверждено.",
            )
            return {
                "ok": True,
                "route": "renewal_confirm",
                "idempotent": bool(request.get("idempotent")),
                "trace_id": trace_id,
            }

        actor_id = await _actor(tenant, callback)
        if action == "start":
            request = await begin_renewal_request(tenant.tenant_id, actor_id)
        elif action == "cancel":
            await cancel_renewal_request(tenant.tenant_id, actor_id)
            await _deliver(callback.chat_id, "Продление отменено. Вернуться можно через личный кабинет.")
            return {"ok": True, "route": "renewal_cancel", "status": "cancelled", "trace_id": trace_id}
        elif action.startswith("plan:"):
            request = await set_renewal_plan(tenant.tenant_id, actor_id, action.split(":", 1)[1])
        elif action.startswith("months:"):
            # Старые кнопки «3/6/12 месяцев», уже разосланные в чатах.
            request = await set_renewal_period(
                tenant.tenant_id, actor_id, int(action.rsplit(":", 1)[1])
            )
        else:
            request = await set_renewal_country(
                tenant.tenant_id, actor_id, action.rsplit(":", 1)[1]
            )
        await _prompt(callback.chat_id, request, tenant.tenant_id)
        return {"ok": True, "route": "renewal", "status": request["status"], "trace_id": trace_id}
    except RenewalRequestError as exc:
        await _deliver(callback.chat_id, str(exc))
        return {"ok": False, "route": "renewal", "status": "rejected", "trace_id": trace_id}


async def try_handle_renewal_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    if msg.chat_type != "private":
        return None
    if (msg.text or "").strip().startswith("/"):
        return None
    try:
        actor_id = await _actor(tenant, msg)
        request = await get_open_renewal_request(tenant.tenant_id, actor_id)
    except RuntimeError as exc:
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if not request:
        return None
    try:
        if request["status"] == "awaiting_payment" and msg.file_id:
            request = await submit_renewal_payment_proof(
                tenant.tenant_id,
                actor_id,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                file_id=msg.file_id,
            )
            what = await plan_title(tenant.tenant_id, request.get("plan_code"))
            what = what or "Сайт на %s мес." % request["access_months"]
            owner_id = str(get_settings().platform_billing_owner_telegram_id or "").strip()
            if owner_id.isdigit():
                await copy_telegram_message(
                    chat_id=owner_id,
                    from_chat_id=str(msg.chat_id),
                    message_id=msg.message_id,
                    bot_token=current_bot_binding().bot_token,
                )
                await _deliver(
                    int(owner_id),
                    "\n".join(
                        [
                            "Оплата услуги.",
                            f"Партнёр: {request['ref_code']}",
                            f"Что: {what}",
                            f"Оплата: {_amount(int(request['amount_minor']), str(request['currency']))}",
                            "Чек выше.",
                        ]
                    ),
                    reply_markup={
                        "inline_keyboard": [[
                            {"text": "Подтвердить", "callback_data": f"renew:confirm:{_request_token(request['request_id'])}"},
                            {"text": "Отклонить", "callback_data": f"renew:reject:{_request_token(request['request_id'])}"},
                        ]]
                    },
                )
        await _prompt(msg.chat_id, request, tenant.tenant_id)
        return {"ok": True, "route": "renewal", "status": request["status"], "trace_id": trace_id}
    except RenewalRequestError as exc:
        await _deliver(msg.chat_id, str(exc))
        return {"ok": False, "route": "renewal", "status": "rejected", "trace_id": trace_id}


# Партнёры пишут словами, а не жмут кнопки: «оплата», «продлить», «продление».
# До 22.09.2026 такое сообщение попадало в командную ветку владельца и партнёр
# получал «Команда недоступна». Теперь слово открывает то же продление, что и
# кнопка в кабинете; у кого персонального сайта нет — отвечает советник.
_RENEWAL_WORDS_RE = re.compile(
    r"^(?:оплат(?:а|ить|у)|продл(?:ить|ение|и)|продлить\s+платформу|оплатить\s+сайт)[\s!.?]*$",
    re.IGNORECASE,
)


def is_renewal_request_text(text: str) -> bool:
    return bool(_RENEWAL_WORDS_RE.match(str(text or "").strip()))


async def try_start_renewal_by_text(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    if msg.chat_type != "private" or not is_renewal_request_text(msg.text):
        return None
    if _owner_allowed(msg.user_id):
        # У владельца «оплата …» — команда биллинга, её разбирает billing.py.
        return None
    actor_id = await _actor(tenant, msg)
    try:
        request = await begin_renewal_request(tenant.tenant_id, actor_id)
    except RenewalRequestError:
        # Сайта нет — это не партнёр, пусть отвечает советник.
        return None
    await _prompt(msg.chat_id, request, tenant.tenant_id)
    return {"ok": True, "route": "renewal", "status": request["status"], "trace_id": trace_id}
