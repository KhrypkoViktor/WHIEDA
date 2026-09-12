"""Tenant-scoped payment-proof workflow for partner subscription renewals."""

from __future__ import annotations

from typing import Any

from app.db import fetch_one, tenant_connection
from app.subscriptions.service import _record_manual_payment_in_connection


class RenewalRequestError(ValueError):
    pass


async def get_open_renewal_request(tenant_id: str, actor_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select * from partner_renewal_requests
            where tenant_id = %s and actor_id = %s
              and status not in ('confirmed', 'rejected', 'cancelled')
            order by created_at desc
            limit 1
            """,
            (tenant_id, actor_id),
        )


async def begin_renewal_request(tenant_id: str, actor_id: str) -> dict[str, Any]:
    existing = await get_open_renewal_request(tenant_id, actor_id)
    if existing:
        return existing
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into partner_renewal_requests (tenant_id, actor_id, ref_code, status)
            select rp.tenant_id, rp.owner_id, rp.ref_code, 'awaiting_period'
            from referral_profiles rp
            where rp.tenant_id = %s and rp.owner_id = %s and rp.enabled = true
            order by rp.ref_code
            limit 1
            returning *
            """,
            (tenant_id, actor_id),
        )
    if not row:
        raise RenewalRequestError("Сначала создайте персональный сайт.")
    return row


async def set_renewal_period(
    tenant_id: str, actor_id: str, access_months: int
) -> dict[str, Any]:
    if access_months not in {3, 6, 12}:
        raise RenewalRequestError("Выберите срок 3, 6 или 12 месяцев.")
    async with tenant_connection(tenant_id) as conn:
        plan = await fetch_one(
            conn,
            """
            select plan_code
            from partner_subscription_plans
            where tenant_id = %s and product_code = 'platform_subscription'
              and access_months = %s and active = true and valid_from <= now()
              and (valid_until is null or valid_until > now())
            limit 1
            """,
            (tenant_id, access_months),
        )
        if not plan:
            raise RenewalRequestError("Этот тариф сейчас недоступен.")
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set access_months = %s, status = 'awaiting_country', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_period'
            returning *
            """,
            (access_months, tenant_id, actor_id),
        )
    if not row:
        raise RenewalRequestError("Заявка уже перешла к следующему шагу.")
    return row


async def set_renewal_country(
    tenant_id: str, actor_id: str, country_code: str
) -> dict[str, Any]:
    country = str(country_code or "").strip().upper()
    if country not in {"BY", "RU"}:
        raise RenewalRequestError("Выберите Беларусь или Россию.")
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select request_id, access_months
            from partner_renewal_requests
            where tenant_id = %s and actor_id = %s and status = 'awaiting_country'
            for update
            """,
            (tenant_id, actor_id),
        )
        if not request:
            raise RenewalRequestError("Сейчас страна оплаты не ожидается.")
        plan = await fetch_one(
            conn,
            """
            select price_wusd_minor, price_rub_minor
            from partner_subscription_plans
            where tenant_id = %s and product_code = 'platform_subscription'
              and access_months = %s and active = true and valid_from <= now()
              and (valid_until is null or valid_until > now())
            limit 1
            """,
            (tenant_id, int(request["access_months"])),
        )
        if not plan:
            raise RenewalRequestError("Этот тариф сейчас недоступен.")
        currency = "RUB" if country == "RU" else "WUSD"
        amount_minor = int(
            plan["price_rub_minor"] if country == "RU" else plan["price_wusd_minor"]
        )
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set country_code = %s, currency = %s, amount_minor = %s,
                status = 'awaiting_payment', updated_at = now()
            where tenant_id = %s and request_id = %s
            returning *
            """,
            (country, currency, amount_minor, tenant_id, request["request_id"]),
        )
    if not row:
        raise RenewalRequestError("Не удалось подготовить оплату.")
    return row


async def submit_renewal_payment_proof(
    tenant_id: str,
    actor_id: str,
    *,
    chat_id: int,
    message_id: int,
    file_id: str,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set proof_chat_id = %s, proof_message_id = %s, proof_file_id = %s,
                status = 'pending_confirmation', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_payment'
            returning *
            """,
            (chat_id, message_id, file_id, tenant_id, actor_id),
        )
    if not row:
        raise RenewalRequestError("Сейчас подтверждение оплаты не ожидается.")
    return row


async def confirm_renewal_request(
    tenant_id: str,
    *,
    request_id: str,
    admin_telegram_user_id: int,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select rr.*, la.display_name, la.telegram_chat_id, la.telegram_user_id
            from partner_renewal_requests rr
            join lead_actors la
              on la.tenant_id = rr.tenant_id and la.actor_id = rr.actor_id
            where rr.tenant_id = %s and rr.request_id = %s::uuid
            for update of rr
            """,
            (tenant_id, request_id),
        )
        if not request:
            raise RenewalRequestError("Заявка не найдена.")
        if request["status"] == "confirmed":
            return {**request, "idempotent": True}
        if request["status"] != "pending_confirmation":
            raise RenewalRequestError("Эту заявку сейчас нельзя подтвердить.")
        payment = await _record_manual_payment_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=str(request["ref_code"]),
            amount_minor=int(request["amount_minor"]),
            currency=str(request["currency"]),
            telegram_chat_id=int(request["proof_chat_id"]),
            telegram_message_id=int(request["proof_message_id"]),
            telegram_user_id=int(request["telegram_user_id"]),
            product_code="platform_subscription",
            access_months=int(request["access_months"]),
        )
        updated = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set status = 'confirmed', payment_id = %s,
                confirmed_by_telegram_user_id = %s, confirmed_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning *
            """,
            (payment["payment_id"], admin_telegram_user_id, tenant_id, request_id),
        )
    return {**updated, "payment": payment, "idempotent": False}


async def reject_renewal_request(
    tenant_id: str, *, request_id: str, admin_telegram_user_id: int
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set status = 'rejected', confirmed_by_telegram_user_id = %s,
                rejected_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
              and status = 'pending_confirmation'
            returning *
            """,
            (admin_telegram_user_id, tenant_id, request_id),
        )
    if not row:
        raise RenewalRequestError("Заявку уже обработали или она не найдена.")
    return row
