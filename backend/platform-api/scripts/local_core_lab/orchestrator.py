"""Docker E2E orchestration for local Core lab."""

from __future__ import annotations

import http.client
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from local_core_lab.constants import (
    ACCEPTANCE_EXAMPLE_TARGET,
    ACCEPTANCE_LOCAL_TARGET,
    ACCEPTANCE_RUNNER,
    API_BASE,
    APPLY_ORDER,
    CORE_COMPOSE,
    CORE_CONTAINER,
    DOCKER_CONTAINER,
    E2E_REPORTS_DIR,
    ENSURE_CORE_DB,
    HTTP_SMOKE,
    LOCAL_CORE_SMOKE_CORPUS,
    LOCAL_CORE_DB,
    PARITY_CORPUS,
    PARITY_RUNNER,
    NO_BLIND_ZONE_RUNNER,
    NO_BLIND_ZONE_DB_PROOF,
    GAP_OPERATOR_RUNNER,
    CONVERSATION_RELIABILITY_RUNNER,
    TELEGRAM_EXPERIENCE_RUNNER,
    SOLUTION_BUNDLES_RUNNER,
    PLATFORM_API,
    POSTGRES_COMPOSE,
    SEED,
    STAGING_PROOF,
)
from local_core_lab.e2e_report import E2EReport, utc_now_iso, write_report
from local_core_lab.e2e_verify import run_verify
from local_core_lab.subprocess_util import StepResult, docker_logs, run_capture


@dataclass
class OrchestratorConfig:
    skip_build: bool = False
    leave_core_up: bool = False
    health_timeout_sec: int = 120
    python: str = field(default_factory=lambda: sys.executable)
    e2e_mode: bool = False
    parity_mode: bool = False
    no_blind_zone_mode: bool = False
    gap_operator_mode: bool = False
    conversation_reliability_mode: bool = False
    telegram_experience_mode: bool = False
    solution_bundles_mode: bool = False
    tenant_telegram_canary: bool = False


@dataclass
class OrchestratorState:
    docker_ok: bool = False
    report: E2EReport = field(default_factory=lambda: E2EReport(status="NOT_RUN", started_at=utc_now_iso()))
    steps: list[StepResult] = field(default_factory=list)


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker required — install Docker Desktop and retry.")


def capture_versions(report: E2EReport) -> None:
    docker_v = run_capture(["docker", "version", "--format", "{{.Server.Version}}"], name="docker-version")
    compose_v = run_capture(["docker", "compose", "version"], name="compose-version")
    report.docker_version = (docker_v.stdout or docker_v.stderr).strip() or None
    report.compose_version = (compose_v.stdout or compose_v.stderr).strip() or None


