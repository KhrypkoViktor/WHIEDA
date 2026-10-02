"""Telegram UX for collecting a paid personal-site request."""

from __future__ import annotations

import logging
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
    request_is_stale,
    set_site_request_contacts,
    set_site_request_country,
    set_site_request_intro,
    set_site_request_photo,
    set_site_request_plan,
    set_site_request_subdomain,
    submit_site_payment_proof,
)
from app.site_requests.contacts import contacts_summary
from app.support.service import FORUM_KIND_SITE, get_forum, set_site_orders_thread
from app.telegram.club_group import NEWS_CHANNEL_TEXT, invite_to_club
from app.telegram.bindings import current_bot_binding
from app.telegram.money import PAYMENT_BY, PAYMENT_RU, both, money, wwc, wwc_signed
from app.telegram.delivery import (
    answer_callback_query,
    copy_telegram_message,
    create_forum_topic,
    send_telegram_text,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)


# Шаги анкеты, которые ждут вложение: фото и чек.
SITE_FILE_STEPS = frozenset({"awaiting_photo", "awaiting_payment"})

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
    "awaiting_contacts": "контакты",
    "awaiting_plan": "выбор пакета",
    "awaiting_payment": "оплата",
}


def partner_tag(msg: TelegramMessage) -> str:
    """«@name · id 123»: владелец видит, кто это, а его ответ (Reply) в теме
    «Заявки на сайты» бот по id доставляет партнёру."""
    return f"@{msg.username} · id {msg.chat_id}" if msg.username else f"id {msg.chat_id}"


async def _notify_owner_step(tenant_id: str, msg: TelegramMessage, request: dict[str, Any], *, done: str) -> None:
    """Владелец узнаёт о каждом шаге заявки, а не только о чеке: люди бросали
    анкету на адресе или фото, и об этом никто не знал (владелец, 22.09.2026:
    «мне нужны алерты в бота, когда заполняют данные»). Фото копируется
    владельцу сразу — раньше его слали ему в личку отдельно."""
    owner_id = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    if not owner_id.isdigit() or int(owner_id) == int(msg.chat_id):
        return
    who = partner_tag(msg)
    if done == "фото" and msg.file_id:
        await _copy_to_owner(tenant_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
    subdomain = request.get("requested_subdomain")
    lines = [
        f"Заявка на сайт — {who}: {done} получено.",
        f"Адрес: {subdomain}.wwc.best" if subdomain else "Адрес: ещё не выбран",
        f"Дальше: {_STEP_LABELS.get(str(request.get('status')), request.get('status'))}.",
    ]
    if done == "текст о себе" and request.get("intro_text"):
        lines.append("")
        lines.append(str(request["intro_text"])[:700])
    if done == "контакты":
        # Владельцу — и как прислали, и как бот разобрал: разбор подсказка,
        # а исходник решает (V12, 24.09.2026).
        lines.append("")
        lines.append(str(request.get("contacts_text") or "")[:1000])
        parsed = contacts_summary(request.get("contacts") or {})
        if parsed:
            lines.append("")
            lines.append("Бот разобрал:")
            lines.extend(parsed)
    await _send_to_owner(tenant_id, chr(10).join(lines))


def _payment_text(request: dict[str, Any]) -> str:
    total = both(int(request["total_amount_minor"]), str(request["currency"]))
    rub = request["currency"] == "RUB"
    if str(request.get("plan_code") or "site") == "bundle":
        what = (
            "Платформа + Клуб на 3 месяца по акции октября (сайт 3 000 ₽ + клуб 10 000 ₽ вместо 12 000 + подключение 2 000 ₽ вместо 3 000)"
            if rub else
            "Платформа + Клуб на 3 месяца по акции октября (сайт 30 WWC$ + клуб 100 WWC$ вместо 120 + подключение 20 WWC$ вместо 30)"
        )
    else:
        what = (
            "Сайт на 3 месяца (PRO 3 000 ₽) и его подключение (3 000 ₽)"
            if rub else
            "Сайт на 3 месяца (PRO 30 WWC$) и его подключение (30 WWC$)"
        )
    return f"{what}: {total}.\n{PAYMENT_RU if rub else PAYMENT_BY}\nПосле перевода пришлите сюда скриншот чека."


async def _deliver(chat_id: int, text: str, *, reply_markup: dict | None = None, thread_id: int | None = None) -> None:
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=binding.bot_token, reply_markup=reply_markup,
        message_thread_id=thread_id,
    )


