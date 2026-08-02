"""Fetch advisor-dev-postgres password from live n8n credentials via SSH (one-off ops)."""

from __future__ import annotations

import json
import sys

import paramiko

from whieda_runtime_env import ssh_config

CREDENTIAL_NAME = "advisor-dev-postgres"


def fetch_password() -> str:
    cfg = ssh_config()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        cfg["host"],
        username=cfg["user"],
        password=cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=20,
    )
    try:
        cmd = (
            "cd ~/n8n && docker compose exec -T n8n n8n export:credentials --all --decrypted "
            "--output=/tmp/whieda-creds.json >/dev/null "
            "&& docker compose exec -T n8n cat /tmp/whieda-creds.json"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(err or out or f"credential export failed ({code})")
        for item in json.loads(out):
            if item.get("name") == CREDENTIAL_NAME:
                password = (item.get("data") or {}).get("password")
                if password:
                    return password
        raise RuntimeError(f"{CREDENTIAL_NAME} password not found in export")
    finally:
        client.close()


def main() -> None:
    print(fetch_password())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
