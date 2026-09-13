"""What the signed-in person owns, for the site menu: balance, PRO, CLUB.

One query by telegram_user_id. A person without a referral profile still gets
their balance (bonuses accrue to lead_actors, not to a site). Club comes from
partner_product_access once that table exists (products migration v7); until
then the club column is simply not selected — PostgreSQL parses the whole
statement, so a missing table cannot be hidden behind CASE.

Display unit is WWC$; amount_minor is hundredths of a WWC$ (1 WWC$ = 100 ₽).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import fetch_one, tenant_connection
from app.subscriptions.service import subscription_state

DISPLAY_CURRENCY = "WWC$"


def _days_left(paid_until: datetime | None, at: datetime) -> int | None:
    if paid_until is None:
        return None
    if paid_until.tzinfo is None:
        paid_until = paid_until.replace(tzinfo=timezone.utc)
    delta = paid_until.astimezone(timezone.utc) - at
    return max(0, delta.days)


def _product(paid_until: datetime | None, at: datetime, **extra: Any) -> dict[str, Any]:
    if paid_until is None:
        return {"status": "none", "paid_until": None, "days_left": None, **extra}
    return {
        "status": subscription_state(paid_until, at=at),
        "paid_until": paid_until,
        "days_left": _days_left(paid_until, at),
        **extra,
    }


def build_site_account(
    *,
    actor_id: str,
    bonus_minor: int,
    pro_paid_until: datetime | None,
    pro_ref_code: str | None,
    club_paid_until: datetime | None,
    at: datetime,
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "balance": {"currency": DISPLAY_CURRENCY, "amount_minor": int(bonus_minor or 0)},
        "pro": _product(pro_paid_until, at, ref_code=pro_ref_code),
        "club": _product(club_paid_until, at),
    }


_ACCOUNT_SQL = """
select la.actor_id,
       coalesce((
         select sum(b.amount_minor) from partner_bonus_ledger b
          where b.tenant_id = la.tenant_id and b.actor_id = la.actor_id
            and b.currency = 'WUSD'
       ), 0)::bigint as bonus_minor,
       ps.paid_until as pro_paid_until,
       rp.ref_code as pro_ref_code,
       {club_column} as club_paid_until
  from lead_actors la
  left join referral_profiles rp
    on rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
  left join partner_subscriptions ps
    on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
 where la.tenant_id = %s and la.telegram_user_id = %s and la.active = true
 order by rp.ref_code
 limit 1
"""


_CLUB_COLUMN = """(
         select pa.paid_until from partner_product_access pa
          where pa.tenant_id = rp.tenant_id and pa.ref_code = rp.ref_code
            and pa.product_code = 'club_subscription'
       )"""
_club_table_present: bool | None = None


async def _club_column(conn: Any) -> str:
    """Select the club column only when its table exists; remembered per process."""
    global _club_table_present
    if _club_table_present is None:
        row = await fetch_one(conn, "select to_regclass('public.partner_product_access') as name")
        _club_table_present = bool(row and row.get("name"))
    return _CLUB_COLUMN if _club_table_present else "null::timestamptz"


async def load_site_account(
    tenant_id: str, telegram_user_id: int, *, at: datetime | None = None
) -> dict[str, Any] | None:
    current = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    async with tenant_connection(tenant_id) as conn:
        sql = _ACCOUNT_SQL.format(club_column=await _club_column(conn))
        row = await fetch_one(conn, sql, (tenant_id, int(telegram_user_id)))
    if not row:
        return None
    return build_site_account(
        actor_id=str(row["actor_id"]),
        bonus_minor=int(row["bonus_minor"] or 0),
        pro_paid_until=row.get("pro_paid_until"),
        pro_ref_code=row.get("pro_ref_code"),
        club_paid_until=row.get("club_paid_until"),
        at=current,
    )
