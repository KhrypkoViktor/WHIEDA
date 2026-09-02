"""P0.3.5.5.1: safe archive validation and setup idempotency tests."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
N8N = ROOT / "n8n" / "current"
STAGING = ROOT / "backend" / "deploy" / "staging"


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
def validator():
    return _load_module("staging_archive_validator", N8N / "staging_archive_validator.py")


@pytest.fixture(scope="module")
def gate():
    return _load_module("staging_deploy_gate_lib_p03551", N8N / "staging_deploy_gate_lib.py")


def _write_tar(path: Path, add_members) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        add_members(archive)


def _add_regular(archive: tarfile.TarFile, name: str, content: bytes = b"ok") -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))


def _add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    archive.addfile(info)


def test_validate_allows_normal_files_and_dirs(validator, tmp_path):
    archive = tmp_path / "safe.tgz"
    _write_tar(
        archive,
        lambda tar: (
            _add_directory(tar, "cabinet"),
            _add_regular(tar, "cabinet/index.html", b"<html></html>"),
            _add_regular(tar, "cabinet/app.js", b"console.log(1)"),
        ),
    )
    validator.validate_tar_archive(archive)


def test_validate_rejects_symlink(validator, tmp_path):
    archive = tmp_path / "symlink.tgz"

    def add_members(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo(name="etc")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc"
        tar.addfile(info)

    _write_tar(archive, add_members)
    with pytest.raises(validator.UnsafeArchiveError, match="symlink_or_hardlink"):
        validator.validate_tar_archive(archive)


def test_validate_rejects_hardlink(validator, tmp_path):
    archive = tmp_path / "hardlink.tgz"

    def add_members(tar: tarfile.TarFile) -> None:
        _add_regular(tar, "a.txt", b"a")
        info = tarfile.TarInfo(name="b.txt")
        info.type = tarfile.LNKTYPE
        info.linkname = "a.txt"
        tar.addfile(info)

    _write_tar(archive, add_members)
    with pytest.raises(validator.UnsafeArchiveError, match="symlink_or_hardlink"):
        validator.validate_tar_archive(archive)


def test_validate_rejects_absolute_path(validator, tmp_path):
    archive = tmp_path / "absolute.tgz"
    _write_tar(archive, lambda tar: _add_regular(tar, "/etc/passwd", b"x"))
    with pytest.raises(validator.UnsafeArchiveError, match="absolute_path"):
        validator.validate_tar_archive(archive)


def test_validate_rejects_path_traversal(validator, tmp_path):
    archive = tmp_path / "traversal.tgz"
    _write_tar(archive, lambda tar: _add_regular(tar, "../outside.txt", b"x"))
    with pytest.raises(validator.UnsafeArchiveError, match="path_traversal"):
        validator.validate_tar_archive(archive)


def test_validate_rejects_device(validator, tmp_path):
    archive = tmp_path / "device.tgz"

    def add_members(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo(name="dev/zero")
        info.type = tarfile.CHRTYPE
        info.devmajor = 1
        info.devminor = 5
        tar.addfile(info)

    _write_tar(archive, add_members)
    with pytest.raises(validator.UnsafeArchiveError, match="special_file"):
        validator.validate_tar_archive(archive)


def test_validate_rejects_fifo(validator, tmp_path):
    archive = tmp_path / "fifo.tgz"

    def add_members(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo(name="pipe")
        info.type = tarfile.FIFOTYPE
        tar.addfile(info)

    _write_tar(archive, add_members)
    with pytest.raises(validator.UnsafeArchiveError, match="special_file"):
        validator.validate_tar_archive(archive)


def test_gate_tar_extract_includes_archive_verify_before_extract(gate):
    path = f"{gate.STAGING_STATIC_DIR}/cabinet-staging.{'a' * 16}.tgz"
    cmd = f"tar -xzf {path} -C {gate.STAGING_STATIC_DIR}"
    result = gate.validate_ssh_original_command(cmd)
    assert result["action"] == "tar_extract"
    assert result["pre_extract"] == [gate.ARCHIVE_VERIFY_PATH, path]
    assert "--no-same-owner" in result["argv"]
    assert "--no-same-permissions" in result["argv"]
    assert result["argv"].index("--no-same-owner") < result["argv"].index("-C")


def test_gate_bash_source_validates_archive_before_tar_extract():
    source = (STAGING / "wwc-admin-staging-deploy-gate").read_text(encoding="utf-8")
    verify_pos = source.index("wwc-admin-staging-archive-verify")
    extract_pos = source.index("exec /bin/tar -xzf")
    assert verify_pos < extract_pos
    assert "--no-same-owner" in source
    assert "--no-same-permissions" in source
    assert "unsafe_archive" in source


def test_gate_bash_source_has_valid_shell_syntax():
    if os.name == "nt":
        pytest.skip("Windows bash.exe may exist without an installed Linux runtime")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    result = subprocess.run(
        [bash, "-n", str(STAGING / "wwc-admin-staging-deploy-gate")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_setup_uses_full_authorized_keys_line_idempotency():
    source = (N8N / "setup_staging_site_wwcdeploy_identity_2026-08-11.py").read_text(encoding="utf-8")
    assert "grep -Fxq" in source
    assert "split()[0]" not in source


def test_archive_verify_cli_rejects_unsafe_archive(validator, tmp_path):
    archive = tmp_path / "bad.tgz"
    _write_tar(archive, lambda tar: _add_regular(tar, "../x.txt", b"x"))
    rc = validator.main([str(archive)])
    assert rc == 1


def test_archive_verify_cli_accepts_safe_archive(validator, tmp_path):
    archive = tmp_path / "good.tgz"
    _write_tar(archive, lambda tar: _add_regular(tar, "cabinet/index.html", b"ok"))
    rc = validator.main([str(archive)])
    assert rc == 0
