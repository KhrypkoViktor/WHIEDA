from __future__ import annotations

import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "n8n" / "current"))
from whieda_runtime_env import ssh_config


config = ssh_config()
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(hostname=config["host"], username=config["user"], password=config["password"], look_for_keys=False, allow_agent=False, timeout=30)
try:
    command = """
set -e
echo '[container-env-names]'
docker exec whieda-shared-staging-api sh -lc 'env | cut -d= -f1 | grep -E "DATABASE|POSTGRES|MEDIA|TENANT" | sort'
echo '[inspect]'
docker inspect --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}{{range .NetworkSettings.Networks}}{{.NetworkID}}{{println}}{{end}}' whieda-shared-staging-api
echo '[staging-db]'
docker exec whieda-staging-db sh -lc 'env | cut -d= -f1 | grep -E "POSTGRES|DATABASE" | sort'
echo '[api-layout]'
docker exec whieda-shared-staging-api sh -lc 'pwd; find /app -maxdepth 3 -type f \( -name "run_shared_staging_tenant_canary.py" -o -name "run_tenant_release_package.py" -o -name "platform_advisor_structured_solution_bundles_v1.sql" \) -print'
echo '[target-without-secret]'
docker exec whieda-shared-staging-api python -c 'import os, urllib.parse; value=urllib.parse.urlparse(os.environ["PLATFORM_DATABASE_URL"]); print({"host":value.hostname,"port":value.port,"database":value.path.lstrip("/"),"user":value.username})'
echo '[staged-gate-m-media]'
docker exec whieda-shared-staging-api sh -lc 'for root in /tmp/whieda-gate-m-*; do [ -f "$root/media-manifest.tsv" ] || continue; echo "$root"; head -n 2 "$root/media-manifest.tsv"; find "$root/media-source" -type f 2>/dev/null | wc -l; done'
"""
    _, stdout, stderr = client.exec_command(command, timeout=45)
    status = stdout.channel.recv_exit_status()
    if status:
        raise SystemExit(stderr.read().decode("utf-8", errors="replace"))
    print(stdout.read().decode("utf-8", errors="replace"))
finally:
    client.close()
