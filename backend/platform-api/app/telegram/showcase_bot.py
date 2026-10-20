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
from datetime import datetime, timezone
from typing import Any

from app.academy.ad_leads import BOT_TAG, MINSK, Lead, alert_pending, fetch_leads, match_lead, opened_text, record_open, sheet_config
from app.academy.keys import parse_course_start_token
from app.academy.notify import course_page_url
from app.academy.service import preview_admin_ids
from app.academy.webinar_gift import GIFT_BUTTON, gift_closed_text, gift_config, gift_text, gift_webinar, grant_gift, is_code_word
from app.academy.webinars import move_markup, move_webinar, moved_text, not_in_bot_texts, webinars_config
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
# Метки вебинаров. Дату в приветствии не пишем (Виктор, 18.10.2026): кнопка «Спасибо» формы у всех
# ведёт на ig_web24, а дату человек выбрал в форме — её подтверждает отдельное сообщение
# (app.academy.webinars), с кнопкой «Перенести на …».
AD_WEBINARS = frozenset({"ig_web24", "ig_web31"})
# Личная ссылка ig_<вебинар>_<id заявки> (19.10.2026): Кирилл шлёт её тем, кто бота не открыл, и бот сразу знает строку таблицы.
_LEAD_ID_RE = re.compile(r"[0-9]{6,25}")
OPEN_ACADEMY_BUTTON = "🎓 Открыть Академию"


def start_alias(showcase: Showcase, token: str | None) -> str | None:
    """Короткая ссылка администратора (20.10.2026: Кирилл — ?start=kvk) ведёт как ссылка с рекламы:
    ``support.start_aliases = {\"kvk\": \"ig_web24\"}`` — приветствие к эфиру, строка таблицы, подтверждение даты."""
    raw = (showcase.support or {}).get("start_aliases")
    if not token or not isinstance(raw, dict):
        return None
    target = raw.get(token) or raw.get(token.casefold())
    return str(target) if target and _AD_TAG_RE.fullmatch(str(target)) else None


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


def split_ad_tag(token: str) -> tuple[str, str | None]:
    """``ig_web24_1817700742504331`` → (``ig_web24``, id заявки); любая другая метка — как есть, без id."""
    base, _, tail = token.rpartition("_")
    if base in AD_WEBINARS and _LEAD_ID_RE.fullmatch(tail):
        return base, tail
    return token, None


def ad_lead_text(showcase: Showcase, tag: str) -> str:
    """Приветствие человеку с рекламной формы: вебинар с датой или общая заявка."""
    hello = f"Здравствуйте! Рады видеть вас в арт-студии {showcase.brand_name}."
    if tag in AD_WEBINARS:
        return (
            f"{hello}\n\nСпасибо, что выбрали нас и записались на бесплатный вебинар Александры Пармон "
            "по композиции.\n\nСсылку на эфир пришлём сюда, в этот чат. "
            "Если появится вопрос — нажмите «Написать администратору»."
        )
    return (
        f"{hello}\n\nСпасибо, что выбрали нас и оставили заявку. Мы скоро свяжемся с вами здесь, в этом чате. "
        "Если появится вопрос — нажмите «Написать администратору»."
    )


async def _sheet_match(
    msg: Any, showcase: Showcase, lead_id: str | None = None,
) -> tuple[Lead | None, str, str | None]:
    """Строка таблицы заявок этого человека и его имя и ник в Telegram. Пришёл по личной ссылке —
    строка с этим id (19.10.2026), нет такой — по нику, иначе по имени. Таблица читается один раз."""
    user = ((msg.raw or {}).get("message") or {}).get("from") or {}
    tg_name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    tg_username = str(user.get("username") or "").strip() or None
    lead = None
    config = sheet_config(showcase.support)
    if config:
        try:
            leads = await fetch_leads(config)
            lead = next((item for item in leads if item.lead_id == lead_id), None) if lead_id else None
            if lead is None:
                lead = match_lead(leads, username=tg_username, name=tg_name, opened_at=datetime.now(timezone.utc))
        except Exception as exc:
            logger.warning("showcase_ad_lead_sheet_failed", extra={"error": type(exc).__name__})
    return lead, tg_name, tg_username


def _opened_line(msg: Any, lead: Lead | None, tg_name: str, tg_username: str | None, *, webinar: bool) -> str:
    """«✅ Имя (@ник) — в боте»: имя и ник из строки таблицы, не нашлась — из Telegram.
    Имя — ссылка в чат с человеком."""
    name = (lead.name if lead and lead.name else tg_name) or "Без имени"
    username = tg_username or (lead.username if lead else None)
    user_id = int(msg.user_id) if str(msg.user_id or "").isdigit() else None
    return opened_text(name=name, username=username, user_id=user_id, webinar=webinar, found=lead is not None)


