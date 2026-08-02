"""Role-aware Telegram main menu callbacks (v1 schema)."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"

MENU_JS = r"""const input = $('Code: Normalize Payload').first().json;
const text = String(input.message_text || '').trim().toLowerCase();
const callback = String(input.callback_data || '').trim();
const isMenu = text === '/menu' || text === 'меню' || callback === 'v1:menu:open:root';
const match = callback.match(/^v1:([a-z_]+):([a-z_]+)(?::([a-z0-9_-]+))?$/i);
const service = match?.[1] || '';
const action = match?.[2] || '';
const entity = match?.[3] || '';
const roles = Array.isArray(input.actor_roles) ? input.actor_roles : [];
const isLeader = roles.some(r => ['platform_owner', 'tenant_admin', 'market_admin', 'leader'].includes(String(r)));
const buttons = [
  [{ text: 'Товары и цены', callback_data: 'v1:catalog:open:root' }],
  [{ text: 'Собрать корзину', callback_data: 'v1:basket:start:root' }],
  [{ text: 'Акции', callback_data: 'v1:promotions:list:root' }],
  [{ text: 'Мероприятия', callback_data: 'v1:events:list:root' }],
  [{ text: 'Мои заявки', callback_data: 'v1:leads:list:mine' }],
  [{ text: 'Помощь', callback_data: 'v1:help:open:root' }],
];
if (isLeader) {
  buttons.push([{ text: 'Рассылка лидерам', callback_data: 'v1:broadcast:preview:root' }]);
}
const menuText = 'Выберите сервис. Можно вернуться сюда командой /menu.';
return [{ json: {
  ...input,
  menu_requested: isMenu,
  callback_service: service,
  callback_action: action,
  callback_entity: entity,
  callback_valid: Boolean(match),
  menu_text: menuText,
  menu_reply_markup: { inline_keyboard: buttons },
} }];"""

ROUTER_PATCH = r"""const row = $input.first().json;
const text = String(row.message_text || '').trim().toLowerCase();
const callback = String(row.callback_data || '').trim();
const openMenu = text === '/menu' || text === 'меню' || callback === 'v1:menu:open:root';
return [{ json: { ...row, route_menu_v1: openMenu } }];"""


def load_helper():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("h", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_node(workflow: dict, *, node_id: str, name: str, js_code: str, position: list[int]) -> None:
    nodes = workflow.get("nodes") or []
    for node in nodes:
        if node.get("id") == node_id or node.get("name") == name:
            node["parameters"] = {"jsCode": js_code}
            node["type"] = "n8n-nodes-base.code"
            node["typeVersion"] = 2
            return
    nodes.append(
        {
            "parameters": {"jsCode": js_code},
            "id": node_id,
            "name": name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": position,
        }
    )
    workflow["nodes"] = nodes


def wire_after_normalize(workflow: dict) -> None:
    connections = workflow.setdefault("connections", {})
    normalize_name = None
    for node in workflow.get("nodes", []):
        if node.get("name") == "Code: Normalize Payload":
            normalize_name = node["name"]
            break
    if not normalize_name:
        raise RuntimeError("Code: Normalize Payload node not found")
    menu_name = "Code: Role-aware Menu v1"
    ensure_node(workflow, node_id="menu-v1", name=menu_name, js_code=MENU_JS, position=[-3600, 200])
    downstream = connections.get(normalize_name, {}).get("main", [[]])[0]
    if not any(edge.get("node") == menu_name for edge in downstream):
        connections[normalize_name] = {"main": [[{"node": menu_name, "type": "main", "index": 0}]]}
        if downstream:
            connections[menu_name] = {"main": [downstream]}


def main() -> None:
    helpers = load_helper()
    session = helpers.login_session()
    backup_dir = BASE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    current = session.get(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=120).json()["data"]
    backup = backup_dir / f"advisor-menu-v1-before-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    workflow = current
    wire_after_normalize(workflow)
    saved = session.patch(
        f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}",
        json=workflow,
        verify=False,
        timeout=180,
    )
    saved.raise_for_status()
    data = saved.json().get("data", saved.json())
    version = data.get("versionId")
    session.post(
        f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version},
        verify=False,
        timeout=60,
    ).raise_for_status()
    helpers.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "menu_node": "Code: Role-aware Menu v1"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
