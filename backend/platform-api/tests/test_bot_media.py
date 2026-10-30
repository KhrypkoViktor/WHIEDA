"""Хранилище медиа бота и слово СИСТЕМА (срез 8 бот-воронки, V45, 30.10.2026).

Без базы и сети: таблица bot_media — в памяти, Telegram и Max подделаны. Настоящий SQL, миграция и RLS —
в test_bot_media_postgres.py.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.bot_media import service
from app.funnel import system_word, texts
from app.max import processor as max_processor
from app.max.client import MaxKeyboardError, max_button_callback
from app.max.update_parser import MaxCallbackEvent, MaxEvent
from app.settings import get_settings
from app.telegram import bot_media_admin, delivery
from app.telegram.bindings import binding_context_scope
from app.telegram.bot_media_admin import BotMediaCommand, attachment_kind, parse_command, try_handle_bot_media_message
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage, parse_telegram_message
from app.tenancy import TenantContext

OWNER = 7001
STRANGER = 9999
TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
OTHER_TENANT = TenantContext(tenant_id="nsp", status="active", display_name="NSP", entitlements={})
BOT_TOKEN = "whieda-test-token"


# ---- хранилище в памяти ----------------------------------------------------------------------------


class FakeMedia:
    """Таблица bot_media в памяти: функции service подменяются на эти."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.connections = 0
        self.token_writes: list[tuple[str, str]] = []

    @asynccontextmanager
    async def tenant_connection(self, tenant_id):
        self.connections += 1
        yield SimpleNamespace(tenant_id=tenant_id)

    def add(self, key, kind="video", *, file_id="tg-file-1", max_token=None, caption=None, tenant="whieda"):
        row = {
            "media_id": len(self.rows) + 1, "tenant_id": tenant, "key": key, "kind": kind, "telegram_file_id": file_id,
            "max_token": max_token, "caption": caption, "source_note": None, "created_by": None,
            "created_at": None, "updated_at": None,
        }
        self.rows[(tenant, key)] = row
        return row

    async def get_media(self, conn, tenant_id, key):  # noqa: ARG002
        row = self.rows.get((tenant_id, key))
        return dict(row) if row else None

    async def list_media(self, conn, tenant_id):  # noqa: ARG002
        return [dict(r) for (t, _k), r in sorted(self.rows.items()) if t == tenant_id]

    async def save_media(self, conn, tenant_id, key, kind, telegram_file_id, *, caption=None, source_note=None, created_by=None):  # noqa: ARG002
        replaced = (tenant_id, key) in self.rows
        row = self.add(key, kind, file_id=telegram_file_id, caption=caption, tenant=tenant_id)
        row.update(source_note=source_note, created_by=created_by)
        return dict(row), replaced

    async def delete_media(self, conn, tenant_id, key):  # noqa: ARG002
        return self.rows.pop((tenant_id, key), None) is not None

    async def set_max_token(self, conn, tenant_id, key, token, *, telegram_file_id):  # noqa: ARG002
        row = self.rows.get((tenant_id, key))
        if row is None or row["telegram_file_id"] != telegram_file_id or row["max_token"]:
            return False
        row["max_token"] = token
        self.token_writes.append((key, token))
        return True


@pytest.fixture
def store():
    fake = FakeMedia()
    with ExitStack() as stack:
        for name in ("get_media", "list_media", "save_media", "delete_media", "set_max_token"):
            stack.enter_context(patch.object(service, name, getattr(fake, name)))
        stack.enter_context(patch.object(service, "tenant_connection", fake.tenant_connection))
        stack.enter_context(patch.object(bot_media_admin, "tenant_connection", fake.tenant_connection))
        yield fake


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.delenv("PLATFORM_SUPER_ADMIN_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def said():
    out: list[str] = []
    with patch.object(bot_media_admin, "_deliver", AsyncMock(side_effect=lambda chat, text: out.append(text))):
        yield out


# ---- обновления Telegram ---------------------------------------------------------------------------


def _update(message: dict[str, Any], user_id: int = OWNER, chat_type: str = "private") -> dict[str, Any]:
    return {"update_id": 1, "message": {"message_id": 11, "chat": {"id": user_id, "type": chat_type}, "from": {"id": user_id}, **message}}


VIDEO = {"video": {"file_id": "V1", "duration": 30}}
VOICE = {"voice": {"file_id": "VO1", "duration": 5}}
NOTE = {"video_note": {"file_id": "N1", "length": 240}}
AUDIO = {"audio": {"file_id": "A1"}}
PHOTO = {"photo": [{"file_id": "P-small"}, {"file_id": "P-big"}]}
DOCUMENT = {"document": {"file_id": "D1", "file_name": "tour.mp4"}}
STICKER = {"sticker": {"file_id": "S1"}}
GIF = {"animation": {"file_id": "G1"}, "document": {"file_id": "G1"}}
ANIMATION_ONLY = {"animation": {"file_id": "G2"}}


def _msg(update: dict[str, Any]) -> TelegramMessage:
    msg = parse_telegram_message(update)
    assert msg is not None
    return msg


async def upload(text: str, attachment: dict[str, Any] | None = None, **kw) -> dict | None:
    message = {"caption": text, **(attachment or {})} if attachment else {"text": text}
    return await try_handle_bot_media_message(TENANT, _msg(_update(message, **kw)), trace_id="t1")


# ---- разбор команды --------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "has_file", "expected"), [
    ("медиа", False, BotMediaCommand("list")),
    ("Медиа", False, BotMediaCommand("list")),
    ("  МЕДИА  ", False, BotMediaCommand("list")),
    ("медиа system_tour", True, BotMediaCommand("save", key="system_tour")),
    ("Медиа System_Tour", True, BotMediaCommand("save", key="system_tour")),   # ключ хранится строчными
    ("медиа a-1_b", True, BotMediaCommand("save", key="a-1_b")),
    ("медиа system_tour", False, BotMediaCommand("save", key="system_tour")),  # без файла — подсказка при обработке
    ("медиа " + "k" * 64, True, BotMediaCommand("save", key="k" * 64)),
    ("медиа удалить system_tour", False, BotMediaCommand("delete", key="system_tour")),
    ("Медиа УДАЛИТЬ System_Tour", False, BotMediaCommand("delete", key="system_tour")),
])
def test_commands_are_recognised(text, has_file, expected):
    assert parse_command(text, has_file=has_file) == expected


@pytest.mark.parametrize(("text", "hint"), [
    ("медиа удалить", bot_media_admin.USAGE_DELETE),
    ("медиа удалить a b", bot_media_admin.USAGE_DELETE),
    ("медиа удалить тур", bot_media_admin.USAGE_DELETE),
])
def test_delete_with_a_wrong_format_gets_a_hint(text, hint):
    assert parse_command(text) == BotMediaCommand("usage", hint=hint)


