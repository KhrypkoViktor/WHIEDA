#!/usr/bin/env python3
"""Stage the guarded Gate M runner in the isolated shared-staging API container.

This bootstrap only copies a sealed local package and the matching runner source to
the staging host. It deliberately has no database connection and cannot import,
publish, or activate a Telegram binding.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "n8n" / "current"))
from whieda_runtime_env import ssh_config  # noqa: E402


REMOTE_ROOT = "/opt/whieda-platform-core/staging-canary-runs"
CONTAINER = "whieda-shared-staging-api"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_tree(archive: tarfile.TarFile, source: Path, arcname: str) -> None:
    for path in sorted(source.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        archive.add(path, arcname=str(Path(arcname) / path.relative_to(source)), recursive=False)


def build_media_inputs(package: Path, photo_root: Path, destination: Path) -> Path:
    """Make Gate I evidence rows from the package's local media references."""
    products = json.loads((package / "products.json").read_text(encoding="utf-8"))
    approved = {str(row.get("sku") or "") for row in products if str(row.get("review_status") or "") == "approved"}
    rows: list[dict[str, str]] = []
    for row in json.loads((package / "media.json").read_text(encoding="utf-8")):
        sku = str(row.get("sku") or "")
        local_ref = str(row.get("url") or "").replace("\\", "/")
        if sku not in approved or not local_ref.startswith(f"media/nsp-maxim/{sku}/"):
            continue
        filename = local_ref.rsplit("/", 1)[-1]
        suffix = filename.removeprefix("1")
        source = photo_root / f"{sku}{suffix}"
        if not source.is_file():
            raise FileNotFoundError(f"approved media source missing: {source}")
        copied = destination / "media-source" / sku / filename
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        rows.append(
            {
                "tenant_id": "nsp-maxim",
                "sku": sku,
                "relative_path": local_ref,
                "filename": filename,
                "source_path": str(Path("media-source") / sku / filename),
                "sha256": sha256(copied),
                "bytes": str(copied.stat().st_size),
                "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "status": "ready",
                "reason": "official_local_photo",
            }
        )
    if len(rows) != len(approved):
        raise RuntimeError(f"media evidence count {len(rows)} != approved product count {len(approved)}")
    manifest = destination / "media-manifest.tsv"
    fields = ("tenant_id", "sku", "relative_path", "filename", "source_path", "sha256", "bytes", "mime_type", "status", "reason")
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: item["sku"]))
    return manifest


