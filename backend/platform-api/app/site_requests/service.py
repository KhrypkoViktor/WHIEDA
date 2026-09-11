"""Tenant-scoped collection and approval of new partner site requests."""

from __future__ import annotations

import json
from typing import Any

from app.db import fetch_one, tenant_connection
from app.subscriptions.service import (
    SubscriptionError,
    _record_manual_payment_in_connection,
    normalize_partner_subdomain,
)


class SiteRequestError(ValueError):
    pass


async def get_open_site_request(tenant_id: str, actor_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select * from partner_site_requests
            where tenant_id = %s and actor_id = %s
              and status not in ('provisioned', 'rejected', 'cancelled')
            order by created_at desc limit 1
            """,
            (tenant_id, actor_id),
        )


async def begin_site_request(tenant_id: str, actor_id: str) -> dict[str, Any]:
    existing = await get_open_site_request(tenant_id, actor_id)
    if existing:
        return existing
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into partner_site_requests (tenant_id, actor_id, status)
            values (%s, %s, 'awaiting_country')
            returning *
            """,
            (tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось создать заявку.")
    return row


async def set_site_request_country(
    tenant_id: str, actor_id: str, country_code: str
) -> dict[str, Any]:
    country = str(country_code or "").strip().upper()
    if country not in {"BY", "RU"}:
        raise SiteRequestError("Выберите Беларусь или Россию.")
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set country_code = %s, status = 'awaiting_subdomain', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_country'
            returning *
            """,
            (country, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Заявка уже перешла к следующему шагу.")
    return row


async def set_site_request_subdomain(
    tenant_id: str, actor_id: str, subdomain: str
) -> dict[str, Any]:
    try:
        normalized = normalize_partner_subdomain(subdomain)
    except SubscriptionError as exc:
        raise SiteRequestError(
            "Напишите другое имя: латинские буквы, цифры и дефис, без пробелов."
        ) from exc
    async with tenant_connection(tenant_id) as conn:
        occupied = await fetch_one(
            conn,
            """
            select 1 as occupied from referral_profiles where ref_code = %s
            union all
            select 1 as occupied from partner_site_requests
            where tenant_id = %s and requested_subdomain = %s and actor_id <> %s
              and status not in ('rejected', 'cancelled')
            limit 1
            """,
            (normalized, tenant_id, normalized, actor_id),
        )
        if occupied:
            raise SiteRequestError("Это имя уже занято. Напишите другой вариант.")
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set requested_subdomain = %s, status = 'awaiting_photo', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_subdomain'
            returning *
            """,
            (normalized, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас имя сайта не ожидается.")
    return row


async def set_site_request_photo(
    tenant_id: str, actor_id: str, file_id: str
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set profile_photo_file_id = %s, status = 'awaiting_text', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_photo'
            returning *
            """,
            (file_id, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас фотография не ожидается.")
    return row


async def set_site_request_intro(
    tenant_id: str, actor_id: str, intro_text: str
) -> dict[str, Any]:
    value = str(intro_text or "").strip()
    if len(value) < 20:
        raise SiteRequestError("Напишите о себе хотя бы 2-3 предложения.")
    if len(value) > 3000:
        raise SiteRequestError("Текст длиннее 3000 знаков. Пришлите более короткий вариант.")
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select country_code from partner_site_requests
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            for update
            """,
            (tenant_id, actor_id),
        )
        if not request:
            raise SiteRequestError("Сейчас текст не ожидается.")
        if request["country_code"] == "RU":
            currency, total, subscription = "RUB", 400_000, 300_000
        else:
            currency, total, subscription = "WUSD", 4_000, 3_000
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set intro_text = %s, total_amount_minor = %s,
                subscription_amount_minor = %s, currency = %s,
                status = 'awaiting_payment', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            returning *
            """,
            (value, total, subscription, currency, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось сохранить текст.")
    return row


async def submit_site_payment_proof(
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
            update partner_site_requests
            set proof_chat_id = %s, proof_message_id = %s, proof_file_id = %s,
                status = 'pending_confirmation', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_payment'
            returning *
            """,
            (chat_id, message_id, file_id, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас подтверждение оплаты не ожидается.")
    return row


async def confirm_site_request(
    tenant_id: str,
    *,
    request_id: str,
    admin_telegram_user_id: int,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            """
            select sr.*, la.display_name, la.telegram_chat_id, la.telegram_user_id
            from partner_site_requests sr
            join lead_actors la
              on la.tenant_id = sr.tenant_id and la.actor_id = sr.actor_id
            where sr.tenant_id = %s and sr.request_id = %s::uuid
            for update of sr
            """,
            (tenant_id, request_id),
        )
        if not request:
            raise SiteRequestError("Заявка не найдена.")
        if request["status"] == "pending_provisioning":
            return {**request, "idempotent": True}
        if request["status"] != "pending_confirmation":
            raise SiteRequestError("Эту заявку сейчас нельзя подтвердить.")
        profile = json.dumps(
            {
                "subdomain": request["requested_subdomain"],
                "display_name": request["display_name"],
                "intro_text": request["intro_text"],
                "telegram_photo_file_id": request["profile_photo_file_id"],
                "provisioning_status": "pending",
            },
            ensure_ascii=False,
        )
        created = await fetch_one(
            conn,
            """
            insert into referral_profiles (
              ref_code, tenant_id, owner_id, display_mode, public_profile,
              country_code, enabled
            ) values (%s, %s, %s, 'named', %s::jsonb, %s, false)
            on conflict (ref_code) do nothing
            returning ref_code
            """,
            (
                request["requested_subdomain"], tenant_id, request["actor_id"],
                profile, request["country_code"],
            ),
        )
        if not created:
            raise SiteRequestError("Имя сайта уже занято. Заявку нужно проверить вручную.")
        payment = await _record_manual_payment_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=str(request["requested_subdomain"]),
            amount_minor=int(request["subscription_amount_minor"]),
            currency=str(request["currency"]),
            telegram_chat_id=int(request["proof_chat_id"]),
            telegram_message_id=int(request["proof_message_id"]),
            telegram_user_id=int(request["telegram_user_id"]),
            product_code="platform_subscription",
            access_months=3,
        )
        updated = await fetch_one(
            conn,
            """
            update partner_site_requests
            set status = 'pending_provisioning', subscription_payment_id = %s,
                confirmed_by_telegram_user_id = %s, confirmed_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning *
            """,
            (payment["payment_id"], admin_telegram_user_id, tenant_id, request_id),
        )
    return {**updated, "payment": payment, "idempotent": False}


async def reject_site_request(
    tenant_id: str, *, request_id: str, admin_telegram_user_id: int
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set status = 'rejected', confirmed_by_telegram_user_id = %s,
                rejected_at = now(), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
              and status = 'pending_confirmation'
            returning *
            """,
            (admin_telegram_user_id, tenant_id, request_id),
        )
    if not row:
        raise SiteRequestError("Заявку уже обработали или она не найдена.")
    return row
