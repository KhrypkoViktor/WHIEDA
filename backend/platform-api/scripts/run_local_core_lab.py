#!/usr/bin/env python3
"""One-command local Core runtime lab (Docker required)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLATFORM_API = ROOT / "backend" / "platform-api"
POSTGRES_COMPOSE = ROOT / "postgres" / "docker-compose.local-staging.yml"
CORE_COMPOSE = PLATFORM_API / "docker-compose.local-core.yml"
STAGING_PROOF = ROOT / "postgres" / "scripts" / "run_local_staging_proof.py"
ENSURE_CORE_DB = ROOT / "postgres" / "scripts" / "ensure_local_core_database.py"
HTTP_SMOKE = PLATFORM_API / "scripts" / "local_http_contract_smoke.py"
API_BASE = "http://127.0.0.1:8080"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    label = " ".join(cmd[:5])
    print(f"\n>>> {label}{'...' if len(cmd) > 5 else ''}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True)


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker required — install Docker Desktop and retry.")


def start_staging_postgres() -> None:
    print("=== start local staging Postgres (55432) ===")
    proc = run(
        ["docker", "compose", "-f", str(POSTGRES_COMPOSE), "up", "-d"],
        cwd=POSTGRES_COMPOSE.parent,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("failed to start staging Postgres")


def wait_api_health(timeout_sec: int = 90) -> None:
    print(f"=== wait Core health {API_BASE}/health/ready ===")
    deadline = time.time() + timeout_sec
    url = f"{API_BASE}/health/ready"
    req = urllib.request.Request(url, headers={"Host": "wwc.best"})
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    print("  Core ready")
                    return
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                pass
        except urllib.error.URLError:
            pass
        time.sleep(2)
    raise TimeoutError(f"Core not ready after {timeout_sec}s")


def stop_core_only() -> None:
    print("=== stop Core container (Postgres stays up) ===")
    run(
        ["docker", "compose", "-f", str(CORE_COMPOSE), "down"],
        cwd=PLATFORM_API,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA local Core runtime lab")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--leave-core-up", action="store_true")
    args = parser.parse_args()

    print("=== WHIEDA local Core runtime lab ===")

    docker_ok = False
    try:
        require_docker()
        docker_ok = True
        start_staging_postgres()

        proof = run([sys.executable, str(STAGING_PROOF)], check=False)
        if proof.returncode != 0:
            raise RuntimeError("run_local_staging_proof.py failed")

        ensure = run([sys.executable, str(ENSURE_CORE_DB)], check=False)
        if ensure.returncode != 0:
            raise RuntimeError("ensure_local_core_database.py failed")

        compose_cmd = ["docker", "compose", "-f", str(CORE_COMPOSE), "up", "-d"]
        if not args.skip_build:
            compose_cmd.append("--build")
        up = run(compose_cmd, cwd=PLATFORM_API, check=False)
        if up.returncode != 0:
            raise RuntimeError("failed to start local Core")

        wait_api_health()

        smoke = run([sys.executable, str(HTTP_SMOKE), "--base-url", API_BASE], check=False)
        if smoke.returncode != 0:
            raise RuntimeError("local_http_contract_smoke.py failed")

        print("\n=== LOCAL CORE RUNTIME LAB: PASS ===")
        return 0
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if docker_ok and not args.leave_core_up:
            stop_core_only()


if __name__ == "__main__":
    raise SystemExit(main())
