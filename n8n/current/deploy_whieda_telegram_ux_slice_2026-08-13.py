"""Deploy only the coherent Core Telegram UX slice with backup and rollback.

This keeps the deployment narrow: no n8n workflows, database migrations, Sheets,
or unrelated files from the dirty repository are included.
"""

from __future__ import annotations

import argparse
import io
import json
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

from whieda_runtime_env import ssh_config


REPO = Path(__file__).resolve().parents[2]
LOCAL_API = REPO / "backend" / "platform-api"
REMOTE = "/opt/whieda-platform-core"
REMOTE_API = f"{REMOTE}/src/platform-api"
COMPOSE_DIR = f"{REMOTE}/src/deploy/core"
TELEGRAM_FILES = (
    "catalog_browse.py",
    "delivery.py",
    "modes.py",
    "navigation.py",
    "processor.py",
    "routes.py",
    "sequencer.py",
    "update_parser.py",
)
ADVISOR_FILES = (
    "gap.py",
    "sql/business_formatters.py",
    "sql/engine.py",
    "sql/repository.py",
    "sql/resolver.py",
    "sql/text.py",
    "sql/product_discovery.py",
    "sql/data/product_discovery_map_v1.tsv",
)


def _exec(client: paramiko.SSHClient, command: str, timeout: int = 900) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if code:
        raise RuntimeError(f"remote command failed ({code}): {err or out}")
    return out


def _archive() -> bytes:
    telegram_dir = LOCAL_API / "app" / "telegram"
    missing = [name for name in TELEGRAM_FILES if not (telegram_dir / name).is_file()]
    missing.extend(
        f"advisor/{name}" for name in ADVISOR_FILES if not (LOCAL_API / "app" / "advisor" / name).is_file()
    )
    if missing:
        raise RuntimeError(f"local Telegram UX files missing: {', '.join(missing)}")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in TELEGRAM_FILES:
            archive.add(telegram_dir / name, arcname=f"app/telegram/{name}")
        for name in ADVISOR_FILES:
            archive.add(LOCAL_API / "app" / "advisor" / name, arcname=f"app/advisor/{name}")
    return buffer.getvalue()


def _health(client: paramiko.SSHClient) -> bool:
    status = _exec(
        client,
        "curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/health/ready || true",
        timeout=25,
    )
    return status == "200"


def _remote_paths() -> list[str]:
    return [f"app/telegram/{name}" for name in TELEGRAM_FILES] + [
        f"app/advisor/{name}" for name in ADVISOR_FILES
    ]


def _remote_existing_paths(client: paramiko.SSHClient) -> list[str]:
    paths = _remote_paths()
    quoted = " ".join(paths)
    output = _exec(
        client,
        f"cd {REMOTE_API} && for path in {quoted}; do test -f \"$path\" && printf '%s\\n' \"$path\"; done",
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _rollback(client: paramiko.SSHClient, backup: str, new_paths: list[str]) -> None:
    if new_paths:
        _exec(client, f"cd {REMOTE_API} && rm -f {' '.join(new_paths)}")
    _exec(client, f"tar -xzf {backup} -C {REMOTE_API}")
    _exec(client, f"cd {COMPOSE_DIR} && docker compose build api worker && docker compose up -d api worker", timeout=1200)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Upload, rebuild, and health-check production Core.")
    args = parser.parse_args()
    payload = _archive()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{REMOTE}/backups/telegram-ux-slice-{stamp}.tar.gz"
    plan = {
        "files": _remote_paths(),
        "bytes": len(payload),
        "backup": backup,
    }
    if not args.apply:
        print(json.dumps({"action": "dry-run", **plan}, ensure_ascii=False))
        return 0

    cfg = ssh_config()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(cfg["host"], username=cfg["user"], password=cfg["password"], look_for_keys=False, allow_agent=False, timeout=30)
    try:
        existing_paths = _remote_existing_paths(client)
        new_paths = [path for path in _remote_paths() if path not in existing_paths]
        if existing_paths:
            _exec(
                client,
                f"mkdir -p {REMOTE}/backups && tar -czf {backup} -C {REMOTE_API} {' '.join(existing_paths)}",
            )
        else:
            _exec(client, f"mkdir -p {REMOTE}/backups && tar -czf {backup} --files-from /dev/null")
        sftp = client.open_sftp()
        try:
            with sftp.file(f"{REMOTE}/telegram-ux-slice.tar.gz", "wb") as target:
                target.write(payload)
        finally:
            sftp.close()
        uploaded = False
        try:
            _exec(client, f"tar -xzf {REMOTE}/telegram-ux-slice.tar.gz -C {REMOTE_API}")
            uploaded = True
            _exec(client, f"cd {COMPOSE_DIR} && docker compose build api worker && docker compose up -d api worker", timeout=1200)
            time.sleep(6)
            if not _health(client):
                raise RuntimeError("health check failed")
        except Exception:
            if uploaded:
                _rollback(client, backup, new_paths)
            raise
        print(
            json.dumps(
                {"action": "deployed", **plan, "health_ready": 200, "new_remote_files": new_paths},
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
