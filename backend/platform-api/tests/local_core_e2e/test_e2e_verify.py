"""Unit tests for non-mock E2E verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from local_core_lab.e2e_verify import (
    assert_local_base,
    check_advisor_valid_json,
    check_health_live,
    check_health_ready,
    check_missing_required_field_4xx,
    check_openapi_advisor_path,
    check_tenant_isolation,
    run_verify,
    verify_core_down,
)

ROOT = Path(__file__).resolve().parents[4]
VERIFY_SCRIPT = ROOT / "backend" / "platform-api" / "scripts" / "verify_local_core_e2e.py"


def test_assert_local_base_rejects_external():
    with pytest.raises(ValueError, match="Refusing"):
        assert_local_base("https://wwc.best")


def test_assert_local_base_accepts_127():
    assert_local_base("http://127.0.0.1:8080")


def test_check_health_live_pass():
    with patch("local_core_lab.e2e_verify._http", return_value=(200, "ok", {})):
        assert check_health_live("http://127.0.0.1:8080").status == "PASS"


def test_check_health_live_fail_on_connection():
    with patch("local_core_lab.e2e_verify._http", side_effect=ConnectionError("refused")):
        assert check_health_live("http://127.0.0.1:8080").status == "FAIL"


def test_check_health_ready_fail_non_200():
    with patch("local_core_lab.e2e_verify._http", return_value=(503, "not ready", {})):
        assert check_health_ready("http://127.0.0.1:8080").status == "FAIL"


def test_check_openapi_advisor_path_pass():
    body = json.dumps({"paths": {"/v1/advisor/query": {"post": {}}}})
    with patch("local_core_lab.e2e_verify._http", return_value=(200, body, {})):
        assert check_openapi_advisor_path("http://127.0.0.1:8080").status == "PASS"


def test_check_openapi_advisor_path_fail_missing():
    body = json.dumps({"paths": {"/health/live": {"get": {}}}})
    with patch("local_core_lab.e2e_verify._http", return_value=(200, body, {})):
        assert check_openapi_advisor_path("http://127.0.0.1:8080").status == "FAIL"


def test_check_missing_field_4xx_pass():
    with patch("local_core_lab.e2e_verify._http", return_value=(422, '{"detail":[]}', {"Content-Type": "application/json"})):
        assert check_missing_required_field_4xx("http://127.0.0.1:8080").status == "PASS"


def test_check_missing_field_4xx_fail_html():
    with patch(
        "local_core_lab.e2e_verify._http",
        return_value=(400, "<html>", {"Content-Type": "text/html"}),
    ):
        assert check_missing_required_field_4xx("http://127.0.0.1:8080").status == "FAIL"


def test_check_tenant_isolation_pass():
    with patch(
        "local_core_lab.e2e_verify._http",
        side_effect=[(200, "{}", {}), (404, "{}", {})],
    ):
        assert check_tenant_isolation("http://127.0.0.1:8080").status == "PASS"


def test_check_tenant_isolation_fail_leak():
    with patch(
        "local_core_lab.e2e_verify._http",
        side_effect=[(200, "{}", {}), (200, "{}", {})],
    ):
        assert check_tenant_isolation("http://127.0.0.1:8080").status == "FAIL"


def test_check_advisor_valid_json_pass():
    body = json.dumps({"answer_text": "hi", "answer_mode": "structured_card"})
    with patch(
        "local_core_lab.e2e_verify._http",
        return_value=(200, body, {"Content-Type": "application/json"}),
    ):
        assert check_advisor_valid_json("http://127.0.0.1:8080").status == "PASS"


def test_check_advisor_valid_json_fail_traceback():
    with patch(
        "local_core_lab.e2e_verify._http",
        return_value=(500, "Traceback...", {"Content-Type": "text/plain"}),
    ):
        assert check_advisor_valid_json("http://127.0.0.1:8080").status == "FAIL"


def test_run_verify_aggregates_failures():
    with patch("local_core_lab.e2e_verify.check_health_live") as live:
        live.return_value = MagicMock(name="health_live", status="FAIL", message="down")
        with patch("local_core_lab.e2e_verify.ALL_VERIFY_CHECKS", (live,)):
            result = run_verify("http://127.0.0.1:8080", include_p0=False)
    assert result["status"] == "FAIL"


def test_verify_core_down_true_on_refused():
    with patch("local_core_lab.e2e_verify._http", side_effect=ConnectionError("refused")):
        assert verify_core_down("http://127.0.0.1:8080") is True


def test_verify_core_down_false_when_up():
    with patch("local_core_lab.e2e_verify._http", return_value=(200, "ok", {})):
        assert verify_core_down("http://127.0.0.1:8080") is False


def test_verify_script_rejects_external_url():
    proc = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), "--base-url", "https://wwc.best"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Refusing" in proc.stderr + proc.stdout


def test_verify_script_has_expect_down_flag():
    text = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "--expect-down" in text


def test_verify_lib_uses_urllib_not_fake_transport():
    verify_lib = (ROOT / "backend" / "platform-api" / "scripts" / "local_core_lab" / "e2e_verify.py").read_text(
        encoding="utf-8"
    )
    assert "import urllib.request" in verify_lib
    assert "FakeTransport(" not in verify_lib
