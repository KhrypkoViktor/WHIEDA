"""Park the old n8n Telegram execution tail without deleting any nodes.

Telegram now enters Platform Core.  The legacy webhook still returns its 200 ACK
for a fast rollback, but its old post-ACK chain is intentionally disconnected.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


HELPER = Path(__file__).with_name("publish_and_run_whieda_sync_2026-07-13.py")
WORKFLOW_ID = "advisor-whieda-phase1"
SOURCE_NODE = "Respond: 200 ACK"
EXPECTED_TARGET = "Code: Normalize Payload"

spec = importlib.util.spec_from_file_location("n8n_helper", HELPER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {HELPER}")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def psql(sql: str) -> str:
    return helper.ssh_run(
        'docker exec n8n-postgres-1 psql -U n8n -d n8n -At -c "'
        + sql.replace('"', '\\"')
        + '"'
    )


def main() -> None:
    raw = psql(
        "select jsonb_build_object('version_id', h.\"versionId\", 'nodes', h.nodes, "
        "'connections', h.connections)::text from workflow_history h "
        "join workflow_entity e on e.id=h.\"workflowId\" and e.\"activeVersionId\"=h.\"versionId\" "
        f"where h.\"workflowId\"='{WORKFLOW_ID}';"
    )
    live = json.loads(raw)
    source = live["connections"].get(SOURCE_NODE, {})
    current = ((source.get("main") or [[]])[0] or [])
    targets = [item.get("node") for item in current]
    if targets == []:
        print(json.dumps({"status": "already_parked", "version_id": live["version_id"]}, ensure_ascii=False))
        return
    if targets != [EXPECTED_TARGET]:
        raise RuntimeError(f"Expected only {EXPECTED_TARGET!r} after {SOURCE_NODE!r}, got {targets!r}")

    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-phase1-before-legacy-tail-park-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    updated = psql(
        "update workflow_history set connections=jsonb_set(connections::jsonb, "
        f"'{{{SOURCE_NODE},main}}', '[[]]'::jsonb, true), \"updatedAt\"=now() "
        f"where \"workflowId\"='{WORKFLOW_ID}' and \"versionId\"='{live['version_id']}' "
        "returning \"versionId\";"
    )
    if live["version_id"] not in updated:
        raise RuntimeError("Published workflow update did not confirm")

    helper.ssh_run("docker restart n8n-n8n-1")
    helper.wait_for_n8n_ready(timeout_seconds=90)
    print(
        json.dumps(
            {
                "status": "parked",
                "version_id": live["version_id"],
                "detached": f"{SOURCE_NODE} -> {EXPECTED_TARGET}",
                "backup": str(backup),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
