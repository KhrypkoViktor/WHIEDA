"""Unit tests for local Core E2E orchestrator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from local_core_lab.orchestrator import (
    OrchestratorConfig,
    ensure_acceptance_target,
    require_docker,
    run_lab,
    wait_api_health,
    _extract_acceptance_summary,
)
from local_core_lab.subprocess_util import StepResult

ROOT = Path(__file__).resolve().parents[4]
LAB_SCRIPT = ROOT / "backend" / "platform-api" / "scripts" / "run_local_core_lab.py"


def _ok_step(name: str, stdout: str = "OK\n") -> StepResult:
    return StepResult(name=name, command=["test"], returncode=0, stdout=stdout)


def test_require_docker_raises_without_cli():
    with patch("local_core_lab.orchestrator.shutil.which", return_value=None):
        try:
            require_docker()
            raised = False
        except RuntimeError:
            raised = True
        assert raised


def test_wait_api_health_pass(monkeypatch):
    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"ok"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    result = wait_api_health(5)
    assert result["status"] == "PASS"


def test_extract_acceptance_summary():
    text = "Run id: x\nTotal 3 | pass 2 fail 1 skip 0 unasserted 0\n"
    assert _extract_acceptance_summary(text).startswith("Total")


def test_ensure_acceptance_target_creates_from_example(tmp_path, monkeypatch):
    example = tmp_path / "acceptance_target.example.json"
    example.write_text('{"base_url":"http://127.0.0.1:8080"}', encoding="utf-8")
    local = tmp_path / "acceptance_target.local.json"
    monkeypatch.setattr("local_core_lab.orchestrator.ACCEPTANCE_EXAMPLE_TARGET", example)
    monkeypatch.setattr("local_core_lab.orchestrator.ACCEPTANCE_LOCAL_TARGET", local)
    step = ensure_acceptance_target()
    assert step is not None
    assert step.ok
    assert local.is_file()


def test_run_lab_not_run_without_docker(tmp_path, monkeypatch):
    monkeypatch.setattr("local_core_lab.orchestrator.shutil.which", lambda name: None)
    monkeypatch.setattr("local_core_lab.orchestrator.E2E_REPORTS_DIR", tmp_path)
    code, state = run_lab(OrchestratorConfig(e2e_mode=True))
    assert code == 1
    assert state.report.status == "NOT_RUN"


def test_run_lab_e2e_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr("local_core_lab.orchestrator.shutil.which", lambda name: "/docker")
    monkeypatch.setattr("local_core_lab.orchestrator.E2E_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        "local_core_lab.orchestrator.capture_versions",
        lambda report: setattr(report, "docker_version", "27"),
    )
    monkeypatch.setattr(
        "local_core_lab.orchestrator.ensure_acceptance_target",
        lambda: None,
    )
    monkeypatch.setattr(
        "local_core_lab.orchestrator.run_verify",
        lambda *a, **k: {"status": "PASS", "checks": []},
    )

    def fake_capture(cmd, **kwargs):
        name = kwargs.get("name", "step")
        if name == "acceptance_p0_run":
            return _ok_step(name, "Total 1 | pass 1 fail 0\nReport: qa/acceptance/reports/x.md\n")
        return _ok_step(name)

    monkeypatch.setattr("local_core_lab.orchestrator.docker_logs", lambda c, tail=200: f"log:{c}")
    monkeypatch.setattr("local_core_lab.orchestrator.stop_core_only", lambda: _ok_step("stop"))
    code, state = run_lab(
        OrchestratorConfig(e2e_mode=True),
        run_capture_fn=fake_capture,
        wait_health_fn=lambda t: {"status": "PASS", "http_status": 200},
    )
    assert code == 0
    assert state.report.status == "PASS"
    assert (tmp_path / "latest_run.json").is_file()


def test_run_lab_fails_on_health_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("local_core_lab.orchestrator.shutil.which", lambda name: "/docker")
    monkeypatch.setattr("local_core_lab.orchestrator.E2E_REPORTS_DIR", tmp_path)
    monkeypatch.setattr("local_core_lab.orchestrator.capture_versions", lambda r: None)
    monkeypatch.setattr("local_core_lab.orchestrator.docker_logs", lambda c, tail=200: "")
    monkeypatch.setattr("local_core_lab.orchestrator.stop_core_only", lambda: _ok_step("stop"))
    code, state = run_lab(
        OrchestratorConfig(e2e_mode=True),
        run_capture_fn=lambda *a, **k: _ok_step("x"),
        wait_health_fn=lambda t: {"status": "FAIL", "error": "timeout"},
    )
    assert code == 1
    assert state.report.failure_stage == "health_ready"


def test_run_lab_non_e2e_skips_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr("local_core_lab.orchestrator.shutil.which", lambda name: "/docker")
    monkeypatch.setattr("local_core_lab.orchestrator.capture_versions", lambda r: None)
    monkeypatch.setattr("local_core_lab.orchestrator.stop_core_only", lambda: _ok_step("stop"))
    verify_called = {"v": False}

    def _verify(*a, **k):
        verify_called["v"] = True
        return {"status": "PASS", "checks": []}

    monkeypatch.setattr("local_core_lab.orchestrator.run_verify", _verify)
    code, _ = run_lab(
        OrchestratorConfig(e2e_mode=False),
        run_capture_fn=lambda *a, **k: _ok_step("x"),
        wait_health_fn=lambda t: {"status": "PASS"},
    )
    assert code == 0
    assert verify_called["v"] is False


def test_lab_script_has_e2e_flag():
    text = LAB_SCRIPT.read_text(encoding="utf-8")
    assert "--e2e" in text
    assert "OrchestratorConfig" in text


def test_lab_script_help():
    proc = subprocess.run([sys.executable, str(LAB_SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--e2e" in proc.stdout


def test_orchestrator_does_not_remove_postgres_volume():
    text = (ROOT / "backend" / "platform-api" / "scripts" / "local_core_lab" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    lower = text.lower()
    assert "down --volumes" not in lower
    assert "volume rm" not in lower


def test_orchestrator_stops_core_in_finally():
    text = (ROOT / "backend" / "platform-api" / "scripts" / "local_core_lab" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "stop_core_only" in text
    assert "leave_core_up" in text
