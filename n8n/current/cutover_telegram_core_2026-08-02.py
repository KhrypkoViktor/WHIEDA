"""Cutover Telegram bot webhook to Platform Core (fixes broken legacy path)."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import uuid

import paramiko
import requests

from whieda_runtime_env import ssh_config

CORE_WEBHOOK_PATH = "/whieda-platform/v1/telegram/whieda-advisor-bot/webhook"
PUBLIC_WEBHOOK = f"https://sysarchn8n.duckdns.org{CORE_WEBHOOK_PATH}"
CORE_DIR = "/opt/whieda-platform-core/src/deploy/core"


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 180) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if code != 0:
        raise RuntimeError(f"remote command failed ({code}): {err or out}")
    return out


def fetch_bot_token(client: paramiko.SSHClient) -> str:
    _, o, _ = client.exec_command(
        "docker exec n8n-n8n-1 printenv WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN",
        timeout=20,
    )
    o.channel.recv_exit_status()
    token = o.read().decode().strip()
    if not token:
        raise RuntimeError("WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN missing in n8n container")
    return token


def patch_core_env(client: paramiko.SSHClient, secret: str) -> None:
    env_path = "/opt/whieda-platform-core/src/deploy/core/.env"
    _, o, _ = client.exec_command(f"cat {env_path}", timeout=20)
    o.channel.recv_exit_status()
    lines = o.read().decode().splitlines()
    out: list[str] = []
    keys = {
        "CORE_ROUTE_TELEGRAM": "core",
        "PLATFORM_TELEGRAM_WEBHOOK_SECRET": secret,
    }
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in keys:
            out.append(f"{key}={keys[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in keys.items():
        if key not in seen:
            out.append(f"{key}={value}")
    content = "\n".join(out).rstrip() + "\n"
    sftp = client.open_sftp()
    with sftp.file(env_path, "w") as f:
        f.write(content)
    sftp.close()
    ssh_exec(client, f"cd {CORE_DIR} && docker compose up -d --force-recreate api worker", timeout=240)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            route = ssh_exec(client, "docker exec core-api-1 printenv CORE_ROUTE_TELEGRAM", timeout=20)
            secret_loaded = ssh_exec(
                client, "docker exec core-api-1 printenv PLATFORM_TELEGRAM_WEBHOOK_SECRET", timeout=20
            )
            health = ssh_exec(
                client,
                'curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://127.0.0.1:8080/health/ready',
                timeout=20,
            )
            if route == "core" and secret_loaded == secret and health == "200":
                return
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError("Core did not become ready with the Telegram route and webhook secret")


def set_telegram_webhook(token: str, secret: str) -> dict:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": PUBLIC_WEBHOOK,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        },
        timeout=30,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"setWebhook failed: {data}")
    info = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=20).json()
    return {"setWebhook": data, "webhookInfo": info.get("result", {})}


def probe_webhook(secret: str) -> int:
    payload = {
        "update_id": int(uuid.uuid4().int % 10_000_000),
        "message": {
            "message_id": 1,
            "from": {"id": 1, "is_bot": False, "first_name": "Probe"},
            "chat": {"id": 1, "type": "private"},
            "date": 1700000000,
            "text": "Привет",
        },
    }
    response = requests.post(
        PUBLIC_WEBHOOK,
        json=payload,
        headers={
            "x-telegram-bot-api-secret-token": secret,
            "Host": "sysarchn8n.duckdns.org",
            "X-Forwarded-Host": "wwc.best",
        },
        timeout=30,
    )
    return response.status_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--secret",
        help="PLATFORM_TELEGRAM_WEBHOOK_SECRET (random if omitted)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secret = (args.secret or secrets.token_urlsafe(24)).strip()
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
    try:
        token = fetch_bot_token(client)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "action": "dry-run",
                        "webhook": PUBLIC_WEBHOOK,
                        "secret_generated": not bool(args.secret),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        patch_core_env(client, secret)
        status = probe_webhook(secret)
        if status != 200:
            raise RuntimeError(f"Core webhook probe failed with HTTP {status}; Telegram webhook was not changed")
        webhook_result = set_telegram_webhook(token, secret)
        print(
            json.dumps(
                {
                    "action": "cutover",
                    "webhook": PUBLIC_WEBHOOK,
                    "probe_status": status,
                    "telegram": webhook_result,
                    "secret_saved_on_server": True,
                    "note": "Save PLATFORM_TELEGRAM_WEBHOOK_SECRET in your password manager",
                    "secret_preview": secret[:6] + "…",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if status == 200 else 1
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
