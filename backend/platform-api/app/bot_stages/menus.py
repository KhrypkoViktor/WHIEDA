"""Три меню бота WWC как данные: текст и ряды кнопок (04.11.2026).

Telegram и Max строят из этих рядов свою клавиатуру (``rows_to_telegram`` / ``rows_to_max`` из ``app/funnel/guest.py``):
вторая часть кнопки — ``callback_data`` или адрес ``https://…`` (кнопка-ссылка). Меню гостя — ``guest_menu_reply``
(``app/funnel/guest.py``, он же собирает встречи); здесь — меню партнёра WHIEDA, его экран «Что дальше», меню партнёра
WWC в Max, экран «Ещё» и списки команд синей кнопки «Меню» Telegram по статусу.

Кабинет партнёра WWC в Telegram (сайт, продление, Академия, CRM, бонусы) собирает ``app/telegram/referral_bonus.py``:
ему нужны асинхронные данные (сайт, баланс, входные ссылки) и кнопка копирования, которой нет в Max.

Все тексты — ``app/funnel/texts.py`` (раздел «три типа людей», заглушки: утверждает владелец).
"""

from __future__ import annotations

from app.cart.web_links import CALCULATOR_WEB_URL
from app.funnel import texts
from app.funnel.guest import Reply, Rows
from app.settings import get_settings
from app.telegram.academy import ACADEMY_HOME_CALLBACK
from app.telegram.navigation import telegram_menu_commands
from app.telegram.support import SERVICES_CARD_CALLBACK, SUPPORT_SITE_CALLBACK

TELEGRAM = "telegram"
MAX = "max"

# Адреса сайта для Max: там нет ни заказа сайта, ни Академии в самом боте — человека ведут на сайт.
SITE_ORDER_URL = "https://wwc.best/start/"
CABINET_URL = "https://wwc.best/me/"

# Кнопки партнёра WHIEDA в Telegram ведут в уже готовые сценарии бота: анкета заказа сайта, Академия, поддержка.
_SITE_ORDER_CALLBACK = "site:create"

# Синяя кнопка «Меню» Telegram по статусу (setMyCommands со scope=chat). Гость — два пункта; партнёр WHIEDA — четыре;
# партнёр WWC — шесть пунктов профиля minimal (кабинет, пригласить, калькулятор, вход через Max, поддержка, пожелание).
GUEST_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "С чего начать"),
    ("support", "Поддержка"),
)
PARTNER_WHIEDA_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "С чего начать"),
    ("site", "Заказать сайт"),
    ("course", "Курс"),
    ("support", "Поддержка"),
)


def commands_for_stage(stage: str, *, include_wish: bool = True, include_max: bool = True) -> list[dict[str, str]]:
    """Список команд синей кнопки «Меню» для статуса. Неизвестный статус — как у гостя (он самый узкий)."""
    if stage == "partner_wwc":
        return telegram_menu_commands(minimal=True, include_wish=include_wish, include_max=include_max)
    source = PARTNER_WHIEDA_COMMANDS if stage == "partner_whieda" else GUEST_COMMANDS
    return [{"command": command, "description": description} for command, description in source]


def academy_url() -> str:
    return get_settings().platform_academy_site_base.rstrip("/") + "/academy/"


def _links(channel: str) -> tuple[str, str, str]:
    """Куда ведут «Заказать сайт», «Курс» и «Поддержка»: в Telegram — в готовые сценарии бота, в Max — на сайт
    и «Задать вопрос» (обращений в поддержку в Max нет, он отправит в Telegram-бота)."""
    if channel == MAX:
        return SITE_ORDER_URL, academy_url(), texts.FUNNEL_ASK_CALLBACK
    return _SITE_ORDER_CALLBACK, ACADEMY_HOME_CALLBACK, SUPPORT_SITE_CALLBACK


def partner_whieda_reply(*, channel: str, example_url: str | None) -> Reply:
    """Меню партнёра WHIEDA: «Что дальше», пример сайта партнёра (если есть адрес), заказ сайта, курс, личный разбор,
    живая встреча, поддержка. Без баланса, реферальной ссылки, Gemini и калькулятора."""
    order, course, support = _links(channel)
    rows: Rows = [[(texts.STAGE_PW_NEXT_BUTTON, texts.STAGE_NEXT_CALLBACK)]]
    if example_url:
        rows.append([(texts.STAGE_PW_EXAMPLE_BUTTON, example_url)])
    rows.extend(
        [
            [(texts.STAGE_PW_ORDER_BUTTON, order)],
            [(texts.STAGE_PW_COURSE_BUTTON, course)],
            [(texts.STAGE_CONSULT_BUTTON, texts.FUNNEL_CONSULT_CALLBACK), (texts.STAGE_MEET_BUTTON, texts.FUNNEL_MEET_CALLBACK)],
            [(texts.STAGE_PW_SUPPORT_BUTTON, support)],
        ]
    )
    return Reply(texts.STAGE_PW_INTRO, rows)


def partner_whieda_next_reply(*, channel: str, example_url: str | None) -> Reply:
    """«Что дальше»: три шага текстом и под каждым шагом его кнопка, внизу — возврат в меню."""
    order, course, _ = _links(channel)
    rows: Rows = []
    if example_url:
        rows.append([(texts.STAGE_PW_EXAMPLE_BUTTON, example_url)])
    rows.extend(
        [
            [(texts.STAGE_PW_ORDER_BUTTON, order)],
            [(texts.STAGE_PW_COURSE_BUTTON, course)],
            [(texts.STAGE_BACK_BUTTON, texts.STAGE_MENU_CALLBACK)],
        ]
    )
    return Reply(texts.STAGE_PW_NEXT_TEXT, rows)


def partner_wwc_max_reply(*, site_url: str | None, telegram_bot_url: str) -> Reply:
    """Меню партнёра WWC в Max: кабинета бота здесь нет (он в Telegram) — сайт, кабинет на сайте, Telegram-бот и вопрос."""
    rows: Rows = []
    if site_url:
        rows.append([(texts.STAGE_CABINET_SITE_BUTTON, site_url)])
    rows.extend(
        [
            [(texts.STAGE_MAX_CABINET_BUTTON, CABINET_URL)],
            [(texts.STAGE_MAX_TELEGRAM_BUTTON, telegram_bot_url)],
            [(texts.STAGE_PW_SUPPORT_BUTTON, texts.FUNNEL_ASK_CALLBACK)],
        ]
    )
    return Reply(texts.STAGE_MAX_PWWC_TEXT, rows)


def more_reply() -> Reply:
    """Экран «Ещё» партнёра WWC: сервисы (подключение Gemini Pro) и калькулятор; внизу — возврат в кабинет."""
    rows: Rows = [
        [(texts.STAGE_SERVICES_BUTTON, SERVICES_CARD_CALLBACK)],
        [(texts.STAGE_CALCULATOR_BUTTON, CALCULATOR_WEB_URL)],
        [(texts.STAGE_BACK_BUTTON, texts.STAGE_MENU_CALLBACK)],
    ]
    return Reply(texts.STAGE_MORE_TEXT, rows)


def guest_free_text_reply() -> Reply:
    """Свободный текст гостя: советчика по товарам у него нет — кнопка «Задать вопрос» передаёт вопрос Виктору."""
    return Reply(texts.STAGE_GUEST_FREE_TEXT, [[(texts.STAGE_ASK_BUTTON, texts.FUNNEL_ASK_CALLBACK)]])