def build_archive(package: Path, media_manifest: Path, media_sources: Path, destination: Path) -> str:
    required = (
        ROOT / "backend" / "platform-api" / "app",
        ROOT / "backend" / "platform-api" / "scripts",
        ROOT / "postgres" / "scripts",
        ROOT / "postgres" / "sql",
        package,
        media_manifest,
        media_sources,
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing required release input: " + ", ".join(missing))

    with tarfile.open(destination, "w:gz") as archive:
        add_tree(archive, ROOT / "backend" / "platform-api" / "app", "bundle/backend/platform-api/app")
        add_tree(archive, ROOT / "backend" / "platform-api" / "scripts", "bundle/backend/platform-api/scripts")
        add_tree(archive, ROOT / "postgres" / "scripts", "bundle/postgres/scripts")
        add_tree(archive, ROOT / "postgres" / "sql", "bundle/postgres/sql")
        add_tree(archive, package, "bundle/package")
        archive.add(media_manifest, arcname="bundle/media-manifest.tsv", recursive=False)
        add_tree(archive, media_sources, "bundle/media-source")
        info = tarfile.TarInfo("bundle/qa/.keep")
        info.size = 0
        archive.addfile(info)
    return sha256(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage the Gate M runner; never connects to Postgres.")
    parser.add_argument("--prepare", action="store_true", help="Explicitly stage files in the isolated container.")
    parser.add_argument("--preflight", action="store_true", help="Run only the staged runner's read-only preflight.")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--photo-root", type=Path, required=True, help="Local directory with approved NSP originals/WebP files.")
    args = parser.parse_args()
    if args.prepare == args.preflight:
        parser.error("pass exactly one of --prepare or --preflight")

    package = args.package.resolve()
    manifest = package / "manifest.json"
    if not manifest.is_file():
        parser.error(f"package manifest not found: {manifest}")
    package_sha = sha256(manifest)
    if args.preflight:
        config = ssh_config()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=config["host"], username=config["user"], password=config["password"],
            look_for_keys=False, allow_agent=False, timeout=30,
        )
        try:
            remote = f"/tmp/whieda-gate-m-{package_sha}"
            command = (
                "set -e; "
                f"docker exec {CONTAINER} sh -lc '"
                f"test -f {remote}/package/manifest.json; "
                f"export PYTHONPATH={remote}/backend/platform-api:{remote}/backend/platform-api/scripts; "
                "export WHIEDA_SHARED_STAGING_DSN=\"$PLATFORM_DATABASE_URL\"; "
                "export WHIEDA_SHARED_STAGING_EXPECTED_DB=whieda_shared_staging; "
                "export PLATFORM_TENANT_MEDIA_BASE_URL=\"$PLATFORM_TENANT_MEDIA_BASE_URL\"; "
                f"python {remote}/backend/platform-api/scripts/run_shared_staging_tenant_canary.py "
                f"--preflight --package {remote}/package --media-manifest {remote}/media-manifest.tsv'"
            )
            _, stdout, stderr = client.exec_command(command, timeout=60)
            status = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8", errors="replace")
            errors = stderr.read().decode("utf-8", errors="replace")
            if status not in {0, 2}:
                raise RuntimeError(errors or output)
            print(output)
            return status
        finally:
            client.close()

    with tempfile.TemporaryDirectory(prefix="whieda-gate-m-") as temp:
        archive = Path(temp) / f"gate-m-{package_sha[:12]}.tar.gz"
        inputs = Path(temp) / "inputs"
        media_manifest = build_media_inputs(package, args.photo_root.resolve(), inputs)
        archive_sha = build_archive(package, media_manifest, inputs / "media-source", archive)
        remote_dir = f"{REMOTE_ROOT}/nsp-maxim/{package_sha}"
        remote_archive = f"{remote_dir}/gate-m-runner.tar.gz"
        config = ssh_config()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=config["host"], username=config["user"], password=config["password"],
            look_for_keys=False, allow_agent=False, timeout=30,
        )
        try:
            sftp = client.open_sftp()
            _, stdout, stderr = client.exec_command(f"mkdir -p {remote_dir}", timeout=30)
            if stdout.channel.recv_exit_status():
                raise RuntimeError(stderr.read().decode("utf-8", errors="replace"))
            sftp.put(str(archive), remote_archive)
            sftp.close()
            command = (
                "set -e; "
                f"printf '%s  %s\\n' '{archive_sha}' '{remote_archive}' | sha256sum -c -; "
                f"rm -rf /tmp/whieda-gate-m-{package_sha}; "
                f"mkdir -p /tmp/whieda-gate-m-{package_sha}; "
                f"tar -xzf {remote_archive} -C /tmp/whieda-gate-m-{package_sha}; "
                f"docker cp /tmp/whieda-gate-m-{package_sha}/bundle/. {CONTAINER}:/tmp/whieda-gate-m-{package_sha}; "
                f"docker exec {CONTAINER} sh -lc 'test -f /tmp/whieda-gate-m-{package_sha}/package/manifest.json'"
            )
            _, stdout, stderr = client.exec_command(command, timeout=90)
            if stdout.channel.recv_exit_status():
                raise RuntimeError(stderr.read().decode("utf-8", errors="replace"))
        finally:
            client.close()
    print({"ok": True, "action": "runner_staged", "package_sha256": package_sha, "archive_sha256": archive_sha})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
