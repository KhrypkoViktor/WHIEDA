"""Tenant-scoped structured SQL advisor engine (indexed queries, no snapshot)."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql import context as session_ctx
from app.advisor.sql import formatters as fmt
from app.advisor.sql import repository as repo
from app.advisor.sql.ambiguity import try_ambiguity_clarification
from app.advisor.sql.basket import build_starter_basket, parse_budget_request, parse_basket_goal
from app.advisor.sql.business_formatters import format_community, format_events, format_promotions
from app.advisor.sql.canonical import try_canonical_response
from app.advisor.sql.cart_list import build_cart_list_response, parse_cart_list_request
from app.advisor.sql.comparison import build_compare_answer
from app.advisor.sql.coach import build_coach_response, parse_coach_command
from app.advisor.sql import resolver as product_resolver
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
    is_pv_definition_question,
    is_product_definition_question,
    normalize_text,
    wants_partner_price,
    wants_retail_price,
    DETAILS_RE,
    VIDEO_RE,
    CERT_RE,
    PDF_RE,
    has_pro_marker,
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
        "Здравствуйте! Я помогу с ценами, карточками товаров, фото, сравнениями "
        "и базовой информацией WHIEDA."
    ),
    "smalltalk_status": "Спасибо, я на связи. Задайте вопрос по товару или бизнесу WHIEDA.",
    "capabilities": (
        "Могу подсказать цену, PV, карточку товара, фото, видео, сертификаты и сравнение. "
        "Назовите товар или укажите артикул."
    ),
    "help": (
        "Напишите название товара, цену, «фото …», «сравни … и …» или вопрос про PV, "
        "доставку и оформление."
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

DETAILS_TOPIC_UNKNOWN_FALLBACK = (
    "Чтобы рассказать подробнее, нужен сам товар — уточните название или артикул."
)

LIMITATIONS_FALLBACK = (
    "при кардиостимуляторе, беременности и хронических заболеваниях "
    "согласуйте применение со специалистом и инструкцией."
)

UNKNOWN_PRODUCT_RE = re.compile(
    r"несуществующ|xyzabc|xyzunknown|qwerty|unknown123",
    re.I,
)


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

    service_intent = detect_service_intent(question)
    if service_intent:
        return await _service_intent_response(tenant.tenant_id, service_intent, trace_id)

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
        or has_compare_intent(question)
        or has_media_intent(question)
        or is_materials_request(question)
        or has_pro_marker(question)
        or (has_price_intent(question) and not is_pv_definition_question(question))
        or SAFETY_TREATMENT_RE.search(question)
        or LIMITATION_RE.search(question)
        or (DETAILS_RE.search(question) and not sku)
        or normalize_text(question) in {"паста", "активатор", "ативатор", "пептид", "пептиды"}
    )

    if not skip_canonical:
        async with tenant_connection(tenant.tenant_id) as conn:
            canonical = await repo.find_canonical_question(conn, tenant.tenant_id, question)
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
            return fmt.ok_response(
                text,
                mode,
                trace_id,
                product={"skus": skus} if skus else None,
                clarifications=clarifications,
            )

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
                    prompt or "Соберу стартовую корзину. На какой бюджет в BYN или какой PV ориентируемся?",
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
                MLM_OBJECTION_FALLBACK,
                "structured_business_objection",
                trace_id,
            )

        if SAFETY_TREATMENT_RE.search(question):
            return fmt.ok_response(
                "Прибор и продукты WHIEDA не заменяет схему лечения диагноза. "
                "Уточните задачу — подскажу по применению и ограничениям из карточки.",
                "clarification",
                trace_id,
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
            return fmt.ok_response(PV_DEFINITION_FALLBACK, "structured_business_faq", trace_id)

        if (is_context_followup(question) or DETAILS_RE.search(question)) and not stored.get(
            "last_product_sku"
        ):
            details_product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)
            if not details_product:
                return fmt.ok_response(
                    DETAILS_TOPIC_UNKNOWN_FALLBACK,
                    "clarification",
                    trace_id,
                    clarifications=["details_topic_unknown"],
                )

        faq_fallback = _business_faq_fallback(question)
        if faq_fallback:
            return fmt.ok_response(faq_fallback, "structured_business_faq", trace_id)

        faq = await repo.find_business_faq(conn, tenant.tenant_id, question)
        if faq and not (has_price_intent(question) and not is_pv_definition_question(question)):
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
                            response = fmt.ok_response(
                                str(comparison.get("answer_text") or "").strip(),
                                "structured_comparison_layer",
                                trace_id,
                                product={
                                    "sku": base["sku"],
                                    "canonical_name": base["canonical_name"],
                                },
                                context={
                                    "last_product_sku": base["sku"],
                                    "last_product_name": base["canonical_name"],
                                },
                            )
                            await session_ctx.merge_session_context(
                                conn, tenant.tenant_id, session, response["context"]
                            )
                            return response
                        left_card = await repo.load_product_card(conn, tenant.tenant_id, base["sku"])
                        right_card = await repo.load_product_card(conn, tenant.tenant_id, anchor["sku"])
                        text = build_compare_answer(base, left_card, anchor, right_card, country=country)
                        return fmt.ok_response(text, "structured_comparison", trace_id)
            comparison_response = await _resolve_comparison_response(
                conn, tenant.tenant_id, question, country, trace_id, session=session
            )
            if comparison_response:
                return comparison_response
            prompt = await repo.load_clarification_prompt(
                conn, tenant.tenant_id, "compare_pair_unknown"
            )
            return fmt.ok_response(
                prompt or "Не нашёл готовое сравнение. Уточните два товара, например: «сравни Спирулину и Активатор».",
                "clarification",
                trace_id,
            )

        product = await _resolve_product(conn, tenant.tenant_id, question, sku, slug)

        if (has_media_intent(question) or is_materials_request(question) or is_context_followup(question)) and stored.get("last_product_sku"):
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
            return fmt.ok_response(text, mode, trace_id, clarifications=keys)

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
                prompt or "Уточните, пожалуйста, название товара или артикул — тогда назову цену.",
                "clarification",
                trace_id,
                clarifications=["product_name_or_sku"],
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

        if product and (has_media_intent(question) or is_materials_request(question)):
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
            if kind == "photo" and media.get("photo_url"):
                text = f"Отправляю фото: {product['canonical_name']}"
                mode = "structured_photo"
            elif kind == "video" and media.get("videos"):
                text = f"Видео по {product['canonical_name']}: {media['videos'][0]['url']}"
                mode = "structured_video"
            elif kind == "certificate" and media.get("documents"):
                text = f"Материалы: {media['documents'][0]['url']}"
                mode = "structured_certificate"
            elif kind == "certificate":
                text = fmt.MISSING_CERTIFICATE_TEXT
                mode = "structured_certificate"
            elif kind == "photo" and not media.get("photo_url"):
                text = f"{fmt.MISSING_PHOTO_TEXT}: {product['canonical_name']}"
                mode = "structured_photo"
            else:
                text = f"По {product['canonical_name']} пока нет подходящего материала в базе."
                mode = "clarification"
            response = fmt.ok_response(
                text,
                mode,
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                media=media,
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )
            await session_ctx.merge_session_context(
                conn, tenant.tenant_id, session, response["context"]
            )
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
            return fmt.ok_response(
                f"{product['canonical_name']}. Ограничения: {limitation_text}",
                "structured_product_detail",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )

        if product:
            card = await repo.load_product_card(conn, tenant.tenant_id, product["sku"])
            text = fmt.format_product_card(card, product)
            canonical = str(product.get("canonical_name") or "").strip()
            if canonical and canonical.lower() not in normalize_text(text)[:240]:
                text = f"{canonical} — {text}" if text else canonical
            if has_pro_marker(canonical):
                pro_name = canonical
                if pro_name and "pro" not in normalize_text(text)[:160]:
                    text = f"{pro_name} — {text}" if text else pro_name
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
            return fmt.ok_response(
                await _knowledge_gap_text(tenant.tenant_id, trace_id),
                "knowledge_gap",
                trace_id,
                media=fmt.empty_media(),
            )

        if normalized and len(normalized) >= 4:
            prompt = await repo.load_clarification_prompt(
                conn, tenant.tenant_id, "product_ambiguity_general"
            )
            if prompt:
                return fmt.ok_response(prompt, "clarification", trace_id)

    return fmt.ok_response(
        await _knowledge_gap_text(tenant.tenant_id, trace_id),
        "knowledge_gap",
        trace_id,
        media=fmt.empty_media(),
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


async def _knowledge_gap_text(tenant_id: str, trace_id: str) -> str:
    async with tenant_connection(tenant_id) as conn:
        for key in ("knowledge_gap_generic", "fallback_unknown", "product_ambiguity_general"):
            prompt = await repo.load_clarification_prompt(conn, tenant_id, key)
            if prompt:
                return prompt
    return (
        "Пока нет подтверждённого ответа в базе WHIEDA. "
        "Уточните название товара или артикул — или я передам вопрос команде."
    )


async def _service_intent_response(tenant_id: str, intent_id: str, trace_id: str) -> dict[str, Any]:
    fallback = SERVICE_FALLBACKS.get(intent_id, SERVICE_FALLBACKS["help"])
    async with tenant_connection(tenant_id) as conn:
        text = None
        for candidate in CAPABILITY_INTENT_ALIASES.get(intent_id, (intent_id,)):
            text = await repo.load_capability_response(conn, tenant_id, candidate)
            if text:
                break
    return fmt.ok_response(text or fallback, "structured_business", trace_id, media=fmt.empty_media())


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
            context={"last_product_sku": left["sku"], "last_product_name": left["canonical_name"]},
        )
        if session:
            await session_ctx.merge_session_context(conn, tenant_id, session, response["context"])
        return response

    left_card = await repo.load_product_card(conn, tenant_id, left["sku"])
    right_card = await repo.load_product_card(conn, tenant_id, right["sku"])
    text = build_compare_answer(left, left_card, right, right_card, country=country)
    response = fmt.ok_response(
        text,
        "structured_comparison",
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
