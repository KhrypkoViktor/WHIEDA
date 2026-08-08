"""Read-only environment checks for local Core E2E lab."""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from local_core_lab.constants import (
    ACCEPTANCE_EXAMPLE_TARGET,
    ACCEPTANCE_LOCAL_TARGET,
    APPLY_ORDER,
    CORE_COMPOSE,
    ENV_EXAMPLE,
    ENSURE_CORE_DB,
    LOCAL_CORE_API_ROLE,
    LOCAL_STAGING_PORT,
    POSTGRES_COMPOSE,
    SEED,
    SQL_DIR,
    API_PORT,
    DOCKER_CONTAINER,
    CORE_CONTAINER,
)

CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str


FORBIDDEN_ENV_FRAGMENTS = (
    "supabase",
    "duckdns.org",
    "185.252.",
    "sysarchn8n",
    "api.telegram.org",
    "amazonaws.com",
    "neon.tech",
    "render.com",
    "wwc.best/api",
)

SECRET_ASSIGNMENT = re.compile(
    r"^(PLATFORM_TELEGRAM_BOT_TOKEN|PLATFORM_TELEGRAM_WEBHOOK_SECRET|OPENAI_API_KEY)=",
    re.I,
)

PRODUCTION_URL = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[^\s\"']+", re.I)


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _docker_ps_names() -> set[str]:
    if shutil.which("docker") is None:
        return set()
    proc = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def check_docker_installed() -> CheckResult:
    if shutil.which("docker") is not None:
        return CheckResult("docker_installed", "PASS", "docker CLI found on PATH")
    return CheckResult(
        "docker_installed",
        "FAIL",
        "Docker CLI not found — install Docker Desktop",
    )


def check_docker_daemon() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("docker_daemon", "FAIL", "Skipped: docker CLI missing")
    proc = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return CheckResult("docker_daemon", "PASS", "Docker daemon reachable")
    detail = (proc.stderr or proc.stdout or "unknown error").strip().splitlines()
    msg = detail[0] if detail else "docker info failed"
    return CheckResult("docker_daemon", "FAIL", f"Docker daemon unavailable: {msg}")


def check_docker_compose() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("docker_compose", "FAIL", "Skipped: docker CLI missing")
    proc = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        version = (proc.stdout or proc.stderr or "").strip().splitlines()[0]
        return CheckResult("docker_compose", "PASS", version or "docker compose available")
    return CheckResult("docker_compose", "FAIL", "docker compose not available")


def _check_port(port: int, *, expected_container: str) -> CheckResult:
    name = f"port_{port}_free"
    if not _port_in_use(port):
        return CheckResult(name, "PASS", f"Port {port} is free")
    running = _docker_ps_names()
    if expected_container in running:
        return CheckResult(
            name,
            "WARN",
            f"Port {port} in use by lab container {expected_container!r} (OK for rerun)",
        )
    return CheckResult(
        name,
        "FAIL",
        f"Port {port} is in use by another process — free it before starting the lab",
    )


def check_port_55432() -> CheckResult:
    return _check_port(LOCAL_STAGING_PORT, expected_container=DOCKER_CONTAINER)


def check_port_8080() -> CheckResult:
    return _check_port(API_PORT, expected_container=CORE_CONTAINER)


def check_compose_files_exist() -> CheckResult:
    missing = [str(p) for p in (POSTGRES_COMPOSE, CORE_COMPOSE) if not p.is_file()]
    if missing:
        return CheckResult(
            "compose_files_exist",
            "FAIL",
            f"Missing compose files: {', '.join(missing)}",
        )
    return CheckResult(
        "compose_files_exist",
        "PASS",
        f"Found {POSTGRES_COMPOSE.name} and {CORE_COMPOSE.name}",
    )


