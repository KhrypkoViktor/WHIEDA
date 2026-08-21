"""No blind zone: guided unresolved responses and gap capture."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.advisor.sql.text import normalize_text
from app.db import tenant_connection

logger = logging.getLogger(__name__)

GAP_DEDUP_WINDOW_SECONDS = 30 * 60

GAP_KINDS = frozenset(
    {
        "unknown_product",
        "ambiguous_product",
        "unknown_followup",
        "unsupported_topic",
        "missing_resource",
        "medical_or_safety_boundary",
        "unrouted_message",
    }
)

PROHIBITED_USER_FRAGMENTS = (
    "не знаю",
    "не смог обработать",
    "нет в базе",
    "передам на проверку",
    "needs human review",
    "this needs human review",
    "передам вопрос команде",
    "нет подтверждённого ответа в базе",
)

GAP_TEXTS: dict[str, str] = {
    "unknown_product": (
        "Я лучше всего помогаю с товарами WHIEDA, ценами и PV, применением, "
        "подбором и бизнесом. Выберите направление — названия товаров знать не обязательно.\n\n"
        "📦 Товары — каталог, карточка, фото, видео и сравнение\n"
        "🧮 Калькулятор — несколько товаров, цена и PV\n"
        "🧭 Подбор — опишите задачу, помогу выбрать направление\n"
        "📈 Бизнес — старт, повторка, PV и маркетинг-план\n"
        "🏢 Компания — продукты, события и встречи"
    ),
    "unrouted_message": (
        "Я лучше всего помогаю с товарами WHIEDA, ценами и PV, применением, "
        "подбором и бизнесом. Выберите направление — названия товаров знать не обязательно.\n\n"
        "📦 Товары — каталог, карточка, фото, видео и сравнение\n"
        "🧮 Калькулятор — несколько товаров, цена и PV\n"
        "🧭 Подбор — опишите задачу, помогу выбрать направление\n"
        "📈 Бизнес — старт, повторка, PV и маркетинг-план\n"
        "🏢 Компания — продукты, события и встречи"
    ),
    "ambiguous_product": (
        "Нужно уточнить, о каком товаре речь — тогда смогу дать цену, карточку или фото."
    ),
    "unknown_followup": (
        "Я лучше всего помогаю с товарами WHIEDA, ценами и PV, применением, "
        "подбором и бизнесом. Выберите направление — названия товаров знать не обязательно.\n\n"
        "📦 Товары — каталог, карточка, фото, видео и сравнение\n"
        "🧮 Калькулятор — несколько товаров, цена и PV\n"
        "🧭 Подбор — опишите задачу, помогу выбрать направление\n"
        "📈 Бизнес — старт, повторка, PV и маркетинг-план\n"
        "🏢 Компания — продукты, события и встречи"
    ),
    "unsupported_topic": (
        "Я лучше всего помогаю с товарами WHIEDA, ценами и PV, применением, "
        "подбором и бизнесом. Выберите направление — названия товаров знать не обязательно.\n\n"
        "📦 Товары — каталог, карточка, фото, видео и сравнение\n"
        "🧮 Калькулятор — несколько товаров, цена и PV\n"
        "🧭 Подбор — опишите задачу, помогу выбрать направление\n"
        "📈 Бизнес — старт, повторка, PV и маркетинг-план\n"
        "🏢 Компания — продукты, события и встречи"
    ),
    "missing_resource": (
        "Для этого товара такой материал пока не прикреплён. "
        "Могу показать карточку, цену или другое доступное фото/видео."
    ),
    "medical_or_safety_boundary": (
        "Любой прибор или продукт WHIEDA не заменяет схему лечения диагноза. "
        "Уточните задачу — подскажу по применению и ограничениям из карточки товара."
    ),
}

NEXT_STEPS: dict[str, list[str]] = {
    "unknown_product": [
        "Назовите товар или артикул",
        "Спросите цену или PV",
        "Попросите сравнение или корзину",
    ],
    "ambiguous_product": [
        "Уточните полное название",
        "Выберите вариант из подсказки",
        "Спросите цену после выбора",
    ],
    "unknown_followup": [
        "Откройте товары или назовите товар",
        "Спросите цену, PV или фото",
        "Опишите задачу для подбора",
    ],
    "unsupported_topic": [
        "Спросите про конкретный товар",
        "Уточните бизнес-вопрос (PV, повторка)",
        "Попросите акции или события",
    ],
    "missing_resource": [
        "Посмотрите карточку товара",
        "Уточните цену",
        "Запросите другое доступное фото/видео",
    ],
    "medical_or_safety_boundary": [
        "Спросите ограничения из карточки",
        "Уточните применение по инструкции",
        "Назовите товар для безопасного ответа",
    ],
    "unrouted_message": [
        "Назовите товар",
        "Спросите про цену, PV или старт",
        "Опишите задачу своими словами",
    ],
}

ANSWER_MODE_BY_KIND: dict[str, str] = {
    "unknown_product": "knowledge_gap",
    "ambiguous_product": "clarification",
    "unknown_followup": "clarification",
    "unsupported_topic": "clarification",
    "missing_resource": "clarification",
    "medical_or_safety_boundary": "clarification",
    "unrouted_message": "clarification",
}


def assert_no_prohibited_fragments(text: str) -> None:
    lowered = str(text or "").lower()
    for fragment in PROHIBITED_USER_FRAGMENTS:
        if fragment in lowered:
            raise ValueError(f"prohibited fragment {fragment!r} in user text")


def sanitize_user_text(text: str, *, fallback_kind: str = "unknown_product") -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return GAP_TEXTS[fallback_kind]
    try:
        assert_no_prohibited_fragments(cleaned)
    except ValueError:
        return GAP_TEXTS[fallback_kind]
    return cleaned


def build_next_steps(gap_kind: str) -> list[str]:
    return list(NEXT_STEPS.get(gap_kind, NEXT_STEPS["unknown_product"])[:3])


def build_gap_response(
    gap_kind: str,
    trace_id: str,
    *,
    text: str | None = None,
    answer_mode: str | None = None,
    product: dict[str, Any] | None = None,
    clarifications: list[str] | None = None,
    context: dict[str, Any] | None = None,
    media: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.advisor.sql import formatters as fmt

    if gap_kind not in GAP_KINDS:
        raise ValueError(f"unknown gap_kind: {gap_kind}")
    answer = sanitize_user_text(text or GAP_TEXTS[gap_kind], fallback_kind=gap_kind)
    mode = answer_mode or ANSWER_MODE_BY_KIND.get(gap_kind, "knowledge_gap")
    steps = build_next_steps(gap_kind)
    response = fmt.ok_response(
        answer,
        mode,
        trace_id,
        product=product,
        media=media if media is not None else fmt.empty_media(),
        clarifications=clarifications,
        context=context,
        gap_kind=gap_kind,
        next_steps=steps,
    )
    return response


def _session_ref_hash(session: str) -> str:
    digest = hashlib.sha256(str(session or "").encode("utf-8")).hexdigest()
    return digest[:16]


def _idempotency_key(
    tenant_id: str,
    session: str,
    gap_kind: str,
    question_normalized: str,
    *,
    now: datetime | None = None,
) -> str:
    moment = now or datetime.now(timezone.utc)
    bucket = int(moment.timestamp()) // GAP_DEDUP_WINDOW_SECONDS
    raw = f"{tenant_id}|{_session_ref_hash(session)}|{gap_kind}|{question_normalized}|{bucket}"
    if len(raw) <= 160:
        return f"advisor_gap:{raw}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"advisor_gap:{tenant_id}:{digest[:48]}"


def _gap_payload(
    *,
    channel: str,
    session_ref: str,
    question_normalized: str,
    gap_kind: str,
    detected_product: str | None,
    trace_id: str,
    answer_mode: str,
    occurred_at: str,
) -> dict[str, Any]:
    return {
        "channel": channel[:32],
        "session_ref": _session_ref_hash(session_ref),
        "question_normalized": question_normalized[:500],
        "gap_kind": gap_kind,
        "detected_product": detected_product,
        "trace_id": trace_id,
        "answer_mode": answer_mode,
        "repeat_count": 1,
        "first_seen_at": occurred_at,
        "last_seen_at": occurred_at,
    }


async def record_advisor_gap(
    tenant_id: str,
    *,
    session: str,
    question: str,
    gap_kind: str,
    trace_id: str,
    answer_mode: str,
    channel: str = "advisor",
    detected_product: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Persist one idempotent advisor_gap event; never raises to caller."""
    if gap_kind not in GAP_KINDS:
        return None
    normalized = normalize_text(question)
    if not normalized:
        return None
    moment = now or datetime.now(timezone.utc)
    idem = _idempotency_key(tenant_id, session, gap_kind, normalized, now=moment)
    payload = _gap_payload(
        channel=channel,
        session_ref=session,
        question_normalized=normalized,
        gap_kind=gap_kind,
        detected_product=detected_product,
        trace_id=trace_id,
        answer_mode=answer_mode,
        occurred_at=moment.isoformat(),
    )
    event_id = str(uuid.uuid4())
    try:
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into interaction_events (
                      event_id, tenant_id, session_id, event_type, idempotency_key, payload
                    ) values (%s::uuid, %s, null, 'advisor_gap', %s, %s::jsonb)
                    on conflict (tenant_id, idempotency_key) do update
                      set payload = jsonb_set(
                        jsonb_set(
                          interaction_events.payload,
                          '{repeat_count}',
                          to_jsonb(
                            coalesce((interaction_events.payload->>'repeat_count')::int, 1) + 1
                          )
                        ),
                        '{last_seen_at}',
                        to_jsonb(%s::text)
                      )
                    returning event_id, (xmax = 0) as inserted
                    """,
                    (
                        event_id,
                        tenant_id,
                        idem,
                        json.dumps(payload, ensure_ascii=False),
                        moment.isoformat(),
                    ),
                )
                row = await cur.fetchone()
        if row:
            event_id_value = row["event_id"] if isinstance(row, dict) else row[0]
            inserted_value = row["inserted"] if isinstance(row, dict) else row[1]
            return {
                "event_id": str(event_id_value),
                "created": bool(inserted_value),
                "idempotency_key": idem,
            }
    except Exception as exc:
        logger.warning(
            "advisor_gap_write_failed tenant=%s gap_kind=%s trace=%s err=%s",
            tenant_id,
            gap_kind,
            trace_id,
            exc,
        )
    return None


async def emit_gap_response(
    tenant_id: str,
    *,
    session: str,
    question: str,
    gap_kind: str,
    trace_id: str,
    channel: str = "advisor",
    text: str | None = None,
    answer_mode: str | None = None,
    product: dict[str, Any] | None = None,
    clarifications: list[str] | None = None,
    context: dict[str, Any] | None = None,
    detected_product: str | None = None,
) -> dict[str, Any]:
    from app.advisor.voice import gap_text_for

    response = build_gap_response(
        gap_kind,
        trace_id,
        text=text or gap_text_for(tenant_id, gap_kind),
        answer_mode=answer_mode,
        product=product,
        clarifications=clarifications,
        context=context,
    )
    product_name = detected_product
    if not product_name and product:
        product_name = str(product.get("canonical_name") or product.get("sku") or "") or None
    await record_advisor_gap(
        tenant_id,
        session=session,
        question=question,
        gap_kind=gap_kind,
        trace_id=trace_id,
        answer_mode=str(response.get("answer_mode") or ""),
        channel=channel,
        detected_product=product_name,
    )
    return response


def contains_prohibited_fragment(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(fragment in lowered for fragment in PROHIBITED_USER_FRAGMENTS)


async def fetch_gap_operator_summary(tenant_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read-only grouped gap summary for local operator reports."""
    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_all

        rows = await fetch_all(
            conn,
            """
            select
              payload->>'gap_kind' as gap_kind,
              payload->>'question_normalized' as question_normalized,
              sum(coalesce((payload->>'repeat_count')::int, 1))::int as total_count,
              max(coalesce(nullif(payload->>'last_seen_at', '')::timestamptz, created_at)) as last_seen
            from interaction_events
            where tenant_id = %s and event_type = 'advisor_gap'
            group by 1, 2
            order by total_count desc, last_seen desc
            limit %s
            """,
            (tenant_id, limit),
        )
    return [dict(row) for row in rows]
