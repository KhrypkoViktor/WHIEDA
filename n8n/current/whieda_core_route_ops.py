"""Safe CORE_ROUTE_* helpers for staging probes vs production rails."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import paramiko

REMOTE_DIR = "/opt/whieda-platform-core/src/deploy/core"

PRODUCTION_ROUTES = {
    "CORE_ROUTE_PUBLIC_REF": "core",
    "CORE_ROUTE_LEADS": "core",
    "CORE_ROUTE_ADVISOR": "shadow",
    "CORE_ROUTE_TELEGRAM": "legacy",
    "CORE_ROUTE_DEEP": "off",
}

PROBE_ADVISOR_ROUTE = "core"


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")


def read_routes(client: paramiko.SSHClient) -> dict[str, str]:
    _, out, _ = ssh_exec(
        client,
        f"grep -E '^(CORE_ROUTE_PUBLIC_REF|CORE_ROUTE_LEADS|CORE_ROUTE_ADVISOR|"
        f"CORE_ROUTE_TELEGRAM|CORE_ROUTE_DEEP)=' {REMOTE_DIR}/.env || true",
    )
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def read_runtime_route(client: paramiko.SSHClient, key: str) -> str | None:
    """Read the effective variable from the running Core container."""
    code, out, _ = ssh_exec(client, f"docker exec core-api-1 printenv {key}", timeout=30)
    return out.strip() if code == 0 and out.strip() else None


def wait_for_runtime_route(client: paramiko.SSHClient, key: str, value: str, *, timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    actual = None
    while time.monotonic() < deadline:
        actual = read_runtime_route(client, key)
        if actual == value:
            return
        time.sleep(1)
    raise RuntimeError(f"Core container did not load {key}={value!r}; effective value is {actual!r}")


def set_route(client: paramiko.SSHClient, key: str, value: str, *, restart: bool = True) -> None:
    code, _, err = ssh_exec(
        client,
        f"cd {REMOTE_DIR} && "
        f"grep -q '^{key}=' .env && "
        f"sed -i 's/^{key}=.*/{key}={value}/' .env || "
        f"echo '{key}={value}' >> .env",
    )
    if code != 0:
        raise RuntimeError(f"Could not update {key}: {err.strip()}")
    if restart:
        code, _, err = ssh_exec(client, f"cd {REMOTE_DIR} && docker compose up -d --force-recreate api")
        if code != 0:
            raise RuntimeError(f"Could not recreate Core API: {err.strip()}")
        wait_for_runtime_route(client, key, value)


def restore_production_routing(client: paramiko.SSHClient) -> dict[str, str]:
    for key, value in PRODUCTION_ROUTES.items():
        set_route(client, key, value, restart=False)
    code, _, err = ssh_exec(client, f"cd {REMOTE_DIR} && docker compose up -d --force-recreate api")
    if code != 0:
        raise RuntimeError(f"Could not restore Core API routes: {err.strip()}")
    for key, value in PRODUCTION_ROUTES.items():
        wait_for_runtime_route(client, key, value)
    return read_routes(client)


def assert_production_routing(routes: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    routes = routes or {}
    errors: list[str] = []
    if routes.get("CORE_ROUTE_TELEGRAM") != "legacy":
        errors.append(f"CORE_ROUTE_TELEGRAM={routes.get('CORE_ROUTE_TELEGRAM')!r}, need legacy")
    advisor = routes.get("CORE_ROUTE_ADVISOR")
    if advisor == "core":
        errors.append(f"CORE_ROUTE_ADVISOR=core — сайт на Core advisor, ждём проверки владельца")
    if advisor not in {"shadow", "legacy"}:
        errors.append(f"CORE_ROUTE_ADVISOR={advisor!r}, need shadow or legacy")
    if str(routes.get("CORE_ROUTE_DEEP", "off")).lower() != "off":
        errors.append(f"CORE_ROUTE_DEEP={routes.get('CORE_ROUTE_DEEP')!r}, need off")
    return not errors, errors


@contextmanager
def temporary_core_advisor_route(client: paramiko.SSHClient) -> Iterator[dict[str, str]]:
    """Force localhost probes through Core SQL; always restore production rails."""
    before = read_routes(client)
    try:
        set_route(client, "CORE_ROUTE_ADVISOR", PROBE_ADVISOR_ROUTE)
        yield before
    finally:
        restore_production_routing(client)
