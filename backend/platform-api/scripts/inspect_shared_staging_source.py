from __future__ import annotations
import sys
from pathlib import Path
import paramiko
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "n8n" / "current"))
from whieda_runtime_env import ssh_config
config = ssh_config()
client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(hostname=config["host"], username=config["user"], password=config["password"], look_for_keys=False, allow_agent=False, timeout=30)
try:
    command = r'''set -e
printf '[candidate-source-roots]\n'
for root in /opt/whieda-platform-core /srv /app; do
  [ -d "$root" ] && find "$root" -maxdepth 5 -type f \( -name run_shared_staging_tenant_canary.py -o -name run_tenant_release_package.py \) -print
done
printf '[staging-compose]\n'
find /opt/whieda-platform-core -maxdepth 4 -type f \( -name '*staging*.yml' -o -name 'docker-compose*.yml' \) -print 2>/dev/null | sort
printf '[staging-container-image]\n'
docker inspect --format '{{.Config.Image}}' whieda-shared-staging-api
'''
    _, stdout, stderr = client.exec_command(command, timeout=45)
    status=stdout.channel.recv_exit_status()
    if status: raise SystemExit(stderr.read().decode('utf-8', errors='replace'))
    print(stdout.read().decode('utf-8', errors='replace'))
finally:
    client.close()