"""Static guards for local Core E2E lab scripts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PLATFORM = ROOT / "backend" / "platform-api"
DOCTOR = PLATFORM / "scripts" / "doctor_local_core_lab.py"
VERIFY = PLATFORM / "scripts" / "verify_local_core_e2e.py"
LAB = PLATFORM / "scripts" / "run_local_core_lab.py"
PREREQ = PLATFORM / "docs" / "LOCAL_CORE_LAB_PREREQUISITES.md"


def test_doctor_script_exists():
    assert DOCTOR.is_file()


def test_doctor_is_read_only():
    text = DOCTOR.read_text(encoding="utf-8")
    assert "compose" not in text or "run_all_checks" in text
    doctor_lib = (PLATFORM / "scripts" / "local_core_lab" / "doctor.py").read_text(encoding="utf-8")
    assert "up -d" not in doctor_lib
    assert "download" not in doctor_lib.lower()


def test_doctor_only_pass_warn_fail():
    doctor_lib = (PLATFORM / "scripts" / "local_core_lab" / "doctor.py").read_text(encoding="utf-8")
    assert 'Literal["PASS", "WARN", "FAIL"]' in doctor_lib


def test_prerequisites_doc_exists():
    assert PREREQ.is_file()
    text = PREREQ.read_text(encoding="utf-8")
    assert "55432" in text
    assert "8080" in text
    assert "doctor_local_core_lab.py" in text


def test_verify_no_fake_transport():
    verify_lib = (PLATFORM / "scripts" / "local_core_lab" / "e2e_verify.py").read_text(encoding="utf-8")
    assert "import urllib.request" in verify_lib
    assert "FakeTransport(" not in verify_lib
    assert "urllib.request.Request" in verify_lib


def test_verify_refuses_non_local():
    proc = subprocess.run(
        [sys.executable, str(VERIFY), "--base-url", "http://example.com:8080"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


def test_lab_e2e_command_documented_in_prereq():
    text = PREREQ.read_text(encoding="utf-8")
    assert "run_local_core_lab.py --e2e" in text.replace("\\", "")


def test_doctor_checks_nobypassrls_statically():
    doctor_lib = (PLATFORM / "scripts" / "local_core_lab" / "doctor.py").read_text(encoding="utf-8")
    assert "check_api_role_nobypassrls" in doctor_lib
    assert "NOBYPASSRLS" in doctor_lib


def test_orchestrator_uses_docker_exec_via_ensure_script():
    text = (ROOT / "postgres" / "scripts" / "ensure_local_core_database.py").read_text(encoding="utf-8")
    assert "docker" in text
    assert "exec" in text
    assert "psql" in text
    assert shutil_which_host_psql_not_required(text)


def shutil_which_host_psql_not_required(ensure_text: str) -> bool:
    return '"docker"' in ensure_text and '"exec"' in ensure_text and '"psql"' in ensure_text


def test_e2e_report_status_values():
    report_lib = (PLATFORM / "scripts" / "local_core_lab" / "e2e_report.py").read_text(encoding="utf-8")
    assert "NOT_RUN" in report_lib
    assert "PASS" in report_lib
    assert "FAIL" in report_lib
