"""Telegram presentation layer for the WWC referral bonus service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from app.cart.web_links import CALCULATOR_WEB_URL
from app.referral_bonus.service import (
    InviterCard,
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
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.money import wwc, wwc_signed
from app.telegram.support import SERVICES_CARD_CALLBACK
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
    return wwc(amount_minor)


def _points(amount_minor: int) -> str:
    return wwc_signed(amount_minor)


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


# Google-форма «Данные для вашего сайта WWC»; ref_code подставляется ссылкой.
# Из бота уходит «новый сайт · бот · <telegram user id>» — без имени, но
# владелец находит человека по id (владелец, 15.09.2026).
SITE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScu0yDkGGw5uKjRoNDUvzTA6lQBCZywnjSGFmhLv_zcHXPnGw/viewform?usp=pp_url&entry.1166182770="


def site_form_url(telegram_user_id: int | None, *, inviter_ref: str | None = None) -> str:
    tail = f"новый сайт · бот · {int(telegram_user_id)}" if telegram_user_id else "новый сайт · бот"
    if inviter_ref:
        tail += f" · от {inviter_ref}"
    return SITE_FORM_URL + quote(tail, safe="")


def _dashboard_keyboard(
    *,
    bot_username: str,
    invite_code: str,
    site_url: str,
    has_site: bool,
    minimal: bool,
    telegram_user_id: int | None = None,
) -> dict[str, Any]:
    link = f"https://t.me/{bot_username}?start=ref_{invite_code}"
    invite = invitation_text(link)
    share_url = _share_url(link)
    # Сайт заказывается диалогом в боте (страна → адрес → фото → текст → чек):
    # гугл-форма из Telegram открывалась во встроенном браузере и терялась
    # (владелец, 18–19.09.2026). Gemini — карточка сервисов (тоннель к администратору).
    order_site = [{"text": "Заказать сайт WWC", "callback_data": "site:create"}]
    gemini = [{"text": "Подключить Gemini Pro", "callback_data": SERVICES_CARD_CALLBACK}]
    if minimal:
        rows = [
            [{"text": "Мой сайт" if has_site else "Посмотреть WWC", "url": site_url}],
            [{"text": "Скопировать реферальную ссылку", "copy_text": {"text": link}}],
            [{"text": "Отправить приглашение", "url": share_url}],
            [{"text": "Калькулятор", "url": CALCULATOR_WEB_URL}],
            gemini,
            [{"text": "Поддержка", "url": SUPPORT_URL}],
        ]
        if not has_site:
            rows.insert(1, order_site)
        return {"inline_keyboard": rows}

    rows: list[list[dict[str, Any]]] = [
        [{"text": "Мой сайт" if has_site else "Посмотреть WWC", "url": site_url}],
        [{"text": "Скопировать приглашение", "copy_text": {"text": invite}}],
        [{"text": "Отправить приглашение", "url": share_url}],
        [{"text": "Мои рефералы", "callback_data": "referral:list"}],
        [{"text": "История WWC$", "callback_data": "referral:history"}],
        gemini,
        [{"text": "Поддержка", "url": SUPPORT_URL}],
    ]
    rows.insert(1, [{"text": "Продлить платформу", "callback_data": "renew:start"}] if has_site else order_site)
    return {"inline_keyboard": rows}


def _share_url(link: str) -> str:
    """t.me/share/url не раскодирует «+» в пробел — в пересланном тексте были
    плюсы между словами (скрин владельца, 19.09.2026). Кодируем через %20."""
    return "https://t.me/share/url?" + urlencode({"url": link, "text": invitation_text("")}, quote_via=quote)


def _invitation_keyboard(link: str) -> dict[str, Any]:
    invite = invitation_text(link)
    share_url = _share_url(link)
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


def site_offer_text(inviter_name: str) -> str:
    """Один экран под одно действие (текст владельца, 17.09.2026). Без баланса
    и рефссылки: человек ещё ничего не купил, кабинет ему сейчас — шум."""
    # Имя из базы в именительном падеже, склонять нельзя — потому «пригласил партнёр: Имя».
    head = f"Вас пригласил партнёр WWC: {inviter_name}. " if inviter_name else ""
    return (
        f"{head}Такой же сайт — за 1 день, 10 WWC$ в месяц (1 000 ₽ / 35 BYN). "
        "20 WWC$ (2 000 ₽) — разовая настройка сайта.\n"
        "Пакет «Платформа + Клуб» на 3 месяца — 105 WWC$ (10 500 ₽), настройка в подарок: wwc.best/start"
    )


def site_offer_keyboard(*, telegram_user_id: int | None, example_url: str, inviter_ref: str | None = None) -> dict:
    rows = [[{"text": "Заказать сайт", "callback_data": "site:create"}]]
    if example_url:
        host = example_url.split("//", 1)[-1].strip("/")
        rows.append([{"text": f"Посмотреть пример: {host}", "url": example_url}])
    return {"inline_keyboard": rows}


async def show_site_offer(
    tenant: TenantContext,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    inviter: InviterCard | None,
    trace_id: str,
) -> dict[str, Any]:
    name = inviter.display_name if inviter else ""
    example = inviter.site_url if inviter else ""
    await _deliver(
        telegram_chat_id,
        site_offer_text(name),
        reply_markup=site_offer_keyboard(
            telegram_user_id=telegram_user_id, example_url=example, inviter_ref=inviter.ref_code if inviter else None
        ),
    )
    return {"ok": True, "route": "site_offer", "trace_id": trace_id}


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
    minimal = get_settings().telegram_ui_profile == "minimal"
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
    lines = [
            "Личный кабинет",
            "",
            access_line,
            f"Оплачено до: {paid_until_text}",
            f"Сайт: {site_url}",
            f"Баланс: {wwc(int(dashboard['balance_wusd_minor']))}",
            "",
            "Ваша реферальная ссылка:",
            link,
            "",
            f"Приглашено: {dashboard['invited_count']}",
            f"Оплатили сайт: {dashboard['paid_count']}",
    ]
    if not minimal:
        lines.extend(
            [
                "",
                "20% начисляется с первой оплаты платформы и 10% с продлений.",
                "Каждые 30 WWC$ автоматически продлевают ваш сайт ещё на 3 месяца.",
                "WWC$ — внутренняя валюта: ими оплачиваются платформа, клуб и курсы, вывести деньгами нельзя.",
            ]
        )
    text = "\n".join(lines)
    await _deliver(
        telegram_chat_id,
        text,
        reply_markup=_dashboard_keyboard(
            bot_username=username,
            invite_code=invite_code,
            site_url=site_url,
            has_site=bool(site),
            minimal=minimal,
            telegram_user_id=telegram_user_id,
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
