"""nsp-maxim: wording for Maxim's NSP bot. Only official-card facts, no treatment claims."""

from __future__ import annotations

from app.tenants import TenantProfile, register_tenant_profile

TENANT_ID = "nsp-maxim"
DISPLAY_NAME = "NSP"
SUPPORT_HINT = "🆘 Поддержка — команда /support"

_MENU = (
    "📦 Товары — карточка, цена в $, как принимать, фото\n"
    "📈 Бизнес — вопросы по компании и маркетинг-плану\n"
    "🏢 Компания — о NSP\n"
    f"{SUPPORT_HINT}"
)

_ASK_MAXIM = "Если вопрос не про товары из каталога — напишите в Поддержку (/support), ответит человек."

NSP_PROFILE = register_tenant_profile(
    TenantProfile(
        tenant_id=TENANT_ID,
        display_name=DISPLAY_NAME,
        contact_handle=None,
        calculator=False,
        partner_prices=False,
        greeting=(
            "Здравствуйте! Я помощник по продукции NSP.\n\n"
            f"{_MENU}\n\n"
            "Напишите название товара или вопрос своими словами."
        ),
        capabilities=(
            "Я могу помочь с товарами NSP:\n\n"
            "• рассказать, что это за продукт и как его принимать\n"
            "• назвать розничную цену в $ из каталога 2026\n"
            "• показать фото\n"
            "• ответить на утверждённые вопросы о компании\n\n"
            f"{_ASK_MAXIM}"
        ),
        help_text=(
            "Выберите, с чем помочь:\n\n"
            f"{_MENU}\n\n"
            "Или напишите название товара."
        ),
        fallback_menu=(
            "Я помогаю с товарами NSP: карточка, цена, как принимать, фото. "
            "Названия знать не обязательно — откройте раздел.\n\n"
            f"{_MENU}\n\n"
            f"{_ASK_MAXIM}"
        ),
        medical_boundary=(
            "Продукты NSP — биологически активные добавки к пище, не лекарства, "
            "и они не заменяют лечение. По вопросам здоровья обратитесь к врачу.\n\n"
            "Могу рассказать, что это за продукт и как его принимать по официальной карточке."
        ),
        gap_overrides={
            "missing_resource": (
                "Для этого товара такой материал пока не добавлен. "
                "Могу показать карточку, цену или фото."
            ),
            "ambiguous_product": (
                "Уточните, о каком товаре речь — тогда дам цену, карточку или фото."
            ),
        },
    )
)
