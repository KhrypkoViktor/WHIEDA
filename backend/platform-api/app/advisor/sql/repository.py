"""Tenant-scoped SQL reads for structured advisor (no full-catalog snapshot)."""

from __future__ import annotations

from typing import Any

from app.db import fetch_all, fetch_one


def client_id(tenant_id: str) -> str:
    return tenant_id


async def load_capability_response(
    conn,
    tenant_id: str,
    intent_id: str,
) -> str | None:
    row = await fetch_one(
        conn,
        """
        select answer_text
        from advisor_structured_capability_responses
        where client_id = %s
          and intent_id = %s
          and enabled is true
        order by response_id asc
        limit 1
        """,
        (client_id(tenant_id), intent_id),
    )
    if not row:
        return None
    return str(row.get("answer_text") or "").replace("⏎", "\n").strip() or None


async def load_clarification_prompt(
    conn,
    tenant_id: str,
    key: str,
) -> str | None:
    row = await fetch_one(
        conn,
        """
        select prompt_text
        from advisor_structured_clarification_prompts
        where client_id = %s
          and clarification_key = %s
          and enabled is true
        limit 1
        """,
        (client_id(tenant_id), key),
    )
    if not row:
        return None
    return str(row.get("prompt_text") or "").strip() or None


async def resolve_product_by_exact_alias(
    conn,
    tenant_id: str,
    alias: str,
) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select p.sku, p.canonical_name, p.retail_price_byn, p.retail_price_rub,
               p.partner_price_byn, p.partner_w
        from advisor_structured_aliases a
        join advisor_structured_products p
          on p.client_id = a.client_id and p.sku = a.canonical_sku
        where a.client_id = %s
          and a.active is true
          and lower(a.alias) = %s
        order by a.priority desc
        limit 1
        """,
        (client_id(tenant_id), alias.lower()),
    )


async def resolve_activator_pro_product(conn, tenant_id: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select sku, canonical_name, retail_price_byn, retail_price_rub,
               partner_price_byn, partner_w
        from advisor_structured_products
        where client_id = %s
          and lower(canonical_name) like %s
        order by length(canonical_name) asc
        limit 1
        """,
        (client_id(tenant_id), "%активатор%pro%"),
    )


async def fetch_alias_candidates(conn, tenant_id: str, question: str) -> list[dict[str, Any]]:
    normalized = question.lower().strip()
    compact = normalized.replace(" ", "")
    return await fetch_all(
        conn,
        """
        select p.sku, p.canonical_name, p.retail_price_byn, p.retail_price_rub,
               p.partner_price_byn, p.partner_w, a.alias, a.priority, a.match_type
        from advisor_structured_aliases a
        join advisor_structured_products p
          on p.client_id = a.client_id and p.sku = a.canonical_sku
        where a.client_id = %s
          and a.active is true
          and (
            lower(a.alias) = %s
            or %s like ('%%' || lower(a.alias) || '%%')
            or %s like ('%%' || lower(a.alias) || '%%')
            or lower(p.canonical_name) like %s
          )
        order by a.priority desc, length(a.alias) desc
        limit 40
        """,
        (client_id(tenant_id), normalized, normalized, compact, f"%{normalized}%"),
    )


async def resolve_product_by_partial_alias(
    conn,
    tenant_id: str,
    question: str,
) -> dict[str, Any] | None:
    normalized = question.lower().strip()
    rows = await fetch_all(
        conn,
        """
        select p.sku, p.canonical_name, p.retail_price_byn, p.retail_price_rub,
               p.partner_price_byn, p.partner_w, a.alias, a.priority
        from advisor_structured_aliases a
        join advisor_structured_products p
          on p.client_id = a.client_id and p.sku = a.canonical_sku
        where a.client_id = %s
          and a.active is true
          and (
            lower(a.alias) = %s
            or %s like ('%%' || lower(a.alias) || '%%')
            or lower(p.canonical_name) like %s
          )
        order by a.priority desc, length(a.alias) desc
        limit 5
        """,
        (client_id(tenant_id), normalized, normalized, f"%{normalized}%"),
    )
    return rows[0] if rows else None


