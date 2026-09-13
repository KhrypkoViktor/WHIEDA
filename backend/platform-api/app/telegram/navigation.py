"""Telegram navigation labels, callback payloads, and keyboards (single source of truth)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.cart.web_links import CALCULATOR_WEB_URL, calculator_web_url

NEWCOMER_PANEL_TEXT = (
    "Помогу быстро освоиться. Названия товаров знать не обязательно — выберите, что хотите сделать.\n\n"
    "📦 Посмотреть товары\n"
    "🧭 Подобрать под задачу\n"
    "🛒 Первая покупка\n"
    "🧮 Посчитать корзину\n"
    "🎥 Отзывы и материалы\n"
    "📚 Как пользоваться\n"
    "👤 Мой наставник"
)

NEWCOMER_ACTION_LABELS: dict[str, str] = {
    "products": "📦 Посмотреть товары",
    "match": "🧭 Подобрать под задачу",
    "first": "🛒 Первая покупка",
    "calc": "🧮 Посчитать корзину",
    "materials": "🎥 Отзывы и материалы",
    "help": "📚 Как пользоваться",
    "mentor": "👤 Мой наставник",
}

# Unfinished actions stay hidden: no empty button, no invented review feed.
NEWCOMER_ACTIONS_ENABLED: frozenset[str] = frozenset(
    {"products", "match", "first", "calc", "help", "mentor"}
)

NEWCOMER_ADVISOR_QUESTIONS: dict[str, str] = {
    "match": "подбери товар",
    "first": "подбери стартовый набор",
    "calc": "калькулятор",
    "help": "помощь",
    "mentor": "мой наставник",
}

# Known ambiguous names from Core SQL — buttons reuse catalog SKU callbacks.
CLARIFICATION_PRODUCT_CHOICES: dict[str, tuple[tuple[str, str], ...]] = {
    "product_ambiguity_activator": (
        ("Активатор клеток", "M015-00"),
        ("Активатор PRO", "EU-N000031-25"),
    ),
    "product_ambiguity_paste": (
        ("Паста Цинфэн", "F071-00"),
        ("Зубная паста", "EU-N000030-25"),
    ),
    "product_ambiguity_color_красн": (("Эликсир Фохоу", "F001-02"),),
    "product_ambiguity_color_зелен": (("Эликсир Саньцин", "F003-02"),),
    "product_ambiguity_color_син": (("Эликсир 3 Драгоценности", "F002-02"),),
    "product_ambiguity_belt": (("Магнитный пояс", "T003"),),
}

GAP_DIRECTION_ACTIONS: tuple[tuple[str, str], ...] = (
    ("📦 Товары", "nav:products"),
    ("🧭 Подбор", "nc:match"),
)

MAX_CALLBACK_BYTES = 64
MAX_CATALOG_PAGE_SIZE = 8
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _normalize(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip("?!.,:;…")

LABEL_PRODUCTS = "📦 Товары"
LABEL_CALCULATOR = "🧮 Калькулятор"
LABEL_BUSINESS = "📈 Бизнес"
LABEL_COMPANY = "🏢 О компании"
LABEL_BASKET = "🧭 Подбор"
LABEL_EVENTS = "📅 Встречи"
LABEL_OPEN_CALCULATOR = "Открыть калькулятор"

MENU_LABELS: tuple[str, ...] = (
    LABEL_PRODUCTS,
    LABEL_CALCULATOR,
    LABEL_BUSINESS,
    LABEL_COMPANY,
    LABEL_BASKET,
    LABEL_EVENTS,
)

MENU_INTENT_BY_LABEL: dict[str, str] = {
    LABEL_PRODUCTS: "nav_products",
    LABEL_CALCULATOR: "nav_calculator",
    LABEL_BUSINESS: "nav_business",
    LABEL_COMPANY: "nav_company",
    LABEL_BASKET: "nav_basket",
    LABEL_EVENTS: "nav_events",
}

MENU_INTENT_BY_COMMAND: dict[str, str] = {
    "products": "nav_products",
    "calculator": "nav_calculator",
    "business": "nav_business",
    "company": "nav_company",
    "match": "nav_basket",
    "events": "nav_events",
}

TELEGRAM_MENU_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "С чего начать"),
    ("cabinet", "Личный кабинет"),
    ("invite", "Пригласить партнёра"),
    ("products", "Товары"),
    ("match", "Подобрать продукты"),
    ("calculator", "Калькулятор"),
    ("business", "Бизнес"),
    ("events", "Встречи"),
    ("company", "О компании"),
    ("support", "Поддержка"),
)

# Production currently accepts site setup and payment confirmation manually through
# Viktor. Keep the public bot focused on the few actions that are dependable.
MINIMAL_TELEGRAM_MENU_COMMANDS: tuple[tuple[str, str], ...] = (
    ("cabinet", "Личный кабинет"),
    ("invite", "Пригласить партнёра"),
    ("calculator", "Калькулятор"),
    ("support", "Поддержка"),
)

NAVIGATION_INTENT_KEYS: frozenset[str] = frozenset(MENU_INTENT_BY_LABEL.values())

CATALOG_LIST_PHRASES: frozenset[str] = frozenset(
    {
        "товар",
        "товары",
        "какие есть товары",
        "какие товары",
        "какой есть товар",
        "какой товар есть",
        "что есть из товаров",
        "любой товар",
        "покажи любой товар",
        "покажите любой товар",
        "каталог",
        "список товаров",
    }
)

MENU_ADVISOR_QUESTIONS: dict[str, str] = {
    "nav_calculator": "калькулятор",
    "nav_business": "какие виды входа",
    "nav_company": "расскажи о компании",
    "nav_basket": "подбери стартовый набор",
    "nav_events": "мероприятия",
}

ACTION_LABELS: dict[str, str] = {
    "card": "Карточка",
    "price": "Цена/PV",
    "photo": "Фото",
    "video": "Видео",
    "cert": "Сертификат",
    "compare": "Сравнить",
}


@dataclass(frozen=True)
class ParsedCallback:
    kind: str
    page: int | None = None
    sku: str | None = None
    action: str | None = None


def resolve_menu_text_intent(text: str) -> str | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    intent = MENU_INTENT_BY_LABEL.get(stripped)
    if intent:
        return intent
    command = re.fullmatch(r"/([a-z]+)(?:@\w+)?", stripped.lower())
    if command:
        return MENU_INTENT_BY_COMMAND.get(command.group(1))
    normalized = _normalize(stripped)
    if normalized in CATALOG_LIST_PHRASES:
        return "nav_products"
    return None


def is_newcomer_panel_request(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if _normalize(stripped) == "с чего начать":
        return True
    match = re.match(r"^/start(?:@\w+)?(?:\s+(.+))?$", stripped, re.I)
    if not match:
        return False
    return not (match.group(1) or "").strip()


def is_catalog_list_request(text: str) -> bool:
    return resolve_menu_text_intent(text) == "nav_products"


def menu_intent_to_advisor_question(intent: str) -> str | None:
    return MENU_ADVISOR_QUESTIONS.get(intent)


def clamp_page_size(page_size: int) -> int:
    return max(1, min(MAX_CATALOG_PAGE_SIZE, int(page_size)))


def clamp_page(page: int) -> int:
    return max(1, int(page))


def _callback_fits(data: str) -> bool:
    return bool(data) and len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES


def build_nav_products_callback() -> str:
    return "nav:products"


def build_catalog_page_callback(page: int) -> str:
    data = f"cat:p:{clamp_page(page)}"
    if not _callback_fits(data):
        raise ValueError("callback too long")
    return data


def build_catalog_sku_callback(sku: str) -> str:
    safe = validate_sku(sku)
    data = f"cat:s:{safe}"
    if not _callback_fits(data):
        raise ValueError("callback too long")
    return data


def build_product_action_callback(action: str, sku: str) -> str:
    if action not in ACTION_LABELS:
        raise ValueError("unknown action")
    safe = validate_sku(sku)
    data = f"act:{action}:{safe}"
    if not _callback_fits(data):
        raise ValueError("callback too long")
    return data


def build_menu_callback() -> str:
    return "nav:menu"


def build_newcomer_callback(action: str) -> str:
    if action not in NEWCOMER_ACTION_LABELS:
        raise ValueError("unknown newcomer action")
    data = f"nc:{action}"
    if not _callback_fits(data):
        raise ValueError("callback too long")
    return data


def validate_sku(sku: str) -> str:
    value = str(sku or "").strip()
    if not SKU_RE.fullmatch(value):
        raise ValueError("invalid sku")
    return value


def parse_callback_data(data: str) -> ParsedCallback | None:
    raw = str(data or "").strip()
    if not _callback_fits(raw):
        return None
    if raw == "nav:products":
        return ParsedCallback(kind="nav_products")
    if raw == "nav:menu":
        return ParsedCallback(kind="nav_menu")
    if raw.startswith("nc:"):
        action = raw.split(":", 1)[1]
        if action not in NEWCOMER_ACTIONS_ENABLED:
            return None
        return ParsedCallback(kind=f"newcomer_{action}")
    if raw.startswith("cat:p:"):
        try:
            page = int(raw.split(":", 2)[2])
        except (IndexError, ValueError):
            return None
        if page < 1:
            return None
        return ParsedCallback(kind="catalog_page", page=page)
    if raw.startswith("cat:s:"):
        sku_part = raw.split(":", 2)[2] if raw.count(":") >= 2 else ""
        try:
            sku = validate_sku(sku_part)
        except ValueError:
            return None
        return ParsedCallback(kind="catalog_sku", sku=sku)
    if raw.startswith("act:"):
        parts = raw.split(":", 2)
        if len(parts) != 3:
            return None
        action, sku_part = parts[1], parts[2]
        if action not in ACTION_LABELS:
            return None
        try:
            sku = validate_sku(sku_part)
        except ValueError:
            return None
        return ParsedCallback(kind="product_action", action=action, sku=sku)
    return None


def main_menu_reply_keyboard(*, include_calculator: bool = True) -> dict[str, Any]:
    # Remove the old persistent reply keyboard. Navigation now lives in
    # Telegram's standard command-menu button beside the message field.
    _ = include_calculator
    return {"remove_keyboard": True}


def telegram_menu_commands(
    *,
    include_calculator: bool = True,
    minimal: bool = False,
) -> list[dict[str, str]]:
    commands = MINIMAL_TELEGRAM_MENU_COMMANDS if minimal else TELEGRAM_MENU_COMMANDS
    return [
        {"command": command, "description": description}
        for command, description in commands
        if include_calculator or command != "calculator"
    ]


def _inline_button(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def newcomer_inline_keyboard(*, include_calculator: bool = True) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for action in ("products", "match", "first", "calc", "materials", "help", "mentor"):
        if action not in NEWCOMER_ACTIONS_ENABLED:
            continue
        if action == "calc" and not include_calculator:
            continue
        rows.append([_inline_button(NEWCOMER_ACTION_LABELS[action], build_newcomer_callback(action))])
    return {"inline_keyboard": rows}


def calculator_open_inline_keyboard(*, url: str | None = None) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": LABEL_OPEN_CALCULATOR, "url": url or calculator_web_url()}]
        ]
    }


def clarification_choice_inline_keyboard(clarification_keys: list[str] | tuple[str, ...] | None) -> dict[str, Any] | None:
    seen: set[str] = set()
    rows: list[list[dict[str, str]]] = []
    for key in clarification_keys or []:
        choices = CLARIFICATION_PRODUCT_CHOICES.get(str(key) or "")
        if not choices:
            continue
        for label, sku in choices:
            if sku in seen:
                continue
            seen.add(sku)
            rows.append([_inline_button(label, build_catalog_sku_callback(sku))])
            if len(rows) >= 3:
                return {"inline_keyboard": rows}
    if not rows:
        return None
    return {"inline_keyboard": rows}


def fallback_direction_inline_keyboard(*, include_calculator: bool = True) -> dict[str, Any]:
    rows = [[{"text": label, "callback_data": data}] for label, data in GAP_DIRECTION_ACTIONS]
    if include_calculator:
        rows.append([{"text": LABEL_OPEN_CALCULATOR, "url": CALCULATOR_WEB_URL}])
    return {"inline_keyboard": rows}


def advisor_followup_inline_keyboard(
    core_response: dict[str, Any] | None,
    *,
    include_calculator: bool = True,
) -> dict[str, Any] | None:
    payload = core_response or {}
    choice = clarification_choice_inline_keyboard(
        [str(item) for item in (payload.get("clarifications") or [])]
    )
    if choice:
        return choice
    gap = str(payload.get("gap_kind") or "")
    if gap in {"unrouted_message", "unknown_product", "unknown_followup", "unsupported_topic"}:
        return fallback_direction_inline_keyboard(include_calculator=include_calculator)
    return None


def catalog_list_inline_keyboard(
    *,
    page: int,
    total_pages: int,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for product in products:
        sku = str(product.get("sku") or "").strip()
        name = str(product.get("canonical_name") or sku).strip()
        if not sku:
            continue
        label = name if len(name) <= 48 else f"{name[:45]}…"
        rows.append([_inline_button(label, build_catalog_sku_callback(sku))])
    nav_row: list[dict[str, str]] = []
    if page > 1:
        nav_row.append(_inline_button("◀ Назад", build_catalog_page_callback(page - 1)))
    if page < total_pages:
        nav_row.append(_inline_button("Далее ▶", build_catalog_page_callback(page + 1)))
    if nav_row:
        rows.append(nav_row)
    rows.append([_inline_button("В меню", build_menu_callback())])
    return {"inline_keyboard": rows}


def product_actions_inline_keyboard(sku: str) -> dict[str, Any]:
    safe = validate_sku(sku)
    rows = [
        [
            _inline_button(ACTION_LABELS["card"], build_product_action_callback("card", safe)),
            _inline_button(ACTION_LABELS["price"], build_product_action_callback("price", safe)),
        ],
        [
            _inline_button(ACTION_LABELS["photo"], build_product_action_callback("photo", safe)),
            _inline_button(ACTION_LABELS["video"], build_product_action_callback("video", safe)),
        ],
        [
            _inline_button(ACTION_LABELS["cert"], build_product_action_callback("cert", safe)),
            _inline_button(ACTION_LABELS["compare"], build_product_action_callback("compare", safe)),
        ],
        [
            _inline_button("◀ К каталогу", build_nav_products_callback()),
            _inline_button("В меню", build_menu_callback()),
        ],
    ]
    return {"inline_keyboard": rows}


def product_action_question(action: str, canonical_name: str) -> str | None:
    name = str(canonical_name or "").strip()
    if not name:
        return None
    if action == "card":
        return f"карточка {name}"
    if action == "price":
        return f"цена {name}"
    if action == "photo":
        return f"фото {name}"
    if action == "video":
        return f"видео {name}"
    if action == "cert":
        return f"сертификат {name}"
    return None
