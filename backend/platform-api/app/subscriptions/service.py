from __future__ import annotations

import calendar
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.db import fetch_all, fetch_one, tenant_connection
from app.theme_access.service import REF_TO_ISSUED_SUBDOMAIN

SubscriptionState = Literal["no_subscription", "active", "grace", "suspended"]

ACCESS_MONTHS = 3
GRACE_PERIOD = timedelta(days=3)
PAYMENT_INTENT_TTL = timedelta(minutes=10)
PARTNER_DOMAIN = "wwc.best"
RESERVED_SUBDOMAINS = frozenset({"", "www", "dev", "staging", "admin"})
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class SubscriptionError(ValueError):
    """Base error for a rejected subscription operation."""


class PartnerNotFoundError(SubscriptionError):
    pass


class PaymentIdempotencyConflictError(SubscriptionError):
    pass


class PartnerIdentityAmbiguousError(SubscriptionError):
    pass


class PartnerHostCollisionError(SubscriptionError):
    pass


class ReservedPartnerHostError(SubscriptionError):
    pass


class SeedManifestMismatchError(SubscriptionError):
    pass


class PaymentIntentNotFoundError(SubscriptionError):
    pass


class PaymentIntentForbiddenError(SubscriptionError):
    pass


class PaymentIntentExpiredError(SubscriptionError):
    pass


class PaymentIntentCancelledError(SubscriptionError):
    pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def subscription_state(
    paid_until: datetime | None,
    *,
    at: datetime | None = None,
) -> SubscriptionState:
    if paid_until is None:
        return "no_subscription"
    current = _as_utc(at or datetime.now(timezone.utc))
    boundary = _as_utc(paid_until)
    if current < boundary:
        return "active"
    if current < boundary + GRACE_PERIOD:
        return "grace"
    return "suspended"


def add_calendar_months(value: datetime, months: int = ACCESS_MONTHS) -> datetime:
    if months <= 0:
        raise ValueError("months must be positive")
    source = _as_utc(value)
    month_index = source.month - 1 + months
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return source.replace(year=year, month=month, day=day)


def paid_access_allowed(paid_until: datetime | None, *, at: datetime | None = None) -> bool:
    return subscription_state(paid_until, at=at) in {"active", "grace"}


def _normalize_subscription(row: dict[str, Any] | None, *, at: datetime) -> dict[str, Any] | None:
    if not row:
        return None
    paid_until = row.get("paid_until")
    state = subscription_state(paid_until, at=at)
    return {
        **row,
        "subscription_status": state,
        "grace_until": paid_until + GRACE_PERIOD if paid_until else None,
        "partner_paid": state in {"active", "grace"},
    }