@pytest.mark.parametrize("text", ["медиа тур", "медиа два слова", "медиа " + "k" * 65, "медиа a.b", "медиа a/b"])
def test_a_bad_key_with_a_file_gets_a_hint_and_without_a_file_is_ordinary_text(text):
    assert parse_command(text, has_file=True) == BotMediaCommand("usage", hint=bot_media_admin.USAGE_KEY)
    assert parse_command(text, has_file=False) is None


def test_a_bare_word_with_a_file_asks_for_a_key():
    assert parse_command("медиа", has_file=True) == BotMediaCommand("usage", hint=bot_media_admin.NO_KEY_FOR_FILE)


@pytest.mark.parametrize("text", ["", None, "привет", "система", "медиаплан на неделю", "про медиа system_tour", "/media"])
def test_other_text_is_not_a_command(text):
    assert parse_command(text) is None
    assert parse_command(text, has_file=True) is None


@pytest.mark.parametrize(("attachment", "kind"), [
    (VIDEO, "video"), (VOICE, "voice"), (NOTE, "video_note"), (AUDIO, "audio"), (PHOTO, "photo"),
    (DOCUMENT, "document"), (GIF, "document"), (STICKER, None), (ANIMATION_ONLY, None),
])
def test_kind_comes_from_the_attachment(attachment, kind):
    assert attachment_kind(_msg(_update({"caption": "медиа k", **attachment}))) == kind


def test_no_attachment_has_no_kind():
    assert attachment_kind(_msg(_update({"text": "медиа k"}))) is None


# ---- загрузка и команды владельца ------------------------------------------------------------------


@pytest.mark.parametrize(("attachment", "kind", "file_id"), [
    (VIDEO, "video", "V1"), (VOICE, "voice", "VO1"), (NOTE, "video_note", "N1"), (AUDIO, "audio", "A1"),
    (PHOTO, "photo", "P-big"), (DOCUMENT, "document", "D1"),
])
async def test_owner_upload_saves_the_file_id_and_the_kind(owner_env, store, said, attachment, kind, file_id):
    result = await upload("медиа system_tour", attachment)
    assert said == [f"Сохранено: system_tour ({kind})"]
    assert result["status"] == "saved" and result["kind"] == kind and result["key"] == "system_tour"
    row = store.rows[("whieda", "system_tour")]
    assert (row["kind"], row["telegram_file_id"], row["max_token"]) == (kind, file_id, None)
    assert row["created_by"] == f"telegram:{OWNER}" and row["source_note"] == f"bot=unknown telegram:{OWNER}:11"


async def test_the_key_is_stored_in_lower_case(owner_env, store, said):
    await upload("Медиа System_Tour", VIDEO)
    assert said == ["Сохранено: system_tour (video)"] and ("whieda", "system_tour") in store.rows


async def test_the_same_key_replaces_the_file_and_says_so(owner_env, store, said):
    store.add("system_tour", "video", file_id="OLD", max_token="max-old")
    result = await upload("медиа system_tour", NOTE)
    assert said == ["Сохранено: system_tour (video_note)\nПрежний файл заменён."]
    assert result["status"] == "replaced"
    assert store.rows[("whieda", "system_tour")]["telegram_file_id"] == "N1"


async def test_the_source_note_names_the_bot_that_received_the_file(owner_env, store, said, whieda_bot_binding):
    with binding_context_scope(whieda_bot_binding):
        await upload("медиа k", VIDEO)
    assert store.rows[("whieda", "k")]["source_note"] == f"bot=whieda-test-binding telegram:{OWNER}:11"


async def test_the_reply_never_shows_the_file_id(owner_env, store, said):
    await upload("медиа k", VIDEO)
    assert "V1" not in said[0]


async def test_a_caption_without_a_file_gets_a_hint_and_writes_nothing(owner_env, store, said):
    result = await upload("медиа system_tour")
    assert said == [bot_media_admin.NO_FILE] and result["status"] == "no_file" and store.rows == {}


async def test_an_unsupported_attachment_is_refused(owner_env, store, said):
    result = await upload("медиа k", STICKER)
    assert said == [bot_media_admin.UNSUPPORTED] and result["status"] == "unsupported" and store.rows == {}
    assert store.connections == 0


async def test_a_wrong_format_with_a_file_gets_a_hint_and_never_opens_the_database(owner_env, store, said):
    for caption, hint in (("медиа тур", bot_media_admin.USAGE_KEY), ("медиа", bot_media_admin.NO_KEY_FOR_FILE)):
        result = await upload(caption, VIDEO)
        assert result["status"] == "invalid_command"
        assert said[-1] == hint
    assert store.connections == 0 and store.rows == {}


async def test_list_shows_keys_kinds_and_whether_max_has_the_file(owner_env, store, said):
    assert (await upload("медиа"))["status"] == "listed"
    assert said == [bot_media_admin.EMPTY_LIST]
    store.add("system_tour", "video", max_token="tok")
    store.add("intro_voice", "voice")
    await upload("медиа")
    lines = said[-1].splitlines()
    assert lines[0].startswith("Медиа бота")
    assert lines[1].startswith("intro_voice · voice · ") and "нет, загрузится при первой отправке" in lines[1]
    assert lines[2].startswith("system_tour · video · ") and lines[2].endswith("· есть")


async def test_the_list_is_the_tenants_own(owner_env, store, said):
    store.add("alien", "video", tenant="nsp")
    await upload("медиа")
    assert said == [bot_media_admin.EMPTY_LIST]


async def test_delete_removes_the_key(owner_env, store, said):
    store.add("system_tour")
    assert (await upload("медиа удалить system_tour"))["status"] == "deleted"
    assert said == ["Удалено: system_tour"] and store.rows == {}
    assert (await upload("медиа удалить system_tour"))["status"] == "not_found"
    assert said[-1] == "Такого ключа нет: system_tour. Список: медиа"


async def test_delete_hint_for_a_wrong_format(owner_env, store, said):
    assert (await upload("медиа удалить"))["status"] == "invalid_command"
    assert said == [bot_media_admin.USAGE_DELETE]


async def test_not_the_owner_group_or_ordinary_phrase_is_not_answered(owner_env, store, said):
    assert await upload("медиа system_tour", VIDEO, user_id=STRANGER) is None
    assert await upload("медиа", user_id=STRANGER) is None
    assert await upload("медиа system_tour", VIDEO, chat_type="group") is None
    assert await upload("медиа system_tour", VIDEO, chat_type="supergroup") is None
    assert await upload("медиа план на неделю") is None          # обычная фраза владельца
    assert await upload("привет") is None
    # файл без подписи — не команда
    assert await try_handle_bot_media_message(TENANT, _msg(_update(VOICE)), trace_id="t1") is None
    assert said == [] and store.connections == 0 and store.rows == {}


