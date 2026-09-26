"""Патчи n8n «согласие на рассылку» через публичный API (ключ WHIEDA_N8N_API_KEY).

Скрипты исполнителя patch_wwc_website_leads_marketing_consent_2026-09-26.py и
patch_whieda_broadcast_marketing_consent_2026-09-26.py ходят через логин
(publish_and_run_whieda_sync_2026-07-13.login_session) — он упирается в лимит
попыток (429) и умеет перезапускать n8n. Здесь используются только их чистые
функции patch_workflow(); чтение и запись — /api/v1/workflows, как в
publish_crm_lead_card_node.py. n8n не перезапускается.

    python n8n/current/apply_marketing_consent_patches_api.py --dry-run
    python n8n/current/apply_marketing_consent_patches_api.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# n8n_api (публичный API по ключу) живёт в wwc_sql.py корневой копии D:\Projects\WHIEDA.
sys.path.insert(0, "D:/Projects/WHIEDA/n8n/current")
from wwc_sql import _helper, n8n_api  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put(session, api: str, wf: dict, nodes: list, connections: dict) -> dict:
    payload = {"name": wf["name"], "nodes": nodes, "connections": connections, "settings": wf.get("settings") or {}}
    r = session.put(f"{api}/workflows/{wf['id']}", json=payload, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"PUT {wf['id']}: {r.status_code} {r.text[:300]}")
    session.post(f"{api}/workflows/{wf['id']}/activate", timeout=60)
    return session.get(f"{api}/workflows/{wf['id']}", timeout=60).json()


def main() -> int:
    dry = "--dry-run" in sys.argv
    leads = load("patch_wwc_website_leads_marketing_consent_2026-09-26")
    bcast = load("patch_whieda_broadcast_marketing_consent_2026-09-26")
    session, api = n8n_api(_helper())

    # A. Заявки
    wf = session.get(f"{api}/workflows/{leads.WORKFLOW_ID}", timeout=60).json()
    (HERE / "wwc_website_leads_p0_before_marketing_consent.json").write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    patched = leads.patch_workflow(json.loads(json.dumps(wf)))
    print("=== A. заявки:", wf["name"])
    print(leads.diff(wf, patched))
    if not dry:
        after = put(session, api, wf, patched["nodes"], patched["connections"])
        print("A: active =", after.get("active"))

    # B. Рассылка
    listing = session.get(f"{api}/workflows", params={"limit": 200}, timeout=60).json().get("data", [])
    found = [w for w in listing if w.get("name") == bcast.WORKFLOW_NAME]
    if len(found) != 1:
        raise SystemExit(f"workflow {bcast.WORKFLOW_NAME!r}: найдено {len(found)}")
    wf2 = session.get(f"{api}/workflows/{found[0]['id']}", timeout=60).json()
    (HERE / "whieda_broadcast_delivery_before_marketing_consent.json").write_text(json.dumps(wf2, ensure_ascii=False, indent=1), encoding="utf-8")
    patched2 = bcast.patch_workflow(json.loads(json.dumps(wf2)))
    print("=== B. рассылка:", wf2["name"], wf2["id"], "active:", wf2.get("active"))
    print(bcast.diff(wf2, patched2))
    if not dry:
        after2 = put(session, api, wf2, patched2["nodes"], patched2["connections"])
        print("B: active =", after2.get("active"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
