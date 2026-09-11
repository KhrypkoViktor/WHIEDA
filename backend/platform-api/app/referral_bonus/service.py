"""Tenant-scoped referral attribution and payment-reward operations."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from app.db import fetch_one, tenant_connection


REFERRAL_START_PREFIX = "ref_"
_INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class ReferralStartResult:
    status: Literal["attributed", "already_registered", "invalid", "self_referral"]
    inviter_actor_id: str | None = None


def parse_referral_start_token(token: str) -> str | None:
    """Return a safe opaque invite code, or None when this is not a referral token."""
    raw = str(token or "").strip()
    if not raw.lower().startswith(REFERRAL_START_PREFIX):
        return None
    code = raw[len(REFERRAL_START_PREFIX) :]
    return code if _INVITE_CODE_RE.fullmatch(code) else ""


def telegram_actor_id(tenant_id: str, telegram_user_id: int) -> str:
    """Stable actor key for a bot user without a personal site."""
    return f"telegram:{tenant_id}:{int(telegram_user_id)}"


def _display_name(raw: dict[str, Any], telegram_user_id: int) -> str:
    user = ((raw or {}).get("message") or {}).get("from") or {}
    username = str(user.get("username") or "").strip()
    if username:
        return f"@{username}"[:240]
    full_name = " ".join(
        part.strip()
        for part in (str(user.get("first_name") or ""), str(user.get("last_name") or ""))
        if part.strip()
    )
    return (full_name or f"Telegram user {telegram_user_id}")[:240]


async def get_or_create_invite_code(tenant_id: str, inviter_actor_id: str) -> str:
    """Return one active opaque code for the actor, creating it race-safely."""
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select invite_code
            from referral_invite_codes
            where tenant_id = %s and inviter_actor_id = %s and active = true
            limit 1
            """,
            (tenant_id, inviter_actor_id),
        )
        if existing:
            return str(existing["invite_code"])

        for _ in range(4):
            code = secrets.token_urlsafe(12)
            created = await fetch_one(
                conn,
                """
                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values (%s, %s, %s)
                on conflict do nothing
                returning invite_code
                """,
                (tenant_id, code, inviter_actor_id),
            )
            if created:
                return str(created["invite_code"])
            existing = await fetch_one(
                conn,
                """
                select invite_code
                from referral_invite_codes
                where tenant_id = %s and inviter_actor_id = %s and active = true
                limit 1
                """,
                (tenant_id, inviter_actor_id),
            )
            if existing:
                return str(existing["invite_code"])
    raise RuntimeError("could not create referral invite code")


async def accept_referral_start(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    invite_code: str,
    raw_update: dict[str, Any],
) -> ReferralStartResult:
    """Fix first-touch only for a previously unseen Telegram user."""
    if not _INVITE_CODE_RE.fullmatch(str(invite_code or "")):
        return ReferralStartResult(status="invalid")

    async with tenant_connection(tenant_id) as conn:
        invite = await fetch_one(
            conn,
            """
            select invite_code, inviter_actor_id
            from referral_invite_codes
            where tenant_id = %s and invite_code = %s and active = true
            limit 1
            """,
            (tenant_id, invite_code),
        )
        if not invite:
            return ReferralStartResult(status="invalid")

        actor = await fetch_one(
            conn,
            """
            select actor_id
            from lead_actors
            where tenant_id = %s
              and (telegram_user_id = %s or telegram_chat_id = %s)
            limit 1
            for update
            """,
            (tenant_id, telegram_user_id, str(telegram_chat_id)),
        )
        if actor:
            return ReferralStartResult(status="already_registered")

        inviter_actor_id = str(invite["inviter_actor_id"])
        invitee_actor_id = telegram_actor_id(tenant_id, telegram_user_id)
        if inviter_actor_id == invitee_actor_id:
            return ReferralStartResult(status="self_referral")

        # A normal bot message must never create attribution by accident.
        created_actor = await fetch_one(
            conn,
            """
            insert into lead_actors (
              actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id
            ) values (%s, %s, %s, %s, %s)
            on conflict (tenant_id, telegram_user_id) do nothing
            returning actor_id
            """,
            (
                invitee_actor_id,
                tenant_id,
                _display_name(raw_update, telegram_user_id),
                str(telegram_chat_id),
                telegram_user_id,
            ),
        )
        if not created_actor:
            return ReferralStartResult(status="already_registered")

        attribution = await fetch_one(
            conn,
            """
            insert into partner_referral_attributions (
              tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source
            ) values (%s, %s, %s, %s, 'telegram_deeplink')
            on conflict (tenant_id, invitee_actor_id) do nothing
            returning invitee_actor_id
            """,
            (tenant_id, invitee_actor_id, inviter_actor_id, invite_code),
        )
        if not attribution:
            return ReferralStartResult(status="already_registered")
        await fetch_one(
            conn,
            """
            insert into partner_referral_attribution_audit (
              tenant_id, invitee_actor_id, new_inviter_actor_id, action, reason
            ) values (%s, %s, %s, 'created', 'telegram_deeplink')
            returning audit_id
            """,
            (tenant_id, invitee_actor_id, inviter_actor_id),
        )
        return ReferralStartResult(status="attributed", inviter_actor_id=inviter_actor_id)


