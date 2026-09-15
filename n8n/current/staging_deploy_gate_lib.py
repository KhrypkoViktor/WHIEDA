"""SSH deploy-gate validation for wwcdeploy staging static identity (P0.3.5.5 / P0.3.5.5.1)."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from typing import Any

from staging_archive_validator import UnsafeArchiveError, validate_tar_archive

STAGING_STATIC_DIR = "/var/www/admin-staging-wwc-best"
DEPLOY_USER = "wwcdeploy"
GATE_PATH = "/usr/local/libexec/wwc-admin-staging-deploy-gate"
ARCHIVE_VERIFY_PATH = "/usr/local/libexec/wwc-admin-staging-archive-verify"
ARCHIVE_VALIDATOR_LIB = "/usr/local/libexec/staging_archive_validator.py"
TAR_EXTRACT_FLAGS = ("--no-same-owner", "--no-same-permissions")

AUTHORIZED_KEYS_PREFIX = (
    f'command="{GATE_PATH}",'
    "no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding"
)

TEMP_TGZ_RE = rf"{re.escape(STAGING_STATIC_DIR)}/cabinet-staging\.[0-9a-f]{{16}}\.tgz"
TEMP_TGZ_PATH = re.compile(rf"^{TEMP_TGZ_RE}$")

_REJECT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[;&|]"), "shell_operator"),
    (re.compile(r"\$\("), "command_substitution"),
    (re.compile(r"\$\{"), "parameter_expansion"),
    (re.compile(r"`"), "backtick"),
    (re.compile(r"\(\)"), "subshell"),
    (re.compile(r"\.\."), "path_traversal"),
    (re.compile(r"[\*\?\[]"), "wildcard"),
    (re.compile(r"/etc/"), "etc_path"),
    (re.compile(r"\brm\s+-rf\b"), "rm_rf"),
    (re.compile(r"^(?:bash|sh|zsh|dash|ksh|csh|tcsh)\b"), "interactive_shell"),
)

_ALLOWED: tuple[tuple[re.Pattern[str], str, list[str]], ...] = (
    (
        re.compile(rf"^scp(?: -p)? -t ({TEMP_TGZ_RE})$"),
        "scp_t",
        ["scp"],
    ),
    (
        re.compile(rf"^mkdir -p {re.escape(STAGING_STATIC_DIR)}$"),
        "mkdir",
        ["mkdir", "-p", STAGING_STATIC_DIR],
    ),
    (
        re.compile("^sha256sum (" + TEMP_TGZ_RE + r")$"),
        "sha256sum",
        ["sha256sum"],
    ),
    (
        re.compile(rf"^tar -xzf ({TEMP_TGZ_RE}) -C {re.escape(STAGING_STATIC_DIR)}$"),
        "tar_extract",
        ["tar", "-xzf"],
    ),
    (
        re.compile(rf"^rm -f ({TEMP_TGZ_RE})$"),
        "rm",
        ["rm", "-f"],
    ),
)


class DeployGateRejectedError(ValueError):
    """SSH_ORIGINAL_COMMAND rejected by deploy gate."""

    code = "deploy_gate_rejected"

    def __init__(self, reason: str, *, command: str = "") -> None:
        self.reason = reason
        self.command = command
        super().__init__(reason)


def _reject(command: str, reason: str) -> None:
    raise DeployGateRejectedError(reason, command=command)


def validate_ssh_original_command(command: str) -> dict[str, Any]:
    """Parse SSH_ORIGINAL_COMMAND; return action + safe argv or raise DeployGateRejectedError."""
    cmd = (command or "").strip()
    if not cmd:
        _reject(cmd, "empty_command")

    for pattern, action, argv_prefix in _ALLOWED:
        match = pattern.fullmatch(cmd)
        if not match:
            continue

        if action == "scp_t":
            remote_path = match.group(1)
            scp_argv = ["scp"]
            if " -p " in cmd or cmd.startswith("scp -p"):
                scp_argv.append("-p")
            scp_argv.extend(["-t", remote_path])
            return {"action": action, "argv": scp_argv, "remote_path": remote_path}

        if action == "mkdir":
            return {"action": action, "argv": list(argv_prefix)}

        if action in {"sha256sum", "tar_extract", "rm"}:
            remote_path = match.group(1)
            if action == "sha256sum":
                return {
                    "action": action,
                    "argv": ["sha256sum", remote_path],
                    "remote_path": remote_path,
                }
            if action == "tar_extract":
                return {
                    "action": action,
                    "argv": [
                        "tar",
                        "-xzf",
                        remote_path,
                        *TAR_EXTRACT_FLAGS,
                        "-C",
                        STAGING_STATIC_DIR,
                    ],
                    "remote_path": remote_path,
                    "pre_extract": [ARCHIVE_VERIFY_PATH, remote_path],
                }
            return {"action": action, "argv": ["rm", "-f", remote_path], "remote_path": remote_path}

    for pattern, reason in _REJECT_PATTERNS:
        if pattern.search(cmd):
            _reject(cmd, reason)

    if TEMP_TGZ_PATH.search(cmd) is None and STAGING_STATIC_DIR in cmd:
        _reject(cmd, "wrong_temp_name")
    _reject(cmd, "command_not_allowed")


def build_authorized_keys_line(public_key: str) -> str:
    key = public_key.strip()
    if not key:
        raise ValueError("public_key_required")
    return f"{AUTHORIZED_KEYS_PREFIX} {key}"


def public_key_fingerprint(public_key: str) -> str:
    key = public_key.strip()
    if not key:
        raise ValueError("public_key_required")

    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen:
        try:
            result = subprocess.run(
                [ssh_keygen, "-lf", "-"],
                input=key + "\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        except (OSError, subprocess.TimeoutExpired):
            pass

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"SHA256:{digest[:43]}"
