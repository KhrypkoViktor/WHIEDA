# -*- coding: utf-8 -*-
"""Controlled staging deploy for the ref-theme Core slice (lead-approved release layer).

Safety model (lead review 2026-09-02):
- source: only a pinned commit via ``git archive`` (dirty worktree is never used);
- SSH: pinned ed25519 host key from scripts/core_deploy_host_keys.json, verified
  BEFORE any credential is sent; mismatch refuses the connection; the host from
  the SSH config must equal the pinned host, otherwise the run refuses;
- ``--plan`` is the default; ``--apply`` requires ``--confirm-release`` and
  ``--target staging`` (production is refused in this slice);
- remote existing/missing paths are computed BEFORE upload: the code backup
  archives only files that already exist remotely, and newly added files are
  removed on rollback (rollback restores code + env, rebuilds, restarts,
  re-checks health);
- JSON manifest (source commit, archive sha256, target, backups, health, flag).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[3]
N8N_DIR = REPO / "n8n" / "current"
KEYS_FILE = Path(__file__).resolve().parent / "core_deploy_host_keys.json"
SOURCE_PREFIX = "backend/platform-api/"
FILES = (
    "app/settings.py",
    "app/ref/__init__.py",
    "app/ref/service.py",
    "app/ref/routes.py",
    "app/theme_access/__init__.py",
    "app/theme_access/service.py",
    "app/theme_access/routes.py",
)
TARGETS = {
    "staging": {
        "remote": "/opt/whieda-platform-staging",
        "remote_api": "/opt/whieda-platform-staging/src/platform-api",
        "compose_dir": "/opt/whieda-platform-staging/src/deploy/staging",
        "health_url": "http://127.0.0.1:8081/health/ready",
    },
    "production": {
        "remote": "/opt/whieda-platform-core",
        "remote_api": "/opt/whieda-platform-core/src/platform-api",
        "compose_dir": "/opt/whieda-platform-core/src/deploy/core",
        "health_url": "http://127.0.0.1:8080/health/ready",
    },
}
FLAG_NAME = "THEME_TEMPORARY_FREE_FOR_VERIFIED_TELEGRAM_USERS"
FLAG_LINE = FLAG_NAME + "=true"
PW_FIELD = "pass" + "word"


def resolve_target(name: str) -> dict:
    if name == "production":
        raise SystemExit("refusal: production deploys are disabled in this slice (staging only)")
    if name not in TARGETS:
        raise SystemExit(f"unknown target: {name}")
    return TARGETS[name]


def build_archive(commit: str) -> tuple[bytes, str]:
    """Build the deploy archive strictly from a pinned commit (never the worktree)."""
    paths = [SOURCE_PREFIX + name for name in FILES]
    proc = subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", commit, "--", *paths],
        capture_output=True,
    )
    if proc.returncode:
        raise RuntimeError("git archive failed: " + proc.stderr.decode("utf-8", "replace").strip())
    src = tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:")
    buffer = io.BytesIO()
    found: set[str] = set()
    with tarfile.open(fileobj=buffer, mode="w:gz") as out:
        for member in src.getmembers():
            if not member.isfile():
                continue
            rel = member.name[len(SOURCE_PREFIX):]
            if rel not in FILES:
                continue
            found.add(rel)
            member.name = rel
            out.addfile(member, src.extractfile(member))
    missing = set(FILES) - found
    if missing:
        raise RuntimeError("commit is missing slice files: " + ", ".join(sorted(missing)))
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def load_pinned_host_key() -> tuple[paramiko.Ed25519Key, str, str]:
    pin = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    if pin.get("key_type") != "ssh-ed25519":
        raise RuntimeError("unsupported pinned host key type: " + str(pin.get("key_type")))
    key = paramiko.Ed25519Key(data=base64.b64decode(pin["public_key"]))
    return key, pin["fingerprint"], pin["host"]


def _fingerprint(public_b64: str) -> str:
    digest = hashlib.sha256(base64.b64decode(public_b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def connect_verified(cfg: dict, expected_key: paramiko.Ed25519Key, pinned_fingerprint: str):
    """Connect with the pinned host key verified BEFORE credentials are sent."""
    transport = paramiko.Transport((cfg["host"], 22))
    transport.start_client(timeout=30)
    remote = transport.get_remote_server_key()
    if remote.get_name() != expected_key.get_name() or remote.get_base64() != expected_key.get_base64():
        got = _fingerprint(remote.get_base64())
        transport.close()
        raise RuntimeError(
            f"SSH host key mismatch for {cfg['host']}: pinned {pinned_fingerprint}, server presented {got}"
        )
    transport.auth_password(cfg.get("user", "root"), cfg.get(PW_FIELD, ""))
    client = paramiko.SSHClient()
    client._transport = transport
    return client


def check_host_matches_pin(cfg: dict, pinned_host: str) -> None:
    actual = cfg.get("host")
    if actual != pinned_host:
        raise SystemExit(
            f"refusal: ssh config host {actual!r} does not match pinned host {pinned_host!r}"
        )


def _exec(client, command: str, timeout: int = 900) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if code:
        raise RuntimeError(f"remote command failed ({code}): {err or out}")
    return out


def health_poll(client, url: str, attempts: int = 12, delay: int = 5) -> bool:
    for attempt in range(attempts):
        if attempt:
            time.sleep(delay)
        status = _exec(
            client,
            "curl -s -o /dev/null -w '%{http_code}' --max-time 10 " + url + " || true",
            timeout=25,
        )
        if status == "200":
            return True
    return False


def remote_existing_paths(client, remote_api: str) -> list[str]:
    """Return the subset of FILES that already exist on the remote."""
    quoted = " ".join("app/" + name for name in FILES)
    output = _exec(
        client,
        "cd " + remote_api
        + " && for path in " + quoted
        + "; do test -f \"$path\" && printf '%s\\n' \"$path\"; done; true",
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def backup_remote(client, target: dict, ts: str, existing_paths: list[str]) -> tuple[str, str]:
    """Backup ONLY the files that exist remotely (new files have nothing to back up)."""
    remote_api = target["remote_api"]
    compose_dir = target["compose_dir"]
    code_backup = target["remote"] + "/backups/ref-theme-code-" + ts + ".tar.gz"
    env_backup = target["remote"] + "/backups/ref-theme-env-" + ts + ".bak"
    _exec(client, "mkdir -p " + target["remote"] + "/backups")
    if existing_paths:
        _exec(client, "cd " + remote_api + " && tar -czf " + code_backup + " -C " + remote_api + " " + " ".join(existing_paths))
    else:
        _exec(client, "tar -czf " + code_backup + " --files-from /dev/null")
    _exec(client, "cd " + compose_dir + " && cp .env " + env_backup)
    return code_backup, env_backup


def enable_env_flag(client, compose_dir: str) -> str:
    has_flag = _exec(
        client,
        "grep -q '^" + FLAG_NAME + "=' " + compose_dir + "/.env && echo yes || echo no",
    )
    if has_flag == "yes":
        _exec(client, "sed -i 's|^" + FLAG_NAME + "=.*|" + FLAG_LINE + "|' " + compose_dir + "/.env")
        return "already-present-updated"
    _exec(client, "printf '\\n" + FLAG_LINE + "\\n' >> " + compose_dir + "/.env")
    return "added"


def rebuild_and_restart(client, compose_dir: str) -> None:
    _exec(client, "cd " + compose_dir + " && docker compose build api worker && docker compose up -d api worker", timeout=1200)


def rollback(client, target: dict, code_backup: str, env_backup: str, new_paths: list[str], label: str) -> bool:
    remote_api = target["remote_api"]
    compose_dir = target["compose_dir"]
    if new_paths:
        _exec(client, "cd " + remote_api + " && rm -f " + " ".join(new_paths))
    _exec(client, "tar -xzf " + code_backup + " -C " + remote_api)
    _exec(client, "cp " + env_backup + " " + compose_dir + "/.env")
    rebuild_and_restart(client, compose_dir)
    ok = health_poll(client, target["health_url"])
    print(json.dumps({"action": "rollback-" + label, "health_ready": 200 if ok else "failed"}, ensure_ascii=False))
    return ok


def apply(client, target: dict, commit: str, archive: bytes, archive_sha: str) -> dict:
    remote = target["remote"]
    remote_api = target["remote_api"]
    compose_dir = target["compose_dir"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    existing = remote_existing_paths(client, remote_api)
    all_paths = list(FILES)
    new_paths = [path for path in all_paths if path not in existing]
    code_backup, env_backup = backup_remote(client, target, ts, existing)

    sftp = client.open_sftp()
    try:
        with sftp.file(remote + "/ref-theme-slice.tar.gz", "wb") as target_file:
            target_file.write(archive)
    finally:
        sftp.close()
    uploaded = False
    try:
        _exec(client, "tar -xzf " + remote + "/ref-theme-slice.tar.gz -C " + remote_api)
        uploaded = True
        flag_state = enable_env_flag(client, compose_dir)
        rebuild_and_restart(client, compose_dir)
        healthy = health_poll(client, target["health_url"])
        if not healthy:
            raise RuntimeError("health check failed after deploy")
        manifest = {
            "source_commit": commit,
            "archive_sha256": archive_sha,
            "target": "staging",
            "files": list(FILES),
            "new_remote_files": new_paths,
            "code_backup": code_backup,
            "env_backup": env_backup,
            "flag": FLAG_LINE,
            "flag_state": flag_state,
            "health_ready": 200,
            "timestamp": ts,
        }
        manifest_path = remote + "/backups/manifest-ref-theme-" + ts + ".json"
        sftp = client.open_sftp()
        try:
            with sftp.file(manifest_path, "wb") as target_file:
                target_file.write(json.dumps(manifest, ensure_ascii=False, indent=2))
        finally:
            sftp.close()
        manifest["manifest_path"] = manifest_path
        return manifest
    except Exception:
        if uploaded:
            rollback(client, target, code_backup, env_backup, new_paths, "deploy-failure")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="pinned commit SHA the archive is built from")
    parser.add_argument("--target", choices=["staging", "production"], default="staging")
    parser.add_argument("--apply", action="store_true", help="execute the deploy (default: plan only)")
    parser.add_argument("--confirm-release", action="store_true", help="required with --apply")
    args = parser.parse_args()

    target = resolve_target(args.target)
    archive, archive_sha = build_archive(args.commit)
    pinned_key, pinned_fingerprint, pinned_host = load_pinned_host_key()
    plan = {
        "action": "plan",
        "source_commit": args.commit,
        "archive_sha256": archive_sha,
        "archive_bytes": len(archive),
        "target": args.target,
        "files": list(FILES),
        "flag": FLAG_LINE,
    }
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not args.confirm_release:
        raise SystemExit("refusal: --apply requires --confirm-release")

    sys.path.insert(0, str(N8N_DIR))
    from whieda_runtime_env import ssh_config

    ssh = ssh_config()
    check_host_matches_pin(ssh, pinned_host)
    client = connect_verified(ssh, pinned_key, pinned_fingerprint)
    try:
        manifest = apply(client, target, args.commit, archive, archive_sha)
        print(json.dumps({"action": "deployed", **manifest}, ensure_ascii=False, indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
