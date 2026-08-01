"""Build and apply Partners_Ref sheet -> lead_actors / referral_profiles runtime sync."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import textwrap
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
SHEET_GID = 1733124410
TENANT_ID = "whieda"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def sql_bool(value: str | bool | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if str(value or "").strip().lower() in {"true", "1", "yes", "y"} else "false"


def display_mode(page_mode: str) -> str:
    return "anonymous" if page_mode.strip().lower() in {"anonymous_ref", "anonymous"} else "named"


def fetch_partners_rows() -> list[dict[str, str]]:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={SHEET_GID}"
    text = requests.get(url, timeout=30).content.decode("utf-8")
    return [row for row in csv.DictReader(io.StringIO(text), delimiter="\t") if (row.get("partner_id") or "").strip()]


def build_sync_sql(rows: list[dict[str, str]]) -> str:
    actor_values: list[str] = []
    role_values: list[str] = []
    profile_values: list[str] = []

    for row in rows:
        actor_id = (row.get("partner_id") or "").strip()
        display_name = (row.get("display_name") or actor_id).strip()
        owner_actor_id = (row.get("owner_actor_id") or actor_id).strip()
        site_type = (row.get("site_type") or "").strip().lower()
        if owner_actor_id != actor_id or site_type == "platform_root":
            telegram_chat_id = None
            telegram_username = None
        else:
            telegram_username = (row.get("telegram_username") or "").strip().lstrip("@") or None
            telegram_chat_id = (row.get("telegram_chat_id") or "").strip() or None
        active = sql_bool(row.get("active"))
        actor_values.append(
            f"({sql_literal(actor_id)}, {sql_literal(TENANT_ID)}, {sql_literal(display_name)}, "
            f"{sql_literal(telegram_chat_id)}, {sql_literal(telegram_username)}, {active})"
        )

        ref_code = (row.get("ref_code") or "").strip()
        page_mode = (row.get("page_mode") or "standard_ref").strip()
        plan_status = (row.get("plan_status") or "").strip().lower()
        enabled = active == "true" and plan_status in {"", "active"}
        if ref_code:
            role_actor_id = owner_actor_id if site_type == "platform_root" else actor_id
            country = (row.get("country") or "GLOBAL").strip() or "GLOBAL"
            if enabled:
                role_values.append(
                    f"({sql_literal(TENANT_ID)}, {sql_literal(role_actor_id)}, 'referral_owner', "
                    f"{sql_literal(country)}, {sql_literal('')}, true)"
                )
            public_profile = {
                "partner_id": actor_id,
                "display_name": display_name,
                "page_mode": page_mode,
                "plan_code": (row.get("plan_code") or "").strip(),
                "plan_status": (row.get("plan_status") or "").strip(),
                "leads_access": (row.get("leads_access") or "").strip(),
                "public_site_url": (row.get("public_site_url") or row.get("ref_url") or "").strip(),
                "site_type": (row.get("site_type") or "").strip(),
                "focus_group": str(row.get("focus_group") or "").strip().upper() == "TRUE",
                "access_tier": (row.get("access_tier") or "").strip(),
                "watcher_actor_id": (row.get("watcher_actor_id") or "").strip(),
                "owner_actor_id": (row.get("owner_actor_id") or actor_id).strip(),
            }
            owner_id = (row.get("owner_actor_id") or actor_id).strip()
            country = (row.get("country") or "").strip() or None
            profile_values.append(
                "("
                f"{sql_literal(ref_code)}, {sql_literal(TENANT_ID)}, {sql_literal(owner_id)}, "
                f"{sql_literal(display_mode(page_mode))}, {sql_literal(json.dumps(public_profile, ensure_ascii=False))}::jsonb, "
                f"{sql_literal(country)}, NULL, {sql_bool(enabled)}"
                ")"
            )

    if not actor_values:
        raise RuntimeError("Partners_Ref sheet returned no partner rows")

    sql = textwrap.dedent(
        f"""
        INSERT INTO lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username, active)
        VALUES
          {",\n  ".join(actor_values)}
        ON CONFLICT (actor_id) DO UPDATE
        SET display_name = excluded.display_name,
            telegram_chat_id = CASE
              WHEN excluded.telegram_chat_id IS NULL THEN lead_actors.telegram_chat_id
              WHEN EXISTS (
                SELECT 1 FROM lead_actors la
                WHERE la.tenant_id = excluded.tenant_id
                  AND la.telegram_chat_id = excluded.telegram_chat_id
                  AND la.actor_id <> excluded.actor_id
              ) THEN lead_actors.telegram_chat_id
              ELSE excluded.telegram_chat_id
            END,
            telegram_username = coalesce(excluded.telegram_username, lead_actors.telegram_username),
            active = excluded.active,
            updated_at = now();
        """
    )

    if role_values:
        sql += textwrap.dedent(
            f"""
            INSERT INTO lead_actor_roles (tenant_id, actor_id, role, country_code, region_code, active)
            VALUES
              {",\n  ".join(role_values)}
            ON CONFLICT (tenant_id, actor_id, role, country_code, region_code) DO UPDATE
            SET active = excluded.active;
            """
        )

    if profile_values:
        sql += textwrap.dedent(
            f"""
            INSERT INTO referral_profiles (
              ref_code, tenant_id, owner_id, display_mode, public_profile, country_code, region_code, enabled
            )
            VALUES
              {",\n  ".join(profile_values)}
            ON CONFLICT (ref_code) DO UPDATE
            SET owner_id = excluded.owner_id,
                display_mode = excluded.display_mode,
                public_profile = excluded.public_profile,
                country_code = excluded.country_code,
                enabled = excluded.enabled,
                profile_version = referral_profiles.profile_version + 1,
                updated_at = now();
            """
        )

    sql += "\n"
    return sql


def apply_sql_via_n8n(helper, session, partners_mod, sql: str) -> dict:
    suffix = uuid.uuid4().hex[:10]
    path = f"whieda-partners-runtime-sync-{suffix}"
    credential = {"postgres": helper.WORKFLOW_CREDENTIAL}
    workflow = {
        "name": f"TEMP WHIEDA Partners Runtime Sync {suffix}",
        "active": False,
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
                "id": "webhook",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-200, 0],
            },
            {
                "parameters": {
                    "jsCode": "const input = $input.first().json;\nconst body = input.body && typeof input.body === 'object' ? input.body : input;\nreturn [{ json: body }];",
                },
                "id": "normalize",
                "name": "Code: Normalize payload",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-20, 0],
            },
            {
                "parameters": {"operation": "executeQuery", "query": "={{ $json.sql }}", "options": {}},
                "id": "postgres",
                "name": "Postgres: Sync partners runtime",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [180, 0],
                "credentials": credential,
            },
            {
                "parameters": {"respondWith": "firstIncomingItem", "options": {"responseCode": 200}},
                "id": "respond",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [380, 0],
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Code: Normalize payload", "type": "main", "index": 0}]]},
            "Code: Normalize payload": {"main": [[{"node": "Postgres: Sync partners runtime", "type": "main", "index": 0}]]},
            "Postgres: Sync partners runtime": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }
    response = partners_mod.call_temp_webhook(helper, session, workflow, path, payload={"sql": sql})
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}


def main() -> None:
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    partners_mod = load_module("partners_upd", "update_partners_sheet_subdomains_2026-08-01.py")
    session = helper.login_session()
    rows = fetch_partners_rows()
    sql = build_sync_sql(rows)
    result = apply_sql_via_n8n(helper, session, partners_mod, sql)
    print(
        json.dumps(
            {
                "partners": [row.get("partner_id") for row in rows],
                "refs": [row.get("ref_code") for row in rows if row.get("ref_code")],
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
