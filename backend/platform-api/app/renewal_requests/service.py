"""Tenant-scoped payment-proof workflow for partner subscription renewals."""

from __future__ import annotations

from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.subscriptions.pricing import PaymentLine
from app.subscriptions.service import record_payment_lines_in_connection

# Каталог услуг продления (V13, 24.09.2026) — строки partner_subscription_plans:
# сайт на 3/6/12 месяцев, пакет «сайт + клуб», любые курсы course_<slug> и
# полка Академии для авторов (academy_shelf, V15 25.09.2026).
# Клуб отдельно не продаётся: только пакетом с сайтом (владелец). Курс
# добавляется одной строкой в таблицу тарифов — здесь ничего менять не надо.
_OFFER_FILTER = """
    active = true and valid_from <= now()
    and (valid_until is null or valid_until > now())
    and (product_code in ('platform_subscription', 'bundle_pro_club', 'academy_shelf')
         or product_code like 'course!_%%' escape '!')
"""


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


async def cancel_renewal_request(tenant_id: str, actor_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set status = 'cancelled', updated_at = now()
            where tenant_id = %s and actor_id = %s
              and status not in ('confirmed', 'rejected', 'cancelled')
            returning *
            """,
            (tenant_id, actor_id),
        )
    if not row:
        raise RenewalRequestError("Активной заявки на продление нет.")
    return row


async def list_renewal_offers(tenant_id: str) -> list[dict[str, Any]]:
    """Что партнёр может оплатить в боте, в порядке показа."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            select plan_code, product_code, access_months, price_wusd_minor, price_rub_minor,
                   coalesce(title, plan_code) as title
            from partner_subscription_plans
            where tenant_id = %s and {_OFFER_FILTER}
            order by sort_order, access_months, plan_code
            """,
            (tenant_id,),
        )


async def _offer(conn: Any, tenant_id: str, plan_code: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"""
        select plan_code, product_code, access_months, price_wusd_minor, price_rub_minor,
               coalesce(title, plan_code) as title
        from partner_subscription_plans
        where tenant_id = %s and plan_code = %s and {_OFFER_FILTER}
        limit 1
        """,
        (tenant_id, plan_code),
    )


async def set_renewal_plan(tenant_id: str, actor_id: str, plan_code: str) -> dict[str, Any]:
    """Выбор услуги: сайт на срок, пакет с клубом или курс."""
    async with tenant_connection(tenant_id) as conn:
        offer = await _offer(conn, tenant_id, str(plan_code or "").strip())
        if not offer:
            raise RenewalRequestError("Эта услуга сейчас недоступна.")
        row = await fetch_one(
            conn,
            """
            update partner_renewal_requests
            set plan_code = %s, access_months = %s, status = 'awaiting_country', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_period'
            returning *
            """,
            (offer["plan_code"], int(offer["access_months"]), tenant_id, actor_id),
        )
    if not row:
        raise RenewalRequestError("Заявка уже перешла к следующему шагу.")
    return row


async def plan_title(tenant_id: str, plan_code: str | None) -> str:
    """Название услуги для сообщений: «Сайт + Клуб на 3 месяца», «Курс …»."""
    if not plan_code:
        return ""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select coalesce(title, plan_code) as title from partner_subscription_plans"
            " where tenant_id = %s and plan_code = %s limit 1",
            (tenant_id, plan_code),
        )
    return str(row["title"]) if row else str(plan_code)


async def renewal_lines(conn: Any, tenant_id: str, request: dict[str, Any]) -> list[PaymentLine]:
    """Строки оплаты для заявки. Пакет раскладывается на сайт и клуб: сайт — по
    цене тарифа на 3 месяца, клуб — остаток пакета; сумма всегда равна цене пакета."""
    currency = str(request["currency"])
    amount = int(request["amount_minor"])
    plan_code = request.get("plan_code")
    if not plan_code:  # заявки до V13: только сайт на access_months
        return [PaymentLine("platform_subscription", amount, currency, int(request["access_months"]), False, "")]
    offer = await fetch_one(
        conn,
        "select plan_code, product_code, access_months from partner_subscription_plans"
        " where tenant_id = %s and plan_code = %s limit 1",
        (tenant_id, plan_code),
    )
    if not offer:
        raise RenewalRequestError("Тариф заявки не найден.")
    product = str(offer["product_code"])
    if product == "bundle_pro_club":
        site = await fetch_one(
            conn,
            """
            select price_wusd_minor, price_rub_minor from partner_subscription_plans
            where tenant_id = %s and product_code = 'platform_subscription' and access_months = 3
              and active = true order by valid_from desc limit 1
            """,
            (tenant_id,),
        )
        site_minor = int(site["price_rub_minor"] if currency == "RUB" else site["price_wusd_minor"])
        note = "пакет сайт + клуб"
        return [
            PaymentLine("platform_subscription", site_minor, currency, 3, False, note),
            PaymentLine("club_subscription", amount - site_minor, currency, 3, True, note),
        ]
    if product.startswith("course_"):
        return [PaymentLine(product, amount, currency, 0, False, "")]
    if product == "academy_shelf":
        # Полка автора — свой срок (academy_shelf.paid_until), не продление сайта.
        return [PaymentLine("academy_shelf", amount, currency, int(offer["access_months"]), False, "")]
    return [PaymentLine("platform_subscription", amount, currency, int(offer["access_months"]), False, "")]


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
            select request_id, access_months, plan_code
            from partner_renewal_requests
            where tenant_id = %s and actor_id = %s and status = 'awaiting_country'
            for update
            """,
            (tenant_id, actor_id),
        )
        if not request:
            raise RenewalRequestError("Сейчас страна оплаты не ожидается.")
        if request.get("plan_code"):
            plan = await _offer(conn, tenant_id, str(request["plan_code"]))
        else:
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
        # Одна оплата — столько строк, сколько в услуге: сайт; сайт + клуб; курс; полка.
        # Курс открывается в Академии, полка продлевается — там же, в
        # record_payment_lines_in_connection (единый писатель academy_access / academy_shelf).
        lines = await renewal_lines(conn, tenant_id, request)
        result = await record_payment_lines_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=str(request["ref_code"]),
            lines=lines,
            received_minor=int(request["amount_minor"]),
            currency=str(request["currency"]),
            telegram_chat_id=int(request["proof_chat_id"]),
            telegram_message_id=int(request["proof_message_id"]),
            telegram_user_id=int(request["telegram_user_id"]),
        )
        main = next(
            (l for l in result["lines"] if l["product_code"] == "platform_subscription"),
            result["lines"][0],
        )
        payment = {
            **main,
            "referral_bonus": result.get("referral_bonus"),
            "club_paid_until": result.get("club_paid_until"),
        }
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
