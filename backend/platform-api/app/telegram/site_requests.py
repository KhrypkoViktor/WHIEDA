"""Telegram UX for collecting a paid personal-site request."""

from __future__ import annotations

import re
from typing import Any

from app.referral_bonus.service import (
    ensure_telegram_actor,
    referral_payment_notification_context,
)
from app.settings import get_settings
from app.site_requests.service import (
    SiteRequestError,
    begin_site_request,
    confirm_site_request,
    get_open_site_request,
    reject_site_request,
    set_site_request_country,
    set_site_request_intro,
    set_site_request_photo,
    set_site_request_plan,
    set_site_request_subdomain,
    submit_site_payment_proof,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.money import PAYMENT_BY, PAYMENT_RU, both, money, wwc, wwc_signed
from app.telegram.delivery import (
    answer_callback_query,
    copy_telegram_message,
    send_telegram_text,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext


_CALLBACK_RE = re.compile(
    r"^site:(create|country:(?:BY|RU)|plan:(?:site|bundle)|confirm|reject)(?::([0-9a-f]{32}))?$"
)


def _owner_allowed(user_id: int) -> bool:
    configured = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    return configured.isdigit() and int(configured) == int(user_id)


def _request_token(request_id: Any) -> str:
    return str(request_id).replace("-", "")




async def _notify_referrer(request: dict[str, Any]) -> None:
    payment = request.get("payment") or {}
    bonus = payment.get("referral_bonus") or {}
    if not bonus or bonus.get("idempotent") or not bonus.get("actor_id"):
        return
    context = await referral_payment_notification_context(
        str(payment["tenant_id"]),
        ref_code=str(payment["ref_code"]),
        inviter_actor_id=str(bonus["actor_id"]),
    )
    inviter = context.get("inviter") or {}
    chat_id = str(inviter.get("telegram_chat_id") or "").strip()
    if not chat_id:
        return
    lines = [
        f"{request['requested_subdomain']}.wwc.best подключился к платформе.",
        f"Начислено: {wwc_signed(int(bonus['amount_minor']))}.",
    ]
    if bonus.get("balance_points") is not None:
        lines.append(f"Баланс: {wwc(int(bonus['balance_points']))}.")
    lines.append("Личный кабинет: /cabinet")
    await _deliver(int(chat_id), "\n".join(lines))


_STEP_LABELS = {
    "awaiting_subdomain": "адрес сайта",
    "awaiting_photo": "фото",
    "awaiting_text": "текст о себе",
    "awaiting_plan": "выбор пакета",
    "awaiting_payment": "оплата",
}


async def _notify_owner_step(msg: TelegramMessage, request: dict[str, Any], *, done: str) -> None:
    """Владелец узнаёт о каждом шаге заявки, а не только о чеке: люди бросали
    анкету на адресе или фото, и об этом никто не знал (владелец, 22.09.2026:
    «мне нужны алерты в бота, когда заполняют данные»). Фото копируется
    владельцу сразу — раньше его слали ему в личку отдельно."""
    owner_id = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    if not owner_id.isdigit() or int(owner_id) == int(msg.chat_id):
        return
    who = f"@{msg.username}" if msg.username else str(msg.chat_id)
    if done == "фото" and msg.file_id:
        await copy_telegram_message(
            chat_id=owner_id,
            from_chat_id=str(msg.chat_id),
            message_id=msg.message_id,
            bot_token=current_bot_binding().bot_token,
        )
    subdomain = request.get("requested_subdomain")
    lines = [
        f"Заявка на сайт — {who}: {done} получено.",
        f"Адрес: {subdomain}.wwc.best" if subdomain else "Адрес: ещё не выбран",
        f"Дальше: {_STEP_LABELS.get(str(request.get('status')), request.get('status'))}.",
    ]
    if done == "текст о себе" and request.get("intro_text"):
        lines.append("")
        lines.append(str(request["intro_text"])[:700])
    await _deliver(int(owner_id), chr(10).join(lines))


def _payment_text(request: dict[str, Any]) -> str:
    total = both(int(request["total_amount_minor"]), str(request["currency"]))
    rub = request["currency"] == "RUB"
    if str(request.get("plan_code") or "site") == "bundle":
        what = (
            "Платформа + Клуб на 3 месяца (сайт 3 000 ₽ + клуб 7 500 ₽ по акции, настройка в подарок)"
            if rub else
            "Платформа + Клуб на 3 месяца (сайт 30 WWC$ + клуб 75 WWC$ по акции, настройка в подарок)"
        )
    else:
        what = (
            "Сайт на 3 месяца (PRO 3 000 ₽) и его настройка (2 000 ₽)"
            if rub else
            "Сайт на 3 месяца (PRO 30 WWC$) и его настройка (20 WWC$)"
        )
    return f"{what}: {total}.\n{PAYMENT_RU if rub else PAYMENT_BY}\nПосле перевода пришлите сюда скриншот чека."


async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=binding.bot_token, reply_markup=reply_markup
    )


async def _actor(tenant: TenantContext, msg: TelegramMessage | TelegramCallbackQuery) -> str:
    return await ensure_telegram_actor(
        tenant.tenant_id,
        telegram_user_id=msg.user_id,
        telegram_chat_id=msg.chat_id,
        raw_update=msg.raw,
    )


async def _prompt_for_request(chat_id: int, request: dict[str, Any]) -> None:
    status = str(request["status"])
    if status == "awaiting_country":
        await _deliver(
            chat_id,
            "В какой стране вы будете оплачивать и работать?",
            reply_markup={"inline_keyboard": [[
                {"text": "Беларусь", "callback_data": "site:country:BY"},
                {"text": "Россия", "callback_data": "site:country:RU"},
            ]]},
        )
    elif status == "awaiting_subdomain":
        await _deliver(
            chat_id,
            "Напишите желаемый адрес сайта латиницей. Например: olesya.\n"
            "Получится: olesya.wwc.best",
        )
    elif status == "awaiting_photo":
        await _deliver(
            chat_id,
            "Пришлите ваше фото. Лучше портрет: вы в кадре, лицо видно, без мелкого текста. "
            "Кадрирование и размер мы подправим сами.",
        )
    elif status == "awaiting_text":
        await _deliver(
            chat_id,
            "Напишите 2-7 предложений о себе, своём опыте и о том, с чем к вам можно обратиться. "
            "Мы сократим и приведём текст к формату сайта.",
        )
    elif status == "awaiting_plan":
        rub = request.get("country_code") == "RU"
        await _deliver(
            chat_id,
            "Что оформляем?",
            reply_markup={"inline_keyboard": [
                [{"text": "Сайт + настройка — 5 000 ₽" if rub else "Сайт + настройка — 50 WWC$", "callback_data": "site:plan:site"}],
                [{"text": "Платформа + Клуб — 10 500 ₽" if rub else "Платформа + Клуб — 105 WWC$", "callback_data": "site:plan:bundle"}],
            ]},
        )
    elif status == "awaiting_payment":
        await _deliver(chat_id, _payment_text(request))
    elif status == "pending_confirmation":
        await _deliver(chat_id, "Чек получен. Виктор проверит оплату и подтвердит заявку.")
    elif status == "pending_provisioning":
        await _deliver(chat_id, "Оплата подтверждена. Данные приняты в работу; сообщим, когда сайт будет готов.")


async def try_handle_site_request_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    binding = current_bot_binding()
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=binding.bot_token)
    if callback.chat_type != "private":
        return {"ok": False, "route": "site_request", "status": "private_chat_required", "trace_id": trace_id}
    action, token = match.groups()
    try:
        if action in {"confirm", "reject"}:
            if not _owner_allowed(callback.user_id) or not token:
                await _deliver(callback.chat_id, "Команда недоступна.")
                return {"ok": False, "route": "site_request_admin", "status": "forbidden", "trace_id": trace_id}
            request_id = f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"
            if action == "reject":
                request = await reject_site_request(
                    tenant.tenant_id, request_id=request_id, admin_telegram_user_id=callback.user_id
                )
                await _deliver(int(request["proof_chat_id"]), "Оплату не удалось подтвердить. Напишите Виктору: @sunraysword.")
                await _deliver(callback.chat_id, "Заявка отклонена.")
                return {"ok": True, "route": "site_request_reject", "trace_id": trace_id}
            request = await confirm_site_request(
                tenant.tenant_id, request_id=request_id, admin_telegram_user_id=callback.user_id
            )
            if not request.get("idempotent"):
                try:
                    await _notify_referrer(request)
                except Exception:
                    # The ledger transaction is already committed. A Telegram
                    # delivery failure must not roll the payment back.
                    pass
                await _deliver(
                    int(request["proof_chat_id"]),
                    "Оплата подтверждена. Данные приняты в работу. Напишем, когда "
                    f"{request['requested_subdomain']}.wwc.best будет готов.",
                )
            await _deliver(callback.chat_id, "Оплата записана. Заявка добавлена в очередь создания сайта.")
            return {"ok": True, "route": "site_request_confirm", "trace_id": trace_id}

        actor_id = await _actor(tenant, callback)
        if action == "create":
            request = await begin_site_request(tenant.tenant_id, actor_id)
        elif action.startswith("plan:"):
            request = await set_site_request_plan(tenant.tenant_id, actor_id, action.rsplit(":", 1)[1])
        else:
            request = await set_site_request_country(
                tenant.tenant_id, actor_id, action.rsplit(":", 1)[1]
            )
        await _prompt_for_request(callback.chat_id, request)
        return {"ok": True, "route": "site_request", "status": request["status"], "trace_id": trace_id}
    except SiteRequestError as exc:
        await _deliver(callback.chat_id, str(exc))
        return {"ok": False, "route": "site_request", "status": "rejected", "trace_id": trace_id}


