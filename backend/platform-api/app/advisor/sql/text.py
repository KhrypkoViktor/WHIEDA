"""Text normalization and service-intent detection for Core SQL advisor."""

from __future__ import annotations

import re

from app.telegram.navigation import is_catalog_list_request

GREETING_RE = re.compile(
    r"^(привет|приве|здравств|здарова|здорово|здров|здрась(?:те|te)|хай|ха[йе]|добрый|hello|hi)\b",
    re.I,
)
CAPABILITY_RE = re.compile(
    r"(что ты умеешь|что умеешь|что можешь|что ты можешь|че\s+ты\s+може(?:шь|ь)|"
    r"чо\s+умеешь|чо\s+може(?:шь|ь)|что\s+мо(?:жешь|ешь|еь)|а что мо(?:жешь|ешь|еь)|може(?:шь|ь)\??|"
    r"чем можешь помочь|какие у тебя возможности|помощь|help|capabilities|"
    r"какие есть товары|какие товары|любой товар|какой есть товар|какой товар есть|что есть из товаров)",
    re.I,
)
CALCULATOR_RE = re.compile(r"^калькулятор\.?$", re.I)
START_OPTIONS_RE = re.compile(
    r"(какие\s+виды\s+вход|вариант\w*\s+вход|виды\s+вход|как\s+начать\s+работ|стартов\w*\s+вариант)",
    re.I,
)
COMPANY_INTRO_RE = re.compile(
    r"(расскаж\w*\s+о\s+компан|что\s+за\s+компан|кто\s+вы\b|что\s+вы\b|"
    r"кто\s+так(?:ая|ое)\s+whieda|о\s+компани)",
    re.I,
)
MARKETING_PLAN_RE = re.compile(r"маркетинг[ -]?план", re.I)
STEP_TOPIC_RE = re.compile(r"\b(?:step|степ)(?:\s+бонус)?\b", re.I)
INCOME_QUESTION_RE = re.compile(
    r"(как\s+заработать|сколько\s+можно\s+заработать|доход\s+партн|заработок\s+партн)",
    re.I,
)
# «чем лечить», «какой бад от …», «вместо/заменить лекарство» — a request for
# treatment, not for a product card (NSP staging golden, 16.09.2026).
DISCOMFORT_BOUNDARY_RE = re.compile(
    r"(бол(?:ит|ят)\s+(?:колен|спин|шея|спина|колени|поясниц)|хочу\s+совет|"
    r"чем\s+(?:по)?лечить|как\s+вылечить|какой\s+бад\s+от|что\s+(?:принимать|пить)\s+от|"
    r"замен(?:ить|яет|а)\s+лекарств|вместо\s+лекарств|вместо\s+таблет)",
    re.I,
)
# These are not product-selection questions.  Keep this deliberately narrow: the
# advisor must not turn an acute human or animal case into a product dialogue.
# Extra stems (2026-08-30): do_not_route for dialysis / onco / pregnancy /
# lactation / the unofficial «Реанимация» dump — files 15, 16, 17, 28.
HIGH_RISK_MEDICAL_BOUNDARY_RE = re.compile(
    r"(гнойн\w*\s+ангин|гемангиом|врожд[её]н\w*|\b\d+\s+месяц\w*|"
    r"гипертони\w*.*(?:скак|пульсир)|пульсир\w*.*(?:давлен|голов)|"
    r"кот\s+умира|почки\s+отказ|сожг\w*\s+внутр|лимфоуз|"
    r"от[её]к.*(?:глаз|щек|виск)|(?:глаз|щек|виск).*от[её]к|"
    r"онколог|химиотерап|\bхимио\b|метастаз|лучев\w*\s+терап|"
    r"\bрак(?:а|ом|у|е)?\b|сарком|"
    r"диализ|гемодиализ|пересадк\w*\s+почк|"
    r"беременн|кормящ|лактац|"
    r"реанимац)",
    re.I,
)
PROMOTION_RE = re.compile(r"(акци|скидк|подар|выгод|promo)", re.I)
EVENT_RE = re.compile(r"(мероприят|событ|встреч|семинар|тренинг|конферен)", re.I)
COMMUNITY_RE = re.compile(r"(чат|сообществ|групп|канал|telegram)", re.I)
OUT_OF_SCOPE_RE = re.compile(
    r"(погод|курс\s+валют|курс\s+доллар|доллар.*курс|курс.*доллар|крипт|бирж|инвестир|"
    r"акци[яи]\s+компан|политик|международн\w*\s+логистик|"
    r"пив(?:о|а|ку|очк|ка|ко)\b|\bbeer\b|выпить\s+пив)",
    re.I,
)
BASKET_RE = re.compile(
    r"(корзин|стартов|набор\s+(?:для\s+старта|старт|корзин)|подбор|подбери|бюджет.*pv|pv.*бюджет)",
    re.I,
)
PRODUCT_SELECTION_RE = re.compile(
    r"(?:^|\b)(?:подбор|подобрать(?:\s+товар)?|подбери(?:\s+товар)?|"
    r"помоги\s+выбрать|не\s+знаю\s+что\s+выбрать|что\s+подарить|подарок|набор|"
    r"для\s+(?:дома|семьи|салона|офиса|поездки)|нужен\s+прибор|"
    r"что\s+(?:для|взять|выбрать|купить)|(?:плохо\s+сплю|мерзну\s+зимой)|"
    r"(?:ноги|глаза)\s+устают|для\s+(?:волос|кожи|пищеварения|энергии)|"
    r"с\s+чего\s+начать|новичок|собери\s+рутину|восстановлен)",
    re.I,
)
DETAILS_RE = re.compile(r"(подробн|детал|состав|противопоказ|как принимать|как использовать)", re.I)
FOLLOWUP_RE = re.compile(
    r"^(?:дай\s+)?(?:а\s+)?(цена|сколько|фото|видео|подробнее|материалы?|карточк\w*)\??$",
    re.I,
)
MENU_REPROMPT_RE = re.compile(
    r"^(ещ[её]|что\s+ещ[её]|дальше|что\s+дальше|ну\s+и\??|ну\??|ок\??|ладно\??|понятно\??)$",
    re.I,
)
PRODUCT_NOISE_RE = re.compile(
    r"^(?:цена|стоимость|сколько стоит|фото|видео|покажи|дай|расскажи|про|о|"
    r"что такое|что это|что значит|подробнее|подробн|pdf)\s+",
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
SMALLTALK_STATUS_RE = re.compile(r"^(как дела|как ты|как жизнь|ты живой)\??$", re.I)
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
PDF_RE = re.compile(r"\bpdf\b", re.I)
COMPARE_RE = re.compile(r"(сравни|сравнение|чем отличается|или лучше|\S+\s+или\s+\S+)", re.I)
COLOR_ELIXIR_SHORTHAND_RE = re.compile(
    r"(?:красн|зел[её]н|син).*эликсир|эликсир.*(?:красн|зел[eё]н|син)",
    re.I,
)
PRODUCT_PRICE_NICKNAME_RE = re.compile(r"^сауны?$", re.I)


_LEADING_EMOJI_RE = re.compile(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\s]+")


def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    # Menu labels arrive with their emoji («📦 товары», «📈 бизнес»); the words decide.
    text = _LEADING_EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_service_intent(question: str) -> str | None:
    normalized = normalize_text(question)
    if not normalized:
        return None
    if is_catalog_list_request(question):
        return None
    if normalized in {"можешь", "можешь?", "а можешь", "а можешь?"}:
        return "capabilities"
    if GREETING_RE.search(normalized):
        return "greeting"
    if SMALLTALK_STATUS_RE.match(normalized):
        return "smalltalk_status"
    if normalized in {"помощь", "помоги", "меню", "команды", "help"}:
        return "help"
    if CAPABILITY_RE.search(normalized):
        return "capabilities"
    return None


def has_implicit_price_intent(question: str) -> bool:
    normalized = normalize_text(question)
    return bool(
        COLOR_ELIXIR_SHORTHAND_RE.search(normalized)
        or PRODUCT_PRICE_NICKNAME_RE.match(normalized)
    )


def has_price_intent(question: str) -> bool:
    if PV_DEFINITION_RE.search(question) or BUSINESS_DEFINITION_RE.search(question):
        return False
    return bool(PRICE_RE.search(question) or has_implicit_price_intent(question))


def wants_partner_price(question: str) -> bool:
    return bool(PARTNER_PRICE_RE.search(question))


def wants_retail_price(question: str) -> bool:
    return bool(RETAIL_PRICE_RE.search(question))


def is_pv_definition_question(question: str) -> bool:
    return bool(PV_DEFINITION_RE.search(question))


def is_product_definition_question(question: str) -> bool:
    normalized = normalize_text(question)
    if not re.search(r"что\s+(?:такое|это|значит|за)\s+\S", normalized):
        return False
    if is_pv_definition_question(question) or BUSINESS_DEFINITION_RE.search(question):
        return False
    entity = product_query_text(question)
    return bool(entity and len(entity) >= 2)


def has_compare_intent(question: str) -> bool:
    return bool(COMPARE_RE.search(question))


def has_media_intent(question: str) -> bool:
    return bool(
        PHOTO_RE.search(question)
        or VIDEO_RE.search(question)
        or CERT_RE.search(question)
        or PDF_RE.search(question)
    )


def media_request_is_product_followup(question: str) -> bool:
    """True when the user asks for media about a session/resolved product, not when
    the media word is part of a product name (e.g. «товар без фото»)."""
    if is_materials_request(question):
        return True
    normalized = normalize_text(question)
    if re.match(
        r"^(?:дай\s+)?(?:а\s+)?(?:фото|видео|сертификат|pdf|материал)\b",
        normalized,
    ):
        return True
    if re.search(r"(?:^|\s)(?:фото|видео|сертификат|pdf|материал)\s+\S", normalized):
        return True
    if is_context_followup(question) and has_media_intent(question):
        return True
    return False


def is_materials_request(question: str) -> bool:
    return bool(re.search(r"\bматериал", normalize_text(question)))


def has_promotion_intent(question: str) -> bool:
    return bool(PROMOTION_RE.search(question))


def has_event_intent(question: str) -> bool:
    return bool(EVENT_RE.search(question))


def has_community_intent(question: str) -> bool:
    return bool(COMMUNITY_RE.search(question))


def is_unsupported_topic(question: str) -> bool:
    """Recognise plainly external topics without guessing that they are products."""
    return bool(OUT_OF_SCOPE_RE.search(question))


def has_basket_intent(question: str) -> bool:
    return bool(BASKET_RE.search(question))


def is_context_followup(question: str) -> bool:
    return bool(FOLLOWUP_RE.match(normalize_text(question)) or DETAILS_RE.search(question))


def is_calculator_request(question: str) -> bool:
    return bool(CALCULATOR_RE.match(normalize_text(question)))


def is_start_options_request(question: str) -> bool:
    return bool(START_OPTIONS_RE.search(question))


def is_company_intro_request(question: str) -> bool:
    return bool(COMPANY_INTRO_RE.search(question))


def is_marketing_plan_request(question: str) -> bool:
    return bool(MARKETING_PLAN_RE.search(question))


def is_step_topic_request(question: str) -> bool:
    return bool(STEP_TOPIC_RE.search(question))


def is_income_question(question: str) -> bool:
    return bool(INCOME_QUESTION_RE.search(question))


def is_discomfort_boundary(question: str) -> bool:
    return bool(DISCOMFORT_BOUNDARY_RE.search(question))


def is_high_risk_medical_boundary(question: str) -> bool:
    """Recognise acute cases that must never enter a product-answer branch."""
    return bool(HIGH_RISK_MEDICAL_BOUNDARY_RE.search(question))


def is_product_selection_request(question: str) -> bool:
    normalized = normalize_text(question)
    # A concrete basket with a budget/PV has its own calculator path.  Do not
    # reduce it to a generic direction menu.
    if "корзин" in normalized and ("pv" in normalized or re.search(r"\d", normalized)):
        return False
    return bool(PRODUCT_SELECTION_RE.search(normalized))


def is_menu_reprompt(question: str) -> bool:
    return bool(MENU_REPROMPT_RE.match(normalize_text(question)))
