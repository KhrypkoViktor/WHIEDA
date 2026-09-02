# -*- coding: utf-8 -*-
"""Release-layer tests for the hardened ref-theme staging deploy script."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ref_theme_slice.py"


def load_module():
    spec = importlib.util.spec_from_file_location("deploy_ref_theme_slice", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_target_is_refused():
    module = load_module()
    with pytest.raises(SystemExit, match="production deploys are disabled"):
        module.resolve_target("production")


def test_staging_target_resolves_with_health_url():
    module = load_module()
    target = module.resolve_target("staging")
    assert target["health_url"] == "http://127.0.0.1:8081/health/ready"
    assert target["remote"].startswith("/opt/whieda-platform-staging")


def test_build_archive_from_head_contains_all_slice_files():
    module = load_module()
    import subprocess

    head = subprocess.run(
        ["git", "-C", str(module.REPO), "rev-parse", "HEAD"], capture_output=True, check=True
    ).stdout.decode().strip()
    archive, sha = module.build_archive(head)
    assert len(sha) == 64
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert names == set(module.FILES)


def test_build_archive_refuses_unknown_commit():
    module = load_module()
    with pytest.raises(RuntimeError, match="git archive failed"):
        module.build_archive("0" * 40)


def test_pinned_host_key_loads_with_fingerprint_and_host():
    module = load_module()
    key, fingerprint, host = module.load_pinned_host_key()
    assert fingerprint.startswith("SHA256:")
    assert key.get_name() == "ssh-ed25519"
    pin = json.loads(module.KEYS_FILE.read_text(encoding="utf-8"))
    assert fingerprint == pin["fingerprint"]
    assert host == pin["host"]


def test_flag_constants_match_contract():
    module = load_module()
    assert module.FLAG_NAME == "THEME_TEMPORARY_FREE_FOR_VERIFIED_TELEGRAM_USERS"
    assert module.FLAG_LINE == module.FLAG_NAME + "=true"


def test_check_host_matches_pin():
    module = load_module()
    module.check_host_matches_pin({"host": "185.252.232.93"}, "185.252.232.93")
    with pytest.raises(SystemExit, match="does not match pinned host"):
        module.check_host_matches_pin({"host": "203.0.113.1"}, "185.252.232.93")


class _FakeFile:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSFTP:
    def __init__(self):
        self.written = {}

    def file(self, path, mode):
        handle = _FakeFile()
        self.written[path] = handle
        return handle

    def close(self):
        pass


class _FakeClient:
    def __init__(self):
        self.sftp = _FakeSFTP()

    def open_sftp(self):
        return self.sftp


def test_remote_existing_paths_and_missing_split(monkeypatch):
    module = load_module()
    existing_remote = {
        "app/settings.py",
        "app/ref/__init__.py",
        "app/theme_access/__init__.py",
        "app/theme_access/service.py",
        "app/theme_access/routes.py",
    }

    def fake_exec(client, command, timeout=900):
        if "for path in" in command:
            return "\n".join(sorted(existing_remote))
        return ""

    monkeypatch.setattr(module, "_exec", fake_exec)
    existing = module.remote_existing_paths(_FakeClient(), "/opt/whieda-platform-staging/src/platform-api")
    all_paths = list(module.FILES)
    new_paths = [path for path in all_paths if path not in existing]
    assert new_paths == ["app/ref/service.py", "app/ref/routes.py"]


def test_backup_remote_archives_only_existing(monkeypatch):
    module = load_module()
    existing = ["app/settings.py", "app/ref/__init__.py"]
    recorded = []

    def fake_exec(client, command, timeout=900):
        recorded.append(command)
        return ""

    monkeypatch.setattr(module, "_exec", fake_exec)
    target = module.resolve_target("staging")
    module.backup_remote(_FakeClient(), target, "TS", existing)
    tar_command = next((c for c in recorded if "tar -czf" in c), "")
    assert "app/settings.py" in tar_command
    assert "app/ref/service.py" not in tar_command
    assert any("cp .env" in c for c in recorded)


def _apply_failure_fixture(monkeypatch):
    """Existing remote = 5 of 7 files; apply fails at the health gate."""
    module = load_module()
    existing = {
        "app/settings.py",
        "app/ref/__init__.py",
        "app/theme_access/__init__.py",
        "app/theme_access/service.py",
        "app/theme_access/routes.py",
    }
    commands = []

    def fake_exec(client, command, timeout=900):
        commands.append(command)
        if "for path in" in command:
            return "\n".join(sorted(existing))
        return ""

    monkeypatch.setattr(module, "_exec", fake_exec)
    monkeypatch.setattr(module, "health_poll", lambda *a, **k: False)
    return module, commands, existing


def test_apply_failure_restores_code_env_and_removes_new_files(monkeypatch):
    module, commands, existing = _apply_failure_fixture(monkeypatch)
    target = module.resolve_target("staging")
    client = _FakeClient()
    with pytest.raises(RuntimeError, match="health check failed after deploy"):
        module.apply(client, target, "37bfbd7", b"archive-bytes", "a" * 64)

    new_paths = ["app/ref/service.py", "app/ref/routes.py"]
    rollback_commands = [c for c in commands if "rm -f" in c or "tar -xzf" in c and "ref-theme-code" in c or c.startswith("cp ")]
    # code + env restored
    assert any("tar -xzf" in c and "ref-theme-code" in c for c in rollback_commands)
    assert any(c.startswith("cp ") and ".bak" in c and "/.env" in c for c in rollback_commands)
    # new files removed
    rm_command = next((c for c in rollback_commands if "rm -f" in c), "")
    for path in new_paths:
        assert path in rm_command
    # rebuild + restart after rollback
    assert any("docker compose build api worker" in c for c in commands)
    # code backup archived only the pre-existing files
    tar_command = next((c for c in commands if "tar -czf" in c and "ref-theme-code" in c), "")
    assert "app/ref/service.py" not in tar_command
    assert "app/settings.py" in tar_command