def _validate_payment_input(
    *,
    amount_minor: int,
    currency: str,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_user_id: int,
) -> str:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise SubscriptionError("amount_minor must be a positive integer")
    normalized_currency = str(currency or "").strip().upper()
    if normalized_currency not in {"RUB", "BYN"}:
        raise SubscriptionError("currency must be RUB or BYN")
    for name, value in (
        ("telegram_chat_id", telegram_chat_id),
        ("telegram_message_id", telegram_message_id),
        ("telegram_user_id", telegram_user_id),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SubscriptionError(f"{name} must be an integer")
    return normalized_currency


async def get_subscription(
    tenant_id: str,
    ref_code: str,
    *,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    current = _as_utc(at or datetime.now(timezone.utc))
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select tenant_id, ref_code, paid_until, created_at, updated_at
            from partner_subscriptions
            where tenant_id = %s and ref_code = %s
            limit 1
            """,
            (tenant_id, ref_code),
        )
    return _normalize_subscription(row, at=current)


async def get_subscription_state(
    tenant_id: str,
    ref_code: str,
    *,
    at: datetime | None = None,
) -> SubscriptionState:
    row = await get_subscription(tenant_id, ref_code, at=at)
    return row["subscription_status"] if row else "no_subscription"


async def is_paid_access_allowed(
    tenant_id: str,
    ref_code: str,
    *,
    at: datetime | None = None,
) -> bool:
    return (await get_subscription_state(tenant_id, ref_code, at=at)) in {"active", "grace"}


async def record_manual_payment(
    tenant_id: str,
    *,
    ref_code: str,
    amount_minor: int,
    currency: str,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    normalized_currency = _validate_payment_input(
        amount_minor=amount_minor,
        currency=currency,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_user_id=telegram_user_id,
    )
    normalized_ref = str(ref_code or "").strip().lower()
    if not normalized_ref:
        raise PartnerNotFoundError("ref_code required")

    async with tenant_connection(tenant_id) as conn:
        return await _record_manual_payment_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=normalized_ref,
            amount_minor=amount_minor,
            currency=normalized_currency,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            telegram_user_id=telegram_user_id,
        )


async def _record_manual_payment_in_connection(
    conn: Any,
    *,
    tenant_id: str,
    ref_code: str,
    amount_minor: int,
    currency: str,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into partner_subscriptions (tenant_id, ref_code, paid_until)
            select rp.tenant_id, rp.ref_code, null
            from referral_profiles rp
            where rp.tenant_id = %s
              and rp.ref_code = %s
              and rp.enabled = true
            on conflict (tenant_id, ref_code) do nothing
            """,
            (tenant_id, ref_code),
        )

    locked = await fetch_one(
        conn,
        """
        select ps.tenant_id, ps.ref_code, ps.paid_until
        from partner_subscriptions ps
        join referral_profiles rp
          on rp.ref_code = ps.ref_code
         and rp.tenant_id = ps.tenant_id
         and rp.enabled = true
        where ps.tenant_id = %s and ps.ref_code = %s
        for update of ps
        """,
        (tenant_id, ref_code),
    )
    if not locked:
        raise PartnerNotFoundError("active referral profile not found")

    existing = await fetch_one(
        conn,
        """
        select payment_id, tenant_id, ref_code, amount_minor, currency,
               period_start, period_end, previous_paid_until, access_months,
               telegram_user_id, created_at
        from partner_payment_ledger
        where tenant_id = %s
          and source = 'telegram_manual'
          and telegram_chat_id = %s
          and telegram_message_id = %s
        limit 1
        """,
        (tenant_id, telegram_chat_id, telegram_message_id),
    )
    if existing:
        same_input = (
            existing["ref_code"] == ref_code
            and int(existing["amount_minor"]) == amount_minor
            and existing["currency"] == currency
            and int(existing["telegram_user_id"]) == telegram_user_id
        )
        if not same_input:
            raise PaymentIdempotencyConflictError(
                "telegram message already records a different payment"
            )
        return {**existing, "paid_until": locked["paid_until"], "idempotent": True}

    clock = await fetch_one(conn, "select now() as current_time")
    current = _as_utc(clock["current_time"])
    previous_paid_until = locked.get("paid_until")
    current_state = subscription_state(previous_paid_until, at=current)
    period_start = (
        _as_utc(previous_paid_until)
        if previous_paid_until is not None and current_state in {"active", "grace"}
        else current
    )
    period_end = add_calendar_months(period_start)
    payment_id = str(uuid.uuid4())

    updated = await fetch_one(
        conn,
        """
        update partner_subscriptions
        set paid_until = %s, updated_at = now()
        where tenant_id = %s and ref_code = %s
        returning paid_until, updated_at
        """,
        (period_end, tenant_id, ref_code),
    )
    ledger = await fetch_one(
        conn,
        """
        insert into partner_payment_ledger (
          payment_id, tenant_id, ref_code, amount_minor, currency,
          access_months, period_start, period_end, previous_paid_until,
          source, telegram_chat_id, telegram_message_id, telegram_user_id
        ) values (
          %s::uuid, %s, %s, %s, %s,
          3, %s, %s, %s,
          'telegram_manual', %s, %s, %s
        )
        returning payment_id, tenant_id, ref_code, amount_minor, currency,
                  period_start, period_end, previous_paid_until, access_months,
                  telegram_user_id, created_at
        """,
        (
            payment_id,
            tenant_id,
            ref_code,
            amount_minor,
            currency,
            period_start,
            period_end,
            previous_paid_until,
            telegram_chat_id,
            telegram_message_id,
            telegram_user_id,
        ),
    )
    return {**ledger, "paid_until": updated["paid_until"], "idempotent": False}


def _billing_identifier(identifier: str) -> tuple[str, str]:
    value = str(identifier or "").strip()
    if value.startswith("@") and len(value) > 1:
        return "username", value[1:].lower()
    if value.lower().startswith("ref:") and len(value) > 4:
        return "ref", value[4:].strip().lower()
    raise SubscriptionError("partner identifier must be @username or ref:code")


async def resolve_partner_for_billing(tenant_id: str, identifier: str) -> dict[str, Any]:
    kind, value = _billing_identifier(identifier)
    if kind == "username":
        condition = "lower(coalesce(la.telegram_username, '')) = %s"
    else:
        condition = "rp.ref_code = %s"
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select rp.tenant_id, rp.ref_code, rp.public_profile,
                   la.actor_id, la.display_name, la.telegram_username,
                   ps.paid_until
            from referral_profiles rp
            join lead_actors la
              on la.actor_id = rp.owner_id
             and la.tenant_id = rp.tenant_id
             and la.active = true
            left join partner_subscriptions ps
              on ps.tenant_id = rp.tenant_id
             and ps.ref_code = rp.ref_code
            where rp.tenant_id = %s
              and rp.enabled = true
              and {condition}
            order by rp.ref_code
            limit 3
            """,
            (tenant_id, value),
        )
    if not rows:
        raise PartnerNotFoundError("active referral profile not found")
    if len(rows) != 1:
        raise PartnerIdentityAmbiguousError("partner identifier is ambiguous")
    row = rows[0]
    return {
        **row,
        "hostname": resolve_partner_hostname(row["ref_code"], row.get("public_profile")),
    }


async def create_payment_intent(
    tenant_id: str,
    *,
    identifier: str,
    amount_minor: int,
    currency: str,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    normalized_currency = _validate_payment_input(
        amount_minor=amount_minor,
        currency=currency,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_user_id=telegram_user_id,
    )
    partner = await resolve_partner_for_billing(tenant_id, identifier)
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                values (%s, %s, %s)
                on conflict (tenant_id, ref_code) do nothing
                """,
                (tenant_id, partner["ref_code"], partner.get("paid_until")),
            )
        clock = await fetch_one(conn, "select now() as current_time")
        current = _as_utc(clock["current_time"])
        intent_id = str(uuid.uuid4())
        await fetch_one(
            conn,
            """
            insert into partner_payment_intents (
              intent_id, tenant_id, ref_code, amount_minor, currency,
              telegram_chat_id, telegram_message_id, telegram_user_id, expires_at
            ) values (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, telegram_chat_id, telegram_message_id) do nothing
            returning intent_id
            """,
            (
                intent_id,
                tenant_id,
                partner["ref_code"],
                amount_minor,
                normalized_currency,
                telegram_chat_id,
                telegram_message_id,
                telegram_user_id,
                current + PAYMENT_INTENT_TTL,
            ),
        )
        intent = await fetch_one(
            conn,
            """
            select intent_id, tenant_id, ref_code, amount_minor, currency,
                   telegram_chat_id, telegram_message_id, telegram_user_id,
                   expires_at, consumed_payment_id, cancelled_at, created_at
            from partner_payment_intents
            where tenant_id = %s and telegram_chat_id = %s and telegram_message_id = %s
            """,
            (tenant_id, telegram_chat_id, telegram_message_id),
        )
    if not intent:
        raise SubscriptionError("payment intent was not created")
    same_input = (
        intent["ref_code"] == partner["ref_code"]
        and int(intent["amount_minor"]) == amount_minor
        and intent["currency"] == normalized_currency
        and int(intent["telegram_user_id"]) == telegram_user_id
    )
    if not same_input:
        raise PaymentIdempotencyConflictError(
            "telegram message already contains a different payment intent"
        )
    paid_until = partner.get("paid_until")
    state = subscription_state(paid_until, at=current)
    period_start = (
        _as_utc(paid_until)
        if paid_until is not None and state in {"active", "grace"}
        else current
    )
    return {
        **intent,
        **partner,
        "period_start": period_start,
        "period_end": add_calendar_months(period_start),
        "grace_until": add_calendar_months(period_start) + GRACE_PERIOD,
    }