def ensure_acceptance_target() -> StepResult | None:
    if ACCEPTANCE_LOCAL_TARGET.is_file():
        return None
    if not ACCEPTANCE_EXAMPLE_TARGET.is_file():
        return StepResult(
            name="ensure_acceptance_target",
            command=[],
            returncode=1,
            error="acceptance_target.example.json missing",
        )
    ACCEPTANCE_LOCAL_TARGET.write_text(
        ACCEPTANCE_EXAMPLE_TARGET.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return StepResult(
        name="ensure_acceptance_target",
        command=["copy", str(ACCEPTANCE_EXAMPLE_TARGET), str(ACCEPTANCE_LOCAL_TARGET)],
        returncode=0,
        stdout=f"Created {ACCEPTANCE_LOCAL_TARGET.name} from example",
    )


def wait_api_health(timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    url = f"{API_BASE}/health/ready"
    req = urllib.request.Request(url, headers={"Host": "wwc.best"})
    last_error = "unknown"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200:
                    return {"status": "PASS", "http_status": 200, "body_preview": body[:200]}
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code != 503:
                body = exc.read().decode("utf-8", errors="replace")
                return {"status": "FAIL", "http_status": exc.code, "body_preview": body[:200]}
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last_error = str(getattr(exc, "reason", exc))
        time.sleep(2)
    return {"status": "FAIL", "error": f"timeout after {timeout_sec}s", "last_error": last_error}


def stop_core_only() -> StepResult:
    return run_capture(
        ["docker", "compose", "-f", str(CORE_COMPOSE), "down"],
        cwd=str(PLATFORM_API),
        name="stop_core_container",
    )


def _record_step(state: OrchestratorState, step: StepResult) -> None:
    state.steps.append(step)
    state.report.steps.append(
        {
            "name": step.name,
            "returncode": step.returncode,
            "stdout_tail": (step.stdout or "")[-500:],
            "stderr_tail": (step.stderr or "")[-500:],
            "error": step.error,
        }
    )


def _finalize_report(state: OrchestratorState) -> None:
    if not state.report.finished_at:
        state.report.finished_at = utc_now_iso()
    if state.docker_ok:
        state.report.container_logs[CORE_CONTAINER] = docker_logs(CORE_CONTAINER)
        state.report.container_logs[DOCKER_CONTAINER] = docker_logs(DOCKER_CONTAINER)
    write_report(state.report, E2E_REPORTS_DIR)


def _fail(state: OrchestratorState, stage: str, message: str, *, e2e: bool) -> int:
    state.report.status = "FAIL"
    state.report.failure_stage = stage
    state.report.failure_message = message
    if e2e:
        _finalize_report(state)
    return 1


def _parse_parity_summary(combined: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"summary_line": _extract_acceptance_summary(combined)}
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.startswith("P0:"):
            summary["p0_line"] = stripped
        if stripped.startswith("P1:"):
            summary["p1_line"] = stripped
        if stripped.startswith("total:"):
            summary["total_line"] = stripped
        if stripped.startswith("NBZ corpus:"):
            summary["total_line"] = stripped
        if stripped.startswith("not_run:"):
            try:
                summary["not_run"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                summary["not_run"] = stripped.split(":", 1)[1].strip()
        if stripped.startswith("timeout:"):
            summary["timeout"] = stripped.split(":", 1)[1].strip().strip("`")
    return summary


def _preflight_score(report: E2EReport) -> str:
    preflight = report.preflight_smoke or report.p0_acceptance
    text = str(preflight.get("summary") or "")
    match = re.search(r"pass\s+(\d+).*Total\s+(\d+)", text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    if preflight.get("status") == "PASS":
        return "8/8"
    return "FAIL"


def _print_final_summary(report: E2EReport) -> None:
    print(f"preflight: {_preflight_score(report)}")
    parity = report.parity_run
    if parity.get("p0_line"):
        print(parity["p0_line"])
    if parity.get("p1_line"):
        print(parity["p1_line"])
    if parity.get("total_line"):
        print(parity["total_line"])
    elif parity.get("summary_line"):
        print(parity["summary_line"])
    verify = report.verify_e2e.get("status")
    if verify:
        print(f"verify_e2e: {verify}")
    print(f"cleanup: {report.cleanup_status}")
    print(f"overall status: {report.status}")


def run_lab(
    config: OrchestratorConfig,
    *,
    run_capture_fn: Callable[..., StepResult] = run_capture,
    wait_health_fn: Callable[[int], dict[str, Any]] = wait_api_health,
) -> tuple[int, OrchestratorState]:
    if config.tenant_telegram_canary:
        from local_core_lab.telegram_canary_lab import run_telegram_canary_lab

        return run_telegram_canary_lab(
            config,
            run_capture_fn=run_capture_fn,
            wait_health_fn=wait_health_fn,
        )

    state = OrchestratorState()
    report = state.report
    report.sql_files = list(APPLY_ORDER)
    report.seed_file = SEED.name
    report.containers = [DOCKER_CONTAINER, CORE_CONTAINER]

    print("=== WHIEDA local Core runtime lab ===")
    if config.e2e_mode:
        print("=== E2E mode: Docker + acceptance + verify ===")
    if config.parity_mode:
        print("=== Parity mode: full advisor HTTP parity corpus ===")
    if config.no_blind_zone_mode:
        print("=== No blind zone mode: guided gap regression corpus ===")
    if config.gap_operator_mode:
        print("=== Gap operator mode: review queue + export proof ===")

    try:
        require_docker()
        state.docker_ok = True
        capture_versions(report)

        preflight_failed = False
        parity_failed = False
        verify_failed = False
        nbz_failed = False
        gap_operator_failed = False
        conv_rel_failed = False
        telegram_exp_failed = False
        solution_bundles_failed = False
        nbz_db_failed = False

        step = run_capture_fn(
            ["docker", "compose", "-f", str(POSTGRES_COMPOSE), "up", "-d"],
            cwd=str(POSTGRES_COMPOSE.parent),
            name="start_staging_postgres",
        )
        _record_step(state, step)
        if not step.ok:
            return _fail(
                state,
                "start_staging_postgres",
                step.stderr or step.error or "compose up failed",
                e2e=config.e2e_mode,
            ), state

        step = run_capture_fn([config.python, str(STAGING_PROOF)], name="staging_proof")
        _record_step(state, step)
        if not step.ok:
            print(step.stdout)
            print(step.stderr, file=sys.stderr)
            return _fail(
                state,
                "staging_proof",
                "run_local_staging_proof.py failed",
                e2e=config.e2e_mode,
            ), state

        ensure_cmd = [config.python, str(ENSURE_CORE_DB)]
        if config.parity_mode:
            ensure_cmd.append("--force-reapply")
        step = run_capture_fn(ensure_cmd, name="ensure_core_database")
        _record_step(state, step)
        if not step.ok:
            print(step.stdout)
            print(step.stderr, file=sys.stderr)
            return _fail(
                state,
                "ensure_core_database",
                "ensure_local_core_database.py failed",
                e2e=config.e2e_mode or config.parity_mode,
            ), state

        if config.parity_mode:
            step = run_capture_fn(ensure_cmd, name="ensure_core_database_reapply")
            _record_step(state, step)
            if not step.ok:
                return _fail(
                    state,
                    "ensure_core_database_reapply",
                    "second schema apply failed",
                    e2e=True,
                ), state

        compose_cmd = ["docker", "compose", "-f", str(CORE_COMPOSE), "up", "-d"]
        if not config.skip_build:
            compose_cmd.append("--build")
        step = run_capture_fn(compose_cmd, cwd=str(PLATFORM_API), name="start_core_api")
        _record_step(state, step)
        if not step.ok:
            return _fail(
                state,
                "start_core_api",
                step.stderr or step.error or "Core compose failed",
                e2e=config.e2e_mode,
            ), state

        health = wait_health_fn(config.health_timeout_sec)
        report.health = health
        if health.get("status") != "PASS":
            return _fail(
                state,
                "health_ready",
                health.get("error") or "health check failed",
                e2e=config.e2e_mode,
            ), state
        print("  Core ready")

        step = run_capture_fn(
            [config.python, str(HTTP_SMOKE), "--base-url", API_BASE],
            name="http_contract_smoke",
        )
        _record_step(state, step)
        if not step.ok:
            print(step.stdout)
            print(step.stderr, file=sys.stderr)
            return _fail(
                state,
                "http_contract_smoke",
                "local_http_contract_smoke failed",
                e2e=config.e2e_mode,
            ), state

        if config.e2e_mode:
            target_step = ensure_acceptance_target()
            if target_step is not None:
                _record_step(state, target_step)
                if not target_step.ok:
                    return _fail(
                        state,
                        "acceptance_target",
                        target_step.error or "target setup failed",
                        e2e=True,
                    ), state

            step = run_capture_fn(
                [
                    config.python,
                    str(ACCEPTANCE_RUNNER),
                    "--check-target",
                    "--target",
                    str(ACCEPTANCE_LOCAL_TARGET),
                ],
                name="acceptance_check_target",
            )
            _record_step(state, step)
            report.check_target = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout": step.stdout[-2000:],
                "stderr": step.stderr[-2000:],
            }
            if not step.ok:
                print(step.stdout)
                print(step.stderr, file=sys.stderr)
                return _fail(
                    state,
                    "acceptance_check_target",
                    "acceptance --check-target failed",
                    e2e=True,
                ), state

            step = run_capture_fn(
                [
                    config.python,
                    str(ACCEPTANCE_RUNNER),
                    "--run",
                    "--priority",
                    "P0",
                    "--corpus",
                    str(LOCAL_CORE_SMOKE_CORPUS),
                    "--target",
                    str(ACCEPTANCE_LOCAL_TARGET),
                ],
                name="acceptance_p0_run",
            )
            _record_step(state, step)
            combined = step.stdout + step.stderr
            report_path = None
            for line in combined.splitlines():
                if line.strip().startswith("Report:"):
                    report_path = line.split("Report:", 1)[1].strip()
            report.p0_acceptance = {
                "status": "PASS" if step.ok else "FAIL",
                "summary": _extract_acceptance_summary(combined),
                "report_path": report_path,
                "stdout_tail": step.stdout[-3000:],
                "stderr_tail": step.stderr[-3000:],
            }
            report.preflight_smoke = dict(report.p0_acceptance)
            if not step.ok:
                preflight_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)
                if not config.parity_mode:
                    return _fail(state, "acceptance_p0_run", "P0 acceptance run failed", e2e=True), state
                print("P0 preflight smoke failed — continuing to parity corpus")

        if config.parity_mode:
            step = run_capture_fn(
                [
                    config.python,
                    str(PARITY_RUNNER),
                    "--target",
                    str(ACCEPTANCE_LOCAL_TARGET),
                    "--case-timeout",
                    "5",
                    "--run-timeout",
                    "300",
                ],
                name="core_local_parity_run",
            )
            _record_step(state, step)
            combined = step.stdout + step.stderr
            parsed = _parse_parity_summary(combined)
            report.parity_run = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
                **parsed,
            }
            if not step.ok:
                parity_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)
                if not config.e2e_mode:
                    return _fail(state, "core_local_parity_run", "parity corpus run failed", e2e=True), state

        if config.e2e_mode:
            verify_result = run_verify(API_BASE, include_p0=False, python=config.python)
            report.verify_e2e = verify_result
            if verify_result["status"] != "PASS":
                verify_failed = True
                failed = [c["name"] for c in verify_result["checks"] if c["status"] == "FAIL"]
                print(f"verify_local_core_e2e FAIL: {', '.join(failed)}", file=sys.stderr)

        if config.no_blind_zone_mode:
            step = run_capture_fn(
                [config.python, str(NO_BLIND_ZONE_RUNNER), "--live"],
                name="no_blind_zone_run",
            )
            _record_step(state, step)
            combined = step.stdout + step.stderr
            parsed = _parse_parity_summary(combined)
            report.no_blind_zone_run = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
                **parsed,
            }
            if not step.ok:
                nbz_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

            step = run_capture_fn(
                [config.python, str(NO_BLIND_ZONE_DB_PROOF), "--base-url", API_BASE],
                name="no_blind_zone_db_proof",
            )
            _record_step(state, step)
            report.no_blind_zone_db_proof = {
                "status": "PASS" if step.ok else "FAIL",
                "summary": (step.stdout or step.stderr).strip()[-1000:],
            }
            if not step.ok:
                nbz_db_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

        if config.gap_operator_mode:
            step = run_capture_fn(
                [config.python, str(GAP_OPERATOR_RUNNER)],
                name="gap_operator_run",
            )
            _record_step(state, step)
            report.gap_operator_run = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
            }
            if not step.ok:
                gap_operator_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

        if config.conversation_reliability_mode:
            step = run_capture_fn(
                [config.python, str(CONVERSATION_RELIABILITY_RUNNER), "--live"],
                name="conversation_reliability_run",
            )
            _record_step(state, step)
            combined = step.stdout + step.stderr
            parsed = _parse_parity_summary(combined)
            report.conversation_reliability_run = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
                **parsed,
            }
            if not step.ok:
                conv_rel_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

        if config.telegram_experience_mode:
            step = run_capture_fn(
                [config.python, str(TELEGRAM_EXPERIENCE_RUNNER), "--live"],
                name="telegram_experience_run",
            )
            _record_step(state, step)
            combined = step.stdout + step.stderr
            parsed = _parse_parity_summary(combined)
            report.telegram_experience_run = {
                "status": "PASS" if step.ok else "FAIL",
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
                **parsed,
            }
            if not step.ok:
                telegram_exp_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

        if config.solution_bundles_mode:
            step = run_capture_fn(
                [config.python, str(SOLUTION_BUNDLES_RUNNER), "--live", "--base-url", API_BASE],
                name="solution_bundles_run",
            )
            _record_step(state, step)
            summary = next(
                (line.strip() for line in (step.stdout + step.stderr).splitlines() if line.startswith("Solution bundles:")),
                None,
            )
            report.solution_bundle_run = {
                "status": "PASS" if step.ok else "FAIL",
                "summary": summary,
                "stdout_tail": step.stdout[-4000:],
                "stderr_tail": step.stderr[-2000:],
            }
            if not step.ok:
                solution_bundles_failed = True
                print(step.stdout)
                print(step.stderr, file=sys.stderr)

        if (
            preflight_failed
            or parity_failed
            or verify_failed
            or nbz_failed
            or nbz_db_failed
            or gap_operator_failed
            or conv_rel_failed
            or telegram_exp_failed
            or solution_bundles_failed
        ):
            parts = []
            if preflight_failed:
                parts.append("preflight_smoke")
            if parity_failed:
                parts.append("parity_corpus")
            if verify_failed:
                parts.append("verify_e2e")
            if nbz_failed:
                parts.append("no_blind_zone")
            if gap_operator_failed:
                parts.append("gap_operator")
            if conv_rel_failed:
                parts.append("conversation_reliability")
            if telegram_exp_failed:
                parts.append("telegram_experience")
            if solution_bundles_failed:
                parts.append("solution_bundles")
            if nbz_db_failed:
                parts.append("no_blind_zone_db_proof")
            report.status = "FAIL"
            if preflight_failed:
                report.failure_stage = "acceptance"
            elif parity_failed:
                report.failure_stage = "parity"
            elif nbz_failed:
                report.failure_stage = "no_blind_zone"
            elif nbz_db_failed:
                report.failure_stage = "no_blind_zone_db_proof"
            elif gap_operator_failed:
                report.failure_stage = "gap_operator"
            elif conv_rel_failed:
                report.failure_stage = "conversation_reliability"
            elif telegram_exp_failed:
                report.failure_stage = "telegram_experience"
            elif solution_bundles_failed:
                report.failure_stage = "solution_bundles"
            else:
                report.failure_stage = "verify_e2e"
            report.failure_message = f"failed: {', '.join(parts)}"
            if config.e2e_mode or config.parity_mode:
                _finalize_report(state)
            return 1, state

        report.status = "PASS"
        if config.e2e_mode or config.parity_mode:
            _finalize_report(state)
        print("\n=== LOCAL CORE RUNTIME LAB: PASS ===")
        if config.e2e_mode:
            print(f"E2E report: {report.report_paths.get('latest_md', E2E_REPORTS_DIR / 'latest_run.md')}")
        return 0, state

    except KeyboardInterrupt:
        report.status = "FAIL"
        report.failure_stage = "interrupted"
        report.failure_message = "KeyboardInterrupt"
        if config.e2e_mode or config.parity_mode:
            _finalize_report(state)
        print("\nInterrupted — stopping Core container", file=sys.stderr)
        return 1, state

    except RuntimeError as exc:
        report.status = "NOT_RUN" if not state.docker_ok else "FAIL"
        report.failure_stage = "precheck"
        report.failure_message = str(exc)
        if config.e2e_mode or config.parity_mode:
            _finalize_report(state)
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1, state
    finally:
        if state.docker_ok and not config.leave_core_up:
            stop = stop_core_only()
            _record_step(state, stop)
            report.cleanup_status = "PASS" if stop.ok else "FAIL"
            if not stop.ok:
                print(
                    f"WARN: Core container cleanup failed: {stop.stderr or stop.error}",
                    file=sys.stderr,
                )
        elif state.docker_ok and config.leave_core_up:
            report.cleanup_status = "SKIPPED"
        if (config.e2e_mode or config.parity_mode) and report.finished_at:
            write_report(report, E2E_REPORTS_DIR)
            _print_final_summary(report)


def _extract_acceptance_summary(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().startswith("Total "):
            return line.strip()
    return None
