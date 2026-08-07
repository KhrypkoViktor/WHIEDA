"""7-day onboarding state machine (Stage 5)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.db import fetch_all, fetch_one, tenant_connection
from app.onboarding.commands import parse_onboarding_command
from app.onboarding.reminders import schedule_next_reminder
from app.ref.service import load_public_ref

DEFAULT_PROGRAM_KEY = "first_week_sql_v1"
MAX_HELP_TEXT = 500


@dataclass
class OnboardingHandleResult:
    answer_text: str
    enrollment_id: str | None
    current_day: int | None
    status: str | None
    escalation_created: bool = False


def _clean(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


async def _load_active_program(tenant_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select program_id, program_key, title
            from onboarding_programs
            where tenant_id = %s and status = 'active'
            order by version desc
            limit 1
            """,
            (tenant_id,),
        )


async def _load_step(tenant_id: str, program_id: str, day_number: int) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select step_id, day_number, title, body_text, next_hint
            from onboarding_steps
            where tenant_id = %s and program_id = %s::uuid and day_number = %s
            limit 1
            """,
            (tenant_id, program_id, day_number),
        )


async def _load_enrollment(tenant_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select enrollment_id, program_id, status, current_day, first_ref,
                   assigned_owner_id, reminders_paused
            from onboarding_enrollments
            where tenant_id = %s and telegram_user_id = %s
            limit 1
            """,
            (tenant_id, telegram_user_id),
        )


async def enroll_user(
    tenant_id: str,
    *,
    telegram_user_id: int,
    first_ref: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    program = await _load_active_program(tenant_id)
    if not program:
        raise HTTPException(status_code=503, detail={"ok": False, "error": "onboarding_program_unavailable"})

    owner_id = None
    if first_ref:
        profile = await load_public_ref(tenant_id, first_ref)
        if profile:
            owner_id = str(profile.get("owner_id") or "") or None

    enrollment_id = str(uuid.uuid4())
    idem = _clean(idempotency_key, 160) or f"enroll:{tenant_id}:{telegram_user_id}"

    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select enrollment_id, status, current_day
            from onboarding_enrollments
            where tenant_id = %s and (idempotency_key = %s or telegram_user_id = %s)
            limit 1
            """,
            (tenant_id, idem, telegram_user_id),
        )
        if existing:
            return {
                "ok": True,
                "created": False,
                "enrollment_id": str(existing["enrollment_id"]),
                "status": existing["status"],
                "current_day": existing["current_day"],
            }

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into onboarding_enrollments (
                  enrollment_id, tenant_id, program_id, telegram_user_id,
                  first_ref, assigned_owner_id, status, current_day, idempotency_key
                ) values (
                  %s::uuid, %s, %s::uuid, %s,
                  %s, %s, 'active', 1, %s
                )
                """,
                (
                    enrollment_id,
                    tenant_id,
                    program["program_id"],
                    telegram_user_id,
                    first_ref,
                    owner_id,
                    idem,
                ),
            )

    step = await _load_step(tenant_id, str(program["program_id"]), 1)
    intro = step["body_text"] if step else "Обучение начато."
    await schedule_next_reminder(tenant_id, enrollment_id, 1)
    return {
        "ok": True,
        "created": True,
        "enrollment_id": enrollment_id,
        "status": "active",
        "current_day": 1,
        "answer_text": f"{step['title'] if step else 'День 1'}\n\n{intro}",
    }


