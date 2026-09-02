"""Core cart session API — contract tests against fixtures, no live DB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "cart" / "fixtures" / "cart_api_v1.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CATALOG = {row["sku"]: dict(row) for row in FIXTURE["catalog"]}
HOST = {"host": "wwc.best"}


@pytest.fixture
def cart_runtime(monkeypatch):
    from app.cart.store import MemoryCartStore
    from app.cart import service

    store = MemoryCartStore()

    async def fake_products(tenant_id: str, skus: list[str]) -> dict[str, dict]:
        assert tenant_id == "whieda"
        return {sku: dict(CATALOG[sku]) for sku in skus if sku in CATALOG}

    monkeypatch.setattr(service, "get_store", lambda: store)
    monkeypatch.setattr(service, "fetch_products_for_skus", fake_products)
    return store


async def _create(client, extra=None):
    body = dict(FIXTURE["create_request"])
    if extra:
        body.update(extra)
    return await client.post("/v1/cart-sessions", json=body, headers=HOST)


@pytest.mark.asyncio
async def test_create_cart_session_is_opaque(client, cart_runtime):
    response = await _create(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    cart_id = payload["cart_session_id"]
    assert cart_id
    assert "M015" not in cart_id
    assert "ladnaya" not in cart_id
    assert payload["market_id"] == "by"
    assert payload["currency_code"] == "BYN"
    assert payload["price_mode"] == "primary"
    assert payload["first_ref"] == "ladnaya"
    assert payload["items"] == []
    assert payload["totals"]["amount"] == 0
    assert payload["totals"]["unavailable_count"] == 0


@pytest.mark.asyncio
async def test_site_alias_creates_same_shape(client, cart_runtime):
    response = await client.post(
        "/api/v1/cart-sessions",
        json=FIXTURE["create_request"],
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["cart_session_id"]


@pytest.mark.asyncio
async def test_qty_1_2_3_same_sku_one_line(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    last = None
    for qty in (1, 2, 3):
        last = await client.post(
            f"/v1/cart-sessions/{cart_id}/items",
            json={"sku": "M015-00", "qty": qty, "idempotency_key": f"qty-{qty}"},
            headers=HOST,
        )
        assert last.status_code == 200
        items = last.json()["items"]
        assert len(items) == 1
        assert items[0]["sku"] == "M015-00"
        assert items[0]["qty"] == qty
    assert last.json()["totals"]["amount"] == 1750 * 3
    assert last.json()["totals"]["pv"] == 300 * 3
    assert last.json()["items"][0]["unit_amount"] == 1750
    assert last.json()["items"][0]["line_amount"] == 5250


@pytest.mark.asyncio
async def test_idempotent_item_change_does_not_duplicate(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    body = {"sku": "M015-00", "qty": 2, "idempotency_key": "same-key"}
    first = await client.post(f"/v1/cart-sessions/{cart_id}/items", json=body, headers=HOST)
    second = await client.post(f"/v1/cart-sessions/{cart_id}/items", json=body, headers=HOST)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["items"] == second.json()["items"]
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["qty"] == 2


@pytest.mark.asyncio
async def test_missing_price_is_null_not_zero(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "a"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "T003", "qty": 1, "idempotency_key": "b"},
        headers=HOST,
    )
    payload = response.json()
    belt = next(item for item in payload["items"] if item["sku"] == "T003")
    assert belt["unit_amount"] is None
    assert belt["line_amount"] is None
    assert belt["price_state"] == "unavailable"
    assert 0 not in {belt["unit_amount"], belt["line_amount"]}
    assert payload["totals"]["amount"] == 1750
    assert payload["totals"]["unavailable_count"] == 1


@pytest.mark.asyncio
async def test_repeat_mode_uses_partner_price(client, cart_runtime):
    created = await _create(client, extra={"price_mode": "repeat"})
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "r"},
        headers=HOST,
    )
    payload = response.json()
    assert payload["price_mode"] == "repeat"
    assert payload["items"][0]["unit_amount"] == 1050
    assert payload["totals"]["amount"] == 1050


@pytest.mark.asyncio
async def test_first_ref_does_not_change(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.patch(
        f"/v1/cart-sessions/{cart_id}",
        json={"ref": "other-partner", "first_ref": "hijack"},
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["first_ref"] == "ladnaya"
    assert response.json()["ref"] == "other-partner"


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_read_cart(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.get(
        f"/v1/cart-sessions/{cart_id}",
        headers={"host": "acme.test.local"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_snapshot_token_has_no_skus_in_url(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "s"},
        headers=HOST,
    )
    snap = await client.post(f"/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=HOST)
    assert snap.status_code == 200
    token = snap.json()["snapshot_token"]
    path = snap.json()["share_path"]
    assert token
    assert "M015" not in token
    assert "M015" not in path
    assert path.startswith("/price/?snap=")
    loaded = await client.get(f"/v1/cart-snapshots/{token}", headers=HOST)
    assert loaded.status_code == 200
    assert loaded.json()["items"][0]["sku"] == "M015-00"
    assert loaded.json()["totals"]["amount"] == 1750


@pytest.mark.asyncio
async def test_checkout_reuses_lead_pipeline(client, cart_runtime, monkeypatch):
    saved = {}

    async def fake_save(lead):
        saved["lead"] = lead
        return {"public_id": "L-CART-1", "created": True, "lead_id": "1"}

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 2, "idempotency_key": "c"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={
            "name": "Иван",
            "contact": "+375290000000",
            "idempotency_key": "checkout-1",
            "page_url": "https://wwc.best/price/?calc=1",
        },
        headers=HOST,
    )
    assert response.status_code == 201
    lead = saved["lead"]
    assert lead.product_sku == "cart-order"
    assert lead.product_name == "cart-order"
    assert lead.first_ref_code == "ladnaya"
    assert "3500" in lead.comment
    assert "Активатор" in lead.comment
    assert getattr(lead, "owner_id", None) is None
    assert "owner_id" not in (lead.metadata or {})


@pytest.mark.asyncio
async def test_checkout_rejects_unavailable_price(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "T003", "qty": 1, "idempotency_key": "bad"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={
            "name": "Иван",
            "contact": "+375290000000",
            "idempotency_key": "checkout-empty-price",
        },
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unavailable_price"


@pytest.mark.asyncio
async def test_client_cannot_send_owner_or_price(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions",
        json={**FIXTURE["create_request"], "owner_id": "evil", "amount": 1},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "forbidden_field"


@pytest.mark.asyncio
async def test_unknown_cart_session_is_404(client, cart_runtime):
    response = await client.get("/v1/cart-sessions/does-not-exist", headers=HOST)
    assert response.status_code == 404
    assert response.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_unknown_snapshot_is_404(client, cart_runtime):
    response = await client.get("/v1/cart-snapshots/does-not-exist", headers=HOST)
    assert response.status_code == 404
    assert response.json()["error"] == "snapshot_not_found"


@pytest.mark.asyncio
async def test_qty_zero_removes_line(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 2, "idempotency_key": "add"},
        headers=HOST,
    )
    removed = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 0, "idempotency_key": "remove"},
        headers=HOST,
    )
    assert removed.status_code == 200
    assert removed.json()["items"] == []
    assert removed.json()["totals"]["amount"] == 0


@pytest.mark.asyncio
async def test_unknown_sku_is_rejected(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "NO-SUCH-SKU", "qty": 1, "idempotency_key": "x"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_sku"


@pytest.mark.asyncio
async def test_invalid_qty_is_rejected(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    too_big = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 100, "idempotency_key": "big"},
        headers=HOST,
    )
    assert too_big.status_code == 400
    assert too_big.json()["error"] == "invalid_qty"


@pytest.mark.asyncio
async def test_clear_empties_cart(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 2, "idempotency_key": "c1"},
        headers=HOST,
    )
    cleared = await client.post(f"/v1/cart-sessions/{cart_id}/clear", json={}, headers=HOST)
    assert cleared.status_code == 200
    assert cleared.json()["items"] == []
    assert cleared.json()["totals"]["amount"] == 0


@pytest.mark.asyncio
async def test_empty_checkout_is_rejected(client, cart_runtime, monkeypatch):
    async def fake_save(lead):
        raise AssertionError("empty cart must not create a lead")

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"name": "Иван", "contact": "+375290000000", "idempotency_key": "empty"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "empty_cart"


@pytest.mark.asyncio
async def test_telegram_user_id_forbidden_on_create(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions",
        json={**FIXTURE["create_request"], "telegram_user_id": "1001"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "forbidden_field"


@pytest.mark.asyncio
async def test_get_returns_created_session(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.get(f"/v1/cart-sessions/{cart_id}", headers=HOST)
    assert response.status_code == 200
    assert response.json()["cart_session_id"] == cart_id
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_sku_with_spaces_is_invalid(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015 00", "qty": 1, "idempotency_key": "spaces"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_sku"


@pytest.mark.asyncio
async def test_negative_qty_is_rejected(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": -1, "idempotency_key": "neg"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_qty"


@pytest.mark.asyncio
async def test_invalid_price_mode_is_rejected(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions",
        json={**FIXTURE["create_request"], "price_mode": "wholesale"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_price_mode"


@pytest.mark.asyncio
async def test_ru_market_uses_rub(client, cart_runtime):
    created = await _create(client, extra={"market_id": "ru"})
    assert created.status_code == 200
    assert created.json()["market_id"] == "ru"
    assert created.json()["currency_code"] == "RUB"
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "ru"},
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["unit_amount"] == 50000
    assert response.json()["totals"]["currency_code"] == "RUB"


@pytest.mark.asyncio
async def test_item_on_missing_cart_is_404(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions/missing-cart/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "gone"},
        headers=HOST,
    )
    assert response.status_code == 404
    assert response.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_snapshot_on_missing_cart_is_404(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions/missing-cart/snapshot",
        json={},
        headers=HOST,
    )
    assert response.status_code == 404
    assert response.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_patch_market_reprices_in_new_currency(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "by"},
        headers=HOST,
    )
    patched = await client.patch(
        f"/v1/cart-sessions/{cart_id}",
        json={"market_id": "ru"},
        headers=HOST,
    )
    assert patched.status_code == 200
    assert patched.json()["market_id"] == "ru"
    assert patched.json()["currency_code"] == "RUB"
    assert patched.json()["first_ref"] == "ladnaya"
    assert patched.json()["items"][0]["unit_amount"] == 50000


@pytest.mark.asyncio
async def test_unknown_market_falls_back_to_global_rub(client, cart_runtime):
    created = await _create(client, extra={"market_id": "kz"})
    assert created.status_code == 200
    assert created.json()["market_id"] == "global"
    assert created.json()["currency_code"] == "RUB"


@pytest.mark.asyncio
async def test_patch_and_checkout_on_missing_cart_are_404(client, cart_runtime):
    patched = await client.patch(
        "/v1/cart-sessions/missing-cart",
        json={"market_id": "ru"},
        headers=HOST,
    )
    assert patched.status_code == 404
    assert patched.json()["error"] == "cart_not_found"
    checkout = await client.post(
        "/v1/cart-sessions/missing-cart/checkout",
        json={"name": "Тест", "contact": "+375000000000"},
        headers=HOST,
    )
    assert checkout.status_code == 404
    assert checkout.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_qty_over_max_is_rejected(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 100, "idempotency_key": "too-many"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_qty"


@pytest.mark.asyncio
async def test_clear_missing_cart_is_404(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions/missing-cart/clear",
        json={},
        headers=HOST,
    )
    assert response.status_code == 404
    assert response.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_two_skus_sum_on_server(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "a"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "EU-N000021-24", "qty": 1, "idempotency_key": "b"},
        headers=HOST,
    )
    assert response.status_code == 200
    payload = response.json()
    assert {row["sku"] for row in payload["items"]} == {"M015-00", "EU-N000021-24"}
    assert payload["totals"]["amount"] == 1995
    assert payload["totals"]["pv"] == 350


@pytest.mark.asyncio
async def test_snapshot_stays_frozen_after_cart_changes(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "snap-1"},
        headers=HOST,
    )
    snap = await client.post(f"/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=HOST)
    token = snap.json()["snapshot_token"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 2, "idempotency_key": "snap-2"},
        headers=HOST,
    )
    frozen = await client.get(f"/v1/cart-snapshots/{token}", headers=HOST)
    live = await client.get(f"/v1/cart-sessions/{cart_id}", headers=HOST)
    assert frozen.status_code == 200
    assert frozen.json()["items"][0]["qty"] == 1
    assert frozen.json()["totals"]["amount"] == 1750
    assert live.json()["items"][0]["qty"] == 2
    assert live.json()["totals"]["amount"] == 3500


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_read_snapshot(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    snap = await client.post(f"/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=HOST)
    token = snap.json()["snapshot_token"]
    response = await client.get(
        f"/v1/cart-snapshots/{token}",
        headers={"host": "acme.test.local"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "snapshot_not_found"


@pytest.mark.asyncio
async def test_patch_price_mode_reprices(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "mode"},
        headers=HOST,
    )
    patched = await client.patch(
        f"/v1/cart-sessions/{cart_id}",
        json={"price_mode": "repeat"},
        headers=HOST,
    )
    assert patched.status_code == 200
    assert patched.json()["price_mode"] == "repeat"
    assert patched.json()["items"][0]["unit_amount"] == 1050
    assert patched.json()["first_ref"] == "ladnaya"


@pytest.mark.asyncio
async def test_qty_99_is_allowed(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 99, "idempotency_key": "max-ok"},
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["qty"] == 99
    assert response.json()["totals"]["amount"] == 1750 * 99


@pytest.mark.asyncio
async def test_checkout_without_name_does_not_save_lead(client, cart_runtime, monkeypatch):
    async def fake_save(lead):
        raise AssertionError("incomplete checkout must not create a lead")

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "need-name"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"contact": "+375290000000", "idempotency_key": "no-name"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert "name" in (response.json().get("errors") or [])


PUBLIC_CART_KEYS = {
    "ok",
    "cart_session_id",
    "market_id",
    "currency_code",
    "price_mode",
    "first_ref",
    "ref",
    "items",
    "totals",
}


@pytest.mark.asyncio
async def test_public_cart_does_not_leak_owner_or_visitor(client, cart_runtime):
    created = await _create(client, extra={"visitor_session_id": "vis-secret"})
    assert created.status_code == 200
    payload = created.json()
    assert set(payload) <= PUBLIC_CART_KEYS
    assert "owner_id" not in payload
    assert "visitor_session_id" not in payload
    assert "telegram_user_id" not in str(payload)


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_write_items(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "x"},
        headers={"host": "acme.test.local"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "cart_not_found"


@pytest.mark.asyncio
async def test_item_rejects_client_price(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={
            "sku": "M015-00",
            "qty": 1,
            "idempotency_key": "priced",
            "unit_amount": 1,
            "line_amount": 1,
        },
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "forbidden_field"


@pytest.mark.asyncio
async def test_empty_and_long_sku_are_invalid(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    empty = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "", "qty": 1, "idempotency_key": "empty"},
        headers=HOST,
    )
    long_sku = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "A" + ("0" * 32), "qty": 1, "idempotency_key": "long"},
        headers=HOST,
    )
    assert empty.status_code == 400
    assert empty.json()["error"] == "invalid_sku"
    assert long_sku.status_code == 400
    assert long_sku.json()["error"] == "invalid_sku"


@pytest.mark.asyncio
async def test_qty_as_string_is_accepted(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": "2", "idempotency_key": "str-qty"},
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["qty"] == 2
    assert response.json()["totals"]["amount"] == 3500


@pytest.mark.asyncio
async def test_clear_keeps_first_ref(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "c"},
        headers=HOST,
    )
    cleared = await client.post(f"/v1/cart-sessions/{cart_id}/clear", json={}, headers=HOST)
    assert cleared.status_code == 200
    assert cleared.json()["items"] == []
    assert cleared.json()["first_ref"] == "ladnaya"


@pytest.mark.asyncio
async def test_checkout_without_contact_does_not_save_lead(client, cart_runtime, monkeypatch):
    async def fake_save(lead):
        raise AssertionError("incomplete checkout must not create a lead")

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "need-contact"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"name": "Иван", "idempotency_key": "no-contact"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert "contact" in (response.json().get("errors") or [])


@pytest.mark.asyncio
async def test_priced_plus_unavailable_checkout_does_not_save_lead(client, cart_runtime, monkeypatch):
    async def fake_save(lead):
        raise AssertionError("unavailable price must not create a lead")

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "ok"},
        headers=HOST,
    )
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "T003", "qty": 1, "idempotency_key": "no-price"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"name": "Иван", "contact": "+375290000000", "idempotency_key": "mix"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unavailable_price"


@pytest.mark.asyncio
async def test_post_snapshot_collection_is_not_allowed(client, cart_runtime):
    response = await client.post("/v1/cart-snapshots/tok", json={}, headers=HOST)
    assert response.status_code in {404, 405}


@pytest.mark.asyncio
async def test_create_without_market_uses_global_rub(client, cart_runtime):
    response = await client.post(
        "/v1/cart-sessions",
        json={"price_mode": "primary"},
        headers=HOST,
    )
    assert response.status_code == 200
    assert response.json()["market_id"] == "global"
    assert response.json()["currency_code"] == "RUB"


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_clear_or_checkout(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    other = {"host": "acme.test.local"}
    cleared = await client.post(f"/v1/cart-sessions/{cart_id}/clear", json={}, headers=other)
    checkout = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"name": "X", "contact": "+375290000000", "idempotency_key": "x"},
        headers=other,
    )
    snap = await client.post(f"/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=other)
    assert cleared.status_code == 404
    assert checkout.status_code == 404
    assert snap.status_code == 404


@pytest.mark.asyncio
async def test_patch_rejects_client_amount(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    response = await client.patch(
        f"/v1/cart-sessions/{cart_id}",
        json={"amount": 1},
        headers=HOST,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "forbidden_field"


@pytest.mark.asyncio
async def test_site_alias_get_and_snapshot(client, cart_runtime):
    created = await client.post(
        "/api/v1/cart-sessions",
        json=FIXTURE["create_request"],
        headers=HOST,
    )
    cart_id = created.json()["cart_session_id"]
    got = await client.get(f"/api/v1/cart-sessions/{cart_id}", headers=HOST)
    snap = await client.post(f"/api/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=HOST)
    token = snap.json()["snapshot_token"]
    loaded = await client.get(f"/api/v1/cart-snapshots/{token}", headers=HOST)
    assert got.status_code == 200
    assert got.json()["cart_session_id"] == cart_id
    assert loaded.status_code == 200
    assert "M015" not in token


@pytest.mark.asyncio
async def test_snapshot_share_path_is_opaque(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "share"},
        headers=HOST,
    )
    snap = await client.post(f"/v1/cart-sessions/{cart_id}/snapshot", json={}, headers=HOST)
    path = snap.json()["share_path"]
    assert path.startswith("/price/?snap=")
    assert "M015" not in path
    assert "sku" not in path.lower()


@pytest.mark.asyncio
async def test_get_after_clear_keeps_same_session(client, cart_runtime):
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "clr"},
        headers=HOST,
    )
    cleared = await client.post(f"/v1/cart-sessions/{cart_id}/clear", json={}, headers=HOST)
    got = await client.get(f"/v1/cart-sessions/{cart_id}", headers=HOST)
    assert cleared.status_code == 200
    assert got.status_code == 200
    assert got.json()["cart_session_id"] == cart_id
    assert got.json()["items"] == []
    assert got.json()["first_ref"] == "ladnaya"


@pytest.mark.asyncio
async def test_checkout_without_idempotency_does_not_save_lead(
    client, cart_runtime, monkeypatch
):
    saved = {"count": 0}

    async def fake_save(lead):
        saved["count"] += 1
        return {"public_id": "L-NO", "created": True, "lead_id": "0"}

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await _create(client)
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "i"},
        headers=HOST,
    )
    response = await client.post(
        f"/v1/cart-sessions/{cart_id}/checkout",
        json={"name": "Иван", "contact": "+375290000000"},
        headers=HOST,
    )
    assert response.status_code == 400
    assert saved["count"] == 0


@pytest.mark.asyncio
async def test_site_alias_checkout_uses_cart_order_sku(
    client, cart_runtime, monkeypatch
):
    saved = {}

    async def fake_save(lead):
        saved["lead"] = lead
        return {"public_id": "L-ALIAS", "created": True, "lead_id": "2"}

    monkeypatch.setattr("app.cart.service.save_lead", fake_save)
    created = await client.post(
        "/api/v1/cart-sessions",
        json=FIXTURE["create_request"],
        headers=HOST,
    )
    cart_id = created.json()["cart_session_id"]
    await client.post(
        f"/api/v1/cart-sessions/{cart_id}/items",
        json={"sku": "M015-00", "qty": 1, "idempotency_key": "alias-c"},
        headers=HOST,
    )
    response = await client.post(
        f"/api/v1/cart-sessions/{cart_id}/checkout",
        json={
            "name": "Иван",
            "contact": "+375290000000",
            "idempotency_key": "alias-checkout",
        },
        headers=HOST,
    )
    assert response.status_code == 201
    assert saved["lead"].product_sku == "cart-order"
    assert "owner_id" not in (saved["lead"].metadata or {})