"""OpenSSH scp/ssh transport for staging static deploy (P0.3.5.4 / P0.3.5.5 wwcdeploy)."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from staging_deploy_gate_lib import DEPLOY_USER, STAGING_STATIC_DIR

DEFAULT_UPLOAD_TIMEOUT = 300


class SiteSshKeyRequiredError(RuntimeError):
    """Missing WHIEDA_SITE_SSH_KEY_PATH or WHIEDA_SITE_KNOWN_HOSTS_PATH."""

    code = "site_ssh_key_required"

    def __init__(self, message: str = "site_ssh_key_required") -> None:
        super().__init__(message)


class SiteSshDeployUserForbiddenError(RuntimeError):
    """WHIEDA_SITE_SSH_USER must be wwcdeploy for staging static deploy."""

    code = "site_ssh_user_forbidden"

    def __init__(self, user: str) -> None:
        super().__init__(f"site_ssh_user_forbidden: {user!r} (only {DEPLOY_USER!r} allowed)")
        self.user = user


class UploadTimeoutError(RuntimeError):
    """Raised when scp upload exceeds subprocess timeout."""

    def __init__(self) -> None:
        super().__init__("upload_timeout")


RunFn = Callable[..., subprocess.CompletedProcess]


def resolve_openssh_binary(name: str) -> str:
    candidate = f"{name}.exe" if sys.platform == "win32" else name
    path = shutil.which(candidate)
    if not path:
        raise RuntimeError(f"{candidate} not found in PATH")
    return path


def load_site_openssh_config() -> dict[str, str]:
    key_path = os.environ.get("WHIEDA_SITE_SSH_KEY_PATH", "").strip()
    known_hosts_path = os.environ.get("WHIEDA_SITE_KNOWN_HOSTS_PATH", "").strip()
    if not key_path or not known_hosts_path:
        raise SiteSshKeyRequiredError()

    key_file = Path(key_path)
    known_hosts_file = Path(known_hosts_path)
    if not key_file.is_file():
        raise SiteSshKeyRequiredError(f"site_ssh_key_required: key file missing")
    if not known_hosts_file.is_file():
        raise SiteSshKeyRequiredError(f"site_ssh_key_required: known_hosts file missing")

    user = os.environ.get("WHIEDA_SITE_SSH_USER", DEPLOY_USER).strip() or DEPLOY_USER
    if user != DEPLOY_USER:
        raise SiteSshDeployUserForbiddenError(user)

    return {
        "host": os.environ.get("WHIEDA_SITE_SSH_HOST", "173.249.45.83"),
        "user": user,
        "key_path": str(key_file),
        "known_hosts_path": str(known_hosts_file),
    }


def openssh_common_options(cfg: dict[str, str]) -> list[str]:
    return [
        "-i",
        cfg["key_path"],
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={cfg['known_hosts_path']}",
    ]


def build_ssh_command(cfg: dict[str, str], remote_command: str) -> list[str]:
    ssh_bin = resolve_openssh_binary("ssh")
    target = f"{cfg['user']}@{cfg['host']}"
    return [ssh_bin, *openssh_common_options(cfg), target, remote_command]


def build_scp_command(cfg: dict[str, str], local_path: Path, remote_spec: str) -> list[str]:
    scp_bin = resolve_openssh_binary("scp")
    return [scp_bin, "-O", *openssh_common_options(cfg), str(local_path), remote_spec]


def run_ssh(
    cfg: dict[str, str],
    remote_command: str,
    *,
    timeout: int,
    run: RunFn = subprocess.run,
) -> subprocess.CompletedProcess:
    result = run(
        build_ssh_command(cfg, remote_command),
        check=False,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ssh failed: {remote_command}\n{detail}")
    return result


def upload_staging_tarball_openssh_atomic(
    *,
    local_tarball: Path,
    remote_dir: str,
    digest: str,
    cfg: dict[str, str],
    upload_timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    run: RunFn = subprocess.run,
) -> dict[str, Any]:
    """scp temp archive → sha256 verify → extract → cleanup temp only."""
    if remote_dir != STAGING_STATIC_DIR:
        raise ValueError(f"remote_dir must be {STAGING_STATIC_DIR!r}, got {remote_dir!r}")

    token = secrets.token_hex(8)
    remote_tar = f"{remote_dir}/cabinet-staging.{token}.tgz"
    remote_spec = f"{cfg['user']}@{cfg['host']}:{remote_tar}"

    try:
        run_ssh(cfg, f"mkdir -p {remote_dir}", timeout=60, run=run)
        try:
            scp_result = run(
                build_scp_command(cfg, local_tarball, remote_spec),
                check=False,
                timeout=upload_timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise UploadTimeoutError() from exc

        if scp_result.returncode != 0:
            detail = (scp_result.stderr or scp_result.stdout or "").strip()
            raise RuntimeError(f"scp failed\n{detail}")

        verify = run_ssh(
            cfg,
            f"sha256sum {remote_tar}",
            timeout=120,
            run=run,
        )
        remote_digest = verify.stdout.strip().split(maxsplit=1)[0] if verify.stdout.strip() else ""
        if remote_digest != digest:
            raise RuntimeError(
                f"upload integrity mismatch: local={digest[:12]} remote={remote_digest[:12]}"
            )

        run_ssh(cfg, f"tar -xzf {remote_tar} -C {remote_dir}", timeout=300, run=run)
        return {"status": "ok", "sha256": digest, "remote_tar": remote_tar}
    finally:
        try:
            run_ssh(cfg, f"rm -f {remote_tar}", timeout=60, run=run)
        except RuntimeError:
            pass
