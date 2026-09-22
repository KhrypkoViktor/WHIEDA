"""Service sales (Gemini licences) sold through the support tunnel.

Money goes one way (owner, 15–16.09.2026): the client pays the owner, the
owner keeps a prepaid deposit with the service administrator, every sale is
written off that deposit. The partner who sold gets WWC$ on the bonus ledger;
nobody is paid money.

    retail 3 990 ₽ → sold by the owner: deposit −2 990
                   → sold by a partner: deposit −2 490, partner +5 WWC$

The numbers live in ``service_tariffs`` and change by a bot command.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from app.db import fetch_all, fetch_one, tenant_connection

Seller = Literal["owner", "partner"]

_SALE_COLUMNS = """
sale_id, tenant_id, ticket_id, offer_code, client_actor_id, seller, partner_ref,
retail_minor, owed_admin_minor, partner_share_wusd_minor, paid_currency, paid_at,
activated_until, status, created_by_telegram_user_id, created_at
"""


# ----------------------------------------------------------------------------
# Tariff
# ----------------------------------------------------------------------------

async def get_tariff(tenant_id: str, offer_code: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await _tariff(conn, tenant_id, offer_code)


async def _tariff(conn, tenant_id: str, offer_code: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor, partner_share_wusd_minor
        from service_tariffs where tenant_id = %s and offer_code = %s
        """,
        (tenant_id, offer_code),
    )


async def list_tariffs(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            "select offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor, partner_share_wusd_minor from service_tariffs where tenant_id = %s order by offer_code",
            (tenant_id,),
        )


async def set_tariff(
    tenant_id: str,
    *,
    offer_code: str,
    retail_minor: int,
    wholesale_direct_minor: int,
    wholesale_partner_minor: int,
    partner_share_wusd_minor: int,
    updated_by: int,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into service_tariffs (
              tenant_id, offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor,
              partner_share_wusd_minor, updated_by_telegram_user_id
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, offer_code) do update
              set retail_minor = excluded.retail_minor,
                  wholesale_direct_minor = excluded.wholesale_direct_minor,
                  wholesale_partner_minor = excluded.wholesale_partner_minor,
                  partner_share_wusd_minor = excluded.partner_share_wusd_minor,
                  updated_by_telegram_user_id = excluded.updated_by_telegram_user_id,
                  updated_at = now()
            returning offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor, partner_share_wusd_minor
            """,
            (tenant_id, offer_code, int(retail_minor), int(wholesale_direct_minor), int(wholesale_partner_minor), int(partner_share_wusd_minor), int(updated_by)),
        )
    return dict(row)


def split_sale(tariff: dict[str, Any], seller: Seller) -> dict[str, int]:
    """Who gets what for one sale under this tariff."""
    retail = int(tariff["retail_minor"])
    if seller == "partner":
        owed = int(tariff["wholesale_partner_minor"])
        share = int(tariff["partner_share_wusd_minor"])
    else:
        owed = int(tariff["wholesale_direct_minor"])
        share = 0
    return {"retail_minor": retail, "owed_admin_minor": owed, "partner_share_wusd_minor": share, "owner_keeps_minor": retail - owed - share * 100}


# ----------------------------------------------------------------------------
# Who sold: the client's referrer is the suggestion, a human confirms
# ----------------------------------------------------------------------------

async def suggest_partner_for_client(tenant_id: str, *, client_telegram_user_id: int) -> dict[str, Any] | None:
    """The partner who brought this person to the bot (referral attribution), if any."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select rp.ref_code as partner_ref, rp.owner_id as partner_actor_id, la.actor_id as client_actor_id
            from lead_actors la
            join partner_referral_attributions a
              on a.tenant_id = la.tenant_id and a.invitee_actor_id = la.actor_id
            join referral_profiles rp
              on rp.tenant_id = a.tenant_id and rp.owner_id = a.inviter_actor_id and rp.enabled = true
            where la.tenant_id = %s
              and (la.telegram_user_id = %s or la.actor_id = %s)
            order by a.created_at desc
            limit 1
            """,
            (tenant_id, int(client_telegram_user_id), f"telegram:{tenant_id}:{int(client_telegram_user_id)}"),
        )


async def partner_label(tenant_id: str, partner_ref: str | None) -> str:
    """Nameless partner identifier for the administrator's group: the e-mail
    login when we know it, otherwise the partner's site login (ref code) —
    the administrator cannot find a person by it, and we no longer wait for
    the e-mails to arrive (owner, 22.09.2026)."""
    if not partner_ref:
        return "Виктор (прямая продажа)"
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select la.email from referral_profiles rp
            join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
            where rp.tenant_id = %s and rp.ref_code = %s
            """,
            (tenant_id, partner_ref),
        )
    email = str((row or {}).get("email") or "").strip()
    if email:
        return email
    return str(partner_ref)


