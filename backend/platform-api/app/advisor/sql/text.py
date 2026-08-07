"""Text normalization and service-intent detection for Core SQL advisor."""

from __future__ import annotations

import re

GREETING_RE = re.compile(
    r"^(привет|здравств|добрый|hello|hi)\b",
    re.I,
)
CAPABILITY_RE = re.compile(
    r"(что ты умеешь|что умеешь|что можешь|что ты можешь|чем можешь помочь|"
    r"какие у тебя возможности|помощь|help|capabilities)",
    re.I,
)
PROMOTION_RE = re.compile(r"(акци|скидк|подар|выгод|promo)", re.I)
EVENT_RE = re.compile(r"(мероприят|событ|встреч|семинар|тренинг|конферен)", re.I)
COMMUNITY_RE = re.compile(r"(чат|сообществ|групп|канал|telegram)", re.I)
BASKET_RE = re.compile(r"(корзин|стартов|набор|подбор|бюджет.*pv|pv.*бюджет)", re.I)
DETAILS_RE = re.compile(r"(подробн|детал|состав|противопоказ|как принимать|как использовать)", re.I)
FOLLOWUP_RE = re.compile(
    r"^(?:дай\s+)?(?:а\s+)?(цена|сколько|фото|видео|подробнее|ещё|еще|материалы?)\??$",
    re.I,
)

PRODUCT_NOISE_RE = re.compile(
    r"^(?:цена|стоимость|сколько стоит|фото|видео|покажи|дай|расскажи|про|о|"
    r"что такое|что это|что значит)\s+",
    re.I,
)


def product_query_text(question: str) -> str:
    text = normalize_text(question)
    for _ in range(3):
        cleaned = PRODUCT_NOISE_RE.sub("", text).strip()
        if cleaned == text:
            break
        text = cleaned
    return text


PRO_MARKER_RE = re.compile(r"(?:^|\s)pro(?:\s|$)", re.I)


def has_pro_marker(value: str) -> bool:
    return bool(PRO_MARKER_RE.search(str(value or "")))
SMALLTALK_STATUS_RE = re.compile(r"^(как дела|как ты|как жизнь)\??$", re.I)
PV_DEFINITION_RE = re.compile(
    r"что\s+(?:такое|это|значит)\s+(?:pv|балл|баллы|баллов?)\b",
    re.I,
)
BUSINESS_DEFINITION_RE = re.compile(
    r"что\s+(?:такое|это|значит)\s+(?:повторк|бинарн|кэшбэк)",
    re.I,
)
PRICE_RE = re.compile(
    r"(цена|стоим|сколько стоит|price|\bpv\b|балл|партнер|повторк|повторн\w*\s+покуп|рознич|первичк|основн\w*\s+цен)",
    re.I,
)
PARTNER_PRICE_RE = re.compile(r"(партнер|повторк|повторн\w*\s+покуп)", re.I)
RETAIL_PRICE_RE = re.compile(r"(рознич|первичк|основн\w*\s+цен)", re.I)
PHOTO_RE = re.compile(r"(фото|фотк|фотограф|картин|изображен)", re.I)
VIDEO_RE = re.compile(r"(видео|ютуб|youtube|обзор)", re.I)
CERT_RE = re.compile(r"(сертифик|декларац|сгр|патент|халяль)", re.I)
COMPARE_RE = re.compile(r"(сравни|сравнение|чем отличается|или лучше)", re.I)


def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def detect_service_intent(question: str) -> str | None:
    normalized = normalize_text(question)
    if not normalized:
        return None
    if GREETING_RE.search(normalized):
        return "greeting"
    if SMALLTALK_STATUS_RE.match(normalized):
        return "smalltalk_status"
    if CAPABILITY_RE.search(normalized):
        return "capabilities"
    if normalized in {"помощь", "помоги", "меню", "команды", "help"}:
        return "help"
    return None


def has_price_intent(question: str) -> bool:
    if PV_DEFINITION_RE.search(question) or BUSINESS_DEFINITION_RE.search(question):
        return False
    return bool(PRICE_RE.search(question))


def wants_partner_price(question: str) -> bool:
    return bool(PARTNER_PRICE_RE.search(question))


def wants_retail_price(question: str) -> bool:
    return bool(RETAIL_PRICE_RE.search(question))


def is_pv_definition_question(question: str) -> bool:
    return bool(PV_DEFINITION_RE.search(question))


def has_compare_intent(question: str) -> bool:
    return bool(COMPARE_RE.search(question))


def has_media_intent(question: str) -> bool:
    return bool(PHOTO_RE.search(question) or VIDEO_RE.search(question) or CERT_RE.search(question))


def is_materials_request(question: str) -> bool:
    return bool(re.search(r"\bматериал", normalize_text(question)))


def has_promotion_intent(question: str) -> bool:
    return bool(PROMOTION_RE.search(question))


def has_event_intent(question: str) -> bool:
    return bool(EVENT_RE.search(question))


def has_community_intent(question: str) -> bool:
    return bool(COMMUNITY_RE.search(question))


def has_basket_intent(question: str) -> bool:
    return bool(BASKET_RE.search(question))


def is_context_followup(question: str) -> bool:
    return bool(FOLLOWUP_RE.match(normalize_text(question)) or DETAILS_RE.search(question))
