"""Preserve the canonical Telegram identity while routing lead commands."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"


def load_helper():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load n8n helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    helper = load_helper()
    session = helper.login_session()
    response = session.get(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-before-leads-context-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    router = next(node for node in workflow["nodes"] if node["name"] == "Code: Route /leads")
    old = router["parameters"]["jsCode"]
    old_prefix = "const input = $input.first().json;\nconst text = String(input.message_text || '').trim();"
    new_prefix = "const runtime = $input.first().json;\nconst envelope = $('Code: Normalize Payload').first().json;\nconst input = { ...runtime, ...envelope };\nconst text = String(input.message_text || '').trim();"
    if old_prefix not in old:
        raise RuntimeError("Expected /leads router prefix not found; workflow was not changed")
    router["parameters"]["jsCode"] = old.replace(old_prefix, new_prefix)

    saved = session.patch(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return versionId")
    session.post(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60).raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    helper.ssh_run("docker restart n8n-n8n-1")
    helper.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