# ----------------------------------------------------------------------------
# Sale: idempotent per ticket; deposit write-off; partner WWC$
# ----------------------------------------------------------------------------

async def record_sale(
    tenant_id: str,
    *,
    ticket: dict[str, Any],
    offer_code: str,
    seller: Seller,
    partner_ref: str | None,
    admin_telegram_user_id: int,
    created_by: int,
) -> dict[str, Any]:
    if seller == "partner" and not partner_ref:
        raise ValueError("partner_ref is required when the seller is a partner")
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            f"select {_SALE_COLUMNS} from service_sales where tenant_id = %s and ticket_id = %s::uuid",
            (tenant_id, str(ticket["ticket_id"])),
        )
        if existing:
            return {**existing, "idempotent": True, "partner_bonus": None}
        tariff = await _tariff(conn, tenant_id, offer_code)
        if not tariff:
            raise ValueError(f"no tariff for {offer_code}")
        split = split_sale(tariff, seller)
        client = await fetch_one(
            conn,
            "select actor_id from lead_actors where tenant_id = %s and (telegram_user_id = %s or actor_id = %s) limit 1",
            (tenant_id, int(ticket["user_telegram_user_id"]), f"telegram:{tenant_id}:{int(ticket['user_telegram_user_id'])}"),
        )
        sale = await fetch_one(
            conn,
            f"""
            insert into service_sales (
              tenant_id, ticket_id, offer_code, client_actor_id, seller, partner_ref,
              retail_minor, owed_admin_minor, partner_share_wusd_minor, created_by_telegram_user_id
            ) values (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
            returning {_SALE_COLUMNS}
            """,
            (
                tenant_id, str(ticket["ticket_id"]), offer_code, (client or {}).get("actor_id"), seller,
                partner_ref if seller == "partner" else None,
                split["retail_minor"], split["owed_admin_minor"], split["partner_share_wusd_minor"], int(created_by),
            ),
        )
        await fetch_one(
            conn,
            """
            insert into service_admin_deposit (tenant_id, admin_telegram_user_id, kind, amount_minor, sale_id, confirmed_at)
            values (%s, %s, 'sale', %s, %s::uuid, now())
            returning entry_id
            """,
            (tenant_id, int(admin_telegram_user_id), -int(split["owed_admin_minor"]), str(sale["sale_id"])),
        )
        bonus = None
        if seller == "partner" and split["partner_share_wusd_minor"] > 0:
            bonus = await _credit_partner(conn, tenant_id, partner_ref=str(partner_ref), sale=sale)
        balance = await _deposit_balance(conn, tenant_id, int(admin_telegram_user_id))
    return {**sale, "idempotent": False, "partner_bonus": bonus, "deposit_balance_minor": balance, "owner_keeps_minor": split["owner_keeps_minor"]}


async def _credit_partner(conn, tenant_id: str, *, partner_ref: str, sale: dict[str, Any]) -> dict[str, Any] | None:
    owner = await fetch_one(
        conn,
        "select owner_id from referral_profiles where tenant_id = %s and ref_code = %s and enabled = true",
        (tenant_id, partner_ref),
    )
    if not owner:
        return None
    entry = await fetch_one(
        conn,
        """
        insert into partner_bonus_ledger (
          tenant_id, actor_id, entry_type, amount_minor, currency, product_code,
          idempotency_key, rule_snapshot, description
        ) values (%s, %s, 'credit', %s, 'WUSD', 'gemini', %s, %s::jsonb, %s)
        on conflict (tenant_id, idempotency_key) do nothing
        returning entry_id
        """,
        (
            tenant_id, owner["owner_id"], int(sale["partner_share_wusd_minor"]), f"service_commission:{sale['sale_id']}",
            '{"kind": "service_commission"}', f"Gemini: доля партнёра за продажу, заявка {sale['ticket_id']}",
        ),
    )
    balance = await fetch_one(
        conn,
        "select coalesce(sum(amount_minor), 0)::bigint as minor from partner_bonus_ledger where tenant_id = %s and actor_id = %s",
        (tenant_id, owner["owner_id"]),
    )
    chat = await fetch_one(
        conn,
        "select telegram_chat_id from lead_actors where tenant_id = %s and actor_id = %s",
        (tenant_id, owner["owner_id"]),
    )
    return {
        "actor_id": owner["owner_id"],
        "amount_minor": int(sale["partner_share_wusd_minor"]),
        "balance_minor": int(balance["minor"]),
        "telegram_chat_id": (chat or {}).get("telegram_chat_id"),
        "idempotent": entry is None,
    }


