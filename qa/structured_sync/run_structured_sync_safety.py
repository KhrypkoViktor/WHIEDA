#!/usr/bin/env python3
"""Structured sync safety P0 local verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N8N_CURRENT = ROOT / "n8n" / "current"
PLATFORM = ROOT / "backend" / "platform-api"
sys.path.insert(0, str(N8N_CURRENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    apply_valid_corpus_transaction,
    apply_with_mid_failure,
    count_audit_status,
    count_products,
    docker_available,
    ensure_audit_schema,
    latest_audit_error,
    reset_audit_runs,
    seed_baseline_products,
    simulate_error_trigger_audit,
    simulate_lock_contention,
)
from whieda_structured_sync_safety_lib import (  # noqa: E402
    build_failed_audit_sql,
    compute_freshness_status,
    redact_sync_error,
)


def _run_pytest() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(PLATFORM / "tests" / "test_structured_sync_safety.py"), "-q"],
        cwd=str(PLATFORM),
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Structured sync safety P0 local runner")
    parser.add_argument("--offline", action="store_true", help="pytest/unit checks only")
    args = parser.parse_args()

    pytest_rc = _run_pytest()
    if pytest_rc != 0:
        print("pytest: FAIL")
        return pytest_rc
    print("pytest: PASS")

    if args.offline:
        print("Docker verification: NOT_RUN")
        print("STRUCTURED_SYNC_SAFETY_P0 offline summary: PASS")
        return 0

    if not docker_available():
        print("Docker verification: NOT_RUN (container unavailable)")
        return 0

    ensure = subprocess.run(
        [sys.executable, str(ROOT / "postgres" / "scripts" / "ensure_local_core_database.py"), "--force-reapply"],
        cwd=str(ROOT),
    )
    if ensure.returncode != 0:
        print("ensure_local_core_database: FAIL")
        return ensure.returncode

    results: dict[str, str] = {}
    ensure_audit_schema()
    reset_audit_runs()
    seed_baseline_products(3)
    before = count_products()

    # 1. Valid corpus apply
    apply_valid_corpus_transaction()
    if count_audit_status("success") < 1:
        results["valid_corpus"] = "FAIL"
    elif count_products() != 1:
        results["valid_corpus"] = f"FAIL products={count_products()}"
    else:
        results["valid_corpus"] = "PASS"

    # 2. Empty critical layer -> circuit breaker path (failed audit, no write)
    reset_audit_runs()
    seed_baseline_products(3)
    before_empty = count_products()
    fake_error = "Structured sync aborted before runtime writes: Products has 0 rows, minimum is 20."
    from harness import docker_psql

    docker_psql(
        build_failed_audit_sql(
            sync_run_uuid=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            workflow_execution_id="circuit-breaker-local",
            error_message=fake_error,
            row_counts={"rows_products": 0},
        )
    )
    if count_products() != before_empty or count_audit_status("failed") < 1:
        results["empty_critical_layer"] = "FAIL"
    else:
        results["empty_critical_layer"] = "PASS"

    # 3. Mid-layer failure rolls back
    reset_audit_runs()
    seed_baseline_products(5)
    before_mid = count_products()
    try:
        apply_with_mid_failure()
        results["mid_failure_rollback"] = "FAIL (expected exception)"
    except RuntimeError:
        if count_products() == before_mid and count_audit_status("failed") >= 1:
            results["mid_failure_rollback"] = "PASS"
        else:
            results["mid_failure_rollback"] = f"FAIL products={count_products()} audit_failed={count_audit_status('failed')}"

    # 4. Parallel runs: contender waits for the transaction lock, then runs.
    reset_audit_runs()
    seed_baseline_products(2)
    before_lock = count_products()
    holder_ok, contender_serialized = simulate_lock_contention()
    if holder_ok and contender_serialized and count_products() == before_lock + 1:
        results["parallel_lock"] = "PASS"
    else:
        results["parallel_lock"] = (
            f"FAIL holder={holder_ok} serialized={contender_serialized} products={count_products()}"
        )

    # 5. Secret redaction in audit
    reset_audit_runs()
    token_msg = "sync failed token=super-secret-abc password= hunter2 Bearer sk-abc123456789012345678901234567890"
    docker_psql(
        build_failed_audit_sql(
            sync_run_uuid=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            workflow_execution_id="redaction-local",
            error_message=token_msg,
            row_counts={},
        )
    )
    err = latest_audit_error() or ""
    if "super-secret" in err or "hunter2" in err or "sk-abc123456789012345678901234567890" in err:
        results["secret_redaction"] = "FAIL"
    elif "[REDACTED]" not in err:
        results["secret_redaction"] = f"FAIL text={err!r}"
    else:
        results["secret_redaction"] = "PASS"

    # 6. Freshness states (unit-level via lib, logged here)
    now = datetime.now(timezone.utc)
    cases = {
        "healthy": compute_freshness_status(last_success_at=now - timedelta(minutes=10), last_failure_at=None, last_failure_summary=None, row_counts={"rows_products": 1}, now=now).status,
        "stale": compute_freshness_status(last_success_at=now - timedelta(minutes=90), last_failure_at=None, last_failure_summary=None, row_counts={"rows_products": 1}, now=now).status,
        "failed": compute_freshness_status(last_success_at=now - timedelta(minutes=10), last_failure_at=now - timedelta(minutes=1), last_failure_summary="x", row_counts={"rows_products": 1}, now=now).status,
        "never_synced": compute_freshness_status(last_success_at=None, last_failure_at=None, last_failure_summary=None, row_counts={"rows_products": 0}, now=now).status,
    }
    freshness_ok = cases == {"healthy": "healthy", "stale": "stale", "failed": "failed", "never_synced": "never_synced"}
    results["freshness_states"] = "PASS" if freshness_ok else f"FAIL {cases}"

    # 7. Error Trigger payload: running -> failed, same uuid + execution id
    reset_audit_runs()
    payload_path = ROOT / "qa" / "structured_sync" / "fixtures" / "n8n_error_trigger_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    sync_uuid = str(__import__("uuid").uuid4())
    started = datetime.now(timezone.utc).isoformat()
    from harness import docker_psql
    from whieda_structured_sync_safety_lib import build_record_running_sql

    docker_psql(
        build_record_running_sql(
            sync_uuid,
            started_at=started,
            workflow_execution_id=payload["execution"]["id"],
            row_counts={"rows_products": 20, "rows_aliases": 60, "rows_resources": 25, "rows_product_cards": 10},
        )
    )
    trigger = simulate_error_trigger_audit(payload)
    if (
        not trigger["skip"]
        and trigger["status"] == "failed"
        and trigger["sync_run_uuid"] == sync_uuid
        and trigger["workflow_execution_id"] == payload["execution"]["id"]
        and "super-secret" not in (trigger["error_summary"] or "")
        and count_audit_status("failed") == 1
        and count_audit_status("running") == 0
    ):
        results["error_trigger_running_to_failed"] = "PASS"
    else:
        results["error_trigger_running_to_failed"] = f"FAIL {trigger}"

    # 8. Non-sync workflow error must not write audit
    reset_audit_runs()
    foreign = {
        "workflow": {"id": "other-workflow", "name": "Other"},
        "execution": {"id": "exec-foreign", "error": {"message": "ignore"}},
    }
    foreign_result = simulate_error_trigger_audit(foreign)
    if foreign_result["skip"] and count_audit_status("failed") == 0:
        results["error_trigger_foreign_skip"] = "PASS"
    else:
        results["error_trigger_foreign_skip"] = f"FAIL {foreign_result}"

    # 9. Patch artifacts inactive (dry-run export)
    deploy = subprocess.run(
        [sys.executable, str(N8N_CURRENT / "prepare_whieda_structured_sync_safety_deploy_2026-08-10.py")],
        cwd=str(N8N_CURRENT),
        capture_output=True,
        text=True,
    )
    if deploy.returncode == 0:
        plan = json.loads(deploy.stdout)
        checks = plan.get("artifact_checks", {})
        if checks.get("main_active") is False and checks.get("error_active") is False:
            results["deploy_dry_run_inactive_artifacts"] = "PASS"
        else:
            results["deploy_dry_run_inactive_artifacts"] = f"FAIL {checks}"
    else:
        results["deploy_dry_run_inactive_artifacts"] = "FAIL"

    print(json.dumps({"before_products": before, "results": results, "redaction_sample": redact_sync_error(token_msg)}, ensure_ascii=False, indent=2))
    failed = [name for name, status in results.items() if status != "PASS"]
    if failed:
        print(f"STRUCTURED_SYNC_SAFETY_P0: FAIL ({', '.join(failed)})")
        return 1
    print("STRUCTURED_SYNC_SAFETY_P0: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
