"""Tenant-scoped advisor wording. WHIEDA brand stays on the WHIEDA tenant only."""

from __future__ import annotations

from app.tenancy import TenantContext

HOME_TENANT_ID = "whieda"


def _tenant_profile(tenant_id: str | None):
    """A tenant's own wording, registered in `app.tenants` (owned by the tenant
    team). The package may be absent on a build that has no tenants yet — then
    every tenant keeps the neutral voice below."""
    try:
        from app.tenants import get_tenant_profile
    except ImportError:
        return None
    return get_tenant_profile(tenant_id)


def is_home_tenant(tenant_id: str) -> bool:
    return tenant_id == HOME_TENANT_ID


def advisor_signature(tenant: TenantContext) -> str:
    name = str(tenant.display_name or "").strip()
    if is_home_tenant(tenant.tenant_id):
        return "советник WHIEDA"
    if name:
        return f"советник {name}"
    return "советник"


def maybe_prefix_home_brand(tenant: TenantContext, answer: str) -> str:
    text = str(answer or "").strip()
    if is_home_tenant(tenant.tenant_id) and "whieda" not in text.casefold():
        return f"WHIEDA\n\n{text}"
    return text


def catalog_browse_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "В каталоге WHIEDA есть приборы, товары для дома, уход и нутрицевтические продукты. "
            "В Telegram нажмите «📦 Товары», чтобы открыть список с кнопками. "
            "Или напишите, что интересует: приборы, уход, сон, эликсиры или конкретный товар."
        )
    return (
        "Каталог этого проекта открывается по кнопке «📦 Товары». "
        "Можно также написать название товара или артикул."
    )


def company_intro_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "WHIEDA — компания с продуктами для здоровья и партнёрской программой. "
            "Могу рассказать про товары, старт и маркетинг-план."
        )
    name = str(tenant.display_name or "").strip() or "этого проекта"
    return (
        f"{name} — отдельный каталог и программа. "
        "Могу рассказать про товары, старт и маркетинг-план, если они есть в базе этого проекта."
    )


def income_question_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "WHIEDA не обещает фиксированный доход — результат зависит от личных продаж и работы с клиентами. "
            "Могу рассказать про маркетинг-план, стартовые варианты и виды входа."
        )
    return (
        "Фиксированный доход не обещается — результат зависит от личных продаж и работы с клиентами. "
        "Могу рассказать про маркетинг-план и стартовые варианты, если они есть в базе этого проекта."
    )


def product_selection_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "Помогу подобрать товар или набор WHIEDA под вашу задачу. Выберите направление:\n\n"
            "• прибор для дома или кабинета;\n"
            "• сон и восстановление;\n"
            "• уход и подарок;\n"
            "• энергия и повседневная поддержка;\n"
            "• старт или бюджет.\n\n"
            "Напишите, для кого и какая цель важнее — предложу 2–3 подходящих варианта."
        )
    return (
        "Помогу подобрать товар или набор под вашу задачу. Выберите направление:\n\n"
        "• прибор для дома или кабинета;\n"
        "• сон и восстановление;\n"
        "• уход и подарок;\n"
        "• энергия и повседневная поддержка;\n"
        "• старт или бюджет.\n\n"
        "Напишите, для кого и какая цель важнее."
    )


def discomfort_boundary_text(tenant: TenantContext) -> str:
    profile = _tenant_profile(tenant.tenant_id)
    if profile and profile.medical_boundary:
        return profile.medical_boundary
    if is_home_tenant(tenant.tenant_id):
        return (
            "Помогу подобрать WHIEDA под вашу задачу. Выберите направление: "
            "домашний прибор, сон и восстановление, уход, энергия или старт. "
            "Напишите, для кого и какая цель важнее — предложу подходящие варианты."
        )
    return (
        "Помогу подобрать товар под вашу задачу. Выберите направление: "
        "домашний прибор, сон и восстановление, уход, энергия или старт."
    )


def pv_definition_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "PV (баллы) — это единица личного объёма в WHIEDA: за покупку товара начисляются баллы, "
            "они учитываются в бонусной программе и повторных заказах."
        )
    return (
        "PV (баллы) — единица личного объёма: за покупку товара начисляются баллы, "
        "они учитываются в бонусной программе и повторных заказах."
    )


