"""Idempotent partner subscription reminder selection and delivery claims."""

from __future__ import annotations

from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection


async def list_due_reminders(tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select
              rp.ref_code,
              la.display_name,
              la.telegram_chat_id,
              rp.country_code,
              ps.paid_until,
              events.event_type,
              events.event_at,
              coalesce(nullif(rp.public_profile->>'subdomain', ''), rp.ref_code) || '.wwc.best'
                as hostname
            from partner_subscriptions ps
            join referral_profiles rp
              on rp.tenant_id = ps.tenant_id and rp.ref_code = ps.ref_code and rp.enabled = true
            join lead_actors la
              on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id and la.active = true
            cross join lateral (
              values
                ('due_7d'::text, ps.paid_until - interval '7 days'),
                ('grace_start'::text, ps.paid_until),
                ('grace_last'::text, ps.paid_until + interval '2 days')
            ) events(event_type, event_at)
            where ps.tenant_id = %s
              and ps.paid_until is not null
              and la.telegram_chat_id is not null
              and rp.country_code in ('BY', 'RU')
              and events.event_at <= now()
              and events.event_at > now() - interval '2 days'
              and not exists (
                select 1 from partner_subscription_reminder_log log
                where log.tenant_id = ps.tenant_id
                  and log.ref_code = ps.ref_code
                  and log.event_type = events.event_type
                  and log.paid_until = ps.paid_until
                  and log.status = 'sent'
              )
            union all
            -- CLUB: учёт + напоминания за 7/3/1 день партнёру (владелец, 13.09.2026).
            select
              rp.ref_code,
              la.display_name,
              la.telegram_chat_id,
              rp.country_code,
              pa.paid_until,
              events.event_type,
              events.event_at,
              coalesce(nullif(rp.public_profile->>'subdomain', ''), rp.ref_code) || '.wwc.best'
                as hostname
            from partner_product_access pa
            join referral_profiles rp
              on rp.tenant_id = pa.tenant_id and rp.ref_code = pa.ref_code and rp.enabled = true
            join lead_actors la
              on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id and la.active = true
            cross join lateral (
              values
                ('club_due_7d'::text, pa.paid_until - interval '7 days'),
                ('club_due_3d'::text, pa.paid_until - interval '3 days'),
                ('club_due_1d'::text, pa.paid_until - interval '1 day')
            ) events(event_type, event_at)
            where pa.tenant_id = %s
              and pa.product_code = 'club_subscription'
              and pa.paid_until is not null
              and la.telegram_chat_id is not null
              and events.event_at <= now()
              and events.event_at > now() - interval '2 days'
              and not exists (
                select 1 from partner_subscription_reminder_log log
                where log.tenant_id = pa.tenant_id
                  and log.ref_code = pa.ref_code
                  and log.event_type = events.event_type
                  and log.paid_until = pa.paid_until
                  and log.status = 'sent'
              )
            order by event_at, ref_code
            limit %s
            """,
            (tenant_id, tenant_id, safe_limit),
        )


async def claim_reminder(tenant_id: str, reminder: dict[str, Any]) -> str | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into partner_subscription_reminder_log (
              tenant_id, ref_code, event_type, paid_until, telegram_chat_id, status
            ) values (%s, %s, %s, %s, %s, 'sending')
            on conflict (tenant_id, ref_code, event_type, paid_until) do update
            set status = 'sending', attempt_count = partner_subscription_reminder_log.attempt_count + 1,
                telegram_chat_id = excluded.telegram_chat_id, last_attempt_at = now(), error_text = null
            where partner_subscription_reminder_log.status = 'failed'
               or (
                 partner_subscription_reminder_log.status = 'sending'
                 and partner_subscription_reminder_log.last_attempt_at < now() - interval '15 minutes'
               )
            returning delivery_id
            """,
            (
                tenant_id,
                reminder["ref_code"],
                reminder["event_type"],
                reminder["paid_until"],
                int(reminder["telegram_chat_id"]),
            ),
        )
    return str(row["delivery_id"]) if row else None


async def mark_reminder_sent(
    tenant_id: str, delivery_id: str, *, telegram_message_id: int | None
) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update partner_subscription_reminder_log
            set status = 'sent', telegram_message_id = %s, sent_at = now(), error_text = null
            where tenant_id = %s and delivery_id = %s::uuid
            returning delivery_id
            """,
            (telegram_message_id, tenant_id, delivery_id),
        )


async def mark_reminder_failed(tenant_id: str, delivery_id: str, *, error: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update partner_subscription_reminder_log
            set status = 'failed', error_text = %s
            where tenant_id = %s and delivery_id = %s::uuid
            returning delivery_id
            """,
            (str(error or "delivery failed")[:500], tenant_id, delivery_id),
        )


def build_due_reminder_text(reminder: dict[str, Any]) -> str:
    name = str(reminder.get("display_name") or reminder["ref_code"]).strip()
    hostname = str(reminder["hostname"])
    event_type = str(reminder["event_type"])
    if event_type.startswith("club_due_"):
        days = {"club_due_7d": "7 дней", "club_due_3d": "3 дня", "club_due_1d": "1 день"}[event_type]
        until = reminder["paid_until"]
        until_text = until.strftime("%d.%m.%Y") if hasattr(until, "strftime") else str(until)
        return "\n".join(
            [
                f"{name}, CLUB заканчивается через {days} (до {until_text}).",
                "Продление — 120 WWC$ (12 000 ₽) за 3 месяца. Напишите Виктору, чтобы продлить.",
            ]
        )
    if event_type == "due_7d":
        lead = f"{name}, до окончания доступа к {hostname} осталось 7 дней."
    elif event_type == "grace_start":
        lead = f"{name}, оплаченный период {hostname} завершился. Сайт работает ещё 3 дня."
    else:
        lead = f"{name}, сегодня последний день льготного доступа к {hostname}."
    return "\n".join(
        [
            lead,
            "Продлить платформу можно в боте: откройте /cabinet и нажмите «Продлить платформу».",
            "После подтверждения оплаты срок обновится автоматически.",
        ]
    )
