"""Count active TEMP workflows via SSH (read-only)."""

from __future__ import annotations

import json
import sys

import paramiko

from whieda_runtime_env import ssh_config


def main() -> None:
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
            "docker exec n8n-postgres-1 psql -U n8n -d n8n -At "
            "-c \"SELECT count(*) FROM workflow_entity WHERE name ILIKE 'TEMP%' AND active=true;\""
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(err or out)
        temp_active = int(out or "0")
        print(json.dumps({"temp_active": temp_active}))
        if temp_active:
            raise SystemExit(1)
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(2) from exc