async def test_without_a_configured_owner_nobody_is_the_owner(monkeypatch, store, said):
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    get_settings.cache_clear()
    try:
        assert await upload("медиа") is None and said == []
    finally:
        get_settings.cache_clear()


# ---- отправка в Telegram: метод по виду ------------------------------------------------------------


class Recorder:
    def __init__(self, ok: bool = True, status: int = 200) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ok, self.status = ok, status

    async def post(self, url: str, payload: dict[str, Any], timeout_sec: float):  # noqa: ARG002
        self.calls.append((url, payload))
        body = {"ok": True, "result": {"message_id": 77}} if self.ok else {"ok": False, "description": "Bad Request: wrong file identifier"}
        return body, self.status


@pytest.fixture
def telegram():
    rec = Recorder()
    with patch.object(delivery, "_post_telegram", rec.post):
        yield rec


@pytest.mark.parametrize(("kind", "method", "field"), [
    ("video", "sendVideo", "video"), ("voice", "sendVoice", "voice"), ("video_note", "sendVideoNote", "video_note"),
    ("photo", "sendPhoto", "photo"), ("audio", "sendAudio", "audio"), ("document", "sendDocument", "document"),
])
async def test_the_bot_api_method_follows_the_kind(telegram, kind, method, field):
    result = await delivery.send_telegram_media(42, kind, "FILE", bot_token=BOT_TOKEN)
    (url, payload), = telegram.calls
    assert url.endswith(f"/bot{BOT_TOKEN}/{method}")
    assert payload["chat_id"] == "42" and payload[field] == "FILE"
    assert result == {"ok": True, "message_id": 77}


async def test_media_methods_cover_every_stored_kind():
    assert set(delivery.MEDIA_METHODS) == set(service.KINDS)
    assert set(service.MAX_KIND) == set(service.KINDS)


async def test_caption_markup_and_streaming(telegram):
    markup = {"inline_keyboard": [[{"text": "Да", "callback_data": "x"}]]}
    await delivery.send_telegram_media(42, "video", "F", bot_token=BOT_TOKEN, caption="Это <b>тур</b> & <script>", reply_markup=markup)
    payload = telegram.calls[0][1]
    assert payload["caption"] == "Это <b>тур</b> &amp; &lt;script&gt;" and payload["parse_mode"] == "HTML"
    assert payload["reply_markup"] == markup and payload["supports_streaming"] is True


async def test_a_video_note_has_no_caption(telegram):
    await delivery.send_telegram_media(42, "video_note", "F", bot_token=BOT_TOKEN, caption="подпись")
    assert "caption" not in telegram.calls[0][1] and "parse_mode" not in telegram.calls[0][1]


async def test_no_caption_no_markup_keeps_the_payload_minimal(telegram):
    await delivery.send_telegram_media(42, "voice", "F", bot_token=BOT_TOKEN, caption="  ")
    assert telegram.calls[0][1] == {"chat_id": "42", "voice": "F"}


async def test_a_caption_over_the_limit_is_refused_not_cut(telegram):
    with pytest.raises(ValueError, match="telegram_caption_too_long"):
        await delivery.send_telegram_media(42, "photo", "F", bot_token=BOT_TOKEN, caption="я" * 1025)
    assert telegram.calls == []
    await delivery.send_telegram_media(42, "photo", "F", bot_token=BOT_TOKEN, caption="я" * 1024)
    assert len(telegram.calls) == 1


def test_caption_fits_counts_the_escaped_text():
    assert delivery.telegram_caption_fits("video", None) and delivery.telegram_caption_fits("video_note", "")
    assert not delivery.telegram_caption_fits("video_note", "подпись")
    assert delivery.telegram_caption_fits("video", "я" * 1024) and not delivery.telegram_caption_fits("video", "я" * 1025)
    assert not delivery.telegram_caption_fits("video", "&" * 300)  # «&amp;» длиннее самого знака


async def test_an_unknown_kind_and_an_empty_file_id(telegram):
    with pytest.raises(ValueError, match="telegram_media_kind_unknown"):
        await delivery.send_telegram_media(42, "sticker", "F", bot_token=BOT_TOKEN)
    assert await delivery.send_telegram_media(42, "video", "  ", bot_token=BOT_TOKEN) == {"ok": False, "skipped": True}
    assert telegram.calls == []


async def test_a_telegram_refusal_is_returned_not_raised():
    rec = Recorder(ok=False, status=400)
    with patch.object(delivery, "_post_telegram", rec.post):
        result = await delivery.send_telegram_media(42, "video", "F", bot_token=BOT_TOKEN)
    assert result["ok"] is False and result["status_code"] == 400


async def test_a_refusal_inside_the_durable_worker_raises_so_the_outbox_retries():
    rec = Recorder(ok=False, status=500)
    with patch.object(delivery, "_post_telegram", rec.post), delivery.outbound_binding_guard(BOT_TOKEN):
        with pytest.raises(delivery.TelegramDeliveryError):
            await delivery.send_telegram_media(42, "video", "F", bot_token=BOT_TOKEN)


async def test_media_is_not_queued_into_a_delivery_plan(telegram):
    with delivery.capture_delivery_plan() as plan:
        await delivery.send_telegram_media(42, "video", "F", bot_token=BOT_TOKEN)
    assert plan == [] and len(telegram.calls) == 1


# ---- скачивание файла из Telegram ------------------------------------------------------------------


class FakeHttp:
    def __init__(self, status: int = 200, body: bytes = b"BYTES") -> None:
        self.status, self.body, self.urls = status, body, []

    def client(self, *args, **kwargs):  # noqa: ARG002
        outer = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                outer.urls.append(url)
                return SimpleNamespace(status_code=outer.status, content=outer.body)

        return Client()


async def test_download_asks_get_file_then_fetches_the_path():
    http = FakeHttp()
    rec = Recorder()

    async def get_file(url, payload, timeout_sec):  # noqa: ARG001
        rec.calls.append((url, payload))
        return {"ok": True, "result": {"file_path": "videos/file_9.mp4"}}, 200

    with patch.object(delivery, "_post_telegram", get_file), patch.object(delivery.httpx, "AsyncClient", http.client):
        body, name = await delivery.download_telegram_file("FID", bot_token=BOT_TOKEN)
    assert (body, name) == (b"BYTES", "file_9.mp4")
    assert rec.calls[0][0].endswith(f"/bot{BOT_TOKEN}/getFile") and rec.calls[0][1] == {"file_id": "FID"}
    assert http.urls == [f"https://api.telegram.org/file/bot{BOT_TOKEN}/videos/file_9.mp4"]


