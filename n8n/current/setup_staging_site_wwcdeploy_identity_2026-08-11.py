"""One-time wwcdeploy identity setup for Site VPS staging static deploy (P0.3.5.5)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from staging_deploy_gate_lib import (
    ARCHIVE_VERIFY_PATH,
    DEPLOY_USER,
    GATE_PATH,
    STAGING_STATIC_DIR,
    build_authorized_keys_line,
    public_key_fingerprint,
)
from staging_deploy_lib import connect_ssh, ssh_exec

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SOURCE = REPO_ROOT / "backend" / "deploy" / "staging" / "wwc-admin-staging-deploy-gate"
ARCHIVE_VERIFY_SOURCE = REPO_ROOT / "backend" / "deploy" / "staging" / "wwc-admin-staging-archive-verify"
ARCHIVE_VALIDATOR_SOURCE = REPO_ROOT / "n8n" / "current" / "staging_archive_validator.py"
ARCHIVE_VALIDATOR_INSTALL = "/usr/local/libexec/staging_archive_validator.py"
AUTHORIZED_KEYS = f"/home/{DEPLOY_USER}/.ssh/authorized_keys"


def load_public_key(path: Path) -> str:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise RuntimeError("public_key_file_empty")
    line = content.splitlines()[0].strip()
    if line.startswith("#"):
        raise RuntimeError("public_key_file_invalid")
    return line


def build_plan(public_key: str) -> dict:
    fingerprint = public_key_fingerprint(public_key)
    auth_line = build_authorized_keys_line(public_key)
    return {
        "action": "dry-run",
        "deploy_user": DEPLOY_USER,
        "gate_path": GATE_PATH,
        "archive_verify_path": ARCHIVE_VERIFY_PATH,
        "archive_validator_lib": ARCHIVE_VALIDATOR_INSTALL,
        "gate_source": str(GATE_SOURCE.relative_to(REPO_ROOT)),
        "archive_verify_source": str(ARCHIVE_VERIFY_SOURCE.relative_to(REPO_ROOT)),
        "archive_validator_source": str(ARCHIVE_VALIDATOR_SOURCE.relative_to(REPO_ROOT)),
        "staging_static_dir": STAGING_STATIC_DIR,
        "authorized_keys_path": AUTHORIZED_KEYS,
        "public_key_fingerprint": fingerprint,
        "authorized_keys_preview": f"{AUTHORIZED_KEYS} … fingerprint={fingerprint}",
        "steps": [
            f"create system user {DEPLOY_USER} (forced-command only, no sudo)",
            f"ensure {STAGING_STATIC_DIR} owned by {DEPLOY_USER}",
            f"install gate script to {GATE_PATH} (root:root 755)",
            f"install archive validator to {ARCHIVE_VALIDATOR_INSTALL} and {ARCHIVE_VERIFY_PATH}",
            f"append authorized_keys full line if missing (grep -Fxq)",
        ],
        "note": "Run with --apply for one-time server setup (requires WHIEDA_SITE_SSH_PASSWORD as root).",
    }


def apply_setup(public_key: str) -> dict:
    from whieda_runtime_env import site_ssh_config

    cfg = site_ssh_config()
    if cfg.get("user") != "root":
        raise RuntimeError("apply_requires_root_ssh_password")

    gate_content = GATE_SOURCE.read_text(encoding="utf-8")
    verify_content = ARCHIVE_VERIFY_SOURCE.read_text(encoding="utf-8")
    validator_content = ARCHIVE_VALIDATOR_SOURCE.read_text(encoding="utf-8")
    auth_line = build_authorized_keys_line(public_key)
    fingerprint = public_key_fingerprint(public_key)

    def _remote_write(path: str, content: str, *, mode: str, owner: str) -> None:
        escaped = content.replace("'", "'\"'\"'")
        ssh_exec(client, f"printf '%s' '{escaped}' > {path}")
        ssh_exec(client, f"chmod {mode} {path}")
        ssh_exec(client, f"chown {owner} {path}")

    client = connect_ssh(cfg)
    try:
        ssh_exec(
            client,
            f"id -u {DEPLOY_USER} >/dev/null 2>&1 || useradd --system --shell /bin/bash --home-dir /home/{DEPLOY_USER} --create-home {DEPLOY_USER}",
        )
        # OpenSSH executes the forced command through the account shell.  A
        # nologin shell blocks even the allow-listed scp commands; the key
        # restrictions and gate, not the login shell, enforce least privilege.
        ssh_exec(client, f"usermod --shell /bin/bash {DEPLOY_USER}")
        ssh_exec(client, f"mkdir -p {STAGING_STATIC_DIR}")
        # Old staging releases were deployed as root.  The restricted deploy
        # identity must own the complete *single approved* static tree in
        # order to replace an existing asset, but has no access outside it.
        ssh_exec(client, f"chown -R {DEPLOY_USER}:{DEPLOY_USER} {STAGING_STATIC_DIR}")
        ssh_exec(client, f"chmod 755 {STAGING_STATIC_DIR}")
        ssh_exec(client, "mkdir -p /usr/local/libexec")
        _remote_write(GATE_PATH, gate_content, mode="755", owner="root:root")
        _remote_write(ARCHIVE_VALIDATOR_INSTALL, validator_content, mode="644", owner="root:root")
        _remote_write(ARCHIVE_VERIFY_PATH, verify_content, mode="755", owner="root:root")
        ssh_exec(client, f"mkdir -p /home/{DEPLOY_USER}/.ssh")
        ssh_exec(client, f"chmod 700 /home/{DEPLOY_USER}/.ssh")
        ssh_exec(client, f"chown {DEPLOY_USER}:{DEPLOY_USER} /home/{DEPLOY_USER}/.ssh")
        escaped_auth = auth_line.replace("'", "'\"'\"'")
        ssh_exec(
            client,
            f"grep -Fxq '{escaped_auth}' {AUTHORIZED_KEYS} 2>/dev/null || "
            f"printf '%s\\n' '{escaped_auth}' >> {AUTHORIZED_KEYS}",
        )
        ssh_exec(client, f"chmod 600 {AUTHORIZED_KEYS}")
        ssh_exec(client, f"chown {DEPLOY_USER}:{DEPLOY_USER} {AUTHORIZED_KEYS}")
        return {
            "status": "ok",
            "deploy_user": DEPLOY_USER,
            "gate_path": GATE_PATH,
            "archive_verify_path": ARCHIVE_VERIFY_PATH,
            "staging_static_dir": STAGING_STATIC_DIR,
            "authorized_keys_path": AUTHORIZED_KEYS,
            "public_key_fingerprint": fingerprint,
        }
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup wwcdeploy least-privilege identity on Site VPS")
    parser.add_argument("--apply", action="store_true", help="Apply changes on server (default: dry-run plan)")
    parser.add_argument("--public-key-file", required=True, type=Path, help="Path to deploy public key file")
    args = parser.parse_args()

    public_key = load_public_key(args.public_key_file)

    if not args.apply:
        print(json.dumps(build_plan(public_key), ensure_ascii=False, indent=2))
        print("Run with --apply for one-time server setup (separate from normal deploy).")
        return 0

    result = apply_setup(public_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
