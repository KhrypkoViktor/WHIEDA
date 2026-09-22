"""Max (max.ru) — второй канал бота: разбор обновлений, секрет webhook, тексты."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.max.update_parser import parse_max_update


def test_bot_started_carries_deep_link_payload():
    event = parse_max_update({
        "update_type": "bot_started", "timestamp": 1, "chat_id": 777,
        "user": {"user_id": 42, "name": "Инна", "first_name": "Инна", "username": "inna_m"},
        "payload": "ref_ABCdef123456789X",
    })
    assert event is not None
    assert (event.kind, event.user_id, event.chat_id, event.text) == ("start", 42, 777, "ref_ABCdef123456789X")
    assert event.username == "inna_m" and event.display_name == "@inna_m"


def test_message_with_typed_start_is_a_start_and_plain_text_is_a_question():
    base = {"update_type": "message_created", "timestamp": 1, "message": {
        "sender": {"user_id": 42, "name": "Инна Петрова", "first_name": "Инна", "last_name": "Петрова"},
        "recipient": {"chat_id": 777, "chat_type": "dialog"},
        "body": {"mid": "m1", "seq": 1, "text": "/start site_ABCdef123456789X"},
    }}
    start = parse_max_update(base)
    assert start is not None and start.kind == "start" and start.text == "site_ABCdef123456789X"
    assert start.display_name == "Инна Петрова"
    base["message"]["body"]["text"] = "стельки с анионами — размеры?"
    question = parse_max_update(base)
    assert question is not None and question.kind == "message" and question.text.startswith("стельки")


def test_groups_bots_and_unknown_updates_are_ignored():
    assert parse_max_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 1, "is_bot": True}, "recipient": {"chat_id": 5, "chat_type": "dialog"}, "body": {"text": "x"}}}) is None
    assert parse_max_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 1}, "recipient": {"chat_id": 5, "chat_type": "chat"}, "body": {"text": "x"}}}) is None
    assert parse_max_update({"update_type": "message_removed"}) is None


def test_webhook_secret_is_required_and_compared(monkeypatch):
    from app.max import routes
    from app.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_webhook_secret", None)
    with pytest.raises(HTTPException) as unset:
        routes.verify_max_secret("anything")
    assert unset.value.status_code == 503
    monkeypatch.setattr(settings, "max_webhook_secret", "s3cret")
    with pytest.raises(HTTPException) as wrong:
        routes.verify_max_secret("nope")
    assert wrong.value.status_code == 403
    routes.verify_max_secret("s3cret")


def test_greeting_names_the_inviter_and_site():
    from app.max.processor import _greeting

    class Card:
        display_name = "Олеся Вселенная"
        site_url = "https://olesya.wwc.best/"

    text = _greeting("attributed", Card())
    assert "Приглашение сохранено." in text
    assert "Олеся Вселенная" in text and "https://olesya.wwc.best/" in text
    assert "Свою ссылку" in _greeting("self_referral", None)


def test_max_webhook_bypasses_tenant_host_resolution():
    """Webhook Max приходит с хоста Max, не с домена тенанта — как и Telegram."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post("/v1/max/webhook", json={}, headers={"host": "127.0.0.1"})
    assert response.status_code in {403, 503}  # не 404 от TenantMiddleware
