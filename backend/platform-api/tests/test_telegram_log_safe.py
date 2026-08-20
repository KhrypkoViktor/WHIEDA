from __future__ import annotations

import logging

from app.telegram.log_safe import (
    TelegramSecretFilter,
    chat_ref,
    install_telegram_log_filter,
    redact_telegram_secrets,
)


def test_chat_ref_hides_raw_id():
    ref = chat_ref("123456789")
    assert ref.startswith("chat:")
    assert "123456789" not in ref
    assert chat_ref(ref) == ref
    assert chat_ref("") == "chat:none"


def test_redact_telegram_bot_url():
    raw = "POST https://api.telegram.org/bot123456:AAHsecretToken/sendMessage failed"
    cleaned = redact_telegram_secrets(raw)
    assert "AAHsecretToken" not in cleaned
    assert "123456:" not in cleaned
    assert "bot[REDACTED]" in cleaned


def test_log_filter_rewrites_chat_id_extra():
    record = logging.LogRecord(
        name="app.telegram.delivery",
        level=logging.WARNING,
        pathname="delivery.py",
        lineno=1,
        msg="telegram_send_failed https://api.telegram.org/bot999:SECRET/sendMessage",
        args=(),
        exc_info=None,
    )
    record.chat_id = "555001"
    assert TelegramSecretFilter().filter(record) is True
    assert record.chat_id.startswith("chat:")
    assert "555001" not in str(record.chat_id)
    assert "SECRET" not in record.msg
    assert "bot[REDACTED]" in record.msg


def test_install_filter_is_idempotent():
    root = logging.getLogger()
    install_telegram_log_filter()
    first = sum(isinstance(item, TelegramSecretFilter) for item in root.filters)
    install_telegram_log_filter()
    second = sum(isinstance(item, TelegramSecretFilter) for item in root.filters)
    assert first >= 1
    assert second == first
