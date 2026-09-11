"""Build and apply Partner_Subscriptions -> lead_actors / referral_profiles sync.

`Partner_Subscriptions` (gid 1209283579) is the sole operational registry.
The old `Partners_Ref` tab is historical and must not be read by runtime.
The sheet stores a Telegram handle, not a durable numeric chat id. Core fills
the latter after the partner writes to the bot; the SQL below never clears an
existing bound chat id when the sheet cell is blank.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import textwrap
import uuid
from pathlib import Path

import paramiko
import requests

from whieda_runtime_env import ssh_config

BASE = Path(__file__).resolve().parent
SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
SHEET_GID = 1209283579
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


def country_code(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    return {"РБ": "BY", "BY": "BY", "РФ": "RU", "RU": "RU"}.get(normalized, "GLOBAL")


def telegram_handle(value: str | None) -> str | None:
    handle = str(value or "").strip()
    if not handle:
        return None
    if "t.me/" in handle:
        handle = handle.split("t.me/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    return handle.strip().lstrip("@") or None


def fetch_partners_rows() -> list[dict[str, str]]:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={SHEET_GID}"
    text = requests.get(url, timeout=30).content.decode("utf-8")
    return [row for row in csv.DictReader(io.StringIO(text), delimiter="\t") if (row.get("ref_code") or "").strip()]


def build_sync_sql(rows: list[dict[str, str]]) -> str:
    actor_values: list[str] = []
    chat_id_values: list[str] = []
    role_values: list[str] = []
    profile_values: list[str] = []

    for row in rows:
        actor_id = (row.get("ref_code") or "").strip()
        display_name = (row.get("Имя") or actor_id).strip()
        owner_actor_id = actor_id
        site_type = "subdomain_site"
        telegram_username = telegram_handle(row.get("Telegram"))
        telegram_chat_id = (row.get("telegram_chat_id") or "").strip() or None
        status = (row.get("Статус") or "").strip().lower()
        active = "false" if status in {"архив", "отключен", "отключён", "inactive", "disabled"} else "true"
        actor_values.append(
            f"({sql_literal(actor_id)}, {sql_literal(TENANT_ID)}, {sql_literal(display_name)}, "
            f"NULL, {sql_literal(telegram_username)}, {active})"
        )
        if telegram_chat_id:
            chat_id_values.append(
                f"({sql_literal(actor_id)}, {sql_literal(TENANT_ID)}, {sql_literal(telegram_chat_id)})"
            )

        ref_code = actor_id
        page_mode = "subdomain_site"
        plan_status = "active" if active == "true" else "inactive"
        enabled = active == "true"
        if ref_code:
            role_actor_id = owner_actor_id if site_type == "platform_root" else actor_id
            country = country_code(row.get("Страна"))
            if enabled:
                role_values.append(
                    f"({sql_literal(TENANT_ID)}, {sql_literal(role_actor_id)}, 'referral_owner', "
                    f"{sql_literal(country)}, {sql_literal('')}, true)"
                )
            public_profile = {
                "partner_id": actor_id,
                "display_name": display_name,
                "page_mode": page_mode,
                "plan_code": "partner_subscription",
                "plan_status": plan_status,
                "leads_access": "partner",
                "public_site_url": (row.get("Сайт") or "").strip(),
                "site_type": site_type,
                "focus_group": False,
                "access_tier": "test_pilot",
                "watcher_actor_id": "",
                "owner_actor_id": owner_actor_id,
                "referrer_ref_code": (row.get("Реферер (ref)") or "").strip(),
            }
            owner_id = owner_actor_id
            country = country_code(row.get("Страна"))
            profile_values.append(
                "("
                f"{sql_literal(ref_code)}, {sql_literal(TENANT_ID)}, {sql_literal(owner_id)}, "
                f"{sql_literal(display_mode(page_mode))}, {sql_literal(json.dumps(public_profile, ensure_ascii=False))}::jsonb, "
                f"{sql_literal(country)}, NULL, {sql_bool(enabled)}"
                ")"
            )

    if not actor_values:
        raise RuntimeError("Partner_Subscriptions sheet returned no partner rows")

    sql = textwrap.dedent(
        f"""
        INSERT INTO lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username, active)
        VALUES
          {",\n  ".join(actor_values)}
        ON CONFLICT (actor_id) DO UPDATE
        SET display_name = excluded.display_name,
            telegram_username = coalesce(excluded.telegram_username, lead_actors.telegram_username),
            active = excluded.active,
            updated_at = now();
        """
    )

    if chat_id_values:
        sql += textwrap.dedent(
            f"""
            UPDATE lead_actors AS target
            SET telegram_chat_id = source.telegram_chat_id,
                updated_at = now()
            FROM (VALUES
              {",\n  ".join(chat_id_values)}
            ) AS source(actor_id, tenant_id, telegram_chat_id)
            WHERE target.actor_id = source.actor_id
              AND target.tenant_id = source.tenant_id
              AND coalesce(target.telegram_chat_id, '') = ''
              AND NOT EXISTS (
                SELECT 1 FROM lead_actors other
                WHERE other.tenant_id = source.tenant_id
                  AND other.telegram_chat_id = source.telegram_chat_id
                  AND other.actor_id <> source.actor_id
              );
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
                "parameters": {
                    "respondWith": "json",
                    "responseBody": '{"status":"ok"}',
                    "options": {"responseCode": 200},
                },
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


def apply_sql_via_core(sql: str) -> dict:
    """Fallback when n8n publishes a webhook but returns an empty response body.

    The Core container already owns the same PostgreSQL connection. SQL is sent
    over stdin, never interpolated into a remote shell command.
    """
    cfg = ssh_config()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        cfg["host"],
        username=cfg["user"],
        password=cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    code = (
        "import os,sys,psycopg; "
        "sql=sys.stdin.read(); "
        "con=psycopg.connect(os.environ['PLATFORM_DATABASE_URL']); "
        "con.execute(sql); con.commit(); print('runtime_sync_applied'); con.close()"
    )
    try:
        stdin, stdout, stderr = client.exec_command(
            "docker exec -i core-api-1 python -c " + repr(code), timeout=90
        )
        stdin.channel.sendall(sql.encode("utf-8"))
        stdin.channel.shutdown_write()
        status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace").strip()
        error = stderr.read().decode("utf-8", errors="replace").strip()
        if status != 0:
            raise RuntimeError(f"Core runtime sync failed ({status}): {error or output}")
        return {"transport": "core_direct", "result": output}
    finally:
        client.close()


def main() -> None:
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    partners_mod = load_module("partners_upd", "update_partners_sheet_subdomains_2026-08-01.py")
    session = helper.login_session()
    rows = fetch_partners_rows()
    sql = build_sync_sql(rows)
    try:
        result = {"transport": "n8n", "result": apply_sql_via_n8n(helper, session, partners_mod, sql)}
    except RuntimeError as exc:
        result = {"n8n_error": str(exc), "fallback": apply_sql_via_core(sql)}
    print(
        json.dumps(
            {
                "partners": [row.get("ref_code") for row in rows],
                "refs": [row.get("ref_code") for row in rows if row.get("ref_code")],
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
