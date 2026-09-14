from datetime import datetime, timezone

import pytest

from app.subscriptions.reminder_text import build_partner_payment_reminder
from app.subscriptions.service import resolve_billing_partner_hostname


def test_initial_belarus_reminder_has_exact_boundaries_and_one_payment_method():
    text = build_partner_payment_reminder(
        recipient_name="Виктор",
        hostname="dev.wwc.best",
        country="РБ",
        paid_until=datetime(2026, 9, 21, 21, tzinfo=timezone.utc),
    )
    assert "до 21 сентября включительно" in text
    assert "22-24 сентября сайт продолжит работать" in text
    assert "с 25 сентября адрес будет временно вести" in text
    assert "Беларусь: 30 WWC$ : SUNRAYSWORD" in text
    assert "Россия:" not in text
    assert "https://t.me/sunraysword" in text


def test_russia_reminder_has_only_russia_payment_method():
    text = build_partner_payment_reminder(
        recipient_name="Елена",
        hostname="elena.wwc.best",
        country="РФ",
        paid_until=datetime(2026, 9, 21, 21, tzinfo=timezone.utc),
    )
    assert "Россия: 3 000 RUB по номеру +79282372677 Т-Банк" in text
    assert "Беларусь:" not in text


def test_non_midnight_boundary_is_not_described_as_full_day():
    text = build_partner_payment_reminder(
        recipient_name="Елена",
        hostname="elena.wwc.best",
        country="RU",
        paid_until=datetime(2026, 12, 9, 9, tzinfo=timezone.utc),
    )
    assert "до 9 декабря, 12:00 МСК" in text
    assert "с 12 декабря, 12:00 МСК" in text
    assert "включительно" not in text


def test_country_is_required_and_unambiguous():
    with pytest.raises(ValueError, match="country"):
        build_partner_payment_reminder(
            recipient_name="Виктор",
            hostname="dev.wwc.best",
            country="",
            paid_until=datetime(2026, 9, 21, 21, tzinfo=timezone.utc),
        )


def test_dev_hostname_is_allowed_only_for_billing():
    assert (
        resolve_billing_partner_hostname("dev", {"subdomain": "dev"})
        == "dev.wwc.best"
    )