ORDERS_TOPIC = "Заявки на сайты"


def _owner_chat() -> int | None:
    value = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    return int(value) if value.isdigit() else None


async def _orders_topic(tenant_id: str) -> tuple[int, int] | None:
    """Тема «Заявки на сайты» в группе поддержки сайтов (kind='site'): туда идут
    шаги анкеты, фото и чеки с «Подтвердить / Отклонить» (владелец, 27.09.2026:
    «почему нет скринов оплаты» — они приходили в личку, а он работает в группе).
    Тему создаём при первой заявке; без группы — None, и всё идёт в личку."""
    binding = current_bot_binding()
    forum = await get_forum(tenant_id, binding_id=binding.binding_id, kind=FORUM_KIND_SITE)
    if not forum:
        return None
    thread = forum.get("reports_thread_id")
    if not thread:
        made = await create_forum_topic(chat_id=str(forum["chat_id"]), name=ORDERS_TOPIC, bot_token=binding.bot_token)
        thread = made.get("message_thread_id") if made.get("ok") else None
        if not thread:
            return None
        await set_site_orders_thread(tenant_id, binding_id=binding.binding_id, thread_id=int(thread))
    return int(forum["chat_id"]), int(thread)


async def _forget_orders_topic(tenant_id: str) -> None:
    """Тему удалили в Telegram — в следующий раз создадим новую."""
    try:
        await set_site_orders_thread(tenant_id, binding_id=current_bot_binding().binding_id, thread_id=None)
    except Exception:
        logger.warning("site_orders_topic_reset_failed", exc_info=True)


async def _send_to_owner(tenant_id: str, text: str, *, reply_markup: dict | None = None) -> None:
    topic = await _orders_topic(tenant_id)
    if topic:
        binding = current_bot_binding()
        sent = await send_telegram_text(
            chat_id=str(topic[0]), text=text, bot_token=binding.bot_token, reply_markup=reply_markup,
            message_thread_id=topic[1],
        )
        if sent.get("ok"):
            return
        await _forget_orders_topic(tenant_id)
    owner = _owner_chat()
    if owner is not None:
        await _deliver(owner, text, reply_markup=reply_markup)


async def _copy_to_owner(tenant_id: str, *, from_chat_id: int, message_id: int) -> None:
    topic = await _orders_topic(tenant_id)
    if topic:
        binding = current_bot_binding()
        copied = await copy_telegram_message(
            chat_id=str(topic[0]), from_chat_id=str(from_chat_id), message_id=int(message_id),
            bot_token=binding.bot_token, message_thread_id=topic[1],
        )
        if copied.get("ok"):
            return
        await _forget_orders_topic(tenant_id)
    owner = _owner_chat()
    if owner is not None:
        await copy_telegram_message(
            chat_id=str(owner), from_chat_id=str(from_chat_id), message_id=int(message_id),
            bot_token=current_bot_binding().bot_token,
        )


