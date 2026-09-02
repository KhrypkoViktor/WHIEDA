from __future__ import annotations

from typing import Any

from app.admin.repositories import leads as leads_repo
from app.admin.repositories import referrals as referrals_repo
from app.admin.services.markets import build_registry_readiness_payload, build_sync_status_payload


async def build_overview_payload(tenant_id: str) -> dict[str, Any]:
    leads_7d = await leads_repo.count_leads(tenant_id, days=7)
    leads_30d = await leads_repo.count_leads(tenant_id, days=30)
    leads_new = await leads_repo.count_leads(tenant_id, status="new")
    leads_in_progress = await leads_repo.count_leads(tenant_id, status="contacted") + await leads_repo.count_leads(
        tenant_id, status="qualified"
    )
    refs = await referrals_repo.count_enabled_referrals(tenant_id)
    sync = await build_sync_status_payload(tenant_id)
    registry_readiness = await build_registry_readiness_payload(tenant_id, referrals=refs)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "leads": {
            "last_7_days": leads_7d,
            "last_30_days": leads_30d,
            "new": leads_new,
            "in_progress": leads_in_progress,
        },
        "referrals": refs,
        "sync_summary": {
            "markets": sync.get("markets_sync"),
            "partners": sync.get("partners_sync"),
            "structured": sync.get("structured_sync"),
            "service_centers": sync.get("service_centers_sync"),
        },
        "registry_readiness": registry_readiness,
        "health": {"api": "ready"},
    }
