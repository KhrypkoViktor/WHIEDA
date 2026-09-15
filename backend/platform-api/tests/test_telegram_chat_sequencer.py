"""Per-chat Telegram update ordering and idempotency tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.gap import GAP_TEXTS
from app.advisor.sql.engine import SERVICE_FALLBACKS
from app.advisor.sql.text import detect_service_intent, is_unsupported_topic
from app.telegram.routes import _process_telegram_update
from app.telegram.sequencer import ChatUpdateSequencer, reset_chat_sequencer_for_tests
from app.telegram.bindings import BotBindingContext


@pytest.fixture
def whieda_bot_binding(whieda_tenant):
    return BotBindingContext(
        binding_id="whieda-test-binding",
        tenant=whieda_tenant,
        bot_token_ref="env:TEST_WHIEDA_BOT_TOKEN",
        webhook_secret_ref="env:TEST_WHIEDA_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token="whieda-test-token",
        webhook_secret="whieda-test-secret",
    )


@pytest.fixture(autouse=True)
def _fresh_sequencer():
    reset_chat_sequencer_for_tests()
    yield
    reset_chat_sequencer_for_tests()


@pytest.mark.asyncio
async def test_same_chat_messages_keep_reply_order():
    seq = ChatUpdateSequencer()
    order: list[str] = []

    async def slow_first():
        order.append("start_capabilities")
        await asyncio.sleep(0.05)
        order.append("end_capabilities")
        return "capabilities"

    async def fast_second():
        order.append("start_oos")
        await asyncio.sleep(0.01)
        order.append("end_oos")
        return "unsupported_topic"

    first = asyncio.create_task(seq.run_ordered("chat-1", 1, slow_first))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(seq.run_ordered("chat-1", 2, fast_second))
    await asyncio.gather(first, second)
    assert order == [
        "start_capabilities",
        "end_capabilities",
        "start_oos",
        "end_oos",
    ]


@pytest.mark.asyncio
async def test_rapid_capabilities_then_oos_content_and_order():
    seq = ChatUpdateSequencer()
    delivered: list[tuple[str, str]] = []

    async def simulate(question: str, delay: float):
        async def handler():
            await asyncio.sleep(delay)
            if detect_service_intent(question) == "capabilities":
                mode = "capabilities"
                text = SERVICE_FALLBACKS["capabilities"]
            elif is_unsupported_topic(question):
                mode = "unsupported_topic"
                text = GAP_TEXTS["unsupported_topic"]
            else:
                mode = "knowledge_gap"
                text = GAP_TEXTS["unknown_product"]
            delivered.append((mode, text))
            return mode

        return await seq.run_ordered("chat-rapid", None, handler)

    t1 = asyncio.create_task(simulate("что можешь", 0.06))
    await asyncio.sleep(0.005)
    t2 = asyncio.create_task(simulate("пивка хочешь", 0.01))
    await asyncio.gather(t1, t2)

    assert [mode for mode, _ in delivered] == ["capabilities", "unsupported_topic"]
    assert ("Я могу помочь" in delivered[0][1]) or ("Могу подсказать" in delivered[0][1])
    assert delivered[1][0] != "capabilities"
    assert "пивка" not in delivered[1][1].casefold() or "сценар" in delivered[1][1].casefold()


@pytest.mark.asyncio
async def test_different_chats_process_in_parallel():
    seq = ChatUpdateSequencer()
    started: list[str] = []
    gate = asyncio.Event()

    async def work(chat_key: str):
        async def handler():
            started.append(f"start:{chat_key}")
            await gate.wait()
            return chat_key

        return await seq.run_ordered(chat_key, None, handler)

    t1 = asyncio.create_task(work("chat-a"))
    t2 = asyncio.create_task(work("chat-b"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(started) == 2:
            break
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert len(started) == 2
    assert set(started) == {"start:chat-a", "start:chat-b"}
    assert r1.value == "chat-a"
    assert r2.value == "chat-b"


@pytest.mark.asyncio
async def test_duplicate_update_id_is_ignored():
    seq = ChatUpdateSequencer()
    calls = 0

    async def handler():
        nonlocal calls
        calls += 1
        return "ok"

    first = await seq.run_ordered("chat-dup", 42, handler)
    second = await seq.run_ordered("chat-dup", 42, handler)
    assert first.duplicate is False
    assert second.duplicate is True
    assert calls == 1


@pytest.mark.asyncio
async def test_duplicate_update_does_not_run_handler_twice_after_success():
    seq = ChatUpdateSequencer()
    deliveries: list[str] = []

    async def handler():
        deliveries.append("sent")
        return "ok"

    first = await seq.run_ordered("chat-deliver", 77, handler)
    second = await seq.run_ordered("chat-deliver", 77, handler)
    assert first.duplicate is False
    assert second.duplicate is True
    assert deliveries == ["sent"]


@pytest.mark.asyncio
async def test_failed_update_is_not_remembered_so_retry_allowed():
    seq = ChatUpdateSequencer()
    attempts = 0

    async def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return "recovered"

    with pytest.raises(RuntimeError, match="transient"):
        await seq.run_ordered("chat-retry", 88, fail_once)
    result = await seq.run_ordered("chat-retry", 88, fail_once)
    assert result.duplicate is False
    assert result.value == "recovered"
    assert attempts == 2


@pytest.mark.asyncio
async def test_routes_duplicate_update_skips_delivery_callback(whieda_bot_binding):
    deliveries = 0

    async def fake_core(*_args, **_kwargs):
        nonlocal deliveries
        deliveries += 1
        await asyncio.sleep(0.02)

    with patch("app.telegram.routes.process_core_telegram_update", side_effect=fake_core):
        update = {
            "update_id": 9002,
            "message": {
                "text": "что можешь",
                "chat": {"id": 556, "type": "private"},
                "from": {"id": 1},
            },
        }
        await asyncio.gather(
            _process_telegram_update(whieda_bot_binding, update, "trace-dup-deliver"),
            _process_telegram_update(whieda_bot_binding, update, "trace-dup-deliver"),
        )
    assert deliveries == 1


@pytest.mark.asyncio
async def test_same_update_and_chat_ids_do_not_collide_across_bindings(
    whieda_bot_binding,
):
    deliveries: list[str] = []

    async def fake_core(_tenant, _update, _trace, *, binding):
        deliveries.append(binding.binding_id)

    nsp_binding = replace(
        whieda_bot_binding,
        binding_id="nsp-binding",
        bot_token_ref="env:NSP_BOT_TOKEN",
        webhook_secret_ref="env:NSP_WEBHOOK_SECRET",
        bot_username="NSP_Leader_bot",
        bot_token="nsp-token",
        webhook_secret="nsp-secret",
    )
    update = {
        "update_id": 9002,
        "message": {
            "text": "что можешь",
            "chat": {"id": 556, "type": "private"},
            "from": {"id": 1},
        },
    }

    with patch("app.telegram.routes.process_core_telegram_update", side_effect=fake_core):
        await _process_telegram_update(whieda_bot_binding, update, "trace-whieda")
        await _process_telegram_update(nsp_binding, update, "trace-nsp")

    assert deliveries == ["whieda-test-binding", "nsp-binding"]


@pytest.mark.asyncio
async def test_failure_releases_chat_for_next_message():
    seq = ChatUpdateSequencer()

    async def fail():
        raise RuntimeError("boom")

    async def ok():
        return "next"

    with pytest.raises(RuntimeError, match="boom"):
        await seq.run_ordered("chat-fail", 1, fail)
    result = await seq.run_ordered("chat-fail", 2, ok)
    assert result.duplicate is False
    assert result.value == "next"


@pytest.mark.asyncio
async def test_routes_duplicate_update_skips_processor(whieda_bot_binding):
    calls = 0

    async def fake_body(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)

    with patch("app.telegram.routes._process_telegram_update_body", side_effect=fake_body):
        update = {
            "update_id": 9001,
            "message": {"text": "что можешь", "chat": {"id": 555}, "from": {"id": 1}},
        }
        await asyncio.gather(
            _process_telegram_update(whieda_bot_binding, update, "trace-1"),
            _process_telegram_update(whieda_bot_binding, update, "trace-1"),
        )
    assert calls == 1