async def get_sale(tenant_id: str, *, sale_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(conn, f"select {_SALE_COLUMNS} from service_sales where tenant_id = %s and sale_id = %s::uuid", (tenant_id, sale_id))


async def get_sale_for_ticket(tenant_id: str, *, ticket_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(conn, f"select {_SALE_COLUMNS} from service_sales where tenant_id = %s and ticket_id = %s::uuid", (tenant_id, ticket_id))


async def activate_sale(tenant_id: str, *, sale_id: str, activated_until: datetime) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
            update service_sales set activated_until = %s, status = 'activated'
            where tenant_id = %s and sale_id = %s::uuid and status = 'paid'
            returning {_SALE_COLUMNS}
            """,
            (activated_until, tenant_id, sale_id),
        )


# ----------------------------------------------------------------------------
# Deposit with the administrator
# ----------------------------------------------------------------------------

async def _deposit_balance(conn, tenant_id: str, admin_id: int) -> int:
    row = await fetch_one(
        conn,
        """
        select coalesce(sum(amount_minor), 0)::bigint as minor from service_admin_deposit
        where tenant_id = %s and admin_telegram_user_id = %s and confirmed_at is not null
        """,
        (tenant_id, int(admin_id)),
    )
    return int(row["minor"])


async def deposit_balance(tenant_id: str, *, admin_telegram_user_id: int) -> int:
    async with tenant_connection(tenant_id) as conn:
        return await _deposit_balance(conn, tenant_id, admin_telegram_user_id)


async def add_topup(tenant_id: str, *, admin_telegram_user_id: int, amount_minor: int, sent_by: int) -> dict[str, Any]:
    if int(amount_minor) <= 0:
        raise ValueError("top-up must be positive")
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into service_admin_deposit (tenant_id, admin_telegram_user_id, kind, amount_minor, sent_by_telegram_user_id)
            values (%s, %s, 'topup', %s, %s)
            returning entry_id, amount_minor, created_at
            """,
            (tenant_id, int(admin_telegram_user_id), int(amount_minor), int(sent_by)),
        )
    return dict(row)


async def confirm_topup(tenant_id: str, *, entry_id: str, confirmed_by: int) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update service_admin_deposit set confirmed_by_telegram_user_id = %s, confirmed_at = now()
            where tenant_id = %s and entry_id = %s::uuid and kind = 'topup' and confirmed_at is null
            returning entry_id, amount_minor, admin_telegram_user_id
            """,
            (int(confirmed_by), tenant_id, entry_id),
        )
        if not row:
            return None
        balance = await _deposit_balance(conn, tenant_id, int(row["admin_telegram_user_id"]))
    return {**row, "deposit_balance_minor": balance}


async def pending_topups(tenant_id: str, *, admin_telegram_user_id: int) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select entry_id, amount_minor, created_at from service_admin_deposit
            where tenant_id = %s and admin_telegram_user_id = %s and kind = 'topup' and confirmed_at is null
            order by created_at
            """,
            (tenant_id, int(admin_telegram_user_id)),
        )


# ----------------------------------------------------------------------------
# Reports
# ----------------------------------------------------------------------------

async def month_report(tenant_id: str, *, admin_telegram_user_id: int, since: datetime | None = None) -> dict[str, Any]:
    start = since or datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with tenant_connection(tenant_id) as conn:
        totals = await fetch_one(
            conn,
            """
            select count(*)::int as sales, coalesce(sum(retail_minor), 0)::bigint as retail_minor,
                   coalesce(sum(owed_admin_minor), 0)::bigint as owed_minor,
                   coalesce(sum(retail_minor - owed_admin_minor - partner_share_wusd_minor * 100), 0)::bigint as owner_minor
            from service_sales where tenant_id = %s and paid_at >= %s
            """,
            (tenant_id, start),
        )
        by_partner = await fetch_all(
            conn,
            """
            select partner_ref, count(*)::int as sales, coalesce(sum(partner_share_wusd_minor), 0)::bigint as share_wusd_minor
            from service_sales where tenant_id = %s and paid_at >= %s and seller = 'partner'
            group by partner_ref order by 2 desc, 1
            """,
            (tenant_id, start),
        )
        balance = await _deposit_balance(conn, tenant_id, int(admin_telegram_user_id))
        pending = await fetch_all(
            conn,
            "select entry_id, amount_minor, created_at from service_admin_deposit where tenant_id = %s and admin_telegram_user_id = %s and kind = 'topup' and confirmed_at is null order by created_at",
            (tenant_id, int(admin_telegram_user_id)),
        )
        tariffs = await fetch_all(conn, "select offer_code, wholesale_direct_minor, wholesale_partner_minor from service_tariffs where tenant_id = %s", (tenant_id,))
    min_licence = min([int(t["wholesale_partner_minor"]) for t in tariffs] or [0])
    return {
        "since": start, **dict(totals), "by_partner": [dict(r) for r in by_partner],
        "deposit_balance_minor": balance, "pending_topups": [dict(r) for r in pending],
        "low_balance": balance < min_licence,
    }
