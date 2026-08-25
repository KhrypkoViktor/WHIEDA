"""Orchestrate the tenant Telegram local canary E2E lab."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from local_core_lab.constants import (
    APPLY_ORDER,
    CANARY_COMPOSE,
    CORE_COMPOSE,
    CORE_CONTAINER,
    DOCKER_CONTAINER,
    E2E_REPORTS_DIR,
    ENSURE_CANARY_DB,
    PLATFORM_API,
    POSTGRES_COMPOSE,
)
from local_core_lab.e2e_report import E2EReport, utc_now_iso, write_report
from local_core_lab.orchestrator import (
    OrchestratorConfig,
    OrchestratorState,
    _record_step,
    capture_versions,
    require_docker,
    wait_api_health,
)
from local_core_lab.subprocess_util import docker_logs, run_capture
from local_core_lab.telegram_canary_eval import evaluate_case_evidence
from local_core_lab.telegram_canary_run import run_cases
from local_core_lab.telegram_capture import TelegramCapture


def _stop_core() -> Any:
    return run_capture(
        ["docker", "compose", "-f", str(CANARY_COMPOSE), "down"],
        cwd=str(PLATFORM_API),
        name="stop_canary_core",
    )


def _fail(state: OrchestratorState, stage: str, message: str) -> int:
    state.report.status = "FAIL"
    state.report.failure_stage = stage
    state.report.failure_message = message
    state.report.finished_at = utc_now_iso()
    write_report(state.report, E2E_REPORTS_DIR)
    return 1


def run_telegram_canary_lab(
    config: OrchestratorConfig,
    *,
    run_capture_fn: Callable[..., Any] = run_capture,
    wait_health_fn: Callable[[int], dict[str, Any]] = wait_api_health,
) -> tuple[int, OrchestratorState]:
    state = OrchestratorState()
    report = state.report
    report.sql_files = list(APPLY_ORDER)
    report.seed_file = "local_telegram_canary_overlay_v1.sql"
    report.containers = [DOCKER_CONTAINER, CORE_CONTAINER]
    capture: TelegramCapture | None = None

    print("=== Tenant Telegram local canary E2E ===")
    try:
        require_docker()
        state.docker_ok = True
        capture_versions(report)

        step = run_capture_fn(
            ["docker", "compose", "-f", str(CORE_COMPOSE), "down"],
            cwd=str(PLATFORM_API),
            name="stop_existing_core",
        )
        _record_step(state, step)

        step = run_capture_fn(
            ["docker", "compose", "-f", str(POSTGRES_COMPOSE), "up", "-d"],
            cwd=str(POSTGRES_COMPOSE.parent),
            name="start_staging_postgres",
        )
        _record_step(state, step)
        if not step.ok:
            return _fail(state, "start_staging_postgres", step.stderr or "compose up failed"), state

        step = run_capture_fn([config.python, str(ENSURE_CANARY_DB)], name="ensure_canary_database")
        _record_step(state, step)
        if not step.ok:
            print(step.stdout)
            print(step.stderr)
            return _fail(state, "ensure_canary_database", "canary database setup failed"), state

        capture = TelegramCapture()
        capture.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:18081/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            return _fail(state, "telegram_capture", "capture health timeout"), state

        compose_cmd = ["docker", "compose", "-f", str(CANARY_COMPOSE), "up", "-d"]
        if not config.skip_build:
            compose_cmd.append("--build")
        step = run_capture_fn(compose_cmd, cwd=str(PLATFORM_API), name="start_canary_core")
        _record_step(state, step)
        if not step.ok:
            return _fail(state, "start_canary_core", step.stderr or "canary compose failed"), state

        health = wait_health_fn(config.health_timeout_sec)
        report.health = health
        if health.get("status") != "PASS":
            report.container_logs[CORE_CONTAINER] = docker_logs(CORE_CONTAINER)
            return _fail(state, "health_ready", health.get("error") or "health check failed"), state

        cases = run_cases(capture, E2E_REPORTS_DIR)
        failed = [case for case in cases if evaluate_case_evidence(case) != "PASS"]
        report.telegram_canary = {
            "status": "FAIL" if failed else "PASS",
            "checks": f"{len(cases) - len(failed)}/{len(cases)}",
            "cases": [
                {
                    "case_id": case.get("case_id"),
                    "status": evaluate_case_evidence(case),
                    "http_status": case.get("http_status"),
                    "inbox_actual": case.get("inbox_actual"),
                    "outbox_actual": case.get("outbox_actual"),
                    "outbox_statuses": case.get("outbox_statuses"),
                    "reason": case.get("reason"),
                }
                for case in cases
            ],
        }
        print(json.dumps(report.telegram_canary, ensure_ascii=False, indent=2))
        if failed:
            return _fail(
                state,
                "telegram_canary_cases",
                f"failed: {', '.join(str(case.get('case_id')) for case in failed)}",
            ), state

        report.status = "PASS"
        report.finished_at = utc_now_iso()
        write_report(report, E2E_REPORTS_DIR)
        print("\n=== TENANT TELEGRAM LOCAL CANARY: PASS ===")
        return 0, state
    except RuntimeError as exc:
        report.status = "NOT_RUN" if not state.docker_ok else "FAIL"
        report.failure_stage = "precheck"
        report.failure_message = str(exc)
        report.finished_at = utc_now_iso()
        write_report(report, E2E_REPORTS_DIR)
        print(f"FAIL: {exc}")
        return 1, state
    finally:
        if capture is not None:
            capture.stop()
        if state.docker_ok and not config.leave_core_up:
            stop = _stop_core()
            _record_step(state, stop)
            report.cleanup_status = "PASS" if stop.ok else "FAIL"
        elif state.docker_ok:
            report.cleanup_status = "SKIPPED"
        if state.docker_ok:
            report.container_logs[CORE_CONTAINER] = docker_logs(CORE_CONTAINER)
            report.container_logs[DOCKER_CONTAINER] = docker_logs(DOCKER_CONTAINER)
        if not report.finished_at:
            report.finished_at = utc_now_iso()
        write_report(report, E2E_REPORTS_DIR)