async def load_product_card(conn, tenant_id: str, sku: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select sku, canonical_name, short_name, what_it_is, who_asks_about_it,
               common_use_cases, how_to_use_short, what_to_expect_soft,
               contraindications_short, primary_image_url
        from advisor_structured_product_cards
        where client_id = %s and sku = %s
        limit 1
        """,
        (client_id(tenant_id), sku),
    )


FAQ_GENERIC_TOKENS = frozenset(
    {
        "что",
        "такое",
        "это",
        "значит",
        "объясни",
        "расскажи",
        "про",
        "какой",
        "какая",
        "какие",
        "как",
        "для",
        "или",
    }
)


def _faq_substantive_tokens(alias: str) -> list[str]:
    return [token for token in alias.split() if len(token) >= 3 and token not in FAQ_GENERIC_TOKENS]


async def find_business_faq(conn, tenant_id: str, question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    rows = await fetch_all(
        conn,
        """
        select faq_id, title, answer_text, aliases, priority
        from advisor_structured_business_faq
        where client_id = %s and active is true
        order by priority desc nulls last
        limit 200
        """,
        (client_id(tenant_id),),
    )
    best: tuple[int, dict[str, Any]] | None = None
    keyword_hints = (
        ("повторк", "повтор"),
        ("бинарн", "бинар"),
        ("кэшбэк", "кэшбэк"),
    )
    for hint, title_needle in keyword_hints:
        if hint in normalized:
            for row in rows:
                blob = f"{row.get('title', '')} {row.get('aliases', '')}".lower()
                if title_needle in blob:
                    score = 90 + int(row.get("priority") or 0)
                    if not best or score > best[0]:
                        best = (score, row)
    for row in rows:
        title = str(row.get("title") or "").lower()
        if "pv" in normalized and "pv" in title and any(
            token in normalized for token in ("что", "какой", "объясни", "значит")
        ):
            score = 80 + int(row.get("priority") or 0)
            if not best or score > best[0]:
                best = (score, row)
            continue
        alias_blob = f"{title} {row.get('aliases', '')}".lower()
        for alias in [part.strip() for part in alias_blob.split("|") if part.strip()]:
            if len(alias) < 4:
                continue
            substantive = _faq_substantive_tokens(alias)
            matched = False
            if alias in normalized:
                matched = True
            elif substantive and all(token in normalized for token in substantive):
                matched = True
            if not matched:
                continue
            score = len(alias) + int(row.get("priority") or 0)
            if alias in normalized:
                score += 20
            if not best or score > best[0]:
                best = (score, row)
    return best[1] if best else None


async def load_recommendation_catalog(conn, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select p.sku, p.canonical_name, p.retail_price_byn, p.partner_price_byn,
               p.partner_w as partner_points,
               r.business_priority, r.universality_score, r.demo_score, r.gift_score,
               r.popularity_score, r.resale_score, r.personal_use_score, r.reason_short
        from advisor_structured_products p
        join advisor_product_recommendation_rules r
          on r.client_id = p.client_id and r.product_id = p.sku
        where p.client_id = %s
          and lower(coalesce(r.status, 'draft')) not in ('paused', 'disabled', 'archived')
          and coalesce(r.registration_enabled, true) is true
          and lower(coalesce(r.availability_status, 'available')) = 'available'
        order by r.business_priority desc nulls last, r.universality_score desc nulls last
        limit 120
        """,
        (client_id(tenant_id),),
    )


async def load_starter_basket_templates(conn, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select template_id, title, goal, preferred_product_ids, excluded_product_ids, priority
        from advisor_starter_basket_templates
        where client_id = %s
          and lower(coalesce(status, 'draft')) not in ('paused', 'disabled', 'archived')
        order by priority desc nulls last
        limit 50
        """,
        (client_id(tenant_id),),
    )


async def load_product_resources(
    conn,
    tenant_id: str,
    sku: str,
    resource_type: str | None = None,
) -> list[dict[str, Any]]:
    if resource_type:
        return await fetch_all(
            conn,
            """
            select resource_id, sku, canonical_name, resource_type, topic, title, url, priority
            from advisor_structured_resources
            where client_id = %s and sku = %s and active is true
              and lower(resource_type) = %s
            order by priority desc nulls last
            """,
            (client_id(tenant_id), sku, resource_type.lower()),
        )
    return await fetch_all(
        conn,
        """
        select resource_id, sku, canonical_name, resource_type, topic, title, url, priority
        from advisor_structured_resources
        where client_id = %s and sku = %s and active is true
        order by priority desc nulls last
        """,
        (client_id(tenant_id), sku),
    )


async def find_product_comparison(
    conn,
    tenant_id: str,
    left_sku: str,
    right_sku: str,
) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select comparison_id, title, answer_text, left_sku, right_sku
        from advisor_structured_product_comparisons
        where client_id = %s and active is true
          and (
            (left_sku = %s and right_sku = %s)
            or (left_sku = %s and right_sku = %s)
          )
        order by priority desc nulls last
        limit 1
        """,
        (client_id(tenant_id), left_sku, right_sku, right_sku, left_sku),
    )


async def resolve_product_by_sku(conn, tenant_id: str, sku: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select sku, canonical_name, retail_price_byn, retail_price_rub,
               partner_price_byn, partner_w
        from advisor_structured_products
        where client_id = %s and sku = %s
        limit 1
        """,
        (client_id(tenant_id), sku),
    )


async def resolve_product_by_slug(conn, tenant_id: str, slug: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select sku, canonical_name, retail_price_byn, retail_price_rub,
               partner_price_byn, partner_w
        from advisor_structured_products
        where client_id = %s and lower(canonical_name) like %s
        limit 1
        """,
        (client_id(tenant_id), f"%{slug.replace('-', ' ')}%"),
    )


