"""P0.3.5 orchestrator: precheck → deploy → post_deploy_smoke → webhook."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "n8n" / "current"
POST_DEPLOY_SMOKE = (
    ROOT / "backend" / "platform-api" / "scripts" / "post_deploy_staging_cabinet_smoke_2026-08-10.py"
)


def run(script: str, *args: str) -> None:
    path = CURRENT / script
    print(f"\n=== {script} {' '.join(args)} ===")
    subprocess.run([sys.executable, str(path), *args], check=True)


def run_py(path: Path, *args: str) -> subprocess.CompletedProcess:
    print(f"\n=== {path.name} {' '.join(args)} ===")
    return subprocess.run([sys.executable, str(path), *args], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bootstrap-base", action="store_true", help="First-time only: pass --with-base to SQL migration")
    parser.add_argument("--insecure-smoke", action="store_true")
    args = parser.parse_args()

    precheck = run_py(CURRENT / "precheck_staging_cabinet_p0_3_5_2026-08-10.py")
    if not args.apply:
        print("\nDry-run: precheck only. Re-run with --apply to deploy.")
        if args.bootstrap_base:
            print("Note: --bootstrap-base will pass --with-base to apply_staging_cabinet_sql (first-time init only).")
        else:
            print("Repeat deploys skip base migrations (no --with-base). Use --bootstrap-base only for fresh env.")
        return precheck.returncode

    if precheck.returncode != 0:
        print(f"\nSTOP: precheck failed (exit {precheck.returncode}); deploy not started.")
        return precheck.returncode

    sql_args = ["--with-base"] if args.bootstrap_base else []
    deploy_steps = [
        ("apply_staging_cabinet_sql_2026-08-10.py", sql_args),
        ("deploy_platform_core_staging_2026-08-09.py", ["--apply"]),
        ("merge_staging_cabinet_secrets_2026-08-09.py", ["--apply"]),
        ("deploy_cabinet_staging_site_2026-08-09.py", ["--apply"]),
        ("patch_site_nginx_admin_staging_2026-08-09.py", ["--apply", "--force"]),
    ]
    for script, script_args in deploy_steps:
        run(script, *script_args)

    smoke_args = ["--insecure"] if args.insecure_smoke else []
    smoke = run_py(POST_DEPLOY_SMOKE, *smoke_args)
    if smoke.returncode != 0:
        print("\nSTOP: post_deploy_smoke failed — webhook NOT applied.")
        return smoke.returncode

    run("setup_staging_cabinet_telegram_webhook_2026-08-09.py", "--apply")
    print("\nOK: P0.3.5 deploy complete. Next: Viktor live QR at https://admin-staging.wwc.best/cabinet/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: step failed with exit {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
