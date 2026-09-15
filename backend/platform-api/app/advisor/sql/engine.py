"""Tenant-scoped structured SQL advisor engine (indexed queries, no snapshot)."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql import context as session_ctx
from app.advisor.gap import emit_gap_response, sanitize_user_text, GAP_TEXTS
from app.advisor.voice import (
    catalog_browse_text,
    company_intro_text,
    discomfort_boundary_text,
    empty_catalog_text,
    gap_text_for,
    income_question_text,
    is_home_tenant,
    maybe_prefix_home_brand,
    mlm_objection_text,
    product_selection_text,
    pv_definition_text,
    service_fallback,
)
from app.advisor.sql import formatters as fmt
from app.advisor.sql import repository as repo
from app.advisor.sql.ambiguity import try_ambiguity_clarification
from app.advisor.sql.basket import build_starter_basket, parse_budget_request, parse_basket_goal
from app.advisor.sql.business_formatters import format_community, format_events, format_promotions
from app.advisor.sql.canonical import try_canonical_response
from app.advisor.sql.cart_list import build_cart_list_response, parse_cart_list_request
from app.advisor.sql.cart_session import (
    build_cart_remove_response,
    cart_items_from_products,
    parse_cart_remove_request,
)
from app.advisor.sql.comparison import build_compare_answer
from app.advisor.sql.coach import build_coach_response, parse_coach_command
from app.advisor.sql import resolver as product_resolver
from app.advisor.sql.product_discovery import build_discovery_choice_response
from app.advisor.sql.solution_bundles import (
    choose_bundle_products,
    format_solution_bundle,
    has_specific_bundle_context,
    match_solution_bundle,
    may_match_solution_bundle,
    ordered_bundle_skus,
)
from app.advisor.sql.text import (
    detect_service_intent,
    has_basket_intent,
    has_community_intent,
    has_compare_intent,
    has_event_intent,
    has_media_intent,
    has_price_intent,
    has_promotion_intent,
    is_context_followup,
    is_materials_request,
    is_unsupported_topic,
    is_pv_definition_question,
    is_product_definition_question,
    is_calculator_request,
    is_start_options_request,
    is_company_intro_request,
    is_marketing_plan_request,
    is_step_topic_request,
    is_income_question,
    is_discomfort_boundary,
    is_high_risk_medical_boundary,
    is_product_selection_request,
    is_menu_reprompt,
    is_catalog_list_request,
    normalize_text,
    product_query_text,
    wants_partner_price,
    wants_retail_price,
    DETAILS_RE,
    VIDEO_RE,
    CERT_RE,
    PDF_RE,
    has_pro_marker,
    media_request_is_product_followup,
)
from app.db import tenant_connection
from app.tenancy import TenantContext

COMPARE_PAIR_RE = re.compile(
    r"(?:сравни(?:ть)?\s+)?(.+?)\s+(?:и|или|vs|против|лучше)\s+(.+)",
    re.I,
)
DIFF_FROM_PAIR_RE = re.compile(
    r"чем\s+отличается\s+(.+?)\s+от\s+(.+?)(?:\?|$)",
    re.I,
)

SAFETY_TREATMENT_RE = re.compile(r"(как\s+лечить|схема\s+лечен|диагноз)", re.I)
LIMITATION_RE = re.compile(r"(ограничен|противопоказ|кардиостимулятор|можно\s+ли)", re.I)

SERVICE_FALLBACKS = {
    "greeting": (
        "Здравствуйте! Я советник WHIEDA.\n\n"
        "📦 Товары\n"
        "• карточка, цена, фото, видео, сертификат\n"
        "• сравнение и подбор под задачу\n\n"
        "📈 Бизнес\n"
        "• PV, повторка, старт, маркетинг-план\n"
        "• акции, встречи и материалы\n\n"
        "Напишите название товара или вопрос своими словами."
    ),
    "smalltalk_status": "Спасибо, я на связи. Задайте вопрос по товару или бизнесу WHIEDA.",
    "capabilities": (
        "Я могу помочь с WHIEDA:\n\n"
        "📦 Товары\n"
        "• рассказать о товаре простыми словами\n"
        "• показать фото, видео, сертификат\n"
        "• сравнить товары\n\n"
        "💳 Цены и подбор\n"
        "• дать цену, PV, первичную и повторную покупку\n"
        "• посчитать корзину\n"
        "• подсказать выгодный старт и акции\n\n"
        "📈 Бизнес\n"
        "• объяснить PV, повторку, Step и старт\n"
        "• подсказать встречу или мероприятие\n\n"
        "Для расчёта напишите: Посчитай: Активатор клеток, БЭМ, Ба-Гуа"
    ),
    "help": (
        "Выберите, с чем помочь:\n\n"
        "📦 Товар: название, цена, фото, видео, сравнение\n"
        "🧮 Калькулятор: «Посчитай: Активатор, БЭМ»\n"
        "📈 Бизнес: PV, повторка, старт, маркетинг-план\n"
        "🏢 Компания: кто мы, продукты, события\n"
        "🧭 Подбор: опишите задачу или интересующую зону"
    ),
}

CAPABILITY_INTENT_ALIASES = {
    "capabilities": ("capabilities", "capability", "help_menu"),
    "greeting": ("greeting", "hello"),
    "help": ("help", "menu"),
    "smalltalk_status": ("smalltalk_status", "status"),
}

PV_DEFINITION_FALLBACK = (
    "PV (баллы) — это единица личного объёма в WHIEDA: за покупку товара начисляются баллы, "
    "они учитываются в бонусной программе и повторных заказах."
)

BUSINESS_FAQ_FALLBACKS: tuple[tuple[str, str], ...] = (
    (
        "повторк",
        "Повторные покупки (повторка) — заказы по партнёрской цене после регистрации в клубе WHIEDA.",
    ),
    ("бинарн", "Бинарный бонус — вид вознаграждения в маркетинг-плане WHIEDA за развитие структуры."),
)

MLM_OBJECTION_RE = re.compile(r"(?:^|\s)(?:млм|mlm)(?:\s|$)|сетевой\s+маркетинг|пирамид", re.I)
MLM_OBJECTION_FALLBACK = (
    "WHIEDA — это компания с продуктами и партнёрской программой. "
    "Доход партнёра зависит от личных продаж и работы с клиентами, а не только от приглашений."
)

DETAILS_TOPIC_UNKNOWN_FALLBACK = GAP_TEXTS["unknown_followup"]

LIMITATIONS_FALLBACK = (
    "при кардиостимуляторе, беременности и хронических заболеваниях "
    "согласуйте применение со специалистом и инструкцией."
)

CALCULATOR_INSTRUCTION = (
    "Для расчёта напишите одной строкой: Посчитай: активатор, спирулина — посчитаю BYN и PV."
)

CATALOG_BROWSE_FALLBACK = (
    "В каталоге WHIEDA есть приборы, товары для дома, уход и нутрицевтические продукты. "
    "В Telegram нажмите «📦 Товары», чтобы открыть список с кнопками. "
    "Или напишите, что интересует: приборы, уход, сон, эликсиры или конкретный товар."
)

COMPANY_INTRO_FALLBACK = (
    "WHIEDA — компания с продуктами для здоровья и партнёрской программой. "
    "Могу рассказать про товары, старт и маркетинг-план."
)

INCOME_QUESTION_FALLBACK = (
    "WHIEDA не обещает фиксированный доход — результат зависит от личных продаж и работы с клиентами. "
    "Могу рассказать про маркетинг-план, стартовые варианты и виды входа."
)

DISCOMFORT_BOUNDARY_TEXT = (
    "Помогу подобрать WHIEDA под вашу задачу. Выберите направление: "
    "домашний прибор, сон и восстановление, уход, энергия или старт. "
    "Напишите, для кого и какая цель важнее — предложу подходящие варианты."
)
PRODUCT_SELECTION_FALLBACK = (
    "Помогу подобрать товар или набор WHIEDA под вашу задачу. Выберите направление:\n\n"
    "• прибор для дома или кабинета;\n"
    "• сон и восстановление;\n"
    "• уход и подарок;\n"
    "• энергия и повседневная поддержка;\n"
    "• старт или бюджет.\n\n"
    "Напишите, для кого и какая цель важнее — предложу 2–3 подходящих варианта."
)
URGENT_SAFETY_BOUNDARY_TEXT = (
    "По описанию может быть острое состояние. Я не могу подсказывать лечение, "
    "дозировки или продолжение процедур. Нужна очная оценка врача; для животного — ветеринара. "
    "До консультации не используйте прибор или продукт для этой ситуации."
)

ACTIVATOR_VARIANT_PROMPT = "Вы про Активатор клеток или Активатор клеток PRO?"
ACTIVATOR_VARIANT_HINT = (
    "Если нужен базовый прибор, напишите «обычный». Если усиленная версия, напишите «PRO»."
)
AFFIRMATIVE_RE = re.compile(r"^(да|ага|угу|yes|ok|ок)\b", re.I)

UNKNOWN_PRODUCT_RE = re.compile(
    r"несуществующ|not[_ -]?in[_ -]?catalog|xyzabc|xyzunknown|qwerty|unknown(?:123)?|test\d{2,}",
    re.I,
)
EXPLICIT_PRODUCT_REQUEST_RE = re.compile(
    r"(?:товар|цена|стоим|фото|видео|сертифик|pdf|сравни|что такое|расскажи|"
    r"покажи|дай|купить|добавь в корзин|артикул)",
    re.I,
)
ACTIVATOR_BASE_CHOICE_RE = re.compile(r"\b(обычн|базов|стандарт)\w*\b", re.I)
ACTIVATOR_PRO_CHOICE_RE = re.compile(r"\b(pro|про)\b", re.I)


async def run_structured_query(
    tenant: TenantContext,
    body: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    question = str(body.get("question") or "").strip()
    session = str(body.get("session") or body.get("session_id") or "").strip()
    country = str(body.get("country") or "BY").upper()
    sku = str(body.get("sku") or "").strip() or None
    slug = str(body.get("slug") or "").strip() or None
    normalized = normalize_text(question)
    channel = str(body.get("channel") or body.get("surface") or "advisor").strip()[:32] or "advisor"

    service_intent = detect_service_intent(question)
    if service_intent:
        return await _service_intent_response(tenant, service_intent, trace_id)

    if is_catalog_list_request(question):
        return fmt.ok_response(
            catalog_browse_text(tenant),
            "structured_business",
            trace_id,
            media=fmt.empty_media(),
        )

    # An explicitly nonexistent SKU/name must not be silently reduced to the
    # nearest real product just because it contains a familiar word.
    if UNKNOWN_PRODUCT_RE.search(question):
        return await emit_gap_response(
            tenant.tenant_id,
            session=session,
            question=question,
            gap_kind="unknown_product",
            trace_id=trace_id,
            channel=channel,
            text=gap_text_for(tenant.tenant_id, "unknown_product"),
        )

    if is_high_risk_medical_boundary(question):
        return await emit_gap_response(
            tenant.tenant_id,
            session=session,
            question=question,
            gap_kind="medical_or_safety_boundary",
            trace_id=trace_id,
            channel=channel,
            text=URGENT_SAFETY_BOUNDARY_TEXT,
            answer_mode="clarification",
            clarifications=["urgent_medical_boundary"],
        )

    if is_unsupported_topic(question):
        return await emit_gap_response(
            tenant.tenant_id,
            session=session,
            question=question,
            gap_kind="unsupported_topic",
            trace_id=trace_id,
            channel=channel,
            text=gap_text_for(tenant.tenant_id, "unsupported_topic"),
        )

    if is_calculator_request(question):
        return fmt.ok_response(CALCULATOR_INSTRUCTION, "structured_business", trace_id, media=fmt.empty_media())

    # Bundle aliases live in the structured master sheet.  Load the small
    # active set for any meaningful query instead of maintaining a second,
    # incomplete topic list in Python.
    # A plain product question («сколько стоит активатор клеток», «линчжи»)
    # opens the product, not a scenario bundle that merely lists it; a bundle
    # wins only when nothing resolves directly or the wording is a scenario
    # («совместимость», «при плоскостопии»). Restored from 8f08a74 — the guard
    # was lost in the 2026-09-02 consolidation restore.
    if may_match_solution_bundle(question):
        async with tenant_connection(tenant.tenant_id) as conn:
            active_bundles = await repo.load_active_solution_bundles(conn, tenant.tenant_id)
            bundle = match_solution_bundle(question, active_bundles)
            if bundle and not has_specific_bundle_context(question):
                direct_product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
                if direct_product:
                    bundle = None
            if bundle:
                products = await repo.load_products_by_skus(
                    conn, tenant.tenant_id, ordered_bundle_skus(bundle)
                )
                selected_products = choose_bundle_products(bundle, products)
                skus = [str(product.get("sku")) for product in selected_products if product.get("sku")]
                context = {
                    "last_solution_bundle_id": str(bundle.get("bundle_id") or ""),
                    "last_solution_bundle_name": str(
                        bundle.get("bundle_name") or bundle.get("title") or ""
                    ),
                }
                if selected_products:
                    context.update(
                        {
                            "last_product_sku": str(selected_products[0].get("sku") or ""),
                            "last_product_name": str(selected_products[0].get("canonical_name") or ""),
                        }
                    )
                response = fmt.ok_response(
                    format_solution_bundle(bundle, selected_products, country),
                    "structured_solution_bundle",
                    trace_id,
                    product={"bundle_id": bundle.get("bundle_id"), "skus": skus},
                    context=context,
                )
                if session:
                    await session_ctx.merge_session_context(conn, tenant.tenant_id, session, context)
                return response

    if is_product_selection_request(question):
        return fmt.ok_response(
            product_selection_text(tenant),
            "clarification",
            trace_id,
            media=fmt.empty_media(),
            clarifications=["task_selection"],
        )

    if is_discomfort_boundary(question):
        return await emit_gap_response(
            tenant.tenant_id,
            session=session,
            question=question,
            gap_kind="medical_or_safety_boundary",
            trace_id=trace_id,
            channel=channel,
            text=discomfort_boundary_text(tenant),
            answer_mode="clarification",
            clarifications=["discomfort_boundary"],
        )

    if is_income_question(question):
        async with tenant_connection(tenant.tenant_id) as conn:
            faq = await repo.find_business_faq(conn, tenant.tenant_id, question)
            if faq:
                return fmt.ok_response(
                    str(faq.get("answer_text") or "").strip(),
                    "structured_business_faq",
                    trace_id,
                )
        return fmt.ok_response(income_question_text(tenant), "structured_business", trace_id, media=fmt.empty_media())

    if is_company_intro_request(question):
        async with tenant_connection(tenant.tenant_id) as conn:
            faq = await repo.find_business_faq(conn, tenant.tenant_id, question)
            objection = await repo.find_business_objection(conn, tenant.tenant_id, question)
            if faq:
                return fmt.ok_response(
                    str(faq.get("answer_text") or "").strip(),
                    "structured_business_faq",
                    trace_id,
                )
            if objection:
                return fmt.ok_response(
                    str(objection.get("first_reply") or "").strip(),
                    "structured_business_objection",
                    trace_id,
                )
        return fmt.ok_response(company_intro_text(tenant), "structured_business", trace_id, media=fmt.empty_media())

    if is_marketing_plan_request(question) or is_step_topic_request(question):
        lookup_question = re.sub(r"\bstep\b", "степ", question, flags=re.I)
        async with tenant_connection(tenant.tenant_id) as conn:
            faq = await repo.find_business_faq(conn, tenant.tenant_id, lookup_question)
        if faq:
            return fmt.ok_response(
                str(faq.get("answer_text") or "").strip(),
                "structured_business_faq",
                trace_id,
            )
        if is_marketing_plan_request(question):
            plan_text = (
                "Маркетинг-план WHIEDA описывает объём PV, повторные покупки, статусы и условия бонусов. "
                "Напишите, что именно разобрать: PV, повторку, бинар, Step или вариант старта."
                if is_home_tenant(tenant.tenant_id)
                else "Маркетинг-план описывает объём PV, повторные покупки, статусы и условия бонусов. "
                "Напишите, что именно разобрать: PV, повторку, бинар, Step или вариант старта."
            )
            return fmt.ok_response(
                plan_text,
                "structured_business_faq",
                trace_id,
                media=fmt.empty_media(),
            )
        return fmt.ok_response(
            "Step связан с условиями действующего маркетинг-плана и не является гарантированной выплатой. "
            "Могу объяснить условия, повторные покупки или статусную механику.",
            "structured_business_faq",
            trace_id,
            media=fmt.empty_media(),
        )

    if is_start_options_request(question):
        async with tenant_connection(tenant.tenant_id) as conn:
            stored = await session_ctx.load_session_context(conn, tenant.tenant_id, session)
            templates = await repo.load_starter_basket_templates(conn, tenant.tenant_id)
            if templates:
                titles = [str(row.get("title") or "").strip() for row in templates if row.get("title")]
                preview = ", ".join(titles[:3])
                text = (
                    "Есть несколько стартовых вариантов входа"
                    + (" в WHIEDA" if is_home_tenant(tenant.tenant_id) else "")
                    + (f": {preview}." if preview else ".")
                    + " Напишите бюджет в BYN или целевой PV — подберу корзину."
                )
                response = fmt.ok_response(
                    text,
                    "structured_starter_basket",
                    trace_id,
                    context={"starter_basket": True, "basket_goal": stored.get("basket_goal") or "balanced"},
                )
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
                return response
            prompt = await repo.load_clarification_prompt(conn, tenant.tenant_id, "starter_basket_budget")
            response = fmt.ok_response(
                prompt or "Подберу стартовую корзину. На какой бюджет в BYN или какой PV ориентируемся?",
                "clarification",
                trace_id,
                clarifications=["starter_basket_budget"],
                context={"starter_basket": True, "basket_goal": "balanced"},
            )
            await session_ctx.merge_session_context(conn, tenant.tenant_id, session, response["context"])
            return response

    coach_cmd = parse_coach_command(question)
    if coach_cmd:
        async with tenant_connection(tenant.tenant_id) as conn:
            stored = await session_ctx.load_session_context(conn, tenant.tenant_id, session)
            objections = await repo.load_coach_objections(conn, tenant.tenant_id)
            built = build_coach_response(coach_cmd, objections, stored)
            if built:
                text, mode, ctx = built
                response = fmt.ok_response(text, mode, trace_id, context=ctx)
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
                return response

    # Compare/cart must run before canonical lookup — substring match on product names
    # (e.g. «активатор клеток» inside «сравни спирулина и активатор клеток») otherwise
    # returns a product card instead of comparison or cart sum.
    skip_canonical = bool(
        parse_cart_list_request(question)
        or parse_cart_remove_request(question)
        or has_compare_intent(question)
        or has_media_intent(question)
        or is_materials_request(question)
        or has_pro_marker(question)
        or (has_price_intent(question) and not is_pv_definition_question(question))
        or SAFETY_TREATMENT_RE.search(question)
        or LIMITATION_RE.search(question)
        or (DETAILS_RE.search(question) and not sku)
        or normalize_text(question) in {"паста", "активатор", "ативатор", "пептид", "пептиды"}
        or ACTIVATOR_BASE_CHOICE_RE.search(normalized)
        or ACTIVATOR_PRO_CHOICE_RE.search(normalized)
    )

    if not skip_canonical:
        async with tenant_connection(tenant.tenant_id) as conn:
            # A plain product name must open the current product card.  Canonical
            # answers are historical shortcuts and may contain an old price-only
            # response for the same phrase.
            direct_product = (
                None
                if is_pv_definition_question(question)
                else await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
            )
            canonical = None if direct_product else await repo.find_canonical_question(
                conn, tenant.tenant_id, question
            )
            if canonical:
                canonical_response = await try_canonical_response(
                    conn,
                    tenant.tenant_id,
                    canonical,
                    country=country,
                    trace_id=trace_id,
                    session=session,
                )
                if canonical_response:
                    if canonical_response.get("context"):
                        await session_ctx.merge_session_context(
                            conn, tenant.tenant_id, session, canonical_response["context"]
                        )
                    return canonical_response

    async with tenant_connection(tenant.tenant_id) as conn:
        stored = await session_ctx.load_session_context(conn, tenant.tenant_id, session)
        if not is_home_tenant(tenant.tenant_id):
            catalog_size = await repo.count_catalog_products(conn, tenant.tenant_id)
            if catalog_size == 0:
                return fmt.ok_response(
                    empty_catalog_text(tenant),
                    "clarification",
                    trace_id,
                    media=fmt.empty_media(),
                    clarifications=["empty_tenant_catalog"],
                )

        if is_menu_reprompt(question):
            pending_clarification = str(stored.get("pending_product_clarification") or "")
            if pending_clarification == "activator_variant":
                return await emit_gap_response(
                    tenant.tenant_id,
                    session=session,
                    question=question,
                    gap_kind="ambiguous_product",
                    trace_id=trace_id,
                    channel=channel,
                    text=f"{ACTIVATOR_VARIANT_PROMPT}\n{ACTIVATOR_VARIANT_HINT}",
                    answer_mode="clarification",
                    clarifications=["product_ambiguity_activator"],
                )
            return await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="unrouted_message",
                trace_id=trace_id,
                channel=channel,
            )

        remove_name = parse_cart_remove_request(question)
        if remove_name is not None:
            active_cart = list(stored.get("active_cart") or [])
            if not active_cart:
                text, _, key = build_cart_remove_response(remove_name, active_cart, resolved_product=None)
                return fmt.ok_response(
                    text,
                    "clarification",
                    trace_id,
                    clarifications=[key] if key else None,
                )
            resolved_remove = await product_resolver.resolve_product(
                conn, tenant.tenant_id, remove_name, None, None, repo=repo
            )
            text, remaining, key = build_cart_remove_response(
                remove_name, active_cart, resolved_product=resolved_remove
            )
            response = fmt.ok_response(
                text,
                "structured_cart" if remaining else "clarification",
                trace_id,
                product={"skus": [str(item["sku"]) for item in remaining]} if remaining else None,
                clarifications=[key] if key else None,
                context={"active_cart": remaining} if remaining else {"active_cart": []},
            )
            if session:
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
            return response

        cart_names = parse_cart_list_request(question)
        if cart_names:
            resolved: list[dict[str, Any]] = []
            missing: list[str] = []
            for name in cart_names:
                product = await product_resolver.resolve_product(
                    conn, tenant.tenant_id, name, None, None, repo=repo
                )
                if not product or any(str(item["sku"]) == str(product["sku"]) for item in resolved):
                    if not product:
                        missing.append(name)
                    continue
                resolved.append(product)
            text, skus = build_cart_list_response(cart_names, resolved, missing)
            mode = "structured_cart" if resolved else "clarification"
            clarifications = ["cart_item_unknown"] if not resolved else None
            cart_ctx = {"active_cart": cart_items_from_products(resolved)} if resolved else {}
            response = fmt.ok_response(
                text,
                mode,
                trace_id,
                product={"skus": skus} if skus else None,
                clarifications=clarifications,
                context=cart_ctx,
            )
            if cart_ctx and session:
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
            return response

        basket_active = has_basket_intent(question) or bool(stored.get("starter_basket"))
        budget_req = parse_budget_request(question, stored) if basket_active else None
        if basket_active:
            if budget_req:
                catalog = await repo.load_recommendation_catalog(conn, tenant.tenant_id)
                templates = await repo.load_starter_basket_templates(conn, tenant.tenant_id)
                text, skus = build_starter_basket(
                    catalog,
                    budget_byn=budget_req.get("budget_byn"),
                    target_pv=budget_req.get("target_pv"),
                    goal=str(budget_req.get("goal") or "balanced"),
                    templates=templates,
                    tenant_id=tenant.tenant_id,
                )
                response = fmt.ok_response(
                    text,
                    "structured_starter_basket",
                    trace_id,
                    product={"skus": skus} if skus else None,
                    context={"starter_basket": False, "basket_goal": budget_req.get("goal")},
                )
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
                return response
            if has_basket_intent(question):
                prompt = await repo.load_clarification_prompt(
                    conn, tenant.tenant_id, "starter_basket_budget"
                )
                response = fmt.ok_response(
                    prompt or "Подберу стартовую корзину. На какой бюджет в BYN или какой PV ориентируемся?",
                    "clarification",
                    trace_id,
                    clarifications=["starter_basket_budget"],
                    context={
                        "starter_basket": True,
                        "basket_goal": parse_basket_goal(question, stored),
                    },
                )
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
                return response

        if has_price_intent(question) and has_promotion_intent(question):
            product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
            parts: list[str] = []
            if product:
                price = fmt.format_price(
                    product,
                    country,
                    partner_only=wants_partner_price(question),
                    retail_only=wants_retail_price(question),
                )
                parts.append(f"{product['canonical_name']}: {price}")
            else:
                prompt = await repo.load_clarification_prompt(
                    conn, tenant.tenant_id, "price_product_unknown"
                )
                if prompt:
                    parts.append(prompt)
            promos = await repo.load_active_promotions(conn, tenant.tenant_id, country)
            promo_text = format_promotions(promos)
            if promo_text:
                parts.append(promo_text)
            response = fmt.ok_response(
                "\n\n".join(parts),
                "structured_price" if product else "structured_promotion",
                trace_id,
                product=(
                    {"sku": product["sku"], "canonical_name": product["canonical_name"]}
                    if product
                    else None
                ),
                context=(
                    {
                        "last_product_sku": product["sku"],
                        "last_product_name": product["canonical_name"],
                    }
                    if product
                    else {}
                ),
            )
            if product:
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
            return response

        if has_promotion_intent(question):
            promos = await repo.load_active_promotions(conn, tenant.tenant_id, country)
            text = format_promotions(promos)
            return fmt.ok_response(text, "structured_promotion", trace_id)

        if has_event_intent(question):
            events = await repo.load_upcoming_events(conn, tenant.tenant_id, country)
            text = format_events(events)
            return fmt.ok_response(text, "structured_event", trace_id)

        if has_community_intent(question):
            resources = await repo.load_community_resources(conn, tenant.tenant_id, country)
            text = format_community(resources)
            return fmt.ok_response(text, "structured_community", trace_id)

        objection = await repo.find_business_objection(conn, tenant.tenant_id, question)
        if objection and not has_price_intent(question):
            return fmt.ok_response(
                str(objection.get("first_reply") or "").strip(),
                "structured_business_objection",
                trace_id,
            )
        if MLM_OBJECTION_RE.search(question):
            return fmt.ok_response(
                mlm_objection_text(tenant),
                "structured_business_objection",
                trace_id,
            )

        if SAFETY_TREATMENT_RE.search(question):
            return await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="medical_or_safety_boundary",
                trace_id=trace_id,
                channel=channel,
                clarifications=["safety_no_treatment_advice"],
            )

        if is_pv_definition_question(question):
            faq_pv = await repo.find_business_faq(conn, tenant.tenant_id, question)
            if faq_pv:
                return fmt.ok_response(
                    str(faq_pv.get("answer_text") or "").strip(),
                    "structured_business_faq",
                    trace_id,
                )
            return fmt.ok_response(pv_definition_text(tenant), "structured_business_faq", trace_id)

        if (
            (is_context_followup(question) or DETAILS_RE.search(question))
            and not stored.get("last_product_sku")
            and not has_price_intent(question)
        ):
            details_product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
            if not details_product:
                return await emit_gap_response(
                    tenant.tenant_id,
                    session=session,
                    question=question,
                    gap_kind="unknown_followup",
                    trace_id=trace_id,
                    channel=channel,
                    clarifications=["details_topic_unknown"],
                )

        faq_fallback = _business_faq_fallback(question) if is_home_tenant(tenant.tenant_id) else None
        if faq_fallback and not has_price_intent(question):
            return fmt.ok_response(faq_fallback, "structured_business_faq", trace_id)

        faq = await repo.find_business_faq(conn, tenant.tenant_id, question)
        # A matching business FAQ explains the rule, but a named product plus
        # «повторка/цена» asks for the current price.  Do not let the FAQ keep
        # its value after the price guard has rejected it.
        if faq and (
            (has_price_intent(question) and not is_pv_definition_question(question))
            or has_media_intent(question)
        ):
            faq = None
        elif faq:
            if is_product_definition_question(question):
                product_for_def = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
                if product_for_def:
                    faq = None
        if faq:
            return fmt.ok_response(
                str(faq.get("answer_text") or "").strip(),
                "structured_business_faq",
                trace_id,
            )

        if has_compare_intent(question) or (
            stored.get("last_product_sku")
            and re.search(r"чем отличается|от обычного|от базов", question, re.I)
        ):
            if stored.get("last_product_sku") and re.search(
                r"чем отличается|от обычного|от базов", question, re.I
            ):
                anchor = await repo.resolve_product_by_sku(
                    conn, tenant.tenant_id, str(stored["last_product_sku"])
                )
                if anchor and has_pro_marker(str(anchor.get("canonical_name") or "")):
                    base = await product_resolver.resolve_product(
                        conn, tenant.tenant_id, "активатор клеток", None, None, repo=repo
                    )
                    if base and anchor:
                        comparison = await repo.find_product_comparison(
                            conn, tenant.tenant_id, base["sku"], anchor["sku"]
                        )
                        if comparison:
                            comparison_context = {
                                "last_product_sku": anchor["sku"],
                                "last_product_name": anchor["canonical_name"],
                                "last_compare_right_sku": base["sku"],
                                "last_compare_right_name": base["canonical_name"],
                            }
                            response = fmt.ok_response(
                                str(comparison.get("answer_text") or "").strip(),
                                "structured_comparison_layer",
                                trace_id,
                                product={
                                    "sku": anchor["sku"],
                                    "canonical_name": anchor["canonical_name"],
                                },
                                context=comparison_context,
                            )
                            await session_ctx.merge_session_context(
                                conn, tenant.tenant_id, session, response["context"]
                            )
                            return response
                        left_card = await repo.load_product_card(conn, tenant.tenant_id, base["sku"])
                        right_card = await repo.load_product_card(conn, tenant.tenant_id, anchor["sku"])
                        text = build_compare_answer(base, left_card, anchor, right_card, country=country)
                        response = fmt.ok_response(
                            text,
                            "structured_comparison_layer",
                            trace_id,
                            product={"sku": anchor["sku"], "canonical_name": anchor["canonical_name"]},
                            context={
                                "last_product_sku": anchor["sku"],
                                "last_product_name": anchor["canonical_name"],
                                "last_compare_right_sku": base["sku"],
                                "last_compare_right_name": base["canonical_name"],
                            },
                        )
                        await session_ctx.merge_session_context(
                            conn, tenant.tenant_id, session, response["context"]
                        )
                        return response
            comparison_response = await _resolve_comparison_response(
                conn, tenant.tenant_id, question, country, trace_id, session=session
            )
            if comparison_response:
                return comparison_response
            prompt = await repo.load_clarification_prompt(
                conn, tenant.tenant_id, "compare_pair_unknown"
            )
            compare_text = sanitize_user_text(
                prompt
                or "Не нашёл готовое сравнение. Уточните два товара, например: «сравни Спирулину и Активатор».",
                fallback_kind="unsupported_topic",
            )
            return await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="unsupported_topic",
                trace_id=trace_id,
                channel=channel,
                text=compare_text,
                answer_mode="clarification",
                clarifications=["compare_pair_unknown"],
            )

        pending_clarification = str(stored.get("pending_product_clarification") or "")
        product = None
        pending_context_patch: dict[str, Any] | None = None

        # Strong exact aliases continue through the normal resolver. Generic
        # discovery phrases are deliberately intercepted before alias scoring
        # can silently choose a random first product. Explicit product tokens
        # (SKU, slug, PRO) resolve first so discovery cannot steal them or
        # require a live DB cursor in the unit path.
        discovery_phrase = product_query_text(question)
        explicit_product = bool(sku or slug or has_pro_marker(question))
        if (
            not explicit_product
            and is_home_tenant(tenant.tenant_id)
            and discovery_phrase
            not in {
                "активатор",
                "паста",
                "красный",
                "зелёный",
                "зеленый",
                "синий",
                "пояс",
            }
        ):
            discovery = await build_discovery_choice_response(
                conn,
                tenant.tenant_id,
                discovery_phrase,
                repo=repo,
                trace_id=trace_id,
                fmt=fmt,
            )
            if discovery:
                if session and discovery.get("context"):
                    await session_ctx.merge_session_context(
                        conn, tenant.tenant_id, session, discovery["context"]
                    )
                return discovery

        if pending_clarification == "activator_variant":
            pending_base_sku = str(stored.get("pending_base_sku") or "")
            pending_pro_sku = str(stored.get("pending_pro_sku") or "")
            if is_home_tenant(tenant.tenant_id):
                pending_base_sku = pending_base_sku or "M015-00"
                pending_pro_sku = pending_pro_sku or "EU-N000031-25"
            pending_sku = None
            if ACTIVATOR_BASE_CHOICE_RE.search(normalized):
                pending_sku = pending_base_sku
            elif ACTIVATOR_PRO_CHOICE_RE.search(normalized):
                pending_sku = pending_pro_sku
            elif AFFIRMATIVE_RE.search(normalized):
                return await emit_gap_response(
                    tenant.tenant_id,
                    session=session,
                    question=question,
                    gap_kind="ambiguous_product",
                    trace_id=trace_id,
                    channel=channel,
                    text=f"{ACTIVATOR_VARIANT_PROMPT}\n{ACTIVATOR_VARIANT_HINT}",
                    answer_mode="clarification",
                    clarifications=["product_ambiguity_activator"],
                )
            if pending_sku:
                product = await repo.resolve_product_by_sku(conn, tenant.tenant_id, pending_sku)
                if product:
                    pending_context_patch = {
                        "pending_product_clarification": None,
                        "pending_base_sku": None,
                        "pending_pro_sku": None,
                        "last_product_sku": product["sku"],
                        "last_product_name": product["canonical_name"],
                    }
                    await session_ctx.merge_session_context(
                        conn, tenant.tenant_id, session, pending_context_patch
                    )

        if not product:
            product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)

        if (
            not product
            and (media_request_is_product_followup(question) or is_context_followup(question))
            and stored.get("last_product_sku")
        ):
            remembered = await repo.resolve_product_by_sku(
                conn, tenant.tenant_id, str(stored["last_product_sku"])
            )
            if remembered:
                product = remembered

        ambiguity = await try_ambiguity_clarification(
            conn, tenant.tenant_id, question, best_product=product, repo=repo
        )
        if ambiguity:
            text, mode, keys = ambiguity
            ambiguity_context: dict[str, Any] | None = None
            if "product_ambiguity_activator" in (keys or []):
                ambiguity_context = {
                    "pending_product_clarification": "activator_variant",
                    "pending_base_sku": "M015-00" if is_home_tenant(tenant.tenant_id) else None,
                    "pending_pro_sku": "EU-N000031-25" if is_home_tenant(tenant.tenant_id) else None,
                }
                text = f"{ACTIVATOR_VARIANT_PROMPT}\n{ACTIVATOR_VARIANT_HINT}"
            response = await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="ambiguous_product",
                trace_id=trace_id,
                channel=channel,
                text=text,
                answer_mode=mode,
                clarifications=keys,
                context=ambiguity_context,
            )
            if ambiguity_context:
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, ambiguity_context
                )
            return response

        if (
            not product
            and not stored.get("last_product_sku")
            and (
                is_context_followup(question)
                or media_request_is_product_followup(question)
            )
        ):
            return await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="unknown_followup",
                trace_id=trace_id,
                channel=channel,
                clarifications=["unknown_followup"],
            )

        if not product and has_price_intent(question) and stored.get("last_product_sku"):
            product = await repo.resolve_product_by_sku(
                conn, tenant.tenant_id, str(stored["last_product_sku"])
            )

        if not product and is_context_followup(question) and stored.get("last_product_sku"):
            product = await repo.resolve_product_by_sku(
                conn, tenant.tenant_id, str(stored["last_product_sku"])
            )

        if not product and has_price_intent(question):
            prompt = await repo.load_clarification_prompt(
                conn, tenant.tenant_id, "price_product_unknown"
            )
            return fmt.ok_response(
                prompt or "О каком товаре хотите узнать цену?",
                "clarification",
                trace_id,
                clarifications=["product_name_or_sku"],
                media=fmt.empty_media(),
            )

        if product and has_price_intent(question):
            price = fmt.format_price(
                product,
                country,
                partner_only=wants_partner_price(question),
                retail_only=wants_retail_price(question),
            )
            response = fmt.ok_response(
                f"{product['canonical_name']}: {price}",
                "structured_price",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )
            await session_ctx.merge_session_context(
                conn, tenant.tenant_id, session, response["context"]
            )
            return response

        if product and media_request_is_product_followup(question):
            card = await repo.load_product_card(conn, tenant.tenant_id, product["sku"])
            resources = await repo.load_product_resources(conn, tenant.tenant_id, product["sku"])
            kind = "photo"
            if is_materials_request(question):
                kind = "certificate"
            elif VIDEO_RE.search(question):
                kind = "video"
            elif CERT_RE.search(question) or PDF_RE.search(question):
                kind = "certificate"
            media = fmt.build_media_payload(card, resources, kind)
            ctx = {
                "last_product_sku": product["sku"],
                "last_product_name": product["canonical_name"],
            }
            if kind == "photo" and media.get("photo_url"):
                response = fmt.ok_response(
                    f"Отправляю фото: {product['canonical_name']}",
                    "structured_photo",
                    trace_id,
                    product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                    media=media,
                    context=ctx,
                )
                await session_ctx.merge_session_context(conn, tenant.tenant_id, session, ctx)
                return response
            if kind == "video" and media.get("videos"):
                response = fmt.ok_response(
                    f"Видео по {product['canonical_name']}: {media['videos'][0]['url']}",
                    "structured_video",
                    trace_id,
                    product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                    media=media,
                    context=ctx,
                )
                await session_ctx.merge_session_context(conn, tenant.tenant_id, session, ctx)
                return response
            if kind == "certificate" and media.get("documents"):
                response = fmt.ok_response(
                    f"Материалы: {media['documents'][0]['url']}",
                    "structured_certificate",
                    trace_id,
                    product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                    media=media,
                    context=ctx,
                )
                await session_ctx.merge_session_context(conn, tenant.tenant_id, session, ctx)
                return response
            if kind == "certificate":
                text = fmt.MISSING_CERTIFICATE_TEXT
                mode = "structured_certificate"
            elif kind == "photo" and not media.get("photo_url"):
                text = f"{fmt.MISSING_PHOTO_TEXT}: {product['canonical_name']}"
                mode = "clarification"
            else:
                text = sanitize_user_text(
                    f"По {product['canonical_name']} такого материала пока нет. "
                    "Могу показать карточку, цену или другое доступное фото или видео.",
                    fallback_kind="missing_resource",
                )
                mode = "clarification"
            response = await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="missing_resource",
                trace_id=trace_id,
                channel=channel,
                text=text,
                answer_mode=mode,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                context=ctx,
                detected_product=product["canonical_name"],
            )
            response["media"] = media
            await session_ctx.merge_session_context(conn, tenant.tenant_id, session, ctx)
            return response

        if product and is_context_followup(question):
            detail = await repo.load_product_detail(conn, tenant.tenant_id, product["sku"], question)
            if detail:
                response = fmt.ok_response(
                    str(detail.get("answer_text") or detail.get("title") or "").strip(),
                    "structured_product_detail",
                    trace_id,
                    product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                    context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
                )
                await session_ctx.merge_session_context(
                    conn, tenant.tenant_id, session, response["context"]
                )
                return response

        if product and LIMITATION_RE.search(question):
            card = await repo.load_product_card(conn, tenant.tenant_id, product["sku"])
            contra = str((card or {}).get("contraindications_short") or "").strip()
            limitation_text = contra or LIMITATIONS_FALLBACK
            if re.search(r"кардиостимулятор|стент", question, re.I):
                return await emit_gap_response(
                    tenant.tenant_id,
                    session=session,
                    question=question,
                    gap_kind="medical_or_safety_boundary",
                    trace_id=trace_id,
                    channel=channel,
                    text=(
                        f"{product['canonical_name']}. Ограничения: {limitation_text} "
                        "При наличии кардиостимулятора или стентов вопрос применения "
                        "нужно согласовать с лечащим врачом и инструкцией к прибору."
                    ),
                    answer_mode="clarification",
                    product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                    context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
                    clarifications=["medical_device_limitation"],
                )
            return fmt.ok_response(
                f"{product['canonical_name']}. Ограничения: {limitation_text}",
                "structured_product_detail",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )

        if product:
            card = await repo.load_product_card(conn, tenant.tenant_id, product["sku"])
            compact = bool(re.search(r"\b(кратко|коротко|short)\b", normalized))
            text = fmt.format_product_card(card, product, compact=compact)
            media = fmt.build_media_payload(card, [], "photo")
            response = fmt.ok_response(
                text,
                "structured_card",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                media=media,
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )
            await session_ctx.merge_session_context(
                conn, tenant.tenant_id, session, response["context"]
            )
            return response

        if not product and _should_use_knowledge_gap(question, normalized):
            return await emit_gap_response(
                tenant.tenant_id,
                session=session,
                question=question,
                gap_kind="unknown_product",
                trace_id=trace_id,
                channel=channel,
            )

    gap_kind = "unknown_product" if EXPLICIT_PRODUCT_REQUEST_RE.search(question) else "unrouted_message"
    return await emit_gap_response(
        tenant.tenant_id,
        session=session,
        question=question,
        gap_kind=gap_kind,
        trace_id=trace_id,
        channel=channel,
    )


def _should_use_knowledge_gap(question: str, normalized: str) -> bool:
    if UNKNOWN_PRODUCT_RE.search(question):
        return True
    if re.search(r"расскаж\w*\s+про", normalized) and len(normalized) >= 12:
        if normalized in {"активатор", "паста", "пептид", "пептиды"}:
            return False
        from app.advisor.sql.ambiguity import weak_color_or_belt_clarification

        weak = weak_color_or_belt_clarification(question)
        if weak and not weak.get("direct"):
            return False
        return True
    return False


def _business_faq_fallback(question: str) -> str | None:
    normalized = normalize_text(question)
    if not re.search(r"что\s+(?:такое|это|значит)|объясни", normalized):
        return None
    for needle, text in BUSINESS_FAQ_FALLBACKS:
        if needle in normalized:
            return text
    return None


async def _service_intent_response(
    tenant: TenantContext, intent_id: str, trace_id: str
) -> dict[str, Any]:
    fallback = service_fallback(tenant, intent_id)
    async with tenant_connection(tenant.tenant_id) as conn:
        text = None
        for candidate in CAPABILITY_INTENT_ALIASES.get(intent_id, (intent_id,)):
            text = await repo.load_capability_response(conn, tenant.tenant_id, candidate)
            if text:
                break
    answer = maybe_prefix_home_brand(tenant, str(text or fallback).strip())
    return fmt.ok_response(answer, "structured_business", trace_id, media=fmt.empty_media())


async def _resolve_product(
    conn,
    tenant_id: str,
    question: str,
    sku: str | None,
    slug: str | None,
) -> dict[str, Any] | None:
    return await product_resolver.resolve_product(
        conn, tenant_id, question, sku, slug, repo=repo
    )


async def _resolve_comparison_response(
    conn,
    tenant_id: str,
    question: str,
    country: str,
    trace_id: str,
    *,
    session: str = "",
) -> dict[str, Any] | None:
    pair = _parse_compare_pair(question)
    if not pair:
        return None
    left = await product_resolver.resolve_product(conn, tenant_id, pair[0], None, None, repo=repo)
    right = await product_resolver.resolve_product(conn, tenant_id, pair[1], None, None, repo=repo)
    if not left or not right:
        return None

    approved = await repo.find_product_comparison(conn, tenant_id, left["sku"], right["sku"])
    approved_text = str(approved.get("answer_text") or approved.get("title") or "").strip() if approved else ""
    if approved_text:
        response = fmt.ok_response(
            approved_text,
            "structured_comparison_layer",
            trace_id,
            product={"sku": left["sku"], "canonical_name": left["canonical_name"]},
            context={
                "last_product_sku": left["sku"],
                "last_product_name": left["canonical_name"],
                "last_compare_right_sku": right["sku"],
                "last_compare_right_name": right["canonical_name"],
            },
        )
        if session:
            await session_ctx.merge_session_context(conn, tenant_id, session, response["context"])
        return response

    left_card = await repo.load_product_card(conn, tenant_id, left["sku"])
    right_card = await repo.load_product_card(conn, tenant_id, right["sku"])
    text = build_compare_answer(left, left_card, right, right_card, country=country)
    response = fmt.ok_response(
        text,
        "structured_comparison_layer",
        trace_id,
        product={
            "left_sku": left["sku"],
            "right_sku": right["sku"],
            "canonical_name": f"{left['canonical_name']} vs {right['canonical_name']}",
        },
        context={"last_product_sku": left["sku"], "last_product_name": left["canonical_name"]},
    )
    if session:
        await session_ctx.merge_session_context(conn, tenant_id, session, response["context"])
    return response


def _parse_compare_pair(question: str) -> tuple[str, str] | None:
    match = DIFF_FROM_PAIR_RE.search(question) or COMPARE_PAIR_RE.search(question)
    if not match:
        return None
    left_text = match.group(1).strip(" :")
    right_text = match.group(2).strip(" :")
    if not left_text or not right_text:
        return None
    return left_text, right_text
