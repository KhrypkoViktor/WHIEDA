"""P0.3.5.5: wwcdeploy deploy-gate validation and transport identity tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


@pytest.fixture(scope="module")
def gate():
    return _load_module("staging_deploy_gate_lib", N8N / "staging_deploy_gate_lib.py")


@pytest.fixture(scope="module")
def transport():
    return _load_module("staging_openssh_transport_p0355", N8N / "staging_openssh_transport.py")


def _temp_path(gate, token: str = "a" * 16) -> str:
    return f"{gate.STAGING_STATIC_DIR}/cabinet-staging.{token}.tgz"


@pytest.mark.parametrize(
    "command,action",
    [
        ("scp -t {path}", "scp_t"),
        ("scp -p -t {path}", "scp_t"),
        ("mkdir -p /var/www/admin-staging-wwc-best", "mkdir"),
        ("sha256sum {path}", "sha256sum"),
        ("tar -xzf {path} -C /var/www/admin-staging-wwc-best", "tar_extract"),
        ("rm -f {path}", "rm"),
    ],
)
def test_validate_allows_deploy_commands(gate, command, action):
    path = _temp_path(gate)
    cmd = command.format(path=path)
    result = gate.validate_ssh_original_command(cmd)
    assert result["action"] == action
    assert result["argv"]


@pytest.mark.parametrize(
    "command",
    [
        "",
        "rm -rf /var/www/admin-staging-wwc-best",
        "cat /etc/passwd",
        "scp -t /var/www/admin-staging-wwc-best/cabinet-staging.deadbeef.tgz",
        "scp -t /var/www/admin-staging-wwc-best/cabinet-staging.0123456789abcdef0.tgz; rm -rf /",
        "mkdir -p /var/www/admin-staging-wwc-best && id",
        "sha256sum /var/www/x",
        "sha256sum {path} | awk '{{print $1}}'",
        "tar -xzf /var/www/admin-staging-wwc-best/cabinet-staging.0123456789abcdef0.tgz -C /tmp",
        "rm -f /var/www/admin-staging-wwc-best/../etc/passwd",
        "bash",
        "sh -c id",
        "scp -t /var/www/admin-staging-wwc-best/cabinet-staging.*.tgz",
        "$(id)",
    ],
)
def test_validate_rejects_forbidden_commands(gate, command):
    path = _temp_path(gate)
    cmd = command.format(path=path) if "{path}" in command else command
    with pytest.raises(gate.DeployGateRejectedError):
        gate.validate_ssh_original_command(cmd)


def test_authorized_keys_line_has_all_restrictions(gate):
    sample_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyComment deploy@local"
    line = gate.build_authorized_keys_line(sample_key)
    assert gate.GATE_PATH in line
    assert "command=" in line
    for restriction in (
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-pty",
        "no-user-rc",
        "no-X11-forwarding",
    ):
        assert restriction in line
    assert line.endswith(sample_key)


def test_public_key_fingerprint_non_empty(gate):
    sample_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyComment deploy@local"
    fp = gate.public_key_fingerprint(sample_key)
    assert fp.startswith("SHA256:")


def test_transport_default_user_wwcdeploy(transport, tmp_path, monkeypatch):
    key_file = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("dummy", encoding="utf-8")
    known_hosts.write_text("host key", encoding="utf-8")
    monkeypatch.setenv("WHIEDA_SITE_SSH_KEY_PATH", str(key_file))
    monkeypatch.setenv("WHIEDA_SITE_KNOWN_HOSTS_PATH", str(known_hosts))
    monkeypatch.delenv("WHIEDA_SITE_SSH_USER", raising=False)
    cfg = transport.load_site_openssh_config()
    assert cfg["user"] == "wwcdeploy"


def test_transport_rejects_root_user_override(transport, tmp_path, monkeypatch):
    key_file = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    key_file.write_text("dummy", encoding="utf-8")
    known_hosts.write_text("host key", encoding="utf-8")
    monkeypatch.setenv("WHIEDA_SITE_SSH_KEY_PATH", str(key_file))
    monkeypatch.setenv("WHIEDA_SITE_KNOWN_HOSTS_PATH", str(known_hosts))
    monkeypatch.setenv("WHIEDA_SITE_SSH_USER", "root")
    with pytest.raises(transport.SiteSshDeployUserForbiddenError) as exc:
        transport.load_site_openssh_config()
    assert exc.value.code == "site_ssh_user_forbidden"


def test_setup_uses_shell_required_for_forced_command_not_nologin():
    source = (N8N / "setup_staging_site_wwcdeploy_identity_2026-08-11.py").read_text(encoding="utf-8")
    assert "--shell /bin/bash" in source
    assert "usermod --shell /bin/bash" in source
    assert "/usr/sbin/nologin" not in source


def test_setup_owns_the_entire_approved_static_tree_only():
    source = (N8N / "setup_staging_site_wwcdeploy_identity_2026-08-11.py").read_text(encoding="utf-8")
    assert "chown -R {DEPLOY_USER}:{DEPLOY_USER} {STAGING_STATIC_DIR}" in source


def test_upload_requires_staging_static_dir(transport, tmp_path):
    cfg = {
        "host": "173.249.45.83",
        "user": "wwcdeploy",
        "key_path": "C:/keys/site_deploy",
        "known_hosts_path": "C:/keys/site_known_hosts",
    }
    with pytest.raises(ValueError, match="remote_dir must be"):
        transport.upload_staging_tarball_openssh_atomic(
            local_tarball=tmp_path / "local.tgz",
            remote_dir="/var/www/wrong",
            digest="abc123",
            cfg=cfg,
            run=lambda *a, **k: None,
        )