async def handle_ad_lead(tenant: TenantContext, msg: Any, showcase: Showcase, tag: str, *, trace_id: str) -> dict[str, Any]:
    """/start ig_<метка>: приветствие заявке с рекламы и кнопки обращений; администратору витрины —
    короткое сообщение (телефон и ответы формы — в таблице заявок). Академию здесь не показываем.
    ``ig_<вебинар>_<id заявки>`` — личная ссылка: метка вебинара отдельно, строка таблицы по id."""
    tenant_id = tenant.tenant_id
    tag, link_lead_id = split_ad_tag(tag)
    lines = [ad_lead_text(showcase, tag)]
    notice = await _first_policy_notice(tenant_id, showcase, int(msg.user_id), int(msg.chat_id))
    if notice:
        lines += ["", notice]
    await _send(msg.chat_id, "\n".join(lines), {"inline_keyboard": support_buttons()})
    lead, tg_name, tg_username = await _sheet_match(msg, showcase, link_lead_id)
    # Отметка «бот открыт» в алерте по заявке (V37, 17.10.2026).
    try:
        await record_open(
            tenant_id, showcase.showcase_id, telegram_user_id=int(msg.user_id), username=tg_username,
            name=tg_name, tag=tag, lead_id=lead.lead_id if lead else None,
        )
    except Exception:
        logger.warning("showcase_ad_open_record_failed", extra={"trace_id": trace_id}, exc_info=True)
    admin = None
    try:
        admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)
    except Exception:
        logger.warning("showcase_ad_lead_admin_lookup_failed", extra={"trace_id": trace_id}, exc_info=True)
    notified = False
    if admin is None:
        logger.info("showcase_ad_lead_no_admin", extra={"trace_id": trace_id, "tag": tag})
    elif await _alert_follows(tenant_id, showcase, lead, trace_id):
        # Алерт по строке таблицы ещё не ушёл — «✅ Бот открыт» придёт в нём, второе сообщение лишнее.
        logger.info("showcase_ad_lead_flag_in_alert", extra={"trace_id": trace_id, "tag": tag})
    else:
        try:
            sent = await send_telegram_text(
                chat_id=str(admin),
                text=_opened_line(msg, lead, tg_name, tg_username, webinar=tag in AD_WEBINARS),
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


async def _try_webinar_callback(
    tenant: TenantContext, callback: Any, showcase: Showcase, *, trace_id: str,
) -> dict[str, Any] | None:
    """«Перенести на …» под подтверждением даты: выбор в боте важнее пункта формы; администратору —
    одна строка о переносе."""
    data = str(callback.data or "")
    if not data.startswith("wbn:") or callback.chat_type != "private":
        return None
    token = current_bot_binding().bot_token
    moved = None
    try:
        moved = await move_webinar(tenant.tenant_id, showcase, int(callback.user_id), data[4:])
    except Exception:
        logger.warning("showcase_webinar_move_failed", extra={"trace_id": trace_id}, exc_info=True)
    if moved is None:
        await answer_callback_query(
            callback_query_id=callback.callback_query_id, bot_token=token,
            text="Не получилось перенести — нажмите «Написать администратору».", show_alert=True,
        )
        return {"ok": True, "route": "showcase_webinar_move", "moved": False, "trace_id": trace_id}
    webinar, opened = moved
    now = datetime.now(timezone.utc)
    webinars = webinars_config(showcase.support)
    asked = next((w for w in webinars if w.tag == data[4:]), None)
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=token)
    if asked is not None and asked.tag != webinar.tag:
        # Старая кнопка «Перенести на …» на прошедший эфир (25.10 и позже) — записали на ближайший.
        time = webinar.at.astimezone(MINSK).strftime("%H:%M")
        text = f"Эфир {asked.date} прошёл, ближайший — {webinar.date} в {time} по Минску. Записали вас на него."
    else:
        text = moved_text(webinar)
    await _send(callback.chat_id, text, move_markup(webinars, webinar.tag, now))
    # После первого эфира перенос на следующий — это запись на повтор (Виктор: 31.10 — повтор 24.10).
    repeat = any(w.at <= now for w in webinars)
    try:
        admin = (await support_targets(tenant.tenant_id, showcase)).get(KIND_ADMIN)
        if admin is not None:
            name = _plain_name(opened.name) or (f"@{opened.username}" if opened.username else "Без имени")
            await send_telegram_text(
                chat_id=str(admin), bot_token=token,
                text=f'🔁 <b><a href="tg://user?id={int(opened.telegram_user_id)}">{name}</a></b>: '
                + (f"запись на повтор — {webinar.date}" if repeat else f"запись перенесена на {webinar.date}"),
            )
    except Exception:
        logger.warning("showcase_webinar_move_notify_failed", extra={"trace_id": trace_id}, exc_info=True)
    logger.info("showcase_webinar_moved", extra={"trace_id": trace_id, "webinar": webinar.tag})
    return {"ok": True, "route": "showcase_webinar_move", "moved": True, "webinar": webinar.tag, "trace_id": trace_id}


NOT_IN_BOT_COMMANDS = {"не в боте", "/nebot", "кто не в боте"}


