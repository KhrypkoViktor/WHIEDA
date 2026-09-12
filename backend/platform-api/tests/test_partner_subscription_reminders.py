"""Partner reminder scheduling and copy contracts."""

from datetime import datetime, timezone
from pathlib import Path

from app.subscriptions.reminders import build_due_reminder_text

ROOT = Path(__file__).resolve().parents[1]


def _row(event_type: str) -> dict:
    return {
        "ref_code": "nnm",
        "display_name": "Виктор",
        "hostname": "nnm.wwc.best",
        "event_type": event_type,
        "paid_until": datetime(2026, 9, 21, 21, tzinfo=timezone.utc),
    }


def test_due_reminder_routes_payment_back_to_cabinet():
    text = build_due_reminder_text(_row("due_7d"))
    assert "осталось 7 дней" in text
    assert "/cabinet" in text
    assert "Продлить платформу" in text
    assert "SUNRAYSWORD" not in text


def test_grace_messages_are_unambiguous():
    start = build_due_reminder_text(_row("grace_start"))
    last = build_due_reminder_text(_row("grace_last"))
    assert "работает ещё 3 дня" in start
    assert "последний день" in last


def test_operational_reminder_scripts_are_copied_into_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/send_due_partner_reminders.py" in dockerfile
    assert "scripts/check_telegram_webhook_health.py" in dockerfile
