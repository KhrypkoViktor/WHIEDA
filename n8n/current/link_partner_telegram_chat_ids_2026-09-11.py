"""Fill lead_actors.telegram_chat_id for partners whose Partners_Ref row has none.

Why this exists: after the Core cutover (2026-08-02) nothing records a partner's
chat id when they press /start, so lead delivery to their bot silently dies
(n8n execution 29675, "chat_id is empty"). Core now links by username on the
next message; this one-off applies the ids the owner already collected.

Every row is matched by @username, never by actor_id alone, so a mistyped id
cannot land on a different partner. Only an empty chat id is filled.

Usage:
    python link_partner_telegram_chat_ids_2026-09-11.py            # apply
    python link_partner_telegram_chat_ids_2026-09-11.py --print    # show SQL only
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
TENANT_ID = "whieda"

# username -> (telegram user id == private chat id). Source: owner, 2026-09-11.
PARTNER_CHAT_IDS: dict[str, int] = {
    "IgorYefimenko": 690501231,
    "Olesya_vselennay": 5497008018,
    "pfedorov73": 216275454,
    "SOFIYA_GALINA": 612816657,
}


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_sql() -> str:
    values = ",\n  ".join(
        f"({sql_literal(username.lower())}, {sql_literal(str(chat_id))}, {int(chat_id)})"
        for username, chat_id in PARTNER_CHAT_IDS.items()
    )
    return f"""
WITH supplied(username, chat_id, user_id) AS (
  VALUES
  {values}
), linked AS (
  UPDATE lead_actors la
     SET telegram_chat_id = s.chat_id,
         telegram_user_id = CASE
           WHEN EXISTS (
             SELECT 1 FROM lead_actors u
              WHERE u.tenant_id = la.tenant_id
                AND u.telegram_user_id = s.user_id
                AND u.actor_id <> la.actor_id
           ) THEN la.telegram_user_id
           ELSE s.user_id
         END,
         updated_at = now()
    FROM supplied s
   WHERE la.tenant_id = {sql_literal(TENANT_ID)}
     AND la.active
     AND lower(ltrim(coalesce(la.telegram_username, ''), '@')) = s.username
     AND coalesce(la.telegram_chat_id, '') = ''
     AND NOT EXISTS (
       SELECT 1 FROM lead_actors c
        WHERE c.tenant_id = la.tenant_id
          AND c.telegram_chat_id = s.chat_id
     )
  RETURNING la.actor_id, la.telegram_username, la.telegram_chat_id
)
SELECT
  (SELECT count(*) FROM linked) AS linked_count,
  (SELECT string_agg(actor_id || '=' || telegram_chat_id, ', ' ORDER BY actor_id) FROM linked) AS linked,
  (SELECT string_agg(s.username, ', ' ORDER BY s.username)
     FROM supplied s
    WHERE NOT EXISTS (
      SELECT 1 FROM lead_actors la
       WHERE la.tenant_id = {sql_literal(TENANT_ID)}
         AND lower(ltrim(coalesce(la.telegram_username, ''), '@')) = s.username
    )) AS unknown_usernames;
"""


def main() -> None:
    sql = build_sql()
    if "--print" in sys.argv[1:]:
        print(sql)
        return
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    partners_mod = load_module("partners_upd", "update_partners_sheet_subdomains_2026-08-01.py")
    runtime_sync = load_module("partners_runtime_sync", "run_partners_ref_runtime_sync_2026-08-01.py")
    session = helper.login_session()
    result = runtime_sync.apply_sql_via_n8n(helper, session, partners_mod, sql)
    print(json.dumps({"usernames": sorted(PARTNER_CHAT_IDS), "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
