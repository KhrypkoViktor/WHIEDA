"""Tenant-scoped collection and approval of new partner site requests."""

from __future__ import annotations

import json
from typing import Any

from app.site_requests.contacts import parse_contacts
from app.db import fetch_one, tenant_connection
from app.subscriptions.pricing import PaymentLine
from app.subscriptions.service import (
    SubscriptionError,
    normalize_partner_subdomain,
    record_payment_lines_in_connection,
)
from app.theme_access.service import ISSUED_SUBDOMAIN_TO_REF

# Что оформляют (владелец, 14–19.09.2026). Суммы в minor: WWC$ ×100, ₽ ×100.
#   site   — PRO 3 мес + настройка сайта: 30 + 20 WWC$ = 3 000 + 2 000 ₽
#   bundle — PRO 3 мес + клуб 3 мес по акции: 105 WWC$ = 10 500 ₽, настройка в подарок
PLANS: dict[str, dict[str, Any]] = {
    "site": {
        "label": "Сайт на 3 месяца + настройка",
        "lines": (("platform_subscription", 3_000, 3, False, ""), ("site_setup", 2_000, 0, False, "")),
    },
    "bundle": {
        "label": "Платформа + Клуб на 3 месяца",
        "lines": (("platform_subscription", 3_000, 3, False, ""), ("club_subscription", 7_500, 3, True, "пакет PRO + клуб (первый поток)")),
    },
}
RUB_PER_WWC = 100


def plan_lines(plan_code: str, currency: str) -> list[PaymentLine]:
    scale = RUB_PER_WWC if currency == "RUB" else 1
    return [
        PaymentLine(code, minor * scale, currency, months, promo, note)
        for code, minor, months, promo, note in PLANS[plan_code]["lines"]
    ]


def plan_total_minor(plan_code: str, currency: str) -> int:
    return sum(line.amount_minor for line in plan_lines(plan_code, currency))


async def set_site_request_plan(tenant_id: str, actor_id: str, plan_code: str) -> dict[str, Any]:
    plan = str(plan_code or "").strip().lower()
    if plan not in PLANS:
        raise SiteRequestError("Выберите вариант кнопкой.")
    async with tenant_connection(tenant_id) as conn:
        request = await fetch_one(
            conn,
            "select country_code from partner_site_requests where tenant_id = %s and actor_id = %s and status = 'awaiting_plan' for update",
            (tenant_id, actor_id),
        )
        if not request:
            raise SiteRequestError("Сейчас выбор пакета не ожидается.")
        currency = "RUB" if request["country_code"] == "RU" else "WUSD"
        lines = plan_lines(plan, currency)
        subscription = next(l.amount_minor for l in lines if l.product_code == "platform_subscription")
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set plan_code = %s, total_amount_minor = %s, subscription_amount_minor = %s, currency = %s,
                status = 'awaiting_payment', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_plan'
            returning *
            """,
            (plan, plan_total_minor(plan, currency), subscription, currency, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось сохранить выбор.")
    return row


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


async def subdomain_taken(conn: Any, tenant_id: str, subdomain: str, actor_id: str = "") -> bool:
    """Занят ли адрес <subdomain>.wwc.best.

    Сверяем не только ref_code: адрес сайта может отличаться от него (elena.wwc.best
    ведёт на onlineelena). 24.09.2026 бот принял elena от нового партнёра, а на сайте
    этот адрес давно у другого человека.
    """
    if subdomain in ISSUED_SUBDOMAIN_TO_REF:
        return True
    row = await fetch_one(
        conn,
        """
        select 1 as taken from referral_profiles
        where ref_code = %s or public_profile->>'subdomain' = %s
           or public_profile->>'public_site_url' like %s
           -- Старые адреса партнёра (svelaya → doronina, 25.09.2026) ведут на него же.
           or coalesce(public_profile->'retired_subdomains', '[]'::jsonb) ? %s
        union all
        select 1 as taken from partner_site_requests
        where tenant_id = %s and requested_subdomain = %s and actor_id <> %s
          and status not in ('rejected', 'cancelled')
        limit 1
        """,
        (subdomain, subdomain, f"https://{subdomain}.wwc.best%", subdomain, tenant_id, subdomain, actor_id),
    )
    return bool(row)


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
        if await subdomain_taken(conn, tenant_id, normalized, actor_id):
            raise SiteRequestError(
                f"Адрес {normalized}.wwc.best уже занят. Напишите другой вариант — "
                "например, добавьте фамилию или город."
            )
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
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set intro_text = %s, status = 'awaiting_contacts', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_text'
            returning *
            """,
            (value, tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Не удалось сохранить текст.")
    return row


async def set_site_request_contacts(
    tenant_id: str, actor_id: str, contacts_text: str
) -> dict[str, Any]:
    """Контакты для сайта одним сообщением (V12, 24.09.2026). «Нет» — тоже ответ:
    шаг закрывается с пустым разбором, сайт соберётся из Telegram."""
    value = str(contacts_text or "").strip()
    if not value:
        raise SiteRequestError("Пришлите контакты сообщением или напишите «нет».")
    if len(value) > 2000:
        raise SiteRequestError("Сообщение длиннее 2000 знаков. Пришлите покороче.")
    parsed = parse_contacts(value)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_site_requests
            set contacts_text = %s, contacts = %s::jsonb,
                status = 'awaiting_plan', updated_at = now()
            where tenant_id = %s and actor_id = %s and status = 'awaiting_contacts'
            returning *
            """,
            (value, json.dumps(parsed, ensure_ascii=False), tenant_id, actor_id),
        )
    if not row:
        raise SiteRequestError("Сейчас контакты не ожидаются.")
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
        # Адрес могли занять, пока заявка ждала оплаты, — ловим до записи денег.
        if await subdomain_taken(conn, tenant_id, str(request["requested_subdomain"]), str(request["actor_id"])):
            raise SiteRequestError("Имя сайта уже занято. Заявку нужно проверить вручную.")
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
        # Профиль до сборки сайта выключен (enabled = false), а строка подписки
        # создаётся платёжным путём только для включённых — заявка падала на
        # «active referral profile not found» и подтверждение никогда не проходило
        # (найдено тестом 19.09.2026). Строку заводим здесь; сроки проставит платёж.
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                values (%s, %s, null)
                on conflict (tenant_id, ref_code) do nothing
                """,
                (tenant_id, request["requested_subdomain"]),
            )
        # Одна оплата — несколько строк (сайт + настройка или сайт + клуб): так же, как
        # владелец записывает вручную через «оплата ref:… / пакет …». Настройка сайта
        # раньше в ledger не попадала, и 2 000 ₽ терялись в отчётах.
        plan = str(request.get("plan_code") or "site")
        currency = str(request["currency"])
        result = await record_payment_lines_in_connection(
            conn,
            tenant_id=tenant_id,
            ref_code=str(request["requested_subdomain"]),
            lines=plan_lines(plan, currency),
            received_minor=plan_total_minor(plan, currency),
            currency=currency,
            telegram_chat_id=int(request["proof_chat_id"]),
            telegram_message_id=int(request["proof_message_id"]),
            telegram_user_id=int(request["telegram_user_id"]),
        )
        pro = next((l for l in result["lines"] if l["product_code"] == "platform_subscription"), result["lines"][0])
        payment = {**pro, "referral_bonus": result.get("referral_bonus"), "club_paid_until": result.get("club_paid_until")}
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
