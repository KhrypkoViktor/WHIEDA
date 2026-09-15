# -*- coding: utf-8 -*-
"""Structured Sync Cron: вкладка Partners Ref больше не пишет referral_profiles.

Зачем. Вкладка Partners Ref (gid=1733124410) — legacy-источник на 10 строк.
Каждые 15 минут sync делал upsert в referral_profiles и воскрешал
'olesya-vselennaya' (удалён 14.09 при переименовании в 'olesya'), затирая
public_profile теми полями, что в вкладке. Профили партнёров теперь ведутся
из Core (partner subscriptions → runtime) и wwc_sql; вкладка остаётся
источником только для lead_actors (chat id заполняется, если пуст) и ролей.

Патч: в Code-узле «Build Structured Sync SQL» блок INSERT INTO referral_profiles
отключён константой. Бэкап workflow — n8n/backups/.

Запуск: python patch_structured_sync_no_profile_upsert_2026-09-15.py [--dry-run]
"""
from __future__ import annotations
import importlib.util, json, sys, datetime as dt
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("h", BASE / "publish_and_run_whieda_sync_2026-07-13.py")
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)

WORKFLOW_ID = "9roEvXNsDpnwqjzH"
NODE = "Code: Build Structured Sync SQL"
OLD = "  if (profileValues.length) {\n    sql += `\n\nINSERT INTO referral_profiles ("
NEW = ("  // 15.09.2026: Partners Ref — legacy. Профили ведутся из Core и wwc_sql;\n"
       "  // upsert отсюда воскрешал удалённые ref_code и затирал public_profile.\n"
       "  const PARTNERS_REF_WRITES_PROFILES = false;\n"
       "  if (PARTNERS_REF_WRITES_PROFILES && profileValues.length) {\n    sql += `\n\nINSERT INTO referral_profiles (")

def main():
    dry = "--dry-run" in sys.argv
    s = h.login_session(); b = h.BASE_URL
    live = s.get(f"{b}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60).json()["data"]
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = BASE.parent / "backups" / f"structured-sync-before-no-profile-upsert-{stamp}.json"
    backup.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")
    node = next(n for n in live["nodes"] if n["name"] == NODE)
    code = node["parameters"]["jsCode"]
    if "PARTNERS_REF_WRITES_PROFILES" in code:
        print("already patched"); return
    assert code.count(OLD) == 1, "anchor not found"
    node["parameters"]["jsCode"] = code.replace(OLD, NEW)
    print("backup:", backup, "| dry_run:", dry)
    if dry:
        return
    payload = {"name": live["name"], "nodes": live["nodes"], "connections": live["connections"],
               "settings": live.get("settings", {}), "versionId": live.get("versionId")}
    r = s.patch(f"{b}/rest/workflows/{WORKFLOW_ID}", json=payload, verify=False, timeout=60)
    r.raise_for_status()
    saved = r.json()["data"]
    act = s.post(f"{b}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": saved.get("versionId")}, verify=False, timeout=60)
    act.raise_for_status()
    fresh = s.get(f"{b}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60).json()["data"]
    print("active:", fresh.get("active"), "| activeVersionId==versionId:", fresh.get("activeVersionId") == fresh.get("versionId"))

if __name__ == "__main__":
    main()
