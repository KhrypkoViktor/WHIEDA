"""Допуск к трекеру привычек: ``PLATFORM_HABITS_ALLOW`` (11.11.2026), fail-closed."""

from __future__ import annotations

import pytest

from app.habits.routes import enabled_for
from app.settings import get_settings

OWNER, ANNA, STRANGER = 1, 9301, 9399


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.delenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _allow(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("PLATFORM_HABITS_ALLOW", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_HABITS_ALLOW", value)
    get_settings.cache_clear()


def test_empty_or_unset_means_owner_only(monkeypatch):
    for value in (None, "", "   ", "abc"):
        _allow(monkeypatch, value)
        assert enabled_for(OWNER) is True
        assert enabled_for(ANNA) is False


def test_listed_ids_only(monkeypatch):
    _allow(monkeypatch, f" {ANNA}, 9302 ,x")
    assert enabled_for(ANNA) is True and enabled_for(9302) is True
    assert enabled_for(STRANGER) is False
    assert enabled_for(OWNER) is True


def test_star_opens_for_every_signed_in_person(monkeypatch):
    _allow(monkeypatch, "*")
    assert enabled_for(STRANGER) is True
    assert get_settings().parsed_habits_allow() is None