async def award_referral_bonus_for_payment(
    conn: Any,
    *,
    tenant_id: str,
    payment: dict[str, Any],
) -> dict[str, Any] | None:
    """Create exactly one WUSD credit for an eligible cash subscription payment."""
    product_code = str(payment.get("product_code") or "platform_subscription")
    payment_id = str(payment["payment_id"])
    owner = await fetch_one(
        conn,
        """
        select owner_id from referral_profiles
        where tenant_id = %s and ref_code = %s
        limit 1
        """,
        (tenant_id, payment["ref_code"]),
    )
    if not owner:
        return None
    attribution = await fetch_one(
        conn,
        """
        select inviter_actor_id from partner_referral_attributions
        where tenant_id = %s and invitee_actor_id = %s
        limit 1
        """,
        (tenant_id, owner["owner_id"]),
    )
    if not attribution:
        return None

    rule = await fetch_one(
        conn,
        """
        select rule_id, first_payment_bps, renewal_payment_bps, reward_currency
        from referral_reward_rules
        where tenant_id = %s and product_code = %s and active = true
          and valid_from <= now() and (valid_until is null or valid_until > now())
        order by valid_from desc limit 1
        """,
        (tenant_id, product_code),
    )
    if not rule:
        return None
    plan = await fetch_one(
        conn,
        """
        select plan_code, price_wusd_minor from partner_subscription_plans
        where tenant_id = %s and product_code = %s and access_months = %s and active = true
          and valid_from <= now() and (valid_until is null or valid_until > now())
        order by valid_from desc limit 1
        """,
        (tenant_id, product_code, int(payment["access_months"])),
    )
    if not plan:
        return None
    previous = await fetch_one(
        conn,
        """
        select count(*) as payment_count from partner_payment_ledger
        where tenant_id = %s and ref_code = %s and product_code = %s
          and payment_id <> %s::uuid
        """,
        (tenant_id, payment["ref_code"], product_code, payment_id),
    )
    is_first_payment = int(previous["payment_count"]) == 0
    bps = int(rule["first_payment_bps"] if is_first_payment else rule["renewal_payment_bps"])
    reward_minor = int(plan["price_wusd_minor"]) * bps // 10_000
    if reward_minor <= 0:
        return None
    snapshot = json.dumps({
        "rule_id": str(rule["rule_id"]), "basis_points": bps,
        "base_wusd_minor": int(plan["price_wusd_minor"]), "plan_code": str(plan["plan_code"]),
        "payment_kind": "first" if is_first_payment else "renewal",
    })
    idempotency_key = f"payment:{payment_id}:referral_credit"
    inserted = await fetch_one(
        conn,
        """
        insert into partner_bonus_ledger (
          tenant_id, actor_id, entry_type, amount_minor, currency, product_code,
          source_payment_id, idempotency_key, rule_snapshot, description
        ) values (%s, %s, 'credit', %s, 'WUSD', %s, %s::uuid, %s, %s::jsonb, %s)
        on conflict do nothing
        returning entry_id, actor_id, amount_minor, currency, created_at
        """,
        (
            tenant_id, attribution["inviter_actor_id"], reward_minor, product_code,
            payment_id, idempotency_key, snapshot,
            "Referral bonus: first payment" if is_first_payment else "Referral bonus: renewal",
        ),
    )
    kind = "first" if is_first_payment else "renewal"
    if inserted:
        return {**inserted, "idempotent": False, "payment_kind": kind}
    existing = await fetch_one(
        conn,
        """
        select entry_id, actor_id, amount_minor, currency, created_at
        from partner_bonus_ledger
        where tenant_id = %s and idempotency_key = %s
        limit 1
        """,
        (tenant_id, idempotency_key),
    )
    return {**existing, "idempotent": True, "payment_kind": kind} if existing else None