async def try_handle_site_request_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    if msg.chat_type != "private":
        return None
    if (msg.text or "").strip().startswith("/"):
        return None
    try:
        actor_id = await _actor(tenant, msg)
        request = await get_open_site_request(tenant.tenant_id, actor_id)
    except RuntimeError as exc:
        # Unit-level routing checks do not initialize a database pool. In a live
        # Core process the pool exists before Telegram updates are accepted.
        if "database pool is not initialized" in str(exc):
            return None
        raise
    if not request:
        return None
    try:
        status = str(request["status"])
        if status == "awaiting_subdomain" and msg.text:
            request = await set_site_request_subdomain(tenant.tenant_id, actor_id, msg.text)
            await _notify_owner_step(msg, request, done="адрес сайта")
        elif status == "awaiting_photo" and msg.file_id:
            request = await set_site_request_photo(tenant.tenant_id, actor_id, msg.file_id)
            await _notify_owner_step(msg, request, done="фото")
        elif status == "awaiting_text" and msg.text:
            request = await set_site_request_intro(tenant.tenant_id, actor_id, msg.text)
            await _notify_owner_step(msg, request, done="текст о себе")
        elif status == "awaiting_payment" and msg.file_id:
            request = await submit_site_payment_proof(
                tenant.tenant_id,
                actor_id,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                file_id=msg.file_id,
            )
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
                            "Новая заявка на сайт.",
                            f"Адрес: {request['requested_subdomain']}.wwc.best",
                            f"Страна: {request['country_code']}",
                            f"Пакет: {'Платформа + Клуб' if str(request.get('plan_code') or 'site') == 'bundle' else 'сайт + настройка'}",
                            f"Оплата: {money(int(request['total_amount_minor']), str(request['currency']))}",
                            "Чек выше.",
                        ]
                    ),
                    reply_markup={"inline_keyboard": [[
                        {"text": "Подтвердить", "callback_data": f"site:confirm:{_request_token(request['request_id'])}"},
                        {"text": "Отклонить", "callback_data": f"site:reject:{_request_token(request['request_id'])}"},
                    ]]},
                )
        else:
            await _prompt_for_request(msg.chat_id, request)
            return {"ok": True, "route": "site_request", "status": status, "trace_id": trace_id}
        await _prompt_for_request(msg.chat_id, request)
        return {"ok": True, "route": "site_request", "status": request["status"], "trace_id": trace_id}
    except SiteRequestError as exc:
        await _deliver(msg.chat_id, str(exc))
        return {"ok": False, "route": "site_request", "status": "rejected", "trace_id": trace_id}
