"""Core Telegram update handler — local/staging ready, no live webhook change."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.advisor.service import handle_structured_query
from app.advisor.sql.text import detect_service_intent
from app.identity.service import exchange_telegram_link_token
from app.leads.actor_link import fill_lead_actor_user_id, link_lead_actor_by_username
from app.onboarding.service import handle_onboarding_text
from app.referral_bonus.service import accept_referral_start, parse_referral_start_token
from app.settings import get_settings
from app.telegram.referral_bonus import (
    show_referral_dashboard,
    try_handle_referral_callback,
    try_handle_referral_message,
)
from app.telegram.renewal_requests import (
    try_handle_renewal_callback,
    try_handle_renewal_message,
)
from app.telegram.site_requests import (
    try_handle_site_request_callback,
    try_handle_site_request_message,
)
from app.telegram.referral_admin import (
    try_handle_referral_admin_callback,
    try_handle_referral_admin_message,
)
from app.telegram.admin_login import try_handle_admin_login
from app.telegram.billing import try_handle_billing_callback, try_handle_billing_message
from app.telegram.content_access import try_handle_content_access
from app.telegram.pro_start import handle_pro_start, is_pro_start_token
from app.telegram.support import (
    try_handle_support_callback,
    try_handle_support_message,
    try_relay_user_message,
)
from app.telegram.bindings import (
    BotBindingContext,
    binding_context_scope,
    current_bot_binding,
)
from app.telegram.log_safe import chat_ref
from app.telegram.catalog_browse import (
    handle_callback_query,
    handle_navigation_text,
    handle_newcomer_panel,
)
from app.telegram.delivery import (
    answer_callback_query,
    deliver_structured_advisor_response,
    send_telegram_text,
)
from app.telegram.modes import should_deliver_telegram_response
from app.telegram.navigation import (
    advisor_followup_inline_keyboard,
    is_newcomer_panel_request,
    main_menu_reply_keyboard,
)
from app.telegram.update_parser import (
    TelegramMessage,
    parse_start_token,
    parse_telegram_callback,
    parse_telegram_message,
    should_process_telegram_message,
)
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

_MANUAL_OPERATION_CALLBACK_PREFIXES = (
    "billing:",
    "renew:",
    "site:",
    "referral:redeem:",
    "referral:confirm:",
    "referral:cancel:",
)


def _manual_partner_operations_only() -> bool:
    return get_settings().telegram_ui_profile == "minimal"


async def _manual_operation_notice(chat_id: int, callback_query_id: str | None = None) -> None:
    binding = current_bot_binding()
    if callback_query_id:
        await answer_callback_query(
            callback_query_id=callback_query_id,
            bot_token=binding.bot_token,
        )
    await deliver_text(
        chat_id,
        "Подключение сайта, продление и оплата сейчас оформляются вручную. Напишите Виктору: @sunraysword.",
    )


async def _remove_legacy_reply_keyboard(chat_id: int) -> None:
    """Telegram keeps an old reply keyboard until a later message removes it."""
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id),
        text="Меню обновлено. Открываю личный кабинет.",
        bot_token=binding.bot_token,
        reply_markup=main_menu_reply_keyboard(),
    )


async def deliver_text(chat_id: int | str, text: str) -> None:
    if not text.strip():
        return
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text.strip(),
        bot_token=binding.bot_token,
    )


async def deliver_advisor_response(
    chat_id: int | str,
    core_response: dict[str, Any],
    *,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    binding = current_bot_binding()
    await deliver_structured_advisor_response(
        chat_id,
        core_response,
        bot_token=binding.bot_token,
        reply_markup=reply_markup,
    )


async def handle_start_token(
    tenant: TenantContext,
    msg: TelegramMessage,
    token: str,
    trace_id: str,
) -> dict[str, Any]:
    result = await exchange_telegram_link_token(
        tenant.tenant_id,
        token,
        telegram_user_id=msg.user_id,
        telegram_chat_id=msg.chat_id,
    )
    lines = ["Связь с сайтом установлена."]
    if result.mentor_display_name:
        lines.append(f"Ваш наставник: {result.mentor_display_name}.")
    elif result.first_ref:
        lines.append("Наставник назначен по вашей персональной ссылке.")
    topic = (result.context or {}).get("topic")
    context = result.context or {}
    product = context.get("last_product_name") or context.get("last_product_sku")
    if topic or product:
        lines.append(f"Последняя тема: {product or topic}.")
    lines.append("Напишите вопрос по товару или «начать обучение» для 7-дневного плана.")
    await deliver_text(msg.chat_id, "\n".join(lines))
    return {"ok": True, "route": "start_token", "link_id": result.link_id, "trace_id": trace_id}


async def handle_referral_start_token(
    tenant: TenantContext, msg: TelegramMessage, token: str, trace_id: str
) -> dict[str, Any]:
    invite_code = parse_referral_start_token(token)
    if invite_code is None:
        raise ValueError("not a referral start token")
    if msg.chat_type != "private":
        return {"ok": True, "route": "referral_start", "status": "private_chat_required"}
    if not invite_code:
        await deliver_text(msg.chat_id, "Ссылка-приглашение недействительна.")
        return {"ok": False, "route": "referral_start", "status": "invalid", "trace_id": trace_id}
    result = await accept_referral_start(
        tenant.tenant_id, telegram_user_id=msg.user_id, telegram_chat_id=msg.chat_id,
        invite_code=invite_code, raw_update=msg.raw,
    )
    messages = {
        "attributed": "Приглашение сохранено. Напишите «с чего начать», чтобы посмотреть возможности бота.",
        "already_registered": "Вы уже знакомы с ботом. Пригласивший автоматически не меняется.",
        "invalid": "Ссылка-приглашение недействительна или больше не активна.",
        "self_referral": "Свою реферальную ссылку нельзя использовать для себя.",
    }
    await deliver_text(msg.chat_id, messages[result.status])
    return {"ok": result.status == "attributed", "route": "referral_start", "status": result.status, "trace_id": trace_id}


async def handle_onboarding(
    tenant: TenantContext,
    msg: TelegramMessage,
    *,
    first_ref: str | None = None,
) -> dict[str, Any] | None:
    result = await handle_onboarding_text(
        tenant.tenant_id,
        telegram_user_id=msg.user_id,
        text=msg.text,
        first_ref=first_ref,
    )
    if not result:
        return None
    answer = str(result.get("answer_text") or "").strip()
    if answer:
        await deliver_text(msg.chat_id, answer)
    return {"ok": True, "route": "onboarding", **result}


async def handle_advisor_query(
    tenant: TenantContext,
    msg: TelegramMessage,
    trace_id: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session": f"telegram:{msg.chat_id}",
        "question": msg.text,
        "surface": "telegram",
        "country": "BY",
        "language": "ru",
    }
    core_response = await handle_structured_query(tenant, body, trace_id)
    mode = core_response.get("answer_mode")
    logger.info(
        "telegram_advisor_response_ready",
        extra={
            "trace_id": trace_id,
            "chat_id": chat_ref(msg.chat_id),
            "text_hash": hashlib.sha256(msg.text.encode("utf-8")).hexdigest()[:12],
            "answer_mode": mode,
            "gap_kind": core_response.get("gap_kind"),
        },
    )
    if should_deliver_telegram_response(core_response):
        include_calculator = tenant.tenant_id == "whieda"
        inline = advisor_followup_inline_keyboard(
            core_response,
            include_calculator=include_calculator,
        )
        await deliver_advisor_response(
            msg.chat_id,
            core_response,
            reply_markup=inline
            or main_menu_reply_keyboard(include_calculator=include_calculator),
        )
    elif mode and str(mode) not in {"", "fallback", "error"}:
        logger.warning(
            "telegram_response_not_delivered",
            extra={"answer_mode": mode, "trace_id": trace_id},
        )
    return {"ok": True, "route": "advisor", "answer_mode": mode, "trace_id": trace_id}


async def process_core_telegram_update(
    tenant: TenantContext,
    update: dict[str, Any],
    trace_id: str,
    *,
    binding: BotBindingContext,
) -> dict[str, Any]:
    """Full Core path with owner billing before generic callbacks and advisor routes."""
    with binding_context_scope(binding):
        return await _process_core_telegram_update_scoped(tenant, update, trace_id)


async def _link_partner_chat(tenant: TenantContext, msg: TelegramMessage, trace_id: str) -> None:
    """Bind a partner's chat to lead_actors by username and complete a chat-only
    row with the numeric user id; the reply must never wait on either."""
    try:
        await link_lead_actor_by_username(
            tenant.tenant_id,
            username=msg.username,
            telegram_user_id=msg.user_id,
            telegram_chat_id=msg.chat_id,
        )
        await fill_lead_actor_user_id(
            tenant.tenant_id,
            telegram_user_id=msg.user_id,
            telegram_chat_id=msg.chat_id,
        )
    except Exception:
        logger.warning(
            "lead_actor_telegram_link_failed",
            extra={"trace_id": trace_id, "chat_id": chat_ref(msg.chat_id)},
            exc_info=True,
        )


async def _process_core_telegram_update_scoped(
    tenant: TenantContext,
    update: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    manual_operations = _manual_partner_operations_only()
    admin_result = await try_handle_admin_login(update, trace_id=trace_id)
    if admin_result is not None:
        return admin_result

    content_result = await try_handle_content_access(update, trace_id=trace_id)
    if content_result is not None:
        return content_result

    if not manual_operations:
        billing_callback_result = await try_handle_billing_callback(
            tenant, update, trace_id=trace_id
        )
        if billing_callback_result is not None:
            return billing_callback_result

    referral_admin_callback_result = await try_handle_referral_admin_callback(
        tenant, update, trace_id=trace_id
    )
    if referral_admin_callback_result is not None:
        return referral_admin_callback_result

    callback = parse_telegram_callback(update)
    if callback:
        if callback.chat_type != "private":
            return {"ok": True, "route": "ignored_group_callback"}
        if manual_operations and callback.data.startswith(_MANUAL_OPERATION_CALLBACK_PREFIXES):
            await _manual_operation_notice(callback.chat_id, callback.callback_query_id)
            return {"ok": True, "route": "manual_partner_operation", "trace_id": trace_id}
        support_callback_result = await try_handle_support_callback(
            tenant, callback, trace_id=trace_id
        )
        if support_callback_result is not None:
            return support_callback_result
        renewal_callback_result = await try_handle_renewal_callback(
            tenant, callback, trace_id=trace_id
        )
        if renewal_callback_result is not None:
            return renewal_callback_result
        site_request_callback_result = await try_handle_site_request_callback(
            tenant, callback, trace_id=trace_id
        )
        if site_request_callback_result is not None:
            return site_request_callback_result
        referral_callback_result = await try_handle_referral_callback(
            tenant, callback, trace_id=trace_id
        )
        if referral_callback_result is not None:
            return referral_callback_result
        return await handle_callback_query(tenant, callback, trace_id)

    msg = parse_telegram_message(update)
    if not msg:
        return {"ok": True, "route": "ignored"}

    if not should_process_telegram_message(msg, current_bot_binding().bot_username):
        return {"ok": True, "route": "ignored_group_message"}

    if msg.chat_type == "private":
        await _link_partner_chat(tenant, msg, trace_id)

    # Services card, the support administrator's replies, and attachments from
    # a subscriber inside an open support ticket.
    support_result = await try_handle_support_message(tenant, msg, trace_id=trace_id)
    if support_result is not None:
        return support_result

    referral_result = await try_handle_referral_message(tenant, msg, trace_id=trace_id)
    if referral_result is not None:
        return referral_result

    if not manual_operations:
        renewal_result = await try_handle_renewal_message(tenant, msg, trace_id=trace_id)
        if renewal_result is not None:
            return renewal_result

        site_request_result = await try_handle_site_request_message(tenant, msg, trace_id=trace_id)
        if site_request_result is not None:
            return site_request_result

    if not msg.text:
        return {"ok": True, "route": "ignored_media"}

    if not manual_operations:
        billing_result = await try_handle_billing_message(tenant, update, trace_id=trace_id)
        if billing_result is not None:
            return billing_result

    referral_admin_result = await try_handle_referral_admin_message(
        tenant, update, trace_id=trace_id
    )
    if referral_admin_result is not None:
        return referral_admin_result

    start_token = parse_start_token(msg.text)
    if start_token:
        if parse_referral_start_token(start_token) is not None:
            return await handle_referral_start_token(tenant, msg, start_token, trace_id)
        if is_pro_start_token(start_token):
            if manual_operations:
                await _manual_operation_notice(msg.chat_id)
                return {"ok": True, "route": "manual_partner_operation", "trace_id": trace_id}
            return await handle_pro_start(tenant, msg, trace_id)
        return await handle_start_token(tenant, msg, start_token, trace_id)

    onboarding_result = await handle_onboarding(tenant, msg)
    if onboarding_result:
        return onboarding_result

    if is_newcomer_panel_request(msg.text) or detect_service_intent(msg.text) == "greeting":
        if manual_operations:
            await _remove_legacy_reply_keyboard(msg.chat_id)
            return await show_referral_dashboard(
                tenant,
                telegram_user_id=msg.user_id,
                telegram_chat_id=msg.chat_id,
                raw_update=msg.raw,
                trace_id=trace_id,
            )
        return await handle_newcomer_panel(tenant, msg.chat_id, trace_id=trace_id)

    navigation_result = await handle_navigation_text(tenant, msg, trace_id)
    if navigation_result:
        return navigation_result

    # Inside an open support ticket, free text goes to the administrator, not
    # to the advisor. Commands and menu buttons above still work as usual.
    relay_result = await try_relay_user_message(tenant, msg, trace_id=trace_id)
    if relay_result is not None:
        return relay_result

    return await handle_advisor_query(tenant, msg, trace_id)
