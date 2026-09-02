"""P0.3.3/P0.3.5: orchestrator must stop on precheck or post-deploy smoke failure."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
ORCH_PATH = ROOT / "n8n" / "current" / "run_staging_cabinet_p0_3_5_2026-08-10.py"


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location("run_staging_cabinet_orchestrator", ORCH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("precheck_code", [1, 2, 3])
def test_apply_stops_on_any_precheck_failure(precheck_code: int, capsys):
    orch = _load_orchestrator()
    run_mock = patch.object(orch, "run")

    with patch.object(orch.subprocess, "run") as subprocess_run, run_mock as run:
        subprocess_run.return_value = subprocess.CompletedProcess(args=[], returncode=precheck_code)
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply"]):
            exit_code = orch.main()

    assert exit_code == precheck_code
    run.assert_not_called()
    captured = capsys.readouterr()
    assert "STOP: precheck failed" in captured.out


def test_apply_runs_steps_when_precheck_and_smoke_pass(capsys):
    orch = _load_orchestrator()
    step_calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(script: str, *args: str) -> None:
        step_calls.append((script, args))

    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run", side_effect=fake_run):
        subprocess_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply"]):
            exit_code = orch.main()

    assert exit_code == 0
    assert step_calls[0] == ("apply_staging_cabinet_sql_2026-08-10.py", ())
    assert step_calls[-1][0] == "setup_staging_cabinet_telegram_webhook_2026-08-09.py"
    assert len(step_calls) == 6


def test_dry_run_does_not_apply_even_when_precheck_fails():
    orch = _load_orchestrator()

    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run") as run:
        subprocess_run.return_value = subprocess.CompletedProcess(args=[], returncode=3)
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py"]):
            exit_code = orch.main()

    assert exit_code == 3
    run.assert_not_called()