def check_env_local_example_safe() -> CheckResult:
    if not ENV_EXAMPLE.is_file():
        return CheckResult("env_local_example_safe", "FAIL", f"Missing {ENV_EXAMPLE}")
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    lower = text.lower()
    for frag in FORBIDDEN_ENV_FRAGMENTS:
        if frag in lower:
            return CheckResult(
                "env_local_example_safe",
                "FAIL",
                f".env.local.example contains forbidden fragment {frag!r}",
            )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if SECRET_ASSIGNMENT.search(stripped):
            return CheckResult(
                "env_local_example_safe",
                "FAIL",
                f".env.local.example assigns live secret: {stripped.split('=')[0]}",
            )
        if "password=" in stripped.lower() and "local_core_api_only" not in stripped.lower():
            if "postgresql://" in stripped.lower() and "local_" not in stripped.lower():
                return CheckResult(
                    "env_local_example_safe",
                    "FAIL",
                    "Production-style database password in .env.local.example",
                )
    if PRODUCTION_URL.search(text.replace("host.docker.internal", "127.0.0.1")):
        return CheckResult(
            "env_local_example_safe",
            "FAIL",
            "Production URL detected in .env.local.example",
        )
    return CheckResult(
        "env_local_example_safe",
        "PASS",
        ".env.local.example is localhost-only with no live secrets",
    )


def check_staging_sql_files() -> CheckResult:
    missing: list[str] = []
    for name in APPLY_ORDER:
        if not (SQL_DIR / name).is_file():
            missing.append(name)
    if not SEED.is_file():
        missing.append(SEED.name)
    if missing:
        return CheckResult(
            "staging_sql_files",
            "FAIL",
            f"Missing staging SQL files: {', '.join(missing)}",
        )
    return CheckResult(
        "staging_sql_files",
        "PASS",
        f"All {len(APPLY_ORDER)} apply-order SQL files + seed exist",
    )


def check_api_role_nobypassrls() -> CheckResult:
    if not ENSURE_CORE_DB.is_file():
        return CheckResult(
            "api_role_nobypassrls",
            "FAIL",
            f"Missing {ENSURE_CORE_DB.name}",
        )
    text = ENSURE_CORE_DB.read_text(encoding="utf-8")
    if LOCAL_CORE_API_ROLE not in text:
        return CheckResult(
            "api_role_nobypassrls",
            "FAIL",
            f"Role {LOCAL_CORE_API_ROLE!r} not defined in ensure script",
        )
    if "NOBYPASSRLS" not in text.upper():
        return CheckResult(
            "api_role_nobypassrls",
            "FAIL",
            "ensure_local_core_database.py must create API role with NOBYPASSRLS",
        )
    if "NOSUPERUSER" not in text.upper():
        return CheckResult(
            "api_role_nobypassrls",
            "FAIL",
            "ensure_local_core_database.py must create API role with NOSUPERUSER",
        )
    return CheckResult(
        "api_role_nobypassrls",
        "PASS",
        f"Script creates {LOCAL_CORE_API_ROLE} with NOSUPERUSER NOBYPASSRLS",
    )


def check_acceptance_target_config() -> CheckResult:
    if ACCEPTANCE_LOCAL_TARGET.is_file():
        try:
            data = json.loads(ACCEPTANCE_LOCAL_TARGET.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return CheckResult(
                "acceptance_target_config",
                "FAIL",
                f"Invalid JSON in {ACCEPTANCE_LOCAL_TARGET.name}: {exc}",
            )
        base = str(data.get("base_url") or "")
        if not base.startswith("http://127.0.0.1:"):
            return CheckResult(
                "acceptance_target_config",
                "FAIL",
                f"acceptance_target.local.json base_url must be 127.0.0.1, got {base!r}",
            )
        return CheckResult(
            "acceptance_target_config",
            "PASS",
            f"Found {ACCEPTANCE_LOCAL_TARGET.name} ({data.get('name', 'unnamed')})",
        )

    if ACCEPTANCE_EXAMPLE_TARGET.is_file():
        return CheckResult(
            "acceptance_target_config",
            "WARN",
            "Copy qa/acceptance/acceptance_target.example.json to "
            "qa/acceptance/acceptance_target.local.json before P0 acceptance run",
        )

    return CheckResult(
        "acceptance_target_config",
        "FAIL",
        "Missing acceptance_target.example.json and acceptance_target.local.json",
    )


ALL_CHECKS = (
    check_docker_installed,
    check_docker_daemon,
    check_docker_compose,
    check_port_55432,
    check_port_8080,
    check_compose_files_exist,
    check_env_local_example_safe,
    check_staging_sql_files,
    check_api_role_nobypassrls,
    check_acceptance_target_config,
)


def run_all_checks() -> list[CheckResult]:
    return [fn() for fn in ALL_CHECKS]


def summarize(checks: list[CheckResult]) -> tuple[str, int]:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL", 1
    return "PASS", 0
