"""Deploy Platform Core API to whieda-n8n (co-located with n8n)."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

from whieda_runtime_env import pg_env, ssh_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_DIR = "/opt/whieda-platform-core"
LOCAL_DEPLOY = REPO_ROOT / "backend" / "deploy" / "core"
LOCAL_API = REPO_ROOT / "backend" / "platform-api"
ROUTE_DEFAULTS = {
    "CORE_ROUTE_PUBLIC_REF": "core",
    "CORE_ROUTE_LEADS": "core",
    "CORE_ROUTE_ADVISOR": "core",
    "CORE_ROUTE_TELEGRAM": "core",
    "CORE_ROUTE_DEEP": "off",
}


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 600) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if code != 0:
        raise RuntimeError(f"Command failed ({code}): {command}\n{out}\n{err}")
    return out


def build_tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in LOCAL_DEPLOY.rglob("*"):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts:
                continue
            arcname = Path("deploy/core") / path.relative_to(LOCAL_DEPLOY)
            tar.add(path, arcname=str(arcname).replace("\\", "/"))
        for path in LOCAL_API.rglob("*"):
            if path.is_dir():
                continue
            if any(part in {".pytest_cache", "__pycache__"} for part in path.parts):
                continue
            arcname = Path("platform-api") / path.relative_to(LOCAL_API)
            tar.add(path, arcname=str(arcname).replace("\\", "/"))
    buffer.seek(0)
    return buffer.read()


def database_url_from_env(client: paramiko.SSHClient | None = None) -> str:
    if client is not None:
        remote_value = fetch_remote_env_value(client, "PLATFORM_DATABASE_URL")
        if remote_value:
            return remote_value
    env = pg_env()
    user = env["PGUSER"]
    password = env["PGPASSWORD"]
    host = env["PGHOST"]
    port = env.get("PGPORT", "5432")
    database = env.get("PGDATABASE", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def fetch_n8n_env(client: paramiko.SSHClient, name: str) -> str | None:
    try:
        value = ssh_exec(client, f"docker exec n8n-n8n-1 printenv {name}", timeout=20)
        return value or None
    except Exception:
        return None


def fetch_remote_env_value(client: paramiko.SSHClient, key: str) -> str | None:
    env_path = f"{REMOTE_DIR}/src/deploy/core/.env"
    try:
        raw = ssh_exec(client, f"grep -m1 '^{key}=' {env_path} 2>/dev/null || true", timeout=15)
        if raw and "=" in raw:
            return raw.split("=", 1)[1].strip() or None
    except Exception:
        pass
    return None


def fetch_container_env_value(client: paramiko.SSHClient, key: str) -> str | None:
    """Read a non-secret live setting so a source deploy cannot change routes."""

    try:
        value = ssh_exec(client, f"docker exec core-api-1 printenv {key} 2>/dev/null || true", timeout=15)
        return value or None
    except Exception:
        return None


def preserved_route_value(client: paramiko.SSHClient | None, key: str) -> str:
    if client is not None:
        return (
            fetch_container_env_value(client, key)
            or fetch_remote_env_value(client, key)
            or ROUTE_DEFAULTS[key]
        )
    return ROUTE_DEFAULTS[key]


def render_env_file(client: paramiko.SSHClient | None = None) -> str:
    telegram_token = os.environ.get("PLATFORM_TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_token and client is not None:
        telegram_token = fetch_n8n_env(client, "WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN") or ""
    lines = [
        f"PLATFORM_DATABASE_URL={database_url_from_env(client)}",
        "PLATFORM_REDIS_URL=redis://redis:6379/0",
        "PLATFORM_LEGACY_N8N_BASE_URL=https://sysarchn8n.duckdns.org",
        "PLATFORM_LEAD_DELIVERY_WEBHOOK_PATH=/webhook/whieda-lead-delivery-v1",
        *(f"{key}={preserved_route_value(client, key)}" for key in ROUTE_DEFAULTS),
    ]
    if telegram_token:
        lines.append(f"PLATFORM_TELEGRAM_BOT_TOKEN={telegram_token}")
    webhook_secret = os.environ.get("PLATFORM_TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not webhook_secret and client is not None:
        webhook_secret = fetch_remote_env_value(client, "PLATFORM_TELEGRAM_WEBHOOK_SECRET") or ""
    if webhook_secret:
        lines.append(f"PLATFORM_TELEGRAM_WEBHOOK_SECRET={webhook_secret}")
    lines.append("PLATFORM_TELEGRAM_LEGACY_TIMEOUT_SEC=180")
    lines.append("PLATFORM_LEGACY_REQUEST_TIMEOUT_SEC=30")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    tarball = build_tarball()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "dry-run",
                    "remote_dir": REMOTE_DIR,
                    "tar_bytes": len(tarball),
                    "env_keys": [
                        "PLATFORM_DATABASE_URL (resolved on server)",
                        "PLATFORM_REDIS_URL",
                        "PLATFORM_LEGACY_N8N_BASE_URL",
                        "PLATFORM_LEAD_DELIVERY_WEBHOOK_PATH",
                        *ROUTE_DEFAULTS.keys(),
                    ],
                    "routing": "preserve live container values when deploying",
                },
                ensure_ascii=False,
            )
        )
        return 0

    cfg = ssh_config()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        cfg["host"],
        username=cfg["user"],
        password=cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    env_content = render_env_file(client)
    try:
        ssh_exec(client, f"mkdir -p {REMOTE_DIR}")
        sftp = client.open_sftp()
        try:
            remote_tar = f"{REMOTE_DIR}/platform-core-src.tar.gz"
            with sftp.file(remote_tar, "wb") as remote_file:
                remote_file.write(tarball)
            with sftp.file(f"{REMOTE_DIR}/.env", "w") as remote_env:
                remote_env.write(env_content)
        finally:
            sftp.close()

        ssh_exec(
            client,
            f"cd {REMOTE_DIR} && rm -rf src && mkdir -p src && "
            f"tar -xzf platform-core-src.tar.gz -C src",
        )

        compose_dir = f"{REMOTE_DIR}/src/deploy/core"
        sftp = client.open_sftp()
        try:
            with sftp.file(f"{compose_dir}/.env", "w") as remote_env:
                remote_env.write(env_content)
        finally:
            sftp.close()

        if not args.skip_build:
            ssh_exec(
                client,
                f"cd {compose_dir} && docker compose build",
                timeout=1200,
            )
        ssh_exec(client, f"cd {compose_dir} && docker compose up -d")
        time.sleep(5)
        health = ssh_exec(
            client,
            'curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://127.0.0.1:8080/health/live',
            timeout=20,
        )
        ready = ssh_exec(
            client,
            'curl -s -o /dev/null -w "%{http_code}" --max-time 12 http://127.0.0.1:8080/health/ready',
            timeout=20,
        )
        print(
            json.dumps(
                {
                    "action": "deployed",
                    "remote_dir": REMOTE_DIR,
                    "health_live": health,
                    "health_ready": ready,
                },
                ensure_ascii=False,
            )
        )
        return 0 if health == "200" and ready == "200" else 1
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
