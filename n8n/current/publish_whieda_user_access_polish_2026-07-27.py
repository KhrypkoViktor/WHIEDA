"""Polish the existing WHIEDA access gate without changing its routing."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"


def load_helpers():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    pub = load_helpers()
    session = pub.login_session()
    response = session.get(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-before-user-access-polish-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    node = next(item for item in workflow["nodes"] if item["name"] == "Telegram: Pending Access Reply")
    node["parameters"]["jsonBody"] = "={{ (() => {\n  const access = $('Postgres: Lookup User Access');\n  const status = access.isExecuted ? String(access.first().json.access_status || 'candidate').toLowerCase() : 'candidate';\n  const text = status === 'blocked'\n    ? 'Доступ к советнику сейчас отключён. Если это ошибка, обратитесь к своему лидеру или администратору.'\n    : 'Заявка принята. Доступ к советнику подтвердит администратор; после этого здесь можно будет смотреть товары, цены, фото и материалы.';\n  return { chat_id: $('Code: Normalize Payload').first().json.external_chat_id, text, disable_web_page_preview: true };\n})() }}"

    saved = session.patch(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return versionId")
    activated = session.post(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60)
    activated.raise_for_status()
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    pub.ssh_run("docker restart n8n-n8n-1")
    pub.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
