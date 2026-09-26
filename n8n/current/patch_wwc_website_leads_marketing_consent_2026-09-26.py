"""WWC Website Leads: carry the «news and offers» checkbox into website_leads.

38-ФЗ ст. 18 (owner, 26.09.2026): the site form has a second, optional checkbox
«Хочу получать новости и предложения» and sends `marketing_consent: true/false`
in the lead body. Before this patch the workflow dropped the field: the Code
node copies only the fields it knows, and the INSERT has no such column.

After the patch:
  * «Code: validate and assign owner» outputs `marketing_consent` — only an
    explicit yes (true / 'true' / '1' / 1 / 'on' / 'yes') counts, unchecked → false;
  * «Postgres: save lead and audit» writes `marketing_consent` and
    `marketing_consent_at` (now() on yes, NULL otherwise).

Requires Core migration postgres/sql/platform_marketing_consent_v17.sql applied
to the same database first — otherwise every lead INSERT fails on the missing
column. Nothing else in the workflow changes.

Usage:
    python patch_wwc_website_leads_marketing_consent_2026-09-26.py --snapshot <before.json> --out <after.json>
        offline: patch a saved workflow JSON, write the result, print the diff (no network)
    python patch_wwc_website_leads_marketing_consent_2026-09-26.py --dry-run
        fetch the live workflow, back it up, print the diff, change nothing
    python patch_wwc_website_leads_marketing_consent_2026-09-26.py
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
WORKFLOW_ID = "wwc-website-leads-p0"

VALIDATE = "Code: validate and assign owner"
SAVE = "Postgres: save lead and audit"

# --- Code node -------------------------------------------------------------
JS_ANCHOR = "const honey = clean(body.website, 160);"
JS_INSERT = (
    "const honey = clean(body.website, 160);\n"
    "// Галочка «Хочу получать новости и предложения» (38-ФЗ ст. 18, 26.09.2026):\n"
    "// только явное «да», снятая галочка или чужое значение — false.\n"
    "const marketingConsent = [true, 'true', '1', 1, 'on', 'yes'].includes(body.marketing_consent);"
)
JS_OUT_ANCHOR = "  idempotency_key: idempotency,\n"
JS_OUT_INSERT = "  idempotency_key: idempotency,\n  marketing_consent: marketingConsent,\n"

# --- Postgres node ---------------------------------------------------------
SQL_COLUMNS_ANCHOR = "service_location_id, country_code, city, idempotency_key, metadata\n  )"
SQL_COLUMNS_INSERT = (
    "service_location_id, country_code, city, idempotency_key, metadata,\n"
    "    marketing_consent, marketing_consent_at\n  )"
)
SQL_VALUES_ANCHOR = "\n  FROM routing\n  LEFT JOIN service_route ON true"
SQL_VALUES_INSERT = (
    ",\n    {{ $json.marketing_consent === true ? 'true' : 'false' }}::boolean,\n"
    "    {{ $json.marketing_consent === true ? 'now()' : 'NULL' }}"
    "\n  FROM routing\n  LEFT JOIN service_route ON true"
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


def _replace_once(text: str, anchor: str, replacement: str, where: str) -> str:
    if text.count(anchor) != 1:
        raise RuntimeError(f"{where}: anchor found {text.count(anchor)} times, refusing to patch: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


def patch_workflow(workflow: dict) -> dict:
    wf = copy.deepcopy(workflow)
    validate = node(wf, VALIDATE)
    save = node(wf, SAVE)
    js = validate["parameters"]["jsCode"]
    query = save["parameters"]["query"]
    if "marketing_consent" in js or "marketing_consent" in query:
        raise RuntimeError("already patched: marketing_consent present")
    js = _replace_once(js, JS_ANCHOR, JS_INSERT, VALIDATE)
    js = _replace_once(js, JS_OUT_ANCHOR, JS_OUT_INSERT, VALIDATE)
    query = _replace_once(query, SQL_COLUMNS_ANCHOR, SQL_COLUMNS_INSERT, SAVE)
    query = _replace_once(query, SQL_VALUES_ANCHOR, SQL_VALUES_INSERT, SAVE)
    validate["parameters"]["jsCode"] = js
    save["parameters"]["query"] = query
    return wf


def diff(before: dict, after: dict) -> str:
    out: list[str] = []
    for name, key in ((VALIDATE, "jsCode"), (SAVE, "query")):
        old = node(before, name)["parameters"][key].splitlines(keepends=True)
        new = node(after, name)["parameters"][key].splitlines(keepends=True)
        out.extend(difflib.unified_diff(old, new, fromfile=f"{name} (before)", tofile=f"{name} (after)", n=2))
    return "".join(out)


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

    current = session.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
    current.raise_for_status()
    live = current.json()["data"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BASE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"wwc-website-leads-p0-before-marketing-consent-{stamp}.json"
    backup_path.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    patched = patch_workflow(live)
    print(diff(live, patched))
    summary = {"backup": str(backup_path), "dry_run": dry_run}
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
    save = session.patch(f"{base}/rest/workflows/{WORKFLOW_ID}", json=body, verify=False, timeout=120)
    save.raise_for_status()
    saved = save.json().get("data", save.json())
    version_id = saved.get("versionId")
    if not version_id:
        refreshed = session.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    activation = session.post(
        f"{base}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version_id},
        verify=False,
        timeout=60,
    )
    activation.raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    summary["version_id"] = version_id
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
