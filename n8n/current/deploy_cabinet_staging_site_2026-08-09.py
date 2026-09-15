"""Deploy WWC cabinet static build to admin-staging site root (P0.3.5.5 wwcdeploy OpenSSH)."""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from staging_deploy_lib import resolve_npm_command, sha256_hex
from staging_deploy_gate_lib import DEPLOY_USER
from staging_openssh_transport import (
    SiteSshDeployUserForbiddenError,
    SiteSshKeyRequiredError,
    load_site_openssh_config,
    upload_staging_tarball_openssh_atomic,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = REPO_ROOT / "03_Website" / "wwc-best"
STAGING_CONFIG = REPO_ROOT / "backend" / "deploy" / "staging" / "wwc-cabinet-config.staging.json"
REMOTE_DIR = "/var/www/admin-staging-wwc-best"
UPLOAD_TIMEOUT = 300


def ensure_staging_config(dist: Path) -> None:
    shutil.copy2(STAGING_CONFIG, dist / "wwc-cabinet-config.json")


def build_site() -> Path:
    subprocess.run([resolve_npm_command(), "run", "build"], cwd=SITE_ROOT, check=True)
    dist = SITE_ROOT / "dist"
    ensure_staging_config(dist)
    return dist


def make_tarball(dist: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in dist.rglob("*"):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(dist)).replace("\\", "/"))
    buffer.seek(0)
    return buffer.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    try:
        site = load_site_openssh_config()
    except SiteSshKeyRequiredError:
        site = {
            "host": __import__("os").environ.get("WHIEDA_SITE_SSH_HOST", "173.249.45.83"),
            "user": __import__("os").environ.get("WHIEDA_SITE_SSH_USER", DEPLOY_USER),
        }

    plan = {
        "action": "apply" if args.apply else "dry-run",
        "site_host": site["host"],
        "remote_dir": REMOTE_DIR,
        "npm_command": resolve_npm_command(),
        "upload_timeout_sec": UPLOAD_TIMEOUT,
        "upload_strategy": "openssh_scp_strict_key",
        "config": json.loads(STAGING_CONFIG.read_text(encoding="utf-8")),
        "cabinet_url": "https://admin-staging.wwc.best/cabinet/",
    }

    print(json.dumps(plan, ensure_ascii=False, indent=2))

    if not args.apply:
        print("Run with --apply to build (unless --skip-build) and upload tarball via OpenSSH scp.")
        return 0

    site = load_site_openssh_config()

    if args.skip_build:
        dist = SITE_ROOT / "dist"
        if not dist.is_dir():
            raise RuntimeError("dist/ missing; run npm run build first")
    else:
        dist = build_site()

    ensure_staging_config(dist)
    tarball = make_tarball(dist)
    digest = sha256_hex(tarball)

    with tempfile.NamedTemporaryFile(prefix="cabinet-staging-", suffix=".tgz", delete=False) as tmp:
        tmp.write(tarball)
        local_tar = Path(tmp.name)

    try:
        result = upload_staging_tarball_openssh_atomic(
            local_tarball=local_tar,
            remote_dir=REMOTE_DIR,
            digest=digest,
            cfg=site,
            upload_timeout=UPLOAD_TIMEOUT,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        local_tar.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SiteSshKeyRequiredError, SiteSshDeployUserForbiddenError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
