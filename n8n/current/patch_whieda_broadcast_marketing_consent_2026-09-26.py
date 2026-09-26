"""WHIEDA Broadcast Delivery Worker: send broadcasts only to people who opted in.

38-ФЗ ст. 18 (owner, 26.09.2026): advertising in the bot goes only to those who
pressed «Хочу получать новости и предложения» (or typed /news_on). Core writes
that answer to `telegram_marketing_consents` (migration
postgres/sql/platform_marketing_consent_v17.sql, one row per person, opted_in
true/false). A consultant's personal reply to a lead is not a broadcast.

Before this patch the recipient list came from the legacy flag
`advisor_telegram_subscriptions.is_subscribed` (set by /subscribe in the old
n8n advisor, default false — it cannot tell «never asked» from «refused»).
After the patch the recipient list is:

    advisor_telegram_subscriptions s   (chat to send to, blocked_at IS NULL)
    JOIN advisor_structured_users_access a   (audience: candidates / partners / leaders)
    JOIN telegram_marketing_consents mc      (opted_in IS TRUE — the consent itself)

`is_subscribed` no longer decides: the only valid consent is the explicit one.
The owner's count preview in the advisor workflow («*_subscriber_count») still
counts by `is_subscribed`; that is a display number, patch it separately if needed.

Usage:
    python patch_whieda_broadcast_marketing_consent_2026-09-26.py --snapshot <before.json> --out <after.json>
        offline: patch a saved workflow JSON, write the result, print the diff (no network)
    python patch_whieda_broadcast_marketing_consent_2026-09-26.py --dry-run
        fetch the live workflow (by name), back it up, print the diff, change nothing
    python patch_whieda_broadcast_marketing_consent_2026-09-26.py
        fetch, back up, patch, save, activate, publish
"""

from __future__ import annotations

import copy
import difflib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_NAME = "WHIEDA Broadcast Delivery Worker"
QUEUE = "Postgres: Queue Broadcast Deliveries"

SQL_ANCHOR = (
    "  JOIN advisor_structured_users_access a ON a.client_id = s.client_id AND a.telegram_user_id = s.telegram_user_id\n"
    "  WHERE s.is_subscribed IS TRUE AND s.blocked_at IS NULL\n"
)
SQL_INSERT = (
    "  JOIN advisor_structured_users_access a ON a.client_id = s.client_id AND a.telegram_user_id = s.telegram_user_id\n"
    "  JOIN telegram_marketing_consents mc ON mc.tenant_id = s.client_id AND mc.telegram_user_id::text = s.telegram_user_id AND mc.opted_in IS TRUE\n"
    "  WHERE s.blocked_at IS NULL\n"
)


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node(workflow: dict, name: str) -> dict:
    for item in workflow["nodes"]:
        if item["name"] == name:
            return item
    raise KeyError(name)


def patch_workflow(workflow: dict) -> dict:
    wf = copy.deepcopy(workflow)
    queue = node(wf, QUEUE)
    query = queue["parameters"]["query"]
    if "telegram_marketing_consents" in query:
        raise RuntimeError("already patched: telegram_marketing_consents present")
    if query.count(SQL_ANCHOR) != 1:
        raise RuntimeError(f"{QUEUE}: recipient WHERE not found exactly once, refusing to patch")
    queue["parameters"]["query"] = query.replace(SQL_ANCHOR, SQL_INSERT)
    return wf


def diff(before: dict, after: dict) -> str:
    old = node(before, QUEUE)["parameters"]["query"].splitlines(keepends=True)
    new = node(after, QUEUE)["parameters"]["query"].splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile=f"{QUEUE} (before)", tofile=f"{QUEUE} (after)", n=2))


def _arg(flag: str) -> str | None:
    args = sys.argv[1:]
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    return None


def main() -> None:
    snapshot = _arg("--snapshot")
    if snapshot:
        live = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        patched = patch_workflow(live)
        out = _arg("--out")
        if out:
            Path(out).write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
        print(diff(live, patched))
        print(json.dumps({"snapshot": snapshot, "out": out, "dry_run": True}, ensure_ascii=False))
        return

    dry_run = "--dry-run" in sys.argv[1:]
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    session = helper.login_session()
    base = helper.BASE_URL

    listing = session.get(f"{base}/rest/workflows?limit=200", verify=False, timeout=30)
    listing.raise_for_status()
    found = [item for item in listing.json().get("data", []) if item.get("name") == WORKFLOW_NAME]
    if len(found) != 1:
        raise RuntimeError(f"expected exactly one workflow named {WORKFLOW_NAME!r}, found {len(found)}")
    workflow_id = found[0]["id"]

    current = session.get(f"{base}/rest/workflows/{workflow_id}", verify=False, timeout=30)
    current.raise_for_status()
    live = current.json()["data"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BASE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"whieda-broadcast-delivery-before-marketing-consent-{stamp}.json"
    backup_path.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    patched = patch_workflow(live)
    print(diff(live, patched))
    summary = {"workflow_id": workflow_id, "backup": str(backup_path), "dry_run": dry_run}
    if dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    body = {
        "name": patched["name"],
        "nodes": patched["nodes"],
        "connections": patched["connections"],
        "settings": patched.get("settings") or {},
        "versionId": live.get("versionId"),
    }
    save = session.patch(f"{base}/rest/workflows/{workflow_id}", json=body, verify=False, timeout=120)
    save.raise_for_status()
    saved = save.json().get("data", save.json())
    version_id = saved.get("versionId")
    if not version_id:
        refreshed = session.get(f"{base}/rest/workflows/{workflow_id}", verify=False, timeout=30)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    activation = session.post(
        f"{base}/rest/workflows/{workflow_id}/activate",
        json={"versionId": version_id},
        verify=False,
        timeout=60,
    )
    activation.raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
    summary["version_id"] = version_id
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
