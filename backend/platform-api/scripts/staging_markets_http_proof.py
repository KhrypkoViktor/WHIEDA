#!/usr/bin/env python3
"""Live HTTP proof against staging Postgres (127.0.0.1:55432)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

# psycopg async requires SelectorEventLoop on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from starlette.testclient import TestClient

from app.main import create_app
from app.leads.service import parse_lead_body


def main() -> int:
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL",
        "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core",
    )
    os.environ.setdefault("ENVIRONMENT", "development")

    lead = parse_lead_body(
        {
            "name": "Staging BY",
            "contact": "+375000000099",
            "product": "M015-00",
            "idempotency_key": "staging-by-iso-proof",
            "country_iso": "BY",
            "market_id": "by",
            "ref": "fixture-ref-alpha",
        },
        tenant_id="whieda",
    )
    print("LEAD country_iso=BY ->", json.dumps({"country_code": lead.country_code, "country_iso": lead.metadata.get("country_iso")}, ensure_ascii=False))

    app = create_app()
    headers = {"Host": "wwc.best"}
    calls = [
        ("GET", "/api/catalog-prices", {"market_id": "ru", "sku": "M015-00"}),
        ("GET", "/api/catalog-prices", {"market_id": "by", "sku": "M015-00"}),
        ("GET", "/api/catalog-prices", {"market_id": "global", "sku": "M015-00"}),
        ("GET", "/api/service-centers", {"ref": "fixture-ref-alpha", "country_iso": "BY", "city": "Минск"}),
        ("GET", "/api/service-centers", {"ref": "fixture-ref-beta", "country_iso": "BY", "city": "Минск"}),
        ("GET", "/api/service-centers", {"ref": "fixture-ref-alpha", "country_iso": "RU", "city": "Новосибирск"}),
    ]

    with TestClient(app) as client:
        for method, path, params in calls:
            response = client.request(method, path, headers=headers, params=params)
            print(f"\n{method} {path} ?{params}")
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            if response.status_code != 200 or not response.json().get("ok"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