async def mark_day_done(tenant_id: str, enrollment: dict[str, Any]) -> OnboardingHandleResult:
    day = int(enrollment["current_day"])
    program_id = str(enrollment["program_id"])
    enrollment_id = str(enrollment["enrollment_id"])
    step = await _load_step(tenant_id, program_id, day)
    if not step:
        return OnboardingHandleResult(
            answer_text="Шаг не найден. Напишите «мой план».",
            enrollment_id=enrollment_id,
            current_day=day,
            status=enrollment["status"],
        )

    progress_idem = f"progress:{enrollment_id}:day:{day}"
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into onboarding_progress (
                  progress_id, tenant_id, enrollment_id, step_id, day_number,
                  status, completed_at, idempotency_key
                ) values (
                  %s::uuid, %s, %s::uuid, %s::uuid, %s,
                  'done', now(), %s
                )
                on conflict (tenant_id, idempotency_key) do nothing
                """,
                (str(uuid.uuid4()), tenant_id, enrollment_id, step["step_id"], day, progress_idem),
            )

            next_day = min(day + 1, 7)
            new_status = "completed" if next_day > 7 or day >= 7 else "active"
            await cur.execute(
                """
                update onboarding_enrollments
                set current_day = %s,
                    status = %s,
                    completed_at = case when %s = 'completed' then now() else completed_at end,
                    updated_at = now()
                where tenant_id = %s and enrollment_id = %s::uuid
                """,
                (next_day if new_status != "completed" else 7, new_status, new_status, tenant_id, enrollment_id),
            )

    if new_status == "completed":
        return OnboardingHandleResult(
            answer_text="Поздравляю! Первые 7 дней завершены. Можно повторить программу или углубиться в каталог.",
            enrollment_id=enrollment_id,
            current_day=7,
            status="completed",
        )

    await schedule_next_reminder(tenant_id, enrollment_id, next_day if new_status != "completed" else 7)

    next_step = await _load_step(tenant_id, program_id, next_day)
    text = f"День {day} отмечен выполненным.\n\n{next_step['title']}\n\n{next_step['body_text']}" if next_step else "День отмечен."
    return OnboardingHandleResult(
        answer_text=text,
        enrollment_id=enrollment_id,
        current_day=next_day,
        status="active",
    )


async def create_escalation(
    tenant_id: str,
    enrollment: dict[str, Any],
    question_text: str,
) -> OnboardingHandleResult:
    safe_text = _clean(question_text, MAX_HELP_TEXT) or "Запрос помощи без текста"
    enrollment_id = str(enrollment["enrollment_id"])
    day = int(enrollment["current_day"])
    idem = f"escalation:{enrollment_id}:{day}"

    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select escalation_id, status
            from mentor_escalations
            where tenant_id = %s and idempotency_key = %s
            limit 1
            """,
            (tenant_id, idem),
        )
        if existing:
            return OnboardingHandleResult(
                answer_text=(
                    "Запрос уже отправлен наставнику. Ответ может занять время — "
                    "мы не обещаем мгновенную реакцию."
                ),
                enrollment_id=enrollment_id,
                current_day=day,
                status=enrollment["status"],
                escalation_created=False,
            )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into mentor_escalations (
                  escalation_id, tenant_id, enrollment_id, day_number,
                  question_text, status, idempotency_key
                ) values (%s::uuid, %s, %s::uuid, %s, %s, 'open', %s)
                """,
                (str(uuid.uuid4()), tenant_id, enrollment_id, day, safe_text, idem),
            )

    return OnboardingHandleResult(
        answer_text=(
            "Передал запрос наставнику. Ответ может занять время — статус «ожидает ответа»."
        ),
        enrollment_id=enrollment_id,
        current_day=day,
        status=enrollment["status"],
        escalation_created=True,
    )


async def build_plan_text(tenant_id: str, enrollment: dict[str, Any]) -> str:
    program_id = str(enrollment["program_id"])
    async with tenant_connection(tenant_id) as conn:
        steps = await fetch_all(
            conn,
            """
            select day_number, title, status
            from onboarding_steps s
            left join onboarding_progress p
              on p.tenant_id = s.tenant_id
             and p.enrollment_id = %s::uuid
             and p.day_number = s.day_number
            where s.tenant_id = %s and s.program_id = %s::uuid
            order by s.day_number
            """,
            (enrollment["enrollment_id"], tenant_id, program_id),
        )
    lines = [f"План обучения — день {enrollment['current_day']} из 7", ""]
    for row in steps:
        mark = "✓" if row.get("status") == "done" else "·"
        lines.append(f"{mark} День {row['day_number']}: {row['title']}")
    return "\n".join(lines)


async def handle_onboarding_text(
    tenant_id: str,
    *,
    telegram_user_id: int,
    text: str,
    first_ref: str | None = None,
) -> dict[str, Any] | None:
    parsed = parse_onboarding_command(text)
    if not parsed:
        return None

    command = parsed["command"]
    enrollment = await _load_enrollment(tenant_id, telegram_user_id)

    if command == "start":
        return await enroll_user(
            tenant_id,
            telegram_user_id=telegram_user_id,
            first_ref=first_ref,
            idempotency_key=f"enroll:{telegram_user_id}",
        )

    if not enrollment:
        return {
            "ok": True,
            "answer_text": "Сначала напишите «начать обучение».",
            "enrollment_id": None,
        }

    if command == "plan":
        return {
            "ok": True,
            "answer_text": await build_plan_text(tenant_id, enrollment),
            "enrollment_id": str(enrollment["enrollment_id"]),
            "current_day": enrollment["current_day"],
            "status": enrollment["status"],
        }

    if command == "done":
        result = await mark_day_done(tenant_id, enrollment)
        return {
            "ok": True,
            "answer_text": result.answer_text,
            "enrollment_id": result.enrollment_id,
            "current_day": result.current_day,
            "status": result.status,
        }

    if command == "help":
        result = await create_escalation(
            tenant_id,
            enrollment,
            parsed.get("question_text") or text,
        )
        return {
            "ok": True,
            "answer_text": result.answer_text,
            "enrollment_id": result.enrollment_id,
            "escalation_created": result.escalation_created,
        }

    if command == "postpone":
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update onboarding_enrollments
                    set status = 'paused', paused_at = now(), updated_at = now()
                    where tenant_id = %s and enrollment_id = %s::uuid
                    """,
                    (tenant_id, enrollment["enrollment_id"]),
                )
        return {
            "ok": True,
            "answer_text": "Обучение приостановлено. Напишите «продолжить обучение», когда будете готовы.",
            "status": "paused",
        }

    if command == "resume":
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update onboarding_enrollments
                    set status = 'active', paused_at = null, updated_at = now()
                    where tenant_id = %s and enrollment_id = %s::uuid
                    """,
                    (tenant_id, enrollment["enrollment_id"]),
                )
        step = await _load_step(tenant_id, str(enrollment["program_id"]), int(enrollment["current_day"]))
        body = step["body_text"] if step else ""
        return {
            "ok": True,
            "answer_text": f"Продолжаем день {enrollment['current_day']}.\n\n{body}",
            "status": "active",
        }

    if command == "pause_reminders":
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update onboarding_enrollments
                    set reminders_paused = true, updated_at = now()
                    where tenant_id = %s and enrollment_id = %s::uuid
                    """,
                    (tenant_id, enrollment["enrollment_id"]),
                )
        return {"ok": True, "answer_text": "Напоминания отключены."}

    if command == "mentor":
        ref = enrollment.get("first_ref")
        name = None
        if ref:
            profile = await load_public_ref(tenant_id, str(ref))
            if profile:
                name = (profile.get("public_profile") or {}).get("display_name")
        if name:
            return {"ok": True, "answer_text": f"Ваш наставник: {name}."}
        return {"ok": True, "answer_text": "Наставник будет назначен по вашей ref-ссылке."}

    return None
