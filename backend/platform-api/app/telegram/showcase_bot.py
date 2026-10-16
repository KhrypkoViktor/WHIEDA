"""Бот витрины автора Академии (V29, 11.10.2026): например @ALMIRA_I_bot — только Академия.

Процессор бота WHIEDA здесь не работает: ученикам студии не нужны кабинет партнёра,
советник по продуктам, онбординг и рассылки WHIEDA. Бот витрины:
- подтверждает вход на сайте витрины (``/start content_access_…``);
- открывает курс по ключу автора (``/start course_<код>``);
- кнопки Академии (``acad:*``), «ключи …» и «мои курсы» автора, «академия»;
- на ``/start`` и любой другой текст — приветствие витрины, кнопка на сайт и курсы.

Личка и группа обращений (/forum, темы). Прочие группы, где состоит бот (у студии это
группа заявок с сайта), бот молча пропускает. Политика обработки данных витрины — один раз, при первом
сообщении (запись в ``telegram_consents``, версия ``showcase:<id>:<дата>``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.academy.keys import parse_course_start_token
from app.academy.showcases import Showcase
from app.referral_bonus.service import ensure_telegram_actor
from app.telegram.academy import handle_course_start_token, show_academy_home, try_handle_academy_callback, try_handle_academy_text
from app.telegram.bindings import current_bot_binding
from app.telegram.consent import record_telegram_consent
from app.telegram.content_access import try_handle_content_access
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.showcase_support import KIND_ADMIN, support_buttons, support_targets, try_handle_callback as try_handle_support_callback, try_handle_forum_message, try_handle_private_message
from app.telegram.update_parser import parse_start_token, parse_telegram_callback, parse_telegram_message
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

POLICY_VERSION = "2026-10-11"

# Заявки с рекламы (16.10.2026): форма Meta → кнопка «Открыть Telegram» → /start ig_<метка>.
_AD_TAG_RE = re.compile(r"^ig_[A-Za-z0-9_-]{1,60}$")
AD_WEBINARS = {"ig_web24": "24 октября", "ig_web31": "31 октября"}
LEADS_SHEET = "«ALMIRA · Заявки»"
OPEN_ACADEMY_BUTTON = "🎓 Открыть Академию"


def welcome_text(showcase: Showcase) -> str:
    return (
        f"Здравствуйте! Это {showcase.academy_title}.\n\n"
        "Здесь вход на сайт Академии, ключи к курсам и напоминания: "
        "проверенная домашка, новый урок."
    )


def policy_text(showcase: Showcase) -> str | None:
    url = showcase.links.get("privacy_url")
    if not url:
        return None
    return f"Пользуясь ботом и Академией, вы соглашаетесь с политикой обработки персональных данных: {url}"


async def _send(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token, reply_markup=reply_markup
    )


async def _first_policy_notice(tenant_id: str, showcase: Showcase, user_id: int, chat_id: int) -> str | None:
    text = policy_text(showcase)
    if not text:
        return None
    try:
        first = await record_telegram_consent(
            tenant_id,
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            policy_version=f"showcase:{showcase.showcase_id}:{POLICY_VERSION}",
        )
    except Exception:
        logger.warning("showcase_consent_record_failed", exc_info=True)
        return None
    return text if first else None


def ad_lead_text(showcase: Showcase, tag: str) -> str:
    """Приветствие человеку с рекламной формы: вебинар с датой или общая заявка."""
    hello = f"Здравствуйте! Рады видеть вас в арт-студии {showcase.brand_name}."
    date = AD_WEBINARS.get(tag)
    if date:
        return (
            f"{hello}\n\nСпасибо, что выбрали нас и записались на бесплатный вебинар Александры Пармон "
            f"по композиции — {date}.\n\nСсылку на эфир пришлём сюда, в этот чат. "
            "Если появится вопрос — нажмите «Написать администратору»."
        )
    return (
        f"{hello}\n\nСпасибо, что выбрали нас и оставили заявку. Мы скоро свяжемся с вами здесь, в этом чате. "
        "Если появится вопрос — нажмите «Написать администратору»."
    )


def _who(msg: Any) -> str:
    user = ((msg.raw or {}).get("message") or {}).get("from") or {}
    name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip() or "без имени"
    username = str(user.get("username") or "").strip()
    return f"{name} (@{username})" if username else f"{name} (без ника)"


async def handle_ad_lead(tenant: TenantContext, msg: Any, showcase: Showcase, tag: str, *, trace_id: str) -> dict[str, Any]:
    """/start ig_<метка>: приветствие заявке с рекламы и кнопки обращений; администратору витрины —
    короткое сообщение (телефон и ответы формы — в таблице заявок). Академию здесь не показываем."""
    tenant_id = tenant.tenant_id
    lines = [ad_lead_text(showcase, tag)]
    notice = await _first_policy_notice(tenant_id, showcase, int(msg.user_id), int(msg.chat_id))
    if notice:
        lines += ["", notice]
    await _send(msg.chat_id, "\n".join(lines), {"inline_keyboard": support_buttons()})
    admin = None
    try:
        admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)
    except Exception:
        logger.warning("showcase_ad_lead_admin_lookup_failed", extra={"trace_id": trace_id}, exc_info=True)
    notified = False
    if admin is None:
        logger.info("showcase_ad_lead_no_admin", extra={"trace_id": trace_id, "tag": tag})
    else:
        what = f"{tag} — вебинар {AD_WEBINARS[tag]}" if tag in AD_WEBINARS else tag
        try:
            sent = await send_telegram_text(
                chat_id=str(admin),
                text=(
                    f"Заявка с рекламы открыла бота: {_who(msg)}, id {int(msg.user_id)}. Метка: {what}. "
                    f"Телефон и ответы формы — в таблице {LEADS_SHEET}."
                ),
                bot_token=current_bot_binding().bot_token,
            )
            notified = bool(sent.get("ok"))
        except Exception:
            logger.warning("showcase_ad_lead_notify_failed", extra={"trace_id": trace_id}, exc_info=True)
    logger.info("showcase_ad_lead", extra={"trace_id": trace_id, "tag": tag, "admin_notified": notified})
    return {
        "ok": True, "route": "showcase_ad_lead", "showcase": showcase.showcase_id, "tag": tag,
        "admin_notified": notified, "trace_id": trace_id,
    }


async def _ensure_actor(tenant: TenantContext, msg: Any) -> None:
    """Ученик — человек в lead_actors: имя в кабинете автора и в уведомлениях."""
    try:
        await ensure_telegram_actor(
            tenant.tenant_id,
            telegram_user_id=int(msg.user_id),
            telegram_chat_id=int(msg.chat_id),
            raw_update=msg.raw or {},
        )
    except Exception:
        logger.warning("showcase_actor_ensure_failed", exc_info=True)


async def show_welcome(tenant: TenantContext, msg: Any, showcase: Showcase, *, trace_id: str) -> dict[str, Any]:
    tenant_id = tenant.tenant_id
    lines = [welcome_text(showcase)]
    notice = await _first_policy_notice(tenant_id, showcase, int(msg.user_id), int(msg.chat_id))
    if notice:
        lines += ["", notice]
    url = await with_site_login(f"{showcase.base_url}/academy/", tenant_id=tenant_id, telegram_user_id=msg.user_id)
    rows = [[{"text": OPEN_ACADEMY_BUTTON, "url": url}]]
    # Обращения (14.10.2026): «художнику» — автору витрины, «администратору» — её администратору.
    rows.extend(support_buttons())
    await _send(msg.chat_id, "\n".join(lines), {"inline_keyboard": rows})
    result = await show_academy_home(tenant_id, msg.chat_id, msg.user_id, showcase)
    return {"ok": True, "route": "showcase_welcome", "showcase": showcase.showcase_id, **result, "trace_id": trace_id}


async def process_showcase_bot_update(
    tenant: TenantContext, update: dict[str, Any], trace_id: str, showcase: Showcase
) -> dict[str, Any]:
    content = await try_handle_content_access(update, trace_id=trace_id)
    if content is not None:
        if content.get("ok"):
            # Галочку согласия ученик поставил на сайте витрины; вход — та же запись, что /start.
            msg = parse_telegram_message(update)
            if msg is not None:
                await _first_policy_notice(tenant.tenant_id, showcase, int(msg.user_id), int(msg.chat_id))
        return content

    callback = parse_telegram_callback(update)
    if callback:
        # Обращения: «Написать …» в личке, «Закрыть обращение» — в личке адресата или в его теме.
        support = await try_handle_support_callback(tenant, callback, showcase, trace_id=trace_id)
        if support is not None:
            return support
        if callback.chat_type != "private":
            return {"ok": True, "route": "showcase_ignored_group", "trace_id": trace_id}
        academy = await try_handle_academy_callback(tenant, callback, trace_id=trace_id)
        if academy is not None:
            return academy
        await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
        return {"ok": True, "route": "showcase_ignored_callback", "trace_id": trace_id}

    msg = parse_telegram_message(update)
    if msg and msg.chat_type == "supergroup":
        # Группа обращений: «/forum» и ответы в темах; остальные группы бот молча пропускает.
        forum = await try_handle_forum_message(tenant, msg, showcase, trace_id=trace_id)
        return forum if forum is not None else {"ok": True, "route": "showcase_ignored", "trace_id": trace_id}
    if not msg or msg.chat_type != "private":
        return {"ok": True, "route": "showcase_ignored", "trace_id": trace_id}
    await _ensure_actor(tenant, msg)

    start_token = parse_start_token(msg.text or "")
    if start_token and parse_course_start_token(start_token) is not None:
        return await handle_course_start_token(tenant, msg, start_token, trace_id=trace_id)
    if start_token and _AD_TAG_RE.fullmatch(start_token):
        return await handle_ad_lead(tenant, msg, showcase, start_token, trace_id=trace_id)
    # Кнопки «Написать …» и ответы адресата через Reply — раньше команд Академии.
    support = await try_handle_private_message(tenant, msg, showcase, trace_id=trace_id, early=True)
    if support is not None:
        return support
    if msg.text and not start_token:
        academy = await try_handle_academy_text(tenant, msg, trace_id=trace_id)
        if academy is not None:
            return academy
    if not start_token:
        # Открытое обращение: текст, фото, голосовое человека — адресату; плоский ответ адресата — человеку.
        support = await try_handle_private_message(tenant, msg, showcase, trace_id=trace_id, early=False)
        if support is not None:
            return support
    return await show_welcome(tenant, msg, showcase, trace_id=trace_id)
