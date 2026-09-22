"""DEPRECATED (P0.3.5): staging API is protected by loopback + SSH reverse tunnel, not UFW."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(
        json.dumps(
            {
                "status": "deprecated",
                "reason": "P0.3.5: Core API binds 127.0.0.1:8081; site nginx uses 127.0.0.1:18081 via wwc-admin-staging-tunnel.service",
                "use_instead": [
                    "backend/deploy/staging/admin-staging.wwc.best.nginx.conf",
                    "systemctl status wwc-admin-staging-tunnel.service (Core VPS)",
                    "post_deploy_staging_cabinet_smoke_2026-08-10.py",
                ],
                "do_not_claim": "firewall restricted without active UFW rules",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