async def _try_not_in_bot(tenant: TenantContext, msg: Any, showcase: Showcase, *, trace_id: str) -> dict[str, Any] | None:
    """Администратор витрины пишет боту «не в боте» — кто из заявок на предстоящие вебинары бота не открыл."""
    if str(msg.text or "").strip().casefold().rstrip("?") not in NOT_IN_BOT_COMMANDS:
        return None
    admin = (await support_targets(tenant.tenant_id, showcase)).get(KIND_ADMIN)
    if admin is None or int(admin) != int(msg.user_id):
        return None
    try:
        texts = await not_in_bot_texts(tenant.tenant_id, showcase)
    except Exception:
        logger.warning("showcase_not_in_bot_failed", extra={"trace_id": trace_id}, exc_info=True)
        texts = ["Не получилось прочитать таблицу заявок — попробуйте через минуту."]
    for text in texts:
        await _send(msg.chat_id, text)
    return {"ok": True, "route": "showcase_not_in_bot", "messages": len(texts), "trace_id": trace_id}


async def _try_code_word(tenant: TenantContext, msg: Any, showcase: Showcase, *, trace_id: str) -> dict[str, Any] | None:
    """Кодовое слово с эфира (20.10.2026) — подарочный курс на 7 дней (app.academy.webinar_gift)."""
    gift = gift_config(showcase.support)
    if gift is None or not msg.text or not is_code_word(msg.text, gift):
        return None
    now = datetime.now(timezone.utc)
    webinars = webinars_config(showcase.support)
    webinar = gift_webinar(webinars, now)
    if webinar is None and int(msg.user_id) in preview_admin_ids() and webinars:
        webinar = min(webinars, key=lambda w: w.at)  # проверка владельцем — в любой день
    if webinar is None:
        await _send(msg.chat_id, gift_closed_text(webinars, now))
        return {"ok": True, "route": "showcase_gift_closed", "trace_id": trace_id}
    try:
        granted = await grant_gift(tenant.tenant_id, gift, int(msg.user_id), webinar.tag, now)
    except Exception:
        logger.warning("showcase_gift_failed", extra={"trace_id": trace_id}, exc_info=True)
        granted = None
    if granted is None:
        logger.warning("showcase_gift_not_granted", extra={"trace_id": trace_id, "course": gift.course})
        await _send(msg.chat_id, "Не получилось открыть урок — попробуйте через минуту.")
        return {"ok": False, "route": "showcase_gift_failed", "trace_id": trace_id}
    url = await with_site_login(
        course_page_url(gift.course, showcase.base_url), tenant_id=tenant.tenant_id, telegram_user_id=msg.user_id,
    )
    await _send(msg.chat_id, gift_text(granted), {"inline_keyboard": [[{"text": GIFT_BUTTON, "url": url}]]})
    return {"ok": True, "route": "showcase_gift", "webinar": webinar.tag, "already": granted.already, "trace_id": trace_id}


async def _note_bot_open(tenant_id: str, showcase: Showcase, msg: Any) -> None:
    """Вход в бота не по ссылке с рекламы — «в боте» для таблицы, списков и напоминаний. Строку заявки
    находит и дату подтверждает шаг воркера (app.academy.webinars) — без задержки входа на сайт."""
    if not str(msg.user_id or "").isdigit():
        return
    user = ((msg.raw or {}).get("message") or {}).get("from") or {}
    name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    try:
        await record_open(
            tenant_id, showcase.showcase_id, telegram_user_id=int(msg.user_id),
            username=str(user.get("username") or "").strip() or None, name=name, tag=BOT_TAG, lead_id=None,
            overwrite=False,
        )
    except Exception:
        logger.warning("showcase_bot_open_record_failed", exc_info=True)


def _plain_name(value: str) -> str:
    return re.sub(r"[<>]", "", str(value or "")).strip()


async def _alert_follows(tenant_id: str, showcase: Showcase, lead: Lead | None, trace_id: str) -> bool:
    try:
        return await alert_pending(tenant_id, showcase, lead)
    except Exception:
        logger.warning("showcase_ad_lead_alert_check_failed", extra={"trace_id": trace_id}, exc_info=True)
        return False


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
                await _note_bot_open(tenant.tenant_id, showcase, msg)
        return content

    callback = parse_telegram_callback(update)
    if callback:
        moved = await _try_webinar_callback(tenant, callback, showcase, trace_id=trace_id)
        if moved is not None:
            return moved
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
    start_token = start_alias(showcase, start_token) or start_token
    if str(msg.text or "").startswith("/start") and not (start_token and _AD_TAG_RE.fullmatch(start_token)):
        await _note_bot_open(tenant.tenant_id, showcase, msg)
    if start_token and parse_course_start_token(start_token) is not None:
        return await handle_course_start_token(tenant, msg, start_token, trace_id=trace_id)
    if start_token and _AD_TAG_RE.fullmatch(start_token):
        return await handle_ad_lead(tenant, msg, showcase, start_token, trace_id=trace_id)
    listed = await _try_not_in_bot(tenant, msg, showcase, trace_id=trace_id)
    if listed is not None:
        return listed
    # Кодовое слово с эфира — раньше обращений: слово, написанное при открытом «Написать художнику», — тоже подарок.
    if not start_token:
        gifted = await _try_code_word(tenant, msg, showcase, trace_id=trace_id)
        if gifted is not None:
            return gifted
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
