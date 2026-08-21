"""Snapshot-based Telegram product card renderer tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql import formatters as fmt
from app.advisor.telegram_card import render_telegram_product_card
from app.telegram.delivery import deliver_structured_advisor_response

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "qa"
    / "telegram_experience"
    / "fixtures"
    / "master_snapshot_cards"
)

CARD_FIELDS = (
    "what_it_is",
    "who_asks_about_it",
    "common_use_cases",
    "how_to_use_short",
    "what_to_expect_soft",
    "contraindications_short",
)

FIELD_MARKERS = {
    "what_it_is": "🔥 Коротко:",
    "who_asks_about_it": "👥 Для кого:",
    "common_use_cases": "✅ Когда обычно рассматривают:",
    "how_to_use_short": "🧭 Как используют:",
    "what_to_expect_soft": "🧠 Почему интересен:",
    "contraindications_short": "⚠️ Ограничения:",
}


def _fixture_slugs() -> list[str]:
    manifest = FIXTURES_DIR / "manifest.json"
    if not manifest.is_file():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return list(data["products"].keys())


def pytest_generate_tests(metafunc):
    if "slug" in metafunc.fixturenames:
        slugs = _fixture_slugs()
        if not slugs:
            metafunc.parametrize("slug", [])
            return
        metafunc.parametrize("slug", slugs)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def _field_reflected(field: str, source: str, rendered: str, title: str) -> bool:
    haystack = _normalized(rendered)
    if field == "what_it_is":
        for sep in (" — ", " - ", ": "):
            prefix = f"{title}{sep}"
            if source.casefold().startswith(prefix.casefold()):
                source = source[len(prefix) :].strip()
                break
    if field in {"common_use_cases", "contraindications_short"}:
        parts = [part.strip() for part in re.split(r"[;\n]", source) if part.strip()]
        return any(_normalized(part)[:24] in haystack for part in parts)
    return _normalized(source)[:24] in haystack


def test_rendered_card_from_master_snapshot(slug: str):
    payload = json.loads((FIXTURES_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    card = payload["card"]
    product = payload["product"]
    rendered = render_telegram_product_card(card, product)
    title = (product.get("canonical_name") or card.get("canonical_name") or "").strip()

    assert "**" not in rendered
    assert "<" not in rendered and ">" not in rendered
    assert rendered.splitlines()[0].strip() == title

    for field in CARD_FIELDS:
        source = str(card.get(field) or "").strip()
        if not source:
            continue
        assert FIELD_MARKERS[field] in rendered, f"{slug}: missing section for {field}"
        assert _field_reflected(field, source, rendered, title), f"{slug}: field {field} not reflected"

    assert "Могу подсказать цену/PV" in rendered
    assert "если эти материалы есть в базе" in rendered
    assert rendered.count("🔥 Коротко:") <= 1


def test_format_product_card_wrapper_matches_renderer(slug: str):
    payload = json.loads((FIXTURES_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    assert fmt.format_product_card(payload["card"], payload["product"]) == render_telegram_product_card(
        payload["card"], payload["product"]
    )


def test_multiline_field_renders_separate_bullets():
    card = {
        "canonical_name": "Тестовый товар",
        "what_it_is": "Короткое описание.",
        "common_use_cases": "первая строка кейса\nвторая строка кейса\nтретья строка кейса",
    }
    product = {"canonical_name": "Тестовый товар", "sku": "TEST-1"}
    rendered = render_telegram_product_card(card, product)
    assert rendered.count("• ") >= 3
    assert "• первая строка кейса" in rendered
    assert "• вторая строка кейса" in rendered
    assert "• третья строка кейса" in rendered


def test_compact_mode_shortens_without_dropping_title():
    payload = json.loads((FIXTURES_DIR / "activator.json").read_text(encoding="utf-8"))
    full = render_telegram_product_card(payload["card"], payload["product"])
    compact = render_telegram_product_card(payload["card"], payload["product"], compact=True)
    assert len(compact) < len(full)
    assert compact.startswith("Активатор клеток")
    assert "🔥 Коротко:" in compact
    assert "👥 Для кого:" not in compact


@pytest.mark.asyncio
async def test_photo_first_delivery_uses_renderer_text_without_caption(monkeypatch):
    monkeypatch.setattr(
        "app.telegram.tenant_media.get_settings",
        lambda: type("S", (), {"platform_tenant_media_base_url": "https://media.test.example/media"})(),
    )
    payload = json.loads((FIXTURES_DIR / "activator.json").read_text(encoding="utf-8"))
    answer_text = fmt.format_product_card(payload["card"], payload["product"])

    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        await deliver_structured_advisor_response(
            1,
            {
                "answer_text": answer_text,
                "product": {"sku": payload["product"]["sku"]},
                "media": {"filename": "main.webp", "sku": payload["product"]["sku"]},
            },
            bot_token="tok",
            tenant_id="whieda",
        )
    photo.assert_awaited_once()
    assert "caption" not in photo.await_args.kwargs
    text.assert_awaited_once()
    assert text.await_args.kwargs["text"] == answer_text
    assert "**" not in text.await_args.kwargs["text"]
