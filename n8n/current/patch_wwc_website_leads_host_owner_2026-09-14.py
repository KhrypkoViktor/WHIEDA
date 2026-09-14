"""WWC Website Leads: партнёрский хост — единственный источник владельца лида.

Инцидент 2026-09-14. Заявка, отправленная с olesya.wwc.best, пришла Марине
(makarova). Цепочка:

1. Сайт прислал active_ref='olesya' — это ref_code сайта, он же поддомен.
2. В runtime referral_profiles такой строки нет: партнёрша заведена как
   'olesya-vselennaya' (page_mode standard_ref, без поддомена). active_profile
   не нашёлся.
3. Запрос владельца падал в COALESCE(active_profile, first_profile, 'viktor').
   Cookie first_ref живёт на домене .wwc.best, то есть общая для всех
   поддоменов; у отправителя она была 'makarova' с прошлого визита.
4. assigned_owner_id = makarova. Лид ушёл не тому партнёру, молча.

Канон (коммит a4abf8e): владелец лида — active_ref, first_ref — только
атрибуция. Запрос канону противоречил: first_ref участвовал в выборе
владельца.

После патча:
- Если заявка пришла с партнёрского хоста <sub>.wwc.best, владелец — партнёр
  этого хоста. Cookie не спрашивается вообще.
- Если хост партнёрский, но runtime его не знает — лид уходит владельцу
  корня ('viktor') с пометкой routing_error='unknown_host_ref:<sub>' в
  metadata. Не «кому попало», а видимо и проверяемо.
- На корне wwc.best владелец — active_ref, если известен; иначе 'viktor'.
  first_ref из выбора владельца исключён.
- Правило про harold (сервисный центр Минска не получает не-BY лиды)
  сохранено и применяется после выбора владельца.

Usage:
    python patch_wwc_website_leads_host_owner_2026-09-14.py --dry-run
    python patch_wwc_website_leads_host_owner_2026-09-14.py
    python patch_wwc_website_leads_host_owner_2026-09-14.py --offline <backup.json>
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "wwc-website-leads-p0"

VALIDATE = "Code: validate and assign owner"
SAVE = "Postgres: save lead and audit"

ROUTING_VERSION_OLD = "routing_version: 'p0.4-site-owner'"
ROUTING_VERSION_NEW = "routing_version: 'p0.5-host-authoritative'"

# --- Code node: отдельно вычисляем ref по хосту, без query-строки ------------
CODE_ANCHOR = "const fromUrl = refFromPageUrl(body.page_url || body.landing_url || '');"
CODE_INSERT = CODE_ANCHOR + """
// Ref по хосту страницы, БЕЗ учёта ?ref= в адресе: на партнёрском поддомене
// владелец лида — партнёр этого поддомена, что бы ни лежало в cookie.
const hostRefFromPageUrl = (value) => {
  try {
    const url = new URL(String(value || ''), 'https://wwc.best');
    const host = url.hostname.toLowerCase();
    if (!host.endsWith('.wwc.best') || host === 'wwc.best' || host === 'www.wwc.best') return '';
    const sub = host.split('.')[0];
    if (sub === 'www' || sub === 'staging' || sub === 'api' || sub === 'admin') return '';
    if (sub === 'elena') return 'onlineelena';
    if (sub === 'samtsova') return 'olga-samtsova';
    return sub;
  } catch (e) {
    return '';
  }
};
const hostRefCode = hostRefFromPageUrl(body.page_url || body.landing_url || '');"""

CODE_OUT_ANCHOR = "  active_ref_code: activeRefCode,"
CODE_OUT_INSERT = CODE_OUT_ANCHOR + "\n  host_ref_code: hostRefCode,"

# --- SQL: supplied получает host_ref -----------------------------------------
SQL_SUPPLIED_OLD = """    '{{ String($json.country_code).replace(/'/g, '') }}'::text AS requested_country
), routing AS ("""
SQL_SUPPLIED_NEW = """    '{{ String($json.country_code).replace(/'/g, '') }}'::text AS requested_country,
    '{{ String($json.host_ref_code || '').replace(/'/g, '') }}'::text AS host_ref
), routing AS ("""

# --- SQL: владелец выбирается по хосту, first_ref в выборе не участвует -------
SQL_OWNER_OLD = """    CASE WHEN supplied.requested_country <> 'BY' AND COALESCE(active_profile.owner_id, first_profile.owner_id) = 'harold' THEN 'viktor' ELSE COALESCE(active_profile.owner_id, first_profile.owner_id, 'viktor') END AS attributed_owner_id,
    CASE WHEN supplied.requested_country <> 'BY' AND COALESCE(active_profile.owner_id, first_profile.owner_id) = 'harold' THEN 'viktor' ELSE COALESCE(active_profile.owner_id, first_profile.owner_id, 'viktor') END AS assigned_owner_id,
    supplied.requested_country,
    first_profile.profile_version AS ref_profile_version
  FROM supplied"""
SQL_OWNER_NEW = """    owner.owner_id AS attributed_owner_id,
    owner.owner_id AS assigned_owner_id,
    owner.routing_error,
    supplied.requested_country,
    first_profile.profile_version AS ref_profile_version
  FROM supplied"""

SQL_JOIN_OLD = """  LEFT JOIN referral_profiles active_profile
    ON active_profile.ref_code = supplied.active_ref
   AND active_profile.tenant_id = '{{ String($json.tenant_id).replace(/'/g, '') }}'
   AND active_profile.enabled = true
), service_route AS ("""
SQL_JOIN_NEW = """  LEFT JOIN referral_profiles active_profile
    ON active_profile.ref_code = supplied.active_ref
   AND active_profile.tenant_id = '{{ String($json.tenant_id).replace(/'/g, '') }}'
   AND active_profile.enabled = true
  LEFT JOIN referral_profiles host_profile
    ON host_profile.ref_code = supplied.host_ref
   AND host_profile.tenant_id = '{{ String($json.tenant_id).replace(/'/g, '') }}'
   AND host_profile.enabled = true
  CROSS JOIN LATERAL (
    SELECT
      CASE WHEN raw.owner_id = 'harold' AND supplied.requested_country <> 'BY' THEN 'viktor' ELSE raw.owner_id END AS owner_id,
      raw.routing_error
    FROM (
      SELECT
        CASE
          WHEN host_profile.owner_id IS NOT NULL THEN host_profile.owner_id
          WHEN supplied.host_ref <> '' THEN 'viktor'
          WHEN active_profile.owner_id IS NOT NULL THEN active_profile.owner_id
          ELSE 'viktor'
        END AS owner_id,
        CASE
          WHEN supplied.host_ref <> '' AND host_profile.owner_id IS NULL THEN 'unknown_host_ref:' || supplied.host_ref
          WHEN supplied.host_ref = '' AND supplied.active_ref <> '' AND active_profile.owner_id IS NULL THEN 'unknown_active_ref:' || supplied.active_ref
          ELSE NULL
        END AS routing_error
    ) raw
  ) owner
), service_route AS ("""

# --- SQL: routing_error попадает в metadata, чтобы его было видно ------------
SQL_META_OLD = "    '{{ JSON.stringify($json.metadata).replace(/'/g, '') }}'::jsonb\n  FROM routing"
SQL_META_NEW = (
    "    ('{{ JSON.stringify($json.metadata).replace(/'/g, '') }}'::jsonb"
    " || jsonb_strip_nulls(jsonb_build_object('routing_error', routing.routing_error)))\n  FROM routing"
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


def replace_once(text: str, old: str, new: str, what: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{what}: expected exactly one match, found {count}; refusing to patch")
    return text.replace(old, new, 1)


def patch_workflow(workflow: dict) -> dict:
    wf = copy.deepcopy(workflow)

    code = node(wf, VALIDATE)["parameters"]["jsCode"]
    if "host_ref_code" in code:
        raise RuntimeError("already patched: host_ref_code present in validate node")
    code = replace_once(code, CODE_ANCHOR, CODE_INSERT, "code: host ref helper")
    code = replace_once(code, CODE_OUT_ANCHOR, CODE_OUT_INSERT, "code: host_ref_code output")
    code = replace_once(code, ROUTING_VERSION_OLD, ROUTING_VERSION_NEW, "code: routing_version")
    node(wf, VALIDATE)["parameters"]["jsCode"] = code

    query = node(wf, SAVE)["parameters"]["query"]
    if "host_profile" in query:
        raise RuntimeError("already patched: host_profile present in save query")
    query = replace_once(query, SQL_SUPPLIED_OLD, SQL_SUPPLIED_NEW, "sql: supplied.host_ref")
    query = replace_once(query, SQL_OWNER_OLD, SQL_OWNER_NEW, "sql: owner columns")
    query = replace_once(query, SQL_JOIN_OLD, SQL_JOIN_NEW, "sql: host join + owner lateral")
    query = replace_once(query, SQL_META_OLD, SQL_META_NEW, "sql: metadata routing_error")
    node(wf, SAVE)["parameters"]["query"] = query
    return wf


def _owner_block(query: str) -> str:
    """Только блок выбора владельца: first_profile легально живёт выше, в атрибуции."""
    start = query.index("CROSS JOIN LATERAL")
    end = query.index(") owner", start)
    return query[start:end]


def offline(path: str) -> None:
    live = json.loads(Path(path).read_text(encoding="utf-8"))
    live = live.get("workflow", live)
    patched = patch_workflow(live)
    print(json.dumps({"offline": path, "patched": True,
                      "validate_has_host_ref": "host_ref_code" in node(patched, VALIDATE)["parameters"]["jsCode"],
                      "save_has_host_profile": "host_profile" in node(patched, SAVE)["parameters"]["query"],
                      "owner_block_uses_first_ref": "first_profile" in _owner_block(node(patched, SAVE)["parameters"]["query"])},
                     ensure_ascii=False, indent=2))


def main() -> None:
    args = sys.argv[1:]
    if "--offline" in args:
        offline(args[args.index("--offline") + 1])
        return
    dry_run = "--dry-run" in args
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    session = helper.login_session()
    base = helper.BASE_URL

    current = session.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
    current.raise_for_status()
    live = current.json()["data"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BASE.parent / "backups" / f"wwc-website-leads-p0-before-host-owner-{stamp}.json"
    backup_path.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    patched = patch_workflow(live)
    summary = {"backup": str(backup_path), "dry_run": dry_run,
               "routing_version": ROUTING_VERSION_NEW.split("'")[1]}
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
    activation = session.post(f"{base}/rest/workflows/{WORKFLOW_ID}/activate",
                              json={"versionId": version_id}, verify=False, timeout=60)
    activation.raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    summary["version_id"] = version_id
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
