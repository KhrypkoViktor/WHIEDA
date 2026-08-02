"""Restart n8n container when public API is unresponsive."""

from __future__ import annotations

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
        for cmd in (
            'docker ps --format "{{.Names}} {{.Status}}" | head -5',
            "docker restart n8n-n8n-1",
            "sleep 15",
            'curl -s -o /dev/null -w "%{http_code}" --max-time 15 http://127.0.0.1:5678/healthz',
        ):
            _, stdout, stderr = client.exec_command(cmd, timeout=180)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            print(cmd)
            print(out or err)
            print(f"exit={code}")
            if code != 0:
                raise SystemExit(code)
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
