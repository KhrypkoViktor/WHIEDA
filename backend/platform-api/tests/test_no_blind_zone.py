"""No blind zone: guided gaps, prohibited fragments, persistence."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.advisor.gap import (
    GAP_KINDS,
    PROHIBITED_USER_FRAGMENTS,
    build_gap_response,
    build_next_steps,
    emit_gap_response,
    record_advisor_gap,
    sanitize_user_text,
    _idempotency_key,
)
from app.advisor.sql.text import is_unsupported_topic
from app.advisor.sql.engine import run_structured_query
from app.tenancy import TenantContext

WHIEDA = TenantContext(
    tenant_id="whieda",
    status="active",
    display_name="WHIEDA",
    entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
)

ACTIVATOR = {
    "sku": "LOCAL-ACT",
    "canonical_name": "Активатор клеток",
    "retail_price_byn": 1750,
    "partner_w": 300,
}


def _fake_tenant_connection(conn):
    @asynccontextmanager
    async def _cm(_tenant_id: str):
        yield conn

    return _cm


@pytest.mark.parametrize("gap_kind", sorted(GAP_KINDS))
def test_build_gap_response_all_kinds(gap_kind: str):
    response = build_gap_response(gap_kind, "trace-1")
    assert response["gap_kind"] == gap_kind
    assert response["next_steps"]
    assert len(response["next_steps"]) <= 3
    lowered = response["answer_text"].lower()
    for fragment in PROHIBITED_USER_FRAGMENTS:
        assert fragment not in lowered


def test_sanitize_user_text_replaces_prohibited_seed():
    bad = "Пока нет подтверждённого ответа в базе WHIEDA. Передам вопрос команде."
    cleaned = sanitize_user_text(bad, fallback_kind="unknown_product")
    assert "передам" not in cleaned.lower()
    assert "каталог" in cleaned.lower()


def test_gap_deduplication_expires_after_short_window():
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    first = _idempotency_key("whieda", "session-1", "unknown_product", "xyz", now=now)
    same_window = _idempotency_key(
        "whieda", "session-1", "unknown_product", "xyz", now=now + timedelta(minutes=29)
    )
    next_window = _idempotency_key(
        "whieda", "session-1", "unknown_product", "xyz", now=now + timedelta(minutes=31)
    )
    assert first == same_window
    assert next_window != first


@pytest.mark.parametrize("question", ["погода в Минске", "как инвестировать в биржу", "курс валют"])
def test_plainly_external_topics_are_not_product_names(question: str):
    assert is_unsupported_topic(question)


@pytest.mark.asyncio
async def test_record_advisor_gap_deduplicates():
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(side_effect=[("e1", True), ("e1", False)])
    cursor_cm = AsyncMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    cursor_cm.__aexit__ = AsyncMock(return_value=False)
    conn.cursor = MagicMock(return_value=cursor_cm)

    with patch("app.advisor.gap.tenant_connection", _fake_tenant_connection(conn)):
        first = await record_advisor_gap(
            "whieda",
            session="s1",
            question="цена",
            gap_kind="unknown_followup",
            trace_id="t1",
            answer_mode="clarification",
        )
        second = await record_advisor_gap(
            "whieda",
            session="s1",
            question="цена",
            gap_kind="unknown_followup",
            trace_id="t2",
            answer_mode="clarification",
        )

    assert first and first["created"] is True
    assert second and second["created"] is False
    assert cursor.execute.await_count == 2


@pytest.mark.asyncio
async def test_record_advisor_gap_failure_does_not_raise():
    with patch("app.advisor.gap.tenant_connection", side_effect=RuntimeError("db down")):
        result = await record_advisor_gap(
            "whieda",
            session="s1",
            question="xyz",
            gap_kind="unknown_product",
            trace_id="t1",
            answer_mode="knowledge_gap",
        )
    assert result is None


@pytest.mark.asyncio
async def test_emit_gap_response_returns_text_on_persist_failure():
    with patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value=None)):
        response = await emit_gap_response(
            "whieda",
            session="s1",
            question="xyzunknown123",
            gap_kind="unknown_product",
            trace_id="trace-x",
        )
    assert response["answer_mode"] == "knowledge_gap"
    assert response["gap_kind"] == "unknown_product"
    assert response["next_steps"]


@pytest.mark.asyncio
async def test_unknown_product_no_prohibited_fragments():
    conn = AsyncMock()

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_clarification_prompt",
                                    AsyncMock(return_value=None),
                                ):
                                    with patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value={})):
                                        result = await run_structured_query(
                                            WHIEDA,
                                            {
                                                "question": "расскажи про xyzunknown123",
                                                "session": "nbz-unit-1",
                                            },
                                            "trace-nbz-1",
                                        )

    assert result["answer_mode"] == "knowledge_gap"
    assert result["gap_kind"] == "unknown_product"
    lowered = result["answer_text"].lower()
    for fragment in PROHIBITED_USER_FRAGMENTS:
        assert fragment not in lowered


@pytest.mark.asyncio
async def test_typo_resolves_without_gap():
    conn = AsyncMock()

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=ACTIVATOR)):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_product_card",
                                    AsyncMock(
                                        return_value={
                                            "what_it_is": "Тест.",
                                            "primary_image_url": "https://example.invalid/a.jpg",
                                        }
                                    ),
                                ):
                                    with patch(
                                        "app.advisor.sql.engine.session_ctx.merge_session_context",
                                        AsyncMock(),
                                    ):
                                        with patch("app.advisor.gap.record_advisor_gap", AsyncMock()) as gap_mock:
                                            result = await run_structured_query(
                                                WHIEDA,
                                                {"question": "ативатор", "session": "nbz-typo"},
                                                "trace-nbz-2",
                                            )

    assert result["answer_mode"] == "structured_card"
    assert "gap_kind" not in result or result.get("gap_kind") is None
    gap_mock.assert_not_called()


@pytest.mark.asyncio
async def test_bare_price_unknown_followup():
    conn = AsyncMock()

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value={})):
                                    result = await run_structured_query(
                                        WHIEDA,
                                        {"question": "цена", "session": "nbz-followup"},
                                        "trace-nbz-3",
                                    )

    assert result["gap_kind"] == "unknown_followup"
    assert result["next_steps"]


@pytest.mark.asyncio
async def test_ambiguous_pasta_gap_kind():
    conn = AsyncMock()
    ambiguity = (
        "Вы про зубную пасту с экстрактом полыни или Пасту Цинфэн?",
        "clarification",
        ["product_ambiguity_paste"],
    )

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=ambiguity),
                            ):
                                with patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value={})):
                                    result = await run_structured_query(
                                        WHIEDA,
                                        {"question": "паста", "session": "nbz-pasta"},
                                        "trace-nbz-4",
                                    )

    assert result["gap_kind"] == "ambiguous_product"


@pytest.mark.asyncio
async def test_safety_medical_boundary():
    conn = AsyncMock()

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value={})):
                            result = await run_structured_query(
                                WHIEDA,
                                {"question": "как лечить диабет активатором", "session": "nbz-safe"},
                                "trace-nbz-5",
                            )

    assert result["gap_kind"] == "medical_or_safety_boundary"
    assert "лечен" not in result["answer_text"].lower() or "не замен" in result["answer_text"].lower()


@pytest.mark.asyncio
async def test_missing_resource_known_product():
    conn = AsyncMock()
    product = {
        "sku": "LOCAL-NOPHOTO",
        "canonical_name": "Товар без фото (тест)",
        "retail_price_byn": 525,
        "partner_w": 150,
    }

    with patch("app.advisor.sql.engine.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=product)):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_product_card",
                                    AsyncMock(return_value={"primary_image_url": None}),
                                ):
                                    with patch(
                                        "app.advisor.sql.engine.repo.load_product_resources",
                                        AsyncMock(return_value=[]),
                                    ):
                                        with patch(
                                            "app.advisor.sql.engine.session_ctx.merge_session_context",
                                            AsyncMock(),
                                        ):
                                            with patch(
                                                "app.advisor.gap.record_advisor_gap",
                                                AsyncMock(return_value={}),
                                            ):
                                                result = await run_structured_query(
                                                    WHIEDA,
                                                    {
                                                        "question": "фото товар без фото",
                                                        "session": "nbz-photo",
                                                    },
                                                    "trace-nbz-6",
                                                )

    assert result["gap_kind"] == "missing_resource"
    assert "прикреп" in result["answer_text"].lower()


def test_next_steps_nonempty_for_all_kinds():
    for gap_kind in GAP_KINDS:
        steps = build_next_steps(gap_kind)
        assert 1 <= len(steps) <= 3
