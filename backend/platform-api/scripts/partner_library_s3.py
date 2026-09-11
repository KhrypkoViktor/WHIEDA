#!/usr/bin/env python3
"""Check a private S3 bucket and upload partner-library files.

Credentials are read only from standard AWS environment variables. The script
never accepts or prints access keys. Run it from backend/platform-api.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.partner_library.storage import S3StorageBackend, validate_storage_key
from app.settings import get_settings


PUBLIC_GRANTEE_URIS = {
    "http://acs.amazonaws.com/groups/global/AllUsers",
    "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
}


def build_backend() -> S3StorageBackend:
    settings = get_settings()
    if settings.platform_partner_library_storage_backend != "s3":
        raise RuntimeError("PLATFORM_PARTNER_LIBRARY_STORAGE_BACKEND must be s3")
    if not settings.platform_partner_library_s3_bucket:
        raise RuntimeError("PLATFORM_PARTNER_LIBRARY_S3_BUCKET is required")
    return S3StorageBackend(
        bucket=settings.platform_partner_library_s3_bucket,
        endpoint_url=settings.platform_partner_library_s3_endpoint_url,
        region_name=settings.platform_partner_library_s3_region,
        addressing_style=settings.platform_partner_library_s3_addressing_style,
    )


def assert_private_bucket(storage: S3StorageBackend) -> dict[str, object]:
    storage.client.head_bucket(Bucket=storage.bucket)
    acl = storage.client.get_bucket_acl(Bucket=storage.bucket)
    public = []
    for grant in acl.get("Grants", []):
        uri = (grant.get("Grantee") or {}).get("URI")
        if uri in PUBLIC_GRANTEE_URIS:
            public.append(uri)
    if public:
        raise RuntimeError("bucket ACL grants public access")
    return {
        "ok": True,
        "bucket": storage.bucket,
        "private_acl": True,
        "owner_only_grants": len(acl.get("Grants", [])),
    }


def probe(storage: S3StorageBackend, tenant_id: str) -> dict[str, object]:
    assert_private_bucket(storage)
    key = validate_storage_key(
        f"{tenant_id}/_canary/{secrets.token_hex(12)}.txt",
        tenant_id=tenant_id,
    )
    body = b"wwc-partner-library-canary\n"
    uploaded = False
    try:
        storage.client.put_object(
            Bucket=storage.bucket,
            Key=key,
            Body=body,
            ContentType="text/plain; charset=utf-8",
            ACL="private",
        )
        uploaded = True
        stat = storage.stat(key)
        url = storage.signed_url(key, 60)
        with urllib.request.urlopen(url, timeout=20) as response:
            downloaded = response.read()
        if downloaded != body:
            raise RuntimeError("pre-signed download returned unexpected bytes")
        return {
            "ok": True,
            "bucket": storage.bucket,
            "write": True,
            "head": stat.size_bytes == len(body),
            "signed_download": True,
            "cleanup": "pending",
        }
    finally:
        if uploaded:
            storage.client.delete_object(Bucket=storage.bucket, Key=key)


def upload(storage: S3StorageBackend, source: Path, key: str, tenant_id: str) -> dict[str, object]:
    assert_private_bucket(storage)
    if not source.is_file():
        raise ValueError(f"file not found: {source}")
    safe_key = validate_storage_key(key, tenant_id=tenant_id)
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    storage.client.upload_file(
        str(source),
        storage.bucket,
        safe_key,
        ExtraArgs={"ACL": "private", "ContentType": mime_type},
    )
    stat = storage.stat(safe_key)
    return {
        "ok": True,
        "bucket": storage.bucket,
        "storage_key": safe_key,
        "size_bytes": stat.size_bytes,
        "mime_type": stat.mime_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="read-only bucket and ACL check")
    probe_parser = sub.add_parser("probe", help="write/read/delete a short canary object")
    probe_parser.add_argument("--tenant-id", default="whieda")
    upload_parser = sub.add_parser("upload", help="upload one private library file")
    upload_parser.add_argument("--tenant-id", default="whieda")
    upload_parser.add_argument("--file", required=True, type=Path)
    upload_parser.add_argument("--key", required=True)
    args = parser.parse_args()

    storage = build_backend()
    if args.command == "check":
        result = assert_private_bucket(storage)
    elif args.command == "probe":
        result = probe(storage, args.tenant_id)
        result["cleanup"] = "done"
    else:
        result = upload(storage, args.file, args.key, args.tenant_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
