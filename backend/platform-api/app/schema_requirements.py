"""Which database tables the running code needs.

The 2026-09-12 outage and the ``user_memory_facts`` gap had the same shape: code
was a step ahead of the schema and the first real user found out. This module is
the single list a release checks *before* new containers take traffic
(``scripts/check_schema_compatibility.py``), and that the app logs at startup.

Optional features can be switched off with ``PLATFORM_DISABLED_FEATURES``
(comma-separated); a disabled feature's router is not mounted and its tables are
not required. Everything else is ``core`` and must exist.

``tests/test_schema_requirements.py`` scans the source for table names and fails
when code starts using a table this map does not know about.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Feature -> package(s) under app/ whose SQL belongs to it. Core is everything else.
OPTIONAL_FEATURE_PACKAGES: dict[str, tuple[str, ...]] = {
    "memory": ("memory",),
    "pilot": ("pilot",),
    "retention": ("retention",),
    "partner_library": ("partner_library",),
}

FEATURE_TABLES: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            # tenancy and transport
            "tenants",
            "tenant_bot_bindings",
            "tenant_domains",
            "tenant_entitlements",
            # identity and journey
            "identity_link_tokens",
            "telegram_identity_links",
            "telegram_consents",
            "visitor_sessions",
            "journey_attributions",
            "interaction_events",
            # leads and partners
            "lead_actors",
            "referral_profiles",
            "service_locations",
            "website_leads",
            "website_lead_owner_history",
            "website_lead_status_history",
            "website_lead_watchers",
            "website_events",
            "lead_delivery_attempts",
            # onboarding
            "onboarding_programs",
            "onboarding_steps",
            "onboarding_enrollments",
            "onboarding_progress",
            "onboarding_reminders",
            "mentor_escalations",
            # billing, referrals, partner requests
            "partner_subscriptions",
            "partner_subscription_plans",
            "partner_subscription_reminder_log",
            "partner_payment_intents",
            "partner_payment_ledger",
            "referral_invite_codes",
            "referral_reward_rules",
            "partner_referral_attributions",
            "partner_referral_attribution_audit",
            "partner_referral_admin_intents",
            "partner_bonus_ledger",
            "partner_bonus_redemption_intents",
            "partner_site_requests",
            "partner_renewal_requests",
            "partner_product_access",
            "partner_price_overrides",
            "partner_payments",
            # support tunnel (subscriber <-> service administrator via the bot)
            "support_tickets",
            "support_messages",
            "support_forums",
            # service sales (Gemini): tariff, sales, administrator deposit
            "service_tariffs",
            "service_sales",
            "service_admin_deposit",
            "service_notice_log",
            # advisor
            "platform_session_context",
            "advisor_structured_products",
            "advisor_structured_product_cards",
            "advisor_structured_product_details",
            "advisor_structured_product_comparisons",
            "advisor_structured_aliases",
            "advisor_structured_canonical_questions",
            "advisor_structured_clarification_prompts",
            "advisor_structured_capability_responses",
            "advisor_structured_business_faq",
            "advisor_structured_business_objections",
            "advisor_structured_resources",
            "advisor_product_recommendation_rules",
            "advisor_promotions",
            "advisor_starter_basket_templates",
            "advisor_whieda_community_resources",
            "advisor_whieda_events",
            # restored with the 2026-09-02..16 consolidation merge (SQL files now
            # in postgres/sql, tables present on the shared DB)
            "platform_admin_audit_log",
            "platform_admin_login_challenges",
            "platform_admin_principals",
            "platform_admin_sessions",
            "advisor_gap_review_items",
            "advisor_gap_review_mutations",
            "platform_cart_sessions",
            "platform_cart_snapshots",
            "content_access_challenges",
            "content_access_materials",
            "content_access_sessions",
            "wwc_markets",
            "wwc_markets_staging",
            "wwc_markets_sync_registry",
            "wwc_product_prices",
            "wwc_product_prices_staging",
            "wwc_ref_structures",
            "wwc_ref_structures_staging",
            "wwc_service_centers",
            "wwc_service_centers_staging",
            "wwc_service_center_coverage",
            "wwc_service_center_coverage_staging",
        }
    ),
    "memory": frozenset({"user_memory_facts"}),
    "pilot": frozenset({"pilot_daily_metrics", "pilot_outcome_events"}),
    "retention": frozenset({"data_export_requests", "data_retention_registry"}),
    "partner_library": frozenset({"partner_library_items"}),
}


def parse_disabled_features(raw: str | None) -> set[str]:
    """``"pilot, retention"`` -> {"pilot", "retention"}; unknown names are an error."""
    names = {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}
    unknown = names - set(OPTIONAL_FEATURE_PACKAGES)
    if unknown:
        raise ValueError(
            f"unknown feature(s) in PLATFORM_DISABLED_FEATURES: {sorted(unknown)}; "
            f"known: {sorted(OPTIONAL_FEATURE_PACKAGES)}"
        )
    return names


def feature_enabled(feature: str, disabled: Iterable[str]) -> bool:
    return feature not in set(disabled)


def required_tables(disabled: Iterable[str]) -> set[str]:
    off = set(disabled)
    required: set[str] = set()
    for feature, tables in FEATURE_TABLES.items():
        if feature == "core" or feature not in off:
            required |= tables
    return required


def missing_tables(existing: Iterable[str], disabled: Iterable[str]) -> list[str]:
    return sorted(required_tables(disabled) - {str(name).lower() for name in existing})


async def find_missing_tables(conn: Any, disabled: Iterable[str]) -> list[str]:
    """Compare the requirement list with ``pg_tables`` on an open async connection."""
    async with conn.cursor() as cur:
        await cur.execute("select tablename from pg_tables where schemaname = 'public'")
        rows = await cur.fetchall()
    existing = [row["tablename"] if isinstance(row, dict) else row[0] for row in rows]
    return missing_tables(existing, disabled)
