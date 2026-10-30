"""Max: кнопки, ответы на нажатия, приём контакта, отложенные сообщения (M2+M3, 30.10.2026).

Без сети и базы: httpx и отправка подделаны, очередь воркера — на фейковом пуле.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.max import client as max_client
from app.max import outbox as max_outbox
from app.max import processor, routes
from app.max.client import (
    MaxKeyboardError,
    answer_max_callback,
    max_button_callback,
    max_button_link,
    max_button_request_contact,
    max_inline_keyboard,
    send_max_attachment,
    send_max_message,
    send_max_text,
    subscribe_webhook,
)
from app.max.update_parser import MaxCallbackEvent, MaxEvent, parse_max_update, verify_max_contact_hash
from app.settings import get_settings
from app.tenancy import TenantContext

BOT_TOKEN = "unit-test-max-token"
TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})


# --- подделка httpx ----------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.text = json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self) -> Any:
        return self._payload


@pytest.fixture
def http(monkeypatch):
    """Запросы клиента Max попадают в http.calls; ответы — из http.responses (по очереди, последний повторяется)."""
    monkeypatch.setattr(get_settings(), "max_bot_token", BOT_TOKEN)

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses: list[_Response] = [_Response(200, {"message": {"body": {"mid": "m-1"}}})]

    recorder = Recorder()

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def post(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None, json: Any = None, **_: Any) -> _Response:
            recorder.calls.append({"url": url, "params": params, "json": json, "auth": (headers or {}).get("Authorization")})
            return recorder.responses.pop(0) if len(recorder.responses) > 1 else recorder.responses[0]

    monkeypatch.setattr(max_client.httpx, "AsyncClient", FakeClient)
    return recorder


# --- клавиатура и лимиты -----------------------------------------------------------------------


def test_keyboard_is_built_from_rows_of_helpers():
    keyboard = max_inline_keyboard([
        [max_button_callback("Да", "quiz:yes"), max_button_callback("Нет", "quiz:no")],
        [max_button_link("Сайт", "https://wwc.best")],
        [max_button_request_contact("Поделиться номером")],
    ])
    assert keyboard == {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "Да", "payload": "quiz:yes"}, {"type": "callback", "text": "Нет", "payload": "quiz:no"}],
        [{"type": "link", "text": "Сайт", "url": "https://wwc.best"}],
        [{"type": "request_contact", "text": "Поделиться номером"}],
    ]}}


def test_row_limits_seven_plain_three_with_link_or_contact():
    seven = [[max_button_callback(str(i), f"p{i}") for i in range(7)]]
    assert len(max_inline_keyboard(seven)["payload"]["buttons"][0]) == 7
    with pytest.raises(MaxKeyboardError, match="row_buttons:8>7"):
        max_inline_keyboard([[max_button_callback(str(i), f"p{i}") for i in range(8)]])
    three = [[max_button_callback("a", "a"), max_button_callback("b", "b"), max_button_link("c", "https://x.ru")]]
    assert len(max_inline_keyboard(three)["payload"]["buttons"][0]) == 3
    with pytest.raises(MaxKeyboardError, match="row_buttons:4>3"):
        max_inline_keyboard([[max_button_callback(str(i), f"p{i}") for i in range(3)] + [max_button_link("c", "https://x.ru")]])
    with pytest.raises(MaxKeyboardError, match="row_buttons:4>3"):
        max_inline_keyboard([[max_button_callback(str(i), f"p{i}") for i in range(3)] + [max_button_request_contact("Номер")]])


def test_keyboard_row_count_and_shape_limits():
    thirty = [[max_button_callback(str(i), f"p{i}")] for i in range(30)]
    assert len(max_inline_keyboard(thirty)["payload"]["buttons"]) == 30
    with pytest.raises(MaxKeyboardError, match="rows:31>30"):
        max_inline_keyboard(thirty + [[max_button_callback("x", "x")]])
    for bad in ([], [[]], [max_button_callback("плоский список", "x")], "кнопки"):
        with pytest.raises(MaxKeyboardError):
            max_inline_keyboard(bad)  # type: ignore[arg-type]


def test_button_text_payload_and_url_limits():
    assert max_button_callback("т" * 128, "п" * 1024)
    for factory in (
        lambda: max_button_callback("", "x"),
        lambda: max_button_callback("т" * 129, "x"),
        lambda: max_button_callback("ok", ""),
        lambda: max_button_callback("ok", "п" * 1025),
        lambda: max_button_link("ok", ""),
        lambda: max_button_link("ok", "https://x.ru/" + "a" * 2048),
        lambda: max_button_request_contact(""),
    ):
        with pytest.raises(MaxKeyboardError):
            factory()
    assert max_button_link("ok", "https://x.ru/" + "a" * 2000)
    with pytest.raises(MaxKeyboardError, match="type_unknown"):
        max_inline_keyboard([[{"type": "pay", "text": "x"}]])


# --- отправка с кнопками, ответ на нажатие, вложение по токену ---------------------------------


async def test_send_max_text_without_buttons_is_unchanged(http):
    result = await send_max_text(user_id=42, text="Привет")
    assert result["ok"] is True
    (call,) = http.calls
    assert call["url"].endswith("/messages") and call["params"] == {"user_id": "42"}
    assert call["json"] == {"text": "Привет"} and call["auth"] == BOT_TOKEN


async def test_send_max_text_with_buttons_goes_as_inline_keyboard(http):
    rows = [[max_button_callback("Дальше", "step:2")]]
    result = await send_max_text(user_id=42, text="Выбирайте", buttons=rows)
    assert result["ok"] is True and result["mid"] == "m-1"
    (call,) = http.calls
    assert call["params"] == {"user_id": "42"}
    assert call["json"] == {"text": "Выбирайте", "attachments": [max_inline_keyboard(rows)]}


async def test_send_max_message_keeps_attachments_then_keyboard_and_old_chat_call(http):
    video = {"type": "video", "payload": {"token": "vid-token"}}
    await send_max_message(chat_id=-7, text="Видео", attachments=[video], buttons=[[max_button_link("Смотреть", "https://wwc.best")]])
    await send_max_message(chat_id=-7, text="Просто текст")
    first, second = http.calls
    assert first["params"] == {"chat_id": "-7"}
    assert [a["type"] for a in first["json"]["attachments"]] == ["video", "inline_keyboard"]
    assert second["params"] == {"chat_id": "-7"} and second["json"] == {"text": "Просто текст"}
    with pytest.raises(ValueError):
        await send_max_message(text="некому")


async def test_send_max_attachment_by_token_and_by_uploaded_dict(http):
    await send_max_attachment(user_id=42, kind="audio", token="aud-token", text="Слушайте")
    await send_max_attachment(user_id=42, attachment={"type": "video", "payload": {"token": "vid-token"}})
    audio, video = http.calls
    assert audio["json"] == {"text": "Слушайте", "attachments": [{"type": "audio", "payload": {"token": "aud-token"}}]}
    assert video["json"]["attachments"] == [{"type": "video", "payload": {"token": "vid-token"}}] and video["json"]["text"] is None
    with pytest.raises(ValueError):
        await send_max_attachment(user_id=42, kind="sticker", token="x")
    with pytest.raises(ValueError):
        await send_max_attachment(user_id=42, kind="video", token=" ")


async def test_answer_max_callback_bodies_and_failures(http):
    assert (await answer_max_callback("cb-1"))["ok"] is True
    await answer_max_callback("cb-2", notification="Готово")
    await answer_max_callback("cb-3", text="Новый текст", buttons=[[max_button_callback("Назад", "back")]])
    empty, note, edited = http.calls
    assert empty["url"].endswith("/answers") and empty["params"] == {"callback_id": "cb-1"} and empty["json"] == {}
    assert note["json"] == {"notification": "Готово"}
    assert edited["json"] == {"message": {"text": "Новый текст", "attachments": [
        {"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback", "text": "Назад", "payload": "back"}]]}}]}}
    http.responses = [_Response(200, {"success": False, "message": "callback.not.found"})]
    assert await answer_max_callback("cb-4") == {"ok": False, "status": 200}
    http.responses = [_Response(401, {"code": "verify.token"})]
    assert await answer_max_callback("cb-5") == {"ok": False, "status": 401}
    with pytest.raises(ValueError):
        await answer_max_callback("  ")


async def test_webhook_subscription_includes_message_callback(http):
    assert "message_callback" in max_client.UPDATE_TYPES
    await subscribe_webhook("https://example.invalid/v1/max/webhook", secret="s")
    (call,) = http.calls
    assert call["url"].endswith("/subscriptions") and "message_callback" in call["json"]["update_types"]


# --- разбор message_callback и контакта --------------------------------------------------------


def _callback_update(**over: Any) -> dict[str, Any]:
    update = {
        "update_type": "message_callback", "timestamp": 1,
        "callback": {"timestamp": 1, "callback_id": "cb-77", "payload": "menu:open:2",
                     "user": {"user_id": 42, "first_name": "Инна", "username": "inna_m"}},
        "message": {"sender": {"user_id": 9, "is_bot": True}, "recipient": {"chat_id": 777, "chat_type": "dialog"},
                    "body": {"mid": "mid.abc", "seq": 5, "text": "Меню", "attachments": []}},
    }
    update.update(over)
    return update


def test_message_callback_is_parsed_into_its_own_event():
    event = parse_max_update(_callback_update())
    assert isinstance(event, MaxCallbackEvent)
    assert (event.kind, event.callback_id, event.payload, event.user_id, event.chat_id, event.message_id, event.chat_type) == (
        "callback", "cb-77", "menu:open:2", 42, 777, "mid.abc", "dialog")
    assert event.username == "inna_m" and event.display_name == "@inna_m"


def test_message_callback_edge_cases():
    gone = parse_max_update(_callback_update(message=None))
    assert isinstance(gone, MaxCallbackEvent) and gone.chat_id is None and gone.message_id is None
    no_payload = _callback_update()
    del no_payload["callback"]["payload"]
    parsed = parse_max_update(no_payload)
    assert isinstance(parsed, MaxCallbackEvent) and parsed.payload == ""
    assert parse_max_update(_callback_update(callback={"callback_id": "", "user": {"user_id": 1}})) is None
    assert parse_max_update(_callback_update(callback={"callback_id": "x", "user": {}})) is None
    group = parse_max_update(_callback_update(message={"recipient": {"chat_id": 5, "chat_type": "chat"}, "body": {"mid": "m"}}))
    assert isinstance(group, MaxCallbackEvent) and group.chat_type == "chat"


VCF = "BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:ez-vcard 0.10.3\r\nTEL;TYPE=cell:79990000000\r\nFN:Ivan Ivanov\r\nEND:VCARD\r\n"


def _hash(vcf: str, token: str = BOT_TOKEN) -> str:
    return hmac.new(token.encode(), vcf.encode(), hashlib.sha256).hexdigest()


def _contact_update(payload: dict[str, Any], *, text: str = "", chat_type: str = "dialog") -> dict[str, Any]:
    return {"update_type": "message_created", "timestamp": 1, "message": {
        "sender": {"user_id": 42, "first_name": "Иван"}, "recipient": {"chat_id": 777, "chat_type": chat_type},
        "body": {"mid": "m1", "seq": 1, "text": text, "attachments": [{"type": "contact", "payload": payload}]}}}


def test_contact_attachment_gives_normalized_phone_owner_and_verification(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_token", BOT_TOKEN)
    event = parse_max_update(_contact_update({"vcf_info": VCF, "hash": _hash(VCF), "max_info": {"user_id": 42, "first_name": "Иван"}}))
    assert isinstance(event, MaxEvent) and event.kind == "message" and event.text == ""
    assert event.contact_phone == "+79990000000"
    assert event.contact_user_id == 42 == event.user_id
    assert event.contact_verified is True


def test_forwarded_or_foreign_contact_is_not_verified(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_token", BOT_TOKEN)
    forwarded = parse_max_update(_contact_update({"vcf_info": VCF, "max_info": {"user_id": 555}}))
    assert forwarded.contact_phone == "+79990000000" and forwarded.contact_user_id == 555 != forwarded.user_id
    assert forwarded.contact_verified is False
    tampered = parse_max_update(_contact_update({"vcf_info": VCF.replace("79990000000", "79991112233"), "hash": _hash(VCF)}))
    assert tampered.contact_phone == "+79991112233" and tampered.contact_verified is False


def test_contact_hash_variants_and_missing_token(monkeypatch):
    digest = hmac.new(BOT_TOKEN.encode(), VCF.encode(), hashlib.sha256).digest()
    assert verify_max_contact_hash(VCF, digest.hex(), bot_token=BOT_TOKEN)
    assert verify_max_contact_hash(VCF, digest.hex().upper(), bot_token=BOT_TOKEN)
    assert verify_max_contact_hash(VCF, base64.b64encode(digest).decode(), bot_token=BOT_TOKEN)
    # «\r\n» буквами (как в документации) считается настоящими переносами строк
    assert verify_max_contact_hash(VCF.replace("\r\n", "\\r\\n"), digest.hex(), bot_token=BOT_TOKEN)
    assert not verify_max_contact_hash(VCF, digest.hex(), bot_token="другой-токен")
    assert not verify_max_contact_hash(VCF, None, bot_token=BOT_TOKEN)
    assert not verify_max_contact_hash(None, digest.hex(), bot_token=BOT_TOKEN)
    monkeypatch.setattr(get_settings(), "max_bot_token", None)
    assert not verify_max_contact_hash(VCF, digest.hex())


def test_contact_phone_fallbacks_and_non_dialog_contact():
    from_vcf_phone = parse_max_update(_contact_update({"vcf_phone": "89281112233", "contact_id": 31}))
    assert from_vcf_phone.contact_phone == "+79281112233" and from_vcf_phone.contact_user_id == 31
    no_phone = parse_max_update(_contact_update({"vcf_info": "BEGIN:VCARD\r\nFN:Без номера\r\nEND:VCARD\r\n"}))
    assert no_phone.contact_phone is None
    group = parse_max_update(_contact_update({"vcf_info": VCF}, chat_type="chat"))
    assert group.kind == "group_message" and group.contact_phone is None
    plain = parse_max_update(_contact_update({}, text="привет"))
    assert plain.contact_phone is None and plain.contact_user_id is None and plain.contact_verified is False


# --- диспетчер нажатий -------------------------------------------------------------------------


@pytest.fixture
def callbacks(monkeypatch):
    """Чистый реестр и подделанный answer_max_callback: answers — что ушло в Max."""
    monkeypatch.setattr(processor, "_CALLBACK_HANDLERS", {})
    answers: list[dict[str, Any]] = []

    async def fake_answer(callback_id: str, **kwargs: Any) -> dict[str, Any]:
        answers.append({"callback_id": callback_id, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(processor, "answer_max_callback", fake_answer)
    return answers


def _event(payload: str = "menu:open:2") -> MaxCallbackEvent:
    event = parse_max_update(_callback_update(callback={"callback_id": "cb-77", "payload": payload, "user": {"user_id": 42, "first_name": "Инна"}}))
    assert isinstance(event, MaxCallbackEvent)
    return event


async def test_dispatcher_calls_handler_then_answers_with_its_result(callbacks):
    seen: list[tuple[str, str]] = []

    async def menu(tenant: TenantContext, event: MaxCallbackEvent, trace_id: str):
        seen.append((event.payload, trace_id))
        return processor.MaxCallbackAnswer(notification="Открыто", text="Раздел 2", buttons=[[max_button_callback("Назад", "menu:back")]])

    processor.register_max_callback("menu:", menu)
    result = await processor.process_max_event(TENANT, _event(), "trace-1")
    assert seen == [("menu:open:2", "trace-1")]
    assert callbacks == [{"callback_id": "cb-77", "notification": "Открыто", "text": "Раздел 2",
                          "buttons": [[max_button_callback("Назад", "menu:back")]]}]
    assert result == {"ok": True, "route": "max_callback", "status": "handled", "prefix": "menu:", "answered": True, "trace_id": "trace-1"}


async def test_dispatcher_longest_prefix_wins_and_dict_or_none_results(callbacks):
    calls: list[str] = []

    async def short(*_: Any):
        calls.append("short")
        return None

    async def long(*_: Any):
        calls.append("long")
        return {"notification": "из словаря"}

    processor.register_max_callback("menu:", short)
    processor.register_max_callback("menu:open:", long)
    await processor.handle_max_callback(TENANT, _event("menu:open:2"), "t")
    await processor.handle_max_callback(TENANT, _event("menu:close"), "t")
    assert calls == ["long", "short"]
    assert callbacks == [
        {"callback_id": "cb-77", "notification": "из словаря", "text": None, "buttons": None},
        {"callback_id": "cb-77", "notification": None, "text": None, "buttons": None},
    ]


async def test_unknown_payload_gets_a_silent_answer_without_error(callbacks):
    result = await processor.handle_max_callback(TENANT, _event("nobody:knows"), "t")
    assert result["ok"] is True and result["status"] == "unknown" and result["prefix"] is None
    assert callbacks == [{"callback_id": "cb-77", "notification": None, "text": None, "buttons": None}]
    empty_payload = await processor.handle_max_callback(TENANT, _event(""), "t")
    assert empty_payload["status"] == "unknown"


async def test_failing_handler_and_failing_answer_do_not_break_the_webhook(callbacks, monkeypatch):
    async def broken(*_: Any):
        raise RuntimeError("boom")

    processor.register_max_callback("bad:", broken)
    failed = await processor.handle_max_callback(TENANT, _event("bad:1"), "t")
    assert failed["ok"] is False and failed["status"] == "handler_failed" and failed["answered"] is True
    assert len(callbacks) == 1  # человеку всё равно ответили

    async def no_network(*_: Any, **__: Any):
        raise OSError("network down")

    monkeypatch.setattr(processor, "answer_max_callback", no_network)
    result = await processor.handle_max_callback(TENANT, _event("nobody"), "t")
    assert result["status"] == "unknown" and result["answered"] is False


def test_register_rejects_empty_prefix_and_silent_override(callbacks):
    async def first(*_: Any):
        return None

    async def second(*_: Any):
        return None

    with pytest.raises(ValueError):
        processor.register_max_callback("", first)
    processor.register_max_callback("x:", first)
    processor.register_max_callback("x:", first)  # тот же обработчик — не ошибка
    with pytest.raises(ValueError, match="prefix_taken"):
        processor.register_max_callback("x:", second)
    processor.unregister_max_callback("x:")
    processor.register_max_callback("x:", second)


async def test_webhook_pipeline_routes_callback_to_handler(callbacks, monkeypatch):
    async def load_tenant(_: str) -> TenantContext:
        return TENANT

    monkeypatch.setattr(routes, "_load_tenant", load_tenant)
    handled: list[int] = []

    async def handler(tenant: TenantContext, event: MaxCallbackEvent, trace_id: str):
        handled.append(event.user_id)

    processor.register_max_callback("menu:", handler)
    await routes._process(_callback_update(), "trace-9")
    assert handled == [42] and [a["callback_id"] for a in callbacks] == ["cb-77"]


# --- отложенные сообщения в Max (outbox) -------------------------------------------------------


def test_outbox_payload_is_validated_when_planned():
    payload = max_outbox.build_max_outbox_payload(
        binding_id="whieda-advisor-bot", user_id=42, text="Через час встреча",
        buttons=[[max_button_link("Войти", "https://zoom.example/j/1")]],
        attachments=[{"type": "video", "token": "vid-1"}, {"type": "audio", "payload": {"token": "aud-1"}}],
    )
    assert payload == {
        "channel": "max", "binding_id": "whieda-advisor-bot", "text": "Через час встреча", "user_id": "42",
        "buttons": [[{"type": "link", "text": "Войти", "url": "https://zoom.example/j/1"}]],
        "attachments": [{"type": "video", "payload": {"token": "vid-1"}}, {"type": "audio", "payload": {"token": "aud-1"}}],
    }
    assert max_outbox.payload_channel(payload) == "max" and max_outbox.payload_channel({"chat_id": "1"}) == "telegram"
    assert max_outbox.build_max_outbox_payload(binding_id="b", chat_id=-5, user_id=42, text="x")["chat_id"] == "-5"
    for bad in (
        {"binding_id": "", "user_id": 1, "text": "x"},
        {"binding_id": "b", "text": "x"},
        {"binding_id": "b", "user_id": 1, "text": "  "},
        {"binding_id": "b", "user_id": 1, "text": "x", "attachments": [{"type": "sticker", "token": "t"}]},
        {"binding_id": "b", "user_id": 1, "text": "x", "attachments": [{"type": "video"}]},
    ):
        with pytest.raises(ValueError):
            max_outbox.build_max_outbox_payload(**bad)
    with pytest.raises(MaxKeyboardError):
        max_outbox.build_max_outbox_payload(binding_id="b", user_id=1, text="x", buttons=[[max_button_callback("a", "a")] * 8])


async def test_enqueue_max_notification_writes_scheduled_row(monkeypatch):
    from datetime import datetime, timezone

    seen: dict[str, Any] = {}

    async def fake_enqueue(conn, tenant_id, event_type, key, payload, due_at=None):
        seen.update(tenant_id=tenant_id, event_type=event_type, key=key, payload=payload, due_at=due_at)
        return {"outbox_id": 1, "status": "scheduled", "created": True}

    monkeypatch.setattr(max_outbox, "enqueue_outbox_event", fake_enqueue)
    due = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    row = await max_outbox.enqueue_max_notification(
        object(), "whieda", idempotency_key="warmup:42:step3", due_at=due, binding_id="whieda-advisor-bot", user_id=42, text="Шаг 3",
    )
    assert row["created"] is True
    assert seen["due_at"] == due and seen["key"] == "warmup:42:step3" and seen["event_type"] == "max_notification"
    assert seen["payload"]["channel"] == "max" and seen["payload"]["user_id"] == "42"


async def test_send_max_outbox_payload_over_http(http):
    payload = max_outbox.build_max_outbox_payload(
        binding_id="b", user_id=42, text="<b>Привет</b>", buttons=[[max_button_callback("Ок", "ok")]],
        attachments=[{"type": "video", "token": "vid-1"}], html_format=True,
    )
    await max_outbox.send_max_outbox_payload(payload)
    (call,) = http.calls
    assert call["params"] == {"user_id": "42"} and call["json"]["format"] == "html" and call["json"]["text"] == "<b>Привет</b>"
    assert [a["type"] for a in call["json"]["attachments"]] == ["video", "inline_keyboard"]


async def test_send_max_outbox_payload_failure_classes(monkeypatch):
    import httpx

    from app.jobs import worker
    from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown

    base = {"channel": "max", "binding_id": "b", "user_id": "42", "text": "x"}

    def send_returning(result: dict[str, Any]):
        async def fake(**_: Any) -> dict[str, Any]:
            return result
        return fake

    monkeypatch.setattr(max_outbox, "send_max_message", send_returning({"ok": False, "status": 400}))
    with pytest.raises(TelegramDeliveryError) as rejected:
        await max_outbox.send_max_outbox_payload(base)
    assert worker._final_delivery_error(rejected.value) is True  # 400/403 — без повторов

    monkeypatch.setattr(max_outbox, "send_max_message", send_returning({"ok": False, "status": 503}))
    with pytest.raises(TelegramDeliveryError) as busy:
        await max_outbox.send_max_outbox_payload(base)
    assert worker._final_delivery_error(busy.value) is False  # 5xx — повтор через 10 минут

    async def timeout(**_: Any):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(max_outbox, "send_max_message", timeout)
    with pytest.raises(TelegramDeliveryUnknown):
        await max_outbox.send_max_outbox_payload(base)  # неизвестно, дошло ли — не повторяем

    for broken in ({**base, "user_id": ""}, {**base, "text": " "}, {**base, "buttons": [[]]}, {**base, "attachments": [{"type": "x"}]}):
        with pytest.raises(TelegramDeliveryUnknown):
            await max_outbox.send_max_outbox_payload(broken)


class _Cursor:
    async def __aenter__(self) -> "_Cursor":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Conn:
    @asynccontextmanager
    async def transaction(self):
        yield self

    def cursor(self) -> _Cursor:
        return _Cursor()


class _Pool:
    @asynccontextmanager
    async def connection(self, timeout: float | None = None):
        yield _Conn()


def _binding():
    from app.telegram.bindings import BotBindingContext

    return BotBindingContext(
        binding_id="whieda-advisor-bot", tenant=TENANT, bot_token_ref="env:T", webhook_secret_ref="env:S",
        bot_username="test_bot", status="active", processing_mode="core", bot_token="telegram-test-token", webhook_secret="s",
    )


def _row(outbox_id: int, payload: dict[str, Any], *, attempts: int = 0) -> dict[str, Any]:
    return {"outbox_id": outbox_id, "tenant_id": "whieda", "event_type": "test", "payload": payload, "attempts": attempts}


@pytest.fixture
def due_queue(monkeypatch):
    """process_due_notifications на фейковом пуле: rows — что «лежит в базе», finished — чем кончилась каждая строка."""
    from app.jobs import worker

    state: dict[str, Any] = {"rows": [], "finished": {}, "telegram": [], "max": []}

    async def fetch_all(conn, sql, params=None):
        return list(state["rows"])

    async def finish(outbox_id: int, *, status: str, attempts: int, error: str | None, retry: bool) -> None:
        state["finished"][outbox_id] = (status, attempts, retry)

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def telegram(*, chat_id: str, text: str, bot_token: str, reply_markup: Any = None) -> dict[str, Any]:
        state["telegram"].append((chat_id, text, bot_token))
        return {"ok": True}

    monkeypatch.setattr(worker, "get_pool", lambda: _Pool())
    monkeypatch.setattr(worker, "fetch_all", fetch_all)
    monkeypatch.setattr(worker, "_finish_due", finish)
    monkeypatch.setattr(worker, "_bury_stuck", noop)
    monkeypatch.setattr(worker, "_return_unattempted", noop)
    monkeypatch.setattr(worker, "send_telegram_text", telegram)
    return state, worker


async def test_due_notifications_send_max_rows_to_max_and_telegram_rows_to_telegram(due_queue, monkeypatch):
    state, worker = due_queue

    async def fake_send(**kwargs: Any) -> dict[str, Any]:
        state["max"].append(kwargs)
        return {"ok": True, "mid": "m-2"}

    monkeypatch.setattr(max_outbox, "send_max_message", fake_send)
    state["rows"] = [
        _row(1, max_outbox.build_max_outbox_payload(
            binding_id="whieda-advisor-bot", user_id=42, text="В Max", buttons=[[max_button_callback("Ок", "ok")]],
            attachments=[{"type": "video", "token": "vid-1"}])),
        _row(2, {"binding_id": "whieda-advisor-bot", "chat_id": "555", "text": "В Telegram"}),
    ]
    sent = await worker.process_due_notifications({"whieda": _binding()})
    assert sent == 2
    assert state["finished"] == {1: ("done", 1, False), 2: ("done", 1, False)}
    assert state["telegram"] == [("555", "В Telegram", "telegram-test-token")]
    (max_call,) = state["max"]
    assert max_call["user_id"] == "42" and max_call["chat_id"] is None and max_call["text"] == "В Max"
    assert max_call["attachments"] == [{"type": "video", "payload": {"token": "vid-1"}}]
    assert max_call["buttons"] == [[{"type": "callback", "text": "Ок", "payload": "ok"}]]


async def test_due_notifications_max_failures_use_the_same_statuses_and_attempts(due_queue, monkeypatch):
    import httpx

    state, worker = due_queue
    outcomes = {10: {"ok": False, "status": 400}, 11: {"ok": False, "status": 503}, 12: {"ok": False, "status": 503}, 13: "timeout"}

    async def fake_send(**kwargs: Any) -> dict[str, Any]:
        outcome = outcomes[int(kwargs["text"])]
        if outcome == "timeout":
            raise httpx.ReadTimeout("slow")
        return outcome

    monkeypatch.setattr(max_outbox, "send_max_message", fake_send)
    state["rows"] = [
        _row(10, {"channel": "max", "binding_id": "whieda-advisor-bot", "user_id": "42", "text": "10"}),
        _row(11, {"channel": "max", "binding_id": "whieda-advisor-bot", "user_id": "42", "text": "11"}),
        _row(12, {"channel": "max", "binding_id": "whieda-advisor-bot", "user_id": "42", "text": "12"}, attempts=worker.DUE_MAX_ATTEMPTS - 1),
        _row(13, {"channel": "max", "binding_id": "whieda-advisor-bot", "user_id": "42", "text": "13"}),
    ]
    assert await worker.process_due_notifications({"whieda": _binding()}) == 0
    assert state["finished"] == {
        10: ("dead", 1, False),  # 400 — окончательно
        11: ("scheduled", 1, True),  # 503 — повтор через 10 минут
        12: ("dead", worker.DUE_MAX_ATTEMPTS, False),  # последняя попытка
        13: ("dead", 1, False),  # таймаут — доставка неоднозначна, второго сообщения не будет
    }
    assert state["telegram"] == []
