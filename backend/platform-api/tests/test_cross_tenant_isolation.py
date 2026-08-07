from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_whieda_ref_not_visible_on_other_tenant_domain(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        if tenant_id == "whieda" and ref_code == "ladnaya":
            return {
                "ref_code": "ladnaya",
                "display_mode": "named",
                "enabled": True,
                "profile_version": 1,
                "public_profile": {"display_name": "Partner"},
            }
        return None

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)

    ok = await client.get("/v1/public/ref/ladnaya", headers={"host": "wwc.best"})
    assert ok.status_code == 200
    assert ok.json()["ref_code"] == "ladnaya"

    missing = await client.get("/v1/public/ref/ladnaya", headers={"host": "acme.test.local"})
    assert missing.status_code == 404
