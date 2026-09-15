"""Core Gate J: schema-feature readiness and controlled degradation."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.db_feature_readiness import (
    CACHE_TTL_SECONDS,
    FEATURE_REQUIREMENTS,
    ONBOARDING_UNAVAILABLE_TEXT,
    FeatureStatus,
    SchemaFeatureUnavailable,
    SQL_DIR,
    evaluate_from_relations,
    format_status_lines,
    get_feature_status,
    log_feature_unavailable,
    reset_feature_readiness_cache,
    reset_feature_readiness_for_tests,
    set_clock_for_tests,
    set_table_probe_for_tests,
    unknown_report,
)
from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.inbox import InMemoryInboxStore, PostgresInboxStore, set_inbox_store_for_tests
from app.telegram.processor import handle_onboarding, process_core_telegram_update
from app.telegram.update_parser import parse_telegram_message

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "backend" / "platform-api" / "scripts" / "check_schema_feature_readiness.py"
QA = ROOT / "qa" / "schema_feature_readiness"
SCRIPTS = ROOT / "postgres" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from staging_proof_lib import APPLY_ORDER  # noqa: E402

FORBIDDEN = (
    "postgresql://",
    "postgres://",
    "bot123456",
    "AAHsecret",
    "+375111111111",
    "supabase",
    "185.252.232.93",
    "wwc.best",
)


@pytest.fixture(autouse=True)
def _reset_readiness():
    reset_feature_readiness_for_tests()
    yield
    reset_feature_readiness_for_tests()


@pytest.fixture
def inbox_store():
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    yield store
    set_inbox_store_for_tests(None)


@pytest.fixture
def nsp_binding(whieda_tenant):
    tenant = type(whieda_tenant)(
        tenant_id="tenant-north",
        status="active",
        display_name="North",
        entitlements={},
    )
    return BotBindingContext(
        binding_id="north-advisor-bot",
        tenant=tenant,
        bot_token_ref="env:NORTH_BOT_TOKEN",
        webhook_secret_ref="env:NORTH_WEBHOOK_SECRET",
        bot_username="north_bot",
        status="active",
        processing_mode="core",
        bot_token="north-test-token",
        webhook_secret="north-test-secret",
    )


def _onboarding_update(text: str = "начать обучение", update_id: int = 9101) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 11,
            "text": text,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200},
        },
    }


def _message(text: str):
    parsed = parse_telegram_message(_onboarding_update(text))
    assert parsed is not None
    return parsed


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _assert_redacted(blob: str) -> None:
    lowered = blob.lower()
    for item in FORBIDDEN:
        assert item.lower() not in lowered


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_ready_manifest_matches_sample():
    relations = json.loads((QA / "manifests" / "ready.json").read_text(encoding="utf-8"))["relations"]
    report = evaluate_from_relations(relations)
    expected = json.loads((QA / "samples" / "ready.json").read_text(encoding="utf-8"))
    assert report.to_json() == expected
    assert format_status_lines(report) == "ready"
    _assert_redacted(json.dumps(report.to_json()))


def test_partial_missing_tables_are_named_exactly():
    relations = json.loads(
        (QA / "manifests" / "degraded-onboarding.json").read_text(encoding="utf-8")
    )["relations"]
    report = evaluate_from_relations(relations)
    expected = json.loads((QA / "samples" / "degraded-onboarding.json").read_text(encoding="utf-8"))
    assert report.to_json() == expected
    onboarding = next(item for item in report.features if item.feature == "onboarding")
    assert onboarding.state == "degraded"
    assert onboarding.missing_tables == ("onboarding_enrollments",)
    assert format_status_lines(report) == "degraded: onboarding (missing onboarding_enrollments)"
    _assert_redacted(json.dumps(report.to_json()))
    _assert_redacted(format_status_lines(report))


def test_unknown_probe_is_not_ready():
    report = unknown_report()
    assert report.state == "unknown"
    assert report.ok is False
    assert format_status_lines(report) == "unknown: database probe failed"
    assert all(item.state == "unknown" for item in report.features)


def test_registry_migrations_exist_in_sql_and_apply_order():
    for name, spec in FEATURE_REQUIREMENTS.items():
        migration = spec["migration"]
        path = SQL_DIR / migration
        assert path.is_file(), f"{name} migration missing: {migration}"
        assert migration in APPLY_ORDER, f"{name} migration not in APPLY_ORDER: {migration}"
        for table in spec["tables"]:
            assert table in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ready_onboarding_keeps_existing_answer(whieda_tenant, nsp_binding):
    async def probe(_tables):
        return ()

    set_table_probe_for_tests(probe)
    with patch(
        "app.telegram.processor.handle_onboarding_text",
        AsyncMock(return_value={"ok": True, "answer_text": "Старт", "created": True}),
    ) as service:
        with patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
            with binding_context_scope(nsp_binding):
                result = await handle_onboarding(whieda_tenant, _message("начать обучение"))
    assert result is not None
    assert result["route"] == "onboarding"
    assert result["answer_text"] == "Старт"
    service.assert_awaited_once()
    deliver.assert_awaited_once()
    assert deliver.await_args.args[1] == "Старт"


@pytest.mark.asyncio
async def test_missing_enrollments_skips_onboarding_db_and_uses_temp_text(
    whieda_tenant, nsp_binding, caplog
):
    async def probe(tables):
        return tuple(name for name in tables if name == "onboarding_enrollments")

    set_table_probe_for_tests(probe)
    caplog.set_level(logging.WARNING)
    with patch("app.telegram.processor.handle_onboarding_text", AsyncMock()) as service:
        with patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
            with binding_context_scope(nsp_binding):
                result = await handle_onboarding(whieda_tenant, _message("мой план"))
    assert result is not None
    assert result["route"] == "onboarding_unavailable"
    service.assert_not_awaited()
    deliver.assert_awaited_once()
    assert deliver.await_args.args[1] == ONBOARDING_UNAVAILABLE_TEXT
    record = next(item for item in caplog.records if item.msg == "schema_feature_unavailable")
    assert record.feature == "onboarding"
    assert record.missing_tables == ["onboarding_enrollments"]
    _assert_redacted(caplog.text)
    _assert_redacted(str(record.__dict__))


@pytest.mark.asyncio
async def test_probe_failure_is_unavailable_not_ready(whieda_tenant, nsp_binding):
    async def probe(_tables):
        return None

    set_table_probe_for_tests(probe)
    with patch("app.telegram.processor.handle_onboarding_text", AsyncMock()) as service:
        with patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
            with binding_context_scope(nsp_binding):
                result = await handle_onboarding(whieda_tenant, _message("начать обучение"))
    assert result["route"] == "onboarding_unavailable"
    service.assert_not_awaited()
    deliver.assert_awaited_once_with(100, ONBOARDING_UNAVAILABLE_TEXT)
    status = await get_feature_status("onboarding")
    assert status.state == "unknown"
    assert status.ready is False


@pytest.mark.asyncio
async def test_non_onboarding_text_does_not_probe(whieda_tenant, nsp_binding):
    probe = AsyncMock(return_value=())
    set_table_probe_for_tests(probe)
    with patch("app.telegram.processor.handle_onboarding_text", AsyncMock()) as service:
        with binding_context_scope(nsp_binding):
            result = await handle_onboarding(whieda_tenant, _message("цена активатор"))
    assert result is None
    service.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_ttl_cache_and_reset_force_fresh_probe():
    calls = {"n": 0}

    async def probe(_tables):
        calls["n"] += 1
        return ()

    clock = FakeClock(10.0)
    set_table_probe_for_tests(probe)
    set_clock_for_tests(clock)
    first = await get_feature_status("onboarding")
    second = await get_feature_status("onboarding")
    assert first.ready and second.ready
    assert calls["n"] == 1
    clock.value = 10.0 + CACHE_TTL_SECONDS + 0.1
    third = await get_feature_status("onboarding")
    assert third.ready
    assert calls["n"] == 2
    reset_feature_readiness_cache()
    fourth = await get_feature_status("onboarding")
    assert fourth.ready
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_missing_enrollments_webhook_acks_200(
    client, inbox_store, nsp_binding, monkeypatch, caplog
):
    async def probe(tables):
        return tuple(name for name in tables if name == "onboarding_enrollments")

    set_table_probe_for_tests(probe)
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    caplog.set_level(logging.WARNING)
    with patch("app.telegram.processor.handle_onboarding_text", AsyncMock()) as service:
        with patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
            response = await client.post(
                "/v1/telegram/north-advisor-bot/webhook",
                json=_onboarding_update("начать обучение"),
                headers={"x-telegram-bot-api-secret-token": "north-test-secret"},
            )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    service.assert_not_awaited()
    deliver.assert_awaited_once()
    assert deliver.await_args.args[1] == ONBOARDING_UNAVAILABLE_TEXT
    _assert_redacted(caplog.text)
    _assert_redacted(json.dumps(response.json()))


@pytest.mark.asyncio
async def test_ready_onboarding_still_routes_before_advisor(whieda_tenant, nsp_binding):
    async def probe(_tables):
        return ()

    set_table_probe_for_tests(probe)
    with patch(
        "app.telegram.processor.handle_onboarding_text",
        AsyncMock(return_value={"ok": True, "answer_text": "План"}),
    ):
        with patch("app.telegram.processor.deliver_text", AsyncMock()):
            with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
                result = await process_core_telegram_update(
                    whieda_tenant,
                    _onboarding_update("мой план"),
                    "t-ready",
                    binding=nsp_binding,
                )
    assert result["route"] == "onboarding"
    advisor.assert_not_called()


@pytest.mark.asyncio
async def test_postgres_inbox_skips_sql_when_table_missing(monkeypatch):
    async def probe(_tables):
        return ("telegram_update_inbox",)

    set_table_probe_for_tests(probe)
    fetch = AsyncMock()
    monkeypatch.setattr("app.telegram.inbox._fetch_one", fetch)
    with pytest.raises(SchemaFeatureUnavailable) as exc:
        await PostgresInboxStore().enqueue(
            binding_id="north-advisor-bot",
            tenant_id="tenant-north",
            telegram_update_id=1,
            payload={"update_id": 1},
        )
    assert exc.value.status.feature == "telegram_durable_inbox"
    assert exc.value.status.missing_tables == ("telegram_update_inbox",)
    fetch.assert_not_awaited()
    _assert_redacted(str(exc.value))


@pytest.mark.asyncio
async def test_inbox_schema_missing_falls_back_to_in_process(client, inbox_store, nsp_binding, monkeypatch):
    """Missing inbox tables must not drop the update: it takes the in-process path."""
    status = FeatureStatus(
        feature="telegram_durable_inbox",
        state="degraded",
        missing_tables=("telegram_update_inbox",),
        migration="platform_telegram_durable_inbox_v1.sql",
    )
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    monkeypatch.setattr("app.telegram.routes._durable_inbox_enabled", lambda binding: True)
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)
    monkeypatch.setattr(
        "app.telegram.routes.get_inbox_store",
        lambda: type("Broken", (), {"enqueue": AsyncMock(side_effect=SchemaFeatureUnavailable(status))})(),
    )
    response = await client.post(
        "/v1/telegram/north-advisor-bot/webhook",
        json=_onboarding_update(),
        headers={"x-telegram-bot-api-secret-token": "north-test-secret"},
    )
    assert response.status_code == 200
    assert inbox_store.snapshot_rows() == []
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_inbox_probe_unknown_returns_503(client, inbox_store, nsp_binding, monkeypatch):
    status = FeatureStatus(
        feature="telegram_durable_inbox",
        state="unknown",
        missing_tables=(),
        migration="platform_telegram_durable_inbox_v1.sql",
    )
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    monkeypatch.setattr("app.telegram.routes._durable_inbox_enabled", lambda binding: True)
    monkeypatch.setattr(
        "app.telegram.routes.get_inbox_store",
        lambda: type("Broken", (), {"enqueue": AsyncMock(side_effect=SchemaFeatureUnavailable(status))})(),
    )
    response = await client.post(
        "/v1/telegram/north-advisor-bot/webhook",
        json=_onboarding_update(),
        headers={"x-telegram-bot-api-secret-token": "north-test-secret"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "telegram_inbox_unavailable"
    assert inbox_store.snapshot_rows() == []


def test_cli_ready_and_degraded_and_refuses_apply(tmp_path: Path):
    ready = _run("--offline-manifest", str(QA / "manifests" / "ready.json"))
    assert ready.returncode == 0
    assert ready.stdout.strip() == "ready"
    _assert_redacted(ready.stdout + ready.stderr)

    report_out = tmp_path / "degraded.json"
    degraded = _run(
        "--offline-manifest",
        str(QA / "manifests" / "degraded-onboarding.json"),
        "--report-out",
        str(report_out),
    )
    assert degraded.returncode == 2
    assert degraded.stdout.strip() == "degraded: onboarding (missing onboarding_enrollments)"
    payload = json.loads(report_out.read_text(encoding="utf-8"))
    assert payload["state"] == "degraded"
    _assert_redacted(degraded.stdout + json.dumps(payload))

    refused = _run("--offline-manifest", str(QA / "manifests" / "ready.json"), "--apply")
    assert refused.returncode == 1
    body = json.loads(refused.stdout)
    assert body["code"] == "action_refused"
    publish = _run("--publish")
    assert publish.returncode == 1
    dsn = _run("--dsn", "postgresql://user:secret@127.0.0.1/db")
    assert dsn.returncode == 1
    _assert_redacted(dsn.stdout)


def test_logs_never_include_secrets_or_user_payload(caplog):
    status = FeatureStatus(
        feature="onboarding",
        state="degraded",
        missing_tables=("onboarding_enrollments",),
        migration="platform_onboarding_v1.sql",
    )
    with caplog.at_level(logging.WARNING):
        log_feature_unavailable(status)
    record = caplog.records[-1]
    blob = f"{record.getMessage()} {record.__dict__}"
    assert "onboarding_enrollments" in str(record.missing_tables)
    assert "начать обучение" not in blob
    _assert_redacted(blob)
