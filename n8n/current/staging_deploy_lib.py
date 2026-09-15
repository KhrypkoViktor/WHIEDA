"""Shared helpers for WWC staging cabinet deploy scripts (P0.3.5+)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import paramiko

HOST_KEYS_PATH = Path(__file__).resolve().parent / "staging_ssh_host_keys.json"


class SshHostKeyMismatchError(RuntimeError):
    """Raised when remote SSH host key does not match configured fingerprint."""


def resolve_npm_command() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_fingerprint(value: str) -> str:
    value = value.strip()
    if value.startswith("SHA256:"):
        return value
    return f"SHA256:{value}"


def key_sha256_fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def load_host_key_fingerprints(path: Path | None = None) -> dict[str, str]:
    keys_path = path or HOST_KEYS_PATH
    payload = json.loads(keys_path.read_text(encoding="utf-8"))
    return {host: normalize_fingerprint(fp) for host, fp in payload.items()}


class StrictFingerprintPolicy(paramiko.MissingHostKeyPolicy):
    """Accept host key only when SHA-256 fingerprint matches; never write known_hosts."""

    def __init__(self, expected_fingerprint: str) -> None:
        self.expected_fingerprint = normalize_fingerprint(expected_fingerprint)

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        actual = key_sha256_fingerprint(key)
        if actual != self.expected_fingerprint:
            raise SshHostKeyMismatchError(
                f"SSH host key mismatch for {hostname}: expected {self.expected_fingerprint}, got {actual}"
            )


def expected_host_fingerprint(host: str, *, keys_path: Path | None = None) -> str:
    fingerprints = load_host_key_fingerprints(keys_path)
    if host not in fingerprints:
        raise RuntimeError(f"No SSH host key fingerprint configured for {host}")
    return fingerprints[host]


def connect_ssh(
    cfg: dict[str, str],
    *,
    timeout: int = 30,
    keys_path: Path | None = None,
) -> paramiko.SSHClient:
    fingerprint = expected_host_fingerprint(cfg["host"], keys_path=keys_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(StrictFingerprintPolicy(fingerprint))
    client.connect(
        cfg["host"],
        username=cfg["user"],
        password=cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=timeout,
    )
    return client


def ssh_exec(client: paramiko.SSHClient, command: str, *, timeout: int = 120) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if code != 0:
        raise RuntimeError(f"{command}\n{out}\n{err}")
    return out
