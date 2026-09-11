"""Tenant-scoped referral attribution and payment-reward operations."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.db import fetch_one, tenant_connection


REFERRAL_START_PREFIX = "ref_"
_INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class ReferralStartResult:
    status: Literal["attributed", "already_registered", "invalid", "self_referral"]
    inviter_actor_id: str | None = None


class BonusRedemptionError(ValueError):
    """A user-visible reason why bonus redemption cannot proceed."""


class BonusRedemptionForbiddenError(BonusRedemptionError):
    pass


class BonusRedemptionExpiredError(BonusRedemptionError):
    pass


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
    payload = raw or {}
    user = ((payload.get("message") or {}).get("from") or (payload.get("callback_query") or {}).get("from") or {})
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


async def ensure_telegram_actor(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    raw_update: dict[str, Any],
) -> str:
    """Register a bot user as an actor without assigning an inviter."""
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select actor_id from lead_actors
            where tenant_id = %s
              and (telegram_user_id = %s or telegram_chat_id = %s)
            limit 1
            """,
            (tenant_id, telegram_user_id, str(telegram_chat_id)),
        )
        if existing:
            return str(existing["actor_id"])
        actor_id = telegram_actor_id(tenant_id, telegram_user_id)
        created = await fetch_one(
            conn,
            """
            insert into lead_actors (
              actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id
            ) values (%s, %s, %s, %s, %s)
            on conflict (tenant_id, telegram_user_id) do nothing
            returning actor_id
            """,
            (
                actor_id,
                tenant_id,
                _display_name(raw_update, telegram_user_id),
                str(telegram_chat_id),
                telegram_user_id,
            ),
        )
        if created:
            return str(created["actor_id"])
        winner = await fetch_one(
            conn,
            """
            select actor_id from lead_actors
            where tenant_id = %s and telegram_user_id = %s
            limit 1
            """,
            (tenant_id, telegram_user_id),
        )
        if winner:
            return str(winner["actor_id"])
    raise RuntimeError("could not register Telegram actor")


