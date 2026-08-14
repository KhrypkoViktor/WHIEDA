"""Runtime contract for the reviewed product discovery map."""

from __future__ import annotations

import asyncio

from app.advisor.sql import product_discovery


class FakeRepo:
    async def resolve_product_by_sku(self, _conn, _tenant_id, sku):
        return {"sku": sku, "canonical_name": {"F001-02": "Эликсир Фохоу", "F003-02": "Эликсир Саньцин", "F002-02": "Эликсир 3 Драгоценности"}.get(sku, sku)}


class FakeFormatter:
    @staticmethod
    def empty_media():
        return {"photos": [], "videos": [], "pdfs": []}

    @staticmethod
    def ok_response(text, mode, trace_id, **kwargs):
        return {"ok": True, "answer_text": text, "answer_mode": mode, "trace_id": trace_id, **kwargs}


def test_generic_elixir_returns_three_ranked_choices():
    response = asyncio.run(
        product_discovery.build_discovery_choice_response(
            object(), "whieda", "эликсир", repo=FakeRepo(), trace_id="trace", fmt=FakeFormatter()
        )
    )
    assert response is not None
    assert response["answer_mode"] == "clarification"
    assert response["product"]["skus"] == ["F001-02", "F003-02", "F002-02"]
    assert "Эликсир Фохоу" in response["answer_text"]


def test_exact_ready_alias_does_not_bypass_normal_resolver():
    assert product_discovery.should_show_choices("цинфэн", product_discovery.lookup_discovery_candidates("цинфэн")) is False


def test_single_word_catalog_category_shows_choices_before_opening_a_card():
    assert product_discovery.should_show_choices("чай", product_discovery.lookup_discovery_candidates("чай")) is True


def test_typo_enters_the_same_discovery_path_as_its_clean_phrase():
    assert product_discovery.normalize_discovery_phrase("пасту") == "паста"
    assert product_discovery.normalize_discovery_phrase("поис") == "пояс"


def test_weak_color_never_builds_a_default_product_choice():
    response = asyncio.run(
        product_discovery.build_discovery_choice_response(
            object(), "whieda", "красный", repo=FakeRepo(), trace_id="trace", fmt=FakeFormatter()
        )
    )
    assert response is None