@pytest.mark.parametrize("reply", [({"ok": False, "description": "Bad Request: file is too big"}, 400), ({"ok": True, "result": {}}, 200)])
async def test_download_without_a_path_is_unavailable(reply):
    async def get_file(url, payload, timeout_sec):  # noqa: ARG001
        return reply

    with patch.object(delivery, "_post_telegram", get_file):
        with pytest.raises(delivery.TelegramFileUnavailable):
            await delivery.download_telegram_file("FID", bot_token=BOT_TOKEN)


async def test_download_refuses_a_too_large_body_and_http_errors():
    async def get_file(url, payload, timeout_sec):  # noqa: ARG001
        return {"ok": True, "result": {"file_path": "a/b.mp4"}}, 200

    for http, limit in ((FakeHttp(body=b"x" * 11), 10), (FakeHttp(status=404), 100)):
        with patch.object(delivery, "_post_telegram", get_file), patch.object(delivery.httpx, "AsyncClient", http.client):
            with pytest.raises(delivery.TelegramFileUnavailable):
                await delivery.download_telegram_file("FID", bot_token=BOT_TOKEN, max_bytes=limit)


# ---- service.send_media ----------------------------------------------------------------------------


MARKUP = {"inline_keyboard": [[{"text": "Да", "callback_data": "funnel:meet"}]]}
BUTTONS = [[max_button_callback("Да", "funnel:meet")]]
BINDING = SimpleNamespace(bot_token=BOT_TOKEN)


@pytest.fixture
def channels(store):
    """Telegram и Max подделаны: tg_media / tg_text — что ушло в Telegram, max_send / max_upload / download — в Max."""
    state = SimpleNamespace(
        tg_media=AsyncMock(return_value={"ok": True, "message_id": 5}),
        tg_text=AsyncMock(return_value={"ok": True}),
        max_send=AsyncMock(return_value={"ok": True, "mid": "m1"}),
        max_upload=AsyncMock(return_value={"type": "video", "payload": {"token": "max-tok-1"}}),
        download=AsyncMock(return_value=(b"BODY", "file_1.mp4")),
        binding=AsyncMock(return_value=BINDING),
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "send_telegram_media", state.tg_media))
        stack.enter_context(patch.object(service, "send_telegram_text", state.tg_text))
        stack.enter_context(patch.object(service, "send_max_attachment", state.max_send))
        stack.enter_context(patch.object(service, "upload_max_media", state.max_upload))
        stack.enter_context(patch.object(service, "download_telegram_file", state.download))
        stack.enter_context(patch.object(service, "resolve_bot_binding_context", state.binding))
        yield state


async def test_telegram_sends_by_file_id_with_the_rows_caption_and_markup(store, channels):
    store.add("system_tour", "video", file_id="FID", caption="Подпись строки")
    result = await service.send_media(None, "whieda", "system_tour", channel="telegram", chat_id=42, reply_markup=MARKUP, binding=BINDING)
    assert result == {"ok": True, "status": "sent", "channel": "telegram", "key": "system_tour", "kind": "video"}
    channels.tg_media.assert_awaited_once_with(42, "video", "FID", bot_token=BOT_TOKEN, caption="Подпись строки", reply_markup=MARKUP)
    channels.tg_text.assert_not_awaited()


async def test_an_explicit_caption_wins_over_the_rows(store, channels):
    store.add("k", "photo", caption="строка")
    await service.send_media(None, "whieda", "k", channel="telegram", chat_id=42, caption="своя", binding=BINDING)
    assert channels.tg_media.await_args.kwargs["caption"] == "своя"


async def test_a_video_note_goes_bare_and_the_caption_with_buttons_follows_as_text(store, channels):
    store.add("note", "video_note", file_id="N")
    result = await service.send_media(None, "whieda", "note", channel="telegram", chat_id=42, caption="Подпись", reply_markup=MARKUP, binding=BINDING)
    assert result["ok"] and result["text_sent"] is True
    channels.tg_media.assert_awaited_once_with(42, "video_note", "N", bot_token=BOT_TOKEN, caption=None, reply_markup=None)
    channels.tg_text.assert_awaited_once_with(chat_id="42", text="Подпись", bot_token=BOT_TOKEN, reply_markup=MARKUP)


async def test_a_video_note_without_caption_carries_the_buttons_itself(store, channels):
    store.add("note", "video_note", file_id="N")
    await service.send_media(None, "whieda", "note", channel="telegram", chat_id=42, reply_markup=MARKUP, binding=BINDING)
    assert channels.tg_media.await_args.kwargs["reply_markup"] == MARKUP
    channels.tg_text.assert_not_awaited()


async def test_a_caption_over_the_limit_follows_as_text(store, channels):
    store.add("tour", "video", file_id="V")
    long_caption = "я" * 1500
    result = await service.send_media(None, "whieda", "tour", channel="telegram", chat_id=42, caption=long_caption, reply_markup=MARKUP, binding=BINDING)
    assert result["ok"]
    assert channels.tg_media.await_args.kwargs["caption"] is None and channels.tg_media.await_args.kwargs["reply_markup"] is None
    assert channels.tg_text.await_args.kwargs["text"] == long_caption and channels.tg_text.await_args.kwargs["reply_markup"] == MARKUP


async def test_a_failed_follow_up_text_does_not_undo_the_sent_file(store, channels):
    store.add("note", "video_note")
    channels.tg_text.side_effect = RuntimeError("boom")
    result = await service.send_media(None, "whieda", "note", channel="telegram", chat_id=42, caption="Подпись", binding=BINDING)
    assert result["ok"] is True and result["text_sent"] is False


async def test_telegram_refusal_and_exception_are_failed_status(store, channels):
    store.add("k", "video")
    channels.tg_media.return_value = {"ok": False, "status_code": 400}
    assert (await service.send_media(None, "whieda", "k", channel="telegram", chat_id=1, binding=BINDING))["status"] == "failed"
    channels.tg_media.side_effect = delivery.TelegramDeliveryUnknown("ambiguous")
    assert (await service.send_media(None, "whieda", "k", channel="telegram", chat_id=1, binding=BINDING))["status"] == "failed"


async def test_a_missing_key_and_a_row_without_a_file(store, channels):
    assert await service.send_media(None, "whieda", "nope", channel="telegram", chat_id=1, binding=BINDING) == {"ok": False, "status": "missing", "key": "nope"}
    assert (await service.send_media(None, "whieda", "Bad Key!", channel="max", user_id=1))["status"] == "missing"
    store.add("empty", "video", file_id=None)
    assert (await service.send_media(None, "whieda", "empty", channel="telegram", chat_id=1, binding=BINDING))["status"] == "no_file"
    assert (await service.send_media(None, "whieda", "empty", channel="max", user_id=1))["status"] == "no_file"
    channels.tg_media.assert_not_awaited()
    channels.max_send.assert_not_awaited()


async def test_keys_of_another_tenant_are_not_found(store, channels):
    store.add("tour", "video", tenant="nsp")
    assert (await service.send_media(None, "whieda", "tour", channel="telegram", chat_id=1, binding=BINDING))["status"] == "missing"


