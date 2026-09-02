"""Advisor gap operator control plane tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.admin.gap_review.constants import DEFAULT_TRIAGE, GAP_KINDS
from app.admin.gap_review.dedup import build_dedup_key
from app.admin.gap_review.export import export_csv, export_markdown
from app.admin.gap_review.guard import assert_local_database_url
from app.admin.gap_review.refresh import refresh_advisor_gap_review_queue
from app.admin.gap_review.service import _validate_patch, patch_item


NOW = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)

SAMPLE_AGG = [
    {
        "gap_kind": "medical_or_safety_boundary",
        "question_normalized": "как лечить диабет",
        "detected_product": None,
        "event_count": 2,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "latest_trace_id": "trace-med-001",
    },
    {
        "gap_kind": "unknown_product",
        "question_normalized": "xyzunknown123",
        "detected_product": None,
        "event_count": 1,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "latest_trace_id": "trace-unk-001",
    },
]


def _fake_tenant_connection(conn):
    @asynccontextmanager
    async def _cm(_tenant_id: str):
        yield conn

    return _cm


@pytest.mark.parametrize("gap_kind", sorted(GAP_KINDS))
def test_default_triage_covers_all_gap_kinds(gap_kind: str):
    defaults = DEFAULT_TRIAGE[gap_kind]
    assert defaults["priority"] in {"p0", "p1", "p2", "p3"}
    assert defaults["owner_role"]
    assert defaults["candidate_type"]


def test_medical_default_is_p0_not_approved():
    defaults = DEFAULT_TRIAGE["medical_or_safety_boundary"]
    assert defaults["priority"] == "p0"
    assert defaults["candidate_type"] == "medical_review"
    assert defaults["candidate_type"] != "approved_candidate"


def test_dedup_key_stable():
    a = build_dedup_key(
        gap_kind="unknown_followup",
        question_normalized="цена",
        detected_product=None,
    )
    b = build_dedup_key(
        gap_kind="unknown_followup",
        question_normalized="цена",
        detected_product=None,
    )
    assert a == b


def test_local_db_guard_refuses_prod_like_url():
    with pytest.raises(RuntimeError):
        assert_local_database_url("postgresql://user:pass@185.252.232.93:5432/prod")


def test_local_db_guard_allows_local_core():
    assert_local_database_url(
        "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core"
    )


def test_export_markdown_has_no_session_ref():
    rows = [
        {
            "gap_kind": "unknown_followup",
            "question_normalized": "цена",
            "detected_product": None,
            "event_count": 3,
            "last_seen_at": NOW,
            "owner_role": "owner",
            "owner_name": None,
            "operator_note": None,
            "candidate_type": "intent_gap",
            "priority": "p2",
            "status": "new",
        }
    ]
    md = export_markdown(rows, tenant_id="whieda")
    assert "session_ref" not in md
    assert "Приоритет" in md
    assert "цена" in md


def test_export_csv_has_no_technical_ids():
    rows = [
        {
            "gap_kind": "missing_resource",
            "question_normalized": "видео товар",
            "detected_product": "Товар",
            "event_count": 1,
            "last_seen_at": NOW,
            "owner_role": "admin",
            "owner_name": None,
            "operator_note": None,
            "candidate_type": "resource_link",
            "priority": "p1",
            "status": "new",
        }
    ]
    csv_text = export_csv(rows)
    assert "session_ref" not in csv_text
    assert "trace:" not in csv_text


def test_validate_patch_rejects_unknown_fields():
    with pytest.raises(HTTPException) as exc:
        _validate_patch({"status": "triaged", "evil": True})
    assert exc.value.status_code == 400


def test_validate_patch_note_limit():
    with pytest.raises(HTTPException):
        _validate_patch({"operator_note": "x" * 2001})


@pytest.mark.asyncio
async def test_refresh_inserts_then_updates_without_duplicates():
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

    stored: dict[str, dict] = {}

    async def fake_aggregate(_conn, _tenant):
        return list(SAMPLE_AGG)

    async def fake_fetch(_conn, tenant_id, dedup_key):
        return stored.get(dedup_key)

    async def fake_insert(_conn, **kwargs):
        item_id = str(uuid4())
        row = {"id": item_id, **kwargs, "status": "new"}
        stored[kwargs["dedup_key"]] = row
        return row

    async def fake_refresh(_conn, **kwargs):
        item_id = kwargs["item_id"]
        for row in stored.values():
            if str(row["id"]) == item_id:
                row["event_count"] = kwargs["event_count"]
                row["last_seen_at"] = kwargs["last_seen_at"]
                return row
        raise AssertionError("missing item")

    with patch("app.admin.gap_review.refresh.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.admin.gap_review.refresh.repo.aggregate_gap_events", fake_aggregate):
            with patch("app.admin.gap_review.refresh.repo.fetch_item_by_dedup", fake_fetch):
                with patch("app.admin.gap_review.refresh.repo.insert_review_item", fake_insert):
                    with patch("app.admin.gap_review.refresh.repo.refresh_existing_counts", fake_refresh):
                        first = await refresh_advisor_gap_review_queue("whieda", dry_run=False)
                        second = await refresh_advisor_gap_review_queue("whieda", dry_run=False)

    assert first.inserted == 2
    assert first.updated == 0
    assert second.inserted == 0
    assert second.unchanged == 2
    assert len(stored) == 2


@pytest.mark.asyncio
async def test_refresh_dry_run_writes_nothing():
    conn = AsyncMock()

    async def fake_aggregate(_conn, _tenant):
        return list(SAMPLE_AGG)

    with patch("app.admin.gap_review.refresh.tenant_connection", _fake_tenant_connection(conn)):
        with patch("app.admin.gap_review.refresh.repo.aggregate_gap_events", fake_aggregate):
            with patch("app.admin.gap_review.refresh.repo.fetch_item_by_dedup", AsyncMock(return_value=None)):
                with patch("app.admin.gap_review.refresh.repo.insert_review_item", AsyncMock()) as insert_mock:
                    result = await refresh_advisor_gap_review_queue("whieda", dry_run=True)

    assert result.inserted == 2
    insert_mock.assert_not_called()


@pytest.mark.asyncio
async def test_patch_creates_audit_and_resolved_timestamp():
    item_id = str(uuid4())
    existing = {
        "id": item_id,
        "status": "new",
        "priority": "p0",
        "owner_role": "medical",
        "owner_name": None,
        "operator_note": None,
        "candidate_type": "medical_review",
        "resolved_at": None,
    }
    updated = dict(existing)
    updated["status"] = "resolved"
    updated["resolved_at"] = NOW

    conn = AsyncMock()

    with patch("app.admin.gap_review.service.tenant_connection", _fake_tenant_connection(conn)):
        with patch(
            "app.admin.gap_review.service.repo.fetch_review_item",
            AsyncMock(side_effect=[existing, existing]),
        ):
            with patch("app.admin.gap_review.service.repo.update_review_item", AsyncMock(return_value=updated)):
                with patch("app.admin.gap_review.service.repo.insert_mutation", AsyncMock()) as mutation_mock:
                    with patch("app.admin.gap_review.service.write_audit_log", AsyncMock()):
                        result = await patch_item(
                            "whieda",
                            item_id,
                            {"status": "resolved"},
                            actor_principal_id=str(uuid4()),
                        )

    assert result["ok"] is True
    assert result["item"]["status"] == "resolved"
    mutation_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_gap_routes_require_auth(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import AsyncMock
    from app.main import create_app
    from app.tenancy import TenantContext

    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(_host: str) -> TenantContext:
        return TenantContext(
            tenant_id="whieda",
            status="active",
            display_name="WHIEDA",
            entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
        )

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    monkeypatch.setattr("app.tenancy._load_tenant", resolve)

    app = create_app()
    app.state.http_client = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cabinet.test.local") as client:
        response = await client.get("/v1/admin/advisor-gaps/summary")
        assert response.status_code == 401
