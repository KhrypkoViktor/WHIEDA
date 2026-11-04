"""Нажатие кнопки бота: отпечаток кнопки, повторный тап, «печатает…», кэши (04.11.2026). Без базы."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.telegram import routes
from app.telegram.delivery import TelegramDeliveryUnknown, send_chat_action
from app.telegram.sequencer import ChatUpdateSequencer, build_message_fingerprint, reset_chat_sequencer_for_tests
from tests.tap_testkit import FakeTelegram, callback_update


@pytest.fixture(autouse=True)
def _fresh_sequencer():
    """Очередь чатов — одна на процесс: отпечатки и номера обновлений одного теста не должны задеть другой."""
    reset_chat_sequencer_for_tests()
    yield
    reset_chat_sequencer_for_tests()

# ---- Отпечаток кнопки ---------------------------------------------------------------------------


def test_fingerprint_same_button_same_message_is_the_same():
    assert build_message_fingerprint(callback_data="site:skip", message_id=100) == build_message_fingerprint(
        callback_data="site:skip", message_id=100
    )


def test_fingerprint_same_button_under_a_new_message_differs():
    # «Пропустить» на шаге «фото» и на шаге «текст» — одна и та же callback_data под разными сообщениями.
    assert build_message_fingerprint(callback_data="site:skip", message_id=100) != build_message_fingerprint(
        callback_data="site:skip", message_id=101
    )


def test_fingerprint_other_button_same_message_differs():
    assert build_message_fingerprint(callback_data="svc:order:gemini_18m", message_id=7) != build_message_fingerprint(
        callback_data="svc:order:gemini_6m", message_id=7
    )


def test_fingerprint_without_message_id_and_text_is_unchanged():
    # Старые вызовы и текстовые сообщения: тот же отпечаток, что и до правки.
    assert build_message_fingerprint(callback_data="site:skip") == hashlib.sha256(b"site:skip").hexdigest()[:24]
    assert build_message_fingerprint(text="  Привет  ") == hashlib.sha256("привет".encode()).hexdigest()[:24]
    # message_id у текстового сообщения в отпечаток не входит.
    assert build_message_fingerprint(text="привет", message_id=5) == build_message_fingerprint(text="привет")
    assert build_message_fingerprint() is None


# ---- Очередь чата: повторный тап ---------------------------------------------------------------


async def test_second_tap_on_the_same_button_is_dropped_while_the_first_runs():
    seq = ChatUpdateSequencer()
    release = asyncio.Event()
    started = asyncio.Event()
    ran: list[str] = []

    async def first() -> str:
        ran.append("first")
        started.set()
        await release.wait()
        return "first"

    async def second() -> str:
        ran.append("second")
        return "second"

    fingerprint = build_message_fingerprint(callback_data="svc:confirm:gemini_18m", message_id=9)
    task = asyncio.create_task(
        seq.run_ordered("c", 1, first, message_fingerprint=fingerprint, drop_inflight_duplicate=True)
    )
    await started.wait()
    # Второй тап приходит, пока первый занят: ответ сразу, не после очереди чата.
    dropped = await asyncio.wait_for(
        seq.run_ordered("c", 2, second, message_fingerprint=fingerprint, drop_inflight_duplicate=True), timeout=0.5
    )
    assert dropped.duplicate is True
    release.set()
    done = await task
    assert done.duplicate is False and ran == ["first"]
    # И после окончания первого тап в пределах 12 с — всё ещё дубль.
    again = await seq.run_ordered("c", 3, second, message_fingerprint=fingerprint, drop_inflight_duplicate=True)
    assert again.duplicate is True and ran == ["first"]


async def test_the_same_button_under_a_new_message_goes_through_within_twelve_seconds():
    seq = ChatUpdateSequencer()
    ran: list[int] = []

    async def handler() -> None:
        ran.append(1)

    for update_id, message_id in ((1, 100), (2, 101), (3, 102)):
        result = await seq.run_ordered(
            "c", update_id, handler,
            message_fingerprint=build_message_fingerprint(callback_data="site:skip", message_id=message_id),
            drop_inflight_duplicate=True,
        )
        assert result.duplicate is False
    assert len(ran) == 3


async def test_other_button_waits_for_the_chat_queue_and_runs_after_the_first():
    seq = ChatUpdateSequencer()
    release = asyncio.Event()
    started = asyncio.Event()
    order: list[str] = []

    async def slow() -> None:
        started.set()
        await release.wait()
        order.append("slow")

    async def quick() -> None:
        order.append("quick")

    first = asyncio.create_task(seq.run_ordered(
        "c", 1, slow, message_fingerprint=build_message_fingerprint(callback_data="a:b", message_id=1),
        drop_inflight_duplicate=True,
    ))
    await started.wait()
    second = asyncio.create_task(seq.run_ordered(
        "c", 2, quick, message_fingerprint=build_message_fingerprint(callback_data="c:d", message_id=1),
        drop_inflight_duplicate=True,
    ))
    await asyncio.sleep(0.05)
    assert order == []  # порядок обновлений одного чата сохраняется
    release.set()
    await asyncio.gather(first, second)
    assert order == ["slow", "quick"]


async def test_failed_first_tap_does_not_block_the_retry():
    seq = ChatUpdateSequencer()
    fingerprint = build_message_fingerprint(callback_data="svc:confirm:gemini_18m", message_id=9)

    async def boom() -> None:
        raise RuntimeError("boom")

    async def ok() -> str:
        return "ok"

    with pytest.raises(RuntimeError):
        await seq.run_ordered("c", 1, boom, message_fingerprint=fingerprint, drop_inflight_duplicate=True)
    retry = await seq.run_ordered("c", 2, ok, message_fingerprint=fingerprint, drop_inflight_duplicate=True)
    assert retry.duplicate is False and retry.value == "ok"


async def test_text_messages_keep_the_old_behaviour_second_waits_then_is_dropped():
    seq = ChatUpdateSequencer()
    release = asyncio.Event()
    started = asyncio.Event()
    ran: list[str] = []

    async def first() -> None:
        ran.append("first")
        started.set()
        await release.wait()

    async def second() -> None:
        ran.append("second")

    fingerprint = build_message_fingerprint(text="привет")
    first_task = asyncio.create_task(seq.run_ordered("c", 1, first, message_fingerprint=fingerprint))
    await started.wait()
    second_task = asyncio.create_task(seq.run_ordered("c", 2, second, message_fingerprint=fingerprint))
    await asyncio.sleep(0.05)
    assert not second_task.done()  # без флага ждёт очереди, как раньше
    release.set()
    await first_task
    result = await second_task
    assert result.duplicate is True and ran == ["first"]


async def test_on_accepted_fires_before_the_queue_and_not_for_duplicates():
    seq = ChatUpdateSequencer()
    release = asyncio.Event()
    started = asyncio.Event()
    accepted: list[str] = []

    async def slow() -> None:
        started.set()
        await release.wait()

    async def quick() -> None:
        return None

    first = asyncio.create_task(seq.run_ordered(
        "c", 1, slow, message_fingerprint=build_message_fingerprint(callback_data="a:b", message_id=1),
        drop_inflight_duplicate=True, on_accepted=lambda: accepted.append("first"),
    ))
    await started.wait()
    other = asyncio.create_task(seq.run_ordered(
        "c", 2, quick, message_fingerprint=build_message_fingerprint(callback_data="c:d", message_id=1),
        drop_inflight_duplicate=True, on_accepted=lambda: accepted.append("other"),
    ))
    await asyncio.sleep(0.05)
    # Чат занят предыдущим нажатием, а «печатает…» второго уже ушло.
    assert accepted == ["first", "other"]
    dup = await seq.run_ordered(
        "c", 3, quick, message_fingerprint=build_message_fingerprint(callback_data="a:b", message_id=1),
        drop_inflight_duplicate=True, on_accepted=lambda: accepted.append("dup"),
    )
    assert dup.duplicate is True and accepted == ["first", "other"]
    release.set()
    await asyncio.gather(first, other)


async def test_result_reports_queue_wait_and_run_time():
    seq = ChatUpdateSequencer()

    async def handler() -> None:
        await asyncio.sleep(0.03)

    result = await seq.run_ordered("c", 1, handler)
    assert result.duplicate is False
    assert result.run_ms >= 25 and result.wait_ms < 25


# ---- Роутер: «печатает…» и ответ на дубль ------------------------------------------------------


@pytest.fixture
def telegram_calls(monkeypatch):
    """Настоящие send_chat_action / answer_callback_query роутера (conftest их глушит) на подменённом Bot API."""
    from app.telegram import delivery

    monkeypatch.setattr(routes, "send_chat_action", delivery.send_chat_action)
    monkeypatch.setattr(routes, "answer_callback_query", delivery.answer_callback_query)
    return FakeTelegram().install(monkeypatch)


async def _settle() -> None:
    """Дать фоновым задачам «печатает…» дойти до конца."""
    for _ in range(5):
        await asyncio.sleep(0)
    while routes._typing_tasks:
        await asyncio.gather(*list(routes._typing_tasks), return_exceptions=True)


def _body_recorder():
    seen: list[str] = []

    async def body(_binding, update, _trace):
        seen.append(update["callback_query"]["data"] if "callback_query" in update else "message")

    return seen, body


async def test_button_press_sends_typing_to_the_private_chat(whieda_bot_binding, telegram_calls):
    seen, body = _body_recorder()
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, callback_update("svc:card:gemini", update_id=1), "t")
    await _settle()
    assert seen == ["svc:card:gemini"]
    typing = telegram_calls.sent("sendChatAction")
    assert len(typing) == 1
    assert typing[0].payload == {"chat_id": "5550001", "action": "typing"}


async def test_typing_failure_does_not_break_the_button(whieda_bot_binding, telegram_calls):
    telegram_calls.fail_methods.add("sendChatAction")
    seen, body = _body_recorder()
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, callback_update("svc:card:gemini", update_id=2), "t")
    await _settle()
    assert seen == ["svc:card:gemini"]  # обработка прошла, хотя Telegram на «печатает…» не ответил


async def test_typing_is_not_sent_for_messages_groups_showcase_other_tenants_or_when_switched_off(
    whieda_bot_binding, telegram_calls, monkeypatch
):
    seen, body = _body_recorder()
    with patch.object(routes, "_process_telegram_update_body", body):
        # обычное сообщение
        await routes._process_telegram_update(
            whieda_bot_binding,
            {"update_id": 10, "message": {"message_id": 1, "text": "привет", "chat": {"id": 7, "type": "private"}, "from": {"id": 7}}},
            "t",
        )
        # группа
        await routes._process_telegram_update(
            whieda_bot_binding, callback_update("svc:close:abc", update_id=11, chat=-100, chat_type="supergroup"), "t"
        )
        # бот витрины автора Академии
        await routes._process_telegram_update(
            replace(whieda_bot_binding, showcase_id="almira"), callback_update("x:y", update_id=12, user=8), "t"
        )
        # чужой арендатор
        other = replace(whieda_bot_binding, tenant=replace(whieda_bot_binding.tenant, tenant_id="nsp"))
        await routes._process_telegram_update(other, callback_update("x:y", update_id=13, user=9), "t")
        # выключатель
        monkeypatch.setenv("PLATFORM_TELEGRAM_CALLBACK_TYPING", "false")
        from app.settings import get_settings

        get_settings.cache_clear()
        try:
            await routes._process_telegram_update(whieda_bot_binding, callback_update("x:y", update_id=14, user=10), "t")
        finally:
            monkeypatch.delenv("PLATFORM_TELEGRAM_CALLBACK_TYPING")
            get_settings.cache_clear()
    await _settle()
    assert len(seen) == 5
    assert telegram_calls.sent("sendChatAction") == []


async def test_duplicate_tap_is_answered_without_text_and_not_processed(whieda_bot_binding, telegram_calls):
    seen, body = _body_recorder()
    first = callback_update("svc:confirm:gemini_18m", update_id=20, message_id=55, query_id="first")
    again = callback_update("svc:confirm:gemini_18m", update_id=21, message_id=55, query_id="again")
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, first, "t")
        await routes._process_telegram_update(whieda_bot_binding, again, "t")
    await _settle()
    assert seen == ["svc:confirm:gemini_18m"]
    answers = telegram_calls.sent("answerCallbackQuery")
    assert [call.payload for call in answers] == [{"callback_query_id": "again"}]  # без text: «часики» сняты


async def test_duplicate_ack_failure_is_swallowed(whieda_bot_binding, telegram_calls):
    telegram_calls.fail_methods.add("answerCallbackQuery")
    seen, body = _body_recorder()
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, callback_update("a:b", update_id=30, message_id=1), "t")
        await routes._process_telegram_update(whieda_bot_binding, callback_update("a:b", update_id=31, message_id=1), "t")
    assert seen == ["a:b"]


async def test_same_button_on_the_next_message_is_not_a_duplicate(whieda_bot_binding, telegram_calls):
    seen, body = _body_recorder()
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, callback_update("site:skip", update_id=40, message_id=100), "t")
        await routes._process_telegram_update(whieda_bot_binding, callback_update("site:skip", update_id=41, message_id=101), "t")
    await _settle()
    assert seen == ["site:skip", "site:skip"]
    assert telegram_calls.sent("answerCallbackQuery") == []


async def test_duplicate_text_message_gets_no_callback_answer(whieda_bot_binding, telegram_calls):
    seen, body = _body_recorder()
    text = {"message_id": 1, "text": "привет", "chat": {"id": 7, "type": "private"}, "from": {"id": 7}}
    with patch.object(routes, "_process_telegram_update_body", body):
        await routes._process_telegram_update(whieda_bot_binding, {"update_id": 50, "message": text}, "t")
        await routes._process_telegram_update(whieda_bot_binding, {"update_id": 51, "message": text}, "t")
    assert seen == ["message"]
    assert telegram_calls.calls == []


# ---- Bot API: sendChatAction -------------------------------------------------------------------


async def test_send_chat_action_posts_typing(telegram_calls):
    result = await send_chat_action(chat_id="42", bot_token="test-bot-token")
    assert result == {"ok": True}
    assert telegram_calls.calls[0].method == "sendChatAction"
    assert telegram_calls.calls[0].payload == {"chat_id": "42", "action": "typing"}


async def test_send_chat_action_transport_error_is_raised_for_the_caller_to_swallow(telegram_calls):
    telegram_calls.fail_methods.add("sendChatAction")
    with pytest.raises(TelegramDeliveryUnknown):
        await send_chat_action(chat_id="42", bot_token="test-bot-token")


async def test_send_chat_action_skips_without_chat_or_token():
    assert (await send_chat_action(chat_id="", bot_token="t"))["skipped"] is True
    assert (await send_chat_action(chat_id="1", bot_token=""))["skipped"] is True
