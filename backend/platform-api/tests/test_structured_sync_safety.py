"""Unit tests for structured sync safety P0 helpers."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import json
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "n8n" / "current"))

from whieda_structured_sync_safety_lib import (  # noqa: E402
    ADVISORY_LOCK_KEY1,
    ADVISORY_LOCK_KEY2,
    ERROR_WORKFLOW_ID_PLACEHOLDER,
    STRUCTURED_SYNC_WORKFLOW_ID,
    build_apply_all_transaction,
    build_error_trigger_failed_audit_sql,
    build_error_trigger_from_payload,
    build_failed_audit_sql,
    build_record_running_sql,
    compute_freshness_status,
    future_sync_status_api_shape,
    redact_sync_error,
)
from whieda_structured_sync_workflow_safety_p0 import (  # noqa: E402
    build_error_audit_workflow,
    build_workflow_safety_p0,
)


def test_redact_sync_error_strips_secrets() -> None:
    raw = "failed token=abc123 password=secret Bearer sk-123456789012345678901234567890 user 123456789"
    redacted = redact_sync_error(raw)
    assert "abc123" not in redacted
    assert "secret" not in redacted or "[REDACTED]" in redacted
    assert "sk-123456789012345678901234567890" not in redacted
    assert "INSERT INTO" not in redacted.upper()


def test_apply_all_transaction_wraps_begin_commit_and_lock() -> None:
    sql = build_apply_all_transaction(["SELECT 1;"], "uuid-1")
    assert sql.startswith("BEGIN;")
    assert "COMMIT;" in sql
    assert "SET LOCAL lock_timeout = '10s';" in sql
    assert f"pg_advisory_xact_lock({ADVISORY_LOCK_KEY1}, {ADVISORY_LOCK_KEY2})" in sql
    assert "DO $$" not in sql
    assert "sync_run_uuid = 'uuid-1'" in sql


def test_live_javascript_lock_avoids_dollar_quoted_blocks() -> None:
    source = (ROOT / "n8n" / "current" / "whieda_structured_sync_code_2026-07-13.js").read_text(
        encoding="utf-8"
    )
    assert "SET LOCAL lock_timeout = '10s';" in source
    assert "pg_advisory_xact_lock(${ADVISORY_LOCK_KEY1}, ${ADVISORY_LOCK_KEY2})" in source
    assert "DO $$" not in source


def test_record_running_sql_inserts_running_status() -> None:
    sql = build_record_running_sql(
        "uuid-2",
        started_at="2026-08-10T08:00:00+00:00",
        workflow_execution_id="exec-1",
        row_counts={"rows_products": 20, "rows_aliases": 60},
    )
    assert "'running'" in sql
    assert "sync_run_uuid" in sql
    assert "rows_products" in sql


def test_failed_audit_sql_redacts_error_summary() -> None:
    sql = build_failed_audit_sql(
        sync_run_uuid="uuid-3",
        started_at="2026-08-10T08:00:00+00:00",
        workflow_execution_id="exec-2",
        error_message="password=hunter2",
        row_counts={"rows_products": 0},
    )
    assert "hunter2" not in sql
    assert "[REDACTED]" in sql


@pytest.mark.parametrize(
    ("age_minutes", "expected"),
    [
        (10, "healthy"),
        (90, "stale"),
    ],
)
def test_freshness_healthy_and_stale(age_minutes: int, expected: str) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    snapshot = compute_freshness_status(
        last_success_at=now - timedelta(minutes=age_minutes),
        last_failure_at=None,
        last_failure_summary=None,
        row_counts={"rows_products": 5},
        now=now,
    )
    assert snapshot.status == expected


def test_freshness_failed_when_recent_failure_after_success() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    snapshot = compute_freshness_status(
        last_success_at=now - timedelta(minutes=60),
        last_failure_at=now - timedelta(minutes=5),
        last_failure_summary="circuit breaker",
        row_counts={"rows_products": 5},
        now=now,
    )
    assert snapshot.status == "failed"


def test_freshness_never_synced_without_cache() -> None:
    snapshot = compute_freshness_status(
        last_success_at=None,
        last_failure_at=None,
        last_failure_summary=None,
        row_counts={"rows_products": 0},
    )
    assert snapshot.status == "never_synced"


def test_future_api_shape_documents_contract() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    snapshot = compute_freshness_status(
        last_success_at=now - timedelta(minutes=5),
        last_failure_at=None,
        last_failure_summary=None,
        row_counts={"rows_products": 3},
        now=now,
    )
    payload = future_sync_status_api_shape(snapshot)
    assert payload["structured_sync"]["status"] == "healthy"
    assert "row_counts" in payload["structured_sync"]
    assert payload["structured_sync"]["healthy_threshold_minutes"] == 30


def test_workflow_patch_artifacts_are_inactive() -> None:
    code = "return [{ json: { query_apply_all: 'SELECT 1' } }];"
    main = build_workflow_safety_p0(code, active=False)
    error = build_error_audit_workflow(active=False)
    assert main["active"] is False
    assert error["active"] is False


def test_workflow_artifact_uses_error_workflow_id_placeholder() -> None:
    code = "return [{ json: { query_apply_all: 'SELECT 1' } }];"
    main = build_workflow_safety_p0(code, active=False)
    assert main["settings"]["errorWorkflow"] == ERROR_WORKFLOW_ID_PLACEHOLDER


def test_deploy_prepare_replaces_placeholder_with_real_id() -> None:
    code = "return [{ json: { query_apply_all: 'SELECT 1' } }];"
    prepared = build_workflow_safety_p0(code, active=False, error_workflow_id="wf-error-real-99")
    assert prepared["settings"]["errorWorkflow"] == "wf-error-real-99"
    assert prepared["settings"]["errorWorkflow"] != ERROR_WORKFLOW_ID_PLACEHOLDER


def test_error_trigger_skips_non_structured_sync_workflow() -> None:
    payload = {
        "workflow": {"id": "other-workflow-id", "name": "Other"},
        "execution": {"id": "exec-999", "error": {"message": "boom"}},
    }
    result = build_error_trigger_from_payload(payload)
    assert result["skip_failed_audit"] is True
    assert "SELECT 1" in result["query_failed_audit"]


def test_error_trigger_updates_running_row_by_original_execution_id() -> None:
    payload = {
        "workflow": {"id": STRUCTURED_SYNC_WORKFLOW_ID, "name": "WHIEDA Structured Sync Cron"},
        "execution": {
            "id": "exec-original-777",
            "error": {"message": "failed token=abc123 password=secret"},
        },
    }
    sql = build_error_trigger_from_payload(payload)["query_failed_audit"]
    assert "exec-original-777" in sql
    assert "workflow_execution_id" in sql
    assert "abc123" not in sql
    assert "secret" not in sql or "[REDACTED]" in sql
    assert "WITH updated AS" in sql


def test_error_trigger_payload_fixture_file_exists() -> None:
    fixture = ROOT / "qa" / "structured_sync" / "fixtures" / "n8n_error_trigger_payload.json"
    assert fixture.is_file()
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    parsed = build_error_trigger_from_payload(payload)
    assert parsed["skip_failed_audit"] is False
    assert parsed["original_execution_id"] == "exec-original-12345"


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeN8nSession:
    def __init__(
        self,
        *,
        main_active: bool = True,
        error_active: bool = False,
        error_exists: bool = True,
        fail_on: str | None = None,
    ):
        self.main_active = main_active
        self.error_active = error_active
        self.error_exists = error_exists
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []
        self.main_id = "main-wf-1"
        self.error_id = "error-wf-9"
        self.workflows: dict[str, dict] = {
            self.main_id: {
                "id": self.main_id,
                "name": "WHIEDA Structured Sync Cron",
                "active": main_active,
                "versionId": "v-main-1",
                "settings": {},
                "nodes": [],
                "connections": {},
            },
        }
        if error_exists:
            self.workflows[self.error_id] = {
                "id": self.error_id,
                "name": "WHIEDA Structured Sync Error Audit",
                "active": error_active,
                "versionId": "v-error-1",
                "nodes": [],
                "connections": {},
            }
        self._patch_count = 0
        self._main_activate_attempts = 0

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append(("get", url))
        if url.endswith("/rest/workflows?limit=200"):
            return _FakeResponse({"data": list(self.workflows.values())})
        workflow_id = url.rsplit("/", 1)[-1]
        return _FakeResponse({"data": dict(self.workflows[workflow_id])})

    def post(self, url: str, json=None, **kwargs) -> _FakeResponse:
        self.calls.append(("post", url))
        if url.endswith("/rest/workflows"):
            body = dict(json or {})
            new_id = body.get("id") or "error-wf-new"
            body["id"] = new_id
            body.setdefault("versionId", "v-new")
            self.workflows[new_id] = body
            return _FakeResponse({"data": body})
        if url.endswith("/activate"):
            workflow_id = url.split("/")[-2]
            if workflow_id == self.main_id:
                self._main_activate_attempts += 1
                if self.fail_on == "activate_main" and self._main_activate_attempts == 1:
                    raise RuntimeError("forced activate failure")
            self.workflows[workflow_id]["active"] = True
            return _FakeResponse({"data": {"active": True}})
        if url.endswith("/deactivate"):
            workflow_id = url.split("/")[-2]
            self.workflows[workflow_id]["active"] = False
            return _FakeResponse({"data": {"active": False}})
        return _FakeResponse({"data": {}})

    def patch(self, url: str, json=None, **kwargs) -> _FakeResponse:
        self.calls.append(("patch", url))
        self._patch_count += 1
        if self.fail_on == "save_main" and self._patch_count >= 2:
            raise RuntimeError("forced main save failure")
        workflow_id = url.rsplit("/", 1)[-1]
        body = dict(json or {})
        body["id"] = workflow_id
        body.setdefault("versionId", self.workflows[workflow_id].get("versionId", "v-patched"))
        if body.get("settings", {}).get("errorWorkflow"):
            body["active"] = False
        self.workflows[workflow_id] = {**self.workflows.get(workflow_id, {}), **body}
        return _FakeResponse({"data": body})


def _load_deploy_module():
    import importlib.util

    deploy_path = ROOT / "n8n" / "current" / "prepare_whieda_structured_sync_safety_deploy_2026-08-10.py"
    spec = importlib.util.spec_from_file_location("whieda_deploy_guard", deploy_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_deploy_dry_run_does_not_use_network() -> None:
    deploy = _load_deploy_module()
    plan = deploy.build_dry_run_plan("return [];")
    assert plan["mode"] == "dry_run"
    assert plan["network"] == "not_used"


def test_deploy_apply_restores_active_main_and_links_error() -> None:
    deploy = _load_deploy_module()
    session = FakeN8nSession(main_active=True)
    plan = deploy.apply_deploy("return [];", session, backup_dir=Path("/tmp/whieda-deploy-test"))
    assert plan["prior_main_state"]["active"] is True
    assert plan["final_main_state"]["active"] is True
    assert plan["final_error_state"]["active"] is True
    assert session.workflows[session.main_id]["settings"]["errorWorkflow"] == session.error_id
    assert any(call[0] == "post" and call[1].endswith("/activate") for call in session.calls)


def test_deploy_apply_inactive_main_leaves_workflows_inactive() -> None:
    deploy = _load_deploy_module()
    session = FakeN8nSession(main_active=False)
    plan = deploy.apply_deploy("return [];", session, backup_dir=Path("/tmp/whieda-deploy-test"))
    assert plan["prior_main_state"]["active"] is False
    assert plan["final_main_state"]["active"] is False
    assert plan["final_error_state"]["active"] is False
    assert not any("activate" in call[1] for call in session.calls if call[0] == "post")


def test_deploy_apply_failure_rolls_back_main_and_restores_inactive_error() -> None:
    deploy = _load_deploy_module()
    session = FakeN8nSession(main_active=True, error_active=False, fail_on="activate_main")
    with pytest.raises(deploy.DeployApplyError) as exc:
        deploy.apply_deploy("return [];", session, backup_dir=Path("/tmp/whieda-deploy-test"))
    plan = exc.value.plan
    assert plan["prior_error_state"]["active"] is False
    assert plan["rollback"]["rolled_back"] is True
    assert plan["rollback"]["error_restored_from_backup"] is True
    assert plan["rollback"]["error_restored_active"] is False
    assert session.workflows[session.main_id]["active"] is True
    assert not session.workflows[session.main_id]["settings"].get("errorWorkflow")
    assert session.workflows[session.error_id]["active"] is False


def test_deploy_apply_failure_restores_previously_active_error() -> None:
    deploy = _load_deploy_module()
    session = FakeN8nSession(main_active=True, error_active=True, fail_on="activate_main")
    with pytest.raises(deploy.DeployApplyError) as exc:
        deploy.apply_deploy("return [];", session, backup_dir=Path("/tmp/whieda-deploy-test"))
    plan = exc.value.plan
    assert plan["prior_error_state"]["active"] is True
    assert plan["rollback"]["rolled_back"] is True
    assert plan["rollback"]["error_restored_from_backup"] is True
    assert plan["rollback"]["error_restored_active"] is True
    assert session.workflows[session.main_id]["active"] is True
    assert session.workflows[session.error_id]["active"] is True


def test_deploy_apply_failure_leaves_newly_created_error_inactive() -> None:
    deploy = _load_deploy_module()
    session = FakeN8nSession(main_active=True, error_exists=False, fail_on="activate_main")
    with pytest.raises(deploy.DeployApplyError) as exc:
        deploy.apply_deploy("return [];", session, backup_dir=Path("/tmp/whieda-deploy-test"))
    plan = exc.value.plan
    assert plan["prior_error_state"]["created_this_run"] is True
    assert plan["rollback"]["rolled_back"] is True
    assert session.workflows[session.main_id]["active"] is True
    error_id = plan["final_error_state"]["workflow_id"]
    assert error_id is not None
    assert session.workflows[error_id]["active"] is False
    assert "error_restored_from_backup" not in plan["rollback"]