async def _is_orders_chat(tenant_id: str, chat_id: int) -> bool:
    """«Подтвердить / Отклонить» нажали в группе поддержки сайтов владельца."""
    forum = await get_forum(tenant_id, binding_id=current_bot_binding().binding_id, kind=FORUM_KIND_SITE)
    return bool(forum) and int(forum["chat_id"]) == int(chat_id)


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
    elif status == "awaiting_contacts":
        # Люди присылают контакты одним сообщением — так и спрашиваем
        # (владелец, 24.09.2026). Разбор по полям делает бот.
        await _deliver(
            chat_id,
            chr(10).join([
                "Какие контакты показать на вашем сайте? Пришлите одним сообщением, каждый с новой строки:",
                "",
                "• телефон",
                "• WhatsApp",
                "• MAX",
                "• e-mail",
                "• ВКонтакте, Instagram, TikTok — ссылкой",
                "• ваш канал или группа — ссылкой (Telegram, ВКонтакте)",
                "",
                "Например:",
                "Телефон и WhatsApp: +7 900 123-45-67",
                "Почта: name@mail.ru",
                "Канал: t.me/mychannel",
                "",
                "Telegram возьмём этот. Чего нет — пропустите. Если ничего добавлять не нужно, напишите «нет».",
            ]),
        )
    elif status == "awaiting_plan":
        rub = request.get("country_code") == "RU"
        await _deliver(
            chat_id,
            "Что оформляем?",
            reply_markup={"inline_keyboard": [
                [{"text": "Сайт + подключение — 6 000 ₽" if rub else "Сайт + подключение — 60 WWC$", "callback_data": "site:plan:site"}],
                [{"text": "Платформа + Клуб — 15 000 ₽" if rub else "Платформа + Клуб — 150 WWC$", "callback_data": "site:plan:bundle"}],
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
    action, token = match.groups()
    in_orders_group = (
        callback.chat_type == "supergroup" and action in {"confirm", "reject"}
        and await _is_orders_chat(tenant.tenant_id, callback.chat_id)
    )
    if callback.chat_type != "private" and not in_orders_group:
        return {"ok": False, "route": "site_request", "status": "private_chat_required", "trace_id": trace_id}
    reply_thread = callback.thread_id if in_orders_group else None
    try:
        if action in {"confirm", "reject"}:
            if not _owner_allowed(callback.user_id) or not token:
                await _deliver(callback.chat_id, "Команда недоступна.", thread_id=reply_thread)
                return {"ok": False, "route": "site_request_admin", "status": "forbidden", "trace_id": trace_id}
            request_id = f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"
            if action == "reject":
                request = await reject_site_request(
                    tenant.tenant_id, request_id=request_id, admin_telegram_user_id=callback.user_id
                )
                await _deliver(int(request["proof_chat_id"]), "Оплату не удалось подтвердить. Напишите Виктору: @sunraysword.")
                await _deliver(callback.chat_id, "Заявка отклонена.", thread_id=reply_thread)
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
                await _welcome_to_club_and_channel(request, trace_id)
            await _deliver(callback.chat_id, "Оплата записана. Заявка добавлена в очередь создания сайта.", thread_id=reply_thread)
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
        await _deliver(callback.chat_id, str(exc), thread_id=reply_thread)
        return {"ok": False, "route": "site_request", "status": "rejected", "trace_id": trace_id}


async def _welcome_to_club_and_channel(request: dict[str, Any], trace_id: str) -> None:
    """Пакет с клубом — ссылка в группу клуба; всем — официальный канал."""
    chat_id = int(request["proof_chat_id"])
    try:
        if str(request.get("plan_code") or "") == "bundle":
            await invite_to_club(chat_id, chat_id, str(request.get("requested_subdomain") or ""))
        else:
            await _deliver(chat_id, NEWS_CHANNEL_TEXT)
    except Exception:
        logger.exception("site_request_club_invite_failed", extra={"trace_id": trace_id})


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
    if request_is_stale(request) and not (str(request["status"]) in SITE_FILE_STEPS and msg.file_id):
        # Брошенная анкета не отвечает на всё подряд: сообщение идёт дальше
        # (советник, меню). Вернуться к ней — кнопкой «Заказать сайт».
        return None
    try:
        status = str(request["status"])
        if status == "awaiting_subdomain" and msg.text:
            request = await set_site_request_subdomain(tenant.tenant_id, actor_id, msg.text)
            await _notify_owner_step(tenant.tenant_id, msg, request, done="адрес сайта")
        elif status == "awaiting_photo" and msg.file_id:
            request = await set_site_request_photo(tenant.tenant_id, actor_id, msg.file_id)
            await _notify_owner_step(tenant.tenant_id, msg, request, done="фото")
        elif status == "awaiting_text" and msg.text:
            request = await set_site_request_intro(tenant.tenant_id, actor_id, msg.text)
            await _notify_owner_step(tenant.tenant_id, msg, request, done="текст о себе")
        elif status == "awaiting_contacts" and msg.text:
            request = await set_site_request_contacts(tenant.tenant_id, actor_id, msg.text)
            await _notify_owner_step(tenant.tenant_id, msg, request, done="контакты")
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
                # Чек — в тему «Заявки на сайты» группы WWC Support (27.09.2026);
                # без группы или при сбое — в личку владельцу, как раньше.
                await _copy_to_owner(tenant.tenant_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
                await _send_to_owner(
                    tenant.tenant_id,
                    "\n".join(
                        [
                            f"Новая заявка на сайт — {partner_tag(msg)}.",
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
