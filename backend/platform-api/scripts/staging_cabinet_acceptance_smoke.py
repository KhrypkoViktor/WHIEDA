#!/usr/bin/env python3
"""HTTP smoke for admin-staging cabinet host (no live Telegram, no secrets)."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def tls_verify() -> bool:
    return os.environ.get("WHIEDA_TLS_VERIFY", "1").strip().lower() not in {"0", "false", "no"}


def fetch(url: str, *, verify_tls: bool) -> tuple[int, str, dict]:
    context = None if verify_tls else ssl._create_unverified_context()
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            cookie_headers = {}
            if "Set-Cookie" in resp.headers:
                cookie_headers["Set-Cookie"] = resp.headers["Set-Cookie"]
            return resp.status, body, cookie_headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://admin-staging.wwc.best")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS hostname verification (staging only; cert may not cover admin-staging yet)",
    )
    args = parser.parse_args()

    verify_tls = tls_verify() and not args.insecure
    base = args.base.rstrip("/")
    results: list[dict] = []

    status, body, _ = fetch(f"{base}/cabinet/", verify_tls=verify_tls)
    login_ok = status == 200 and ("cabinet-app" in body or "Кабинет — вход" in body)
    results.append({"check": "cabinet_login_page", "status": status, "ok": login_ok})

    status, body, _ = fetch(f"{base}/wwc-cabinet-config.json", verify_tls=verify_tls)
    cfg_ok = False
    if status == 200:
        try:
            cfg = json.loads(body)
            cfg_ok = cfg.get("enabled") is True and cfg.get("apiBasePath") == "/api/v1/admin"
        except json.JSONDecodeError:
            cfg_ok = False
    results.append({"check": "cabinet_config_enabled", "status": status, "ok": cfg_ok})

    status, body, _ = fetch(f"{base}/api/v1/admin/me", verify_tls=verify_tls)
    results.append({"check": "admin_me_unauth_401", "status": status, "ok": status == 401})

    status, body, _ = fetch(f"{base}/cabinet/", verify_tls=verify_tls)
    no_advisor = "wwc-advisor-widget" not in body and "metrika" not in body.lower()
    results.append({"check": "cabinet_html_no_advisor_metrika", "status": status, "ok": no_advisor})

    failed = [r for r in results if not r["ok"]]
    print(
        json.dumps(
            {"base": base, "tls_verify": verify_tls, "results": results, "failed": len(failed)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
