"""P0.4 admin sync registry metadata tests."""

from __future__ import annotations

import pytest

from app.admin.sync_registry_meta import (
    build_empty_state_meta,
    enrich_registry_block,
    operational_status,
    safe_source_url,
)


def test_safe_source_url_accepts_google_sheets_only():
    assert safe_source_url("https://docs.google.com/spreadsheets/d/abc123/edit") is not None
    assert safe_source_url("https://evil.example/x") is None
    assert safe_source_url("") is None


def test_operational_status_mapping():
    assert operational_status({"field_status": "gap"}) == "awaiting_first"
    assert operational_status({"field_status": "implemented", "status": "ok"}) == "working"
    assert operational_status({"field_status": "implemented", "status": "ok", "last_error": "x"}) == "needs_review"


def test_enrich_registry_block_never_includes_unsafe_source(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_SOURCE_URL_MARKETS", "https://evil.example/x")
    from app.settings import get_settings

    get_settings.cache_clear()
    block = enrich_registry_block("markets", {"field_status": "gap", "reason": "no_markets_sync_registry"})
    assert "source_url" not in block
    assert "reason" not in block
    assert block["owner_message"] == "Реестр ещё не подключён к рабочей синхронизации."
    get_settings.cache_clear()


def test_empty_state_meta_has_owner_facing_copy():
    meta = build_empty_state_meta("markets")
    assert "title" in meta
    assert "body" in meta
    assert "site_impact" in meta
    assert "gap" not in meta["title"].lower()
