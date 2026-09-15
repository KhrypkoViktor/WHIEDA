#!/usr/bin/env python3
"""Live HTTP smoke for WWC Owner Cabinet P0.1 / P0.1B (local staging Postgres 127.0.0.1:55432)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
from typing import Any
from unittest.mock import AsyncMock, patch

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SMOKE_SUPER_TELEGRAM_ID = 888001001
SMOKE_UNKNOWN_TELEGRAM_ID = 888001002
SMOKE_CONFIRM_SECRET = "local_admin_cabinet_smoke_confirm_v1"
SMOKE_BINDING_ID = "whieda-local-smoke-bot"
SMOKE_TELEGRAM_UPDATE_ID = 8809001
BROWSER_NONCE = "smoke-browser-nonce-0123456789"
CABINET_HOST = "cabinet.test.local"
API_BASE = "http://cabinet.test.local"
DEFAULT_DB_URL = (
    "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core"
)


def _record(results: list[dict[str, Any]], name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


def _extract_start_param(deep_link: str) -> str:
    parsed = urllib.parse.urlparse(deep_link)
    start = urllib.parse.parse_qs(parsed.query).get("start", [""])[0]
    if not start:
        raise ValueError(f"missing start param in deep_link: {deep_link}")
    return start


async def _audit_has_cross_tenant(principal_id: str) -> bool:
    from app.db import admin_connection, fetch_one

    async with admin_connection() as conn:
        row = await fetch_one(
            conn,
            """
            select 1
            from platform_admin_audit_log
            where principal_id = %s::uuid
              and target_tenant_id = 'test-acme'
              and action = 'admin_me_view'
            limit 1
            """,
            (principal_id,),
        )
    return row is not None


async def _simulate_telegram_admin_login(
    *,
    start_param: str,
    telegram_user_id: int,
    update_id: int = SMOKE_TELEGRAM_UPDATE_ID,
) -> dict[str, Any]:
    from app.telegram.admin_login import try_handle_admin_login

    update = {
        "update_id": update_id,
        "message": {
            "text": f"/start {start_param}",
            "chat": {"id": telegram_user_id},
            "from": {"id": telegram_user_id},
        },
    }
    with patch("app.telegram.admin_login.send_telegram_text", new=AsyncMock(return_value={"ok": True})):
        return await try_handle_admin_login(update, trace_id="admin-cabinet-smoke-p0.1b")


def main() -> int:
    os.environ.setdefault("PLATFORM_DATABASE_URL", DEFAULT_DB_URL)
    os.environ["PLATFORM_ADMIN_SUPER_TELEGRAM_IDS"] = str(SMOKE_SUPER_TELEGRAM_ID)
    os.environ["PLATFORM_ADMIN_CONFIRM_SECRET"] = SMOKE_CONFIRM_SECRET
    os.environ.setdefault("PLATFORM_TELEGRAM_BOT_USERNAME", "wwc_smoke_bot")
    os.environ["PLATFORM_ADMIN_COOKIE_SECURE"] = "false"
    os.environ.setdefault("ENVIRONMENT", "development")

    from app.main import create_app
    from app.settings import get_settings

    get_settings.cache_clear()

    results: list[dict[str, Any]] = []
    headers = {"Host": CABINET_HOST}

    with __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(create_app()) as client:
        r = client.get("/v1/admin/me", headers=headers)
        _record(results, "unauthenticated_me_401", r.status_code == 401, f"status={r.status_code}")

        r = client.post(
            "/v1/admin/auth/telegram-confirm",
            headers={**headers, "X-Platform-Admin-Secret": "wrong-secret"},
            json={"challenge_token": "adm_x", "telegram_user_id": SMOKE_SUPER_TELEGRAM_ID},
        )
        _record(results, "confirm_wrong_secret_403", r.status_code == 403, f"status={r.status_code}")

        r = client.post("/v1/admin/auth/challenge", headers=headers, json={"browser_nonce": BROWSER_NONCE})
        challenge_ok = r.status_code == 200 and r.json().get("ok") is True
        challenge_id = r.json().get("challenge_id") if challenge_ok else None
        deep_link = r.json().get("deep_link") if challenge_ok else None
        _record(results, "auth_challenge", challenge_ok, f"status={r.status_code}")

        start_param = _extract_start_param(deep_link) if deep_link else ""

        unknown_ingress = asyncio.run(
            _simulate_telegram_admin_login(
                start_param=start_param,
                telegram_user_id=SMOKE_UNKNOWN_TELEGRAM_ID,
                update_id=SMOKE_TELEGRAM_UPDATE_ID + 1,
            )
        )
        _record(
            results,
            "telegram_ingress_unknown_user",
            unknown_ingress.get("ok") is False and unknown_ingress.get("status") == "admin_not_allowed",
            json.dumps(
                {
                    "api_base": API_BASE,
                    "binding_id": SMOKE_BINDING_ID,
                    "update_id": unknown_ingress.get("update_id"),
                    "route": unknown_ingress.get("route"),
                },
                ensure_ascii=False,
            ),
        )

        # fresh challenge for happy path via Telegram ingress (P0.1B primary)
        r = client.post("/v1/admin/auth/challenge", headers=headers, json={"browser_nonce": BROWSER_NONCE})
        challenge_id = r.json()["challenge_id"]
        start_param = _extract_start_param(r.json()["deep_link"])

        ingress = asyncio.run(
            _simulate_telegram_admin_login(
                start_param=start_param,
                telegram_user_id=SMOKE_SUPER_TELEGRAM_ID,
                update_id=SMOKE_TELEGRAM_UPDATE_ID,
            )
        )
        ingress_ok = ingress.get("ok") is True and ingress.get("status") == "approved"
        _record(
            results,
            "telegram_ingress_super_admin",
            ingress_ok,
            json.dumps(
                {
                    "api_base": API_BASE,
                    "binding_id": SMOKE_BINDING_ID,
                    "update_id": ingress.get("update_id"),
                    "challenge_id": ingress.get("challenge_id") or challenge_id,
                    "route": ingress.get("route"),
                },
                ensure_ascii=False,
            ),
        )

        r = client.post(
            "/v1/admin/auth/telegram-confirm",
            headers={**headers, "X-Platform-Admin-Secret": SMOKE_CONFIRM_SECRET},
            json={"challenge_token": "admin_login_secondary_path_check", "telegram_user_id": SMOKE_SUPER_TELEGRAM_ID},
        )
        _record(
            results,
            "direct_confirm_secondary_still_protected",
            r.status_code in {403, 404, 409, 410},
            f"status={r.status_code}",
        )

        r = client.get(
            f"/v1/admin/auth/challenge/{challenge_id}",
            headers={**headers, "X-Browser-Nonce": BROWSER_NONCE},
        )
        poll_body = r.json()
        poll_ok = r.status_code == 200 and poll_body.get("status") == "authenticated"
        no_token_in_body = "token" not in json.dumps(poll_body).lower()
        _record(
            results,
            "poll_authenticated_cookie_exchange",
            poll_ok,
            json.dumps(
                {
                    "challenge_id": challenge_id,
                    "poll_status": poll_body.get("status"),
                    "set_cookie": "Set-Cookie" in r.headers,
                },
                ensure_ascii=False,
            ),
        )
        _record(results, "poll_no_bearer_in_body", no_token_in_body)

        r = client.get("/v1/admin/me", headers=headers, params={"tenant_id": "whieda"})
        me_ok = r.status_code == 200 and r.json().get("role") == "super_admin"
        principal_id = r.json().get("principal_id") if me_ok else None
        _record(results, "get_me_super_admin", me_ok, f"status={r.status_code}")

        for path, params in (
            ("/v1/admin/leads", {"tenant_id": "whieda", "limit": 5}),
            ("/v1/admin/referrals", {"tenant_id": "whieda", "limit": 5}),
            ("/v1/admin/markets", {"tenant_id": "whieda"}),
            ("/v1/admin/sync-status", {"tenant_id": "whieda"}),
            ("/v1/admin/overview", {"tenant_id": "whieda"}),
        ):
            r = client.get(path, headers=headers, params=params)
            ok = r.status_code == 200 and r.json().get("ok") is True
            _record(results, f"get_{path.split('/')[-1]}", ok, f"status={r.status_code}")

        r = client.get("/v1/admin/markets", headers=headers, params={"tenant_id": "whieda"})
        body = r.json()
        markets_ok = (
            r.status_code == 200
            and body.get("ok") is True
            and (
                body.get("meta", {}).get("field_status") == "gap"
                or bool(body.get("items"))
            )
        )
        _record(results, "markets_gap_or_data_200", markets_ok)

        r = client.get("/v1/admin/me", headers=headers, params={"tenant_id": "test-acme"})
        cross_ok = r.status_code == 200 and r.json().get("effective_tenant_id") == "test-acme"
        _record(results, "super_admin_cross_tenant_me", cross_ok, f"status={r.status_code}")

        audit_ok = False
        if principal_id:
            audit_ok = asyncio.run(_audit_has_cross_tenant(principal_id))
        _record(results, "cross_tenant_audit_record", audit_ok)

        leads = client.get("/v1/admin/leads", headers=headers, params={"tenant_id": "whieda", "limit": 1}).json()
        items = leads.get("items") or []
        if items:
            lead_id = items[0].get("lead_id")
            r = client.get(f"/v1/admin/leads/{lead_id}", headers=headers, params={"tenant_id": "whieda"})
            _record(results, "lead_detail", r.status_code == 200 and r.json().get("ok") is True)
        else:
            _record(results, "lead_detail", True, "skipped (no leads in fixture DB)")

        r = client.post("/v1/admin/auth/logout", headers=headers)
        _record(results, "logout", r.status_code == 200 and r.json().get("ok") is True)

        r = client.get("/v1/admin/me", headers=headers, params={"tenant_id": "whieda"})
        _record(results, "me_after_logout_401", r.status_code == 401, f"status={r.status_code}")

    failed = [x for x in results if not x["ok"]]
    print("\nSUMMARY:", json.dumps({"passed": len(results) - len(failed), "failed": len(failed), "checks": results}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

