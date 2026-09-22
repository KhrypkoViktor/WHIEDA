"""Владелец получает сообщение на каждом шаге заявки на сайт (22.09.2026)."""
import asyncio

from app.telegram import site_requests as sr
from app.telegram.update_parser import TelegramMessage


def _msg(**kw) -> TelegramMessage:
    base = dict(chat_id=111, user_id=111, message_id=7, text="", chat_type="private", file_id=None, raw={}, username="partner")
    base.update(kw)
    return TelegramMessage(**base)


def test_owner_gets_step_alert_and_photo_copy(monkeypatch):
    sent, copied = [], []

    class S:
        platform_billing_owner_telegram_id = "999"

    monkeypatch.setattr(sr, "get_settings", lambda: S())

    async def deliver(chat_id, text, *, reply_markup=None):
        sent.append((chat_id, text))

    async def copy(**kw):
        copied.append(kw)

    monkeypatch.setattr(sr, "_deliver", deliver)
    monkeypatch.setattr(sr, "copy_telegram_message", copy)
    monkeypatch.setattr(sr, "current_bot_binding", lambda: type("B", (), {"bot_token": "t"})())

    request = {"requested_subdomain": "olga", "status": "awaiting_text"}
    asyncio.run(sr._notify_owner_step(_msg(file_id="F1"), request, done="фото"))

    assert copied and copied[0]["chat_id"] == "999" and copied[0]["from_chat_id"] == "111"
    assert sent == [(999, "Заявка на сайт — @partner: фото получено.\nАдрес: olga.wwc.best\nДальше: текст о себе.")]


def test_owner_is_not_alerted_about_own_request(monkeypatch):
    sent = []

    class S:
        platform_billing_owner_telegram_id = "111"

    monkeypatch.setattr(sr, "get_settings", lambda: S())

    async def deliver(chat_id, text, *, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(sr, "_deliver", deliver)
    asyncio.run(sr._notify_owner_step(_msg(text="olga"), {"status": "awaiting_photo"}, done="адрес сайта"))
    assert sent == []
