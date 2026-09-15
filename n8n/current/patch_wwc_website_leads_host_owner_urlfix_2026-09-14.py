"""Дополнение к patch_wwc_website_leads_host_owner_2026-09-14.py.

Проверка тестовой заявкой L-4A6B801AFE (execution 30862): владелец выбран
верно (viktor, а не makarova), но host_ref_code пришёл пустым при
page_url=https://olesya.wwc.best/#contact. Причина: в песочнице Code-узла n8n
нет глобального URL, `new URL(...)` бросает исключение, catch отдаёт ''.
Старый helper refFromPageUrl в том же узле сломан так же — просто на него
никто не опирался.

Хост парсится регуляркой, без URL.

Usage:
    python patch_wwc_website_leads_host_owner_urlfix_2026-09-14.py --dry-run
    python patch_wwc_website_leads_host_owner_urlfix_2026-09-14.py
"""
from __future__ import annotations
import copy, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "wwc-website-leads-p0"
VALIDATE = "Code: validate and assign owner"

OLD = """const hostRefFromPageUrl = (value) => {
  try {
    const url = new URL(String(value || ''), 'https://wwc.best');
    const host = url.hostname.toLowerCase();
    if (!host.endsWith('.wwc.best') || host === 'wwc.best' || host === 'www.wwc.best') return '';"""
NEW = """const hostRefFromPageUrl = (value) => {
  try {
    // Без new URL: в песочнице Code-узла его нет, исключение уходило в catch
    // и host_ref_code всегда был пустым.
    const m = /^https?:\/\/([^\/?#:]+)/i.exec(String(value || '').trim());
    const host = m ? m[1].toLowerCase() : '';
    if (!host.endsWith('.wwc.best') || host === 'wwc.best' || host === 'www.wwc.best') return '';"""

def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def node(wf, name):
    for n in wf["nodes"]:
        if n["name"] == name: return n
    raise KeyError(name)

def patch_workflow(workflow):
    wf = copy.deepcopy(workflow)
    code = node(wf, VALIDATE)["parameters"]["jsCode"]
    if code.count(OLD) != 1:
        raise RuntimeError(f"expected exactly one URL-based helper, found {code.count(OLD)}")
    node(wf, VALIDATE)["parameters"]["jsCode"] = code.replace(OLD, NEW, 1)
    return wf

def main():
    dry = "--dry-run" in sys.argv[1:]
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    s = helper.login_session(); base = helper.BASE_URL
    live = s.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30); live.raise_for_status(); live = live.json()["data"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bp = BASE.parent / "backups" / f"wwc-website-leads-p0-before-host-owner-urlfix-{stamp}.json"
    bp.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")
    patched = patch_workflow(live)
    if dry:
        print(json.dumps({"backup": str(bp), "dry_run": True}, ensure_ascii=False, indent=2)); return
    body = {"name": patched["name"], "nodes": patched["nodes"], "connections": patched["connections"],
            "settings": patched.get("settings") or {}, "versionId": live.get("versionId")}
    r = s.patch(f"{base}/rest/workflows/{WORKFLOW_ID}", json=body, verify=False, timeout=120); r.raise_for_status()
    vid = r.json().get("data", r.json()).get("versionId") or s.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30).json()["data"].get("versionId")
    a = s.post(f"{base}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": vid}, verify=False, timeout=60); a.raise_for_status()
    # activate через REST делает версию активной (activeVersionId) — проверено
    # 14.09.2026; SSH-шаг publish:workflow здесь не нужен.
    print(json.dumps({"backup": str(bp), "version_id": vid}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