async def load_active_promotions(conn, tenant_id: str, country: str = "BY") -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select promotion_id, title, short_text, full_text, benefit_text, product_ids,
               priority, ends_at, source_url
        from advisor_promotions
        where client_id = %s
          and lower(coalesce(tenant_id, 'by')) = 'by'
          and status = 'active'
          and starts_at <= now()
          and ends_at >= now()
          and (country is null or upper(country) = %s or country = '')
        order by priority desc nulls last
        limit 10
        """,
        (client_id(tenant_id), country.upper()),
    )


async def load_upcoming_events(conn, tenant_id: str, country: str = "BY") -> list[dict[str, Any]]:
    _ = country
    return await fetch_all(
        conn,
        """
        select event_id, title, description, starts_at, ends_at, city, address,
               online_url, status, contact, timezone, recurrence_rule
        from advisor_whieda_events
        where client_id = %s
          and lower(coalesce(tenant_id, 'by')) = 'by'
          and lower(status) in ('active', 'confirmed', 'published')
        order by starts_at asc
        limit 20
        """,
        (client_id(tenant_id),),
    )


async def load_community_resources(conn, tenant_id: str, country: str = "BY") -> list[dict[str, Any]]:
    _ = country
    return await fetch_all(
        conn,
        """
        select resource_id, title, description, url, platform, category, priority
        from advisor_whieda_community_resources
        where client_id = %s
          and lower(coalesce(tenant_id, 'by')) = 'by'
          and lower(status) in ('active', 'confirmed', 'published')
        order by priority desc nulls last
        limit 5
        """,
        (client_id(tenant_id),),
    )


async def load_product_detail(
    conn,
    tenant_id: str,
    sku: str,
    question: str,
) -> dict[str, Any] | None:
    normalized = question.lower()
    rows = await fetch_all(
        conn,
        """
        select detail_id, topic, title, answer_text, priority
        from advisor_structured_product_details
        where client_id = %s and sku = %s and active is true
        order by priority desc nulls last
        limit 50
        """,
        (client_id(tenant_id), sku),
    )
    best: tuple[int, dict[str, Any]] | None = None
    for row in rows:
        blob = f"{row.get('title', '')} {row.get('topic', '')}".lower()
        if blob and blob in normalized:
            score = len(blob) + int(row.get("priority") or 0)
            if not best or score > best[0]:
                best = (score, row)
    if best:
        return best[1]
    return rows[0] if rows else None


async def load_coach_objections(conn, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select objection_id, title, first_reply, clarify, next_step, do_not_say, priority
        from advisor_structured_business_objections
        where client_id = %s and active is true
        order by objection_id
        limit 50
        """,
        (client_id(tenant_id),),
    )


async def find_canonical_question(conn, tenant_id: str, question: str) -> dict[str, Any] | None:
    normalized = question.lower().strip()
    if len(normalized) < 4:
        return None
    rows = await fetch_all(
        conn,
        """
        select question_id, intent_id, entity_id, canonical_question, real_examples, answer_key, frequency
        from advisor_structured_canonical_questions
        where client_id = %s
          and lower(coalesce(status, 'approved')) not in ('paused', 'disabled', 'archived', 'rejected')
        order by frequency desc nulls last
        limit 300
        """,
        (client_id(tenant_id),),
    )
    best: tuple[int, dict[str, Any]] | None = None
    for row in rows:
        canonical = str(row.get("canonical_question") or "").lower().strip()
        examples = [
            part.strip().lower()
            for part in str(row.get("real_examples") or "").split("|")
            if part.strip()
        ]
        candidates = [canonical, *examples]
        for candidate in candidates:
            if len(candidate) < 4:
                continue
            if candidate == normalized or candidate in normalized or normalized in candidate:
                score = len(candidate) + int(row.get("frequency") or 0)
                if candidate == normalized:
                    score += 40
                if not best or score > best[0]:
                    best = (score, row)
    return best[1] if best else None


async def find_business_objection(conn, tenant_id: str, question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    rows = await fetch_all(
        conn,
        """
        select objection_id, aliases, first_reply, priority
        from advisor_structured_business_objections
        where client_id = %s and active is true
        order by priority desc nulls last
        limit 200
        """,
        (client_id(tenant_id),),
    )
    best: tuple[int, dict[str, Any]] | None = None
    for row in rows:
        for alias in [part.strip().lower() for part in str(row.get("aliases") or "").split("|") if part.strip()]:
            if len(alias) < 4 or alias not in normalized:
                continue
            score = len(alias) + int(row.get("priority") or 0)
            if not best or score > best[0]:
                best = (score, row)
    return best[1] if best else None