async def referral_dashboard(
    tenant_id: str,
    *,
    actor_id: str,
    history_limit: int = 10,
) -> dict[str, Any]:
    """Read only the caller's referral totals, history and affordable plans."""
    async with tenant_connection(tenant_id) as conn:
        balance = await fetch_one(
            conn,
            """
            select coalesce(sum(amount_minor), 0) as amount_minor
            from partner_bonus_ledger
            where tenant_id = %s and actor_id = %s and currency = 'WUSD'
            """,
            (tenant_id, actor_id),
        )
        counts = await fetch_one(
            conn,
            """
            select
              count(*) as invited_count,
              count(*) filter (
                where exists (
                  select 1
                  from referral_profiles rp
                  join partner_subscriptions ps
                    on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
                  where rp.tenant_id = a.tenant_id
                    and rp.owner_id = a.invitee_actor_id
                    and partner_subscription_state(ps.paid_until, now()) in ('active', 'grace')
                )
              ) as paid_count
            from partner_referral_attributions a
            where a.tenant_id = %s and a.inviter_actor_id = %s
            """,
            (tenant_id, actor_id),
        )
        history = await fetch_one(
            conn,
            """
            select coalesce(json_agg(rows order by created_at desc), '[]'::json) as entries
            from (
              select entry_type, amount_minor, product_code, description, created_at
              from partner_bonus_ledger
              where tenant_id = %s and actor_id = %s
              order by created_at desc
              limit %s
            ) rows
            """,
            (tenant_id, actor_id, max(1, min(history_limit, 30))),
        )
        plans = await fetch_one(
            conn,
            """
            select coalesce(json_agg(rows order by access_months), '[]'::json) as items
            from (
              select plan_code, access_months, price_wusd_minor
              from partner_subscription_plans
              where tenant_id = %s and product_code = 'platform_subscription'
                and active = true and valid_from <= now()
                and (valid_until is null or valid_until > now())
            ) rows
            """,
            (tenant_id,),
        )
    amount_minor = int((balance or {}).get("amount_minor") or 0)
    return {
        "actor_id": actor_id,
        "balance_wusd_minor": amount_minor,
        "invited_count": int((counts or {}).get("invited_count") or 0),
        "paid_count": int((counts or {}).get("paid_count") or 0),
        "history": list((history or {}).get("entries") or []),
        "plans": list((plans or {}).get("items") or []),
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def create_bonus_redemption_intent(
    tenant_id: str,
    *,
    actor_id: str,
    plan_code: str,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    """Prepare a user-owned confirmation for full WUSD payment of one plan."""
    normalized_plan = str(plan_code or "").strip().lower()
    async with tenant_connection(tenant_id) as conn:
        profile = await fetch_one(
            conn,
            """
            select ref_code, public_profile
            from referral_profiles
            where tenant_id = %s and owner_id = %s and enabled = true
            order by ref_code
            limit 2
            """,
            (tenant_id, actor_id),
        )
        if not profile:
            raise BonusRedemptionError("Бонусами можно продлить только свой персональный сайт.")
        plan = await fetch_one(
            conn,
            """
            select plan_code, access_months, price_wusd_minor
            from partner_subscription_plans
            where tenant_id = %s and plan_code = %s and product_code = 'platform_subscription'
              and active = true and valid_from <= now()
              and (valid_until is null or valid_until > now())
            limit 1
            """,
            (tenant_id, normalized_plan),
        )
        if not plan:
            raise BonusRedemptionError("Этот тариф сейчас недоступен.")
        balance = await fetch_one(
            conn,
            """
            select coalesce(sum(amount_minor), 0) as amount_minor
            from partner_bonus_ledger
            where tenant_id = %s and actor_id = %s and currency = 'WUSD'
            """,
            (tenant_id, actor_id),
        )
        cost = int(plan["price_wusd_minor"])
        if int(balance["amount_minor"]) < cost:
            raise BonusRedemptionError("Недостаточно бонусов для этого тарифа.")
        await fetch_one(
            conn,
            """
            insert into partner_subscriptions (tenant_id, ref_code, paid_until)
            values (%s, %s, null)
            on conflict (tenant_id, ref_code) do nothing
            returning ref_code
            """,
            (tenant_id, profile["ref_code"]),
        )
        intent_id = str(uuid.uuid4())
        expires_at = _utc_now() + timedelta(minutes=10)
        intent = await fetch_one(
            conn,
            """
            insert into partner_bonus_redemption_intents (
              intent_id, tenant_id, actor_id, plan_code, ref_code, cost_wusd_minor,
              telegram_chat_id, telegram_user_id, expires_at
            ) values (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
            returning intent_id, plan_code, ref_code, cost_wusd_minor, expires_at
            """,
            (
                intent_id, tenant_id, actor_id, plan["plan_code"], profile["ref_code"], cost,
                telegram_chat_id, telegram_user_id, expires_at,
            ),
        )
    return {**intent, "access_months": int(plan["access_months"])}


async def confirm_bonus_redemption_intent(
    tenant_id: str,
    *,
    intent_id: str,
    actor_id: str,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    """Debit a full plan and extend the personal site atomically."""
    try:
        normalized_intent_id = str(uuid.UUID(str(intent_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BonusRedemptionForbiddenError("Подтверждение недействительно.") from exc
    async with tenant_connection(tenant_id) as conn:
        intent = await fetch_one(
            conn,
            """
            select intent_id, actor_id, plan_code, ref_code, cost_wusd_minor,
                   telegram_chat_id, telegram_user_id, expires_at, consumed_entry_id, cancelled_at
            from partner_bonus_redemption_intents
            where tenant_id = %s and intent_id = %s::uuid
            for update
            """,
            (tenant_id, normalized_intent_id),
        )
        if not intent or str(intent["actor_id"]) != actor_id or int(intent["telegram_chat_id"]) != telegram_chat_id or int(intent["telegram_user_id"]) != telegram_user_id:
            raise BonusRedemptionForbiddenError("Подтверждение недействительно.")
        if intent.get("cancelled_at") is not None:
            raise BonusRedemptionError("Это списание отменено.")
        if intent.get("consumed_entry_id") is not None:
            return {"entry_id": str(intent["consumed_entry_id"]), "idempotent": True}
        if intent["expires_at"] <= _utc_now():
            raise BonusRedemptionExpiredError("Подтверждение истекло. Выберите тариф ещё раз.")
        await fetch_one(conn, "select pg_advisory_xact_lock(hashtext(%s)) as locked", (f"bonus:{tenant_id}:{actor_id}",))
        plan = await fetch_one(
            conn,
            """
            select access_months
            from partner_subscription_plans
            where tenant_id = %s and plan_code = %s and product_code = 'platform_subscription'
              and active = true and valid_from <= now()
              and (valid_until is null or valid_until > now())
            limit 1
            """,
            (tenant_id, intent["plan_code"]),
        )
        if not plan:
            raise BonusRedemptionError("Этот тариф больше недоступен.")
        still_owner = await fetch_one(
            conn,
            """
            select ref_code from referral_profiles
            where tenant_id = %s and ref_code = %s and owner_id = %s and enabled = true
            limit 1
            """,
            (tenant_id, intent["ref_code"], actor_id),
        )
        if not still_owner:
            raise BonusRedemptionForbiddenError("Персональный сайт больше не связан с этим аккаунтом.")
        balance = await fetch_one(
            conn,
            """
            select coalesce(sum(amount_minor), 0) as amount_minor
            from partner_bonus_ledger
            where tenant_id = %s and actor_id = %s and currency = 'WUSD'
            """,
            (tenant_id, actor_id),
        )
        if int(balance["amount_minor"]) < int(intent["cost_wusd_minor"]):
            raise BonusRedemptionError("Бонусов уже недостаточно для этого тарифа.")
        subscription = await fetch_one(
            conn,
            """
            select paid_until from partner_subscriptions
            where tenant_id = %s and ref_code = %s
            for update
            """,
            (tenant_id, intent["ref_code"]),
        )
        if not subscription:
            raise BonusRedemptionError("Персональный сайт не найден.")
        from app.subscriptions.service import add_calendar_months, subscription_state

        now = _utc_now()
        previous = subscription.get("paid_until")
        period_start = previous if previous and subscription_state(previous, at=now) in {"active", "grace"} else now
        period_end = add_calendar_months(period_start, int(plan["access_months"]))
        entry = await fetch_one(
            conn,
            """
            insert into partner_bonus_ledger (
              tenant_id, actor_id, entry_type, amount_minor, currency, product_code,
              idempotency_key, rule_snapshot, description
            ) values (%s, %s, 'debit', %s, 'WUSD', 'platform_subscription', %s, %s::jsonb, %s)
            returning entry_id
            """,
            (
                tenant_id, actor_id, -int(intent["cost_wusd_minor"]),
                f"bonus_redemption:{normalized_intent_id}",
                json.dumps({"plan_code": intent["plan_code"], "access_months": int(plan["access_months"])}),
                f"Bonus redemption: {intent['plan_code']}",
            ),
        )
        await fetch_one(
            conn,
            """
            update partner_subscriptions set paid_until = %s, updated_at = now()
            where tenant_id = %s and ref_code = %s
            returning paid_until
            """,
            (period_end, tenant_id, intent["ref_code"]),
        )
        await fetch_one(
            conn,
            """
            update partner_bonus_redemption_intents set consumed_entry_id = %s
            where tenant_id = %s and intent_id = %s::uuid
            returning intent_id
            """,
            (entry["entry_id"], tenant_id, normalized_intent_id),
        )
    return {"entry_id": str(entry["entry_id"]), "paid_until": period_end, "idempotent": False}


async def cancel_bonus_redemption_intent(
    tenant_id: str,
    *,
    intent_id: str,
    actor_id: str,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> str:
    try:
        normalized_intent_id = str(uuid.UUID(str(intent_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BonusRedemptionForbiddenError("Подтверждение недействительно.") from exc
    async with tenant_connection(tenant_id) as conn:
        intent = await fetch_one(
            conn,
            """
            select actor_id, telegram_chat_id, telegram_user_id, consumed_entry_id, cancelled_at
            from partner_bonus_redemption_intents
            where tenant_id = %s and intent_id = %s::uuid
            for update
            """,
            (tenant_id, normalized_intent_id),
        )
        if not intent or str(intent["actor_id"]) != actor_id or int(intent["telegram_chat_id"]) != telegram_chat_id or int(intent["telegram_user_id"]) != telegram_user_id:
            raise BonusRedemptionForbiddenError("Подтверждение недействительно.")
        if intent.get("consumed_entry_id") is not None:
            return "confirmed"
        if intent.get("cancelled_at") is not None:
            return "cancelled"
        await fetch_one(
            conn,
            """
            update partner_bonus_redemption_intents set cancelled_at = now()
            where tenant_id = %s and intent_id = %s::uuid
            returning intent_id
            """,
            (tenant_id, normalized_intent_id),
        )
    return "cancelled"


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
