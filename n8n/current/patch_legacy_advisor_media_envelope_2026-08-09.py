"""P0 repair: prevent the legacy public advisor from returning the full media catalog.

The Website API response node must serialize only the resource rows selected by
the structured lookup.  The previous version serialized the raw Postgres input,
which contained every active resource in the catalog.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import datetime
from pathlib import Path

import paramiko


WORKFLOW_ID = "advisor-whieda-phase1"
TARGET_NODE = "Code: Format Website API Response"
OLD = "if (Array.isArray(source.resources)) {"
NEW = "const scopedResources = Array.isArray(source.structured_resources) ? source.structured_resources : [];\nif (Array.isArray(scopedResources)) {"
OLD_LOOP = "for (const item of source.resources) {"
NEW_LOOP = "for (const item of scopedResources) {"

HELPER = Path(__file__).with_name("publish_and_run_whieda_sync_2026-07-13.py")
spec = importlib.util.spec_from_file_location("n8n_helper", HELPER)
helper = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(helper)
from whieda_runtime_env import ssh_config


def psql(sql: str) -> str:
    return helper.ssh_run(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At -c "'
        + sql.replace('"', '\\"')
        + '"'
    )


def apply_large_sql(sql: str) -> str:
    """Run a large workflow update from a UTF-8 file, not an SSH command line."""
    cfg = ssh_config()
    remote_host_path = "/tmp/whieda_media_envelope_patch.sql"
    remote_container_path = "/tmp/whieda_media_envelope_patch.sql"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".sql") as handle:
        handle.write(sql)
        local_path = Path(handle.name)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            cfg["host"], username=cfg["user"], password=cfg["password"],
            look_for_keys=False, allow_agent=False, timeout=30,
        )
        sftp = client.open_sftp()
        try:
            sftp.put(str(local_path), remote_host_path)
        finally:
            sftp.close()
        command = (
            f"docker cp {remote_host_path} n8n-postgres-1:{remote_container_path} "
            f"&& docker exec n8n-postgres-1 psql -U n8n -d n8n -At -f {remote_container_path} "
            f"&& rm -f {remote_host_path}"
        )
        _, stdout, stderr = client.exec_command(command, timeout=180)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if code != 0:
            raise RuntimeError(f"large SQL failed ({code}): {out}\n{err}")
        return out
    finally:
        local_path.unlink(missing_ok=True)
        client.close()


def main() -> None:
    raw = psql(
        "select jsonb_build_object('version_id', h.\"versionId\", 'nodes', h.nodes, "
        "'connections', h.connections)::text from workflow_history h "
        "join workflow_entity e on e.id=h.\"workflowId\" and e.\"activeVersionId\"=h.\"versionId\" "
        f"where h.\"workflowId\"='{WORKFLOW_ID}';"
    )
    live = json.loads(raw)
    node = next((item for item in live["nodes"] if item.get("name") == TARGET_NODE), None)
    if not node:
        raise RuntimeError(f"Missing node: {TARGET_NODE}")
    code = str(node.get("parameters", {}).get("jsCode", ""))
    if OLD not in code or OLD_LOOP not in code:
        raise RuntimeError("Expected vulnerable media envelope is not present; no change made")
    if "scopedResources" in code:
        raise RuntimeError("Scoped media guard already present; no change made")

    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-phase1-before-media-envelope-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    node["parameters"]["jsCode"] = code.replace(OLD, NEW, 1).replace(OLD_LOOP, NEW_LOOP, 1)
    nodes_json = json.dumps(live["nodes"], ensure_ascii=False, separators=(",", ":"))
    changed = apply_large_sql(
        "update workflow_history set nodes="
        f"'{nodes_json.replace(chr(39), chr(39) + chr(39))}'::jsonb, \"updatedAt\"=now() "
        f"where \"workflowId\"='{WORKFLOW_ID}' and \"versionId\"='{live['version_id']}' "
        'returning "versionId";'
    )
    if live["version_id"] not in changed:
        raise RuntimeError("Published workflow update did not confirm")

    helper.ssh_run("docker restart n8n-n8n-1")
    helper.wait_for_n8n_ready(timeout_seconds=120)
    print(json.dumps({"status": "applied", "version_id": live["version_id"], "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