async def test_wrong_arguments_are_programming_errors(store, channels):
    store.add("k", "video")
    with pytest.raises(ValueError, match="channel"):
        await service.send_media(None, "whieda", "k", channel="sms", chat_id=1)
    with pytest.raises(ValueError, match="chat_id"):
        await service.send_media(None, "whieda", "k", channel="telegram", binding=BINDING)
    with pytest.raises(ValueError, match="recipient"):
        await service.send_media(None, "whieda", "k", channel="max")


async def test_the_caller_s_connection_is_used_without_opening_another(store, channels):
    store.add("k", "video", max_token="tok")
    conn = SimpleNamespace(tenant_id="whieda")
    await service.send_media(conn, "whieda", "k", channel="max", user_id=7)
    assert store.connections == 0


# ---- Max: токен и ленивая загрузка -----------------------------------------------------------------


@pytest.mark.parametrize(("kind", "max_kind"), [
    ("video", "video"), ("video_note", "video"), ("voice", "audio"), ("audio", "audio"), ("photo", "image"), ("document", "file"),
])
async def test_max_sends_by_the_stored_token_with_the_mapped_type(store, channels, kind, max_kind):
    store.add("k", kind, max_token="TOK", caption="Подпись")
    result = await service.send_media(None, "whieda", "k", channel="max", user_id=42, chat_id=555, buttons=BUTTONS)
    assert result == {"ok": True, "status": "sent", "channel": "max", "key": "k", "kind": kind}
    channels.max_send.assert_awaited_once_with(user_id=42, chat_id=555, kind=max_kind, token="TOK", text="Подпись", buttons=BUTTONS)
    channels.download.assert_not_awaited()
    channels.max_upload.assert_not_awaited()


@pytest.mark.parametrize(("kind", "max_kind", "name"), [
    ("video", "video", "file_1.mp4"), ("video_note", "video", "file_1.mp4"), ("voice", "audio", "file_1.mp4"),
    ("photo", "image", "file_1.mp4"), ("document", "file", "file_1.mp4"),
])
async def test_max_lazy_upload_downloads_from_telegram_uploads_and_remembers_the_token(store, channels, kind, max_kind, name):
    store.add("tour", kind, file_id="FID")
    result = await service.send_media(None, "whieda", "tour", channel="max", user_id=42, caption="Подпись", buttons=BUTTONS)
    assert result["ok"] is True
    channels.download.assert_awaited_once_with("FID", bot_token=BOT_TOKEN)
    channels.max_upload.assert_awaited_once_with(max_kind, b"BODY", filename=name)
    assert store.token_writes == [("tour", "max-tok-1")] and store.rows[("whieda", "tour")]["max_token"] == "max-tok-1"
    channels.max_send.assert_awaited_once_with(user_id=42, chat_id=None, kind=max_kind, token="max-tok-1", text="Подпись", buttons=BUTTONS)
    channels.binding.assert_awaited_once_with(service.WWC_BINDING_ID)


async def test_the_second_send_uses_the_saved_token_and_does_not_download_again(store, channels):
    store.add("tour", "video", file_id="FID")
    await service.send_media(None, "whieda", "tour", channel="max", user_id=42)
    await service.send_media(None, "whieda", "tour", channel="max", user_id=43)
    channels.download.assert_awaited_once()
    channels.max_upload.assert_awaited_once()
    assert [c.kwargs["token"] for c in channels.max_send.await_args_list] == ["max-tok-1", "max-tok-1"]


async def test_two_simultaneous_first_sends_upload_the_file_once(store, channels):
    store.add("tour", "video", file_id="FID")

    async def slow_download(*args, **kwargs):  # noqa: ARG001
        await asyncio.sleep(0.01)
        return b"BODY", "f.mp4"

    channels.download.side_effect = slow_download
    results = await asyncio.gather(*(service.send_media(None, "whieda", "tour", channel="max", user_id=n) for n in (1, 2, 3)))
    assert all(r["ok"] for r in results)
    channels.max_upload.assert_awaited_once()
    assert len(channels.max_send.await_args_list) == 3


async def test_lazy_upload_uses_the_passed_binding_and_the_callers_connection(store, channels):
    store.add("tour", "video", file_id="FID")
    other = SimpleNamespace(bot_token="other-token")
    await service.send_media(SimpleNamespace(), "whieda", "tour", channel="max", user_id=1, binding=other)
    channels.download.assert_awaited_once_with("FID", bot_token="other-token")
    channels.binding.assert_not_awaited()
    assert store.connections == 0


@pytest.mark.parametrize(("failure", "status"), [
    ("download_unavailable", "download_failed"), ("download_error", "download_failed"),
    ("upload_error", "upload_failed"), ("no_token", "upload_failed"),
])
async def test_max_lazy_upload_failures_are_statuses_and_send_nothing(store, channels, failure, status):
    store.add("tour", "video", file_id="FID")
    if failure == "download_unavailable":
        channels.download.side_effect = delivery.TelegramFileUnavailable("telegram_get_file_failed:400")
    elif failure == "download_error":
        channels.download.side_effect = RuntimeError("connection reset")
    elif failure == "upload_error":
        channels.max_upload.side_effect = RuntimeError("503")
    else:
        channels.max_upload.return_value = {"type": "video", "payload": {}}
    result = await service.send_media(None, "whieda", "tour", channel="max", user_id=1)
    assert result["ok"] is False and result["status"] == status
    channels.max_send.assert_not_awaited()
    assert store.token_writes == [] and store.rows[("whieda", "tour")]["max_token"] is None


async def test_without_a_telegram_binding_the_lazy_upload_is_unavailable(store, channels):
    channels.binding.return_value = None
    store.add("tour", "video", file_id="FID")
    assert (await service.send_media(None, "whieda", "tour", channel="max", user_id=1))["status"] == "no_telegram_binding"
    channels.download.assert_not_awaited()


async def test_a_telegram_send_without_any_binding_is_a_status(store, channels):
    channels.binding.return_value = None
    store.add("tour", "video")
    with patch.object(service, "current_bot_binding", side_effect=RuntimeError("not set")):
        assert (await service.send_media(None, "whieda", "tour", channel="telegram", chat_id=1))["status"] == "no_telegram_binding"


async def test_bad_buttons_fail_before_the_file_is_downloaded(store, channels):
    store.add("tour", "video", file_id="FID")
    with pytest.raises(MaxKeyboardError):
        await service.send_media(None, "whieda", "tour", channel="max", user_id=1, buttons=[[{"type": "callback", "text": "", "payload": "x"}]])
    channels.download.assert_not_awaited()
    channels.max_upload.assert_not_awaited()