def mlm_objection_text(tenant: TenantContext) -> str:
    if is_home_tenant(tenant.tenant_id):
        return (
            "WHIEDA — это компания с продуктами и партнёрской программой. "
            "Доход партнёра зависит от личных продаж и работы с клиентами, а не только от приглашений."
        )
    name = str(tenant.display_name or "").strip() or "Этот проект"
    return (
        f"{name} — каталог продуктов и партнёрская программа. "
        "Доход партнёра зависит от личных продаж и работы с клиентами, а не только от приглашений."
    )


def empty_catalog_text(tenant: TenantContext) -> str:
    signature = advisor_signature(tenant)
    return (
        f"Каталог пока пуст. Я {signature}: могу открыть меню или принять вопрос, "
        "но не подставляю товары другого проекта."
    )


def service_fallback(tenant: TenantContext, intent_id: str) -> str:
    signature = advisor_signature(tenant)
    if is_home_tenant(tenant.tenant_id):
        from app.advisor.sql.engine import SERVICE_FALLBACKS

        return SERVICE_FALLBACKS.get(intent_id, SERVICE_FALLBACKS["help"])
    profile = _tenant_profile(tenant.tenant_id)
    if profile:
        own = profile.service_text(intent_id) or (profile.help_text if intent_id not in {"greeting", "capabilities", "smalltalk_status"} else None)
        if own:
            return own
    greeting = (
        f"Здравствуйте! Я {signature}.\n\n"
        "📦 Товары\n"
        "• карточка, цена, фото, видео, сертификат\n"
        "• сравнение и подбор под задачу\n\n"
        "📈 Бизнес\n"
        "• PV, повторка, старт, маркетинг-план\n"
        "• акции, встречи и материалы\n\n"
        "Напишите название товара или вопрос своими словами."
    )
    capabilities = (
        f"Я могу помочь как {signature}:\n\n"
        "📦 Товары — карточка, фото, видео, сравнение\n"
        "💳 Цены и подбор — цена, PV, корзина\n"
        "📈 Бизнес — PV, повторка, старт\n"
    )
    help_text = (
        "Выберите, с чем помочь:\n\n"
        "📦 Товар: название, цена, фото, видео, сравнение\n"
        "🧮 Калькулятор: список товаров\n"
        "📈 Бизнес: PV, повторка, старт, маркетинг-план\n"
        "🏢 Компания: кто мы, продукты, события\n"
        "🧭 Подбор: опишите задачу"
    )
    mapping = {
        "greeting": greeting,
        "smalltalk_status": "Спасибо, я на связи. Задайте вопрос по товару или бизнесу.",
        "capabilities": capabilities,
        "help": help_text,
    }
    return mapping.get(intent_id, help_text)


_NEUTRAL_MENU = (
    "Я лучше всего помогаю с товарами, ценами и PV, применением, "
    "подбором и бизнесом. Выберите направление — названия товаров знать не обязательно.\n\n"
    "📦 Товары — каталог, карточка, фото, видео и сравнение\n"
    "🧮 Калькулятор — несколько товаров, цена и PV\n"
    "🧭 Подбор — опишите задачу, помогу выбрать направление\n"
    "📈 Бизнес — старт, повторка, PV и маркетинг-план\n"
    "🏢 Компания — продукты, события и встречи"
)


def gap_text_for(tenant_id: str, kind: str) -> str:
    from app.advisor.gap import GAP_TEXTS

    if is_home_tenant(tenant_id):
        return GAP_TEXTS[kind]
    profile = _tenant_profile(tenant_id)
    if profile:
        own = profile.gap_text(kind)
        if own:
            return own
    if kind == "missing_resource":
        return (
            "Для этого товара такой материал пока не прикреплён. "
            "Могу показать карточку, цену или другое доступное фото/видео."
        )
    if kind == "ambiguous_product":
        return "Нужно уточнить, о каком товаре речь — тогда смогу дать цену, карточку или фото."
    if kind == "medical_or_safety_boundary":
        return (
            "Любой прибор или продукт не заменяет схему лечения диагноза. "
            "Уточните задачу — подскажу по применению и ограничениям из карточки товара."
        )
    return _NEUTRAL_MENU