async def confirm_payment_intent(
    tenant_id: str,
    *,
    intent_id: str,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> dict[str, Any]:
    try:
        normalized_intent_id = str(uuid.UUID(str(intent_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentIntentNotFoundError("payment intent not found") from exc

    async with tenant_connection(tenant_id) as conn:
        intent = await fetch_one(
            conn,
            """
            select intent_id, ref_code, amount_minor, currency,
                   telegram_chat_id, telegram_message_id, telegram_user_id,
                   expires_at, consumed_payment_id, cancelled_at
            from partner_payment_intents
            where tenant_id = %s and intent_id = %s::uuid
            for update
            """,
            (tenant_id, normalized_intent_id),
        )
        if not intent:
            raise PaymentIntentNotFoundError("payment intent not found")
        if (
            int(intent["telegram_chat_id"]) != telegram_chat_id
            or int(intent["telegram_user_id"]) != telegram_user_id
        ):
            raise PaymentIntentForbiddenError("payment intent belongs to another Telegram user")
        if intent.get("cancelled_at") is not None:
            raise PaymentIntentCancelledError("payment intent was cancelled")
        if intent.get("consumed_payment_id") is not None:
            payment = await fetch_one(
                conn,
                """
                select payment_id, tenant_id, ref_code, amount_minor, currency,
                       period_start, period_end, previous_paid_until, access_months,
                       telegram_user_id, created_at
                from partner_payment_ledger
                where tenant_id = %s and payment_id = %s
                """,
                (tenant_id, intent["consumed_payment_id"]),
            )
            return {**payment, "paid_until": payment["period_end"], "idempotent": True}
        clock = await fetch_one(conn, "select now() as current_time")
        if _as_utc(intent["expires_at"]) <= _as_utc(clock["current_time"]):
            raise PaymentIntentExpiredError("payment intent expired")

        payment = await _record_manual_payment_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=intent["ref_code"],
            amount_minor=int(intent["amount_minor"]),
            currency=intent["currency"],
            telegram_chat_id=int(intent["telegram_chat_id"]),
            telegram_message_id=int(intent["telegram_message_id"]),
            telegram_user_id=int(intent["telegram_user_id"]),
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update partner_payment_intents
                set consumed_payment_id = %s
                where tenant_id = %s and intent_id = %s::uuid
                """,
                (payment["payment_id"], tenant_id, normalized_intent_id),
            )
        return payment


async def cancel_payment_intent(
    tenant_id: str,
    *,
    intent_id: str,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> str:
    try:
        normalized_intent_id = str(uuid.UUID(str(intent_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PaymentIntentNotFoundError("payment intent not found") from exc
    async with tenant_connection(tenant_id) as conn:
        intent = await fetch_one(
            conn,
            """
            select telegram_chat_id, telegram_user_id, consumed_payment_id, cancelled_at
            from partner_payment_intents
            where tenant_id = %s and intent_id = %s::uuid
            for update
            """,
            (tenant_id, normalized_intent_id),
        )
        if not intent:
            raise PaymentIntentNotFoundError("payment intent not found")
        if (
            int(intent["telegram_chat_id"]) != telegram_chat_id
            or int(intent["telegram_user_id"]) != telegram_user_id
        ):
            raise PaymentIntentForbiddenError("payment intent belongs to another Telegram user")
        if intent.get("consumed_payment_id") is not None:
            return "confirmed"
        if intent.get("cancelled_at") is not None:
            return "cancelled"
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update partner_payment_intents
                set cancelled_at = now()
                where tenant_id = %s and intent_id = %s::uuid
                """,
                (tenant_id, normalized_intent_id),
            )
    return "cancelled"


async def resolve_paid_partner_by_telegram_user_id(
    tenant_id: str,
    telegram_user_id: int,
    *,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    current = _as_utc(at or datetime.now(timezone.utc))
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select la.actor_id, la.telegram_user_id, rp.ref_code, rp.public_profile,
                   ps.paid_until
            from lead_actors la
            join referral_profiles rp
              on rp.tenant_id = la.tenant_id
             and rp.owner_id = la.actor_id
             and rp.enabled = true
            left join partner_subscriptions ps
              on ps.tenant_id = rp.tenant_id
             and ps.ref_code = rp.ref_code
            where la.tenant_id = %s
              and la.telegram_user_id = %s
              and la.active = true
            order by rp.ref_code
            limit 2
            """,
            (tenant_id, telegram_user_id),
        )
    if not rows:
        return None
    if len(rows) > 1:
        raise PartnerIdentityAmbiguousError("telegram user owns multiple referral profiles")
    row = _normalize_subscription(rows[0], at=current)
    return row if row and row["partner_paid"] else None


async def list_due_subscriptions(
    tenant_id: str,
    *,
    horizon_days: int = 7,
    at: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if horizon_days < 0 or limit <= 0:
        raise ValueError("invalid due-list bounds")
    current = _as_utc(at or datetime.now(timezone.utc))
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select ps.tenant_id, ps.ref_code, ps.paid_until,
                   rp.public_profile, la.display_name, la.telegram_username
            from partner_subscriptions ps
            join referral_profiles rp
              on rp.ref_code = ps.ref_code
             and rp.tenant_id = ps.tenant_id
             and rp.enabled = true
            join lead_actors la
              on la.actor_id = rp.owner_id
             and la.tenant_id = rp.tenant_id
            where ps.tenant_id = %s
              and ps.paid_until is not null
              and ps.paid_until <= %s + (%s * interval '1 day')
            order by ps.paid_until, ps.ref_code
            limit %s
            """,
            (tenant_id, current, horizon_days, limit),
        )
    return [_normalize_subscription(row, at=current) for row in rows]


def _profile_subdomain(public_profile: Any) -> str:
    if isinstance(public_profile, str):
        try:
            public_profile = json.loads(public_profile)
        except json.JSONDecodeError:
            return ""
    if not isinstance(public_profile, dict):
        return ""
    return str(public_profile.get("subdomain") or "").strip()


def normalize_partner_subdomain(value: str) -> str:
    normalized = str(value or "").strip().lower().rstrip(".")
    suffix = f".{PARTNER_DOMAIN}"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)]
    if normalized in RESERVED_SUBDOMAINS:
        raise ReservedPartnerHostError("reserved partner subdomain")
    if not _SUBDOMAIN_RE.fullmatch(normalized):
        raise SubscriptionError("invalid or reserved partner subdomain")
    return normalized


def resolve_partner_hostname(ref_code: str, public_profile: Any = None) -> str:
    normalized_ref = str(ref_code or "").strip().lower()
    candidate = (
        _profile_subdomain(public_profile)
        or REF_TO_ISSUED_SUBDOMAIN.get(normalized_ref, "")
        or normalized_ref
    )
    return f"{normalize_partner_subdomain(candidate)}.{PARTNER_DOMAIN}"


def build_host_snapshot(rows: list[dict[str, Any]], *, generated_at: datetime) -> dict[str, Any]:
    hosts: dict[str, str] = {}
    for row in rows:
        try:
            host = resolve_partner_hostname(row["ref_code"], row.get("public_profile"))
        except ReservedPartnerHostError:
            continue
        previous_ref = hosts.get(host)
        if previous_ref and previous_ref != row["ref_code"]:
            raise PartnerHostCollisionError(
                f"hostname {host} resolves to both {previous_ref} and {row['ref_code']}"
            )
        hosts[host] = row["ref_code"]
    allowed_hosts = sorted(hosts)
    version = hashlib.sha256("\n".join(allowed_hosts).encode("utf-8")).hexdigest()
    return {
        "tenant_id": rows[0]["tenant_id"] if rows else None,
        "generated_at": _as_utc(generated_at).isoformat(),
        "version": version,
        "allowed_hosts": allowed_hosts,
    }


async def export_active_partner_hosts(
    tenant_id: str,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    current = _as_utc(at or datetime.now(timezone.utc))
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select ps.tenant_id, ps.ref_code, rp.public_profile
            from partner_subscriptions ps
            join referral_profiles rp
              on rp.ref_code = ps.ref_code
             and rp.tenant_id = ps.tenant_id
             and rp.enabled = true
            where ps.tenant_id = %s
              and partner_subscription_state(ps.paid_until, %s) in ('active', 'grace')
            order by ps.ref_code
            """,
            (tenant_id, current),
        )
    snapshot = build_host_snapshot(rows, generated_at=current)
    snapshot["tenant_id"] = tenant_id
    return snapshot


def build_seed_manifest(
    tenant_id: str,
    rows: list[dict[str, Any]],
    *,
    paid_until: datetime,
) -> dict[str, Any]:
    cutoff = _as_utc(paid_until)
    partners: list[dict[str, Any]] = []
    seen_hosts: dict[str, str] = {}
    for row in rows:
        try:
            host = resolve_partner_hostname(row["ref_code"], row.get("public_profile"))
        except ReservedPartnerHostError:
            continue
        previous_ref = seen_hosts.get(host)
        if previous_ref and previous_ref != row["ref_code"]:
            raise PartnerHostCollisionError(
                f"hostname {host} resolves to both {previous_ref} and {row['ref_code']}"
            )
        seen_hosts[host] = row["ref_code"]
        current_paid_until = row.get("paid_until")
        target = max(_as_utc(current_paid_until), cutoff) if current_paid_until else cutoff
        partners.append(
            {
                "tenant_id": tenant_id,
                "ref_code": row["ref_code"],
                "hostname": host,
                "previous_paid_until": (
                    _as_utc(current_paid_until).isoformat() if current_paid_until else None
                ),
                "new_paid_until": target.isoformat(),
            }
        )
    partners.sort(key=lambda item: (item["hostname"], item["ref_code"]))
    payload = {
        "tenant_id": tenant_id,
        "paid_until": cutoff.isoformat(),
        "partners": partners,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


async def _seed_rows(conn: Any, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select rp.tenant_id, rp.ref_code, rp.public_profile, ps.paid_until
        from referral_profiles rp
        left join partner_subscriptions ps
          on ps.tenant_id = rp.tenant_id
         and ps.ref_code = rp.ref_code
        where rp.tenant_id = %s
          and rp.enabled = true
        order by rp.ref_code
        """,
        (tenant_id,),
    )


async def preview_initial_access_seed(
    tenant_id: str,
    *,
    paid_until: datetime,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        rows = await _seed_rows(conn, tenant_id)
    return build_seed_manifest(tenant_id, rows, paid_until=paid_until)


async def apply_initial_access_seed(
    tenant_id: str,
    *,
    paid_until: datetime,
    expected_manifest_sha: str,
) -> dict[str, Any]:
    cutoff = _as_utc(paid_until)
    async with tenant_connection(tenant_id) as conn:
        rows = await _seed_rows(conn, tenant_id)
        manifest = build_seed_manifest(tenant_id, rows, paid_until=cutoff)
        if manifest["sha256"] != str(expected_manifest_sha or "").strip().lower():
            raise SeedManifestMismatchError("seed manifest changed; run dry-run again")

        changed = 0
        async with conn.cursor() as cur:
            for partner in manifest["partners"]:
                await cur.execute(
                    """
                    insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                    values (%s, %s, %s)
                    on conflict (tenant_id, ref_code) do update
                    set paid_until = case
                          when partner_subscriptions.paid_until is null
                            or partner_subscriptions.paid_until < excluded.paid_until
                          then excluded.paid_until
                          else partner_subscriptions.paid_until
                        end,
                        updated_at = case
                          when partner_subscriptions.paid_until is null
                            or partner_subscriptions.paid_until < excluded.paid_until
                          then now()
                          else partner_subscriptions.updated_at
                        end
                    """,
                    (tenant_id, partner["ref_code"], cutoff),
                )
                previous = partner["previous_paid_until"]
                if previous is None or datetime.fromisoformat(previous) < cutoff:
                    changed += 1
    return {**manifest, "applied": True, "changed_count": changed}
