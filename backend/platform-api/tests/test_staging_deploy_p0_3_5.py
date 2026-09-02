"""P0.3.5 / P0.3.5.1: staging deploy hardening tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

ROOT = Path(__file__).resolve().parents[3]
N8N = ROOT / "n8n" / "current"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(N8N))
    try:
        spec.loader.exec_module(module)
    finally:
        if str(N8N) in sys.path:
            sys.path.remove(str(N8N))
    return module


def test_resolve_npm_command_windows():
    lib = _load_module("staging_deploy_lib", N8N / "staging_deploy_lib.py")
    with patch.object(lib.sys, "platform", "win32"):
        assert lib.resolve_npm_command() == "npm.cmd"
    with patch.object(lib.sys, "platform", "linux"):
        assert lib.resolve_npm_command() == "npm"


def test_precheck_source_excludes_post_deploy_checks():
    source = (N8N / "precheck_staging_cabinet_p0_3_5_2026-08-10.py").read_text(encoding="utf-8")
    assert "check_http_smoke" not in source
    assert "check_core_port" not in source
    assert "Does NOT require" in source


def test_post_deploy_smoke_includes_health_and_401():
    source = (ROOT / "backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py").read_text(
        encoding="utf-8"
    )
    assert "8081/health/live" in source
    assert "18081/health/live" in source
    assert "/api/v1/admin/me" in source
    assert "404=proxy/API not connected" in source


def test_firewall_script_deprecated():
    result = subprocess.run(
        [sys.executable, str(N8N / "restrict_staging_api_firewall_2026-08-09.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "deprecated"


def test_orchestrator_p0_3_5_stops_on_precheck_failure():
    orch = _load_module("run_p035", N8N / "run_staging_cabinet_p0_3_5_2026-08-10.py")
    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run") as run:
        subprocess_run.return_value = subprocess.CompletedProcess(args=[], returncode=3)
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply"]):
            exit_code = orch.main()
    assert exit_code == 3
    run.assert_not_called()


def test_orchestrator_p0_3_5_no_webhook_when_post_smoke_fails():
    orch = _load_module("run_p035b", N8N / "run_staging_cabinet_p0_3_5_2026-08-10.py")

    def fake_run(script: str, *args: str) -> None:
        return None

    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run", side_effect=fake_run):
        subprocess_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=1),
        ]
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply"]):
            exit_code = orch.main()
    assert exit_code == 1


def test_nginx_conf_uses_local_tunnel_upstream():
    conf = (ROOT / "backend/deploy/staging/admin-staging.wwc.best.nginx.conf").read_text(encoding="utf-8")
    assert "127.0.0.1:18081" in conf
    assert "185.252.232.93" not in conf


def test_ssh_host_key_config_has_core_and_site():
    payload = json.loads((N8N / "staging_ssh_host_keys.json").read_text(encoding="utf-8"))
    assert "185.252.232.93" in payload
    assert "173.249.45.83" in payload
    for fp in payload.values():
        assert fp.startswith("SHA256:")


def test_strict_fingerprint_accepts_matching_key():
    lib = _load_module("staging_deploy_lib_fp_ok", N8N / "staging_deploy_lib.py")
    key = paramiko.RSAKey.generate(2048)
    expected = lib.key_sha256_fingerprint(key)
    policy = lib.StrictFingerprintPolicy(expected)
    policy.missing_host_key(MagicMock(), "185.252.232.93", key)


def test_strict_fingerprint_rejects_mismatch_before_connect():
    lib = _load_module("staging_deploy_lib_fp_bad", N8N / "staging_deploy_lib.py")
    key = paramiko.RSAKey.generate(2048)
    policy = lib.StrictFingerprintPolicy("SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    with pytest.raises(lib.SshHostKeyMismatchError):
        policy.missing_host_key(MagicMock(), "185.252.232.93", key)


def test_connect_ssh_uses_strict_policy_not_auto_add():
    lib = _load_module("staging_deploy_lib_connect", N8N / "staging_deploy_lib.py")
    source = (N8N / "staging_deploy_lib.py").read_text(encoding="utf-8")
    assert "AutoAddPolicy" not in source
    assert "StrictFingerprintPolicy" in source

    with patch.object(lib.paramiko, "SSHClient") as client_cls:
        client = client_cls.return_value
        with patch.object(lib, "expected_host_fingerprint", return_value="SHA256:expected"):
            lib.connect_ssh({"host": "185.252.232.93", "user": "root", "password": "x"})
        client.set_missing_host_key_policy.assert_called_once()
        policy = client.set_missing_host_key_policy.call_args[0][0]
        assert isinstance(policy, lib.StrictFingerprintPolicy)


def test_upload_staging_tarball_success_order():
    transport = _load_module("staging_openssh_ok", N8N / "staging_openssh_transport.py")
    gate = _load_module("staging_deploy_gate_lib_ok", N8N / "staging_deploy_gate_lib.py")
    cfg = {
        "host": "173.249.45.83",
        "user": "wwcdeploy",
        "key_path": "C:/keys/site_deploy",
        "known_hosts_path": "C:/keys/site_known_hosts",
    }
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        stdout = "abc123\n" if any("sha256sum" in part for part in cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    with patch.object(transport, "resolve_openssh_binary", side_effect=lambda name: f"{name}.exe"):
        result = transport.upload_staging_tarball_openssh_atomic(
            local_tarball=Path("local.tgz"),
            remote_dir=gate.STAGING_STATIC_DIR,
            digest="abc123",
            cfg=cfg,
            run=fake_run,
        )

    assert result["status"] == "ok"
    joined = [" ".join(cmd) for cmd in calls]
    assert any("mkdir -p" in cmd for cmd in joined)
    assert any("scp.exe" in cmd for cmd in joined)
    assert any("sha256sum" in cmd for cmd in joined)
    assert all("awk" not in cmd for cmd in joined)
    assert joined.index(next(cmd for cmd in joined if "sha256sum" in cmd)) < joined.index(
        next(cmd for cmd in joined if "tar -xzf" in cmd)
    )
    assert joined[-1].startswith("ssh.exe")
    assert "rm -f" in joined[-1]
    assert "cabinet-staging." in joined[1]


def test_upload_staging_tarball_mismatch_does_not_extract():
    transport = _load_module("staging_openssh_bad", N8N / "staging_openssh_transport.py")
    gate = _load_module("staging_deploy_gate_lib_bad", N8N / "staging_deploy_gate_lib.py")
    cfg = {
        "host": "173.249.45.83",
        "user": "wwcdeploy",
        "key_path": "C:/keys/site_deploy",
        "known_hosts_path": "C:/keys/site_known_hosts",
    }
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        stdout = "wrongdigest\n" if any("sha256sum" in part for part in cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    with patch.object(transport, "resolve_openssh_binary", side_effect=lambda name: f"{name}.exe"):
        with pytest.raises(RuntimeError, match="integrity mismatch"):
            transport.upload_staging_tarball_openssh_atomic(
                local_tarball=Path("local.tgz"),
                remote_dir=gate.STAGING_STATIC_DIR,
                digest="abc123",
                cfg=cfg,
                run=fake_run,
            )

    joined = " ".join(" ".join(cmd) for cmd in calls)
    assert "tar -xzf" not in joined
    assert "rm -f" in joined


def test_upload_staging_tarball_timeout_does_not_extract():
    transport = _load_module("staging_openssh_timeout", N8N / "staging_openssh_transport.py")
    gate = _load_module("staging_deploy_gate_lib_timeout", N8N / "staging_deploy_gate_lib.py")
    cfg = {
        "host": "173.249.45.83",
        "user": "wwcdeploy",
        "key_path": "C:/keys/site_deploy",
        "known_hosts_path": "C:/keys/site_known_hosts",
    }
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if any(part.endswith("scp.exe") or part == "scp.exe" for part in cmd):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(transport, "resolve_openssh_binary", side_effect=lambda name: f"{name}.exe"):
        with pytest.raises(transport.UploadTimeoutError):
            transport.upload_staging_tarball_openssh_atomic(
                local_tarball=Path("local.tgz"),
                remote_dir=gate.STAGING_STATIC_DIR,
                digest="abc123",
                cfg=cfg,
                run=fake_run,
            )

    joined = " ".join(" ".join(cmd) for cmd in calls)
    assert "tar -xzf" not in joined
    assert "rm -f" in joined


def test_openssh_commands_include_strict_key_and_known_hosts():
    transport = _load_module("staging_openssh_args", N8N / "staging_openssh_transport.py")
    cfg = {
        "host": "173.249.45.83",
        "user": "deploy",
        "key_path": "D:/secrets/site_deploy_ed25519",
        "known_hosts_path": "D:/secrets/site_known_hosts",
    }
    with patch.object(transport, "resolve_openssh_binary", side_effect=["scp.exe", "ssh.exe"]):
        scp_cmd = transport.build_scp_command(cfg, Path("local.tgz"), "deploy@173.249.45.83:/tmp/x.tgz")
        ssh_cmd = transport.build_ssh_command(cfg, "echo ok")
    assert "-O" in scp_cmd
    assert scp_cmd.index("-O") == 1
    assert "BatchMode=yes" in scp_cmd
    assert "StrictHostKeyChecking=yes" in scp_cmd
    assert "UserKnownHostsFile=D:/secrets/site_known_hosts" in scp_cmd
    assert scp_cmd[scp_cmd.index("-i") + 1] == "D:/secrets/site_deploy_ed25519"
    assert "BatchMode=yes" in ssh_cmd
    assert "StrictHostKeyChecking=no" not in ssh_cmd


def test_missing_site_key_or_known_hosts_exits_before_scp():
    transport = _load_module("staging_openssh_cfg", N8N / "staging_openssh_transport.py")
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(transport.SiteSshKeyRequiredError) as exc:
            transport.load_site_openssh_config()
    assert exc.value.code == "site_ssh_key_required"


def test_deploy_site_script_uses_openssh_not_paramiko_sftp():
    source = (N8N / "deploy_cabinet_staging_site_2026-08-09.py").read_text(encoding="utf-8")
    assert "staging_openssh_transport" in source
    assert "upload_staging_tarball_openssh_atomic" in source
    assert "open_sftp" not in source
    assert "upload_bytes_with_timeout" not in source
    assert "connect_ssh" not in source
    assert "SiteSshKeyRequiredError" in source


def test_staging_deploy_lib_has_no_sftp_upload_helpers():
    source = (N8N / "staging_deploy_lib.py").read_text(encoding="utf-8")
    assert "open_sftp" not in source
    assert "upload_bytes_with_timeout" not in source
    assert "upload_staging_tarball_atomic" not in source


def test_orchestrator_apply_without_bootstrap_base():
    orch = _load_module("run_p035_sql", N8N / "run_staging_cabinet_p0_3_5_2026-08-10.py")
    step_calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(script: str, *args: str) -> None:
        step_calls.append((script, args))

    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run", side_effect=fake_run):
        subprocess_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply"]):
            orch.main()

    assert step_calls[0] == ("apply_staging_cabinet_sql_2026-08-10.py", ())
    assert all("--with-base" not in args for _, args in step_calls)


def test_orchestrator_apply_with_bootstrap_base():
    orch = _load_module("run_p035_sql_boot", N8N / "run_staging_cabinet_p0_3_5_2026-08-10.py")
    step_calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(script: str, *args: str) -> None:
        step_calls.append((script, args))

    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run", side_effect=fake_run):
        subprocess_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--apply", "--bootstrap-base"]):
            orch.main()

    assert step_calls[0] == ("apply_staging_cabinet_sql_2026-08-10.py", ("--with-base",))


def test_orchestrator_dry_run_mentions_bootstrap_base(capsys):
    orch = _load_module("run_p035_dry", N8N / "run_staging_cabinet_p0_3_5_2026-08-10.py")
    with patch.object(orch.subprocess, "run") as subprocess_run, patch.object(orch, "run") as run:
        subprocess_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(sys, "argv", ["run_staging_cabinet_p0_3_5_2026-08-10.py", "--bootstrap-base"]):
            orch.main()
    run.assert_not_called()
    captured = capsys.readouterr()
    assert "--bootstrap-base" in captured.out
    assert "--with-base" in captured.out


SMOKE_SCRIPT = ROOT / "backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py"


def _load_smoke_module():
    """Load smoke script from its repo path; relies on script-local sys.path bootstrap."""
    spec = importlib.util.spec_from_file_location("post_deploy_smoke", SMOKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_post_deploy_smoke_source_bootstraps_n8n_path():
    source = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert 'sys.path.insert(0, str(_N8N_CURRENT))' in source or "sys.path.insert(0, str(_N8N_CURRENT))" in source
    assert '_N8N_CURRENT = _REPO_ROOT / "n8n" / "current"' in source


def test_post_deploy_smoke_subprocess_no_module_not_found():
    import os

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--insecure"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert "ModuleNotFoundError" not in combined
    assert "No module named 'whieda_runtime_env'" not in combined
    assert "No module named 'staging_deploy_lib'" not in combined
    payload = json.loads(result.stdout)
    assert payload["phase"] == "post_deploy_smoke"


def test_post_deploy_smoke_passes_with_mock_ssh_and_public():
    smoke = _load_smoke_module()
    if str(N8N) not in sys.path:
        sys.path.insert(0, str(N8N))
    import whieda_runtime_env
    import staging_deploy_lib as lib

    core_cfg = {"host": "185.252.232.93", "user": "root", "password": "test"}
    site_cfg = {"host": "173.249.45.83", "user": "root", "password": "test"}

    def mock_exec(_client, command: str, *, timeout: int = 120) -> str:
        if "8081/health/live" in command:
            return "200"
        if "18081/health/live" in command:
            return "200"
        if "wwc-admin-staging-tunnel.service" in command:
            return "active"
        raise AssertionError(f"unexpected ssh command: {command}")

    public_ok = {
        "check": "public_http",
        "ok": True,
        "results": [
            {"path": "/cabinet/", "status": 200, "ok": True},
            {"path": "/wwc-cabinet-config.json", "status": 200, "ok": True},
            {"path": "/api/v1/admin/me", "status": 401, "ok": True},
        ],
        "insecure_tls": True,
    }

    with patch.object(whieda_runtime_env, "ssh_config", return_value=core_cfg), patch.object(
        whieda_runtime_env, "site_ssh_config", return_value=site_cfg
    ), patch.object(lib, "connect_ssh", return_value=MagicMock()), patch.object(lib, "ssh_exec", side_effect=mock_exec), patch.object(
        smoke, "check_public_http", return_value=public_ok
    ):
        with patch.object(sys, "argv", ["post_deploy_staging_cabinet_smoke_2026-08-10.py", "--insecure"]):
            exit_code = smoke.main()

    assert exit_code == 0


def test_post_deploy_smoke_public_404_fails():
    smoke = _load_smoke_module()
    if str(N8N) not in sys.path:
        sys.path.insert(0, str(N8N))
    import whieda_runtime_env
    import staging_deploy_lib as lib

    core_cfg = {"host": "185.252.232.93", "user": "root", "password": "test"}
    site_cfg = {"host": "173.249.45.83", "user": "root", "password": "test"}

    def mock_exec(_client, command: str, *, timeout: int = 120) -> str:
        if "8081/health/live" in command or "18081/health/live" in command:
            return "200"
        if "wwc-admin-staging-tunnel.service" in command:
            return "active"
        return ""

    public_fail = {
        "check": "public_http",
        "ok": False,
        "results": [
            {"path": "/cabinet/", "status": 200, "ok": True},
            {"path": "/wwc-cabinet-config.json", "status": 200, "ok": True},
            {
                "path": "/api/v1/admin/me",
                "status": 404,
                "ok": False,
                "hint": "401=proxy+auth OK; 404=proxy/API not connected",
            },
        ],
        "insecure_tls": True,
    }

    with patch.object(whieda_runtime_env, "ssh_config", return_value=core_cfg), patch.object(
        whieda_runtime_env, "site_ssh_config", return_value=site_cfg
    ), patch.object(lib, "connect_ssh", return_value=MagicMock()), patch.object(lib, "ssh_exec", side_effect=mock_exec), patch.object(
        smoke, "check_public_http", return_value=public_fail
    ):
        with patch.object(sys, "argv", ["post_deploy_staging_cabinet_smoke_2026-08-10.py", "--insecure"]):
            exit_code = smoke.main()

    assert exit_code == 1