async def test_a_failed_token_save_still_sends_the_message(store, channels):
    store.add("tour", "video", file_id="FID")
    with patch.object(service, "set_max_token", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await service.send_media(None, "whieda", "tour", channel="max", user_id=1)
    assert result["ok"] is True
    channels.max_send.assert_awaited_once()


async def test_max_refusal_and_exception_are_failed_status(store, channels):
    store.add("k", "video", max_token="T")
    channels.max_send.return_value = {"ok": False, "status": 400}
    assert (await service.send_media(None, "whieda", "k", channel="max", user_id=1))["status"] == "failed"
    channels.max_send.side_effect = RuntimeError("PLATFORM_MAX_BOT_TOKEN is not set")
    assert (await service.send_media(None, "whieda", "k", channel="max", user_id=1))["status"] == "failed"


async def test_a_replaced_file_resets_the_token_so_max_uploads_the_new_one(store, channels):
    """Замена файла командой владельца даёт строку без токена Max: следующая отправка снова грузит файл."""
    store.add("tour", "video", file_id="OLD", max_token="old-token")
    store.rows[("whieda", "tour")].update(telegram_file_id="NEW", max_token=None)
    await service.send_media(None, "whieda", "tour", channel="max", user_id=1)
    channels.download.assert_awaited_once_with("NEW", bot_token=BOT_TOKEN)


async def test_logs_never_carry_the_bot_token_or_the_attachment_token(store, channels, caplog):
    store.add("tour", "video", file_id="FID")
    channels.download.side_effect = RuntimeError(f"https://api.telegram.org/file/bot{BOT_TOKEN}/x failed")
    with caplog.at_level(logging.DEBUG):
        await service.send_media(None, "whieda", "tour", channel="max", user_id=1)
        channels.download.side_effect = None
        await service.send_media(None, "whieda", "tour", channel="max", user_id=1)
    text = " ".join(f"{r.getMessage()} {sorted(r.__dict__.items())}" for r in caplog.records)
    assert BOT_TOKEN not in text and "max-tok-1" not in text


def test_service_key_rules():
    assert service.normalize_key(" System_Tour ") == "system_tour"
    for bad in ("", " ", "тур", "a b", "a.b", "k" * 65, None):
        assert service.normalize_key(bad) is None


async def test_save_media_validates_before_touching_the_database():
    with pytest.raises(ValueError, match="key"):
        await service.save_media(None, "whieda", "Плохой ключ", "video", "F")
    with pytest.raises(ValueError, match="kind"):
        await service.save_media(None, "whieda", "k", "gif", "F")
    with pytest.raises(ValueError, match="file_id"):
        await service.save_media(None, "whieda", "k", "video", "  ")


# ---- слово СИСТЕМА ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["система", "Система", "СИСТЕМА", "  система  ", "система!", "система.", "СИСТЕМА !", "\tсистема\n"])
def test_the_word_is_recognised(text):
    assert system_word.is_system_word(text)


@pytest.mark.parametrize("text", [
    "", None, "системa?", "система?", "система бонусов", "моя система", "системы", "системами", "system", "система система",
    "система!!", "систе ма", "система,", "эта система",
])
def test_other_text_is_not_the_word(text):
    assert not system_word.is_system_word(text)


def test_the_two_buttons_are_the_same_in_telegram_and_max():
    assert system_word.telegram_markup() == {"inline_keyboard": [
        [{"text": "Живая встреча", "callback_data": "funnel:meet"}],
        [{"text": "Личный разбор", "callback_data": "funnel:consult"}],
    ]}
    assert system_word.max_buttons() == [
        [max_button_callback("Живая встреча", "funnel:meet")],
        [max_button_callback("Личный разбор", "funnel:consult")],
    ]


def _tg_msg(text: str, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(chat_id=42, user_id=42, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={})


@pytest.fixture
def tg_bot(whieda_bot_binding):
    """Бот WWC в Telegram: send_media и send_telegram_text подделаны, привязка бота установлена."""
    state = SimpleNamespace(
        media=AsyncMock(return_value={"ok": True, "status": "sent", "kind": "video"}),
        text=AsyncMock(return_value={"ok": True}),
        answer=AsyncMock(return_value={"ok": True}),
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(system_word, "send_media", state.media))
        stack.enter_context(patch.object(system_word, "send_telegram_text", state.text))
        stack.enter_context(patch.object(system_word, "answer_callback_query", state.answer))
        stack.enter_context(binding_context_scope(whieda_bot_binding))
        yield state


async def test_telegram_word_sends_the_tour_with_caption_and_two_buttons(tg_bot):
    result = await system_word.try_handle_system_word(TENANT, _tg_msg("Система!"), trace_id="t1")
    assert result == {"ok": True, "route": "funnel_system", "trace_id": "t1", "status": "tour", "kind": "video"}
    tg_bot.media.assert_awaited_once_with(
        None, "whieda", "system_tour", channel="telegram", chat_id=42,
        caption=texts.SYSTEM_TOUR_CAPTION, reply_markup=system_word.telegram_markup(),
    )
    tg_bot.text.assert_not_awaited()


@pytest.mark.parametrize("outcome", [{"ok": False, "status": "missing"}, {"ok": False, "status": "failed"}, RuntimeError("db down")])
async def test_telegram_without_the_tour_answers_with_the_pending_text_and_the_same_buttons(tg_bot, outcome):
    if isinstance(outcome, Exception):
        tg_bot.media.side_effect = outcome
    else:
        tg_bot.media.return_value = outcome
    result = await system_word.try_handle_system_word(TENANT, _tg_msg("система"), trace_id="t1")
    assert result["status"] == "pending"
    tg_bot.text.assert_awaited_once_with(
        chat_id="42", text=texts.SYSTEM_TOUR_PENDING, bot_token=BOT_TOKEN, reply_markup=system_word.telegram_markup()
    )


@pytest.mark.parametrize(("tenant", "text", "chat_type"), [
    (OTHER_TENANT, "система", "private"),     # бот другого тенанта отвечает как раньше
    (TENANT, "система", "group"),
    (TENANT, "система", "supergroup"),
    (TENANT, "система бонусов", "private"),
    (TENANT, "привет", "private"),
])
async def test_telegram_word_ignores_everything_else(tg_bot, tenant, text, chat_type):
    assert await system_word.try_handle_system_word(tenant, _tg_msg(text, chat_type), trace_id="t1") is None
    tg_bot.media.assert_not_awaited()
    tg_bot.text.assert_not_awaited()


def _cb(data: str, chat_type: str = "private") -> TelegramCallbackQuery:
    return TelegramCallbackQuery(chat_id=42, user_id=42, callback_query_id="cq-1", data=data, chat_type=chat_type, raw={})


@pytest.mark.parametrize(("data", "placeholder"), [
    ("funnel:meet", texts.FUNNEL_MEET_PLACEHOLDER), ("funnel:consult", texts.FUNNEL_CONSULT_PLACEHOLDER),
])
async def test_telegram_buttons_answer_with_the_placeholder_texts(tg_bot, data, placeholder):
    result = await system_word.try_handle_funnel_callback(TENANT, _cb(data), trace_id="t1")
    assert result["route"] == "funnel_callback" and result["status"] == data.removeprefix("funnel:")
    tg_bot.answer.assert_awaited_once_with(callback_query_id="cq-1", bot_token=BOT_TOKEN)
    tg_bot.text.assert_awaited_once_with(chat_id="42", text=placeholder, bot_token=BOT_TOKEN)


@pytest.mark.parametrize(("tenant", "data"), [(TENANT, "funnel:other"), (TENANT, "consent:news:yes"), (OTHER_TENANT, "funnel:meet")])
async def test_telegram_callback_is_not_ours(tg_bot, tenant, data):
    assert await system_word.try_handle_funnel_callback(tenant, _cb(data), trace_id="t1") is None
    tg_bot.answer.assert_not_awaited()
    tg_bot.text.assert_not_awaited()


# ---- Max -------------------------------------------------------------------------------------------


def _max_event(text: str = "система", chat_id: int | None = 555) -> MaxEvent:
    return MaxEvent(kind="message", user_id=42, chat_id=chat_id, text=text, username=None, display_name="Инна", raw={})


@pytest.fixture
def max_bot(monkeypatch):
    state = SimpleNamespace(
        media=AsyncMock(return_value={"ok": True, "status": "sent", "kind": "video"}),
        text=AsyncMock(return_value={"ok": True}),
        advisor=AsyncMock(return_value={"answer_text": "Ответ советника", "answer_mode": "structured_business"}),
    )
    monkeypatch.setattr(system_word, "send_media", state.media)
    monkeypatch.setattr(system_word, "send_max_text", state.text)
    monkeypatch.setattr(max_processor, "send_max_text", state.text)
    monkeypatch.setattr(max_processor, "ensure_channel_actor", AsyncMock())
    monkeypatch.setattr(max_processor, "handle_structured_query", state.advisor)
    monkeypatch.setattr(max_processor, "should_deliver_telegram_response", lambda _r: True)
    return state


async def test_max_word_sends_the_tour_with_the_same_caption_and_buttons(max_bot):
    result = await max_processor.handle_max_message(TENANT, _max_event("СИСТЕМА"), "t1")
    assert result == {"ok": True, "route": "max_funnel_system", "trace_id": "t1", "status": "tour", "kind": "video"}
    max_bot.media.assert_awaited_once_with(
        None, "whieda", "system_tour", channel="max", chat_id=555, user_id=42,
        caption=texts.SYSTEM_TOUR_CAPTION, buttons=system_word.max_buttons(),
    )
    max_bot.text.assert_not_awaited()
    max_bot.advisor.assert_not_awaited()


async def test_max_word_without_the_tour_answers_with_the_pending_text_and_buttons(max_bot):
    max_bot.media.return_value = {"ok": False, "status": "download_failed"}
    result = await max_processor.handle_max_message(TENANT, _max_event("система."), "t1")
    assert result["status"] == "pending" and result["reason"] == "download_failed"
    max_bot.text.assert_awaited_once_with(chat_id=555, user_id=42, text=texts.SYSTEM_TOUR_PENDING, buttons=system_word.max_buttons())
    max_bot.advisor.assert_not_awaited()


async def test_max_word_survives_an_error_in_the_media_layer(max_bot):
    max_bot.media.side_effect = RuntimeError("db down")
    result = await max_processor.handle_max_message(TENANT, _max_event(), "t1")
    assert result["status"] == "pending"
    max_bot.text.assert_awaited_once()


async def test_max_word_without_chat_id_writes_to_the_user(max_bot):
    max_bot.media.return_value = {"ok": False, "status": "missing"}
    await max_processor.handle_max_message(TENANT, _max_event(chat_id=None), "t1")
    assert max_bot.text.await_args.kwargs["chat_id"] is None and max_bot.text.await_args.kwargs["user_id"] == 42


@pytest.mark.parametrize("text", ["система бонусов", "моя система", "привет"])
async def test_max_other_text_still_goes_to_the_advisor(max_bot, text):
    result = await max_processor.handle_max_message(TENANT, _max_event(text), "t1")
    assert result["route"] == "max_message"
    max_bot.advisor.assert_awaited_once()
    max_bot.media.assert_not_awaited()


async def test_max_existing_words_are_answered_as_before(max_bot, monkeypatch):
    monkeypatch.setattr(max_processor, "is_my_leads_text", lambda text: text == "Мои заявки")
    monkeypatch.setattr(max_processor, "my_leads_answer", AsyncMock(return_value=("Заявок нет", "empty")))
    result = await max_processor.handle_max_message(TENANT, _max_event("Мои заявки"), "t1")
    assert result["route"] == "max_my_leads"
    max_bot.media.assert_not_awaited()


def test_the_funnel_prefix_is_registered_in_the_max_dispatcher():
    found = max_processor.resolve_max_callback("funnel:meet")
    assert found is not None and found[0] == "funnel:" and found[1] is system_word.max_funnel_callback
    assert max_processor.resolve_max_callback("funnel:consult") is not None
    assert max_processor.resolve_max_callback("menu:open") is None


def _max_callback(payload: str) -> MaxCallbackEvent:
    return MaxCallbackEvent(
        callback_id="cb-1", payload=payload, user_id=42, chat_id=555, message_id="mid-1", chat_type="dialog",
        username=None, display_name="Инна", raw={},
    )


@pytest.mark.parametrize(("payload", "placeholder"), [
    ("funnel:meet", texts.FUNNEL_MEET_PLACEHOLDER), ("funnel:consult", texts.FUNNEL_CONSULT_PLACEHOLDER),
])
async def test_max_buttons_send_the_placeholder_as_a_new_message_and_acknowledge(max_bot, monkeypatch, payload, placeholder):
    answered = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(max_processor, "answer_max_callback", answered)
    result = await max_processor.process_max_event(TENANT, _max_callback(payload), "t1")
    assert result["status"] == "handled" and result["prefix"] == "funnel:" and result["answered"] is True
    max_bot.text.assert_awaited_once_with(chat_id=555, user_id=42, text=placeholder)
    # ответ на нажатие — пустой: текст сообщения с туром и его кнопки остаются как были
    answered.assert_awaited_once_with("cb-1", notification=None, text=None, buttons=None)


async def test_an_unknown_funnel_payload_is_a_quiet_answer(max_bot, monkeypatch):
    monkeypatch.setattr(max_processor, "answer_max_callback", AsyncMock(return_value={"ok": True}))
    result = await max_processor.process_max_event(TENANT, _max_callback("funnel:nope"), "t1")
    assert result["status"] == "handled"
    max_bot.text.assert_not_awaited()


# ---- через процессор Telegram ----------------------------------------------------------------------

_EARLIER_HANDLERS = (
    "try_handle_wish_message", "try_handle_profile_reject_reason", "try_handle_shop_message", "try_handle_support_message",
    "try_handle_referral_message", "try_handle_renewal_message", "try_handle_site_request_message", "try_start_renewal_by_text",
    "try_handle_billing_message", "try_handle_referral_admin_message", "try_handle_meetings_admin_message",
    "try_handle_login_identity_message", "try_handle_lead_email_message", "try_handle_profile_requests_command",
    "try_handle_rhythm_text", "try_handle_academy_text", "try_handle_crm_text", "handle_onboarding", "try_relay_user_message",
)
_NEXT = {"ok": True, "route": "next_handler"}


def _through_processor(stack: ExitStack) -> dict[str, AsyncMock]:
    """Цепочка до нужного места пуста; общие вызовы базы и сети подменены. next — следующий обработчик за нашими."""
    stack.enter_context(patch("app.telegram.processor._link_partner_chat", AsyncMock()))
    stack.enter_context(patch("app.telegram.processor.try_handle_marketing_consent_message", AsyncMock(return_value=None)))
    mocks: dict[str, AsyncMock] = {}
    for name in _EARLIER_HANDLERS:
        mocks[name] = stack.enter_context(patch(f"app.telegram.processor.{name}", AsyncMock(return_value=None)))
    mocks["support_media"] = stack.enter_context(patch("app.telegram.processor.try_handle_support_media", AsyncMock(return_value=None)))
    mocks["unrouted"] = stack.enter_context(
        patch("app.telegram.processor.accept_unrouted_attachment", AsyncMock(return_value={"ok": True, "route": "unrouted_attachment"}))
    )
    mocks["site_voice"] = stack.enter_context(patch("app.telegram.processor.try_handle_site_request_voice", AsyncMock(return_value=None)))
    mocks["service_text"] = stack.enter_context(patch("app.telegram.processor.try_handle_wwc_service_text", AsyncMock(return_value=_NEXT)))
    return mocks


async def test_the_word_goes_through_the_processor_to_our_handler(store, tg_bot, whieda_tenant, whieda_bot_binding):
    for text in ("система", "СИСТЕМА!", "  Система. "):
        tg_bot.media.reset_mock()
        with ExitStack() as stack:
            mocks = _through_processor(stack)
            result = await process_core_telegram_update(
                whieda_tenant, _update({"text": text}, user_id=42), "t1", binding=whieda_bot_binding
            )
        assert result["route"] == "funnel_system" and result["status"] == "tour", text
        tg_bot.media.assert_awaited_once()
        mocks["service_text"].assert_not_awaited()


async def test_other_words_still_reach_the_next_handler(store, tg_bot, whieda_tenant, whieda_bot_binding):
    for text in ("система бонусов", "расскажи про систему", "моя система", "системы"):
        with ExitStack() as stack:
            _through_processor(stack)
            result = await process_core_telegram_update(
                whieda_tenant, _update({"text": text}, user_id=42), "t1", binding=whieda_bot_binding
            )
        assert result == _NEXT, text
    tg_bot.media.assert_not_awaited()


async def test_an_open_support_ticket_keeps_the_word_with_the_administrator(store, tg_bot, whieda_tenant, whieda_bot_binding):
    """Системное слово стоит после пересылки в открытое обращение: человек в диалоге с поддержкой остаётся в нём."""
    with ExitStack() as stack:
        mocks = _through_processor(stack)
        mocks["try_relay_user_message"].return_value = {"ok": True, "route": "support_relay"}
        result = await process_core_telegram_update(whieda_tenant, _update({"text": "система"}, user_id=42), "t1", binding=whieda_bot_binding)
    assert result["route"] == "support_relay"
    tg_bot.media.assert_not_awaited()


async def test_the_owner_upload_is_taken_before_the_attachment_goes_to_support(owner_env, store, said, whieda_tenant, whieda_bot_binding):
    for attachment, kind in ((VIDEO, "video"), (VOICE, "voice"), (NOTE, "video_note"), (PHOTO, "photo"), (DOCUMENT, "document")):
        with ExitStack() as stack:
            mocks = _through_processor(stack)
            result = await process_core_telegram_update(
                whieda_tenant, _update({"caption": "медиа system_tour", **attachment}, user_id=OWNER), "t1", binding=whieda_bot_binding
            )
        assert result["route"] == "bot_media_admin" and result["kind"] == kind
        mocks["unrouted"].assert_not_awaited()
        mocks["support_media"].assert_not_awaited()
    assert said[-1] == "Сохранено: system_tour (document)\nПрежний файл заменён."


async def test_not_the_owner_the_same_file_goes_to_support_as_before(owner_env, store, said, whieda_tenant, whieda_bot_binding):
    with ExitStack() as stack:
        mocks = _through_processor(stack)
        result = await process_core_telegram_update(
            whieda_tenant, _update({"caption": "медиа system_tour", **VIDEO}, user_id=STRANGER), "t1", binding=whieda_bot_binding
        )
    assert result["route"] == "unrouted_attachment"
    mocks["unrouted"].assert_awaited_once()
    assert store.rows == {} and said == []


async def test_the_owner_s_file_without_the_word_goes_on_as_before(owner_env, store, said, whieda_tenant, whieda_bot_binding):
    with ExitStack() as stack:
        mocks = _through_processor(stack)
        result = await process_core_telegram_update(
            whieda_tenant, _update({"caption": "это чек", **PHOTO}, user_id=OWNER), "t1", binding=whieda_bot_binding
        )
    assert result["route"] == "unrouted_attachment"
    assert store.rows == {} and said == []


async def test_the_owner_list_command_goes_through_the_processor(owner_env, store, said, whieda_tenant, whieda_bot_binding):
    store.add("system_tour", "video")
    with ExitStack() as stack:
        _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update({"text": "медиа"}, user_id=OWNER), "t1", binding=whieda_bot_binding)
    assert result["route"] == "bot_media_admin" and result["status"] == "listed"
    assert said[0].startswith("Медиа бота")


async def test_the_funnel_callback_goes_through_the_processor(tg_bot, whieda_tenant, whieda_bot_binding):
    update = {"update_id": 5, "callback_query": {"id": "cq-9", "data": "funnel:consult", "from": {"id": 42},
                                                  "message": {"message_id": 3, "chat": {"id": 42, "type": "private"}}}}
    with patch("app.telegram.processor.try_handle_marketing_consent_callback", AsyncMock(return_value=None)):
        result = await process_core_telegram_update(whieda_tenant, update, "t1", binding=whieda_bot_binding)
    assert result["route"] == "funnel_callback" and result["status"] == "consult"
    tg_bot.text.assert_awaited_once()
