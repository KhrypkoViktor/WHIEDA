"""Deactivate and delete leftover TEMP n8n workflows that block startup."""
from __future__ import annotations

import argparse

import paramiko

HOST = "185.252.232.93"
USER = "root"
PASSWORD = "***REMOVED***"


def ssh(cmd: str, timeout: int = 120) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False, timeout=20)
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(f"failed ({code}): {cmd}\n{out}\n{err}")
        return out.strip()
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="Restart n8n after cleanup")
    args = parser.parse_args()

    before = ssh(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At '
        '-c "SELECT count(*) FROM workflow_entity WHERE name ILIKE \'TEMP%\';"'
    )
    ssh(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At '
        '-c "UPDATE workflow_entity SET active=false WHERE name ILIKE \'TEMP%\';"'
    )
    deleted = ssh(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At '
        '-c "DELETE FROM workflow_entity WHERE name ILIKE \'TEMP%\';"'
    )
    after = ssh(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At '
        '-c "SELECT count(*) FROM workflow_entity WHERE name ILIKE \'TEMP%\';"'
    )
    active = ssh(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At '
        '-c "SELECT count(*) FROM workflow_entity WHERE active=true;"'
    )
    print(f"temp_before={before} delete={deleted} temp_after={after} active_total={active}")
    if args.restart:
        ssh("docker restart n8n-n8n-1", timeout=180)
        health = ssh('curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:5678/healthz')
        print(f"healthz={health}")


if __name__ == "__main__":
    main()
