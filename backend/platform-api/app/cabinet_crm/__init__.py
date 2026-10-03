"""WWC CRM для кабинета /me/: сколько людей у партнёра в CRM и его таймзона.

Таблицы CRM принадлежат необязательной функции «crm» (schema_requirements),
а кабинет — ядро: ядро их SQL не читает. Поэтому выборка лежит здесь, в пакете
функции «crm», и вызывается только при включённой CRM. Аккаунт не создаётся:
нет аккаунта — ноль людей. Удалённые карточки тоже считаются: шаг пути
«добавить первого человека» — про то, что человек это уже сделал.
"""

from __future__ import annotations

from app.db import fetch_one, tenant_connection


async def crm_account(tenant_id: str, telegram_user_id: int) -> dict | None:
    """Аккаунт WWC CRM в форме app.crm.service (account_id, timezone, today) — без
    создания: кабинет не заводит CRM тому, кто её не открывал."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select account_id::text as account_id, telegram_user_id, timezone,
                   (now() at time zone timezone)::date as today
            from platform_accounts
            where tenant_id = %s and telegram_user_id = %s
            """,
            (tenant_id, int(telegram_user_id)),
        )
    return dict(row) if row else None


async def crm_contacts_count(tenant_id: str, telegram_user_id: int) -> int:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select count(c.contact_id)::int as contacts
            from platform_accounts a
            join crm_contacts c
              on c.tenant_id = a.tenant_id and c.account_id = a.account_id
            where a.tenant_id = %s and a.telegram_user_id = %s
            """,
            (tenant_id, int(telegram_user_id)),
        )
    return int((row or {}).get("contacts") or 0)


async def account_timezone(tenant_id: str, telegram_user_id: int) -> str | None:
    """Таймзона из аккаунта WWC CRM (общая с кабинетом); нет аккаунта — None."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select timezone from platform_accounts where tenant_id = %s and telegram_user_id = %s",
            (tenant_id, int(telegram_user_id)),
        )
    return str(row["timezone"]) if row and row.get("timezone") else None
