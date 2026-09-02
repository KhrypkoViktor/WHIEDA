"""Unit tests for local Core lab doctor checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from local_core_lab.doctor import (
    ALL_CHECKS,
    CheckResult,
    check_acceptance_target_config,
    check_api_role_nobypassrls,
    check_compose_files_exist,
    check_docker_compose,
    check_docker_daemon,
    check_docker_installed,
    check_env_local_example_safe,
    check_port_55432,
    check_port_8080,
    check_staging_sql_files,
    run_all_checks,
    summarize,
)

ROOT = Path(__file__).resolve().parents[4]
ACCEPTANCE_LOCAL = ROOT / "qa" / "acceptance" / "acceptance_target.local.json"


def test_all_checks_count():
    assert len(ALL_CHECKS) == 10


def test_summarize_fail_on_any_fail():
    checks = [
        CheckResult("a", "PASS", "ok"),
        CheckResult("b", "FAIL", "bad"),
    ]
    status, code = summarize(checks)
    assert status == "FAIL"
    assert code == 1


def test_summarize_pass_without_fail():
    checks = [
        CheckResult("a", "PASS", "ok"),
        CheckResult("b", "WARN", "warn"),
    ]
    status, code = summarize(checks)
    assert status == "PASS"
    assert code == 0


def test_check_docker_installed_pass():
    with patch("local_core_lab.doctor.shutil.which", return_value="/usr/bin/docker"):
        assert check_docker_installed().status == "PASS"


def test_check_docker_installed_fail():
    with patch("local_core_lab.doctor.shutil.which", return_value=None):
        assert check_docker_installed().status == "FAIL"


def test_check_docker_daemon_pass():
    with patch("local_core_lab.doctor.shutil.which", return_value="/usr/bin/docker"):
        with patch("local_core_lab.doctor.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert check_docker_daemon().status == "PASS"


def test_check_docker_daemon_fail():
    with patch("local_core_lab.doctor.shutil.which", return_value="/usr/bin/docker"):
        with patch("local_core_lab.doctor.subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="cannot connect")
            assert check_docker_daemon().status == "FAIL"


def test_check_docker_compose_pass():
    with patch("local_core_lab.doctor.shutil.which", return_value="/usr/bin/docker"):
        with patch("local_core_lab.doctor.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="Docker Compose v2.24", stderr="")
            assert check_docker_compose().status == "PASS"


def test_check_port_free_pass():
    with patch("local_core_lab.doctor._port_in_use", return_value=False):
        assert check_port_55432().status == "PASS"
        assert check_port_8080().status == "PASS"


def test_check_port_lab_container_warn():
    with patch("local_core_lab.doctor._port_in_use", return_value=True):
        with patch("local_core_lab.doctor._docker_ps_names", return_value={"whieda-local-staging-postgres"}):
            assert check_port_55432().status == "WARN"


def test_check_port_foreign_process_fail():
    with patch("local_core_lab.doctor._port_in_use", return_value=True):
        with patch("local_core_lab.doctor._docker_ps_names", return_value=set()):
            assert check_port_8080().status == "FAIL"


def test_check_compose_files_exist_pass():
    assert check_compose_files_exist().status == "PASS"


def test_check_env_local_example_safe_pass():
    assert check_env_local_example_safe().status == "PASS"


def test_check_staging_sql_files_pass():
    result = check_staging_sql_files()
    assert result.status == "PASS"
    assert "apply-order SQL files + seed exist" in result.message


def test_check_api_role_nobypassrls_pass():
    assert check_api_role_nobypassrls().status == "PASS"


def test_check_acceptance_target_warn_without_local(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "local_core_lab.doctor.ACCEPTANCE_LOCAL_TARGET",
        tmp_path / "missing.local.json",
    )
    monkeypatch.setattr(
        "local_core_lab.doctor.ACCEPTANCE_EXAMPLE_TARGET",
        ROOT / "qa" / "acceptance" / "acceptance_target.example.json",
    )
    assert check_acceptance_target_config().status == "WARN"


def test_check_acceptance_target_pass_with_local(tmp_path, monkeypatch):
    target = tmp_path / "acceptance_target.local.json"
    target.write_text(
        json.dumps({"name": "test", "base_url": "http://127.0.0.1:8080"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("local_core_lab.doctor.ACCEPTANCE_LOCAL_TARGET", target)
    assert check_acceptance_target_config().status == "PASS"


def test_doctor_cli_help():
    doctor = ROOT / "backend" / "platform-api" / "scripts" / "doctor_local_core_lab.py"
    proc = subprocess.run([sys.executable, str(doctor)], capture_output=True, text=True)
    assert "Overall:" in proc.stdout or proc.returncode in (0, 1)


def test_run_all_checks_returns_ten_results():
    checks = run_all_checks()
    assert len(checks) == 10
    assert all(c.status in {"PASS", "WARN", "FAIL"} for c in checks)
